"""Adapters: the deterministic, Qt-free half of the dashboard.

Everything here can be reasoned about without a widget, which is the point: the
fixtures and the health model are pure functions so a test failure names the data
rather than a paint event.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.dashboard import adapters, theme
from src.dashboard.state_models import LinkState, OutputState
from tools.incident_lifecycle import IncidentLifecycle, OperatorState, ResetBlocker

METRICS = Path("experiments/ai_metrics.csv")


# -- firmware STATUS parsing ------------------------------------------------


def test_parse_status_reads_every_field():
    line = (
        "STATUS STATE=ALARM_LATCHED INCIDENT=7 ACK=0 RESET_READY=0 REARM=3 "
        "GREEN=0 RED=1 BUZZER=1 LINK=1"
    )

    status = adapters.parse_firmware_status(line)

    assert status is not None
    assert status.state == "ALARM_LATCHED"
    assert status.incident == 7
    assert status.ack == 0
    assert status.reset_ready == 0
    assert status.rearm == 3
    assert (status.green, status.red, status.buzzer, status.link) == (0, 1, 1, 1)


def test_parse_status_tolerates_the_human_status_block():
    """The sketch prints a prose block before the machine line.

    Returning ``None`` for those lines is what keeps the parser from reading a
    sentence as a refusal.
    """

    assert adapters.parse_firmware_status("---------------- STATUS ----------------") is None
    assert adapters.parse_firmware_status("firmware        : Phase 4A") is None


def test_parse_status_rejects_a_truncated_line():
    """A STATUS line that is present but incomplete is a protocol fault, so it
    raises instead of guessing the missing fields."""

    with pytest.raises(Exception, match="missing"):
        adapters.parse_firmware_status("STATUS STATE=SAFE GREEN=1")


def test_reported_outputs_come_from_the_board_not_the_laptop():
    line = "STATUS STATE=SAFE INCIDENT=0 ACK=0 RESET_READY=0 REARM=0 GREEN=1 RED=0 BUZZER=0 LINK=1"
    outputs = adapters.parse_firmware_status(line).to_outputs()

    assert outputs.reported is True
    assert outputs.green is OutputState.ON
    assert outputs.red is OutputState.OFF
    assert outputs.buzzer is OutputState.OFF


def test_unparsed_board_reports_unknown_not_off():
    """Off and unknown must not look the same on screen."""

    from src.dashboard.state_models import BoardSnapshot

    board = BoardSnapshot(port="/dev/null")

    assert board.outputs.reported is False
    assert board.outputs.green is OutputState.UNKNOWN
    assert board.link is LinkState.CLOSED


def test_link_flag_zero_does_not_forge_a_healthy_link():
    """LINK=0 means the board has not heard a HEARTBEAT in 3 s, so the pins it
    reports are a stale photograph, not current fact."""

    line = "STATUS STATE=SAFE INCIDENT=1 ACK=1 RESET_READY=0 REARM=0 GREEN=0 RED=1 BUZZER=0 LINK=0"
    status = adapters.parse_firmware_status(line)

    assert status.link is False
    assert status.to_outputs().link_healthy is False


# -- reset gate text --------------------------------------------------------


def test_every_reset_blocker_has_operator_text():
    for blocker in ResetBlocker:
        text = adapters.reset_blocker_text(blocker)

        assert text
        assert text.endswith(".")


def test_open_gate_tells_the_operator_the_key_to_press():
    assert "request a reset" in adapters.reset_blocker_text(ResetBlocker.NONE).lower()


# -- health -----------------------------------------------------------------


def test_health_marks_unconfigured_services_rather_than_offline():
    items = adapters.build_health_items(camera_ok=True, model_ok=True, serial_ok=True)
    by_name = {item.name: item for item in items}

    assert by_name["Blynk"].status == theme.NOT_CONFIGURED
    assert by_name["Wi-Fi"].status == theme.NOT_CONFIGURED


def test_health_reports_a_dead_camera_as_offline_not_disabled():
    items = adapters.build_health_items(camera_ok=False, model_ok=True, serial_ok=True)
    by_name = {item.name: item for item in items}

    assert by_name["Camera"].status == theme.OFFLINE


def test_health_reports_demo_data_as_disabled():
    items = adapters.build_health_items(
        camera_ok=None, model_ok=None, serial_ok=None, demo_data=True
    )
    by_name = {item.name: item for item in items}

    assert by_name["Camera"].status == theme.DISABLED
    assert by_name["Serial link"].status == theme.DISABLED


def test_fresh_status_is_online():
    items = adapters.build_health_items(
        camera_ok=True, model_ok=True, serial_ok=True, status_age_seconds=0.4
    )
    by_name = {item.name: item for item in items}

    assert by_name["Serial link"].status == theme.ONLINE


def test_stale_status_degrades_the_serial_row():
    """A link that answers but has gone quiet must not read as healthy: the
    firmware's own heartbeat window is 3 s."""

    items = adapters.build_health_items(
        camera_ok=True, model_ok=True, serial_ok=True, status_age_seconds=30.0
    )
    by_name = {item.name: item for item in items}

    assert by_name["Serial link"].status == theme.DEGRADED


