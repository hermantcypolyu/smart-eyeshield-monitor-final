"""Main window: header, panel layout, wiring, shortcuts, shutdown.

Three rules shape this file.

**One writer.** The lifecycle and the correlator live on the GUI thread, and only
this class mutates them. Workers report frames and serial lines through signals;
nothing else touches safety state. That is what makes every read of
``lifecycle`` consistent without a lock anywhere.

**Reuse, don't re-implement.** ``Observation``, ``TemporalFilter``,
``CommandCorrelator`` and the reply parser are imported from
``tools.live_eye_shield_demo``. A copy would let the dashboard's idea of a reply
drift from the demo's, and showing the same thing the demo shows is the whole
point.

**Never invent state.** Pin states come from the firmware's ``STATUS`` line only.
Until one has been parsed the lamps read ``UNKNOWN``, because a dashboard that
lights green from its own belief is not measuring anything.
"""

from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from tools.incident_lifecycle import IncidentLifecycle, ResetBlocker

from . import branding, theme
from .adapters import (
    DemoDataDriver,
    SessionAccumulator,
    build_health_items,
    parse_firmware_status,
    reset_blocker_text,
    state_duration_shares,
)
from .camera_panel import CameraPanel
from .charts_panel import ChartsPanel
from .control_panel import ControlPanel
from .event_timeline import EventTimeline
from .hardware_panel import HardwarePanel
from .health_panel import HealthPanel
from .incident_panel import IncidentPanel
from .metrics_panel import MetricsPanel
from .safety_state_panel import SafetyStatePanel
from .signal_flow import SignalFlowWidget
from .state_models import (
    BoardSnapshot,
    DashboardSnapshot,
    GaugeSnapshot,
    LinkState,
    TimelineEntry,
)
from .workers import CameraInferenceWorker, SerialWorker, WorkerHandle

REPO_ROOT = Path(__file__).resolve().parents[2]

#: How long a lifecycle command may wait for its reply before the link is judged
#: broken. Matches the demo so the two cannot disagree about a dead board.
REPLY_TIMEOUT_SECONDS = 2.0


def _protocol_helpers():
    """Import the demo's protocol helpers with the repo root importable."""

    root = str(REPO_ROOT)

    if root not in sys.path:
        sys.path.insert(0, root)

    from tools.live_eye_shield_demo import (  # noqa: PLC0415
        CommandCorrelator,
        Observation,
        TemporalFilter,
        parse_serial_reply,
    )

    return CommandCorrelator, Observation, TemporalFilter, parse_serial_reply


class _WorkerPort:
    """Adapt ``SerialWorker.send`` to the duck-typed port the correlator writes to.

    ``CommandCorrelator.send`` writes through ``connection.write``/``flush``,
    because the demo owns the port directly. The dashboard writes on a worker
    thread instead, so this shim queues the bytes. Returning the byte count keeps
    the contract the demo's ``send_serial_command`` checks: the command was
    accepted for transmission, not delivered.
    """

    def __init__(self, worker: SerialWorker) -> None:
        self._worker = worker

    def write(self, data: bytes) -> int:
        text = data.decode("utf-8", errors="replace").strip()

        if text:
            self._worker.send(text)

        return len(data)

    def flush(self) -> None:
        return None


