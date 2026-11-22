"""Narrow adapters between the working core and the dashboard.

The dashboard is a *presentation* layer. It must not become a second
implementation of the safety logic, so every rule it needs is imported from the
module that already owns it:

- Lifecycle decisions: ``tools.incident_lifecycle``
- Temporal filter: ``tools.live_eye_shield_demo.TemporalFilter``
- Frame decision: ``tools.live_eye_shield_demo.decide_observation``
- Wire parsing: ``tools.pre_demo_reset.parse_reply`` / ``parse_status``
- Command correlation: ``tools.live_eye_shield_demo.CommandCorrelator``

This module adds only what a dashboard needs and the demo does not: a metrics
reader, a deterministic fixture source, and helpers that fold the results into
Qt-free snapshot objects. Nothing here writes firmware, sends ``ALL_OFF``, or
clears an incident by itself.

Reuse is done by import, not by copy, precisely so a future fix to the lifecycle
or the protocol cannot drift away from what the dashboard shows.
"""

from __future__ import annotations

import csv
import hashlib
import math
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from tools.incident_lifecycle import (
    AlarmIntent,
    IncidentLifecycle,
    OperatorState,
    ResetBlocker,
)

from .state_models import (
    BoardOutputs,
    BoardSnapshot,
    DashboardSnapshot,
    GaugeSnapshot,
    HealthItem,
    LinkState,
    OutputState,
    SessionCounters,
    TelemetrySample,
    TimelineEntry,
)
from . import theme


REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Firmware status parsing (delegated)
# ---------------------------------------------------------------------------


def _import_serial_helpers():
    """Import the demo's protocol helpers lazily.

    ``tools.live_eye_shield_demo`` pulls in torch, ultralytics and OpenCV at
    import time. The dashboard's fixture mode and its unit tests must work on a
    machine where those are broken or absent, so the import is deferred to the
    moment a real board is actually talked to.
    """

    from tools import live_eye_shield_demo as demo

    return demo


@dataclass(frozen=True)
class FirmwareStatus:
    """The board's own view of itself, adapted from ``pre_demo_reset``."""

    state: str
    incident: int
    ack: bool
    reset_ready: bool
    rearm: int
    green: bool
    red: bool
    buzzer: bool
    link: bool

    def to_outputs(self) -> BoardOutputs:
        return BoardOutputs(
            green=OutputState.ON if self.green else OutputState.OFF,
            red=OutputState.ON if self.red else OutputState.OFF,
            buzzer=OutputState.ON if self.buzzer else OutputState.OFF,
            link_healthy=self.link,
            firmware_state=self.state,
        )


def parse_firmware_status(line: str) -> FirmwareStatus | None:
    """Parse a full ``STATUS STATE=... ...`` line into a snapshot.

    Returns ``None`` for anything that is not a STATUS line, including the
    boot banner and the human-readable block the sketch also prints. Raises
    ``ValueError`` when a STATUS line is present but malformed - that is a real
    protocol fault and must be visible, not swallowed.
    """

    from tools.pre_demo_reset import parse_status

    text = line.strip()

    if not text.startswith("STATUS "):
        return None

    parsed = parse_status(text[len("STATUS ") :])

    return FirmwareStatus(
        state=parsed.state,
        incident=parsed.incident,
        ack=parsed.ack,
        reset_ready=parsed.reset_ready,
        rearm=parsed.rearm,
        green=parsed.green,
        red=parsed.red,
        buzzer=parsed.buzzer,
        link=parsed.link,
    )


def board_snapshot_from_status(
    port: str,
    baud: int,
    status: FirmwareStatus,
    now: float,
) -> BoardSnapshot:
    """Build the hardware panel's snapshot from a parsed STATUS."""

    return BoardSnapshot(
        port=port,
        baud=baud,
        link=LinkState.OPEN,
        outputs=status.to_outputs(),
        incident_id=status.incident,
        acknowledged=status.ack,
        reset_ready=status.reset_ready,
        rearm_count=status.rearm,
        status_at=now,
    )


