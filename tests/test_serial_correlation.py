"""The laptop must never confirm a command the board did not answer.

Two real defects motivated these tests:

1. A latched alarm reverted to green about one second later. The green lamp
   came back because ``ALARM`` was a *pin preset* on the sketch: it drove the
   pins and never touched ``IncidentCore``, so the next ``HEARTBEAT`` recomputed
   the outputs from ``Safe`` and turned green on.
2. The overlay showed ACKNOWLEDGED while the board was still buzzing. The
   laptop promoted itself on *any* reply it happened to read, including
   ``OK HEARTBEAT``, and there were zero ``ACKNOWLEDG`` lines in the hardware
   log.

The fix is a single outstanding command with a known expected reply code. These
tests pin the consequences.
"""

from __future__ import annotations

import pytest

from tools.incident_lifecycle import (
    AlarmIntent,
    Event,
    IncidentLifecycle,
    OperatorState,
    ResetBlocker,
)
from tools.live_eye_shield_demo import (
    FIRE_AND_FORGET,
    CommandCorrelator,
    OutstandingCommand,
    SerialReply,
    expected_code_for,
    reply_matches,
)

VIOLATION = "VIOLATION"
SAFE = "SAFE"
UNKNOWN = "UNKNOWN"


def _ok(code: str) -> SerialReply:
    return SerialReply(raw=f"OK {code}", ok=True, code=code)


def _err(code: str, detail: str = "") -> SerialReply:
    return SerialReply(raw=f"ERR {code} {detail}".strip(), ok=False, code=code, detail=detail)


def _lc() -> IncidentLifecycle:
    lifecycle = IncidentLifecycle(confirmation_seconds=2.0, clearance_seconds=2.0)
    lifecycle.start(0.0)
    return lifecycle


def _latch(lifecycle: IncidentLifecycle, start: float = 0.0) -> float:
    for offset in (0.0, 1.0, 2.0):
        lifecycle.update(VIOLATION, start + offset, violation_ratio=1.0)
    assert lifecycle.state is OperatorState.ALARM_LATCHED
    return start + 2.0


# ---------------------------------------------------------------------------
# Expected-reply mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command, expected",
    [
        ("INCIDENT 3", "ALARM_LATCHED"),
        ("SILENCE 2", "SILENCED"),
        ("ACKNOWLEDGE 1", "ACKNOWLEDGED"),
        ("ACK 1", "ACKNOWLEDGED"),
        ("RESET", "RESET_ACCEPTED"),
        ("RESET_READY 4", "RESET_READY"),
        ("NO_INCIDENT 2", "NO_INCIDENT"),
    ],
)
def test_01_expected_reply_code_covers_every_lifecycle_command(command, expected):
    """Every lifecycle command must declare the reply that proves it landed."""

    assert expected_code_for(command) == expected


@pytest.mark.parametrize(
    "command",
    [
        "ALARM",
        "SAFE",
        "GREEN_ON",
        "RED_ON",
        "BUZZER_ON",
        "GARBAGE",
        "SILENCE",
        "ACK",
        "INCIDENT",
        "NO_INCIDENT",
        "RESET_READY",
    ],
)
def test_02_bare_pin_presets_have_no_place_in_the_lifecycle_protocol(command):
    """The presets are what bypassed IncidentCore, so they are not sendable.

    ``ALARM``/``SAFE`` are matched against the firmware's preset table before
    the real parser, which is exactly why they drove pins without latching
    anything. The laptop must never put one on the wire.

    Bare ``SILENCE``/``ACK`` are refused for a different reason: they are the
    firmware's id-less legacy aliases, which skip the incident-id check the
    rest of the protocol relies on. Bare ``INCIDENT``/``NO_INCIDENT``/
    ``RESET_READY`` are id-mandatory on the board and would earn
    ``ERR BAD_ARGUMENT``, which is worse than never sending them.
    """

    assert expected_code_for(command) is None


def test_02b_id_carrying_forms_are_accepted():
    assert expected_code_for("SILENCE 1") == "SILENCED"
    assert expected_code_for("ACKNOWLEDGE 1") == "ACKNOWLEDGED"
    assert expected_code_for("ACK 1") == "ACKNOWLEDGED"


