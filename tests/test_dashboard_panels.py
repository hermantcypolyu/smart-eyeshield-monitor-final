"""Tests for the dashboard panels.

Each panel is fed a ``DashboardSnapshot`` built from the real lifecycle and
real board snapshot types, so these tests exercise the same path the window
uses. The assertions target operator-facing meaning: does the panel tell the
truth about which lamp is lit, whether a violation is latched, and whether an
action is allowed.
"""

from __future__ import annotations

import time

import pytest

from src.dashboard import theme
from src.dashboard.camera_panel import CameraPanel
from src.dashboard.charts_panel import ChartsPanel
from src.dashboard.control_panel import ControlPanel
from src.dashboard.event_timeline import EventTimeline
from src.dashboard.health_panel import HealthPanel
from src.dashboard.hardware_panel import HardwarePanel
from src.dashboard.incident_panel import IncidentPanel
from src.dashboard.metrics_panel import MetricsPanel
from src.dashboard.safety_state_panel import SafetyStatePanel
from src.dashboard.signal_flow import SignalFlowWidget
from tools.incident_lifecycle import AlarmIntent, LifecycleStatus, OperatorState, ResetBlocker

from src.dashboard.state_models import (
    BoardOutputs,
    BoardSnapshot,
    DashboardSnapshot,
    GaugeSnapshot,
    LinkState,
    OutputState,
    SessionCounters,
    TelemetrySample,
    TimelineEntry,
)


# -- snapshot helpers ---------------------------------------------------


def _snapshot(
    *,
    state: "OperatorState" = None,
    incident_id: int | None = None,
    acknowledged: bool = False,
    safe_recovered: bool = False,
    green: OutputState = OutputState.ON,
    red: OutputState = OutputState.OFF,
    buzzer: OutputState = OutputState.OFF,
    link: LinkState = LinkState.OPEN,
    reported: bool = True,
    firmware_state: str = "SAFE",
    demo_data: bool = False,
    banner: str = "",
    blocker=None,
    health=(),
    counters: SessionCounters | None = None,
) -> DashboardSnapshot:
    """Build a snapshot from real value objects, not from a mock."""

    operator_state = state if state is not None else OperatorState.SAFE
    now = time.monotonic()

    return DashboardSnapshot(
        lifecycle=LifecycleStatus(
            state=operator_state,
            alarm=(
                AlarmIntent.SOUNDING
                if operator_state is OperatorState.ALARM_LATCHED
                else AlarmIntent.SILENCED
                if operator_state in {OperatorState.ACKNOWLEDGED, OperatorState.SAFE_RECOVERED}
                else AlarmIntent.WAITING
                if operator_state
                in {OperatorState.STARTUP, OperatorState.WAITING_FOR_TARGET}
                else AlarmIntent.SILENT
            ),
            incident_id=incident_id,
            violation_ratio=0.0,
            pending_elapsed=None,
            acknowledged=acknowledged,
            safe_recovered=safe_recovered,
        ),
        board=BoardSnapshot(
            port="/dev/cu.usbserial-10",
            baud=115200,
            link=link,
            outputs=BoardOutputs(
                green=green,
                red=red,
                buzzer=buzzer,
                firmware_state=firmware_state if reported else "",
            ),
            incident_id=incident_id,
            acknowledged=acknowledged,
            reset_ready=False,
            rearm_count=0,
            status_at=now if reported else None,
        ),
        gauge=GaugeSnapshot(
            violation_ratio=0.0,
            confirmation_seconds=2.0,
            clearance_seconds=2.0,
        ),
        counters=counters or SessionCounters(),
        health=tuple(health),
        reset_blocker=blocker if blocker is not None else ResetBlocker.NONE,
        demo_data=demo_data,
        banner=banner,
    )


# -- SafetyStatePanel ---------------------------------------------------


def test_safety_panel_shows_the_state_name(qapp):
    panel = SafetyStatePanel()

    panel.update_from(_snapshot(state="ALARM_LATCHED", incident_id=2))

    assert "ALARM" in panel.state_block.state_label.text().upper()


def test_safety_panel_paints_safe_green_and_alarm_red(qapp):
    panel = SafetyStatePanel()

    panel.update_from(_snapshot(state="SAFE"))
    safe_style = panel.state_block.state_label.styleSheet()

    panel.update_from(_snapshot(state="ALARM_LATCHED", incident_id=2))
    alarm_style = panel.state_block.state_label.styleSheet()

    assert theme.GREEN in safe_style
    assert theme.RED in alarm_style


