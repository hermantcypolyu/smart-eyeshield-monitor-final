"""Deterministic tests for the temporal-filter timers and overlay text.

Every timestamp is injected, so these run without a camera, an ESP32, a
serial port, a model file or network access. They pin the exact operator
text for each state, because a confirmed incident reporting
``TIMER: 0.00 s`` is what made the two-second filter look broken.
"""

from __future__ import annotations

import pytest

from tools.live_eye_shield_demo import (
    Observation,
    State,
    TemporalFilter,
    decide_observation,
    format_timer_line,
    measure_frame,
)

CONFIRM_LINE = "CONFIRM TIMER: {:.2f} / {:.2f} s"
CLEAR_LINE = "CLEAR TIMER: {:.2f} / {:.2f} s"


def _filter(**overrides) -> TemporalFilter:
    settings = {
        "confirmation_seconds": 2.0,
        "window_seconds": 2.0,
        "clearance_seconds": 2.0,
        "violation_ratio_required": 0.80,
        "unknown_tolerance_seconds": 0.35,
    }
    settings.update(overrides)

    return TemporalFilter(**settings)


def _feed(filter_: TemporalFilter, observation: Observation, times):
    """Update the filter at each timestamp and return every status."""

    return [
        filter_.update(observation, now)
        for now in times
    ]


def _confirm(filter_: TemporalFilter, start: float = 0.0) -> None:
    """Drive a filter to VIOLATION_CONFIRMED and leave it there."""

    step = filter_.confirmation_seconds

    _feed(
        filter_,
        Observation.VIOLATION,
        [start + step * index / 10 for index in range(11)],
    )

    assert filter_.state == State.VIOLATION_CONFIRMED


# --------------------------------------------------------------------------- #
# 1-2. Idle states
# --------------------------------------------------------------------------- #


def test_initial_unknown_shows_idle_timer():
    filter_ = _filter()

    status = filter_.update(Observation.UNKNOWN, 0.0)

    assert status.state == State.UNKNOWN
    assert status.pending_elapsed is None
    assert status.confirmed_after_seconds is None
    assert status.safe_clear_elapsed is None
    assert format_timer_line(status, 2.0, 2.0) == "TIMER: IDLE"


def test_initial_safe_shows_idle_timer():
    filter_ = _filter()

    status = filter_.update(Observation.SAFE, 0.0)

    assert status.state == State.SAFE
    assert status.pending_elapsed is None
    assert format_timer_line(status, 2.0, 2.0) == "TIMER: IDLE"


# --------------------------------------------------------------------------- #
# 3-5. Pending window and confirmation
# --------------------------------------------------------------------------- #


def test_first_violation_frame_starts_pending_at_zero():
    filter_ = _filter()

    status = filter_.update(Observation.VIOLATION, 100.0)

    assert status.state == State.VIOLATION_PENDING
    assert status.pending_elapsed == pytest.approx(0.0)
    assert format_timer_line(status, 2.0, 2.0) == "CONFIRM TIMER: 0.00 / 2.00 s"


def test_violation_just_below_threshold_stays_pending():
    filter_ = _filter()

    *_, last = _feed(filter_, Observation.VIOLATION, [0.0, 1.0, 1.99])

    assert last.state == State.VIOLATION_PENDING
    assert last.pending_elapsed == pytest.approx(1.99)
    assert format_timer_line(last, 2.0, 2.0) == "CONFIRM TIMER: 1.99 / 2.00 s"
    assert filter_.incident_id is None


def test_violation_at_threshold_confirms():
    filter_ = _filter()

    *_, last = _feed(filter_, Observation.VIOLATION, [0.0, 1.0, 2.0])

    assert last.state == State.VIOLATION_CONFIRMED
    assert filter_.confirmed is True
    assert filter_.incident_id == 1
    assert filter_.confirmed_at == pytest.approx(2.0)
    assert last.confirmed_after_seconds == pytest.approx(2.0)
    assert last.pending_elapsed is None


# --------------------------------------------------------------------------- #
# 6-7. Confirmation duration is preserved
# --------------------------------------------------------------------------- #