# ---------------------------------------------------------------------------
# Incident-id agreement
# ---------------------------------------------------------------------------


def incident_disagreement(
    lifecycle_id: int,
    board: BoardSnapshot,
) -> str:
    """Describe a laptop/board incident mismatch, or return "".

    The dashboard runs *alongside* the live demo, so it must be able to say "the
    board is holding incident 1 while the laptop thinks there is no incident"
    without pretending either side is authoritative. Reset is blocked in exactly
    that situation, so surfacing it is the difference between a mystery and a
    diagnosis.
    """

    if board.incident_id is None:
        return ""

    if lifecycle_id == board.incident_id:
        return ""

    return (
        f"Incident mismatch: laptop reports {lifecycle_id or 'none'}, "
        f"board reports {board.incident_id or 'none'}"
    )


# ---------------------------------------------------------------------------
# Reset gate explanation
# ---------------------------------------------------------------------------


#: Age past which a parsed STATUS line no longer counts as a live link. The
#: firmware's own ``heartbeat_timeout_ms_`` is 3000 ms; a little slack avoids
#: flickering DEGRADED on a healthy 1 Hz poll.
STALE_STATUS_SECONDS = 5.0

#: Operator-facing reason each blocker stops a reset. Ordered the way
#: ``IncidentLifecycle.reset_blocker()`` evaluates them, so the panel can show
#: the *first* unmet condition rather than every downstream one.
RESET_BLOCKER_TEXT: dict[ResetBlocker, str] = {
    ResetBlocker.NONE: "All preconditions met - R will request a reset.",
    ResetBlocker.NO_INCIDENT: "No open incident to reset.",
    ResetBlocker.NOT_ACKNOWLEDGED: "The board has not confirmed an acknowledge yet.",
    ResetBlocker.NOT_SAFE_RECOVERED: "Recovery not proven for this incident.",
    ResetBlocker.HEALTH_INVALID: "Camera or model health is invalid.",
    ResetBlocker.LINK_INVALID: "Serial link is not healthy; the board will refuse.",
}


def reset_blocker_text(blocker: ResetBlocker) -> str:
    return RESET_BLOCKER_TEXT.get(blocker, f"Reset blocked ({blocker}).")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def build_health_items(
    *,
    camera_ok: bool | None,
    model_ok: bool | None,
    serial_ok: bool | None,
    serial_detail: str = "",
    status_age_seconds: float | None = None,
    demo_data: bool = False,
    missing_logo: bool = False,
) -> tuple[HealthItem, ...]:
    """Assemble the subsystem health rows.

    ``Blynk`` and ``Wi-Fi`` are always ``NOT CONFIGURED`` in this build. Showing
    them as OFFLINE would imply a fault; showing them as ONLINE would be a lie.
    """

    def tri(value: bool | None, detail_ok: str, detail_bad: str) -> HealthItem:
        if value is None:
            return HealthItem("", theme.DISABLED, "not checked yet")
        if value:
            return HealthItem("", theme.ONLINE, detail_ok)
        return HealthItem("", theme.OFFLINE, detail_bad)

    rows: list[HealthItem] = []

    if demo_data:
        rows.append(HealthItem("Camera", theme.DISABLED, "fixtures (demo data)"))
        rows.append(HealthItem("Model", theme.DISABLED, "fixtures (demo data)"))
        rows.append(HealthItem("Serial link", theme.DISABLED, "fixtures (demo data)"))
    else:
        cam = tri(camera_ok, "frames arriving", "no frames")
        rows.append(HealthItem("Camera", cam.status, cam.detail))

        model = tri(model_ok, "inference running", "inference failed")
        rows.append(HealthItem("Model", model.status, model.detail))

        if serial_ok:
            age = (
                f", status {status_age_seconds:.1f}s old"
                if status_age_seconds is not None
                else ""
            )
            # A link that answers but has stopped sending STATUS is not healthy:
            # the firmware declares its own link stale after 3 s of no
            # HEARTBEAT, so a screen claiming ONLINE on a 30 s-old sample would
            # contradict the board it is describing.
            stale = (
                status_age_seconds is not None
                and status_age_seconds > STALE_STATUS_SECONDS
            )
            rows.append(
                HealthItem(
                    "Serial link",
                    theme.DEGRADED if stale else theme.ONLINE,
                    (serial_detail or "connected") + age,
                )
            )
        elif serial_ok is None:
            rows.append(HealthItem("Serial link", theme.DISABLED, "not opened yet"))
        else:
            rows.append(
                HealthItem(
                    "Serial link",
                    theme.OFFLINE,
                    serial_detail or "no reply from board",
                )
            )

    rows.append(HealthItem("Blynk", theme.NOT_CONFIGURED, "not part of this build"))
    rows.append(HealthItem("Wi-Fi", theme.NOT_CONFIGURED, "not part of this build"))
    rows.append(
        HealthItem(
            "Branding assets",
            theme.DEGRADED if missing_logo else theme.ONLINE,
            "placeholder in use" if missing_logo else "present",
        )
    )

    return tuple(rows)