# ---------------------------------------------------------------------------
# reply_matches: the anti-false-positive gate
# ---------------------------------------------------------------------------


def test_03_heartbeat_never_matches_an_acknowledge():
    """The original defect in one assertion.

    An ``ACKNOWLEDGE 1`` is in flight. The board answers a periodic heartbeat
    with ``OK HEARTBEAT``. That line must not consume the acknowledgement.
    """

    correlator = CommandCorrelator()
    correlator.outstanding = OutstandingCommand(
        command="ACKNOWLEDGE 1",
        expected_code="ACKNOWLEDGED",
        sent_at=0.0,
        incident_id=1,
    )

    assert correlator.match(_ok("HEARTBEAT")) == "IGNORED"
    assert correlator.outstanding is not None
    assert correlator.confirmed == 0


def test_04_acknowledge_is_not_confirmed_by_ok_heartbeat():
    lifecycle = _lc()
    _latch(lifecycle)
    lifecycle.acknowledge(2.1)

    outcome = lifecycle.confirm_acknowledge(_ok("HEARTBEAT"), 2.2, 1)

    assert outcome == "IGNORED"
    assert lifecycle.incident is not None
    assert lifecycle.incident.acknowledged is False
    assert lifecycle.status().alarm is AlarmIntent.SOUNDING
    assert lifecycle.reset_blocker() is ResetBlocker.NOT_ACKNOWLEDGED


def test_05_acknowledge_is_confirmed_only_by_ok_acknowledged():
    lifecycle = _lc()
    _latch(lifecycle)
    lifecycle.acknowledge(2.1)

    outcome = lifecycle.confirm_acknowledge(_ok("ACKNOWLEDGED"), 2.2, 1)

    assert outcome == "CONFIRMED"
    assert lifecycle.incident is not None and lifecycle.incident.acknowledged
    assert lifecycle.status().alarm is AlarmIntent.SILENCED
    # Red is still on: acknowledge is not reset.
    assert lifecycle.reset_blocker() is ResetBlocker.NOT_SAFE_RECOVERED


def test_06_acknowledge_for_the_wrong_incident_is_ignored():
    """A late answer for a previous episode must not close the current one."""

    lifecycle = _lc()
    _latch(lifecycle)
    lifecycle.acknowledge(2.1)

    assert lifecycle.confirm_acknowledge(_ok("ACKNOWLEDGED"), 2.2, 99) == "IGNORED"
    assert lifecycle.incident is not None and not lifecycle.incident.acknowledged


def test_07_acknowledge_reply_without_a_pending_request_is_ignored():
    """An unsolicited OK ACKNOWLEDGED must not create authority from nothing."""

    lifecycle = _lc()
    _latch(lifecycle)

    assert lifecycle.confirm_acknowledge(_ok("ACKNOWLEDGED"), 2.2, 1) == "NO_PENDING_ACK"
    assert lifecycle.incident is not None and not lifecycle.incident.acknowledged


def test_08_rejected_acknowledge_leaves_the_alarm_sounding():
    lifecycle = _lc()
    latch_at = _latch(lifecycle)
    lifecycle.acknowledge(2.1)

    outcome = lifecycle.confirm_acknowledge(
        _err("ACKNOWLEDGE_REJECTED", "NO_INCIDENT"), 2.2, 1
    )

    assert outcome == "REJECTED"
    assert lifecycle.status().alarm is AlarmIntent.SOUNDING
    assert lifecycle.reset_blocker() is ResetBlocker.NOT_ACKNOWLEDGED
    assert lifecycle.transitions[-1].event is Event.ACKNOWLEDGE_REJECTED
    assert lifecycle.last_now >= latch_at


def test_09_repeated_acknowledge_requests_do_not_stack():
    lifecycle = _lc()
    _latch(lifecycle)

    assert lifecycle.acknowledge(2.1) == (True, "OK")
    assert lifecycle.acknowledge(2.2) == (True, "ACKNOWLEDGE_PENDING")


# ---------------------------------------------------------------------------
# The correlator itself
# ---------------------------------------------------------------------------


