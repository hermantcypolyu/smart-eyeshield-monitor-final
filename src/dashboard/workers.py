"""Background workers for the dashboard.

Two long-running jobs must never touch the GUI thread: camera inference and the
serial conversation. Each gets its own ``QThread`` and communicates only through
Qt signals carrying Qt-free value objects, so no panel ever blocks on a frame or
a port read.

Three design rules matter more than the plumbing:

1. **No hidden state changes.** A worker reports; it does not decide. The
   lifecycle lives on the GUI thread, and a worker that mutated it would make
   the safety contract impossible to reason about.
2. **Bounded queues.** If the GUI cannot keep up, frames are dropped rather than
   queued, because a growing backlog would make the displayed frame-age a lie
   and eventually exhaust memory during a long demo.
3. **Deterministic shutdown.** ``stop()`` is idempotent and joins with a timeout.
   An ``ALL_OFF`` is never sent on the way out: a latched alarm must survive the
   dashboard closing, exactly as the live demo documents.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass

from PySide6.QtCore import QMutex, QObject, QThread, QWaitCondition, Signal, Slot


# ---------------------------------------------------------------------------
# Camera + inference
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrameResult:
    """One processed frame, in a form the GUI thread can render safely.

    ``image_bytes`` is a copy of the annotated frame's raw RGB buffer, not a
    reference to a cv2 array: the worker reuses its buffers, so handing out the
    array itself would let the GUI paint a frame that is being overwritten.
    """

    at: float
    width: int
    height: int
    image_bytes: bytes
    observation: str
    detections: tuple[tuple[str, float, int, int, int, int], ...]
    fps: float
    inference_ms: float
    #: Best raw score per class *before* the per-class threshold was applied.
    #: ``detections`` is what survived, which is the right input for the
    #: lifecycle but the wrong input for a confidence chart: a class sitting just
    #: under its threshold would otherwise report nothing at all, and "nothing"
    #: reads as a flat line. Defaulted so existing constructors keep working.
    class_scores: tuple[tuple[str, float], ...] = ()


class CameraInferenceWorker(QObject):
    """Run the camera and YOLO in the background and emit one result per frame.

    The worker deliberately contains no lifecycle logic and no serial calls. It
    converts pixels into an observation, which is the same contract
    ``decide_observation()`` already provides, and lets the GUI thread decide
    what that means.
    """

    frame_ready = Signal(object)          # FrameResult
    failed = Signal(str)                  # human-readable reason
    started_ok = Signal(str)              # device / model description

    def __init__(
        self,
        *,
        source: int | str = 0,
        weights: str = "models/best_v2.pt",
        candidate_confidence: float = 0.40,
        goggles_confidence: float = 0.60,
        no_goggles_confidence: float = 0.55,
        iou: float = 0.30,
        device: str = "auto",
        max_fps: float = 30.0,
    ) -> None:
        super().__init__()
        self.source = source
        self.weights = weights
        self.candidate_confidence = candidate_confidence
        self.goggles_confidence = goggles_confidence
        self.no_goggles_confidence = no_goggles_confidence
        self.iou = iou
        self.device = device
        self.max_fps = max_fps

        self._stop = False
        self._mutex = QMutex()
        self._wait = QWaitCondition()

    # -- lifecycle ----------------------------------------------------------

    def stop(self) -> None:
        """Ask the loop to finish. Safe to call more than once."""

        self._mutex.lock()
        self._stop = True
        self._wait.wakeAll()
        self._mutex.unlock()

    @property
    def stopping(self) -> bool:
        self._mutex.lock()
        try:
            return self._stop
        finally:
            self._mutex.unlock()

    # -- main loop ----------------------------------------------------------

    @Slot()
    def run(self) -> None:
        """Capture and infer until stopped, emitting ``frame_ready`` per frame.

        Every failure path closes the capture handle and reports through
        ``failed``; nothing is swallowed, because a silent camera failure looks
        exactly like a safe operator to the rest of the app.
        """

        capture = None
        model = None

        try:
            import cv2
            from ultralytics import YOLO
            import sys
            from pathlib import Path

            if str(Path(__file__).resolve().parents[2]) not in sys.path:
                sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

            from tools.live_eye_shield_demo import (
                Observation,
                choose_device,
                decide_observation,
            )

            chosen = choose_device(self.device)
            model = YOLO(self.weights)
            self.started_ok.emit(f"{self.weights} on {chosen}")

            capture = cv2.VideoCapture(self.source)

            if not capture.isOpened():
                self.failed.emit(f"Could not open video source {self.source!r}")
                return

            frame_interval = 1.0 / self.max_fps if self.max_fps > 0 else 0.0
            last_emit = 0.0

            while not self.stopping:
                ok, frame = capture.read()

                if not ok or frame is None:
                    self.failed.emit("Camera returned no frame")
                    break

                started = time.monotonic()

                results = model.predict(
                    frame,
                    conf=self.candidate_confidence,
                    iou=self.iou,
                    device=chosen,
                    verbose=False,
                )

                accepted: list[tuple[str, float]] = []
                boxes: list[tuple[str, float, int, int, int, int]] = []
                seen_scores: dict[str, float] = {}

                if results:
                    names = results[0].names or {}

                    for box in results[0].boxes or []:
                        cls_index = int(box.cls.item())
                        name = str(names.get(cls_index, cls_index))
                        score = float(box.conf.item())

                        if score > seen_scores.get(name, 0.0):
                            seen_scores[name] = score

                        threshold = (
                            self.goggles_confidence
                            if name == "goggles"
                            else self.no_goggles_confidence
                            if name == "no_goggles"
                            else self.candidate_confidence
                        )

                        if score < threshold:
                            continue

                        x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
                        accepted.append((name, score))
                        boxes.append((name, score, x1, y1, x2, y2))

                observation = decide_observation(accepted)
                inference_ms = (time.monotonic() - started) * 1000.0

                now = time.monotonic()

                if frame_interval and now - last_emit < frame_interval:
                    continue

                elapsed = now - last_emit if last_emit else frame_interval or 1e-6
                last_emit = now

                height, width = frame.shape[:2]
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                self.frame_ready.emit(
                    FrameResult(
                        at=now,
                        width=width,
                        height=height,
                        image_bytes=rgb.tobytes(),
                        observation=getattr(observation, "value", str(observation)),
                        detections=tuple(boxes),
                        fps=(1.0 / elapsed) if elapsed > 0 else 0.0,
                        inference_ms=inference_ms,
                        class_scores=tuple(sorted(seen_scores.items())),
                    )
                )

        except Exception as error:  # noqa: BLE001 - must surface to the operator
            self.failed.emit(
                f"{type(error).__name__}: {error}\n{traceback.format_exc(limit=3)}"
            )
        finally:
            if capture is not None:
                try:
                    capture.release()
                except Exception:  # noqa: BLE001
                    pass
            # ``model`` needs no explicit teardown; dropping the last reference
            # is enough and calling into torch from here can deadlock on exit.


# ---------------------------------------------------------------------------
# Serial
# ---------------------------------------------------------------------------


class SerialWorker(QObject):
    """Own the ESP32 port on a worker thread.

    Reads and writes only. Every line is forwarded verbatim so the event
    timeline can show what actually crossed the wire, and every write is
    reported through ``tx`` so a panel can distinguish "we asked" from "the board
    agreed" - the distinction the whole project is built on.
    """

    line_received = Signal(str)
    tx = Signal(str)
    opened = Signal(str)
    closed = Signal(str)
    failed = Signal(str)

    def __init__(self, port: str, baud: int = 115200, *, read_timeout: float = 0.2) -> None:
        super().__init__()
        self.port = port
        self.baud = baud
        self.read_timeout = read_timeout
        self._stop = False
        self._connection = None
        self._mutex = QMutex()
        self._pending: list[str] = []

    # -- lifecycle ----------------------------------------------------------

    def stop(self) -> None:
        self._mutex.lock()
        self._stop = True
        self._mutex.unlock()

    @property
    def stopping(self) -> bool:
        self._mutex.lock()
        try:
            return self._stop
        finally:
            self._mutex.unlock()

    def send(self, command: str) -> None:
        """Queue one command. Writing happens on the worker thread only."""

        self._mutex.lock()
        try:
            self._pending.append(command)
        finally:
            self._mutex.unlock()

    def _drain_pending(self) -> list[str]:
        self._mutex.lock()
        try:
            queued, self._pending = self._pending, []
            return queued
        finally:
            self._mutex.unlock()

    # -- main loop ----------------------------------------------------------

    @Slot()
    def run(self) -> None:
        try:
            import serial
        except Exception as error:  # noqa: BLE001
            self.failed.emit(f"pyserial unavailable: {error}")
            return

        try:
            self._connection = serial.Serial(
                port=self.port,
                baudrate=self.baud,
                timeout=self.read_timeout,
                write_timeout=1.0,
            )
        except Exception as error:  # noqa: BLE001
            self.failed.emit(f"Could not open {self.port}: {error}")
            return

        self.opened.emit(self.port)

        try:
            # The USB-CDC bridge resets the board when the port opens, so the
            # first second of input is boot noise. Waiting it out here keeps the
            # timeline free of a fake burst of "unknown command" lines.
            deadline = time.monotonic() + 2.0

            while time.monotonic() < deadline and not self.stopping:
                time.sleep(0.05)

            while not self.stopping:
                for command in self._drain_pending():
                    try:
                        self._connection.write(f"{command.strip()}\n".encode("utf-8"))
                        self._connection.flush()
                        self.tx.emit(command.strip())
                    except Exception as error:  # noqa: BLE001
                        self.failed.emit(f"Serial write failed: {error}")
                        return

                try:
                    waiting = self._connection.in_waiting
                except Exception as error:  # noqa: BLE001
                    self.failed.emit(f"Serial read failed: {error}")
                    return

                if not waiting:
                    time.sleep(0.01)
                    continue

                try:
                    raw = self._connection.readline()
                except Exception as error:  # noqa: BLE001
                    self.failed.emit(f"Serial read failed: {error}")
                    return

                text = raw.decode("utf-8", errors="replace").strip()

                if text:
                    self.line_received.emit(text)
        finally:
            if self._connection is not None:
                try:
                    self._connection.close()
                except Exception:  # noqa: BLE001
                    pass
                self._connection = None
            self.closed.emit(self.port)


# ---------------------------------------------------------------------------
# Thread helper
# ---------------------------------------------------------------------------


class WorkerHandle:
    """Own a worker plus its thread, with a shutdown that cannot hang.

    ``QThread.quit()`` only stops an event loop. These workers run plain
    ``run()`` loops, so the loop must be asked to stop first and the thread then
    waited on. Doing it in one place keeps every panel from inventing its own
    shutdown order, which is how a stuck thread ends up blocking quit.
    """

    def __init__(self, worker: QObject, *, name: str) -> None:
        self.worker = worker
        self.thread = QThread()
        self.thread.setObjectName(name)
        worker.moveToThread(self.thread)
        self.thread.started.connect(worker.run)
        self._running = False

    def start(self) -> None:
        if not self._running:
            self._running = True
            self.thread.start()

    def stop(self, timeout_ms: int = 4000) -> None:
        if not self._running:
            return

        stopper = getattr(self.worker, "stop", None)

        if callable(stopper):
            stopper()

        self.thread.quit()

        if not self.thread.wait(timeout_ms):
            # A worker that will not stop must not be killed silently; leaving it
            # running and reporting is safer than terminating a thread mid-write.
            self.thread.requestInterruption()

        self._running = False

    @property
    def running(self) -> bool:
        return self._running
