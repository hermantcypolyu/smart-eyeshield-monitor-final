"""Live AI eye-shield monitor with ESP32 serial integration.

Current integration:
- YOLOv8n webcam inference
- Class-specific confidence thresholds
- Two-second temporal violation confirmation
- One latched incident
- Text-command serial control of ESP32-S3
- SAFE and ALARM hardware output
- Optional V2 live-acceptance CSV logging

Not yet included:
- Final JSON serial protocol
- ESP32 acknowledgement
- Acknowledge / conditional reset
- Serial heartbeat and reconnect
- Blynk

Suppression / NMS semantics
---------------------------
``--iou`` sets the NMS IoU threshold handed to Ultralytics. It is easy to
get backwards:

- A **lower** IoU threshold suppresses more aggressively. Two boxes for
  the same face that overlap by more than the threshold are merged, so
  only the highest-scoring one survives.
- A **higher** IoU threshold keeps more overlapping boxes alive.

The Domain V2 model produced duplicate stacked ``no_goggles`` boxes on
single faces at IoU 0.40, which lowered precision and produced repeated
detections. Lowering IoU (0.30, or 0.25 if duplicates persist) is the
correct response. Raising IoU would have made the duplicates worse.

Live acceptance presets (see ``docs/v2_live_acceptance.md``):

    Preset A: --candidate-confidence 0.40 --goggles-confidence 0.60
              --no-goggles-confidence 0.55 --iou 0.30
    Preset B: --candidate-confidence 0.50 --goggles-confidence 0.65
              --no-goggles-confidence 0.60 --iou 0.30

Diagnostics never change the frame decision. The duplicate-box count is a
measurement of already-accepted boxes only; no second NMS and no
re-filtering is applied.

Timer semantics
---------------
``RAW`` is the decision from this single frame; ``STATE`` is the latched,
time-filtered application state. They deliberately diverge: after an
incident is confirmed, a frame that sees no face still reports
``RAW: UNKNOWN`` while ``STATE`` stays ``VIOLATION_CONFIRMED``.

Every duration uses ``time.monotonic()``, never wall-clock time and never
frame counts, so the threshold is unaffected by frame-rate changes.

    UNKNOWN                 TIMER: IDLE
    SAFE                    TIMER: IDLE
    VIOLATION_PENDING       CONFIRM TIMER: X.XX / <confirmation> s
    VIOLATION_CONFIRMED     CONFIRMED AFTER: X.XX s
    SAFE_CLEAR_PENDING      CLEAR TIMER: X.XX / <clearance> s

The confirmation duration is measured once, when the incident is created,
and then held. Continued violation or UNKNOWN frames cannot reset it to
zero.

A pending violation is cancelled when UNKNOWN lasts longer than
``--unknown-tolerance-seconds`` after the last accepted VIOLATION frame. A
SAFE frame cancels it immediately. Neither case creates an incident.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import cv2
import serial
import torch
from ultralytics import YOLO

# Allow both ``python -m tools.live_eye_shield_demo`` and
# ``python tools/live_eye_shield_demo.py``.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.incident_lifecycle import (
    AlarmIntent,
    IncidentLifecycle,
    OperatorState,
    ResetBlocker,
)
from src.blynk_client import (
    BlynkClient,
    BlynkConfigError,
    LifecycleReading,
)
from tools.v2_acceptance_schema import (
    CONDITIONS,
    CSV_COLUMNS as ACCEPTANCE_COLUMNS,
    NO_DETECTION_CLASS,
    POSES,
    count_duplicate_overlaps,
    sha256_of_file,
)


class Observation(str, Enum):
    """Raw decision from one processed frame."""

    UNKNOWN = "UNKNOWN"
    SAFE = "SAFE"
    VIOLATION = "VIOLATION"


class State(str, Enum):
    """Time-filtered application state."""

    UNKNOWN = "UNKNOWN"
    SAFE = "SAFE"
    VIOLATION_PENDING = "VIOLATION_PENDING"
    VIOLATION_CONFIRMED = "VIOLATION_CONFIRMED"
    SAFE_CLEAR_PENDING = "SAFE_CLEAR_PENDING"


@dataclass(frozen=True)
class TemporalStatus:
    """One filter result, with each timer kept as its own named value.

    A single ``timer_seconds`` used to be reused for every state, so the
    overlay could only guess which clock it was rendering and a confirmed
    incident showed ``0.00``. Each field here is ``None`` when it does not
    apply to ``state``, which makes a wrong timer impossible to show.
    """

    state: State
    violation_ratio: float
    pending_elapsed: float | None = None
    confirmed_after_seconds: float | None = None
    safe_clear_elapsed: float | None = None


class TemporalFilter:
    """Time-based violation confirmation and safe-clear filter."""

    def __init__(
        self,
        confirmation_seconds: float = 2.0,
        window_seconds: float = 2.0,
        clearance_seconds: float = 2.0,
        violation_ratio_required: float = 0.80,
        unknown_tolerance_seconds: float = 0.35,
    ) -> None:
        self.confirmation_seconds = confirmation_seconds
        self.window_seconds = window_seconds
        self.clearance_seconds = clearance_seconds
        self.violation_ratio_required = violation_ratio_required
        self.unknown_tolerance_seconds = unknown_tolerance_seconds

        # All timestamps are time.monotonic() values supplied by the
        # caller, never wall-clock time.
        self.pending_since: float | None = None
        self.safe_since: float | None = None
        self.last_violation_time: float | None = None

        self.confirmed = False
        self.confirmed_at: float | None = None
        self.confirmed_after_seconds: float | None = None
        self.incident_id: int | None = None
        self.next_incident_id = 1

        self.observations: deque[tuple[float, Observation]] = deque()
        self.state = State.UNKNOWN

    def _remove_old_samples(self, now: float) -> None:
        cutoff = now - self.window_seconds

        while self.observations and self.observations[0][0] < cutoff:
            self.observations.popleft()

    def _violation_ratio(self) -> float:
        valid_observations = [
            observation
            for _, observation in self.observations
            if observation != Observation.UNKNOWN
        ]

        if not valid_observations:
            return 0.0

        violation_count = sum(
            observation == Observation.VIOLATION
            for observation in valid_observations
        )

        return violation_count / len(valid_observations)

    def update(
        self,
        observation: Observation,
        now: float,
    ) -> TemporalStatus:
        self.observations.append((now, observation))
        self._remove_old_samples(now)

        ratio = self._violation_ratio()

        # No confirmed incident yet.
        if not self.confirmed:
            if observation == Observation.SAFE:
                # A real SAFE observation cancels a pending violation.
                self.pending_since = None
                self.last_violation_time = None
                self.safe_since = now
                self.state = State.SAFE

                return TemporalStatus(
                    state=self.state,
                    violation_ratio=ratio,
                )

            if observation == Observation.UNKNOWN:
                # UNKNOWN must never become SAFE.
                if self.pending_since is None:
                    self.state = State.UNKNOWN

                    return TemporalStatus(
                        state=self.state,
                        violation_ratio=ratio,
                    )

                # Allow a brief missed detection during a pending
                # violation. Repeat the original elapsed time so the
                # operator keeps seeing the same running clock.
                if (
                    self.last_violation_time is not None
                    and now - self.last_violation_time
                    <= self.unknown_tolerance_seconds
                ):
                    self.state = State.VIOLATION_PENDING

                    return TemporalStatus(
                        state=self.state,
                        violation_ratio=ratio,
                        pending_elapsed=now - self.pending_since,
                    )

                # UNKNOWN lasted longer than unknown_tolerance_seconds
                # past the last accepted VIOLATION frame. Cancel the
                # pending confirmation; no incident is created.
                self.pending_since = None
                self.last_violation_time = None
                self.state = State.UNKNOWN

                return TemporalStatus(
                    state=self.state,
                    violation_ratio=ratio,
                )

            # Current observation is VIOLATION.
            self.last_violation_time = now
            self.safe_since = None

            if self.pending_since is None:
                self.pending_since = now

            elapsed = now - self.pending_since

            if (
                elapsed >= self.confirmation_seconds
                and ratio >= self.violation_ratio_required
            ):
                self.confirmed = True
                self.incident_id = self.next_incident_id
                self.next_incident_id += 1
                self.confirmed_at = now

                # Freeze the measured duration once, at confirmation, so
                # later frames cannot overwrite it with 0.00.
                self.confirmed_after_seconds = elapsed
                self.state = State.VIOLATION_CONFIRMED

                return TemporalStatus(
                    state=self.state,
                    violation_ratio=ratio,
                    confirmed_after_seconds=elapsed,
                )

            self.state = State.VIOLATION_PENDING

            return TemporalStatus(
                state=self.state,
                violation_ratio=ratio,
                pending_elapsed=elapsed,
            )

        # A confirmed incident already exists.
        if observation == Observation.SAFE:
            if self.safe_since is None:
                self.safe_since = now

            self.state = State.SAFE_CLEAR_PENDING

            return TemporalStatus(
                state=self.state,
                violation_ratio=ratio,
                confirmed_after_seconds=self.confirmed_after_seconds,
                safe_clear_elapsed=now - self.safe_since,
            )

        # UNKNOWN or continued VIOLATION cancels safe-clear progress. The
        # incident stays latched and its measured duration is preserved.
        self.safe_since = None
        self.state = State.VIOLATION_CONFIRMED

        return TemporalStatus(
            state=self.state,
            violation_ratio=ratio,
            confirmed_after_seconds=self.confirmed_after_seconds,
        )


def choose_device(requested_device: str) -> str:
    """Select Apple MPS automatically, otherwise CPU."""

    if requested_device != "auto":
        return requested_device

    if torch.backends.mps.is_available():
        return "mps"

    return "cpu"


def decide_observation(
    detections: list[tuple[str, float]],
) -> Observation:
    """Convert accepted detections into one conservative frame decision.

    Safety policy:
    - Any accepted no_goggles detection means VIOLATION.
    - Goggles means SAFE only if no accepted no_goggles exists.
    - No accepted detections means UNKNOWN.
    """

    if not detections:
        return Observation.UNKNOWN

    has_goggles = any(
        class_name == "goggles"
        for class_name, _ in detections
    )

    has_no_goggles = any(
        class_name == "no_goggles"
        for class_name, _ in detections
    )

    if has_no_goggles:
        return Observation.VIOLATION

    if has_goggles:
        return Observation.SAFE

    return Observation.UNKNOWN


def state_colour(state: State) -> tuple[int, int, int]:
    """Return BGR overlay colour."""

    return {
        State.UNKNOWN: (150, 150, 150),
        State.SAFE: (0, 210, 0),
        State.VIOLATION_PENDING: (0, 210, 255),
        State.VIOLATION_CONFIRMED: (0, 0, 255),
        State.SAFE_CLEAR_PENDING: (255, 180, 0),
    }[state]


def operator_colour(state: OperatorState) -> tuple[int, int, int]:
    """Return BGR panel colour for the latched operator state.

    The panel is driven by the operator lifecycle, not the raw frame state,
    because the thing the operator must be able to read at a glance is
    "is an incident open", not "what did this one frame contain".
    """

    return {
        OperatorState.STARTUP: (150, 150, 150),
        OperatorState.WAITING_FOR_TARGET: (150, 150, 150),
        OperatorState.SAFE: (0, 210, 0),
        OperatorState.VIOLATION_PENDING: (0, 210, 255),
        OperatorState.ALARM_LATCHED: (0, 0, 255),
        OperatorState.SAFE_CLEAR_PENDING: (255, 180, 0),
        OperatorState.SAFE_RECOVERED: (0, 200, 255),
        OperatorState.ACKNOWLEDGED: (0, 120, 255),
        OperatorState.FAULT: (255, 0, 255),
    }[state]


def alarm_intent_to_gpio(alarm: AlarmIntent) -> tuple[int, int, int]:
    """Map an :class:`AlarmIntent` to physical (green, red, buzzer) levels.

    This is the same meaning the firmware enforces on IO11/IO12/IO14, so the
    Blynk dashboard mirrors what the operator would actually see on the board.
    ``SILENCED`` still holds the red LED on (buzzer acknowledged away), which
    is exactly why ``ACKNOWLEDGED`` must keep showing red=1 in the cloud.
    """

    return {
        AlarmIntent.SILENT: (1, 0, 0),    # green only, nothing wrong
        AlarmIntent.WAITING: (0, 0, 0),   # nothing on yet
        AlarmIntent.SOUNDING: (0, 1, 1),  # red + buzzer
        AlarmIntent.SILENCED: (0, 1, 0),  # red held, buzzer acknowledged
        AlarmIntent.FAULT: (0, 0, 0),     # parked; no trusted output
    }[alarm]


def detection_colour(class_name: str) -> tuple[int, int, int]:
    """Return BGR bounding-box colour."""

    if class_name == "goggles":
        return (255, 80, 0)

    return (0, 0, 255)


def draw_detection(
    frame,
    class_name: str,
    confidence: float,
    coordinates: tuple[int, int, int, int],
) -> None:
    """Draw one accepted detection."""

    x1, y1, x2, y2 = coordinates
    colour = detection_colour(class_name)

    cv2.rectangle(
        frame,
        (x1, y1),
        (x2, y2),
        colour,
        3,
    )

    label = f"{class_name} {confidence:.2f}"

    (text_width, text_height), _ = cv2.getTextSize(
        label,
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        2,
    )

    label_top = max(0, y1 - text_height - 14)

    cv2.rectangle(
        frame,
        (x1, label_top),
        (x1 + text_width + 12, y1),
        colour,
        -1,
    )

    cv2.putText(
        frame,
        label,
        (x1 + 5, y1 - 6),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )


def send_serial_command(
    connection: serial.Serial | None,
    command: str,
) -> bool:
    """Send one newline-terminated command to the ESP32."""

    if connection is None:
        return False

    try:
        connection.write(
            f"{command.strip()}\n".encode("utf-8")
        )
        connection.flush()

        print(f"SERIAL TX: {command}")

        return True

    except serial.SerialException as error:
        print(f"SERIAL WRITE ERROR: {error}")

        return False


@dataclass(frozen=True)
class SerialReply:
    """One parsed line from the ESP32.

    The firmware answers every command with ``OK ...`` or ``ERR CODE
    DETAIL``. Parsing it is what lets the laptop drive the real reset gate
    instead of assuming a command worked because it was written to the port.
    """

    raw: str
    ok: bool
    code: str
    detail: str = ""

    @property
    def rejected(self) -> bool:
        return not self.ok


def parse_serial_reply(line: str) -> SerialReply | None:
    """Parse one firmware reply line, or return None for noise.

    Boot banners, self-test chatter and echoed commands are not protocol
    replies, so they must not be mistaken for a rejected command.
    """

    text = line.strip()

    if text.startswith("OK"):
        code = text[2:].strip()
        head, _, detail = code.partition(" ")
        return SerialReply(raw=text, ok=True, code=head, detail=detail.strip())

    if text.startswith("ERR"):
        body = text[3:].strip()
        head, _, detail = body.partition(" ")
        return SerialReply(raw=text, ok=False, code=head, detail=detail.strip())

    return None


def read_serial_responses(
    connection: serial.Serial | None,
) -> list[SerialReply]:
    """Drain and parse all ESP32 replies currently waiting in the buffer."""

    replies: list[SerialReply] = []

    if connection is None:
        return replies

    try:
        while connection.in_waiting:
            line = connection.readline().decode(
                "utf-8",
                errors="replace",
            ).strip()

            if not line:
                continue

            print(f"ESP32 RX: {line}")

            parsed = parse_serial_reply(line)

            if parsed is not None:
                replies.append(parsed)

    except serial.SerialException as error:
        print(f"SERIAL READ ERROR: {error}")

    return replies


@dataclass
class OutstandingCommand:
    """A command we wrote and have not yet seen a reply for.

    The board's protocol is strictly request/response, so there is at most one
    of these at a time. Recording what we asked for is what makes it possible to
    reject a reply that answers a different question.
    """

    command: str
    expected_code: str
    sent_at: float
    incident_id: int | None = None

    @property
    def name(self) -> str:
        return self.command.split()[0] if self.command else ""


#: Command -> the reply code that actually answers it. A reply whose code is
#: not listed here is never treated as a confirmation, which is the whole point
#: of the table: ``OK HEARTBEAT`` cannot confirm an ``ACKNOWLEDGE``.
#:
#: The bare pin presets (``ALARM``, ``SAFE``, ``GREEN_ON``, ...) are absent on
#: purpose. They are diagnostic-only on the firmware and are matched before the
#: real parser, so sending one drives the pins without touching IncidentCore.
#: That is precisely how a latched alarm went green again. Keeping them out of
#: this table means the laptop cannot send one at all.
EXPECTED_REPLY_CODES: dict[str, str] = {
    "INCIDENT": "ALARM_LATCHED",
    "NO_INCIDENT": "NO_INCIDENT",
    "SILENCE": "SILENCED",
    "ACKNOWLEDGE": "ACKNOWLEDGED",
    "ACK": "ACKNOWLEDGED",
    "RESET_READY": "RESET_READY",
    "RESET": "RESET_ACCEPTED",
    "HEARTBEAT": "HEARTBEAT",
    "STATUS": "STATUS",
}

#: Commands that must not occupy the correlation slot: they are sent on a
#: fixed period, so correlating them would keep the slot busy forever and
#: starve the lifecycle commands that actually gate the lamp.
FIRE_AND_FORGET = frozenset({"HEARTBEAT", "STATUS"})

#: Lifecycle commands whose id-less spelling is either a different, weaker
#: firmware command or an outright `ERR BAD_ARGUMENT`. Bare ``SILENCE`` and
#: ``ACKNOWLEDGE`` are legacy aliases that skip the incident-id check, and the
#: sketch lists ``INCIDENT``/``NO_INCIDENT`` as id-mandatory, so the laptop must
#: always carry the id. Mirrors the sketch's ``needs_id`` list.
_ID_SCOPED = frozenset(
    {"INCIDENT", "NO_INCIDENT", "SILENCE", "ACKNOWLEDGE", "ACK", "RESET_READY"}
)


def expected_code_for(command: str) -> str | None:
    """Map a command line to the reply code that proves it was executed.

    Returns None for anything the laptop is not allowed to send, which includes
    every bare pin preset.
    """

    parts = command.strip().split()
    if not parts:
        return None

    name = parts[0].upper()

    # The legacy id-less forms are not lifecycle commands. Only the id-carrying
    # variants may go on the wire, so a request for the id captured here keeps
    # SILENCE/ACKNOWLEDGE from being confused with the firmware aliases.
    if name in _ID_SCOPED and len(parts) < 2:
        return None

    return EXPECTED_REPLY_CODES.get(name)


def reply_matches(outstanding: OutstandingCommand, reply: SerialReply) -> bool:
    """Decide whether ``reply`` answers ``outstanding`` and nothing else.

    Both halves matter. The code must be the one this command produces, and for
    an incident-scoped command the incident id in the firmware's ``STATUS``
    line must still be the one we asked about.
    """

    if not reply.ok:
        # A refusal is only an answer if it names the command we sent.
        return reply.code.upper().startswith(outstanding.expected_code[:3]) or (
            outstanding.name in reply.code.upper()
        )

    return reply.code.upper() == outstanding.expected_code


class CommandCorrelator:
    """Single-outstanding-command correlation for the ESP32 link.

    Replaces the previous blind ``resolve_pending_command(reply)`` on every
    line read. Three behaviours are deliberate:

    1. Only one command is in flight. A second send while one is pending is
       refused rather than queued, because the board answers in order and a
       queue would let a stale reply be matched to the wrong command.
    2. A timeout expires the slot instead of waiting forever. An unanswered
       command is evidence of a broken link, so it degrades the link rather
       than being silently forgotten.
    3. Only a matching reply can complete the promise. Anything else is logged
       as unmatched noise and changes nothing.
    """

    def __init__(self, timeout_seconds: float = 2.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.outstanding: OutstandingCommand | None = None
        self.timed_out = 0
        self.unmatched = 0
        self.confirmed = 0
        self.last_timeout_command: str | None = None

    @property
    def busy(self) -> bool:
        return self.outstanding is not None

    def send(
        self,
        connection: "serial.Serial | None",
        command: str,
        now: float,
        incident_id: int | None = None,
    ) -> bool:
        """Write ``command`` and claim the correlation slot for it."""

        if self.outstanding is not None:
            return False

        expected = expected_code_for(command)

        if expected is None:
            return False

        if not send_serial_command(connection, command):
            return False

        self.outstanding = OutstandingCommand(
            command=command.strip(),
            expected_code=expected,
            sent_at=now,
            incident_id=incident_id,
        )
        return True

    def match(self, reply: SerialReply) -> str:
        """Consume ``reply`` if it answers the command in flight.

        Returns ``MATCHED``, ``TIMEOUT``, ``IDLE`` or ``IGNORED``. ``IGNORED``
        covers the heartbeat case that caused the false ACKNOWLEDGED report.
        """

        if self.outstanding is None:
            return "IDLE"

        if not reply_matches(self.outstanding, reply):
            self.unmatched += 1
            return "IGNORED"

        self.outstanding = None
        self.confirmed += 1
        return "MATCHED"

    def expire(self, now: float) -> str | None:
        """Drop the slot if it has been waiting too long; return what expired."""

        if self.outstanding is None:
            return None

        if now - self.outstanding.sent_at < self.timeout_seconds:
            return None

        expired = self.outstanding.command
        self.last_timeout_command = expired
        self.outstanding = None
        self.timed_out += 1
        return expired


def format_timer_line(
    status: TemporalStatus,
    confirmation_seconds: float,
    clearance_seconds: float,
) -> str:
    """Render the one timing line that matches the current state.

    ``TIMER: 0.00 s`` was previously shown for a confirmed incident because
    the filter returned 0.0 there. A confirmed incident now shows the
    measured duration instead, and idle states say so explicitly.
    """

    if status.state == State.VIOLATION_PENDING:
        return (
            f"CONFIRM TIMER: {status.pending_elapsed or 0.0:.2f}"
            f" / {confirmation_seconds:.2f} s"
        )

    if status.state == State.VIOLATION_CONFIRMED:
        if status.confirmed_after_seconds is None:
            return "CONFIRMED AFTER: unknown"

        return (
            f"CONFIRMED AFTER: {status.confirmed_after_seconds:.2f} s"
        )

    if status.state == State.SAFE_CLEAR_PENDING:
        return (
            f"CLEAR TIMER: {status.safe_clear_elapsed or 0.0:.2f}"
            f" / {clearance_seconds:.2f} s"
        )

    # UNKNOWN and SAFE have no running clock.
    return "TIMER: IDLE"


def lifecycle_timer_line(
    lc_status,
    confirmation_seconds: float,
    clearance_seconds: float,
) -> str:
    """Render the operator-level clock, mirroring ``format_timer_line``.

    A confirmed incident must show the measured duration, never ``0.00``.
    ``LifecycleStatus.safe_clear_required`` is the clearance budget, so the
    confirmation budget has to be passed in explicitly rather than reused.
    """

    if lc_status.state is OperatorState.VIOLATION_PENDING:
        return (
            f"CONFIRM TIMER: {lc_status.pending_elapsed or 0.0:.2f}"
            f" / {confirmation_seconds:.2f} s"
        )

    if lc_status.confirmed_after_seconds is not None:
        return (
            f"CONFIRMED AFTER: {lc_status.confirmed_after_seconds:.2f} s"
        )

    if lc_status.state is OperatorState.SAFE_CLEAR_PENDING:
        return (
            f"CLEAR TIMER: {lc_status.safe_clear_elapsed or 0.0:.2f}"
            f" / {clearance_seconds:.2f} s"
        )

    return "TIMER: IDLE"


def reset_hint(lc_status, blocker: ResetBlocker) -> str:
    """Explain what still blocks a reset, in operator words."""

    if lc_status.incident_id is None:
        return "RESET: nothing to reset"

    if blocker is ResetBlocker.NONE:
        return "RESET: READY (press R)"

    return {
        ResetBlocker.NO_INCIDENT: "RESET: nothing to reset",
        ResetBlocker.NOT_ACKNOWLEDGED: "RESET blocked: not acknowledged (A)",
        ResetBlocker.NOT_SAFE_RECOVERED: "RESET blocked: goggles not verified",
        ResetBlocker.HEALTH_INVALID: "RESET blocked: camera/model unhealthy",
        ResetBlocker.LINK_INVALID: "RESET blocked: ESP32 link unhealthy",
    }[blocker]


def draw_status(
    frame,
    observation: Observation,
    status: TemporalStatus,
    fps: float,
    incident_id: int | None,
    device: str,
    goggles_threshold: float,
    no_goggles_threshold: float,
    confirmation_seconds: float,
    clearance_seconds: float,
    serial_connected: bool,
    lc_status=None,
    blocker: ResetBlocker = ResetBlocker.NO_INCIDENT,
    last_tx: str = "",
    last_rx: str = "",
    model_name: str = "",
    blynk_status: str = "DISABLED",
) -> None:
    """Draw the operator status panel.

    ``lc_status`` is optional so the frame-level overlay remains usable on
    its own (and in tests). When present it drives the panel colour and the
    incident/acknowledge/reset rows, which are the rows an operator actually
    acts on.
    """

    state = status.state

    if lc_status is not None:
        colour = operator_colour(lc_status.state)
        headline = f"STATE: {lc_status.state.value}"
        timer_description = lifecycle_timer_line(
            lc_status,
            confirmation_seconds,
            clearance_seconds,
        )
        incident_text = (
            lc_status.incident_id
            if lc_status.incident_id is not None
            else "NONE"
        )
    else:
        colour = state_colour(state)
        headline = f"STATE: {state.value}"
        timer_description = format_timer_line(
            status,
            confirmation_seconds,
            clearance_seconds,
        )
        incident_text = (
            incident_id if incident_id is not None else "NONE"
        )

    panel_right = min(frame.shape[1] - 10, 760)
    panel_bottom = min(frame.shape[0] - 10, 330)

    cv2.rectangle(
        frame,
        (10, 10),
        (panel_right, panel_bottom),
        (20, 20, 20),
        -1,
    )

    cv2.rectangle(
        frame,
        (10, 10),
        (panel_right, panel_bottom),
        colour,
        3,
    )

    serial_status = (
        "CONNECTED"
        if serial_connected
        else "NOT CONNECTED"
    )

    lines = [
        f"RAW: {observation.value}",
        headline,
        timer_description,
        f"VIOLATION RATIO: {status.violation_ratio:.2f}",
        f"INCIDENT: {incident_text}",
    ]

    if lc_status is not None:
        ack_text = "YES" if lc_status.acknowledged else "NO"
        recovered_text = "YES" if lc_status.safe_recovered else "NO"
        lines.extend(
            [
                f"ALARM: {lc_status.alarm.value}  "
                f"ACKNOWLEDGED: {ack_text}  "
                f"SAFE RECOVERED: {recovered_text}",
                reset_hint(lc_status, blocker),
            ]
        )

        if lc_status.fault_reason:
            lines.append(f"FAULT: {lc_status.fault_reason}")

    lines.extend(
        [
            f"FPS: {fps:.1f}  DEVICE: {device}",
            (
                f"THRESHOLDS: goggles={goggles_threshold:.2f} "
                f"no_goggles={no_goggles_threshold:.2f}"
            ),
            (
                f"SERIAL: {serial_status}  |  "
                f"BLYNK: {blynk_status}"
            ),
            f"TX: {last_tx or '-'}   RX: {last_rx or '-'}",
        ]
    )

    if model_name:
        lines.append(f"MODEL: {model_name}")

    lines.append("Keys: A=acknowledge  R=reset  Q/ESC=quit")

    y = 38

    for index, line in enumerate(lines):
        if index < 2:
            font_scale = 0.75
            thickness = 2
            text_colour = colour
        else:
            font_scale = 0.56
            thickness = 1
            text_colour = (245, 245, 245)

        cv2.putText(
            frame,
            line,
            (25, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            text_colour,
            thickness,
            cv2.LINE_AA,
        )

        y += 24


def parse_source(value: str) -> int | str:
    """Convert a numeric camera source to int."""

    if value.isdigit():
        return int(value)

    return value


def _open_source(source: int | str) -> "cv2.typing.MatLike | None":
    """Return a decoded still image, or None when the source is a stream.

    ``cv2.VideoCapture`` accepts an image path but only ever yields one frame:
    the second ``read()`` returns False, so an image source used to abort after
    the first frame and could never reach a two-second confirmation. Detecting
    the still up front keeps images usable as repeatable smoke tests.
    """

    if not isinstance(source, str) or not source or not Path(source).is_file():
        return None

    image = cv2.imread(source)

    return image if image is not None else None


@dataclass(frozen=True)
class FrameDiagnostics:
    """Measurement of one processed frame.

    Everything here is derived from the *accepted* detections only, so it
    describes what the application actually acted on.
    """

    observation: Observation
    accepted_count: int
    top_class: str
    top_confidence: float
    goggles_count: int
    no_goggles_count: int
    duplicate_boxes: int


def measure_frame(
    accepted_detections: list[
        tuple[str, float, tuple[float, float, float, float]]
    ],
    observation: Observation,
    iou_threshold: float,
) -> FrameDiagnostics:
    """Summarise one frame without changing its decision."""

    top_class = NO_DETECTION_CLASS
    top_confidence = 0.0

    if accepted_detections:
        best = max(
            accepted_detections,
            key=lambda entry: entry[1],
        )
        top_class = best[0]
        top_confidence = best[1]

    return FrameDiagnostics(
        observation=observation,
        accepted_count=len(accepted_detections),
        top_class=top_class,
        top_confidence=top_confidence,
        goggles_count=sum(
            1
            for class_name, _, _ in accepted_detections
            if class_name == "goggles"
        ),
        no_goggles_count=sum(
            1
            for class_name, _, _ in accepted_detections
            if class_name == "no_goggles"
        ),
        duplicate_boxes=count_duplicate_overlaps(
            accepted_detections,
            iou_threshold=iou_threshold,
        ),
    )


class TrialSession:
    """Accumulate per-frame diagnostics so one CSV row can be emitted.

    The session tracks the *worst* safety outcome seen, never the best:
    a single VIOLATION frame therefore cannot be hidden by later SAFE
    frames, and the recorded state is the state the operators would have
    responded to.
    """

    def __init__(self) -> None:
        self.frames = 0
        self.state_counts: dict[str, int] = {}
        self.top_class_counts: dict[str, int] = {}
        self.best_confidence = 0.0
        self.best_confidence_class = NO_DETECTION_CLASS
        self.max_duplicates = 0
        self.fps_values: list[float] = []
        self.saw_safe = False
        self.saw_confirmed = False
        self.saw_unknown = False

    def record(
        self,
        diagnostics: FrameDiagnostics,
        state: State,
        fps: float,
    ) -> None:
        self.frames += 1

        state_name = state.value
        self.state_counts[state_name] = (
            self.state_counts.get(state_name, 0) + 1
        )

        if diagnostics.top_class != NO_DETECTION_CLASS:
            self.top_class_counts[diagnostics.top_class] = (
                self.top_class_counts.get(diagnostics.top_class, 0) + 1
            )

            if diagnostics.top_confidence > self.best_confidence:
                self.best_confidence = diagnostics.top_confidence
                self.best_confidence_class = diagnostics.top_class

        self.max_duplicates = max(
            self.max_duplicates,
            diagnostics.duplicate_boxes,
        )

        if fps > 0.0:
            self.fps_values.append(fps)

        if state == State.SAFE:
            self.saw_safe = True

        if state == State.VIOLATION_CONFIRMED:
            self.saw_confirmed = True

        if state == State.UNKNOWN:
            self.saw_unknown = True

    def worst_state(self) -> str:
        """Return the safety-relevant state for the whole trial.

        Precedence is deliberate and conservative:

        1. VIOLATION_CONFIRMED - the physical alarm was raised.
        2. SAFE - the scene was cleared with no incident.
        3. SAFE_CLEAR_PENDING - a previously SAFE scene is clearing.
        4. VIOLATION_PENDING - a violation was building but unconfirmed.
        5. UNKNOWN - nothing was ever seen.
        """

        if self.saw_confirmed:
            return State.VIOLATION_CONFIRMED.value

        if self.saw_safe:
            return State.SAFE.value

        for candidate in (
            State.SAFE_CLEAR_PENDING,
            State.VIOLATION_PENDING,
            State.UNKNOWN,
        ):
            if self.state_counts.get(candidate.value):
                return candidate.value

        return State.UNKNOWN.value

    def mean_fps(self) -> float:
        if not self.fps_values:
            return 0.0

        return sum(self.fps_values) / len(self.fps_values)

    def threshold_preset(self) -> str:
        """Label the trial by the argument set that produced it.

        This is derived from the actual thresholds, so a mislabelled
        command line cannot mislabel the data.
        """

        return threshold_preset_for(
            self._candidate_confidence,
            self._goggles_confidence,
            self._no_goggles_confidence,
            self._iou,
        )

    # Populated by ``build_acceptance_row``.
    _candidate_confidence: float = 0.0
    _goggles_confidence: float = 0.0
    _no_goggles_confidence: float = 0.0
    _iou: float = 0.0


def threshold_preset_for(
    candidate_confidence: float,
    goggles_confidence: float,
    no_goggles_confidence: float,
    iou_threshold: float,
) -> str:
    """Return 'A', 'B' or 'custom' for a threshold set."""

    preset_a = (
        abs(candidate_confidence - 0.40) < 1e-9
        and abs(goggles_confidence - 0.60) < 1e-9
        and abs(no_goggles_confidence - 0.55) < 1e-9
        and abs(iou_threshold - 0.30) < 1e-9
    )
    preset_b = (
        abs(candidate_confidence - 0.50) < 1e-9
        and abs(goggles_confidence - 0.65) < 1e-9
        and abs(no_goggles_confidence - 0.60) < 1e-9
        and abs(iou_threshold - 0.30) < 1e-9
    )

    if preset_a:
        return "A"

    if preset_b:
        return "B"

    return "custom"


def build_acceptance_row(
    session: TrialSession,
    *,
    trial_id: str,
    condition: str,
    pose: str,
    weights: Path,
    weights_digest: str,
    notes: str,
    candidate_confidence: float,
    goggles_confidence: float,
    no_goggles_confidence: float,
    iou_threshold: float,
) -> dict[str, str]:
    """Build the CSV row for one finished trial.

    No value here is a judgement call: the row records what the
    application did. The operator confirms it before it is appended.
    """

    observed = session.worst_state()

    false_safe = (
        1 if condition == "no_goggles" and observed in (
            "SAFE",
            "SAFE_CLEAR_PENDING",
        ) else 0
    )
    false_alarm = (
        1 if condition in ("goggles", "empty")
        and observed == "VIOLATION_CONFIRMED"
        else 0
    )
    unknown = 1 if observed == "UNKNOWN" else 0

    expected = {
        "goggles": "SAFE",
        "no_goggles": "VIOLATION_CONFIRMED",
        "empty": "UNKNOWN",
    }[condition]

    return {
        "trial_id": trial_id,
        "timestamp": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "model": Path(weights).name,
        "weights_sha256": weights_digest,
        "condition": condition,
        "pose": pose,
        "expected_state": expected,
        "observed_state": observed,
        "top_class": session.best_confidence_class,
        "top_confidence": f"{session.best_confidence:.4f}",
        "false_safe": str(false_safe),
        "false_alarm": str(false_alarm),
        "unknown": str(unknown),
        "duplicate_boxes": str(session.max_duplicates),
        "application_fps": f"{session.mean_fps():.2f}",
        "threshold_preset": threshold_preset_for(
            candidate_confidence,
            goggles_confidence,
            no_goggles_confidence,
            iou_threshold,
        ),
        "notes": notes,
    }


def append_acceptance_row(
    row: dict[str, str],
    path: Path,
) -> None:
    """Append one already-built row, writing the header if needed."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    write_header = not path.is_file() or path.stat().st_size == 0

    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(ACCEPTANCE_COLUMNS),
        )

        if write_header:
            writer.writeheader()

        writer.writerow(
            {column: row.get(column, "") for column in ACCEPTANCE_COLUMNS}
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Live eye-shield monitor with ESP32 serial control"
        )
    )

    parser.add_argument(
        "--source",
        default="0",
        help="Camera index, image path, or video path",
    )

    parser.add_argument(
        "--weights",
        default="models/best_v2.pt",
    )

    parser.add_argument(
        "--candidate-confidence",
        type=float,
        default=0.40,
        help="Initial YOLO candidate threshold",
    )

    parser.add_argument(
        "--goggles-confidence",
        type=float,
        default=0.60,
        help="Minimum accepted confidence for goggles",
    )

    parser.add_argument(
        "--no-goggles-confidence",
        type=float,
        default=0.45,
        help="Minimum accepted confidence for no_goggles",
    )

    parser.add_argument(
        "--iou",
        type=float,
        default=0.40,
        help=(
            "NMS IoU threshold. LOWER suppresses duplicate boxes "
            "more aggressively; HIGHER keeps more overlapping "
            "boxes. Use 0.30 (or 0.25) to collapse stacked "
            "duplicate detections."
        ),
    )

    parser.add_argument(
        "--image-size",
        type=int,
        default=640,
    )

    parser.add_argument(
        "--confirmation-seconds",
        type=float,
        default=2.0,
    )

    parser.add_argument(
        "--window-seconds",
        type=float,
        default=2.0,
    )

    parser.add_argument(
        "--clearance-seconds",
        type=float,
        default=2.0,
    )

    parser.add_argument(
        "--violation-ratio",
        type=float,
        default=0.80,
    )

    parser.add_argument(
        "--unknown-tolerance-seconds",
        type=float,
        default=0.35,
    )

    parser.add_argument(
        "--device",
        default="auto",
        choices=("auto", "mps", "cpu"),
    )

    parser.add_argument(
        "--serial-port",
        default="",
        help=(
            "ESP32 serial port; leave empty to disable "
            "hardware output"
        ),
    )

    parser.add_argument(
        "--serial-baud",
        type=int,
        default=115200,
    )

    parser.add_argument(
        "--no-serial",
        action="store_true",
        help=(
            "Force-disable ESP32 serial output even if "
            "--serial-port is set. Use for acceptance runs "
            "where only the software state is scored."
        ),
    )

    parser.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help=(
            "Stop after N processed frames. 0 runs until Q or ESC. "
            "Useful for headless smoke tests."
        ),
    )

    parser.add_argument(
        "--log-csv",
        default="",
        help=(
            "Append one V2 live-acceptance row to this CSV when "
            "the run ends. The row is printed and must be "
            "confirmed interactively, or accepted with "
            "--yes when headless."
        ),
    )

    parser.add_argument(
        "--trial-id",
        default="",
        help="Trial identifier for --log-csv, e.g. V2-001",
    )

    parser.add_argument(
        "--condition",
        default="",
        choices=("", *CONDITIONS),
        help=(
            "Ground-truth condition of this trial. Required "
            "with --log-csv."
        ),
    )

    parser.add_argument(
        "--pose",
        default="",
        choices=("", *POSES),
        help="Head pose of this trial. Required with --log-csv.",
    )

    parser.add_argument(
        "--notes",
        default="",
        help="Free-text note stored in the acceptance CSV row",
    )

    parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Write the acceptance row without an interactive "
            "confirmation prompt."
        ),
    )

    parser.add_argument(
        "--headless",
        action="store_true",
        help=(
            "Do not open a video window. Requires --max-frames "
            "or the process will never end."
        ),
    )

    parser.add_argument(
        "--no-blynk",
        action="store_true",
        help=(
            "Disable the Blynk Cloud bridge even when BLYNK_ENABLED "
            "is set in .env. Monitoring/notification only; no remote "
            "control."
        ),
    )

    args = parser.parse_args()

    for argument_name, value in (
        (
            "candidate-confidence",
            args.candidate_confidence,
        ),
        (
            "goggles-confidence",
            args.goggles_confidence,
        ),
        (
            "no-goggles-confidence",
            args.no_goggles_confidence,
        ),
        (
            "iou",
            args.iou,
        ),
        (
            "violation-ratio",
            args.violation_ratio,
        ),
    ):
        if not 0.0 <= value <= 1.0:
            raise SystemExit(
                f"ERROR: --{argument_name} must be "
                "between 0 and 1"
            )

    weights = Path(args.weights)

    if not weights.is_file():
        raise SystemExit(
            f"ERROR: weights not found: {weights}"
        )

    if args.log_csv:
        for argument_name, value in (
            ("trial-id", args.trial_id),
            ("condition", args.condition),
            ("pose", args.pose),
        ):
            if not value:
                raise SystemExit(
                    f"ERROR: --{argument_name} is required "
                    "with --log-csv"
                )

        if args.condition == "empty" and args.pose != "none":
            raise SystemExit(
                "ERROR: empty scenes must use --pose none"
            )

        if (
            args.condition in ("goggles", "no_goggles")
            and args.pose == "none"
        ):
            raise SystemExit(
                "ERROR: --pose none is only valid for an "
                "empty scene"
            )

    if args.headless and args.max_frames <= 0:
        raise SystemExit(
            "ERROR: --headless requires --max-frames so the "
            "run can terminate"
        )

    device = choose_device(args.device)
    model = YOLO(str(weights))

    # Strict class contract. A reordered or extended classifier would
    # silently invert the safety decision, so refuse to run.
    expected_names = {0: "goggles", 1: "no_goggles"}
    observed_names = {
        int(class_id): str(class_name)
        for class_id, class_name in model.names.items()
    }

    if observed_names != expected_names:
        raise SystemExit(
            "ERROR: class contract mismatch. Expected "
            f"{expected_names}, got {observed_names}. "
            "Refusing to run because the safety decision "
            "would be inverted."
        )

    weights_digest = sha256_of_file(weights)

    if args.log_csv:
        # Fail fast, before the operator performs a trial.
        from tools.v2_acceptance_schema import (
            AcceptanceError,
            read_rows,
            validate_row,
            EXPECTED_STATE_BY_CONDITION,
        )

        try:
            probe = validate_row(
                {
                    "trial_id": args.trial_id,
                    "timestamp": datetime.now(
                        timezone.utc
                    ).isoformat(timespec="seconds"),
                    "model": weights.name,
                    "weights_sha256": weights_digest,
                    "condition": args.condition,
                    "pose": args.pose,
                    "expected_state": EXPECTED_STATE_BY_CONDITION[
                        args.condition
                    ],
                    "observed_state": "UNKNOWN",
                    "top_class": NO_DETECTION_CLASS,
                    "top_confidence": "0.0",
                    "false_safe": "0",
                    "false_alarm": "0",
                    "unknown": "1",
                    "duplicate_boxes": "0",
                    "application_fps": "0.0",
                    "threshold_preset": threshold_preset_for(
                        args.candidate_confidence,
                        args.goggles_confidence,
                        args.no_goggles_confidence,
                        args.iou,
                    ),
                    "notes": args.notes,
                },
                known_trial_ids=(
                    row["trial_id"]
                    for row in read_rows(Path(args.log_csv))
                ),
            )
        except AcceptanceError as error:
            raise SystemExit(f"ERROR: {error}")

        if probe["threshold_preset"] == "custom":
            raise SystemExit(
                "ERROR: --log-csv needs a documented "
                "threshold preset (A or B); the given "
                "thresholds are custom"
            )

    source = parse_source(args.source)
    still_image = _open_source(source)

    if still_image is not None:
        capture = None
        static_frame = still_image
    else:
        capture = cv2.VideoCapture(source)
        static_frame = None

        if not capture.isOpened():
            raise SystemExit(
                f"ERROR: cannot open source: {source}"
            )

    serial_connection: serial.Serial | None = None

    if args.serial_port and not args.no_serial:
        try:
            serial_connection = serial.Serial(
                port=args.serial_port,
                baudrate=args.serial_baud,
                timeout=0.05,
                write_timeout=1.0,
            )

            # Opening the UART bridge may reset the ESP32.
            time.sleep(2.0)
            serial_connection.reset_input_buffer()

            print(
                f"Serial connected: {args.serial_port} "
                f"at {args.serial_baud}"
            )

            # Startup must not claim SAFE and must not claim an alarm.
            # WAITING_FOR_TARGET is the honest description of an unknown
            # scene, and the firmware maps it to green-off/red-off rather
            # than borrowing ALL_OFF, which is diagnostic-only.
            send_serial_command(
                serial_connection,
                "HEARTBEAT",
            )

            time.sleep(0.1)
            read_serial_responses(serial_connection)

        except serial.SerialException as error:
            if capture is not None:
                capture.release()

            raise SystemExit(
                "ERROR: cannot open ESP32 serial port: "
                f"{error}"
            )

    temporal_filter = TemporalFilter(
        confirmation_seconds=args.confirmation_seconds,
        window_seconds=args.window_seconds,
        clearance_seconds=args.clearance_seconds,
        violation_ratio_required=args.violation_ratio,
        unknown_tolerance_seconds=(
            args.unknown_tolerance_seconds
        ),
    )

    frame_times: deque[float] = deque(maxlen=30)
    previous_time = time.monotonic()

    lifecycle = IncidentLifecycle(
        confirmation_seconds=args.confirmation_seconds,
        clearance_seconds=args.clearance_seconds,
    )

    # Blynk Cloud bridge: monitoring & notification only. The client is
    # non-blocking and degrades to a no-op when disabled, so a missing .env
    # or an unreachable cloud never halts detection or the serial link.
    blynk: BlynkClient | None = None
    if not args.no_blynk:
        try:
            blynk = BlynkClient.from_env(require=False)
        except BlynkConfigError as error:
            blynk = None
            print(f"Blynk disabled: {error}")

    previous_state: State | None = None

    # The single source of truth for what the operator sees. Every serial
    # command below is derived from a lifecycle transition, so the laptop and
    # the firmware cannot disagree about whether an incident is open.
    lc_status = lifecycle.status()
    blocker = lifecycle.reset_blocker()
    last_tx = ""
    last_rx = ""
    serial_ok = serial_connection is not None

    # The firmware refuses a RESET when it has not heard from us for
    # HEARTBEAT_TIMEOUT_S, so the link must be kept warm well inside that.
    heartbeat_period = 1.0
    last_heartbeat = 0.0

    # One command in flight at a time. The board is request/response, so a
    # reply can only be attributed to a command if exactly one is outstanding.
    correlator = CommandCorrelator(timeout_seconds=2.0)

    trial_session = TrialSession()
    trial_session._candidate_confidence = args.candidate_confidence
    trial_session._goggles_confidence = args.goggles_confidence
    trial_session._no_goggles_confidence = args.no_goggles_confidence
    trial_session._iou = args.iou

    processed_frames = 0

    print("=" * 68)
    print("Eye-Shield Monitor - AI + ESP32 integration")
    print("=" * 68)
    print(f"Source: {source}")
    print(f"Weights: {weights.resolve()}")
    print(f"Device: {device}")
    print(f"Candidate confidence: {args.candidate_confidence}")
    print(f"Goggles confidence: {args.goggles_confidence}")
    print(
        "No-goggles confidence: "
        f"{args.no_goggles_confidence}"
    )
    print(f"IoU: {args.iou}")
    print(
        f"Confirmation time: "
        f"{args.confirmation_seconds} s"
    )
    print(f"Clearance time: {args.clearance_seconds} s")
    print(
        "Serial: "
        f"{args.serial_port if args.serial_port else 'disabled'}"
    )
    if blynk is not None and blynk.enabled:
        print("Blynk: ONLINE (monitoring & notification only)")
    else:
        print("Blynk: disabled")
    print("Press Q or ESC in the video window to quit")
    print("=" * 68)

    try:
        while True:
            if static_frame is not None:
                ok, frame = True, static_frame.copy()
            else:
                ok, frame = capture.read()

            if not ok:
                print(
                    "ERROR: camera or frame source unavailable"
                )
                break

            now = time.monotonic()
            frame_delta = now - previous_time
            previous_time = now

            if frame_delta > 0:
                frame_times.append(1.0 / frame_delta)

            measured_fps = (
                sum(frame_times) / len(frame_times)
                if frame_times
                else 0.0
            )

            results = model.predict(
                frame,
                imgsz=args.image_size,
                conf=args.candidate_confidence,
                iou=args.iou,
                device=device,
                verbose=False,
            )

            result = results[0]

            # Start from the original frame so rejected boxes
            # are not drawn.
            annotated = frame.copy()

            accepted_detections: list[
                tuple[str, float, tuple[float, float, float, float]]
            ] = []

            if result.boxes is not None:
                for box in result.boxes:
                    class_id = int(box.cls.item())
                    confidence = float(box.conf.item())
                    class_name = model.names[class_id]

                    accepted = False

                    if class_name == "goggles":
                        accepted = (
                            confidence
                            >= args.goggles_confidence
                        )

                    elif class_name == "no_goggles":
                        accepted = (
                            confidence
                            >= args.no_goggles_confidence
                        )

                    if not accepted:
                        continue

                    coordinates = tuple(
                        map(
                            int,
                            box.xyxy[0].tolist(),
                        )
                    )

                    accepted_detections.append(
                        (class_name, confidence, coordinates)
                    )

                    draw_detection(
                        annotated,
                        class_name,
                        confidence,
                        coordinates,
                    )

            # Accepted detections only: rejected boxes are never
            # drawn and never influence the frame decision.
            observation = decide_observation(
                [
                    (class_name, confidence)
                    for class_name, confidence, _ in accepted_detections
                ]
            )

            diagnostics = measure_frame(
                accepted_detections,
                observation,
                args.iou,
            )

            status = temporal_filter.update(
                observation,
                now,
            )

            trial_session.record(
                diagnostics,
                status.state,
                measured_fps,
            )

            # Receive ESP32 responses without blocking video. A read
            # failure means the board is unreachable, which is a reset
            # blocker: we must never accept a reset we cannot confirm.
            try:
                replies = read_serial_responses(serial_connection)
                if serial_connection is not None:
                    serial_ok = True
            except Exception as error:  # noqa: BLE001 - link loss is not fatal
                print(f"SERIAL LINK LOST: {error}")
                replies = []
                serial_ok = False

            for reply in replies:
                last_rx = reply.raw
                print(f"[ESP32 RX] {reply.raw.strip()}")

                incident_id = (
                    lifecycle.incident.incident_id if lifecycle.incident else None
                )

                # Correlation first: a reply that does not answer the command
                # in flight must not be allowed to change any state. This is
                # what stops OK HEARTBEAT from confirming an ACKNOWLEDGE.
                verdict = correlator.match(reply)

                if verdict == "IGNORED":
                    print(
                        f"[CORRELATION] ignored {reply.raw.strip()!r} "
                        f"(waiting for {correlator.outstanding.expected_code if correlator.outstanding else '-'})"
                    )
                    continue

                if verdict == "IDLE":
                    # Unsolicited line: useful on the overlay, never a state
                    # change on its own.
                    if reply.ok and reply.code == "ALARM_LATCHED":
                        print(
                            "[CORRELATION] unsolicited ALARM_LATCHED; "
                            "firmware latched without us asking"
                        )
                    continue

                if reply.ok and reply.code == "RESET_ACCEPTED":
                    # The firmware is the authority. resolve_pending_command
                    # clears the local latch to match it.
                    print("RESET CONFIRMED BY ESP32")

                elif reply.rejected:
                    print(
                        f"ESP32 REJECTED {reply.code} "
                        f"{reply.detail}".strip()
                    )

                # A confirmed ACKNOWLEDGED is the only thing that may move the
                # incident to acknowledged. The keypress alone never does.
                if reply.ok and reply.code == "ACKNOWLEDGED":
                    outcome = lifecycle.confirm_acknowledge(reply, now, incident_id)
                    print(f"[CORRELATION] acknowledge {outcome} for incident {incident_id}")

                # Finish or abandon the handshake this reply implies. A
                # refused RESET re-asserts our steady state so the firmware
                # cannot be left holding a stale token.
                follow_up = lifecycle.resolve_pending_command(reply)

                if follow_up is not None:
                    if correlator.send(serial_connection, follow_up, now, incident_id):
                        last_tx = follow_up
                    else:
                        serial_ok = False

            expired = correlator.expire(now)

            if expired is not None:
                # No answer is not success. Drop the link so the laptop parks
                # in FAULT instead of pretending the command landed.
                print(f"[CORRELATION] TIMEOUT waiting for reply to {expired!r}")
                serial_ok = False
                lifecycle.set_health(
                    camera_ok=ok,
                    model_ok=True,
                    serial_ok=False,
                    now=now,
                    reason=f"SERIAL TIMEOUT {expired}",
                )

            lifecycle.set_health(
                camera_ok=ok,
                model_ok=True,
                serial_ok=serial_ok,
                now=now,
            )

            lc_status = lifecycle.update(
                observation,
                now,
                violation_ratio=status.violation_ratio,
                pending_since=temporal_filter.pending_since,
                safe_since=temporal_filter.safe_since,
            )

            # Log lifecycle state changes
            if lc_status.state != previous_state:
                print(f"[LIFECYCLE STATE] {previous_state} -> {lc_status.state}")
                print(f"[LIFECYCLE STATUS] acknowledged={lc_status.acknowledged}, safe_recovered={lc_status.safe_recovered}, incident_id={lc_status.incident_id}")

            # Blynk Cloud bridge (monitoring/notification only). Build the
            # reading from the physical GPIO mirror so the dashboard shows the
            # red LED latched through ACKNOWLEDGED/SAFE_CLEAR_PENDING.
            if blynk is not None and blynk.enabled:
                green, red, buzzer = alarm_intent_to_gpio(lc_status.alarm)
                reading = LifecycleReading(
                    state=lc_status.state.value,
                    incident_id=lc_status.incident_id,
                    violation_ratio=lc_status.violation_ratio,
                    link_online=serial_ok,
                    incident_count=max(0, lifecycle.next_incident_id - 1),
                    top_confidence=diagnostics.top_confidence,
                    fps=measured_fps,
                    green=green,
                    red=red,
                    buzzer=buzzer,
                )
                blynk.push_status(reading)

            # Recovery is only proven by the detector itself. Recording it
            # here, rather than in the key handler, is what stops an operator
            # from acknowledging and clearing an incident that was never
            # actually resolved.
            if lc_status.safe_recovered:
                lifecycle.mark_onboard_verified()

            if status.state != previous_state:
                print(
                    "STATE TRANSITION: "
                    f"{previous_state} -> {status.state}"
                )
                previous_state = status.state

            if (
                serial_connection is not None
                and now - last_heartbeat >= heartbeat_period
            ):
                # Heartbeats are fire-and-forget: they are sent on a fixed
                # period, so they never claim the correlation slot.
                if send_serial_command(serial_connection, "HEARTBEAT"):
                    last_heartbeat = now
                else:
                    serial_ok = False

            command = lifecycle.take_pending_command()

            if command is not None:
                incident_id = (
                    lifecycle.incident.incident_id if lifecycle.incident else None
                )
                print(f"[{time.time():.3f}] [LIFECYCLE TX] Sending command: {command}")

                if correlator.send(serial_connection, command, now, incident_id):
                    last_tx = command
                    print(
                        f"[{time.time():.3f}] [LIFECYCLE TX] Command sent, "
                        f"awaiting {correlator.outstanding.expected_code if correlator.outstanding else '-'}"
                    )
                elif correlator.busy:
                    # One command in flight. Put it back rather than dropping
                    # it: a lost INCIDENT command is a lost alarm.
                    lifecycle.requeue_last_command()
                    print(
                        f"[{time.time():.3f}] [CORRELATION] busy waiting for "
                        f"{correlator.outstanding.command!r}; will retry"
                    )
                else:
                    # A failed write is link loss, not a delivered command.
                    serial_ok = False
                    print(f"[{time.time():.3f}] [LIFECYCLE TX] Command send FAILED")
                    lifecycle.set_health(
                        camera_ok=ok,
                        model_ok=True,
                        serial_ok=False,
                        now=now,
                        reason="SERIAL WRITE FAILED",
                    )

            blocker = lifecycle.reset_blocker()

            draw_status(
                annotated,
                observation,
                status,
                measured_fps,
                temporal_filter.incident_id,
                device,
                args.goggles_confidence,
                args.no_goggles_confidence,
                args.confirmation_seconds,
                args.clearance_seconds,
                serial_connection is not None,
                lc_status=lc_status,
                blocker=blocker,
                last_tx=last_tx,
                last_rx=last_rx,
                model_name=weights.name,
                blynk_status=(
                    "ONLINE"
                    if blynk is not None and blynk.enabled
                    else "DISABLED"
                ),
            )

            if not args.headless:
                cv2.imshow(
                    "Eye-Shield Monitor - AI + ESP32",
                    annotated,
                )

                key = cv2.waitKey(1) & 0xFF

                if key in (ord("q"), 27):
                    break

                if key == ord("a"):
                    # Same call a future dashboard button will make. There is
                    # no second code path that can bypass the latch.
                    #
                    # This only *requests* the acknowledgement. It emits a
                    # transition, and the normal drain below sends
                    # ACKNOWLEDGE <id>. The incident stays unanswered in the UI
                    # until the board's OK ACKNOWLEDGED is correlated back.
                    ok_ack, reason = lifecycle.acknowledge(now)
                    print(
                        f"ACKNOWLEDGE REQUESTED: {reason}"
                        if ok_ack
                        else f"ACKNOWLEDGE REJECTED: {reason}"
                    )

                if key == ord("r"):
                    # An intent, not a reset. The firmware decides, and
                    # nothing is displayed as cleared until it accepts.
                    started, reason = lifecycle.request_reset(now)

                    if not started:
                        print(f"RESET REJECTED: {reason}")
                    else:
                        token = lifecycle.pending_reset_command

                        if correlator.send(
                            serial_connection,
                            token,
                            now,
                            lifecycle.incident.incident_id if lifecycle.incident else None,
                        ):
                            last_tx = token
                            print(f"RESET HANDSHAKE STARTED: {token}")
                        else:
                            print("RESET NOT SENT: serial link unavailable")
                            lifecycle.pending_reset_command = None

                    blocker = lifecycle.reset_blocker()
                    lc_status = lifecycle.status()

            processed_frames += 1

            if (
                args.max_frames > 0
                and processed_frames >= args.max_frames
            ):
                print(
                    f"Reached --max-frames {args.max_frames}"
                )
                break

    except KeyboardInterrupt:
        print("\nInterrupted by user")

    finally:
        if capture is not None:
            capture.release()
        cv2.destroyAllWindows()

        # Do not send ALL_OFF here.
        # A confirmed physical alarm must remain latched
        # even if the laptop application closes.
        if serial_connection is not None:
            serial_connection.close()
            print("Serial connection closed")

        # Flush any queued cloud telemetry before exit so the final state is
        # not lost; close() is a no-op when the bridge was never created.
        if blynk is not None:
            blynk.close()
            print("Blynk bridge closed")

    print("Application stopped safely")

    if args.log_csv:
        return finalise_trial_log(
            trial_session,
            args=args,
            weights=weights,
            weights_digest=weights_digest,
        )

    return 0


