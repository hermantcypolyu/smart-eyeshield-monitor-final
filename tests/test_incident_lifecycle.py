"""Focused tests for the operator incident lifecycle (``tools/incident_lifecycle.py``).

Every test here is synthetic: no camera, no model, no serial port, no torch.
The lifecycle module was written as a pure layer precisely so these 25
scenarios could be proven without hardware, and so a future dashboard or Blynk
UI inherits behaviour that is already tested rather than re-deriving it.

The scenarios follow the acceptance list in the lifecycle specification, in the
same order, so a failing test number maps straight onto a numbered requirement.
"""

from __future__ import annotations

import pytest

from tools.incident_lifecycle import (
    INCIDENT_STATES,
    AlarmIntent,
    Event,
    IncidentLifecycle,
    OperatorState,
    ResetBlocker,
)


class Obs:
    """Minimal stand-in for the demo's ``Observation`` enum.

    The lifecycle only ever reads ``.name``, so these tests stay importable in
    milliseconds instead of paying the ~4 s torch/cv2 import the demo costs.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Obs({self.name})"


UNKNOWN = Obs("UNKNOWN")
SAFE = Obs("SAFE")
VIOLATION = Obs("VIOLATION")

#: The bare pin presets. None of these may ever be emitted for an incident:
#: they are diagnostic-only on the firmware and bypass IncidentCore entirely.
BARE_PRESETS = {"SAFE", "ALARM", "SILENCE", "GREEN_ON", "GREEN_OFF", "RED_ON", "RED_OFF"}


def make_lifecycle(
    confirmation_seconds: float = 2.0,
    clearance_seconds: float = 2.0,
) -> IncidentLifecycle:
    lc = IncidentLifecycle(
        confirmation_seconds=confirmation_seconds,
        clearance_seconds=clearance_seconds,
    )
    lc.start(0.0)
    return lc


def feed(
    lc: IncidentLifecycle,
    observation: Obs,
    now: float,
    violation_ratio: float = 0.0,
) -> None:
    lc.update(observation, now, violation_ratio=violation_ratio)


def latch_incident(lc: IncidentLifecycle, start: float = 0.0) -> float:
    """Drive a fresh lifecycle into ALARM_LATCHED and return the latch time."""

    feed(lc, VIOLATION, start + 0.0, violation_ratio=1.0)
    feed(lc, VIOLATION, start + 1.0, violation_ratio=1.0)
    feed(lc, VIOLATION, start + 2.0, violation_ratio=1.0)
    assert lc.state is OperatorState.ALARM_LATCHED
    return start + 2.0


def recover(lc: IncidentLifecycle, start: float) -> float:
    """Feed enough SAFE frames to reach SAFE_RECOVERED; return that time."""

    feed(lc, SAFE, start + 0.5)
    feed(lc, SAFE, start + 1.5)
    feed(lc, SAFE, start + 2.5)
    assert lc.incident is not None and lc.incident.safe_recovered
    # The video loop is the only authority allowed to report a recovery; the
    # reset gate refuses an incident nobody observed as recovered.
    assert lc.mark_onboard_verified() == lc.incident.incident_id
    return start + 2.5


def ack(lc: IncidentLifecycle, now: float) -> tuple[bool, str]:
    """Press ACK, then apply the board's answer.

    The laptop no longer treats its own keypress as an acknowledgement: it
    queues ``ACKNOWLEDGE <id>`` and waits. Helpers simulate the firmware
    replying ``OK ACKNOWLEDGED`` for the incident the laptop asked about.
    """

    incident_id = lc.incident.incident_id if lc.incident else None
    ok, detail = lc.acknowledge(now)
    if ok and detail == "OK":
        lc.confirm_acknowledge(Reply(True, "ACKNOWLEDGED"), now, incident_id)
    return ok, detail


def fully_condition(lc: IncidentLifecycle) -> None:
    """Satisfy acknowledge + safe recovery so a reset should be accepted."""

    latch_at = latch_incident(lc)
    ok, _ = ack(lc, latch_at + 0.1)
    assert ok
    recover(lc, latch_at)


# ---------------------------------------------------------------------------
# 1-4: startup and target presence
# ---------------------------------------------------------------------------


def test_01_startup_does_not_claim_safe():
    """Requirement 1: startup must never present SAFE.

    The operator may not be in frame yet. Reporting SAFE here would be a
    fabricated observation and would also produce a green lamp with nobody
    monitored.
    """

    lc = IncidentLifecycle()
    assert lc.state is OperatorState.STARTUP

    lc.start(0.0)

    assert lc.state is OperatorState.WAITING_FOR_TARGET
    assert lc.status().alarm is AlarmIntent.WAITING


def test_02_startup_sends_no_alarm_command():
    """Requirement 2: no ALARM, SILENCE or reset is implied at startup."""

    lc = IncidentLifecycle()
    lc.start(0.0)

    assert lc.serial_state == "WAITING"
    assert [t.event for t in lc.transitions] == [Event.STARTUP_COMPLETE]


def test_03_unknown_never_becomes_safe():
    """Requirement 3: repeated UNKNOWN frames stay WAITING_FOR_TARGET.

    This is the anti-masking requirement. A detector that returns nothing is
    returning no evidence, not evidence of safety.
    """

    lc = make_lifecycle()

    for second in range(10):
        feed(lc, UNKNOWN, float(second))

    assert lc.state is OperatorState.WAITING_FOR_TARGET
    assert lc.status().alarm is AlarmIntent.WAITING


def test_04_single_safe_frame_reaches_safe():
    """Requirement 4: one real SAFE frame is enough to green the lamp."""

    lc = make_lifecycle()

    feed(lc, SAFE, 1.0)

    assert lc.state is OperatorState.SAFE
    assert lc.status().alarm is AlarmIntent.SILENT
    # Nothing is open, so there is no lifecycle command to send at all.
    assert lc.serial_state == "WAITING"


# ---------------------------------------------------------------------------
# 5-8: pending confirmation
# ---------------------------------------------------------------------------


def test_05_violation_below_threshold_does_not_confirm():
    """Requirement 5: 2 s of violation must not latch before the timer expires."""

    lc = make_lifecycle(confirmation_seconds=2.0)

    feed(lc, VIOLATION, 0.0, violation_ratio=1.0)
    feed(lc, VIOLATION, 1.0, violation_ratio=1.0)
    feed(lc, VIOLATION, 1.9, violation_ratio=1.0)

    assert lc.state is OperatorState.VIOLATION_PENDING
    assert lc.incident is None
    # A pending confirmation has not earned the alarm: lamp green, buzzer off.
    assert lc.status().alarm is AlarmIntent.SILENT


def test_06_confirmation_latches_at_threshold():
    """Requirement 6: at the threshold the incident latches and gets an id."""

    lc = make_lifecycle(confirmation_seconds=2.0)

    feed(lc, VIOLATION, 0.0, violation_ratio=1.0)
    feed(lc, VIOLATION, 1.0, violation_ratio=1.0)
    feed(lc, VIOLATION, 2.0, violation_ratio=1.0)

    assert lc.state is OperatorState.ALARM_LATCHED
    assert lc.incident is not None
    assert lc.incident.incident_id == 1
    assert lc.status().alarm is AlarmIntent.SOUNDING


def test_07_confirmed_after_seconds_is_frozen():
    """Requirement 7: the measured duration survives later frames.

    Regression guard for the reported bug where the overlay showed 0.00 s right
    after confirmation.
    """

    lc = make_lifecycle(confirmation_seconds=2.0)

    feed(lc, VIOLATION, 0.0, violation_ratio=1.0)
    feed(lc, VIOLATION, 1.0, violation_ratio=1.0)
    feed(lc, VIOLATION, 2.0, violation_ratio=1.0)

    frozen = lc.status().confirmed_after_seconds
    assert frozen is not None and frozen >= 2.0

    # Several more violation frames must not overwrite it.
    feed(lc, VIOLATION, 3.0, violation_ratio=1.0)
    feed(lc, VIOLATION, 9.0, violation_ratio=1.0)

    assert lc.status().confirmed_after_seconds == frozen


def test_08_pending_violation_cancelled_by_safe():
    """Requirement 8: a SAFE frame cancels a pending confirmation."""

    lc = make_lifecycle(confirmation_seconds=2.0)

    feed(lc, VIOLATION, 0.0, violation_ratio=1.0)
    feed(lc, VIOLATION, 1.0, violation_ratio=1.0)
    assert lc.state is OperatorState.VIOLATION_PENDING

    feed(lc, SAFE, 1.2)

    assert lc.state is OperatorState.SAFE
    assert lc.incident is None
    assert Event.VIOLATION_CANCELLED in {t.event for t in lc.transitions}


# ---------------------------------------------------------------------------
# 9-12: the latch must be sticky
# ---------------------------------------------------------------------------


def test_09_safe_frames_never_clear_a_latched_incident():
    """Requirement 9: 60 s of SAFE frames cannot clear the alarm by itself."""

    lc = make_lifecycle()
    latch_at = latch_incident(lc)

    assert lc.state is OperatorState.ALARM_LATCHED
    assert lc.status().alarm is AlarmIntent.SOUNDING

    for tenth in range(600):
        feed(lc, SAFE, latch_at + 0.1 * tenth)

    # Reached recovery, but the incident is still open and the lamp is still red.
    assert lc.incident is not None
    assert lc.state in INCIDENT_STATES
    assert lc.status().alarm is AlarmIntent.SILENCED


def test_10_unknown_during_incident_does_not_clear():
    """Requirement 10: UNKNOWN cannot be used to escape an incident."""

    lc = make_lifecycle()
    latch_at = latch_incident(lc)

    for tenth in range(600):
        feed(lc, UNKNOWN, latch_at + 0.1 * tenth)

    assert lc.state is OperatorState.ALARM_LATCHED
    assert lc.incident is not None


def test_11_safe_clear_pending_requires_continuous_safe():
    """Requirement 11: UNKNOWN interrupts safe-clear progress.

    Clearing needs *continuous* verified goggles. Allowing UNKNOWN to count
    would let a partial occlusion plus a lucky frame reach recovery.
    """

    lc = make_lifecycle(clearance_seconds=2.0)
    latch_at = latch_incident(lc)

    feed(lc, SAFE, latch_at + 0.5)
    feed(lc, SAFE, latch_at + 1.0)
    assert lc.state is OperatorState.SAFE_CLEAR_PENDING

    # Interrupt with UNKNOWN, then resume: the clock has to restart.
    feed(lc, UNKNOWN, latch_at + 1.2)
    assert lc.state is OperatorState.ALARM_LATCHED
    assert lc.incident is not None and not lc.incident.safe_recovered

    feed(lc, SAFE, latch_at + 1.5)
    feed(lc, SAFE, latch_at + 2.0)
    assert lc.state is OperatorState.SAFE_CLEAR_PENDING
    assert lc.incident is not None and not lc.incident.safe_recovered


def test_12_renewed_violation_rearms_audible_warning():
    """Requirement 12: a fresh violation after recovery must buzz again.

    The episode stays open with the same id, but the audible warning returns,
    because the person is unshielded again.
    """

    lc = make_lifecycle()
    latch_at = latch_incident(lc)
    ack(lc, latch_at + 0.1)
    recover(lc, latch_at)

    assert lc.state is OperatorState.ACKNOWLEDGED
    assert lc.status().alarm is AlarmIntent.SILENCED

    feed(lc, VIOLATION, latch_at + 3.0, violation_ratio=1.0)

    assert lc.state is OperatorState.ALARM_LATCHED
    assert lc.status().alarm is AlarmIntent.SOUNDING
    assert lc.rearm_count == 1
    assert lc.incident is not None and lc.incident.incident_id == 1


# ---------------------------------------------------------------------------
# 13-17: acknowledge semantics
# ---------------------------------------------------------------------------


def test_13_acknowledge_silences_buzzer_but_keeps_red():
    """Requirement 13: acknowledge is not a reset."""

    lc = make_lifecycle()
    latch_at = latch_incident(lc)

    ok, detail = ack(lc, latch_at + 0.1)

    assert ok and detail == "OK"
    assert lc.state is OperatorState.ACKNOWLEDGED
    assert lc.status().alarm is AlarmIntent.SILENCED
    assert lc.status().acknowledged is True
    assert lc.incident is not None


def test_14_acknowledge_without_incident_is_rejected():
    """Requirement 14: acknowledge needs something to acknowledge."""

    lc = make_lifecycle()

    ok, detail = ack(lc, 1.0)

    assert not ok
    assert detail == "NO_INCIDENT"
    assert lc.state is OperatorState.WAITING_FOR_TARGET


def test_15_acknowledge_is_idempotent():
    """Requirement 15: a second acknowledge must not disturb the state."""

    lc = make_lifecycle()
    latch_at = latch_incident(lc)
    ack(lc, latch_at + 0.1)

    ok, detail = ack(lc, latch_at + 0.2)

    assert ok and detail == "ALREADY_ACKNOWLEDGED"
    assert lc.state is OperatorState.ACKNOWLEDGED


def test_16_recovered_before_acknowledged_then_acknowledged():
    """Requirement 16: order must not matter for acknowledge vs recovery."""

    lc = make_lifecycle()
    latch_at = latch_incident(lc)

    recover(lc, latch_at)
    assert lc.state is OperatorState.SAFE_RECOVERED

    ok, _ = ack(lc, latch_at + 3.0)

    assert ok
    assert lc.state is OperatorState.ACKNOWLEDGED
    assert lc.status().safe_recovered is True


def test_17_recovery_after_acknowledge_moves_to_acknowledged():
    """Requirement 17: recovering last still lands in ACKNOWLEDGED."""

    lc = make_lifecycle()
    latch_at = latch_incident(lc)

    ack(lc, latch_at + 0.1)
    assert lc.state is OperatorState.ACKNOWLEDGED

    recover(lc, latch_at)

    assert lc.state is OperatorState.ACKNOWLEDGED
    assert lc.status().safe_recovered is True


# ---------------------------------------------------------------------------
# 18-24: the five reset preconditions
# ---------------------------------------------------------------------------


def test_18_reset_rejected_without_incident():
    """Requirement 18: nothing to reset."""

    lc = make_lifecycle()

    ok, blocker = lc.reset(1.0)

    assert not ok
    assert blocker is ResetBlocker.NO_INCIDENT
    assert Event.RESET_REJECTED in {t.event for t in lc.transitions}


def test_19_reset_rejected_without_acknowledge():
    """Requirement 19: recovery alone is not consent to clear the lamp."""

    lc = make_lifecycle()
    latch_at = latch_incident(lc)
    recover(lc, latch_at)

    ok, blocker = lc.reset(latch_at + 3.0)

    assert not ok
    assert blocker is ResetBlocker.NOT_ACKNOWLEDGED
    assert lc.incident is not None


def test_20_reset_rejected_without_safe_recovery():
    """Requirement 20: acknowledge alone proves review, not goggles."""

    lc = make_lifecycle()
    latch_at = latch_incident(lc)
    ack(lc, latch_at + 0.1)

    ok, blocker = lc.reset(latch_at + 0.2)

    assert not ok
    assert blocker is ResetBlocker.NOT_SAFE_RECOVERED
    assert lc.incident is not None


def test_21_reset_rejected_when_health_invalid():
    """Requirement 21: a failing camera must not be able to reset an incident."""

    lc = make_lifecycle()
    fully_condition(lc)

    lc.set_health(camera_ok=False, model_ok=True, serial_ok=True, now=10.0)
    assert lc.state is OperatorState.FAULT

    ok, blocker = lc.reset(10.1)

    assert not ok
    assert blocker is ResetBlocker.HEALTH_INVALID


def test_22_reset_rejected_when_link_invalid():
    """Requirement 22: no link, no claim of a clean reset."""

    lc = make_lifecycle()
    fully_condition(lc)

    lc.set_health(camera_ok=True, model_ok=True, serial_ok=False, now=10.0)

    ok, blocker = lc.reset(10.1)

    assert not ok
    assert blocker is ResetBlocker.LINK_INVALID


def test_23_reset_accepted_with_all_five_conditions():
    """Requirement 23: the happy path clears everything and re-arms."""

    lc = make_lifecycle()
    fully_condition(lc)

    assert lc.reset_blocker() is ResetBlocker.NONE
    ok, blocker = lc.reset(10.0)

    assert ok
    assert blocker is ResetBlocker.NONE
    assert lc.incident is None
    assert lc.state is OperatorState.WAITING_FOR_TARGET
    assert lc.status().alarm is AlarmIntent.WAITING
    assert lc.status().confirmed_after_seconds is None


def test_24_second_incident_gets_a_new_id():
    """Requirement 24: ids increment, so a log can tell incidents apart."""

    lc = make_lifecycle()

    first_latch = latch_incident(lc, start=0.0)
    fully = lc.incident
    assert fully is not None and fully.incident_id == 1
    ack(lc, first_latch + 0.1)
    recover(lc, first_latch)
    assert lc.reset(first_latch + 3.0)[0]

    second_latch = latch_incident(lc, start=20.0)

    assert lc.incident is not None
    assert lc.incident.incident_id == 2
    assert second_latch > first_latch


# ---------------------------------------------------------------------------
# 25: fault handling and the transition-only event log
# ---------------------------------------------------------------------------


def test_25_fault_overrides_and_recovers_without_clearing_incident():
    """Requirement 25: FAULT wins over everything and preserves the incident."""

    lc = make_lifecycle()
    latch_at = latch_incident(lc)

    lc.set_health(camera_ok=False, model_ok=True, serial_ok=True, now=5.0, reason="CAMERA_UNAVAILABLE")

    assert lc.state is OperatorState.FAULT
    assert lc.status().alarm is AlarmIntent.FAULT
    assert lc.fault_reason == "CAMERA_UNAVAILABLE"
    assert lc.incident is not None

    # Health restored: three consecutive healthy frames clear the fault, and the
    # incident comes back latched rather than silently gone.
    for frame in range(IncidentLifecycle.FAULT_CLEAR_FRAMES):
        lc.set_health(camera_ok=True, model_ok=True, serial_ok=True, now=6.0 + frame)

    assert lc.state is OperatorState.ALARM_LATCHED
    assert lc.incident is not None and lc.incident.incident_id == 1
    assert latch_at < 6.0


def test_26_event_log_records_transitions_only():
    """Event log must stay bounded: 300 identical frames, few entries."""

    lc = make_lifecycle()
    latch_at = latch_incident(lc)

    before = len(lc.transitions)
    for tenth in range(300):
        feed(lc, VIOLATION, latch_at + 0.1 * tenth, violation_ratio=1.0)
    after = len(lc.transitions)

    assert after == before, f"per-frame logging: {after - before} extra entries"


def test_27_serial_intent_never_clears_red_during_incident():
    """The serial intent for every incident state must keep red on.

    This is the guard that stops a future refactor from mapping
    SAFE_RECOVERED onto a bare ``SAFE`` command, which on the firmware would
    extinguish the lamp before anyone acknowledged the incident.
    """

    lc = make_lifecycle()
    latch_at = latch_incident(lc)

    assert lc.serial_state == "INCIDENT 1"

    ack(lc, latch_at + 0.1)
    assert lc.serial_state == "ACKNOWLEDGE 1"

    recover(lc, latch_at)
    assert lc.serial_state == "ACKNOWLEDGE 1"

    # Red must still be asserted; nothing may fall back to a green preset.
    assert lc.serial_state not in BARE_PRESETS


def test_28_recovery_does_not_emit_safe_command():
    """No transition before a reset may produce the SAFE serial command."""

    lc = make_lifecycle()
    latch_at = latch_incident(lc)
    ack(lc, latch_at + 0.1)
    recover(lc, latch_at)

    commands = [
        cmd
        for cmd in (lc.serial_command_for_transition(t) for t in lc.transitions)
        if cmd is not None
    ]

    assert commands
    assert not BARE_PRESETS.intersection(commands)
    assert any("INCIDENT" in cmd for cmd in commands)


@pytest.mark.parametrize(
    "state",
    [
        OperatorState.ALARM_LATCHED,
        OperatorState.SAFE_CLEAR_PENDING,
        OperatorState.SAFE_RECOVERED,
        OperatorState.ACKNOWLEDGED,
    ],
)
def test_29_incident_states_are_exactly_the_red_lamp_states(state):
    """The incident state set and the red-lamp states must be the same set."""

    assert state in INCIDENT_STATES
    assert state is not OperatorState.SAFE
    assert state is not OperatorState.WAITING_FOR_TARGET


def test_30_acknowledged_via_lifecycle_matches_serial_state():
    """Red stays on for every acknowledged incident variant."""

    for acknowledge_first in (True, False):
        lc = make_lifecycle()
        latch_at = latch_incident(lc)

        if acknowledge_first:
            ack(lc, latch_at + 0.1)
            recover(lc, latch_at)
        else:
            recover(lc, latch_at)
            ack(lc, latch_at + 3.0)

        assert lc.state is OperatorState.ACKNOWLEDGED
        assert lc.status().alarm is AlarmIntent.SILENCED
        assert lc.serial_state == "ACKNOWLEDGE 1"


# ---------------------------------------------------------------------------
# 31-34: two-step reset handshake
#
# The laptop mints RESET_READY <id>; the firmware echoes OK RESET_READY; only
# then may the laptop send RESET. Neither side alone can clear the lamp, so
# these tests pin down both the happy path and every refusal.
# ---------------------------------------------------------------------------


class Reply:
    """Stand-in for the serial layer's parsed firmware reply."""

    def __init__(self, ok: bool, code: str) -> None:
        self.ok = ok
        self.code = code


