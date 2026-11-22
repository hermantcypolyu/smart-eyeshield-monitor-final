"""Contract between the video loop and the operator lifecycle.

The 42 lifecycle tests and the 363-test suite both passed while
``tools/live_eye_shield_demo.py`` called ``IncidentLifecycle.mark_onboard_verified()``
— a method that did not exist. Nothing imported both sides, so the crash could
only appear on a real camera run.

These tests close that gap without a camera: they read the demo's source, collect
every method it calls on its ``lifecycle`` object, and assert the lifecycle
really defines each one. They also drive the same call sequence the video loop
uses, so the two authorities are proven to fit together.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tools.incident_lifecycle import (
    IncidentLifecycle,
    OperatorState,
    ResetBlocker,
)
from tools.live_eye_shield_demo import SerialReply

DEMO = Path(__file__).resolve().parents[1] / "tools" / "live_eye_shield_demo.py"

LIFECYCLE_CALLS = sorted(
    {
        node.func.attr
        for node in ast.walk(ast.parse(DEMO.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "lifecycle"
    }
)


class Obs:
    def __init__(self, name: str) -> None:
        self.name = name


VIOLATION = Obs("VIOLATION")
SAFE = Obs("SAFE")


def _lifecycle() -> IncidentLifecycle:
    return IncidentLifecycle(
        confirmation_seconds=2.0,
        clearance_seconds=2.0,
    )


def _feed(lc: IncidentLifecycle, obs: Obs, now: float, ratio: float = 0.0) -> None:
    lc.update(obs, now, violation_ratio=ratio)


def test_demo_calls_are_discovered():
    """Guard the guard: the AST scan must find the calls, not silently miss them."""

    assert "update" in LIFECYCLE_CALLS
    assert "mark_onboard_verified" in LIFECYCLE_CALLS
    assert len(LIFECYCLE_CALLS) >= 5


@pytest.mark.parametrize("name", LIFECYCLE_CALLS)
def test_lifecycle_defines_every_method_the_demo_calls(name: str):
    assert hasattr(IncidentLifecycle, name), (
        f"{DEMO.name} calls lifecycle.{name}() but IncidentLifecycle does not "
        "define it; this is an AttributeError waiting for a live run"
    )


def test_video_loop_sequence_reaches_reset():
    """Drive the exact order the frame loop uses, including the verification call."""

    lc = _lifecycle()

    _feed(lc, VIOLATION, 0.0, ratio=1.0)
    _feed(lc, VIOLATION, 1.0, ratio=1.0)
    status = lc.update(VIOLATION, 2.0, violation_ratio=1.0)
    assert lc.state is OperatorState.ALARM_LATCHED
    incident_id = status.incident_id
    assert incident_id is not None

    ok, _ = lc.acknowledge(2.1)
    assert ok
    # The keypress only queues ACKNOWLEDGE <id>; the board must answer before
    # the incident counts as answered.
    assert lc.reset_blocker() is ResetBlocker.NOT_ACKNOWLEDGED
    confirmed = lc.confirm_acknowledge(
        SerialReply(raw="OK ACKNOWLEDGED", ok=True, code="ACKNOWLEDGED"),
        2.2,
        incident_id,
    )
    assert confirmed == "CONFIRMED"
    _feed(lc, SAFE, 2.5)
    _feed(lc, SAFE, 3.5)
    status = lc.update(SAFE, 4.5)
    assert status.safe_recovered
    assert lc.mark_onboard_verified() == incident_id

    assert lc.reset_blocker() is ResetBlocker.NONE
    accepted, blocker = lc.reset(4.6)
    assert accepted and blocker is ResetBlocker.NONE
    assert lc.onboard_verified_incident is None


def test_mark_onboard_verified_is_safe_without_recovery():
    """The loop guards on safe_recovered, but the method must not rely on it."""

    lc = _lifecycle()
    assert lc.mark_onboard_verified() is None

    _feed(lc, VIOLATION, 0.0, ratio=1.0)
    _feed(lc, VIOLATION, 1.0, ratio=1.0)
    lc.update(VIOLATION, 2.0, violation_ratio=1.0)
    assert lc.mark_onboard_verified() is None
