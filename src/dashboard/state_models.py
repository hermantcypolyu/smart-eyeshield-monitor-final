"""Qt-free value objects shared by the dashboard panels.

Panels render these; they never reach into ``IncidentLifecycle`` or a serial
port themselves. Keeping the snapshot types free of Qt has three payoffs:

1. They can be built and asserted in tests without a ``QApplication``.
2. A worker thread can construct one and hand it to the GUI thread as a single
   immutable value, so no panel ever reads half-updated state.
3. The distinction the whole project depends on - *what we asked the board to
   do* versus *what the board said it did* versus *what the board reports its
   pins are doing* - is encoded in the field names rather than in a convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from tools.incident_lifecycle import (
    AlarmIntent,
    LifecycleStatus,
    OperatorState,
    ResetBlocker,
)


class LinkState(str, Enum):
    """State of the USB serial link, as the dashboard believes it."""

    CLOSED = "CLOSED"
    OPENING = "OPENING"
    OPEN = "OPEN"
    ERROR = "ERROR"

    @property
    def label(self) -> str:
        return {
            LinkState.CLOSED: "NOT CONNECTED",
            LinkState.OPENING: "CONNECTING",
            LinkState.OPEN: "CONNECTED",
            LinkState.ERROR: "ERROR",
        }[self]


class OutputState(str, Enum):
    """Physical pin state, as *reported by the firmware*, not as requested.

    This is the distinction that makes the dashboard honest. A dashboard that
    lights its green LED because the lifecycle says SAFE is only echoing its own
    belief; one that lights it because ``STATUS`` said ``GREEN=1`` is reporting
    the hardware. When the two disagree the discrepancy is the bug.
    """

    UNKNOWN = "UNKNOWN"
    ON = "ON"
    OFF = "OFF"


@dataclass(frozen=True)
class BoardOutputs:
    """Pin states parsed from a firmware ``STATUS`` line."""

    green: OutputState = OutputState.UNKNOWN
    red: OutputState = OutputState.UNKNOWN
    buzzer: OutputState = OutputState.UNKNOWN
    link_healthy: bool | None = None
    firmware_state: str = ""

    @property
    def reported(self) -> bool:
        """True when these came from a real ``STATUS`` line."""

        return self.firmware_state != ""


@dataclass(frozen=True)
class BoardSnapshot:
    """Everything known about the ESP32 node right now."""

    port: str = ""
    baud: int = 115200
    link: LinkState = LinkState.CLOSED
    outputs: BoardOutputs = field(default_factory=BoardOutputs)
    firmware_version: str = ""
    #: Incident id the board itself reports, which may lag the laptop's.
    incident_id: int | None = None
    acknowledged: bool | None = None
    reset_ready: bool | None = None
    rearm_count: int | None = None
    last_error: str = ""
    #: Monotonic time of the last successful ``STATUS`` parse.
    status_at: float | None = None

    @property
    def disagreement(self) -> str:
        """Describe a laptop/board incident-id mismatch, or return "".

        Left for the renderer to decide how loudly to show it; this only
        reports the fact.
        """

        return ""


@dataclass(frozen=True)
class TelemetrySample:
    """One frame's timing and detection counts for the rolling charts."""

    at: float
    fps: float
    inference_ms: float
    frame_age_ms: float
    detection_count: int
    max_confidence: float


@dataclass(frozen=True)
class GaugeSnapshot:
    """The temporal filter's current decision evidence."""

    observation: str = "UNKNOWN"
    state: str = "UNKNOWN"
    violation_ratio: float = 0.0
    pending_elapsed: float | None = None
    confirmation_seconds: float = 2.0
    safe_clear_elapsed: float | None = None
    clearance_seconds: float = 2.0
    confirmed_after_seconds: float | None = None
    violation_ratio_required: float = 0.80

    @property
    def pending_fraction(self) -> float:
        """0..1 progress through the confirmation timer, clamped."""

        if self.pending_elapsed is None or self.confirmation_seconds <= 0:
            return 0.0
        return _clamp01(self.pending_elapsed / self.confirmation_seconds)

    @property
    def clear_fraction(self) -> float:
        """0..1 progress through the clearance timer, clamped."""

        if self.safe_clear_elapsed is None or self.clearance_seconds <= 0:
            return 0.0
        return _clamp01(self.safe_clear_elapsed / self.clearance_seconds)


def _clamp01(value: float) -> float:
    return 0.0 if value < 0 else 1.0 if value > 1 else value


@dataclass(frozen=True)
class SessionCounters:
    """Operator-facing session tallies. All derived, never invented."""

    frames: int = 0
    safe_frames: int = 0
    violation_frames: int = 0
    unknown_frames: int = 0
    incidents: int = 0
    acknowledgements: int = 0
    resets: int = 0
    rearm_count: int = 0
    #: Seconds spent per operator state, keyed by state name.
    state_durations: dict[str, float] = field(default_factory=dict)

    @property
    def safe_ratio(self) -> float:
        """Fraction of *decided* frames that were safe.

        ``UNKNOWN`` is excluded from the denominator on purpose: an empty frame
        is missing information, so counting it as unsafe would overstate risk
        and counting it as safe would manufacture compliance.
        """

        decided = self.safe_frames + self.violation_frames
        if decided <= 0:
            return 0.0
        return self.safe_frames / decided


@dataclass(frozen=True)
class HealthItem:
    """One row of the subsystem health panel."""

    name: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class DashboardSnapshot:
    """The complete picture a panel set needs for one repaint."""

    lifecycle: LifecycleStatus
    board: BoardSnapshot = field(default_factory=BoardSnapshot)
    gauge: GaugeSnapshot = field(default_factory=GaugeSnapshot)
    counters: SessionCounters = field(default_factory=SessionCounters)
    health: tuple[HealthItem, ...] = ()
    reset_blocker: ResetBlocker = ResetBlocker.NO_INCIDENT
    #: True when running on fixtures rather than hardware.
    demo_data: bool = False
    #: Set when the dashboard cannot trust its own view.
    banner: str = ""

    @property
    def state(self) -> OperatorState:
        return self.lifecycle.state

    @property
    def alarm(self) -> AlarmIntent:
        return self.lifecycle.alarm


@dataclass(frozen=True)
class TimelineEntry:
    """One row of the event timeline, combining laptop and board events."""

    at: float
    kind: str
    message: str
    state: str = ""
    incident_id: int | None = None
    #: Where the event came from: ``lifecycle``, ``tx``, ``rx``, ``operator``.
    source: str = "lifecycle"


def snapshot_from_lifecycle(
    status: LifecycleStatus,
    *,
    blocker: ResetBlocker = ResetBlocker.NO_INCIDENT,
    board: BoardSnapshot | None = None,
    gauge: GaugeSnapshot | None = None,
    counters: SessionCounters | None = None,
    health: tuple[HealthItem, ...] = (),
    demo_data: bool = False,
) -> DashboardSnapshot:
    """Assemble a snapshot from a lifecycle status plus optional extras."""

    return DashboardSnapshot(
        lifecycle=status,
        board=board or BoardSnapshot(),
        gauge=gauge or GaugeSnapshot(),
        counters=counters or SessionCounters(),
        health=health,
        reset_blocker=blocker,
        demo_data=demo_data,
    )