# ---------------------------------------------------------------------------
# Metrics CSV
# ---------------------------------------------------------------------------


#: Columns the metrics panel needs. Read from the header at runtime rather than
#: assuming an order, because the CSV is experiment data and may gain columns.
METRICS_REQUIRED_COLUMNS = (
    "test_id",
    "model",
    "dataset",
    "precision",
    "recall",
    "f1",
    "map50",
    "map50_95",
)


@dataclass(frozen=True)
class MetricsRow:
    """One locked-test result from ``experiments/ai_metrics.csv``."""

    test_id: str
    model: str
    dataset: str
    split: str
    test_images: str
    precision: float
    recall: float
    f1: float
    map50: float
    map50_95: float
    notes: str
    recorded_at: str


@dataclass(frozen=True)
class MetricsTable:
    """The parsed metrics CSV plus why it may be unavailable."""

    rows: tuple[MetricsRow, ...] = ()
    path: Path | None = None
    error: str = ""

    @property
    def available(self) -> bool:
        return bool(self.rows) and not self.error


def metrics_path(root: Path | None = None) -> Path:
    return (root or REPO_ROOT) / "experiments" / "ai_metrics.csv"


def _to_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def load_metrics(path: Path | None = None) -> MetricsTable:
    """Read the AI metrics CSV, degrading to an explicit error string.

    A missing or malformed file must not stop the dashboard from starting: the
    metrics panel is evidence, not safety equipment. It reports the problem
    instead of showing invented numbers.
    """

    target = path or metrics_path()

    if not target.is_file():
        return MetricsTable(path=target, error=f"Metrics file not found: {target}")

    try:
        with target.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)

            if reader.fieldnames is None:
                return MetricsTable(path=target, error="Metrics file is empty.")

            missing = [c for c in METRICS_REQUIRED_COLUMNS if c not in reader.fieldnames]

            if missing:
                return MetricsTable(
                    path=target,
                    error="Metrics file is missing columns: " + ", ".join(missing),
                )

            rows = tuple(
                MetricsRow(
                    test_id=row.get("test_id", "").strip(),
                    model=row.get("model", "").strip(),
                    dataset=row.get("dataset", "").strip(),
                    split=row.get("split", "").strip(),
                    test_images=row.get("test_images", "").strip(),
                    precision=_to_float(row.get("precision", "")),
                    recall=_to_float(row.get("recall", "")),
                    f1=_to_float(row.get("f1", "")),
                    map50=_to_float(row.get("map50", "")),
                    map50_95=_to_float(row.get("map50_95", "")),
                    notes=(row.get("notes", "") or "").strip(),
                    recorded_at=(row.get("recorded_at", "") or "").strip(),
                )
                for row in reader
            )
    except (OSError, csv.Error, UnicodeDecodeError) as error:
        return MetricsTable(path=target, error=f"Could not read metrics: {error}")

    if not rows:
        return MetricsTable(path=target, error="Metrics file has no data rows.")

    return MetricsTable(rows=rows, path=target)