def test_confirmation_duration_is_recorded_and_kept():
    filter_ = _filter()
    _confirm(filter_)

    assert filter_.confirmed_after_seconds == pytest.approx(2.0)

    status = filter_.update(Observation.VIOLATION, 2.3)

    assert status.state == State.VIOLATION_CONFIRMED
    assert status.confirmed_after_seconds == pytest.approx(2.0)
    assert filter_.confirmed_after_seconds == pytest.approx(2.0)


def test_continued_violation_never_resets_timer_to_zero():
    """The reported bug: a confirmed incident showed TIMER: 0.00 s."""

    filter_ = _filter()
    _confirm(filter_)

    statuses = _feed(filter_, Observation.VIOLATION, [3.0, 4.5, 9.0])

    for status in statuses:
        assert status.state == State.VIOLATION_CONFIRMED
        assert status.confirmed_after_seconds == pytest.approx(2.0)
        assert (
            format_timer_line(status, 2.0, 2.0)
            == "CONFIRMED AFTER: 2.00 s"
        )
        assert "0.00" not in format_timer_line(status, 2.0, 2.0)


# --------------------------------------------------------------------------- #
# 8. UNKNOWN after confirmation
# --------------------------------------------------------------------------- #


def test_unknown_after_confirmation_keeps_incident_and_duration():
    filter_ = _filter()
    _confirm(filter_)

    status = filter_.update(Observation.UNKNOWN, 5.0)

    assert status.state == State.VIOLATION_CONFIRMED
    assert filter_.incident_id == 1
    assert status.confirmed_after_seconds == pytest.approx(2.0)
    assert (
        format_timer_line(status, 2.0, 2.0) == "CONFIRMED AFTER: 2.00 s"
    )


# --------------------------------------------------------------------------- #
# 9-10. UNKNOWN during a pending violation
# --------------------------------------------------------------------------- #


def test_brief_unknown_preserves_pending_start_time():
    filter_ = _filter()

    _feed(filter_, Observation.VIOLATION, [0.0, 1.0])

    status = filter_.update(Observation.UNKNOWN, 1.2)

    assert status.state == State.VIOLATION_PENDING
    # Still counted from the first violation frame, not from the gap.
    assert status.pending_elapsed == pytest.approx(1.2)
    assert filter_.pending_since == pytest.approx(0.0)
    assert filter_.incident_id is None


def test_extended_unknown_cancels_pending_confirmation():
    filter_ = _filter()

    _feed(filter_, Observation.VIOLATION, [0.0, 1.0])

    # 0.4 s past the last violation frame exceeds the 0.35 s tolerance.
    status = filter_.update(Observation.UNKNOWN, 1.4)

    assert status.state == State.UNKNOWN
    assert status.pending_elapsed is None
    assert filter_.pending_since is None
    assert filter_.last_violation_time is None
    assert filter_.incident_id is None
    assert format_timer_line(status, 2.0, 2.0) == "TIMER: IDLE"


# --------------------------------------------------------------------------- #
# 11. SAFE before confirmation
# --------------------------------------------------------------------------- #


def test_safe_before_confirmation_cancels_pending():
    filter_ = _filter()

    _feed(filter_, Observation.VIOLATION, [0.0, 1.0])

    status = filter_.update(Observation.SAFE, 1.5)

    assert status.state == State.SAFE
    assert status.pending_elapsed is None
    assert filter_.pending_since is None
    assert filter_.confirmed is False
    assert filter_.incident_id is None
    assert format_timer_line(status, 2.0, 2.0) == "TIMER: IDLE"


# --------------------------------------------------------------------------- #
# 12-14. Safe-clear timing after a confirmed incident
# --------------------------------------------------------------------------- #


def test_safe_after_confirmation_starts_safe_clear_timing():
    filter_ = _filter()
    _confirm(filter_)

    status = filter_.update(Observation.SAFE, 10.0)

    assert status.state == State.SAFE_CLEAR_PENDING
    assert status.safe_clear_elapsed == pytest.approx(0.0)
    # The violation timer is not shown in this state.
    assert status.pending_elapsed is None
    assert (
        format_timer_line(status, 2.0, 2.0) == "CLEAR TIMER: 0.00 / 2.00 s"
    )