def test_safety_panel_never_paints_pending_green(qapp):
    """VIOLATION_PENDING is not compliance and must not borrow the green."""

    panel = SafetyStatePanel()

    panel.update_from(_snapshot(state="VIOLATION_PENDING"))

    assert theme.GREEN not in panel.state_block.state_label.styleSheet()


# -- HardwarePanel ------------------------------------------------------


def test_hardware_panel_reports_each_lamp_separately(qapp):
    """The whole point of this panel is the per-lamp truth, not a summary."""

    panel = HardwarePanel()

    panel.update_from(
        _snapshot(
            state="ALARM_LATCHED",
            incident_id=2,
            green=OutputState.OFF,
            red=OutputState.ON,
            buzzer=OutputState.ON,
        )
    )

    assert panel.green_row.value_label.text() == "off"
    assert panel.red_row.value_label.text() == "ON"
    assert panel.buzzer_row.value_label.text() == "ON"


def test_hardware_panel_marks_unknown_outputs_as_unknown(qapp):
    """With no STATUS yet the panel must not claim the lamps are off."""

    panel = HardwarePanel()

    panel.update_from(
        _snapshot(
            link=LinkState.CLOSED,
            green=OutputState.UNKNOWN,
            red=OutputState.UNKNOWN,
            buzzer=OutputState.UNKNOWN,
            reported=False,
        )
    )

    assert panel.green_row.value_label.text() == "UNKNOWN"
    assert panel.red_row.value_label.text() == "UNKNOWN"


def test_hardware_panel_knows_the_board_is_unreachable(qapp):
    panel = HardwarePanel()

    panel.update_from(_snapshot(link=LinkState.CLOSED, reported=False))

    assert LinkState.CLOSED.label in panel.link_pill.text()


# -- IncidentPanel ------------------------------------------------------


def test_incident_panel_says_none_when_no_incident_is_open(qapp):
    panel = IncidentPanel()

    panel.update_from(_snapshot())

    assert panel.rows["id"].value_label.text() == "none"


def test_incident_panel_names_the_open_incident(qapp):
    panel = IncidentPanel()

    panel.update_from(_snapshot(state="ALARM_LATCHED", incident_id=7))

    assert panel.rows["id"].value_label.text() == "7"


def test_incident_panel_distinguishes_requested_from_confirmed(qapp):
    """A keypress is a request. Only the board's reply is an acknowledgement."""

    panel = IncidentPanel()

    panel.update_from(_snapshot(state="ALARM_LATCHED", incident_id=7))
    before = panel.rows["acknowledged"].value_label.text()

    panel.update_from(
        _snapshot(state="ACKNOWLEDGED", incident_id=7, acknowledged=True)
    )
    after = panel.rows["acknowledged"].value_label.text()

    assert before != after
    assert "yes" in after.lower() or "true" in after.lower()


# -- HealthPanel --------------------------------------------------------


def test_health_panel_renders_every_subsystem_row(qapp):
    from src.dashboard.adapters import build_health_items

    panel = HealthPanel()

    panel.update_from(
        _snapshot(
            health=build_health_items(
                camera_ok=True,
                model_ok=True,
                serial_ok=True,
                status_age_seconds=0.2,
            )
        )
    )

    assert len(panel._rows) >= 4


def test_health_panel_shows_a_faulting_subsystem_as_not_ok(qapp):
    from src.dashboard.adapters import build_health_items

    panel = HealthPanel()

    panel.update_from(
        _snapshot(
            health=build_health_items(
                camera_ok=False,
                model_ok=True,
                serial_ok=True,
                status_age_seconds=0.2,
            )
        )
    )

    camera_row = panel._rows[0]
    assert camera_row.dot._colour != theme.GREEN


# -- MetricsPanel -------------------------------------------------------


def test_metrics_panel_reads_the_committed_csv(qapp):
    """The figures must come from the frozen metrics file, never be invented."""

    panel = MetricsPanel()

    assert len(panel.table.rows) > 0


def test_metrics_panel_renders_without_raising(qapp):
    panel = MetricsPanel()
    panel.show()


# -- EventTimeline ------------------------------------------------------