class HeaderBar(QFrame):
    """White header: PolyU logo, title block, EIE logo, burgundy rule."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Header")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        row = QWidget()
        row.setStyleSheet(f"background-color: {theme.HEADER_WHITE};")

        layout = QHBoxLayout(row)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(18)

        self.polyu_logo = branding.LogoLabel(branding.POLYU, align=Qt.AlignLeft)
        layout.addWidget(self.polyu_logo, 0, Qt.AlignVCenter)

        centre = QVBoxLayout()
        centre.setContentsMargins(0, 0, 0, 0)
        centre.setSpacing(1)

        title = QLabel(branding.TITLE)
        title.setObjectName("HeaderTitle")
        title.setAlignment(Qt.AlignCenter)
        centre.addWidget(title)

        subtitle = QLabel(branding.SUBTITLE)
        subtitle.setObjectName("HeaderSubtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        centre.addWidget(subtitle)

        context = QLabel(branding.CONTEXT)
        context.setObjectName("HeaderContext")
        context.setAlignment(Qt.AlignCenter)
        centre.addWidget(context)

        layout.addLayout(centre, 1)

        self.eie_logo = branding.LogoLabel(branding.EIE, align=Qt.AlignRight)
        layout.addWidget(self.eie_logo, 0, Qt.AlignVCenter)

        outer.addWidget(row)

        rule = QFrame()
        rule.setFixedHeight(4)
        rule.setStyleSheet(f"background-color: {theme.BURGUNDY};")
        outer.addWidget(rule)


class BannerBar(QLabel):
    """Full-width banner for DEMO DATA and for trust-breaking conditions."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Banner")
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignCenter)
        self.setVisible(False)

    def show_banner(self, text: str, colour: str) -> None:
        self.setText(text)
        self.setStyleSheet(
            "QLabel {"
            f"background-color: {colour};"
            "color: #FFFFFF;"
            "padding: 7px 14px;"
            "font-size: 11px;"
            "font-weight: 700;"
            "letter-spacing: 0.6px;"
            "}"
        )
        self.setVisible(bool(text))

    def hide_banner(self) -> None:
        self.setText("")
        self.setVisible(False)


