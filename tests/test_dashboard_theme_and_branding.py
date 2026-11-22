"""The colour contract and the branding fallback.

``SYSTEM_STATE_REFERENCE.md`` defines what each state looks like. If a colour
drifts, an operator reading the screen is misinformed, so these tests pin the
mapping rather than the exact hex values: what matters is which *family* each
state belongs to, and that no state can render with a missing style.
"""

from __future__ import annotations

import pytest

from src.dashboard import branding, theme
from tools.incident_lifecycle import AlarmIntent, OperatorState


def test_every_operator_state_has_a_style():
    """A new lifecycle state must fail loudly rather than paint grey."""

    for state in OperatorState:
        style = theme.style_for_state(state)

        assert style.label
        assert style.hint
        assert style.accent.startswith("#")


def test_unknown_state_raises_with_actionable_message():
    with pytest.raises(KeyError, match="STATE_STYLES"):
        theme.style_for_state("NOT_A_STATE")  # type: ignore[arg-type]


def test_safe_is_the_only_green_state():
    """Green means verified compliance and nothing else may borrow it."""

    green_states = {
        state
        for state in OperatorState
        if theme.style_for_state(state).accent == theme.GREEN
    }

    assert green_states == {OperatorState.SAFE}


def test_red_is_reserved_for_a_latched_alarm():
    """Red means "compliance withdrawn", so a latched alarm must outrank the
    recovery timer. ``SAFE_CLEAR_PENDING`` keeps red because the alarm is still
    latched and the clearance can still be interrupted."""

    red_states = {
        state
        for state in OperatorState
        if theme.style_for_state(state).accent == theme.RED
    }

    assert red_states == {OperatorState.ALARM_LATCHED, OperatorState.SAFE_CLEAR_PENDING}
    assert OperatorState.SAFE not in red_states


def test_fault_is_magenta_and_never_green():
    style = theme.style_for_state(OperatorState.FAULT)

    assert style.accent == theme.MAGENTA
    assert style.accent != theme.GREEN


def test_pending_violation_reads_amber_not_green():
    """A running confirmation timer is not compliance and is not an alarm."""

    assert theme.style_for_state(OperatorState.VIOLATION_PENDING).accent == theme.AMBER


def test_safe_recovered_is_amber_because_green_would_claim_compliance():
    """The reference table gives SAFE_RECOVERED red/amber.

    Amber is chosen over red because the alarm is muted-by-nothing but recovery
    is proven; it is chosen over green because the board has not reset yet.
    """

    style = theme.style_for_state(OperatorState.SAFE_RECOVERED)

    assert style.accent == theme.AMBER
    assert style.accent != theme.GREEN


def test_acknowledged_is_orange_because_compliance_is_still_withdrawn():
    assert theme.style_for_state(OperatorState.ACKNOWLEDGED).accent == theme.ORANGE


def test_every_alarm_intent_has_a_colour():
    for intent in AlarmIntent:
        assert theme.ALARM_COLOURS[intent].startswith("#")


def test_state_accent_handles_none():
    assert theme.state_accent(None) == theme.GREY


def test_health_colours_cover_declared_statuses():
    for status in (
        theme.ONLINE,
        theme.OFFLINE,
        theme.DEGRADED,
        theme.FAULT,
        theme.NOT_CONFIGURED,
        theme.DISABLED,
    ):
        assert theme.health_colour(status) == theme.HEALTH_COLOURS[status]


def test_unknown_health_status_degrades_to_grey_not_green():
    assert theme.health_colour("something-new") == theme.GREY


def test_stylesheet_mentions_every_object_name_the_window_uses():
    """QSS silently ignores unknown selectors, so a rename loses styling."""

    for name in ("Header", "Card", "CardTitle", "Banner", "Primary"):
        assert f"#{name}" in theme.STYLESHEET


def test_missing_logos_are_reported_by_label():
    """Both assets are absent in a clean checkout, and that must be visible."""

    missing = branding.missing_logos()

    assert isinstance(missing, tuple)

    for label in missing:
        assert label in {"POLYU", "EIE"}


def test_placeholder_is_labelled_and_obviously_not_a_logo(qapp):
    from PySide6.QtGui import QPixmap

    pixmap = branding.placeholder_pixmap(120, 42, "POLYU")

    assert isinstance(pixmap, QPixmap)
    assert not pixmap.isNull()
    assert pixmap.width() == 120


def test_logo_label_survives_a_missing_asset(qapp):
    """The header must render even with no logo files at all."""

    label = branding.LogoLabel(branding.POLYU)

    assert label.pixmap().width() > 0
    assert label.toolTip().startswith("POLYU logo")


def test_branding_context_line_denies_official_status():
    """A screenshot must not be able to imply a University product."""

    assert "not an official" in branding.CONTEXT.lower()
    assert "Student AIoT Mini Project" in branding.SUBTITLE