def test_31_request_reset_mints_token_without_clearing_incident():
    lc = make_lifecycle()
    fully_condition(lc)

    assert lc.pending_reset_command is None
    ok, reason = lc.request_reset(lc.last_now + 0.1)

    assert ok is True
    assert reason == "RESET_PENDING"
    # The local latch is still open: only the firmware can close it.
    assert lc.pending_reset_command == f"RESET_READY {lc.incident.incident_id}"
    assert lc.state is OperatorState.ACKNOWLEDGED
    assert lc.incident is not None
    assert lc.status().alarm is AlarmIntent.SILENCED


def test_32_reset_handshake_completes_after_firmware_accepts():
    lc = make_lifecycle()
    fully_condition(lc)
    lc.request_reset(lc.last_now + 0.1)

    # RESET_READY accepted -> the laptop now sends the bare RESET.
    assert lc.resolve_pending_command(Reply(True, "RESET_READY")) == "RESET"
    assert lc.pending_reset_command == "RESET"

    # RESET accepted -> incident closed, so there is nothing left to re-assert.
    assert lc.resolve_pending_command(Reply(True, "RESET_ACCEPTED")) is None
    assert lc.pending_reset_command is None
    assert lc.incident is None
    assert lc.state is OperatorState.WAITING_FOR_TARGET
    assert lc.status().incident_open is False
    assert lc.reset_blocker() is ResetBlocker.NO_INCIDENT