class MainWindow(QMainWindow):
    """The operations centre window."""

    def __init__(
        self,
        *,
        demo_data: bool = False,
        source: int | str = 0,
        weights: str = "models/best_v2.pt",
        candidate_confidence: float = 0.40,
        goggles_confidence: float = 0.60,
        no_goggles_confidence: float = 0.55,
        iou: float = 0.30,
        device: str = "auto",
        serial_port: str | None = None,
        confirmation_seconds: float = 2.0,
        clearance_seconds: float = 2.0,
        max_runtime: float = 0.0,
        autostart: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        #: False builds the full widget tree and leaves the safety state alone,
        #: which is what automated tests and an embedded view need: no camera
        #: thread, no serial thread, no device claimed behind the caller's back.
        self.autostart = autostart

        self.demo_data = demo_data
        self.serial_port = serial_port
        self.source = source
        self.weights = weights

        CommandCorrelator, Observation, TemporalFilter, parse_serial_reply = (
            _protocol_helpers()
        )

        self.Observation = Observation
        self.parse_serial_reply = parse_serial_reply

        self.lifecycle = IncidentLifecycle(
            confirmation_seconds=confirmation_seconds,
            clearance_seconds=clearance_seconds,
        )
        self.lifecycle.start(time.monotonic())

        self.temporal = TemporalFilter(
            confirmation_seconds=confirmation_seconds,
            clearance_seconds=clearance_seconds,
        )
        self.correlator = CommandCorrelator(timeout_seconds=REPLY_TIMEOUT_SECONDS)

        self.accumulator = SessionAccumulator()

        self.board = BoardSnapshot(port=serial_port or "", baud=115200)
        self.gauge = GaugeSnapshot(
            confirmation_seconds=confirmation_seconds,
            clearance_seconds=clearance_seconds,
        )

        self._session_start = time.monotonic()
        self._last_requested = ""
        self._last_acknowledged = ""
        self._camera_ok = False
        self._model_ok = False
        self._serial_ok: bool | None = False if serial_port else None
        self._serial_detail = ""
        self._status_age: float | None = None

        self._camera: WorkerHandle | None = None
        self._serial: WorkerHandle | None = None
        self._port: _WorkerPort | None = None

        self._fixture: DemoDataDriver | None = None
        self._fixture_last = time.monotonic()
        self._fixture_timeline_index = 0

        # Firmware upload identity and thermal limits are recorded so the header
        # can be checked against the board without opening a terminal.
        self.weights = weights
        self.candidate_confidence = candidate_confidence
        self.goggles_confidence = goggles_confidence
        self.no_goggles_confidence = no_goggles_confidence
        self.iou = iou
        self.device = device

        self.setWindowTitle(branding.TITLE)
        self.resize(1520, 950)

        self._build_ui()
        self._install_shortcuts()
        self._start_timers()

        if max_runtime > 0:
            QTimer.singleShot(int(max_runtime * 1000), self._on_max_runtime)

        if self.autostart:
            self._start_workers()
        elif self.demo_data:
            # The fixture claims no device, so it is safe to run headless. Tests
            # and screenshots need the deterministic stream without a webcam.
            self._start_fixture()

        self._refresh()

    # -- construction -------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("Central")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = HeaderBar()
        root.addWidget(self.header)

        self.banner = BannerBar()
        root.addWidget(self.banner)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        root.addWidget(scroll, 1)

        content = QWidget()
        content.setObjectName("Content")
        scroll.setWidget(content)

        grid = QGridLayout(content)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        # Row 0: camera | safety state | hardware
        self.camera_panel = CameraPanel()
        self.camera_panel.setMinimumWidth(420)
        grid.addWidget(self.camera_panel, 0, 0)

        self.safety_panel = SafetyStatePanel()
        grid.addWidget(self.safety_panel, 0, 1)

        self.hardware_panel = HardwarePanel()
        grid.addWidget(self.hardware_panel, 0, 2)

        # Row 1-2: incident | charts (tall) | health + metrics
        self.incident_panel = IncidentPanel()
        grid.addWidget(self.incident_panel, 1, 0)

        self.charts_panel = ChartsPanel()
        grid.addWidget(self.charts_panel, 1, 1, 2, 1)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(12)

        self.health_panel = HealthPanel()
        right.addWidget(self.health_panel)

        self.metrics_panel = MetricsPanel()
        right.addWidget(self.metrics_panel)
        right.addStretch(1)

        right_host = QWidget()
        right_host.setLayout(right)
        grid.addWidget(right_host, 1, 2)

        # Row 2: operator actions
        self.control_panel = ControlPanel()
        self.control_panel.acknowledge_requested.connect(self.request_acknowledge)
        self.control_panel.silence_requested.connect(self.request_acknowledge)
        self.control_panel.reset_requested.connect(self.request_reset)
        self.control_panel.export_requested.connect(self.export_session)
        grid.addWidget(self.control_panel, 2, 0)

        # Row 3: timeline | signal flow
        self.timeline = EventTimeline()
        self.timeline.setMinimumHeight(220)
        grid.addWidget(self.timeline, 3, 0, 1, 2)

        flow_card = QFrame()
        flow_card.setObjectName("Card")
        flow_layout = QVBoxLayout(flow_card)
        flow_layout.setContentsMargins(14, 12, 14, 12)
        flow_layout.setSpacing(6)

        flow_title = QLabel("SIGNAL FLOW")
        flow_title.setObjectName("CardTitle")
        flow_layout.addWidget(flow_title)

        self.signal_flow = SignalFlowWidget()
        flow_layout.addWidget(self.signal_flow)

        flow_note = QLabel(
            "Where the chain stops is the fault. PINS shows firmware-reported "
            "output, never a guess."
        )
        flow_note.setObjectName("CardCaption")
        flow_note.setWordWrap(True)
        flow_layout.addWidget(flow_note)
        flow_layout.addStretch(1)

        grid.addWidget(flow_card, 3, 2)

        for column in range(3):
            grid.setColumnStretch(column, 1)

        self.status_label = QLabel("Starting\u2026")
        self.statusBar().addWidget(self.status_label, 1)

    def _install_shortcuts(self) -> None:
        bindings = (
            ("F", self.toggle_fullscreen),
            ("Escape", self.leave_fullscreen),
            ("S", self.request_acknowledge),
            ("A", self.request_acknowledge),
            ("R", self.request_reset),
            ("Q", self.close),
            ("Ctrl+E", self.export_session),
            ("F1", self.show_help),
        )

        self._shortcuts: list[QShortcut] = []

        for sequence, handler in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(handler)
            self._shortcuts.append(shortcut)

    def _start_timers(self) -> None:
        self.paint_timer = QTimer(self)
        self.paint_timer.setInterval(100)
        self.paint_timer.timeout.connect(self._refresh)
        self.paint_timer.start()

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(1000)
        self.poll_timer.timeout.connect(self._poll_board)
        self.poll_timer.start()

    # -- workers ------------------------------------------------------------

    def _start_workers(self) -> None:
        if self.demo_data:
            self._start_fixture()
            return

        self._log("operator", "Session starting", source="operator")

        camera = CameraInferenceWorker(
            source=self.source,
            weights=self.weights,
            candidate_confidence=self.candidate_confidence,
            goggles_confidence=self.goggles_confidence,
            no_goggles_confidence=self.no_goggles_confidence,
            iou=self.iou,
            device=self.device,
        )
        camera.frame_ready.connect(self._on_frame)
        camera.failed.connect(self._on_camera_failed)
        camera.started_ok.connect(self._on_model_loaded)

        self._camera = WorkerHandle(camera, name="camera")
        self._camera.start()

        if self.serial_port:
            worker = SerialWorker(self.serial_port)
            worker.line_received.connect(self._on_serial_line)
            worker.tx.connect(self._on_serial_tx)
            worker.opened.connect(self._on_serial_opened)
            worker.closed.connect(self._on_serial_closed)
            worker.failed.connect(self._on_serial_failed)

            self._serial = WorkerHandle(worker, name="serial")
            self._port = _WorkerPort(worker)
            self._serial.start()
        else:
            self._log(
                "operator",
                "No serial port supplied: the board is not under control",
                source="operator",
            )

        self._push_banner()

    def _start_fixture(self) -> None:
        self._fixture = DemoDataDriver(self.lifecycle)
        now = time.monotonic()
        self._fixture.prime(now)
        self._fixture_last = now
        self._fixture_timeline_index = len(self._fixture.timeline)

        self._camera_ok = True
        self._model_ok = True
        self._serial_ok = True
        self._serial_detail = "fixtures (demo data)"
        self.board = self._fixture.snapshot_board()

        self._log("operator", "Demo-data session started", source="operator")
        self._push_banner()

    # -- frame handling -----------------------------------------------------

    def _on_model_loaded(self, description: str) -> None:
        self._model_ok = True
        self._log("operator", f"Model loaded: {description}", source="operator")

    def _on_camera_failed(self, reason: str) -> None:
        self._camera_ok = False
        first = reason.strip().splitlines()[0] if reason.strip() else "unknown"
        self._log("operator", f"Camera failure: {first}", source="operator")
        self._push_banner()

    def _on_frame(self, frame) -> None:  # noqa: ANN001 - FrameResult
        """One camera frame: render it, then advance filter and lifecycle."""

        now = time.monotonic()

        if not self._camera_ok:
            self._camera_ok = True
            self._push_banner()

        self.camera_panel.show_frame(frame)

        peak = max((box[1] for box in frame.detections), default=0.0)
        self.charts_panel.push_telemetry(
            fps=frame.fps,
            inference_ms=frame.inference_ms,
            frame_age_ms=max(0.0, (now - frame.at) * 1000.0),
            max_confidence=peak,
        )

        observation = getattr(self.Observation, str(frame.observation), None)

        if observation is None:
            # An unrecognised verdict is missing information, and missing
            # information must never be promoted to SAFE.
            observation = self.Observation.UNKNOWN

        self._advance(observation, now, kind=str(frame.observation))

    def _advance(self, observation, now: float, *, kind: str) -> None:
        """Run one tick of the filter and lifecycle, then drain commands.

        ``pending_since``/``safe_since`` come from the filter rather than being
        recreated here. A fresh timestamp each frame would restart the
        confirmation timer and the incident would never latch.
        """

        temporal = self.temporal.update(observation, now)

        self.lifecycle.set_health(
            camera_ok=self._camera_ok,
            model_ok=self._model_ok,
            serial_ok=bool(self._serial_ok),
            now=now,
        )

        status = self.lifecycle.update(
            observation,
            now,
            violation_ratio=temporal.violation_ratio,
            pending_since=self.temporal.pending_since,
            safe_since=self.temporal.safe_since,
        )

        # Recovery is only provable because the detector watched it happen. The
        # demo calls this from its loop, so the dashboard must too, or the reset
        # gate stays shut for a recovery that really occurred.
        if status.safe_recovered:
            self.lifecycle.mark_onboard_verified()

        self.gauge = GaugeSnapshot(
            observation=getattr(observation, "value", kind),
            state=status.state.value,
            violation_ratio=temporal.violation_ratio,
            pending_elapsed=temporal.pending_elapsed,
            confirmation_seconds=self.lifecycle.confirmation_seconds,
            safe_clear_elapsed=temporal.safe_clear_elapsed,
            clearance_seconds=self.lifecycle.clearance_seconds,
            confirmed_after_seconds=status.confirmed_after_seconds,
            violation_ratio_required=self.temporal.violation_ratio_required,
        )

        self.accumulator.record_frame(kind)
        self.accumulator.record_state(status.state.value, now)

        if status.incident_id is not None:
            self.accumulator.incidents = max(
                self.accumulator.incidents, status.incident_id
            )

        self._drain_commands(now)

    # -- serial -------------------------------------------------------------

    def _on_serial_opened(self, port: str) -> None:
        self._serial_ok = True
        self._serial_detail = "connected"
        self.board = BoardSnapshot(port=port, baud=self.board.baud, link=LinkState.OPEN)
        self._log("operator", f"Serial port open: {port}", source="operator")
        self._push_banner()

    def _on_serial_closed(self, port: str) -> None:
        self._serial_ok = False
        self._serial_detail = "closed"
        self.board = BoardSnapshot(
            port=port, baud=self.board.baud, link=LinkState.CLOSED
        )
        self._log("operator", f"Serial port closed: {port}", source="operator")
        self._push_banner()

    def _on_serial_failed(self, reason: str) -> None:
        self._serial_ok = False
        first = reason.strip().splitlines()[0] if reason.strip() else "unknown"
        self._serial_detail = first
        self.board = BoardSnapshot(
            port=self.board.port, baud=self.board.baud, link=LinkState.ERROR
        )
        self._log("operator", f"Serial failure: {first}", source="operator")
        self._push_banner()

    def _on_serial_tx(self, command: str) -> None:
        self._last_requested = command
        self._log("tx", command, source="tx")

    def _on_serial_line(self, line: str) -> None:
        text = line.strip()

        if not text:
            return

        self._log("rx", text, source="rx")

        if text.startswith("STATUS "):
            self._apply_status(text)
            return

        if not (text.startswith("OK") or text.startswith("ERR")):
            # Boot banner, HELP text and the human-readable STATUS block are not
            # protocol replies. Parsing them as refusals was a real bug.
            return

        reply = self.parse_serial_reply(text)

        if reply is None:
            return

        verdict = self.correlator.match(reply)

        if verdict == "IGNORED":
            outstanding = self.correlator.outstanding
            self._log(
                "correlation",
                f"ignored {text!r}; waiting for "
                f"{outstanding.expected_code if outstanding else '-'}",
            )
            return

        if verdict == "IDLE":
            # Unsolicited line: useful in the log, but it answers nothing, so it
            # must not change any state.
            return

        now = time.monotonic()

        if reply.ok and reply.code == "ACKNOWLEDGED":
            incident_id = (
                self.lifecycle.incident.incident_id
                if self.lifecycle.incident is not None
                else None
            )

            if self.lifecycle.confirm_acknowledge(reply, now, incident_id) == "CONFIRMED":
                self.accumulator.acknowledgements += 1

        if reply.ok and reply.code == "RESET_ACCEPTED":
            self.accumulator.resets += 1

        follow_up = self.lifecycle.resolve_pending_command(reply)

        if follow_up is not None:
            self._send(follow_up, now)

        self._drain_commands(now)

    def _apply_status(self, line: str) -> None:
        try:
            firmware = parse_firmware_status(line)
        except ValueError as error:
            self._log("operator", f"Malformed STATUS line: {error}", source="rx")
            return

        if firmware is None:
            return

        self._serial_ok = True
        self._serial_detail = "connected"
        self._status_age = 0.0

        self.board = BoardSnapshot(
            port=self.board.port,
            baud=self.board.baud,
            link=LinkState.OPEN,
            outputs=firmware.to_outputs(),
            firmware_version=firmware.state,
            incident_id=firmware.incident,
            acknowledged=firmware.ack,
            reset_ready=firmware.reset_ready,
            rearm_count=firmware.rearm,
            last_error="",
            status_at=time.monotonic(),
        )

        self.accumulator.rearm_count = firmware.rearm

    def _poll_board(self) -> None:
        """Ask the board two fire-and-forget questions.

        Deliberately outside the correlator: these repeat on a fixed period, so
        claiming the correlation slot for them would starve the lifecycle
        commands that actually gate the lamp. Polling pauses while a lifecycle
        command is in flight so replies stay in order.
        """

        if self._serial is None or self.correlator.busy:
            return

        self._serial.worker.send("STATUS")
        self._serial.worker.send("HEARTBEAT")

    # -- command dispatch ---------------------------------------------------

    def _drain_commands(self, now: float) -> None:
        """Push lifecycle-implied commands to the board.

        The reset token is read from ``pending_reset_command`` because it is not
        in the transition log, so ``take_pending_command()`` can never return it.
        """

        while True:
            command = self.lifecycle.take_pending_command()

            if command is None:
                break

            if not self._send(command, now):
                self.lifecycle.requeue_last_command()
                break

        token = self.lifecycle.pending_reset_command

        if token is not None and not self.correlator.busy:
            # The token must survive until its reply arrives: it is the reply to
            # RESET_READY that advances it to RESET.
            self._send(token, now)

    def _send(self, command: str, now: float) -> bool:
        """Transmit one command, claiming the correlation slot.

        A missing port is *not* success. Reporting it as success would advance
        the lifecycle as though the board had been told, which is exactly how a
        dashboard ends up green while the lamp is dark. The command stays
        queued instead, and the banner says the board cannot be reached.
        """

        if self._serial is None or self._port is None:
            self._log("correlation", f"QUEUED {command!r}: no serial port open")
            self._serial_ok = False
            self._serial_detail = "no serial port open"
            return False

        incident_id = (
            self.lifecycle.incident.incident_id
            if self.lifecycle.incident is not None
            else None
        )

        if self.correlator.send(self._port, command, now, incident_id):
            return True

        reason = (
            f"waiting for {self.correlator.outstanding.command!r}"
            if self.correlator.busy
            else "not permitted by the protocol table"
        )
        self._log("correlation", f"BLOCKED {command!r}: {reason}")
        return False

    def _retry_pending_reset(self, now: float) -> None:
        """Send the reset token once the correlation slot frees up.

        Without this, a token minted while another command was in flight would
        sit unsent forever and the reset gate would appear open while nothing
        reached the board.
        """

        token = self.lifecycle.pending_reset_command

        if token is None or self.correlator.busy:
            return

        self._send(token, now)

    def _expire_correlator(self) -> None:
        expired = self.correlator.expire(time.monotonic())

        if expired is None:
            return

        # No answer is not success: drop the link so the laptop parks in FAULT
        # instead of pretending the command landed.
        self._log("correlation", f"TIMEOUT waiting for reply to {expired!r}")
        self._serial_ok = False
        self._serial_detail = f"no reply to {expired}"

    # -- operator requests --------------------------------------------------

    def request_acknowledge(self) -> None:
        now = time.monotonic()
        ok, reason = self.lifecycle.acknowledge(now)

        self._log(
            "operator",
            f"Acknowledge requested ({reason})"
            if ok
            else f"Acknowledge refused: {reason}",
            source="operator",
        )

        self._drain_commands(now)

    def request_reset(self) -> None:
        now = time.monotonic()
        ok, reason = self.lifecycle.request_reset(now)

        self._log(
            "operator",
            f"Reset requested ({reason})" if ok else f"Reset refused: {reason}",
            source="operator",
        )

        self._drain_commands(now)

    # -- presentation -------------------------------------------------------

    def _refresh(self) -> None:
        now = time.monotonic()

        if self._fixture is not None:
            self._step_fixture(now)
        else:
            self._expire_correlator()
            self._retry_pending_reset(now)

        self._status_age = (
            None
            if self.board.status_at is None
            else max(0.0, now - self.board.status_at)
        )

        status = self.lifecycle.status()

        if status.incident_id is not None and status.safe_recovered:
            self.lifecycle.mark_onboard_verified()

        counters = (
            self._fixture.accumulator.snapshot()
            if self._fixture is not None
            else self.accumulator.snapshot()
        )

        # Recompute the banner from this tick's truth, so the snapshot and the
        # bar above the panels can never disagree.
        self._push_banner()

        snapshot = DashboardSnapshot(
            lifecycle=status,
            board=self.board,
            gauge=self.gauge,
            counters=counters,
            health=self._health(),
            reset_blocker=self.lifecycle.reset_blocker(),
            demo_data=self.demo_data,
            banner=self.banner.text(),
        )

        self.safety_panel.update_from(snapshot)
        self.hardware_panel.update_from(
            snapshot,
            last_requested=self._last_requested,
            last_acknowledged=self._last_acknowledged,
            now=now,
        )
        self.incident_panel.update_from(snapshot)
        self.health_panel.update_from(snapshot)
        self.charts_panel.update_from(
            snapshot,
            shares=state_duration_shares(counters.state_durations),
        )
        self.control_panel.update_from(snapshot)
        self._update_signal_flow(snapshot)
        self._update_status_bar(snapshot)

    def _health(self):
        return build_health_items(
            camera_ok=None if self.demo_data else self._camera_ok,
            model_ok=None if self.demo_data else self._model_ok,
            serial_ok=None if self.demo_data else self._serial_ok,
            serial_detail=self._serial_detail,
            status_age_seconds=self._status_age,
            demo_data=self.demo_data,
            missing_logo=bool(branding.missing_logos()),
        )

    def _step_fixture(self, now: float) -> None:
        driver = self._fixture

        if driver is None:
            return

        dt = max(0.0, now - self._fixture_last)
        self._fixture_last = now

        driver.step(now, dt)

        self.board = driver.snapshot_board()
        self.gauge = driver.gauge()

        for entry in driver.timeline[self._fixture_timeline_index :]:
            self.timeline.add(entry)

        self._fixture_timeline_index = len(driver.timeline)

        if driver.sent_commands:
            self._last_requested = driver.sent_commands[-1]

    def _update_signal_flow(self, snapshot: DashboardSnapshot) -> None:
        serial_up = snapshot.board.link is LinkState.OPEN

        live = {
            "camera": self._camera_ok,
            "detector": self._model_ok,
            "filter": snapshot.counters.frames > 0,
            "lifecycle": True,
            "serial": serial_up,
            "pins": snapshot.board.outputs.reported,
        }

        break_at = ""

        for key in ("camera", "detector", "filter", "serial", "pins"):
            if not live[key]:
                break_at = key
                break

        notes = {
            "camera": "frames" if live["camera"] else "no frames",
            "detector": "ok" if live["detector"] else "not loaded",
            "filter": f"{snapshot.counters.frames} frames" if live["filter"] else "idle",
            "serial": "open" if live["serial"] else "closed",
            "pins": "reported" if live["pins"] else "unknown",
        }

        self.signal_flow.set_flow(live, break_at=break_at, notes=notes)

    def _update_status_bar(self, snapshot: DashboardSnapshot) -> None:
        blocker = self.lifecycle.reset_blocker()
        reset_text = (
            "reset gate open"
            if blocker is ResetBlocker.NONE
            else reset_blocker_text(blocker).rstrip(".")
        )

        self.status_label.setText(
            f"{snapshot.state.value}"
            f"  \u00b7  alarm {snapshot.alarm.value.lower()}"
            f"  \u00b7  incident {snapshot.lifecycle.incident_id or 'none'}"
            f"  \u00b7  {reset_text}"
            f"  \u00b7  frames {snapshot.counters.frames}"
            f"  \u00b7  {'DEMO DATA' if self.demo_data else 'live'}"
        )

    def _push_banner(self) -> None:
        if self.demo_data:
            self.banner.show_banner(
                "DEMO DATA  \u00b7  no camera, no serial port  \u00b7  "
                "fixtures only, not a live safety system",
                theme.AMBER,
            )
            return

        reasons: list[str] = []

        # Without a configured port nothing can reach the board, however healthy
        # the camera is. That is the most important thing on the screen.
        if not self.serial_port:
            reasons.append("NO SERIAL PORT \u00b7 this window cannot drive the board")
        elif self._serial_ok is False:
            reasons.append("SERIAL LINK DOWN \u00b7 the board is not receiving commands")

        if not self._camera_ok:
            reasons.append("CAMERA UNAVAILABLE \u00b7 no verdicts are being produced")

        if reasons:
            self.banner.show_banner("   \u00b7   ".join(reasons), theme.RED)
        else:
            self.banner.hide_banner()

    def _log(self, kind: str, message: str, *, source: str = "lifecycle") -> None:
        status = self.lifecycle.status()

        self.timeline.add(
            TimelineEntry(
                at=time.monotonic() - self._session_start,
                kind=kind,
                message=message,
                state=status.state.value,
                incident_id=status.incident_id,
                source=source,
            )
        )

    # -- actions ------------------------------------------------------------

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def leave_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()

    def show_help(self) -> None:
        QMessageBox.information(
            self,
            "Keyboard shortcuts",
            "F       toggle fullscreen\n"
            "Esc     leave fullscreen\n"
            "S       silence (same as acknowledge)\n"
            "A       acknowledge\n"
            "R       request reset\n"
            "Ctrl+E  export the session timeline\n"
            "Q       quit\n"
            "F1      this help",
        )

    def export_session(self) -> None:
        default = str(
            REPO_ROOT
            / "experiments"
            / f"dashboard_session_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        )

        path, _ = QFileDialog.getSaveFileName(
            self, "Export session", default, "CSV (*.csv)"
        )

        if not path:
            return

        rows = self.timeline.row_texts()

        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["time_s", "source", "state", "event"])
            writer.writerows(rows)

        self._log("operator", f"Session exported to {path}", source="operator")

    def _on_max_runtime(self) -> None:
        self._log(
            "operator",
            "Maximum runtime reached; closing without touching the outputs",
            source="operator",
        )
        self.close()

    # -- shutdown -----------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Stop workers without silencing an alarm.

        No ``ALL_OFF`` and no ``RESET`` is sent. A latched incident must survive
        the dashboard closing: an operator who walks away from a red lamp should
        come back to a red lamp.
        """

        self.paint_timer.stop()
        self.poll_timer.stop()

        camera = self._camera
        self._camera = None

        if camera is not None and hasattr(camera, "stop"):
            camera.stop()

        serial = self._serial
        self._serial = None
        self._port = None

        # Guarded by duck typing, not by truthiness: a shutdown path that raises
        # would leave the real serial thread holding the port.
        if serial is not None and hasattr(serial, "stop"):
            serial.stop()

        self._log(
            "operator",
            "Session ended; outputs left exactly as the board reports them",
            source="operator",
        )

        super().closeEvent(event)