# ---------------------------------------------------------------------------
# Session counters
# ---------------------------------------------------------------------------


#: Frames keyed the same way the demo keys them.
_COUNTER_FIELD = {
    "SAFE": "safe_frames",
    "VIOLATION": "violation_frames",
    "UNKNOWN": "unknown_frames",
}


@dataclass
class SessionAccumulator:
    """Mutable tally that produces immutable ``SessionCounters``.

    Durations are accumulated from monotonic timestamps, never wall-clock, so a
    clock change mid-demo cannot invent or erase time in a state.
    """

    frames: int = 0
    safe_frames: int = 0
    violation_frames: int = 0
    unknown_frames: int = 0
    incidents: int = 0
    acknowledgements: int = 0
    resets: int = 0
    rearm_count: int = 0
    state_durations: dict[str, float] = field(default_factory=dict)
    _last_state: str = ""
    _last_at: float | None = None

    def record_frame(self, observation: str) -> None:
        self.frames += 1
        field_name = _COUNTER_FIELD.get(observation)
        if field_name:
            setattr(self, field_name, getattr(self, field_name) + 1)

    def record_state(self, state: str, now: float) -> None:
        """Credit time to the *previous* state, then switch.

        Crediting on exit rather than on entry means a state that is still
        running is not double counted, and the last sample is closed by
        ``close()``.
        """

        if self._last_state and self._last_at is not None and now > self._last_at:
            elapsed = now - self._last_at
            self.state_durations[self._last_state] = (
                self.state_durations.get(self._last_state, 0.0) + elapsed
            )

        if state != self._last_state:
            self._last_state = state

        self._last_at = now

    def close(self, now: float) -> None:
        if self._last_state and self._last_at is not None and now > self._last_at:
            elapsed = now - self._last_at
            self.state_durations[self._last_state] = (
                self.state_durations.get(self._last_state, 0.0) + elapsed
            )
        self._last_at = now

    def snapshot(self, *, rearm_count: int | None = None) -> SessionCounters:
        return SessionCounters(
            frames=self.frames,
            safe_frames=self.safe_frames,
            violation_frames=self.violation_frames,
            unknown_frames=self.unknown_frames,
            incidents=self.incidents,
            acknowledgements=self.acknowledgements,
            resets=self.resets,
            rearm_count=self.rearm_count if rearm_count is None else rearm_count,
            state_durations=dict(self.state_durations),
        )


def state_duration_shares(
    durations: dict[str, float],
) -> tuple[tuple[str, float], ...]:
    """Normalise durations to (state, share) pairs sorted descending.

    Zero-length states are dropped so the chart does not draw invisible slices
    with labels stacked on top of each other.
    """

    total = sum(max(0.0, v) for v in durations.values())

    if total <= 0:
        return ()

    pairs = [(name, value / total) for name, value in durations.items() if value > 0]
    pairs.sort(key=lambda item: item[1], reverse=True)
    return tuple(pairs)


# ---------------------------------------------------------------------------
# Deterministic demo data
# ---------------------------------------------------------------------------


#: The scripted demo storyline: (seconds to hold, action).
#:
#: The story is chosen to visit every operator state in order, so a fixture run
#: exercises the whole lifecycle contract - a pending violation that is
#: cancelled, a real latch, recovery, acknowledge, reset - rather than only the
#: happy path. Each action is fed to the *real* ``IncidentLifecycle``; the only
#: thing replaced is the camera.
DEMO_SCRIPT: tuple[tuple[float, str], ...] = (
    (2.0, "SAFE"),
    (1.0, "VIOLATION_PENDING"),   # cancelled by the next SAFE step
    (1.0, "SAFE"),
    (2.6, "VIOLATION_PENDING"),   # runs long enough to latch
    (2.4, "SAFE_CLEAR_PENDING"),  # recovery timer, then SAFE_RECOVERED
    (2.0, "ACKNOWLEDGE"),
    (2.0, "RESET"),
    (1.5, "SAFE"),
)


