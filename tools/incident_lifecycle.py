"""Operator incident lifecycle — the 9-state machine behind the live demo.

Why this module exists
----------------------
The detector answers "what is in this frame?" and ``TemporalFilter`` answers
"has that been true long enough to be real?". Neither of them knows anything
about *operators*. Someone has to decide when the buzzer is allowed to start,
when it is allowed to stop, whether a human has actually looked at the incident,
and when the red lamp may finally go out. Those are separate concerns with
separate failure modes, and mixing them into the video loop is what let a
confirmed incident show a 0.00 s timer.

This module is that layer, and it is deliberately pure:

* no camera, no serial, no model, no torch
* time is injected (``time.monotonic()`` values supplied by the caller)
* every decision is a plain function of the previous state plus one event

That purity is what makes the 25 lifecycle tests possible without hardware, and
it is also what lets the keyboard controls, the future dashboard and the future
Blynk UI share one implementation. They all call :meth:`IncidentLifecycle.acknowledge`
and :meth:`IncidentLifecycle.reset` rather than re-implementing the rules.

The nine states
---------------
::

    STARTUP ──► WAITING_FOR_TARGET ──► SAFE ◄──────────────┐
                       │                 │                 │
                       │                 │ VIOLATION       │ RESET accepted
                       │◄─── UNKNOWN ────┤ (pending)       │
                       │                 ▼                 │
                       │        VIOLATION_PENDING ─────────┤
                       │                 │ 2 s confirmed    │
                       │                 ▼                 │
                       │           ALARM_LATCHED ──────────┤
                       │                 │ SAFE frames      │
                       │                 ▼                 │
                       │        SAFE_CLEAR_PENDING         │
                       │                 │ clearance met    │
                       │                 ▼                 │
                       └────────        SAFE_RECOVERED     │
                                          │ ACKNOWLEDGE    │
                                          ▼                │
                                     ACKNOWLEDGED ─────────┘

    FAULT ◄── any state, when the camera/model/link stops being trustworthy

Two invariants drive the whole design:

1. **The alarm is sticky.** Once latched it stays latched until an explicit,
   fully-conditioned reset. Leaving the frame, looking away, or a run of SAFE
   frames never clears it, because those are exactly the situations a person
   trying to evade detection would produce.
2. **UNKNOWN is never SAFE.** A missing detection is missing information. It can
   hold or interrupt a pending confirmation, but it can never manufacture a SAFE
   observation, and it can never be reported to the operator as safe.

Why the reset has five preconditions
------------------------------------
Resetting is the only operation that extinguishes the red lamp, so it is the one
operation worth defending. All five must hold:

``INCIDENT_ACTIVE``
    There is an open incident. Resetting nothing is meaningless.
``ACKNOWLEDGED``
    A human has seen the incident. Without this, an automated reset could erase
    the record of a violation nobody ever looked at.
``SAFE_RECOVERED``
    Goggles were *observed*, continuously, for the full clearance period after
    the incident. ``ACKNOWLEDGED`` alone only proves someone clicked.
``HEALTH_VALID``
    The camera and model are currently trustworthy. A frozen or failing model
    must not be able to look "safe".
``LINK_VALID``
    The ESP32 is actually reachable. Otherwise the lamp would stay red while the
    software claimed a clean reset.

:meth:`IncidentLifecycle.reset` returns the *first* missing condition, so the
operator is told what is actually blocking them instead of a generic refusal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OperatorState(str, Enum):
    """The nine operator-visible lifecycle states."""

    STARTUP = "STARTUP"
    WAITING_FOR_TARGET = "WAITING_FOR_TARGET"
    SAFE = "SAFE"
    VIOLATION_PENDING = "VIOLATION_PENDING"
    ALARM_LATCHED = "ALARM_LATCHED"
    SAFE_CLEAR_PENDING = "SAFE_CLEAR_PENDING"
    SAFE_RECOVERED = "SAFE_RECOVERED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    FAULT = "FAULT"


class Event(str, Enum):
    """Transition names used in the append-only event log.

    Logging transitions rather than frames keeps the log readable and bounded:
    a five minute session at 30 fps is 9,000 frames but only a handful of
    transitions.
    """

    STARTUP_COMPLETE = "STARTUP_COMPLETE"
    TARGET_LOST = "TARGET_LOST"
    TARGET_FOUND_SAFE = "TARGET_FOUND_SAFE"
    VIOLATION_PENDING = "VIOLATION_PENDING"
    VIOLATION_CANCELLED = "VIOLATION_CANCELLED"
    ALARM_LATCHED = "ALARM_LATCHED"
    ALARM_REARMED = "ALARM_REARMED"
    SAFE_CLEAR_PENDING = "SAFE_CLEAR_PENDING"
    SAFE_CLEAR_CANCELLED = "SAFE_CLEAR_CANCELLED"
    SAFE_RECOVERED = "SAFE_RECOVERED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    ACKNOWLEDGE_REQUESTED = "ACKNOWLEDGE_REQUESTED"
    ACKNOWLEDGE_REJECTED = "ACKNOWLEDGE_REJECTED"
    RESET_ACCEPTED = "RESET_ACCEPTED"
    RESET_REJECTED = "RESET_REJECTED"
    FAULT_RAISED = "FAULT_RAISED"
    FAULT_CLEARED = "FAULT_CLEARED"


class ResetBlocker(str, Enum):
    """Why a reset was refused. Ordered as checked."""

    NONE = "NONE"
    NO_INCIDENT = "NO_INCIDENT"
    NOT_ACKNOWLEDGED = "NOT_ACKNOWLEDGED"
    NOT_SAFE_RECOVERED = "NOT_SAFE_RECOVERED"
    HEALTH_INVALID = "HEALTH_INVALID"
    LINK_INVALID = "LINK_INVALID"


#: States in which an incident is still open. Used by the serial layer to decide
#: whether the firmware may be told ``SAFE`` at all.
INCIDENT_STATES: frozenset[OperatorState] = frozenset(
    {
        OperatorState.ALARM_LATCHED,
        OperatorState.SAFE_CLEAR_PENDING,
        OperatorState.SAFE_RECOVERED,
        OperatorState.ACKNOWLEDGED,
    }
)


class AlarmIntent(str, Enum):
    """What the physical alarm should be doing right now.

    The demo turns this into serial commands; the tests assert on it directly.
    It is deliberately a small closed set so no code path can invent a fourth
    possibility such as "quiet but not latched", which is how an alarm silently
    stops being an alarm.
    """

    SILENT = "SILENT"          # green only, nothing wrong
    WAITING = "WAITING"        # nothing on yet; no target or starting up
    SOUNDING = "SOUNDING"      # red + buzzer, needs a human
    SILENCED = "SILENCED"      # red only, buzzer acknowledged away
    FAULT = "FAULT"            # parked; we do not trust our own outputs


@dataclass(frozen=True)
class LifecycleStatus:
    """Immutable snapshot for the overlay and the serial layer."""

    state: OperatorState
    alarm: AlarmIntent
    incident_id: int | None
    violation_ratio: float
    pending_elapsed: float | None = None
    confirmed_after_seconds: float | None = None
    safe_clear_elapsed: float | None = None
    safe_clear_required: float | None = None
    acknowledged: bool = False
    safe_recovered: bool = False
    fault_reason: str | None = None
    rearm_count: int = 0

    @property
    def incident_open(self) -> bool:
        return self.state in INCIDENT_STATES or self.incident_id is not None


@dataclass(frozen=True)
class Transition:
    """One logged state change. Emitted only when something actually changed."""

    at_monotonic: float
    from_state: OperatorState
    to_state: OperatorState
    event: Event
    incident_id: int | None = None
    detail: str = ""


@dataclass
class _Incident:
    incident_id: int
    #: True only once the firmware has actually answered ``OK ACKNOWLEDGED``
    #: for this incident id. A keypress sets ``acknowledge_pending`` instead:
    #: the laptop never gets to decide on its own that a human was seen.
    acknowledged: bool = False
    #: An ACKNOWLEDGE <id> was sent and its reply has not arrived yet.
    acknowledge_pending: bool = False
    safe_recovered: bool = False


class IncidentLifecycle:
    """Operator-level lifecycle layered on top of the frame-level filter.

    The class consumes :class:`~tools.live_eye_shield_demo.Observation`-shaped
    values by duck typing (anything with ``.name`` of ``UNKNOWN``/``SAFE``/
    ``VIOLATION``) so importing this module never pulls in torch, cv2 or the
    serial stack.
    """

    #: Frames of consecutive trustworthy negatives before a fault clears.
    FAULT_CLEAR_FRAMES = 3

    def __init__(
        self,
        confirmation_seconds: float = 2.0,
        clearance_seconds: float = 2.0,
    ) -> None:
        self.confirmation_seconds = confirmation_seconds
        self.clearance_seconds = clearance_seconds

        self.state = OperatorState.STARTUP
        self.started = False

        self.incident: _Incident | None = None
        self.next_incident_id = 1
        self.rearm_count = 0

        # Timers are monotonic timestamps from the caller.
        self.pending_since: float | None = None
        self.confirmed_after_seconds: float | None = None
        self.safe_since: float | None = None

        self.pending_elapsed: float | None = None
        self.safe_clear_elapsed: float | None = None

        self.fault_reason: str | None = None
        self._healthy_frames = 0

        # Health/link flags maintained by the caller each frame.
        self.camera_ok = True
        self.model_ok = True
        self.serial_ok = True

        self.violation_ratio = 0.0
        self.transitions: list[Transition] = []
        # Index of the transition the next serial command must be derived
        # from. The caller drains commands with take_pending_command(), so the
        # log stays append-only and complete while each transition's command is
        # sent exactly once.
        self._next_command_index = 0
        # Set while a RESET handshake is in flight; cleared by the reply.
        self.pending_reset_command: str | None = None
        # Set by the video loop, never by a key handler: proof that the
        # detector itself saw continuous goggles for this incident.
        self.onboard_verified_incident: int | None = None
        self.last_now: float = 0.0

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _name(observation: object) -> str:
        return str(getattr(observation, "name", observation)).upper()

    def _health_ok(self) -> bool:
        return self.camera_ok and self.model_ok

    def _emit(
        self,
        event: Event,
        now: float,
        to_state: OperatorState,
        detail: str = "",
    ) -> None:
        if to_state is self.state and not detail:
            return

        self.transitions.append(
            Transition(
                at_monotonic=now,
                from_state=self.state,
                to_state=to_state,
                event=event,
                incident_id=self.incident.incident_id if self.incident else None,
                detail=detail,
            )
        )
        self.state = to_state
        self.last_now = now

    def _alarm_intent(self) -> AlarmIntent:
        if self.state is OperatorState.FAULT:
            return AlarmIntent.FAULT
        if self.state is OperatorState.ALARM_LATCHED:
            return AlarmIntent.SOUNDING
        if self.state in INCIDENT_STATES:
            # Red stays on for the whole life of an incident. Only the buzzer is
            # ever silenced, and only after a human acknowledged.
            return AlarmIntent.SILENCED
        if self.state in (OperatorState.STARTUP, OperatorState.WAITING_FOR_TARGET):
            return AlarmIntent.WAITING
        # SAFE and VIOLATION_PENDING both keep the lamp green: a pending
        # confirmation has not earned the alarm yet.
        return AlarmIntent.SILENT

    def status(self) -> LifecycleStatus:
        # Same reasoning as the frozen TemporalStatus: each clock is only
        # populated for the state that actually owns it, so the overlay cannot
        # render the wrong timer.
        pending_elapsed = (
            self.pending_elapsed
            if self.state is OperatorState.VIOLATION_PENDING
            else None
        )
        safe_clear_elapsed = (
            self.safe_clear_elapsed
            if self.state is OperatorState.SAFE_CLEAR_PENDING
            else None
        )

        return LifecycleStatus(
            state=self.state,
            alarm=self._alarm_intent(),
            incident_id=self.incident.incident_id if self.incident else None,
            violation_ratio=self.violation_ratio,
            pending_elapsed=pending_elapsed,
            confirmed_after_seconds=self.confirmed_after_seconds,
            safe_clear_elapsed=safe_clear_elapsed,
            safe_clear_required=self.clearance_seconds if self.incident else None,
            acknowledged=bool(self.incident and self.incident.acknowledged),
            safe_recovered=bool(self.incident and self.incident.safe_recovered),
            fault_reason=self.fault_reason,
            rearm_count=self.rearm_count,
        )

    # -- health ----------------------------------------------------------

    def set_health(
        self,
        camera_ok: bool,
        model_ok: bool,
        serial_ok: bool,
        now: float,
        reason: str = "",
    ) -> None:
        """Record subsystem health. Any loss of health raises FAULT.

        A camera that fails or a model that stops answering must not be able to
        look like a clean SAFE, so health loss wins over every other state,
        including a latched incident.
        """

        self.camera_ok = camera_ok
        self.model_ok = model_ok
        self.serial_ok = serial_ok

        if not self._health_ok():
            self._healthy_frames = 0
            if self.state is not OperatorState.FAULT:
                self.fault_reason = reason or self._default_fault_reason()
                self._emit(Event.FAULT_RAISED, now, OperatorState.FAULT, self.fault_reason)
            return

        if self.state is OperatorState.FAULT:
            self._healthy_frames += 1
            if self._healthy_frames >= self.FAULT_CLEAR_FRAMES:
                self.fault_reason = None
                # Recover to the safest honest state: an open incident goes back
                # to latched (red only, still unanswered); otherwise we are back
                # to waiting, because we have not seen a real SAFE frame yet.
                target = (
                    OperatorState.ALARM_LATCHED
                    if self.incident is not None
                    else OperatorState.WAITING_FOR_TARGET
                )
                self._emit(Event.FAULT_CLEARED, now, target)

    def _default_fault_reason(self) -> str:
        if not self.camera_ok:
            return "CAMERA_UNAVAILABLE"
        if not self.model_ok:
            return "MODEL_UNAVAILABLE"
        return "HEALTH_INVALID"

    # -- frame update ----------------------------------------------------

    def start(self, now: float) -> None:
        """Leave STARTUP once the camera and model are actually ready.

        Nothing is asserted here about the scene. The operator may not be in
        the frame at all, and claiming SAFE at startup would be a fabricated
        SAFE observation.
        """

        if self.started:
            return
        self.started = True
        self._emit(Event.STARTUP_COMPLETE, now, OperatorState.WAITING_FOR_TARGET)

    def update(
        self,
        observation: object,
        now: float,
        violation_ratio: float = 0.0,
        pending_since: float | None = None,
        safe_since: float | None = None,
    ) -> LifecycleStatus:
        """Advance the machine by one frame.

        ``pending_since``/``safe_since`` come from the frame-level filter so the
        two layers agree on when a timer started instead of each keeping its own
        slightly different clock.
        """

        if not self.started:
            self.start(now)

        self.violation_ratio = violation_ratio
        name = self._name(observation)

        if self.state is OperatorState.FAULT:
            return self.status()

        # An open incident never drops back to a non-incident state without a
        # reset. This branch is the whole latch guarantee.
        if self.incident is not None:
            return self._update_incident(name, now, pending_since, safe_since)

        return self._update_clear(name, now, pending_since)

    def _update_clear(
        self,
        name: str,
        now: float,
        pending_since: float | None,
    ) -> LifecycleStatus:
        """No open incident: track target presence and pending violations."""

        if name == "UNKNOWN":
            # Missing information. It cancels a pending confirmation rather than
            # extending it, and it is never reported as SAFE.
            if self.state is OperatorState.VIOLATION_PENDING:
                self.pending_since = None
                self._emit(Event.VIOLATION_CANCELLED, now, OperatorState.WAITING_FOR_TARGET)
            elif self.state is not OperatorState.WAITING_FOR_TARGET:
                self._emit(Event.TARGET_LOST, now, OperatorState.WAITING_FOR_TARGET)
            return self.status()

        if name == "SAFE":
            cancelled = self.state is OperatorState.VIOLATION_PENDING
            self.pending_since = None
            if cancelled:
                # An operator reading the log needs to see that a confirmation
                # was interrupted, not just that we happen to be SAFE now.
                self._emit(Event.VIOLATION_CANCELLED, now, OperatorState.SAFE)
            elif self.state is not OperatorState.SAFE:
                self._emit(Event.TARGET_FOUND_SAFE, now, OperatorState.SAFE)
            return self.status()

        # VIOLATION
        if pending_since is not None:
            self.pending_since = pending_since
        elif self.pending_since is None:
            self.pending_since = now

        if self.state is not OperatorState.VIOLATION_PENDING:
            self._emit(Event.VIOLATION_PENDING, now, OperatorState.VIOLATION_PENDING)

        elapsed = now - self.pending_since
        self.pending_elapsed = elapsed

        if elapsed >= self.confirmation_seconds:
            return self._latch(now, elapsed)

        return self.status()

    def _latch(self, now: float, elapsed: float) -> LifecycleStatus:
        self.incident = _Incident(incident_id=self.next_incident_id)
        self.next_incident_id += 1
        # A new incident starts unverified: the recovery proof belongs to the
        # incident it was observed for, not to the monitor session.
        self.onboard_verified_incident = None
        # Frozen at confirmation so later frames cannot overwrite it with 0.00,
        # which is exactly the bug the timer fix addressed at frame level.
        self.confirmed_after_seconds = elapsed
        self.pending_since = None
        self.safe_since = None
        self._emit(Event.ALARM_LATCHED, now, OperatorState.ALARM_LATCHED)
        return self.status()

    def _update_incident(
        self,
        name: str,
        now: float,
        pending_since: float | None,
        safe_since: float | None,
    ) -> LifecycleStatus:
        assert self.incident is not None

        if name == "VIOLATION":
            # A renewed violation after recovery re-arms the audible warning:
            # the episode is still open, so the drone must come back.
            if self.incident.safe_recovered or self.incident.acknowledged:
                self.incident.acknowledged = False
                self.incident.acknowledge_pending = False
                self.incident.safe_recovered = False
                self.rearm_count += 1
                self.safe_since = None
                self._emit(Event.ALARM_REARMED, now, OperatorState.ALARM_LATCHED)
                return self.status()

            if self.state is OperatorState.SAFE_CLEAR_PENDING:
                self.safe_since = None
                self._emit(Event.SAFE_CLEAR_CANCELLED, now, OperatorState.ALARM_LATCHED)
            return self.status()

        if name == "UNKNOWN":
            # UNKNOWN interrupts safe-clear progress. It must not look like the
            # person put goggles on.
            if self.state is OperatorState.SAFE_CLEAR_PENDING:
                self.safe_since = None
                self._emit(Event.SAFE_CLEAR_CANCELLED, now, OperatorState.ALARM_LATCHED)
            return self.status()

        # SAFE frames while an incident is open.
        if safe_since is not None:
            self.safe_since = safe_since
        elif self.safe_since is None:
            self.safe_since = now

        elapsed = now - self.safe_since
        self.safe_clear_elapsed = elapsed

        if elapsed >= self.clearance_seconds:
            if not self.incident.safe_recovered:
                self.incident.safe_recovered = True
                target = (
                    OperatorState.ACKNOWLEDGED
                    if self.incident.acknowledged
                    else OperatorState.SAFE_RECOVERED
                )
                self._emit(Event.SAFE_RECOVERED, now, target)
            return self.status()

        if self.state is not OperatorState.SAFE_CLEAR_PENDING:
            self._emit(Event.SAFE_CLEAR_PENDING, now, OperatorState.SAFE_CLEAR_PENDING)

        return self.status()

    # -- operator operations ---------------------------------------------

    def acknowledge(self, now: float) -> tuple[bool, str]:
        """Record that a human says they saw the incident.

        This does *not* mark the incident acknowledged. The laptop is not the
        authority on whether an alarm was answered: the board is. The keypress
        queues ``ACKNOWLEDGE <id>`` and the incident only becomes acknowledged
        when the matching ``OK ACKNOWLEDGED`` reply arrives at
        :meth:`confirm_acknowledge`.

        Treating the local keypress as sufficient was the second defect: the
        demo promoted itself to ACKNOWLEDGED on any reply it happened to read,
        including ``OK HEARTBEAT``, so the overlay could claim an alarm had been
        answered while the board was still buzzing.
        """

        if self.incident is None:
            self._emit(Event.ACKNOWLEDGE_REJECTED, now, self.state, "NO_INCIDENT")
            return False, "NO_INCIDENT"

        if self.incident.acknowledged:
            return True, "ALREADY_ACKNOWLEDGED"

        if self.incident.acknowledge_pending:
            return True, "ACKNOWLEDGE_PENDING"

        self.incident.acknowledge_pending = True

        # A *request*, not the acknowledgement. The state does not move and the
        # incident stays unanswered until the board says otherwise. The event is
        # logged so the command for the request can be drained exactly once.
        self._emit(
            Event.ACKNOWLEDGE_REQUESTED,
            now,
            self.state,
            f"incident {self.incident.incident_id}",
        )

        # Red stays on. This is the difference between acknowledge and reset.
        return True, "OK"

    def confirm_acknowledge(
        self,
        reply: object,
        now: float,
        expected_incident_id: int | None = None,
    ) -> str:
        """Apply the firmware's answer to a pending ``ACKNOWLEDGE <id>``.

        Returns one of ``CONFIRMED``, ``REJECTED``, ``IGNORED`` or
        ``NO_PENDING_ACK``. ``IGNORED`` is the important one: a reply that is
        not an acknowledgement answer must leave the laptop exactly where it
        was. That is what stops ``OK HEARTBEAT`` from ever confirming anything.
        """

        if self.incident is None or not self.incident.acknowledge_pending:
            return "NO_PENDING_ACK"

        if expected_incident_id is not None and (
            expected_incident_id != self.incident.incident_id
        ):
            # A late answer for a previous episode must not close this one.
            return "IGNORED"

        ok = bool(getattr(reply, "ok", False))
        code = str(getattr(reply, "code", "")).upper()

        if not ok:
            if not code.startswith("ACK"):
                return "IGNORED"
            self.incident.acknowledge_pending = False
            self._emit(
                Event.ACKNOWLEDGE_REJECTED,
                now,
                self.state,
                str(getattr(reply, "detail", "")) or code or "REJECTED",
            )
            return "REJECTED"

        if code != "ACKNOWLEDGED":
            return "IGNORED"

        self.incident.acknowledge_pending = False
        self.incident.acknowledged = True
        if self.state is not OperatorState.ACKNOWLEDGED:
            self._emit(Event.ACKNOWLEDGED, now, OperatorState.ACKNOWLEDGED)
        return "CONFIRMED"

    def mark_onboard_verified(self) -> int | None:
        """Record that the detector itself proved recovery for this incident.

        Called only from the camera loop, never from a key handler. Returns the
        incident id that is now verified, or None when there was nothing to
        verify. Idempotent: repeated calls return the same id.
        """

        if self.incident is None:
            return None

        if not self.incident.safe_recovered:
            return None

        self.onboard_verified_incident = self.incident.incident_id
        return self.onboard_verified_incident

    def reset_blocker(self) -> ResetBlocker:
        """The first precondition that is not met, or NONE if a reset is valid."""

        if self.incident is None:
            return ResetBlocker.NO_INCIDENT
        if not self.incident.acknowledged:
            return ResetBlocker.NOT_ACKNOWLEDGED
        if not self.incident.safe_recovered:
            return ResetBlocker.NOT_SAFE_RECOVERED
        # A missed transition must not be able to skip the observation: a
        # recovery the detector never reported is not a recovery.
        if self.onboard_verified_incident != self.incident.incident_id:
            return ResetBlocker.NOT_SAFE_RECOVERED
        if not self._health_ok():
            return ResetBlocker.HEALTH_INVALID
        if not self.serial_ok:
            return ResetBlocker.LINK_INVALID
        return ResetBlocker.NONE

    def reset(self, now: float) -> tuple[bool, ResetBlocker]:
        """Extinguish the incident, but only when all five conditions hold.

        Returns the blocker instead of a bare False so the operator is told what
        to fix. A refusal is a no-op: the incident stays latched with every flag
        intact.
        """

        blocker = self.reset_blocker()

        if blocker is not ResetBlocker.NONE:
            self._emit(
                Event.RESET_REJECTED,
                now,
                self.state,
                blocker.value,
            )
            return False, blocker

        self.incident = None
        self.pending_since = None
        self.safe_since = None
        self.confirmed_after_seconds = None
        self.onboard_verified_incident = None
        self.pending_reset_command = None
        # Back to waiting, not SAFE: we have not just observed a fresh negative,
        # so claiming SAFE would be inventing an observation.
        self._emit(Event.RESET_ACCEPTED, now, OperatorState.WAITING_FOR_TARGET)
        return True, ResetBlocker.NONE

    # -- serial intent ---------------------------------------------------

    def serial_command_for_transition(self, transition: Transition) -> str | None:
        """Map a logged transition to the single serial command it implies.

        One command per transition keeps the link traffic proportional to what
        actually happened, and it means a dropped frame cannot cause a command
        storm. ``None`` means the transition is display-only.

        Every command is a lifecycle command carrying the incident id.
        ``ALARM``/``SAFE``/``SILENCE`` are deliberately absent: they are pin
        presets on the firmware that bypass IncidentCore, which is exactly the
        defect that let a latched alarm go quiet a second later.
        """

        if transition.event in (Event.ALARM_LATCHED, Event.ALARM_REARMED):
            if transition.incident_id is not None:
                return f"INCIDENT {transition.incident_id}"
            return None

        if transition.event == Event.ACKNOWLEDGE_REQUESTED:
            # The request is what goes on the wire. ACKNOWLEDGED itself is
            # display-only: it is the board's answer, not a command.
            if transition.incident_id is not None:
                return f"ACKNOWLEDGE {transition.incident_id}"
            return None

        if transition.event == Event.RESET_ACCEPTED:
            return "RESET"

        return None

    # -- reset handshake -------------------------------------------------

    def request_reset(self, now: float) -> tuple[bool, str]:
        """Begin the reset handshake, or refuse with the blocking reason.

        This only *mints* the proof-of-recovery token. Nothing is cleared here;
        the firmware is the authority and the local latch is released only when
        it answers RESET_ACCEPTED.
        """

        blocker = self.reset_blocker()

        if blocker is not ResetBlocker.NONE:
            self._emit(Event.RESET_REJECTED, now, self.state, blocker.value)
            return False, blocker.value

        assert self.incident is not None
        # Advertise recovery first. RESET is only legal after the firmware has
        # acknowledged this token for this incident.
        self.pending_reset_command = f"RESET_READY {self.incident.incident_id}"
        return True, "RESET_PENDING"

    def resolve_pending_command(self, reply: object) -> str | None:
        """Turn a firmware reply into the next command in the reset handshake.

        The handshake is two round trips on purpose:

            laptop   RESET_READY <id>   ->  firmware  OK RESET_READY
            laptop   RESET              ->  firmware  OK RESET_ACCEPTED

        The laptop only advertises RESET_READY after its own detector proved
        recovery; the firmware only honours RESET after that token arrived for
        the *current* incident. Neither side alone can clear the lamp.

        Any refusal must re-assert the steady-state command for our own state.
        Otherwise a rejected RESET leaves the firmware in ReadyToReset while
        the laptop still shows a latched incident, and the next attempt would be
        judged against a stale token.
        """

        if self.pending_reset_command is None:
            return None

        ok = bool(getattr(reply, "ok", False))
        code = str(getattr(reply, "code", ""))

        if ok and code == "RESET_READY":
            # Firmware accepted our proof of recovery; now consume the token.
            self.pending_reset_command = "RESET"
            return "RESET"

        if ok and code == "RESET_ACCEPTED":
            self.pending_reset_command = None
            self._advance_after_firmware_reset()
            return self._steady_state_command()

        if not ok and "RESET" in code:
            # Clear the token so the next attempt is judged on its own merits,
            # and re-assert our current state so the two ends cannot drift.
            self.pending_reset_command = None
            return self._steady_state_command()

        return None

    def _steady_state_command(self) -> str | None:
        """Re-assert our state, or nothing at all when no command can say it.

        ``WAITING`` and ``FAULT`` are laptop-side descriptions, not firmware
        commands, so they must never reach the wire. Sending them would earn an
        ``ERR UNKNOWN_COMMAND`` and burn the correlation slot.
        """

        state = self.serial_state
        return None if state in ("WAITING", "FAULT") else state

    def _advance_after_firmware_reset(self) -> None:
        """Mirror the firmware's accepted reset into the local state machine."""

        self.incident = None
        self.pending_since = None
        self.safe_since = None
        self.pending_elapsed = None
        self.safe_clear_elapsed = None
        self.confirmed_after_seconds = None
        self.onboard_verified_incident = None
        # WAITING_FOR_TARGET, not SAFE: no fresh SAFE frame has been observed.
        self._emit(Event.RESET_ACCEPTED, self.last_now, OperatorState.WAITING_FOR_TARGET)
        # The RESET for this transition was the handshake itself, already
        # answered. Mark it drained so take_pending_command() cannot send a
        # second RESET against an incident the firmware has just closed.
        self._next_command_index = len(self.transitions)

    def take_pending_command(self) -> str | None:
        """Return the next serial command implied by the transition log.

        Draining rather than recomputing means a command is emitted exactly
        once per transition, including a repeat ALARM when an acknowledged
        incident re-arms. Returns ``None`` when every transition has been
        consumed or the next one is display-only.
        """

        while self._next_command_index < len(self.transitions):
            transition = self.transitions[self._next_command_index]
            self._next_command_index += 1
            command = self.serial_command_for_transition(transition)

            if command is not None:
                return command

        return None

    def requeue_last_command(self) -> None:
        """Put the last drained command back so it is retried.

        Used when the serial link is mid-handshake and cannot accept a second
        command. Dropping the command would mean dropping an alarm, so it is
        re-queued instead.
        """

        if self._next_command_index > 0:
            self._next_command_index -= 1

    @property
    def serial_state(self) -> str:
        """The steady-state lifecycle command the firmware should be in.

        Every incident value here is a lifecycle command carrying the incident
        id, never a bare pin preset. ``ALARM``/``SAFE``/``SILENCE`` used to be
        returned here and they were exactly the diagnostic presets on the
        sketch side, so the laptop was itself driving the code path that
        bypassed IncidentCore and let the green lamp come back on by itself.

        ``WAITING`` is a sentinel: nothing is open, so there is no command to
        re-assert and the caller must send nothing.
        """

        incident_id = self.incident.incident_id if self.incident else None

        if self.state is OperatorState.FAULT:
            # No command can assert FAULT; the board raises it from link loss.
            return "FAULT"

        if incident_id is None:
            return "WAITING"

        if self.state is OperatorState.ALARM_LATCHED:
            return f"INCIDENT {incident_id}"

        if self.state in INCIDENT_STATES:
            if self.incident and self.incident.acknowledged:
                return f"ACKNOWLEDGE {incident_id}"
            return f"SILENCE {incident_id}"

        return "WAITING"