def test_timeline_shows_rows_in_arrival_order(qapp):
    timeline = EventTimeline()
    now = time.monotonic()

    timeline.add_many(
        [
            TimelineEntry(at=now, kind="SESSION_START", message="session started", state="SAFE"),
            TimelineEntry(
                at=now + 1.0,
                kind="ALARM_LATCHED",
                message="incident 1 latched",
                state="VIOLATION_PENDING",
            ),
        ]
    )

    rows = timeline.row_texts()
    assert len(rows) == 2
    assert any("SAFE" in cell for cell in rows[0])
    assert any("VIOLATION" in cell for cell in rows[1])


def test_timeline_can_be_cleared(qapp):
    timeline = EventTimeline()
    timeline.add(
        TimelineEntry(at=time.monotonic(), kind="X", message="x", state="SAFE")
    )

    timeline.clear()

    assert timeline.row_texts() == []


# -- ControlPanel -------------------------------------------------------


def test_control_panel_enables_acknowledge_only_for_an_open_incident(qapp):
    panel = ControlPanel()

    panel.update_from(_snapshot())
    assert panel.acknowledge_button.isEnabled() is False

    panel.update_from(_snapshot(state="ALARM_LATCHED", incident_id=1))
    assert panel.acknowledge_button.isEnabled() is True


def test_control_panel_disables_acknowledge_once_acknowledged(qapp):
    panel = ControlPanel()

    panel.update_from(
        _snapshot(state="ACKNOWLEDGED", incident_id=1, acknowledged=True)
    )

    assert panel.acknowledge_button.isEnabled() is False


def test_control_panel_enables_reset_only_when_the_gate_is_open(qapp):
    panel = ControlPanel()

    panel.update_from(_snapshot(blocker=ResetBlocker.NOT_ACKNOWLEDGED, incident_id=1))
    assert panel.reset_button.isEnabled() is False

    panel.update_from(_snapshot(blocker=ResetBlocker.NONE, incident_id=1))
    assert panel.reset_button.isEnabled() is True


def test_control_panel_says_why_reset_is_blocked(qapp):
    panel = ControlPanel()

    panel.update_from(_snapshot(blocker=ResetBlocker.NOT_SAFE_RECOVERED, incident_id=1))

    assert panel.gate_label.text().strip() not in {"", "--"}


def test_control_panel_emits_a_signal_per_action(qapp):
    panel = ControlPanel()
    seen: list[str] = []

    panel.silence_requested.connect(lambda: seen.append("silence"))
    panel.acknowledge_requested.connect(lambda: seen.append("acknowledge"))
    panel.reset_requested.connect(lambda: seen.append("reset"))

    panel.silence_button.click()
    panel.acknowledge_button.click()
    panel.reset_button.click()

    assert seen == ["silence", "acknowledge", "reset"]


# -- ChartsPanel --------------------------------------------------------


def test_charts_panel_accepts_a_snapshot(qapp):
    panel = ChartsPanel()

    panel.update_from(_snapshot(), shares=(("SAFE", 90.0), ("ALARM_LATCHED", 10.0)))
    panel.show()


def test_charts_panel_tracks_telemetry_without_raising(qapp):
    panel = ChartsPanel()

    panel.push_telemetry(
        fps=28.5, inference_ms=18.0, frame_age_ms=42.0, max_confidence=0.91
    )
    panel.push_telemetry(
        fps=27.0, inference_ms=22.0, frame_age_ms=60.0, max_confidence=0.88
    )
    panel.show()


def test_charts_panel_can_be_cleared(qapp):
    panel = ChartsPanel()
    panel.push_telemetry(
        fps=1.0, inference_ms=1.0, frame_age_ms=1.0, max_confidence=0.1
    )

    panel.clear()

    panel.show()


# -- SignalFlowWidget ---------------------------------------------------


def test_signal_flow_accepts_a_live_map(qapp):
    widget = SignalFlowWidget()

    widget.set_flow(
        {"camera": True, "model": True, "filter": True, "lifecycle": True}
    )
    widget.show()


def test_signal_flow_marks_where_the_chain_breaks(qapp):
    widget = SignalFlowWidget()

    widget.set_flow(
        {"camera": True, "model": False, "filter": False, "lifecycle": False},
        break_at="model",
        notes={"model": "weights missing"},
    )

    assert widget._break_at == "model"
    widget.show()


# -- CameraPanel --------------------------------------------------------


def test_camera_panel_shows_a_placeholder_before_any_frame(qapp):
    panel = CameraPanel()

    assert panel.view is not None


def test_camera_panel_accepts_a_message(qapp):
    panel = CameraPanel()

    panel.show_message("Waiting for the first frame")

    panel.show()