class _FakeReply:
    """Minimal ``SerialReply`` stand-in used by fixture mode.

    Duck-typed on purpose: ``confirm_acknowledge`` and
    ``resolve_pending_command`` only ever read ``ok``, ``code`` and ``detail``,
    so fixture mode satisfies the same interface a real reply does instead of
    needing a branch inside the lifecycle.
    """

    __slots__ = ("ok", "code", "detail")

    def __init__(self, ok: bool, code: str, detail: str = "") -> None:
        self.ok = ok
        self.code = code
        self.detail = detail

    @property
    def rejected(self) -> bool:
        return not self.ok

    @property
    def raw(self) -> str:
        return f"{'OK' if self.ok else 'ERR'} {self.code} {self.detail}".strip()


#: What the firmware would answer for each lifecycle command. Fixture mode uses
#: this table so the acknowledge path still requires a reply, exactly as on
#: hardware - a demo build that confirmed acknowledges locally would hide the
#: very bug this project spent weeks fixing.
_FIXTURE_REPLIES: dict[str, str] = {
    "INCIDENT": "ALARM_LATCHED",
    "NO_INCIDENT": "NO_INCIDENT",
    "SILENCE": "SILENCED",
    "ACKNOWLEDGE": "ACKNOWLEDGED",
    "RESET_READY": "RESET_READY",
    "RESET": "RESET_ACCEPTED",
}