def test_safe_clear_timer_increases_monotonically():
    filter_ = _filter()
    _confirm(filter_)
    _feed(filter_, Observation.SAFE, [10.0])

    statuses = _feed(filter_, Observation.SAFE, [10.5, 11.0, 11.9])

    elapsed = [status.safe_clear_elapsed for status in statuses]

    assert elapsed == pytest.approx([0.5, 1.0, 1.9])
    assert elapsed == sorted(elapsed)
    assert all(
        status.state == State.SAFE_CLEAR_PENDING
        for status in statuses
    )
    assert (
        format_timer_line(statuses[-1], 2.0, 2.0)
        == "CLEAR TIMER: 1.90 / 2.00 s"
    )


def test_unknown_interrupts_safe_clear_progress():
    filter_ = _filter()
    _confirm(filter_)
    _feed(filter_, Observation.SAFE, [10.0, 11.0])

    status = filter_.update(Observation.UNKNOWN, 11.4)

    assert status.state == State.VIOLATION_CONFIRMED
    assert status.safe_clear_elapsed is None
    assert filter_.safe_since is None
    assert filter_.incident_id == 1
    assert status.confirmed_after_seconds == pytest.approx(2.0)


def test_continued_violation_interrupts_safe_clear_progress():
    filter_ = _filter()
    _confirm(filter_)
    _feed(filter_, Observation.SAFE, [10.0, 11.0])

    status = filter_.update(Observation.VIOLATION, 11.2)

    assert status.state == State.VIOLATION_CONFIRMED
    assert status.safe_clear_elapsed is None
    assert status.confirmed_after_seconds == pytest.approx(2.0)


# --------------------------------------------------------------------------- #
# 15. One episode, one incident
# --------------------------------------------------------------------------- #


def test_one_sustained_episode_creates_only_one_incident():
    filter_ = _filter()

    statuses = _feed(
        filter_,
        Observation.VIOLATION,
        [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 10.0],
    )

    assert [status.state for status in statuses].count(
        State.VIOLATION_CONFIRMED
    ) == 4
    assert filter_.incident_id == 1
    assert filter_.next_incident_id == 2


def test_safe_between_episodes_does_not_clear_the_incident():
    filter_ = _filter()
    _confirm(filter_)

    status = filter_.update(Observation.SAFE, 5.0)
    assert status.state == State.SAFE_CLEAR_PENDING

    status = filter_.update(Observation.SAFE, 30.0)
    assert status.state == State.SAFE_CLEAR_PENDING
    assert filter_.incident_id == 1
    assert filter_.confirmed is True


# --------------------------------------------------------------------------- #
# 16. Duplicate detections collapse to one update
# --------------------------------------------------------------------------- #


def test_duplicate_detections_produce_one_filter_update():
    nested = (0, 0, 100, 100)

    observation = decide_observation(
        [("no_goggles", 0.45), ("no_goggles", 0.90)]
    )
    assert observation == Observation.VIOLATION

    diagnostics = measure_frame(
        [
            ("no_goggles", 0.45, nested),
            ("no_goggles", 0.90, nested),
        ],
        observation,
        0.30,
    )

    # Two accepted boxes, measured but not double-counted as samples.
    assert diagnostics.accepted_count == 2
    assert diagnostics.duplicate_boxes == 1

    filter_ = _filter()
    _feed(filter_, Observation.VIOLATION, [0.0, 2.0])

    assert filter_.incident_id == 1
    assert filter_.next_incident_id == 2