class _FakePort:
    """Minimal stand-in for a pyserial port: records writes, never blocks."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, data: bytes) -> None:
        self.written.append(data.decode("utf-8"))

    def flush(self) -> None:
        pass


def test_10_correlator_claims_the_slot_for_a_written_command():
    port = _FakePort()
    correlator = CommandCorrelator()

    assert correlator.send(port, "ACKNOWLEDGE 1", 5.0, incident_id=1) is True
    assert port.written == ["ACKNOWLEDGE 1\n"]
    assert correlator.busy is True
    assert correlator.outstanding is not None
    assert correlator.outstanding.expected_code == "ACKNOWLEDGED"
    assert correlator.outstanding.sent_at == 5.0
    assert correlator.outstanding.incident_id == 1


def test_11_a_second_send_while_busy_is_refused_not_queued():
    """Queueing would let a stale reply be matched to the wrong command."""

    port = _FakePort()
    correlator = CommandCorrelator()

    assert correlator.send(port, "INCIDENT 1", 1.0, incident_id=1) is True
    assert correlator.send(port, "RESET", 1.1, incident_id=1) is False

    assert port.written == ["INCIDENT 1\n"]
    assert correlator.outstanding is not None
    assert correlator.outstanding.command == "INCIDENT 1"


def test_12_fire_and_forget_commands_never_claim_the_slot():
    """A periodic heartbeat would otherwise block every real confirmation."""

    port = _FakePort()
    correlator = CommandCorrelator()

    assert correlator.outstanding is None
    assert expected_code_for("HEARTBEAT") == "HEARTBEAT"
    # HEARTBEAT is in EXPECTED_REPLY_CODES but the demo sends it with
    # send_serial_command directly, so it is never correlated. Pin that the
    # set which documents the intent stays accurate.
    assert "HEARTBEAT" in FIRE_AND_FORGET
    assert "ACKNOWLEDGE" not in FIRE_AND_FORGET


def test_12b_ignored_reply_keeps_the_slot_claimed():
    port = _FakePort()
    correlator = CommandCorrelator()
    correlator.send(port, "ACKNOWLEDGE 1", 2.0, incident_id=1)

    assert correlator.match(_ok("HEARTBEAT")) == "IGNORED"
    assert correlator.busy is True
    assert correlator.unmatched == 1

    assert correlator.match(_ok("ACKNOWLEDGED")) == "MATCHED"
    assert correlator.busy is False
    assert correlator.confirmed == 1


def test_12c_unsolicited_reply_with_nothing_in_flight_is_idle():
    correlator = CommandCorrelator()
    assert correlator.match(_ok("ACKNOWLEDGED")) == "IDLE"
    # Nothing in flight means nothing can have been confirmed.
    assert correlator.confirmed == 0


def test_13_timeout_expires_the_slot_and_is_not_success():
    correlator = CommandCorrelator(timeout_seconds=2.0)
    correlator.outstanding = None


    correlator.outstanding = OutstandingCommand(
        command="ACKNOWLEDGE 1",
        expected_code="ACKNOWLEDGED",
        sent_at=10.0,
        incident_id=1,
    )

    assert correlator.expire(11.0) is None
    assert correlator.outstanding is not None

    assert correlator.expire(12.5) == "ACKNOWLEDGE 1"
    assert correlator.outstanding is None
    assert correlator.timed_out == 1


def test_14_exact_match_required_not_prefix():
    """``OK SILENCED`` must not answer ``ACKNOWLEDGE``."""


    pending = OutstandingCommand(
        command="ACKNOWLEDGE 1",
        expected_code="ACKNOWLEDGED",
        sent_at=0.0,
        incident_id=1,
    )

    assert reply_matches(pending, _ok("SILENCED")) is False
    assert reply_matches(pending, _ok("ACKNOWLEDGED_EXTRA")) is False


# ---------------------------------------------------------------------------
# Heartbeats and the latch
# ---------------------------------------------------------------------------


def test_15_heartbeats_cannot_clear_a_latch():
    lifecycle = _lc()
    latch_at = _latch(lifecycle)

    for second in range(10):
        lifecycle.update(UNKNOWN, latch_at + 1.0 + second)

    assert lifecycle.state is OperatorState.ALARM_LATCHED
    assert lifecycle.status().alarm is AlarmIntent.SOUNDING
    assert lifecycle.status().incident_id == 1


def test_16_incident_command_carries_the_id_not_a_preset():
    lifecycle = _lc()
    _latch(lifecycle)

    assert lifecycle.take_pending_command() == "INCIDENT 1"


def test_17_acknowledge_command_carries_the_id():
    lifecycle = _lc()
    latch_at = _latch(lifecycle)
    lifecycle.acknowledge(latch_at + 0.1)

    commands = []
    while (command := lifecycle.take_pending_command()) is not None:
        commands.append(command)

    assert commands == ["INCIDENT 1", "ACKNOWLEDGE 1"]
    assert not {"SAFE", "ALARM", "SILENCE"}.intersection(commands)


def test_18_serial_state_never_returns_a_bare_preset():
    lifecycle = _lc()
    seen = {lifecycle.serial_state}

    latch_at = _latch(lifecycle)
    seen.add(lifecycle.serial_state)

    lifecycle.acknowledge(latch_at + 0.1)
    seen.add(lifecycle.serial_state)

    lifecycle.confirm_acknowledge(_ok("ACKNOWLEDGED"), latch_at + 0.2, 1)
    seen.add(lifecycle.serial_state)

    lifecycle.update(SAFE, latch_at + 0.5)
    lifecycle.update(SAFE, latch_at + 1.5)
    lifecycle.update(SAFE, latch_at + 2.5)
    seen.add(lifecycle.serial_state)

    assert not {"SAFE", "ALARM", "SILENCE"}.intersection(seen)
    assert "INCIDENT 1" in seen
    assert "ACKNOWLEDGE 1" in seen


def test_19_safe_frames_do_not_move_an_unanswered_incident_to_green():
    """Recovering the person is not answering the alarm."""

    lifecycle = _lc()
    latch_at = _latch(lifecycle)

    lifecycle.update(SAFE, latch_at + 0.5)
    lifecycle.update(SAFE, latch_at + 1.5)
    status = lifecycle.update(SAFE, latch_at + 2.5)

    assert status.safe_recovered is True
    assert lifecycle.status().alarm is AlarmIntent.SILENCED
    assert lifecycle.reset_blocker() is ResetBlocker.NOT_ACKNOWLEDGED


def test_20_reset_is_still_refused_after_recovery_but_no_ack():
    lifecycle = _lc()
    latch_at = _latch(lifecycle)

    lifecycle.update(SAFE, latch_at + 0.5)
    lifecycle.update(SAFE, latch_at + 1.5)
    lifecycle.update(SAFE, latch_at + 2.5)
    lifecycle.mark_onboard_verified()

    accepted, blocker = lifecycle.reset(latch_at + 3.0)

    assert accepted is False
    assert blocker is ResetBlocker.NOT_ACKNOWLEDGED
    assert lifecycle.incident is not None


def test_21_pending_acknowledge_is_cleared_when_the_alarm_rearms():
    """A renewed violation must wipe the request, not leave it dangling."""

    lifecycle = _lc()
    latch_at = _latch(lifecycle)
    lifecycle.acknowledge(latch_at + 0.1)
    lifecycle.confirm_acknowledge(_ok("ACKNOWLEDGED"), latch_at + 0.2, 1)

    lifecycle.update(VIOLATION, latch_at + 3.0, violation_ratio=1.0)

    assert lifecycle.state is OperatorState.ALARM_LATCHED
    assert lifecycle.incident is not None
    assert lifecycle.incident.acknowledged is False
    assert lifecycle.incident.acknowledge_pending is False
    assert lifecycle.status().alarm is AlarmIntent.SOUNDING


def test_22_requeue_makes_a_command_retryable():
    """A command that could not be written must not be silently dropped."""

    lifecycle = _lc()
    _latch(lifecycle)

    assert lifecycle.take_pending_command() == "INCIDENT 1"
    assert lifecycle.take_pending_command() is None

    lifecycle.requeue_last_command()
    assert lifecycle.take_pending_command() == "INCIDENT 1"


def test_23_requeue_on_empty_log_is_a_no_op():
    lifecycle = _lc()
    lifecycle.requeue_last_command()
    assert lifecycle.take_pending_command() is None


def test_24_steady_state_is_silent_when_nothing_is_open():
    """Nothing open means nothing to assert, so no command goes out."""

    lifecycle = _lc()
    assert lifecycle.serial_state == "WAITING"
    assert lifecycle._steady_state_command() is None