def test_33_reset_refusal_releases_token_and_reasserts_state():
    """A refused RESET must not leave the firmware in READY_TO_RESET."""

    lc = make_lifecycle()
    fully_condition(lc)
    lc.request_reset(lc.last_now + 0.1)

    restored = lc.resolve_pending_command(
        Reply(False, "ERR RESET_REJECTED NOT_SAFE_RECOVERED")
    )

    # Re-assert our own steady state so the two ends cannot drift apart.
    assert restored == lc.serial_state == "ACKNOWLEDGE 1"
    assert lc.pending_reset_command is None
    # A refusal is a no-op on the incident itself.
    assert lc.incident is not None
    assert lc.state is OperatorState.ACKNOWLEDGED


def test_34_reset_handshake_ignores_replies_when_no_request_in_flight():
    lc = make_lifecycle()
    fully_condition(lc)

    # Nothing was requested, so a stray reply must not clear anything.
    assert lc.resolve_pending_command(Reply(True, "RESET_ACCEPTED")) is None
    assert lc.incident is not None

    # A refusal for an unrelated command likewise changes nothing.
    assert lc.resolve_pending_command(Reply(False, "ERR UNKNOWN_COMMAND")) is None
    assert lc.incident is not None


def test_35_completed_handshake_does_not_re_send_reset():
    """The handshake already delivered RESET; the drain must not repeat it.

    Regression test: ``_advance_after_firmware_reset`` logs a RESET_ACCEPTED
    transition, which maps to the ``RESET`` command. Without marking it drained
    the very next ``take_pending_command()`` would send a second RESET for an
    incident the firmware had just closed, which the board answers with
    ``ERR RESET_REJECTED NO_INCIDENT``.
    """

    lc = make_lifecycle()
    fully_condition(lc)

    # Real operator flow: INCIDENT <id> and ACKNOWLEDGE <id> were drained as they happened.
    assert lc.take_pending_command() == "INCIDENT 1"
    assert lc.take_pending_command() == "ACKNOWLEDGE 1"

    lc.request_reset(lc.last_now + 0.1)
    assert lc.resolve_pending_command(Reply(True, "RESET_READY")) == "RESET"
    assert lc.resolve_pending_command(Reply(True, "RESET_ACCEPTED")) is None

    # Nothing is outstanding: the RESET half of the handshake was already sent.
    assert lc.take_pending_command() is None


