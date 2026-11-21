"""Live AI eye-shield monitor with ESP32 serial integration.

Current integration:
- YOLOv8n webcam inference
- Class-specific confidence thresholds
- Two-second temporal violation confirmation
- One latched incident
- Text-command serial control of ESP32-S3
- SAFE and ALARM hardware output

Not yet included:
- Final JSON serial protocol
- ESP32 acknowledgement
- Acknowledge / conditional reset
- Serial heartbeat and reconnect
- Blynk
"""

from __future__ import annotations

import argparse
import time
from collections import deque
from enum import Enum
from pathlib import Path

import cv2
import serial
import torch
from ultralytics import YOLO


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

        self.pending_since: float | None = None
        self.safe_since: float | None = None
        self.last_violation_time: float | None = None

        self.confirmed = False
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
    ) -> tuple[State, float, float]:
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

                return self.state, 0.0, ratio

            if observation == Observation.UNKNOWN:
                # UNKNOWN must never become SAFE.
                if self.pending_since is None:
                    self.state = State.UNKNOWN
                    return self.state, 0.0, ratio

                # Allow a brief missed detection during a pending violation.
                if (
                    self.last_violation_time is not None
                    and now - self.last_violation_time
                    <= self.unknown_tolerance_seconds
                ):
                    elapsed = now - self.pending_since
                    self.state = State.VIOLATION_PENDING

                    return self.state, elapsed, ratio

                # UNKNOWN lasted too long. Cancel the pending violation.
                self.pending_since = None
                self.last_violation_time = None
                self.state = State.UNKNOWN

                return self.state, 0.0, ratio

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
                self.state = State.VIOLATION_CONFIRMED
            else:
                self.state = State.VIOLATION_PENDING

            return self.state, elapsed, ratio

        # A confirmed incident already exists.
        if observation == Observation.SAFE:
            if self.safe_since is None:
                self.safe_since = now

            clear_elapsed = now - self.safe_since

            # This does not clear the incident.
            self.state = State.SAFE_CLEAR_PENDING

            return self.state, clear_elapsed, ratio

        # UNKNOWN or continued VIOLATION cancels safe-clear progress.
        self.safe_since = None
        self.state = State.VIOLATION_CONFIRMED

        return self.state, 0.0, ratio


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


def draw_status(
    frame,
    observation: Observation,
    state: State,
    timer_seconds: float,
    violation_ratio: float,
    fps: float,
    incident_id: int | None,
    device: str,
    goggles_threshold: float,
    no_goggles_threshold: float,
    clearance_seconds: float,
    serial_connected: bool,
) -> None:
    """Draw the operator status panel."""

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

    if state == State.SAFE_CLEAR_PENDING:
        timer_description = (
            f"CLEAR TIMER: {timer_seconds:.2f}"
            f"/{clearance_seconds:.2f} s"
        )
    else:
        timer_description = f"TIMER: {timer_seconds:.2f} s"

    serial_status = (
        "CONNECTED"
        if serial_connected
        else "NOT CONNECTED"
    )

    lines = [
        f"RAW: {observation.value}",
        f"STATE: {state.value}",
        timer_description,
        f"VIOLATION RATIO: {violation_ratio:.2f}",
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

    device = choose_device(args.device)
    model = YOLO(str(weights))

    if model.names.get(0) != "goggles":
        raise SystemExit(
            "ERROR: model class 0 must be goggles"
        )

    if model.names.get(1) != "no_goggles":
        raise SystemExit(
            "ERROR: model class 1 must be no_goggles"
        )

    source = parse_source(args.source)
    capture = cv2.VideoCapture(source)

    if not capture.isOpened():
        raise SystemExit(
            f"ERROR: cannot open source: {source}"
        )

    serial_connection: serial.Serial | None = None

    if args.serial_port:
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
                tuple[str, float]
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

                    accepted_detections.append(
                        (class_name, confidence)
                    )

                    coordinates = tuple(
                        map(
                            int,
                            box.xyxy[0].tolist(),
                        )
                    )

                    draw_detection(
                        annotated,
                        class_name,
                        confidence,
                        coordinates,
                    )

            observation = decide_observation(
                accepted_detections
            )

            state, timer_seconds, violation_ratio = (
                temporal_filter.update(
                    observation,
                    now,
                )
            )

            # Receive ESP32 responses without blocking video.
            read_serial_responses(serial_connection)

            # Transmit only on meaningful state transitions.
            if state != previous_state:
                print(
                    "STATE TRANSITION: "
                    f"{previous_state} -> {state}"
                )

                # Before an incident exists, SAFE turns
                # the physical system green.
                if (
                    state == State.SAFE
                    and temporal_filter.incident_id is None
                ):
                    send_serial_command(
                        serial_connection,
                        "SAFE",
                    )

                # Send one ALARM command for each incident.
                if (
                    state == State.VIOLATION_CONFIRMED
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
                previous_state = state

            draw_status(
                annotated,
                observation,
                state,
                timer_seconds,
                violation_ratio,
                measured_fps,
                temporal_filter.incident_id,
                device,
                args.goggles_confidence,
                args.no_goggles_confidence,
                args.clearance_seconds,
                serial_connection is not None,
            )

            cv2.imshow(
                "Eye-Shield Monitor - AI + ESP32",
                annotated,
            )

            key = cv2.waitKey(1) & 0xFF

            if key in (ord("q"), 27):
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