def test_missing_logo_degrades_branding_row():
    items = adapters.build_health_items(
        camera_ok=True, model_ok=True, serial_ok=True, missing_logo=True
    )
    by_name = {item.name: item for item in items}

    assert by_name["Branding assets"].status == theme.DEGRADED


# -- metrics ----------------------------------------------------------------


def test_metrics_file_exists():
    assert METRICS.is_file()


def test_load_metrics_reads_the_locked_test_row():
    table = adapters.load_metrics()

    assert table.available is True
    assert table.error == ""
    assert len(table.rows) >= 1

    row = table.rows[0]
    assert row.test_id == "AI-01"
    assert 0.0 <= row.precision <= 1.0
    assert 0.0 <= row.map50 <= 1.0


def test_load_metrics_reports_a_missing_file_without_raising(tmp_path):
    table = adapters.load_metrics(tmp_path / "absent.csv")

    assert table.available is False
    assert tuple(table.rows) == ()
    assert "absent.csv" in table.error


def test_metrics_columns_are_not_hard_coded_twice():
    with METRICS.open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle))

    for column in adapters.METRICS_REQUIRED_COLUMNS:
        assert column in header


# -- accumulator ------------------------------------------------------------


def test_accumulator_counts_frames_by_verdict():
    acc = adapters.SessionAccumulator()

    for verdict in ("SAFE", "SAFE", "VIOLATION", "UNKNOWN", "SAFE"):
        acc.record_frame(verdict)

    snapshot = acc.snapshot()

    assert snapshot.frames == 5
    assert snapshot.safe_frames == 3
    assert snapshot.violation_frames == 1
    assert snapshot.unknown_frames == 1


def test_safe_ratio_ignores_unknown_frames():
    """Unknown is missing information: counting it as unsafe would overstate the
    violation rate, and counting it as safe would overstate compliance."""

    acc = adapters.SessionAccumulator()

    acc.record_frame("SAFE")
    acc.record_frame("UNKNOWN")
    acc.record_frame("UNKNOWN")

    assert acc.snapshot().safe_ratio == pytest.approx(1.0)


def test_safe_ratio_is_zero_when_nothing_is_known():
    assert adapters.SessionAccumulator().snapshot().safe_ratio == 0.0


def test_state_durations_close_on_exit_not_entry():
    acc = adapters.SessionAccumulator()

    acc.record_state("SAFE", 100.0)
    acc.record_state("ALARM_LATCHED", 103.0)
    acc.record_state("SAFE", 108.0)

    durations = acc.snapshot().state_durations

    assert durations["SAFE"] == pytest.approx(3.0)
    assert durations["ALARM_LATCHED"] == pytest.approx(5.0)


def test_state_duration_shares_sum_to_one():
    shares = adapters.state_duration_shares({"SAFE": 6.0, "ALARM_LATCHED": 2.0})

    labels = {name: value for name, value in shares}

    assert labels["SAFE"] == pytest.approx(0.75)
    assert sum(value for _, value in shares) == pytest.approx(1.0)


def test_state_duration_shares_of_nothing_is_empty_not_a_division_error():
    assert list(adapters.state_duration_shares({})) == []


# -- formatting -------------------------------------------------------------


def test_format_seconds_is_fixed_width_and_handles_none():
    assert adapters.format_seconds(4.2) == "4.20 s"
    assert adapters.format_seconds(None) == "--"


def test_format_percent_rounds_consistently():
    assert adapters.format_percent(0.756) == "75.6%"


def test_fingerprint_changes_when_the_file_changes(tmp_path):
    target = tmp_path / "f.bin"
    target.write_bytes(b"one")
    first = adapters.fingerprint(target)

    target.write_bytes(b"two")
    second = adapters.fingerprint(target)

    assert first != second


