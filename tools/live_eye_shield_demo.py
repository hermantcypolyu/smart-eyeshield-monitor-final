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


def read_serial_responses(
    connection: serial.Serial | None,
) -> None:
    """Print all ESP32 replies currently waiting in the buffer."""

    if connection is None:
        return

    try:
        while connection.in_waiting:
            line = connection.readline().decode(
                "utf-8",
                errors="replace",
            ).strip()

            if line:
                print(f"ESP32 RX: {line}")

    except serial.SerialException as error:
        print(f"SERIAL READ ERROR: {error}")


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
) -> None:
    """Draw the operator status panel."""

    state = status.state
    colour = state_colour(state)

    panel_right = min(frame.shape[1] - 10, 760)
    panel_bottom = min(frame.shape[0] - 10, 275)

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

    timer_description = format_timer_line(
        status,
        confirmation_seconds,
        clearance_seconds,
    )

    serial_status = (
        "CONNECTED"
        if serial_connected
        else "NOT CONNECTED"
    )

    lines = [
        f"RAW: {observation.value}",
        f"STATE: {state.value}",
        timer_description,
        f"VIOLATION RATIO: {status.violation_ratio:.2f}",
        f"INCIDENT: {incident_id if incident_id is not None else 'NONE'}",
        f"FPS: {fps:.1f}  DEVICE: {device}",
        (
            f"THRESHOLDS: goggles={goggles_threshold:.2f} "
            f"no_goggles={no_goggles_threshold:.2f}"
        ),
        (
            f"SERIAL: {serial_status}  |  "
            "BLYNK: NOT CONFIGURED"
        ),
        "Press Q or ESC to quit",
    ]

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
        default="models/best.pt",
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
    capture = cv2.VideoCapture(source)

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

            # Startup UNKNOWN must not claim SAFE.
            send_serial_command(
                serial_connection,
                "ALL_OFF",
            )

            time.sleep(0.1)
            read_serial_responses(serial_connection)

        except serial.SerialException as error:
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

    previous_state: State | None = None
    alarm_sent_for_incident: int | None = None

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
    print("Blynk: not configured")
    print("Press Q or ESC in the video window to quit")
    print("=" * 68)

    try:
        while True:
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

            # Receive ESP32 responses without blocking video.
            read_serial_responses(serial_connection)

            # Transmit only on meaningful state transitions.
            if status.state != previous_state:
                print(
                    "STATE TRANSITION: "
                    f"{previous_state} -> {status.state}"
                )

                # Before an incident exists, SAFE turns
                # the physical system green.
                if (
                    status.state == State.SAFE
                    and temporal_filter.incident_id is None
                ):
                    send_serial_command(
                        serial_connection,
                        "SAFE",
                    )

                # Send one ALARM command for each incident.
                if (
                    status.state == State.VIOLATION_CONFIRMED
                    and temporal_filter.incident_id
                    is not None
                    and alarm_sent_for_incident
                    != temporal_filter.incident_id
                ):
                    if send_serial_command(
                        serial_connection,
                        "ALARM",
                    ):
                        alarm_sent_for_incident = (
                            temporal_filter.incident_id
                        )

                # Deliberately do NOT send SAFE after
                # a confirmed incident. Red and buzzer remain
                # active until a later explicit command.
                previous_state = status.state

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
            )

            if not args.headless:
                cv2.imshow(
                    "Eye-Shield Monitor - AI + ESP32",
                    annotated,
                )

                key = cv2.waitKey(1) & 0xFF

                if key in (ord("q"), 27):
                    break

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
        capture.release()
        cv2.destroyAllWindows()

        # Do not send ALL_OFF here.
        # A confirmed physical alarm must remain latched
        # even if the laptop application closes.
        if serial_connection is not None:
            serial_connection.close()
            print("Serial connection closed")

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