# ---------------------------------------------------------------------------
# 36-38: onboard recovery verification (the crash the live run exposed)
# ---------------------------------------------------------------------------


def test_36_mark_onboard_verified_is_idempotent():
    """Repeated SAFE frames must not re-record or bump anything.

    The video loop calls this on every frame where ``safe_recovered`` holds, so
    it has to be a cheap no-op the second time.
    """

    lc = make_lifecycle()
    latch_at = latch_incident(lc)
    recover(lc, latch_at)

    incident_id = lc.incident.incident_id
    assert lc.mark_onboard_verified() == incident_id
    assert lc.mark_onboard_verified() == incident_id
    assert lc.onboard_verified_incident == incident_id


def test_37_mark_onboard_verified_needs_an_incident():
    """Nothing is recorded without an incident, or before recovery."""

    lc = make_lifecycle()
    assert lc.mark_onboard_verified() is None

    latch_at = latch_incident(lc)
    # Confirmed but not recovered: the detector has not proven anything yet.
    assert lc.mark_onboard_verified() is None
    assert lc.onboard_verified_incident is None


def test_38_reset_requires_the_recovery_to_be_verified():
    """A missed SAFE_RECOVERED observation must not become a clearable incident.

    Regression test: the demo called ``mark_onboard_verified`` on a lifecycle
    that did not define it, so every recovery crashed the run. The gate is now
    enforced here too, so the failure surfaces as a refused reset rather than an
    AttributeError in the camera loop.
    """

    lc = make_lifecycle()
    latch_at = latch_incident(lc)
    ok, _ = ack(lc, latch_at + 0.1)
    assert ok

    # Reach safe recovery without going through the helper that verifies it.
    feed(lc, SAFE, latch_at + 0.5)
    feed(lc, SAFE, latch_at + 1.5)
    feed(lc, SAFE, latch_at + 2.5)
    assert lc.incident.safe_recovered

    assert lc.reset_blocker() is ResetBlocker.NOT_SAFE_RECOVERED
    accepted, blocker = lc.reset(lc.last_now + 0.1)
    assert not accepted
    assert blocker is ResetBlocker.NOT_SAFE_RECOVERED
    assert lc.incident is not None

    assert lc.mark_onboard_verified() == lc.incident.incident_id
    assert lc.reset_blocker() is ResetBlocker.NONE
    accepted, blocker = lc.reset(lc.last_now + 0.2)
    assert accepted and blocker is ResetBlocker.NONE


def test_39_new_incident_starts_unverified():
    """The recovery proof is per incident, not per process."""

    lc = make_lifecycle()
    fully_condition(lc)
    first_id = lc.incident.incident_id
    assert lc.onboard_verified_incident == first_id

    accepted, _ = lc.reset(lc.last_now + 0.1)
    assert accepted
    assert lc.onboard_verified_incident is None

    latch_at = latch_incident(lc, start=10.0)
    assert lc.incident.incident_id != first_id
    assert lc.onboard_verified_incident is None

    ok, _ = ack(lc, latch_at + 0.1)
    assert ok
    feed(lc, SAFE, latch_at + 0.5)
    feed(lc, SAFE, latch_at + 1.5)
    feed(lc, SAFE, latch_at + 2.5)
    assert lc.reset_blocker() is ResetBlocker.NOT_SAFE_RECOVERED