class DemoDataDriver:
    """Drive a real ``IncidentLifecycle`` from a fixed script.

    Deliberately *not* a generator of fake numbers. It feeds observations and
    operator presses through the same ``update()``, ``acknowledge()``,
    ``request_reset()`` and ``resolve_pending_command()`` calls the camera loop
    uses, with a simulated firmware answering in order. Any defect in the
    lifecycle therefore shows up in fixture mode too, which is what makes the
    fixtures worth having.

    The caller supplies ``now`` and ``dt``, so a whole demo can be replayed
    deterministically in a test without sleeping.
    """

    def __init__(
        self,
        lifecycle: IncidentLifecycle | None = None,
        *,
        script: Sequence[tuple[float, str]] = DEMO_SCRIPT,
        confirmation_seconds: float = 2.0,
        clearance_seconds: float = 2.0,
    ) -> None:
        from tools.live_eye_shield_demo import Observation

        self._Observation = Observation
        self.lifecycle = lifecycle or IncidentLifecycle(
            confirmation_seconds=confirmation_seconds,
            clearance_seconds=clearance_seconds,
        )
        self.script = tuple(script)
        self.step_index = 0
        self.step_elapsed = 0.0
        self.accumulator = SessionAccumulator()
        self.timeline: list[TimelineEntry] = []
        self.started = False
        self.current_observation = Observation.UNKNOWN
        #: Commands handed to the simulated firmware, for the TX log.
        self.sent_commands: list[str] = []
        self._drain_index = 0
        self._pressed_in_step = ""
        self._run_observation = None
        self._run_started = 0.0
        self._outbox: list[str] = []

    # -- lifecycle plumbing -------------------------------------------------

    def _log(
        self,
        now: float,
        kind: str,
        message: str,
        source: str = "lifecycle",
    ) -> None:
        status = self.lifecycle.status()
        self.timeline.append(
            TimelineEntry(
                at=now,
                kind=kind,
                message=message,
                state=status.state.value,
                incident_id=status.incident_id,
                source=source,
            )
        )
        if len(self.timeline) > 400:
            del self.timeline[: len(self.timeline) - 400]

    def _drain_transitions(self, now: float) -> None:
        """Mirror new lifecycle transitions into the timeline."""

        for transition in self.lifecycle.transitions[self._drain_index :]:
            detail = f"  ({transition.detail})" if transition.detail else ""
            self._log(
                now,
                transition.event.value,
                f"{transition.from_state.value} \u2192 {transition.to_state.value}{detail}",
            )
        self._drain_index = len(self.lifecycle.transitions)

    def prime(self, now: float) -> None:
        """Start the lifecycle and declare the link healthy, once."""

        if self.started:
            return

        self.lifecycle.set_health(
            camera_ok=True,
            model_ok=True,
            serial_ok=True,
            now=now,
            reason="fixture session",
        )
        self.lifecycle.start(now)
        self.started = True
        self._drain_index = len(self.lifecycle.transitions)
        self._log(now, "SESSION_START", "Fixture session started", source="operator")

    # -- stepping -----------------------------------------------------------

    def step(self, now: float, dt: float) -> str | None:
        """Advance the script, feed the lifecycle, and answer the firmware.

        Returns the last command the simulated board received, which the caller
        may append to a TX log.
        """

        self.prime(now)

        # Clamp at the end rather than looping: a repeating story would keep
        # minting new incident ids and the session counters would belong to
        # several unrelated episodes.
        index = min(self.step_index, len(self.script) - 1)
        duration, action = self.script[index]
        if self.step_index < len(self.script):
            self.step_elapsed += dt

        observation, press = self._resolve_action(action)
        self.current_observation = observation

        # Timer continuity matters. The real camera loop hands the frame filter's
        # ``pending_since``/``safe_since`` straight through, so a run of identical
        # observations shares one start time. Recomputing the start on every tick
        # would reset the confirmation timer to zero each frame and the lifecycle
        # would never latch. Track the run here for the same reason.
        if observation != self._run_observation:
            self._run_observation = observation
            self._run_started = now

        incident_id = (
            self.lifecycle.incident.incident_id if self.lifecycle.incident else None
        )

        self.lifecycle.update(
            observation,
            now,
            violation_ratio=1.0 if observation == self._Observation.VIOLATION else 0.0,
            pending_since=(
                self._run_started
                if observation == self._Observation.VIOLATION
                else None
            ),
            safe_since=(
                self._run_started if observation == self._Observation.SAFE else None
            ),
        )

        # Recovery evidence only exists if the detector observed it. The demo
        # calls this from the camera loop, so fixture mode must too - otherwise
        # reset would be blocked for a recovery that really did happen.
        if (
            self.lifecycle.incident is not None
            and self.lifecycle.incident.safe_recovered
        ):
            self.lifecycle.mark_onboard_verified()

        last_command = self._service_firmware(now)

        if press:
            if press != self._pressed_in_step:
                self._pressed_in_step = press
                self._apply_press(press, now)
                served = self._service_firmware(now)
                last_command = served or last_command
        else:
            self._pressed_in_step = ""

        self.accumulator.record_frame(observation.value)
        status = self.lifecycle.status()
        self.accumulator.record_state(status.state.value, now)
        self.accumulator.incidents = max(
            self.accumulator.incidents, status.incident_id or 0
        )
        self._drain_transitions(now)

        if self.step_elapsed >= duration and self.step_index < len(self.script):
            self.step_elapsed = 0.0
            self.step_index += 1

        return last_command

    # -- simulated firmware -------------------------------------------------

    def _service_firmware(self, now: float) -> str | None:
        """Send queued commands and apply the replies the board would give.

        This mirrors the live loop exactly, including the part that is easy to
        get wrong: the reset token lives in ``pending_reset_command`` rather than
        in the transition log, and the reply to ``RESET_READY`` produces the
        ``RESET`` that must then be sent and answered. Driving that whole
        two-round-trip handshake here is what makes fixture mode a real test of
        the reset gate instead of a shortcut around it.
        """

        last: str | None = None

        # Bounded so a protocol mistake surfaces as a stop rather than a hang.
        for _ in range(32):
            command = self._next_outbound()

            if command is None:
                break

            self.sent_commands.append(command)
            last = command

            name = command.split()[0].upper()
            code = _FIXTURE_REPLIES.get(name)

            if code is None:
                # Not a protocol command; the firmware would print an error.
                continue

            reply = _FakeReply(True, code, self._incident_id_text())

            if code == "ACKNOWLEDGED":
                incident_id = (
                    self.lifecycle.incident.incident_id
                    if self.lifecycle.incident
                    else None
                )
                if (
                    self.lifecycle.confirm_acknowledge(reply, now, incident_id)
                    == "CONFIRMED"
                ):
                    self.accumulator.acknowledgements += 1

            if code == "RESET_ACCEPTED":
                self.accumulator.resets += 1

            follow_up = self.lifecycle.resolve_pending_command(reply)

            if follow_up is not None:
                self._outbox.append(follow_up)

        return last

    def _next_outbound(self) -> str | None:
        """The next thing the board would receive, or None when idle."""

        if self._outbox:
            return self._outbox.pop(0)

        token = self.lifecycle.pending_reset_command

        if token is not None:
            return token

        return self.lifecycle.take_pending_command()

    def _incident_id_text(self) -> str:
        return (
            str(self.lifecycle.incident.incident_id)
            if self.lifecycle.incident
            else ""
        )

    # -- operator presses ---------------------------------------------------

    def _apply_press(self, press: str, now: float) -> None:
        if press == "ACK":
            ok, reason = self.lifecycle.acknowledge(now)
            self._log(
                now,
                "ACKNOWLEDGE_REQUESTED" if ok else "ACKNOWLEDGE_REJECTED",
                reason,
                source="operator",
            )
            return

        if press == "RESET":
            started, reason = self.lifecycle.request_reset(now)
            self._log(
                now,
                "RESET_REQUESTED" if started else "RESET_REJECTED",
                reason,
                source="operator",
            )

    # -- action mapping -----------------------------------------------------

    def _resolve_action(self, action: str):
        """Map a script action to an observation plus an optional press."""

        O = self._Observation

        mapping = {
            "SAFE": (O.SAFE, ""),
            "VIOLATION_PENDING": (O.VIOLATION, ""),
            "ALARM_LATCHED": (O.VIOLATION, ""),
            "SAFE_CLEAR_PENDING": (O.SAFE, ""),
            "SAFE_RECOVERED": (O.SAFE, ""),
            "UNKNOWN": (O.UNKNOWN, ""),
            "ACKNOWLEDGE": (O.SAFE, "ACK"),
            "RESET": (O.SAFE, "RESET"),
        }

        return mapping.get(action, (O.UNKNOWN, ""))

    # -- inspection ---------------------------------------------------------

    def snapshot_board(self) -> BoardSnapshot:
        """A fixture ``BoardSnapshot`` derived from lifecycle state.

        Not random: the pins are derived from the same intent table the firmware
        uses, so the fixture hardware panel shows what the real board *would*
        be doing rather than decorative values.
        """

        status = self.lifecycle.status()
        incident = status.incident_id or 0

        green = status.state is OperatorState.SAFE
        red = status.state in (
            OperatorState.ALARM_LATCHED,
            OperatorState.SAFE_CLEAR_PENDING,
            OperatorState.SAFE_RECOVERED,
            OperatorState.ACKNOWLEDGED,
        )
        buzzer = status.alarm is AlarmIntent.SOUNDING

        firmware_state = {
            OperatorState.SAFE: "SAFE",
            OperatorState.ALARM_LATCHED: "ALARM_LATCHED",
            OperatorState.SAFE_CLEAR_PENDING: "ALARM_LATCHED",
            OperatorState.SAFE_RECOVERED: "READY_TO_RESET",
            OperatorState.ACKNOWLEDGED: "ACKNOWLEDGED",
            OperatorState.FAULT: "FAULT",
        }.get(status.state, "SAFE")

        return BoardSnapshot(
            port="(demo data)",
            baud=115200,
            link=LinkState.OPEN,
            outputs=BoardOutputs(
                green=OutputState.ON if green else OutputState.OFF,
                red=OutputState.ON if red else OutputState.OFF,
                buzzer=OutputState.ON if buzzer else OutputState.OFF,
                link_healthy=True,
                firmware_state=firmware_state,
            ),
            incident_id=incident,
            acknowledged=status.acknowledged,
            reset_ready=status.safe_recovered and status.acknowledged,
            rearm_count=status.rearm_count,
        )

    def gauge(self) -> GaugeSnapshot:
        """Fixture gauge from the lifecycle's own timer values."""

        status = self.lifecycle.status()
        observation = self.current_observation.value

        return GaugeSnapshot(
            observation=observation,
            state=status.state.value,
            violation_ratio=status.violation_ratio,
            pending_elapsed=status.pending_elapsed,
            confirmation_seconds=status.confirmed_after_seconds
            or self.lifecycle.confirmation_seconds,
            safe_clear_elapsed=status.safe_clear_elapsed,
            clearance_seconds=self.lifecycle.clearance_seconds,
            confirmed_after_seconds=status.confirmed_after_seconds,
        )

