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
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

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

from . import branding, theme
from .adapters import (
    STALE_STATUS_SECONDS,
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
from .widgets import Chip, EmptyState
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

#: Rows in the LIVE tab's event list. An operator watching a lamp needs the last
#: few things that happened, not three hundred; the full record is on the
#: analytics tab and in the export.
LIVE_TIMELINE_ROWS = 8


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


def alarm_intent_to_gpio(alarm: AlarmIntent) -> tuple[int, int, int]:
    """Map an :class:`AlarmIntent` to physical (green, red, buzzer) levels.

    Mirrors the firmware's IO11/IO12/IO14 semantics so the Blynk dashboard
    reports what the operator would actually see on the board. ``SILENCED``
    holds red=1 (buzzer acknowledged away), which is why ``ACKNOWLEDGED`` and
    ``SAFE_CLEAR_PENDING`` must keep showing red in the cloud.
    """

    return {
        AlarmIntent.SILENT: (1, 0, 0),
        AlarmIntent.WAITING: (0, 0, 0),
        AlarmIntent.SOUNDING: (0, 1, 1),
        AlarmIntent.SILENCED: (0, 1, 0),
        AlarmIntent.FAULT: (0, 0, 0),
    }[alarm]


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
        subtitle.setToolTip(branding.CONTEXT)
        centre.addWidget(subtitle)

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
        blynk_enabled: bool = True,
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

        # Skip any stale incident ids from a previous run so the first alarm
        # fires with a fresh id and a clean notification, instead of colliding
        # with ids the Blynk client already marked as "alerted".
        self.lifecycle.next_incident_id = 500

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

        #: Presentation mode is a view flag. It gates type size and the analytics
        #: tab and nothing else.
        self._presentation = False
        self.active_tab = theme.TAB_TITLES[0]

        # Firmware upload identity and thermal limits are recorded so the header
        # can be checked against the board without opening a terminal.
        self.weights = weights
        self.candidate_confidence = candidate_confidence
        self.goggles_confidence = goggles_confidence
        self.no_goggles_confidence = no_goggles_confidence
        self.iou = iou
        self.device = device

        # Blynk is a monitoring/notification-only bridge. It observes the
        # lifecycle and pushes V0-V10; it can never drive an output. Missing
        # or disabled config degrades to a chip that says so, never a crash.
        self._blynk: BlynkClient | None = None
        # Throttle marker for Blynk datastream pushes. ``_push_blynk`` is
        # called every frame (~30 FPS); pushing on every tick floods the cloud
        # and trips Blynk's anti-spam, but pushing only on state *transitions*
        # froze the dashboard widgets in steady state (SAFE). So we push the
        # datastreams at 1 Hz and let the client's own event anti-spam fire
        # ``eye_violation`` exactly once per incident.
        self._last_blynk_push_time: float = 0.0
        # ISO timestamp of the most recent ALARM_LATCHED transition. Tracked
        # here (GUI layer) rather than in the shared state machine so V6 stays
        # populated without touching incident_lifecycle.py.
        self._last_alert_iso: str | None = None
        if blynk_enabled and not demo_data:
            try:
                # use_thread=False so every push (and the eye_violation
                # logEvent) is sent synchronously on the GUI's own tick rather
                # than handed to a background worker whose timing is
                # independent of the Qt event loop. The event was firing in
                # the CLI because test_blynk.py called flush(); the GUI never
                # exits, so synchronous sends are the reliable choice here.
                self._blynk = BlynkClient.from_env(require=False, use_thread=False)
            except BlynkConfigError:
                self._blynk = None

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

        # The chip strip carries the true-but-not-urgent statements. They are
        # deliberately below the banner: a missing logo must never out-shout a
        # latched alarm, and a demo build has at least three of these.
        self.chip_strip = QWidget()
        chip_layout = QHBoxLayout(self.chip_strip)
        chip_layout.setContentsMargins(12, 4, 12, 0)
        chip_layout.setSpacing(6)

        self.mode_chip = Chip("")
        self.branding_chip = Chip("")
        self.blynk_chip = Chip("BLYNK NOT CONFIGURED", theme.TEXT_MUTED)
        self.yellow_chip = Chip("YELLOW LED NOT WIRED", theme.TEXT_MUTED)

        for chip in (
            self.mode_chip,
            self.branding_chip,
            self.blynk_chip,
            self.yellow_chip,
        ):
            chip_layout.addWidget(chip)

        chip_layout.addStretch(1)
        root.addWidget(self.chip_strip)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("MainTabs")
        self.tabs.setDocumentMode(True)
        root.addWidget(self.tabs, 1)

        self.live_tab = self._build_live_tab()
        self.analytics_tab = self._build_analytics_tab()

        self.tabs.addTab(self.live_tab, theme.TAB_TITLES[0])
        self.tabs.addTab(self.analytics_tab, theme.TAB_TITLES[1])
        self.tabs.setTabToolTip(0, theme.TAB_SUBTITLES[theme.TAB_LIVE])
        self.tabs.setTabToolTip(1, theme.TAB_SUBTITLES[theme.TAB_ANALYTICS])
        self.tabs.currentChanged.connect(self._on_tab_changed)

        root.addWidget(self._build_footer())

        self.status_label = QLabel("Starting\u2026")
        self.statusBar().addWidget(self.status_label, 1)

    # -- tabs ---------------------------------------------------------------

    def _tab_subtitle(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("TabSubtitle")
        label.setWordWrap(True)
        return label

    def _build_live_tab(self) -> QWidget:
        """The screen that stays open during a demo. No vertical scrolling.

        Proportions follow the brief: camera roughly half the width, verdict and
        incident about a quarter, hardware and health the rest. Everything here
        is what an operator needs while watching a lamp; everything that is only
        interesting afterwards lives on the analytics tab.
        """

        page = QWidget()
        page.setObjectName("TabPage")

        shell = QVBoxLayout(page)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        # The live page is designed to fit without scrolling at 1080p and above.
        # Below that it is genuinely too tall to compress further, and a clipped
        # reset gate is worse than a scroll bar, so the scroll area is a safety
        # valve rather than the primary layout.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("LiveScroll")
        shell.addWidget(scroll)

        page_body = QWidget()
        page_body.setObjectName("Content")
        # The page's minimum is 835 px, comfortably under a 1080p viewport, but
        # word-wrapped captions make its height-for-width land 30 px higher and
        # that is what the scroll area honours. Ignoring the vertical hint lets
        # the layout settle on the minimum instead of the padded hint.
        page_body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)
        scroll.setWidget(page_body)

        outer = QVBoxLayout(page_body)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(8)

        outer.addWidget(
            self._tab_subtitle(theme.TAB_SUBTITLES[theme.TAB_LIVE])
        )

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        # Row 0: camera | verdict + incident | hardware
        self.camera_panel = CameraPanel()
        self.camera_panel.setMinimumWidth(420)
        grid.addWidget(self.camera_panel, 0, 0)

        middle = QVBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(10)

        self.safety_panel = SafetyStatePanel()
        middle.addWidget(self.safety_panel, 3)

        self.incident_panel = IncidentPanel()
        middle.addWidget(self.incident_panel, 2)

        middle_host = QWidget()
        middle_host.setLayout(middle)
        grid.addWidget(middle_host, 0, 1)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(10)

        self.hardware_panel = HardwarePanel()
        right.addWidget(self.hardware_panel, 3)

        self.health_panel = HealthPanel()
        right.addWidget(self.health_panel, 2)

        right_host = QWidget()
        right_host.setLayout(right)
        grid.addWidget(right_host, 0, 2)

        # Row 1: operator controls | live events | compact flow
        self.control_panel = ControlPanel()
        self.control_panel.acknowledge_requested.connect(self.request_acknowledge)
        self.control_panel.silence_requested.connect(self.request_acknowledge)
        self.control_panel.reset_requested.connect(self.request_reset)
        self.control_panel.export_requested.connect(self.export_session)
        self.control_panel.status_requested.connect(self.request_status)
        self.control_panel.help_requested.connect(self.show_help)
        grid.addWidget(self.control_panel, 1, 0)

        self.live_timeline = EventTimeline(
            title="Recent events",
            display_limit=LIVE_TIMELINE_ROWS,
        )
        self.live_timeline.setMinimumHeight(180)
        grid.addWidget(self.live_timeline, 1, 1)

        flow_card = QFrame()
        flow_card.setObjectName("Card")
        flow_layout = QVBoxLayout(flow_card)
        flow_layout.setContentsMargins(14, 10, 14, 10)
        flow_layout.setSpacing(6)

        flow_title = QLabel("SIGNAL FLOW")
        flow_title.setObjectName("CardTitle")
        flow_layout.addWidget(flow_title)

        self.signal_flow = SignalFlowWidget()
        flow_layout.addWidget(self.signal_flow)

        self.live_flow_note = QLabel("")
        self.live_flow_note.setObjectName("CardCaption")
        self.live_flow_note.setWordWrap(True)
        flow_layout.addWidget(self.live_flow_note)
        flow_layout.addStretch(1)

        grid.addWidget(flow_card, 1, 2)

        # Camera gets the wide column; verdict and hardware share the rest.
        grid.setColumnStretch(0, 5)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(2, 2)
        grid.setRowStretch(0, 3)
        grid.setRowStretch(1, 2)

        outer.addLayout(grid, 1)

        return page

    def _build_analytics_tab(self) -> QWidget:
        """Evidence and history. Scrollable, because it is read, not watched."""

        page = QWidget()
        page.setObjectName("TabPage")

        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        content.setObjectName("Content")
        scroll.setWidget(content)

        grid = QGridLayout(content)
        grid.setContentsMargins(12, 8, 12, 12)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        grid.addWidget(
            self._tab_subtitle(theme.TAB_SUBTITLES[theme.TAB_ANALYTICS]), 0, 0, 1, 3
        )

        # The evidence tab shows the same feed as LIVE OPERATIONS: an operator
        # reviewing an incident should not have to switch tabs to confirm what
        # the model saw. Both CameraPanels are independent widgets fed from the
        # same frame callback.
        evidence_columns = QVBoxLayout()
        evidence_columns.setContentsMargins(0, 0, 0, 0)
        evidence_columns.setSpacing(6)

        self.evidence_camera = CameraPanel()
        evidence_columns.addWidget(self.evidence_camera, 1)

        evidence_caption = QLabel(
            "Same frames as LIVE OPERATIONS, kept beside the evidence."
        )
        evidence_caption.setObjectName("CardCaption")
        evidence_caption.setWordWrap(True)
        evidence_columns.addWidget(evidence_caption)

        evidence_host = QWidget()
        evidence_host.setLayout(evidence_columns)
        grid.addWidget(evidence_host, 1, 0, 2, 1)

        self.charts_panel = ChartsPanel()
        grid.addWidget(self.charts_panel, 1, 1, 1, 2)

        self.metrics_panel = MetricsPanel()
        grid.addWidget(self.metrics_panel, 2, 1, 1, 2)

        self.timeline = EventTimeline(title="Full event record")
        self.timeline.setMinimumHeight(230)
        grid.addWidget(self.timeline, 3, 0, 1, 3)

        diagnostics_card = QFrame()
        diagnostics_card.setObjectName("Card")
        diagnostics_layout = QVBoxLayout(diagnostics_card)
        diagnostics_layout.setContentsMargins(14, 10, 14, 10)
        diagnostics_layout.setSpacing(6)

        diagnostics_title = QLabel("DIAGNOSTICS \u2014 RAW SERIAL TRAFFIC")
        diagnostics_title.setObjectName("CardTitle")
        diagnostics_layout.addWidget(diagnostics_title)

        diagnostics_note = QLabel(
            "Every STATUS line, HEARTBEAT and boot banner, unfiltered. The same "
            "record the CSV export writes in full."
        )
        diagnostics_note.setObjectName("CardCaption")
        diagnostics_note.setWordWrap(True)
        diagnostics_layout.addWidget(diagnostics_note)

        self.diagnostics_table = EventTimeline(title="Raw record")
        self.diagnostics_table.setMinimumHeight(180)
        self.diagnostics_table.show_raw(True)
        diagnostics_layout.addWidget(self.diagnostics_table, 1)

        grid.addWidget(diagnostics_card, 4, 0, 1, 3)

        grid.setColumnStretch(0, 4)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(2, 3)
        grid.setRowStretch(1, 4)
        grid.setRowStretch(2, 3)
        grid.setRowStretch(3, 3)

        return page

    def _build_footer(self) -> QWidget:
        """One compact persistent line: mode, board, model, help hint."""

        footer = QWidget()
        footer.setObjectName("Footer")

        layout = QHBoxLayout(footer)
        layout.setContentsMargins(12, 3, 12, 3)
        layout.setSpacing(10)

        self.footer_label = QLabel("")
        self.footer_label.setObjectName("Footer")
        self.footer_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self.footer_label, 1)

        self.presentation_hint = QLabel("P presentation  \u00b7  F1 help")
        self.presentation_hint.setObjectName("Footer")
        layout.addWidget(self.presentation_hint)

        return footer

    def _on_tab_changed(self, index: int) -> None:
        """Record which tab is on screen.

        Nothing is replayed and no worker is touched: every timeline is fed from
        ``_log`` as events happen, so a tab that was hidden simply shows the
        record already accumulated. Switching tabs is a paint operation only.
        """

        self.active_tab = theme.TAB_TITLES[0] if index == 0 else theme.TAB_TITLES[1]

    # -- presentation mode --------------------------------------------------

    @property
    def presentation(self) -> bool:
        return self._presentation

    def toggle_presentation(self) -> None:
        """Enlarge type and hide the analytics tab. Esc leaves it.

        Presentation mode is a view change and nothing else: it never touches the
        lifecycle, the correlator or either worker, so toggling it mid-incident is
        safe.
        """

        self.set_presentation(not self._presentation)

    def set_presentation(self, presentation: bool) -> None:
        self._presentation = bool(presentation)

        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.stylesheet(presentation=self._presentation))

        self.safety_panel.set_presentation(self._presentation)

        index = self.tabs.indexOf(self.analytics_tab)
        self.tabs.setTabVisible(index, not self._presentation)

        # With the analytics tab hidden there is a single page left, so the tab
        # bar would be a lone tab that consumes height the live layout needs.
        # Hiding the bar - not just the tab - is what buys back the room the
        # enlarged type costs.
        self.tabs.tabBar().setVisible(not self._presentation)

        if self._presentation:
            self.tabs.setCurrentIndex(0)
            self.showFullScreen()
        else:
            self.leave_fullscreen()

        self.presentation_hint.setText(
            "PRESENTATION MODE  \u00b7  Esc restores  \u00b7  P toggles"
            if self._presentation
            else "P presentation  \u00b7  F1 help"
        )

        self._refresh()

    def _install_shortcuts(self) -> None:
        bindings = (
            ("F", self.toggle_fullscreen),
            ("Escape", self.leave_presentation_or_fullscreen),
            ("S", self.request_acknowledge),
            ("A", self.request_acknowledge),
            ("R", self.request_reset),
            ("Q", self.close),
            ("Ctrl+E", self.export_session),
            ("F1", self.show_help),
            ("P", self.toggle_presentation),
            ("T", self.request_status),
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
        self.evidence_camera.show_frame(frame)

        peak = max((box[1] for box in frame.detections), default=0.0)

        # Per-class peaks come from what the model actually produced, before the
        # per-class thresholds were applied. Reading them from ``detections``
        # instead would hide every class sitting in the band between the
        # candidate threshold and its own one - which is exactly the band the
        # operator needs to see when tuning. A class with no box at all stays
        # ``None`` so the chart shows a gap rather than a fake zero.
        goggles_conf: float | None = None
        no_goggles_conf: float | None = None

        for label, confidence in frame.class_scores or frame.detections:
            name = str(label).strip().lower()

            if name in {"goggles", "with_goggles", "safe", "helmet"}:
                goggles_conf = max(goggles_conf or 0.0, confidence)
            elif name in {"no_goggles", "nogoggles", "no-goggles", "violation"}:
                no_goggles_conf = max(no_goggles_conf or 0.0, confidence)

        self.charts_panel.push_telemetry(
            fps=frame.fps,
            inference_ms=frame.inference_ms,
            frame_age_ms=max(0.0, (now - frame.at) * 1000.0),
            max_confidence=peak,
            goggles_confidence=goggles_conf,
            no_goggles_confidence=no_goggles_conf,
        )

        observation = getattr(self.Observation, str(frame.observation), None)

        if observation is None:
            # An unrecognised verdict is missing information, and missing
            # information must never be promoted to SAFE.
            observation = self.Observation.UNKNOWN

        self._advance(
            observation,
            now,
            kind=str(frame.observation),
            fps=frame.fps,
            top_confidence=peak,
        )

    def _advance(
        self,
        observation,
        now: float,
        *,
        kind: str,
        fps: float | None = None,
        top_confidence: float | None = None,
    ) -> None:
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

        # Record the wall-clock moment the alarm first latched so V6 (Last
        # Alert Time) carries a real ISO timestamp in the cloud.
        if status.state is OperatorState.ALARM_LATCHED and status.incident_id:
            self._last_alert_iso = datetime.now(timezone.utc).isoformat()

        self._push_blynk(
            status,
            fps=fps,
            top_confidence=top_confidence,
            last_alert_time=self._last_alert_iso,
        )

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

    def _push_blynk(
        self,
        status,
        *,
        fps: float | None = None,
        top_confidence: float | None = None,
        last_alert_time: str | None = None,
    ) -> None:
        """Push the lifecycle state to Blynk as a monitoring-only reading.

        Builds a :class:`LifecycleReading` from the physical GPIO mirror so the
        cloud shows the red LED latched through ACKNOWLEDGED and
        SAFE_CLEAR_PENDING, exactly as the board holds it. The client enforces
        the one-event-per-incident anti-spam rule and is fully non-blocking.
        """

        client = self._blynk

        if client is None or not client.enabled:
            return

        state_value = status.state.value
        incident_id = status.incident_id

        # Time-based throttling: refresh the datastreams at 1 Hz rather than
        # every frame (30 FPS) or only on transitions. The latter froze the
        # dashboard widgets in steady state; the former floods Blynk and trips
        # its anti-spam. At 1 Hz the datastreams stay live while the client's
        # own event anti-spam still fires ``eye_violation`` exactly once per
        # incident id.
        now = time.monotonic()
        if now - self._last_blynk_push_time < 1.0:
            return

        self._last_blynk_push_time = now

        # On a clean return to SAFE, forget every already-alerted incident id
        # so the next violation fires a fresh notification even if the
        # lifecycle happens to reuse a recent id. Without this, the client's
        # one-per-incident anti-spam would suppress the next real alarm.
        if status.state is OperatorState.SAFE:
            client.clear_alerts()

        green, red, buzzer = alarm_intent_to_gpio(status.alarm)
        reading = LifecycleReading(
            state=state_value,
            incident_id=incident_id,
            violation_ratio=status.violation_ratio,
            link_online=bool(self._serial_ok),
            incident_count=max(0, self.lifecycle.next_incident_id - 1),
            top_confidence=top_confidence,
            fps=fps,
            green=green,
            red=red,
            buzzer=buzzer,
        )

        # Diagnostic line so the live GUI run shows exactly what is (or is not)
        # being sent each tick. An incident_id of None/0 here means the alarm
        # fired before the lifecycle allocated an id, and the event is skipped
        # by the anti-spam guard.
        if status.state is OperatorState.ALARM_LATCHED or reading.incident_id:
            print(
                f"DEBUG: Sending Blynk. State: {status.state.value}, "
                f"ID: {reading.incident_id}"
            )

        try:
            results = client.push_status(reading, last_alert_time=last_alert_time)
        except Exception as exc:  # noqa: BLE001 - cloud must never break the loop
            # Surface the real error instead of swallowing it, so a bad token
            # or a timeout is visible in the terminal rather than invisible.
            import traceback

            print(f"DEBUG: Blynk push raised {type(exc).__name__}: {exc}")
            traceback.print_exc()
            self._log(
                "blynk",
                f"Blynk push failed ({type(exc).__name__}: {exc}); "
                "continuing without cloud",
                source="blynk",
            )
            return

        # Log the event outcome explicitly so the operator can see whether the
        # eye_violation event actually fired (vs being suppressed as a duplicate
        # or by cooldown) without digging through the client internals.
        event = results.get("event")
        if event is not None:
            print(
                f"DEBUG: Blynk event result: status={event.status.value}, "
                f"detail={event.detail}, http={event.http_status}, "
                f"msg={event.message!r}"
            )

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
        self._update_chips(snapshot)
        self._update_footer(snapshot)
        self._update_status_bar(snapshot)

        if self._fixture is not None:
            self._render_fixture_camera()

    def _update_chips(self, snapshot: DashboardSnapshot) -> None:
        """Populate the secondary warning strip.

        Everything here is true and none of it is urgent, which is exactly why it
        is a chip and not a banner. A chip that has nothing to say hides itself.
        """

        self.mode_chip.setText("DEMO DATA" if self.demo_data else "LIVE")
        self.mode_chip.set_colour(theme.AMBER if self.demo_data else theme.GREEN)

        missing = branding.missing_logos()
        if missing:
            self.branding_chip.setText(
                "LOGO MISSING: " + ", ".join(missing)
            )
            self.branding_chip.set_colour(theme.AMBER)
            self.branding_chip.setVisible(True)
        else:
            self.branding_chip.setVisible(False)

        self.blynk_chip.setVisible(not self.demo_data)
        self.yellow_chip.setVisible(not self.demo_data)

        if self.demo_data:
            return

        if self._blynk is not None and self._blynk.enabled:
            self.blynk_chip.set_chip("BLYNK ONLINE", theme.GREEN)
        else:
            self.blynk_chip.set_chip("BLYNK DISABLED", theme.TEXT_MUTED)

    def _update_footer(self, snapshot: DashboardSnapshot) -> None:
        board = snapshot.board
        model = self.weights.split("/")[-1]

        self.footer_label.setText(
            f"{self.active_tab}"
            f"   \u00b7   {snapshot.state.value}"
            f"   \u00b7   board {board.link.value}"
            f"   \u00b7   {board.port or 'no port'}"
            f"   \u00b7   model {model}"
            f"   \u00b7   {snapshot.counters.frames} frames"
        )

    def _render_fixture_camera(self) -> None:
        """Paint the labelled demo fixture instead of a webcam frame.

        Deterministic in the step index and unmistakably synthetic: a fake feed
        that looks real is the one thing a safety dashboard must never show.
        """

        driver = self._fixture

        if driver is None:
            return

        status = self.lifecycle.status()

        for panel in (self.camera_panel, self.evidence_camera):
            panel.show_fixture(
                driver.step_index,
                observation=str(
                    getattr(driver.current_observation, "value", "UNKNOWN")
                ),
                detections=1 if status.state is OperatorState.ALARM_LATCHED else 0,
            )

    def _health(self):
        return build_health_items(
            camera_ok=None if self.demo_data else self._camera_ok,
            model_ok=None if self.demo_data else self._model_ok,
            serial_ok=None if self.demo_data else self._serial_ok,
            serial_detail=self._serial_detail,
            status_age_seconds=self._status_age,
            demo_data=self.demo_data,
            missing_logo=bool(branding.missing_logos()),
            blynk_ok=self._blynk_status(),
        )

    def _blynk_status(self) -> bool | None:
        """Health status of the Blynk bridge for the health panel.

        ``None`` means "not applicable" (demo data or no config); ``True``
        means enabled and pushing; ``False`` means explicitly disabled.
        """

        if self.demo_data:
            return None

        client = self._blynk

        if client is None:
            return None

        return bool(client.enabled)

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
            # The driver's entries already carry their own timestamp and state,
            # so they bypass _log's re-derivation and go straight to the views.
            self._record(entry)

        self._fixture_timeline_index = len(driver.timeline)

        if driver.sent_commands:
            self._last_requested = driver.sent_commands[-1]

    def _update_signal_flow(self, snapshot: DashboardSnapshot) -> None:
        serial_up = snapshot.board.link is LinkState.OPEN

        firmware_up = bool(snapshot.board.firmware_version)
        outputs = snapshot.board.outputs

        live = {
            "camera": self._camera_ok,
            "detector": self._model_ok,
            "filter": snapshot.counters.frames > 0,
            "lifecycle": snapshot.state is not OperatorState.FAULT,
            "serial": serial_up,
            "esp32": firmware_up,
            "outputs": outputs.reported,
        }

        break_at = ""

        for key in ("camera", "detector", "filter", "lifecycle", "serial", "esp32", "outputs"):
            if not live[key]:
                break_at = key
                break

        notes = {
            "camera": "frames" if live["camera"] else "no frames",
            "detector": "ok" if live["detector"] else "not loaded",
            "filter": f"{snapshot.counters.frames} frames" if live["filter"] else "idle",
            "lifecycle": snapshot.state.value if live["lifecycle"] else "fault",
            "serial": "open" if live["serial"] else "closed",
            "esp32": snapshot.board.firmware_version or "no STATUS",
            "outputs": "reported" if live["outputs"] else "unknown",
        }

        self.signal_flow.set_flow(live, break_at=break_at, notes=notes)

        if hasattr(self, "live_flow_note"):
            self.live_flow_note.setText(self.signal_flow.summary_line())

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

        # One banner, ordered by consequence. A fault parks the outputs; a dead
        # serial link means the board never hears anything; a dead camera means
        # no verdict is ever produced. Everything below that is advisory, and an
        # advisory must never overwrite an urgent line - so the first matching
        # band wins and lower bands are not appended to it.
        status = self.lifecycle.status()

        if status.alarm.value == "FAULT" or status.state.value == "FAULT":
            detail = status.fault_reason or "a safety input was lost"
            self.banner.show_banner(
                f"FAULT \u00b7 {detail} \u00b7 outputs parked", theme.MAGENTA
            )
            return

        if not self.serial_port:
            self.banner.show_banner(
                "NO SERIAL PORT \u00b7 this window cannot drive the board", theme.RED
            )
            return

        if self._serial_ok is False:
            self.banner.show_banner(
                "SERIAL LINK DOWN \u00b7 the board is not receiving commands", theme.RED
            )
            return

        if not self._camera_ok:
            self.banner.show_banner(
                "CAMERA UNAVAILABLE \u00b7 no verdicts are being produced", theme.RED
            )
            return

        age = self._status_age
        if age is not None and age > STALE_STATUS_SECONDS:
            self.banner.show_banner(
                f"FIRMWARE STATUS STALE \u00b7 last reply {age:.0f}s ago "
                "\u00b7 lamp state is unverified",
                theme.ORANGE,
            )
            return

        # Advisory band: presentation-only warnings, shown only when nothing
        # safety-relevant is wrong.
        advisory: list[str] = []

        if not self.metrics_panel.table.available:
            advisory.append("model metrics unavailable")

        if branding.missing_logos():
            advisory.append(
                "branding logo missing: " + ", ".join(branding.missing_logos())
            )

        if advisory:
            self.banner.show_banner(
                "   \u00b7   ".join(advisory).upper(), theme.AMBER
            )
        else:
            self.banner.hide_banner()

    def _record(self, entry: TimelineEntry) -> None:
        """Fan one already-built entry out to every timeline view."""

        self.timeline.add(entry)
        self.live_timeline.add(entry)
        self.diagnostics_table.add(entry)

    def _log(self, kind: str, message: str, *, source: str = "lifecycle") -> None:
        """Record one event in every timeline view.

        All three views are fed from here rather than on tab switch, because a
        view rebuilt on demand would show a gap for anything that happened while
        it was hidden - and the analytics tab is normally hidden.
        """

        status = self.lifecycle.status()

        self._record(
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

    def leave_presentation_or_fullscreen(self) -> None:
        """Esc steps out of presentation mode first, then out of fullscreen.

        One key for both would mean a presenter has to press Esc twice with no
        indication that the first press did anything.
        """

        if self._presentation:
            self.set_presentation(False)
            return

        self.leave_fullscreen()

    def request_status(self) -> None:
        """Ask the board for STATUS now, without claiming the correlation slot."""

        if self._serial is None:
            self._log(
                "operator",
                "Status request ignored: no serial port open",
                source="operator",
            )
            return

        self._serial.worker.send("STATUS")
        self._log("operator", "Status requested", source="operator")

    def show_help(self) -> None:
        QMessageBox.information(
            self,
            "Keyboard shortcuts",
            "F       toggle fullscreen\n"
            "P       presentation mode (larger type, analytics hidden)\n"
            "Esc     leave presentation mode or fullscreen\n"
            "S       silence (same as acknowledge)\n"
            "A       acknowledge\n"
            "R       request reset\n"
            "T       request a board STATUS poll\n"
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

        if self._blynk is not None:
            try:
                self._blynk.close()
            except Exception:  # noqa: BLE001 - shutdown must never raise
                pass
            self._blynk = None

        self._log(
            "operator",
            "Session ended; outputs left exactly as the board reports them",
            source="operator",
        )

        super().closeEvent(event)