# --------------------------------------------------------------------------- #
# 17. Frame interval independence
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "times",
    [
        [0.0, 2.0],
        [0.0, 0.1, 0.2, 0.3, 1.9, 2.0],
        [0.0, 0.9, 1.98, 2.0],
        [0.0, 0.033, 0.066, 1.0, 1.5, 1.999, 2.0],
    ],
)
def test_threshold_is_independent_of_frame_interval(times):
    filter_ = _filter()

    statuses = _feed(filter_, Observation.VIOLATION, times)

    assert statuses[-1].state == State.VIOLATION_CONFIRMED
    assert statuses[-1].confirmed_after_seconds == pytest.approx(2.0)
    assert all(
        status.state == State.VIOLATION_PENDING
        for status in statuses[:-1]
    )


def test_threshold_is_not_reached_early_at_high_frame_rate():
    filter_ = _filter()

    statuses = _feed(
        filter_,
        Observation.VIOLATION,
        [index / 60 for index in range(115)],
    )

    assert all(
        status.state == State.VIOLATION_PENDING
        for status in statuses
    )
    assert filter_.incident_id is None


# --------------------------------------------------------------------------- #
# 18. A diagnostic five-second configuration really waits five seconds
# --------------------------------------------------------------------------- #


def test_five_second_confirmation_confirms_at_five_not_two():
    filter_ = _filter(confirmation_seconds=5.0)

    times = [0.0, 1.0, 2.0, 3.0, 4.0, 4.99, 5.0]
    statuses = _feed(filter_, Observation.VIOLATION, times)

    for index, status in enumerate(statuses[:-1]):
        assert status.state == State.VIOLATION_PENDING
        assert status.confirmed_after_seconds is None
        assert status.pending_elapsed == pytest.approx(times[index])

    assert statuses[-1].state == State.VIOLATION_CONFIRMED
    assert statuses[-1].confirmed_after_seconds == pytest.approx(5.0)
    assert (
        format_timer_line(statuses[-1], 5.0, 2.0)
        == "CONFIRMED AFTER: 5.00 s"
    )


def test_five_second_overlay_shows_a_counting_timer():
    filter_ = _filter(confirmation_seconds=5.0)

    statuses = _feed(filter_, Observation.VIOLATION, [0.0, 1.5, 3.25])

    assert [
        format_timer_line(status, 5.0, 2.0)
        for status in statuses
    ] == [
        "CONFIRM TIMER: 0.00 / 5.00 s",
        "CONFIRM TIMER: 1.50 / 5.00 s",
        "CONFIRM TIMER: 3.25 / 5.00 s",
    ]


# --------------------------------------------------------------------------- #
# Overlay text for every state
# --------------------------------------------------------------------------- #


def test_overlay_text_matches_operator_contract():
    filter_ = _filter()
    _confirm(filter_)

    expected = {
        Observation.UNKNOWN: "TIMER: IDLE",
        Observation.SAFE: "TIMER: IDLE",
    }

    fresh = _filter()
    for observation, text in expected.items():
        status = fresh.update(observation, 0.0)
        assert format_timer_line(status, 2.0, 2.0) == text

    confirmed = filter_.update(Observation.VIOLATION, 3.0)
    assert (
        format_timer_line(confirmed, 2.0, 2.0) == "CONFIRMED AFTER: 2.00 s"
    )

    clearing = filter_.update(Observation.SAFE, 4.0)
    assert (
        format_timer_line(clearing, 2.0, 2.0) == "CLEAR TIMER: 0.00 / 2.00 s"
    )


def test_timer_line_never_claims_zero_for_a_confirmed_incident():
    filter_ = _filter(confirmation_seconds=5.0)
    _confirm(filter_, start=0.0)

    for observation in (
        Observation.VIOLATION,
        Observation.UNKNOWN,
        Observation.SAFE,
    ):
        status = filter_.update(observation, 7.0)

        if status.state == State.VIOLATION_CONFIRMED:
            line = format_timer_line(status, 5.0, 2.0)
            assert line == "CONFIRMED AFTER: 5.00 s"
            assert line != "TIMER: 0.00 s"

        if status.state == State.SAFE_CLEAR_PENDING:
            assert (
                format_timer_line(status, 5.0, 2.0)
                == "CLEAR TIMER: 0.00 / 2.00 s"
            )