# ---------------------------------------------------------------------------
# Deterministic telemetry fixtures
# ---------------------------------------------------------------------------


def fixture_telemetry(
    step: int,
    *,
    state: str = "SAFE",
    seed: int = 20240501,
) -> TelemetrySample:
    """A repeatable telemetry sample for a given step index.

    Seeded from ``step`` alone, so two runs of the same storyline produce byte
    identical charts. That is what makes the fixture charts testable instead of
    decorative.
    """

    rng = random.Random(seed + step)

    # A mild sine keeps the waveform readable rather than pure noise.
    base_fps = 30.0 + 2.5 * math.sin(step / 9.0)
    base_ms = 18.0 + 2.0 * math.cos(step / 7.0)

    fps = max(1.0, base_fps + rng.uniform(-1.2, 1.2))

    if state in ("ALARM_LATCHED", "SAFE_CLEAR_PENDING"):
        inference_ms = max(4.0, base_ms + rng.uniform(1.5, 4.0))
    else:
        inference_ms = max(4.0, base_ms + rng.uniform(-1.0, 1.0))

    detections = "+" if state in ("VIOLATION_PENDING", "ALARM_LATCHED") else (0,)
    count = rng.choice(detections) if isinstance(detections, tuple) else 1
    confidence = 0.0 if count == 0 else round(rng.uniform(0.62, 0.93), 3)

    return TelemetrySample(
        at=float(step),
        fps=round(fps, 2),
        inference_ms=round(inference_ms, 2),
        frame_age_ms=round(inference_ms + rng.uniform(2.0, 9.0), 2),
        detection_count=count,
        max_confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def fingerprint(path: Path) -> str:
    """Short SHA-256 of a file, for the model-integrity line in the UI.

    Read-only and streaming so a 6 MB weight file costs nothing noticeable and
    is never modified.
    """

    if not path.is_file():
        return ""

    digest = hashlib.sha256()

    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return ""

    return digest.hexdigest()[:12]


def file_size_mb(path: Path) -> str:
    try:
        return f"{path.stat().st_size / (1024 * 1024):.1f} MB"
    except OSError:
        return ""


def format_seconds(value: float | None) -> str:
    if value is None:
        return "--"
    return f"{value:.2f} s"


def format_percent(value: float | None, digits: int = 1) -> str:
    if value is None or isinstance(value, float) and math.isnan(value):
        return "--"
    return f"{value * 100:.{digits}f}%"


def format_metric(value: float | None, digits: int = 4) -> str:
    if value is None or isinstance(value, float) and math.isnan(value):
        return "--"
    return f"{value:.{digits}f}"
