"""The window's safety-critical wiring.

These tests target the behaviours that would silently misinform an operator:

* the confirmation timer must not reset every frame;
* a reply must only ever confirm the command it answers;
* no exit path may silence a latched alarm;
* closing must not send ``ALL_OFF`` or ``RESET``.
"""

from __future__ import annotations

import time

import pytest

from src.dashboard import theme
from tools.incident_lifecycle import OperatorState


class FakePort:
    """Records what the correlator wrote, and answers like the board does."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, data: bytes) -> int:
        self.written.append(data.decode("utf-8", errors="replace").strip())
        return len(data)

    def flush(self) -> None:
        return None


def test_live_window_starts_in_a_non_compliant_state(live_window):
    """Nothing may render as SAFE before a verdict has arrived.

    A demo window is allowed to start mid-scenario; a live one is not, because
    a green lamp with no verdict behind it is the exact failure this system
    exists to prevent.
    """

    state = live_window.lifecycle.status().state

    assert state is not OperatorState.SAFE
    assert live_window.board.outputs.reported is False


def test_demo_banner_states_that_data_is_not_live(window):
    text = window.banner.text()

    assert "DEMO DATA" in text
    assert "not a live safety system" in text


def test_live_window_without_a_port_says_it_cannot_drive_the_board(live_window):
    live_window._refresh()
    live_window._push_banner()

    assert "NO SERIAL PORT" in live_window.banner.text()


def test_confirmation_timer_survives_repeated_frames(live_window):
    """The real defect this guards: recreating the timestamps each frame reset
    the timer forever, so a sustained violation never latched."""

    window = live_window

    observation = window.Observation.VIOLATION
    now = time.monotonic()
    for step in range(60):
        window._advance(observation, now + step * 0.05, kind="VIOLATION")
    assert window.lifecycle.status().state is OperatorState.ALARM_LATCHED


def test_safe_verdict_alone_does_not_latch_anything(live_window):
    window = live_window

    observation = window.Observation.SAFE
    now = time.monotonic()
    for step in range(40):
        window._advance(observation, now + step * 0.05, kind="SAFE")
    assert window.lifecycle.status().state in {
        OperatorState.SAFE,
        OperatorState.WAITING_FOR_TARGET,
    }
    assert window.lifecycle.status().incident_id is None


def test_unknown_verdict_never_promotes_to_safe(live_window):
    """An empty frame is missing information. It must not confirm compliance."""

    window = live_window

    observation = window.Observation.UNKNOWN
    now = time.monotonic()
    for step in range(20):
        window._advance(observation, now + step * 0.05, kind="UNKNOWN")
    assert window.lifecycle.status().state is not OperatorState.SAFE


def test_heartbeat_reply_does_not_confirm_an_acknowledgement(live_window):
    """The bug the correlator exists to prevent: any reply used to be treated as
    an acknowledgement, including ``OK HEARTBEAT``."""

    window = live_window

    observation = window.Observation.VIOLATION
    now = time.monotonic()
    for step in range(60):
        window._advance(observation, now + step * 0.05, kind="VIOLATION")
    assert window.lifecycle.status().state is OperatorState.ALARM_LATCHED
    port = FakePort()
    window._port = port
    window._serial = object()  # marks the link as present for _send
    window._drain_commands(time.monotonic())
    # The board must answer the alarm before the operator may press A: until it
    # does, the correlator legitimately holds the slot and refuses a second
    # command, which is what stops a stale reply confirming the wrong one.
    assert port.written[-1] == "INCIDENT 1"
    window._on_serial_line("OK ALARM_LATCHED 1")
    window.request_acknowledge()
    assert port.written[-1].startswith("ACKNOWLEDGE")
    window._on_serial_line("OK HEARTBEAT")
    assert window.lifecycle.status().acknowledged is False
    assert window.accumulator.acknowledgements == 0


def test_matching_acknowledgement_reply_is_accepted(live_window):
    window = live_window

    observation = window.Observation.VIOLATION
    now = time.monotonic()
    for step in range(60):
        window._advance(observation, now + step * 0.05, kind="VIOLATION")
    port = FakePort()
    window._port = port
    window._serial = object()
    window._drain_commands(time.monotonic())
    window._on_serial_line("OK ALARM_LATCHED 1")
    window.request_acknowledge()
    assert port.written[-1].startswith("ACKNOWLEDGE")
    window._on_serial_line("OK ACKNOWLEDGED")
    assert window.lifecycle.status().acknowledged is True
    assert window.accumulator.acknowledgements == 1


def test_boot_banner_is_not_read_as_a_reply(live_window):
    """The sketch prints prose at boot; treating it as a refusal would park the
    laptop in FAULT on every connect."""

    window = live_window

    before = len(window.timeline.row_texts())
    window._on_serial_line("---------------- STATUS ----------------")
    window._on_serial_line("firmware        : Phase 4A incident lifecycle")
    # Logged, but no state change and no correlator traffic.
    assert len(window.timeline.row_texts()) >= before
    assert window.correlator.confirmed == 0


def test_status_line_updates_the_reported_pin_state(live_window):
    window = live_window

    window._on_serial_line(
        "STATUS STATE=ALARM_LATCHED INCIDENT=2 ACK=0 RESET_READY=0 REARM=1 "
        "GREEN=0 RED=1 BUZZER=1 LINK=1"
    )
    assert window.board.outputs.reported is True
    assert window.board.outputs.red.value == "ON"
    assert window.board.outputs.green.value == "OFF"
    assert window.board.outputs.buzzer.value == "ON"
    assert window.board.incident_id == 2


def test_pending_reset_token_is_sent_after_its_reply(live_window):
    """RESET_READY advances to RESET only via the board's reply, so the token
    must be transmitted rather than cleared locally."""

    window = live_window

    port = FakePort()
    window._port = port
    window._serial = object()
    observation = window.Observation.VIOLATION
    now = time.monotonic()
    for step in range(60):
        window._advance(observation, now + step * 0.05, kind="VIOLATION")
    window._drain_commands(now)
    window._on_serial_line("OK ALARM_LATCHED 1")
    window.request_acknowledge()
    window._on_serial_line("OK ACKNOWLEDGED")
    assert "ACKNOWLEDGE 1" in port.written
    # Prove recovery so the reset gate can open.
    safe = window.Observation.SAFE
    for step in range(60):
        window._advance(safe, now + 10.0 + step * 0.05, kind="SAFE")
    assert window.lifecycle.reset_blocker().name == "NONE"
    window.request_reset()
    assert "RESET_READY 1" in port.written
    window._on_serial_line("OK RESET_READY")
    assert "RESET" in port.written
    window._on_serial_line("OK RESET_ACCEPTED")
    assert window.lifecycle.status().state is OperatorState.WAITING_FOR_TARGET
    assert window.lifecycle.status().incident_id is None
    assert window.accumulator.resets == 1


def test_close_never_sends_all_off_or_reset(live_window):
    """A latched alarm must outlive the dashboard window."""

    window = live_window
    port = FakePort()
    window._port = port
    window._serial = object()

    observation = window.Observation.VIOLATION
    now = time.monotonic()

    for step in range(60):
        window._advance(observation, now + step * 0.05, kind="VIOLATION")

    assert window.lifecycle.status().state is OperatorState.ALARM_LATCHED

    written_before_close = list(port.written)
    window.close()

    assert port.written == written_before_close
    assert "ALL_OFF" not in port.written
    assert window.lifecycle.status().state is OperatorState.ALARM_LATCHED


def test_control_panel_buttons_track_the_reset_gate(window):
    """A disabled button must show the rejection before the key is pressed."""

    window._refresh()

    assert window.control_panel.reset_button.isEnabled() is False
    assert window.control_panel.acknowledge_button.isEnabled() is False
    assert window.control_panel.gate_label.text()


def test_shortcuts_exist_for_every_documented_key(window):
    sequences = {
        shortcut.key().toString() for shortcut in window._shortcuts
    }

    for expected in ("F", "Esc", "S", "A", "R", "Q", "Ctrl+E", "F1", "P", "T"):
        assert expected in sequences


def test_status_bar_names_the_mode_and_the_state(window):
    window._refresh()

    text = window.status_label.text()

    assert "DEMO DATA" in text
    assert window.lifecycle.status().state.value in text


def test_export_writes_a_csv(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    target = tmp_path / "session.csv"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )

    window._refresh()
    window.export_session()

    assert target.is_file()

    header = target.read_text(encoding="utf-8").splitlines()[0]

    assert header == "time_s,source,state,event"


def test_export_cancelled_writes_nothing(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", ""))
    )

    window.export_session()

    assert list(tmp_path.iterdir()) == []


def test_every_panel_renders_from_a_snapshot(window):
    window._refresh()

    # A snapshot built from a real lifecycle must drive every panel without a
    # KeyError, which is what a missing STATE_STYLES row would raise.
    for panel in (
        window.safety_panel,
        window.hardware_panel,
        window.incident_panel,
        window.health_panel,
        window.control_panel,
        window.camera_panel,
    ):
        assert panel.isVisible()

    # The charts and the evidence camera live on the analytics tab, so they are
    # only visible once that tab is raised. Checking them while the live tab is
    # showing would assert the tab bar, not the panel.
    assert not window.charts_panel.isVisible()
    assert not window.evidence_camera.isVisible()

    window.tabs.setCurrentIndex(1)
    assert window.charts_panel.isVisible()
    assert window.evidence_camera.isVisible()

    assert theme.style_for_state(window.lifecycle.status().state)