def finalise_trial_log(
    trial_session: TrialSession,
    *,
    args,
    weights: Path,
    weights_digest: str,
) -> int:
    """Print, confirm and append one acceptance row."""

    from tools.v2_acceptance_schema import (
        AcceptanceError,
        append_row,
    )

    if trial_session.frames == 0:
        print("ERROR: no frames were processed; row not written")
        return 1

    row = build_acceptance_row(
        trial_session,
        trial_id=args.trial_id,
        condition=args.condition,
        pose=args.pose,
        weights=weights,
        weights_digest=weights_digest,
        notes=args.notes,
        candidate_confidence=args.candidate_confidence,
        goggles_confidence=args.goggles_confidence,
        no_goggles_confidence=args.no_goggles_confidence,
        iou_threshold=args.iou,
    )

    print()
    print("ACCEPTANCE ROW (not yet written)")
    print("-" * 60)

    for column in ACCEPTANCE_COLUMNS:
        print(f"  {column:<20} {row[column]}")

    print("-" * 60)

    confirmed = args.yes

    if not confirmed:
        try:
            answer = input(
                "Append this row to "
                f"{args.log_csv}? [y/N] "
            )
        except EOFError:
            answer = ""

        confirmed = answer.strip().lower() in ("y", "yes")

    if not confirmed:
        print("Row discarded. Nothing was written.")
        return 1

    try:
        append_row(row, Path(args.log_csv))
    except AcceptanceError as error:
        print(f"ERROR: {error}")
        return 1

    print(f"Row appended to {args.log_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