def test_fingerprint_of_a_missing_file_is_empty(tmp_path):
    """Empty, not a made-up hash: an absent model must not look verified."""

    assert adapters.fingerprint(tmp_path / "absent.bin") == ""


def test_file_size_mb_is_a_display_string(tmp_path):
    target = tmp_path / "f.bin"
    target.write_bytes(b"x" * (2 * 1024 * 1024))

    assert adapters.file_size_mb(target) == "2.0 MB"
    assert adapters.file_size_mb(tmp_path / "absent.bin") == ""


# -- telemetry fixtures -----------------------------------------------------


def test_fixture_telemetry_is_deterministic():
    a = adapters.fixture_telemetry(7)
    b = adapters.fixture_telemetry(7)

    assert a == b


def test_fixture_telemetry_differs_between_steps():
    assert adapters.fixture_telemetry(7) != adapters.fixture_telemetry(8)


def test_fixture_telemetry_is_bounded_and_positive():
    for step in range(0, 200, 7):
        sample = adapters.fixture_telemetry(step)

        assert 0.0 < sample.fps < 120.0
        assert 0.0 < sample.inference_ms < 500.0
        assert 0.0 <= sample.max_confidence <= 1.0


def test_fixture_telemetry_is_slower_while_the_alarm_is_latched():
    """An alarm means more detections to draw, so the fixture must not show a
    suspiciously fast frame rate exactly when the screen turns red."""

    safe = sum(
        adapters.fixture_telemetry(step, state="SAFE").inference_ms
        for step in range(20)
    )
    latched = sum(
        adapters.fixture_telemetry(step, state="ALARM_LATCHED").inference_ms
        for step in range(20)
    )

    assert latched > safe


# -- demo driver ------------------------------------------------------------


def test_demo_driver_plays_the_whole_storyline():
    """The fixture must reach every operator-visible state, including the reset
    handshake, or a rehearsal would not look like the demo."""

    import time

    lifecycle = IncidentLifecycle(confirmation_seconds=2.0, clearance_seconds=2.0)
    lifecycle.start(0.0)

    driver = adapters.DemoDataDriver(lifecycle)
    now = time.monotonic()
    driver.prime(now)

    seen: list[str] = []

    for _ in range(600):
        now += 0.05
        driver.step(now, 0.05)
        state = lifecycle.status().state.value

        if not seen or seen[-1] != state:
            seen.append(state)

    assert "SAFE" in seen
    assert "VIOLATION_PENDING" in seen
    assert "ALARM_LATCHED" in seen
    assert "SAFE_CLEAR_PENDING" in seen
    assert "SAFE_RECOVERED" in seen
    assert "ACKNOWLEDGED" in seen

    assert "INCIDENT 1" in driver.sent_commands
    assert "ACKNOWLEDGE 1" in driver.sent_commands
    assert "RESET_READY 1" in driver.sent_commands
    assert "RESET" in driver.sent_commands


def test_demo_driver_never_sends_an_invented_command():
    """Every command the fixture emits must exist in the firmware protocol."""

    import time

    allowed = {
        "HEARTBEAT",
        "STATUS",
        "INCIDENT",
        "NO_INCIDENT",
        "SILENCE",
        "ACKNOWLEDGE",
        "RESET_READY",
        "RESET",
        "GREEN_ON",
        "GREEN_OFF",
        "RED_ON",
        "RED_OFF",
        "BUZZER_ON",
        "BUZZER_OFF",
        "ALL_OFF",
    }

    lifecycle = IncidentLifecycle()
    lifecycle.start(0.0)

    driver = adapters.DemoDataDriver(lifecycle)
    now = time.monotonic()
    driver.prime(now)

    for _ in range(600):
        now += 0.05
        driver.step(now, 0.05)

    for command in driver.sent_commands:
        assert command.split()[0] in allowed, command


def test_demo_driver_reports_a_board_that_matches_the_lifecycle():
    import time

    lifecycle = IncidentLifecycle()
    lifecycle.start(0.0)

    driver = adapters.DemoDataDriver(lifecycle)
    now = time.monotonic()
    driver.prime(now)

    for _ in range(300):
        now += 0.05
        driver.step(now, 0.05)

    board = driver.snapshot_board()

    assert board.outputs.reported is True
    assert board.link is LinkState.OPEN
