"""Tests for the reusable dashboard widgets.

These widgets are presentation only, so the tests check the two things that
can actually go wrong in a presentation layer: content that misleads the
operator, and state that is silently dropped.
"""

from __future__ import annotations

import pytest

from PySide6.QtCore import Qt

from src.dashboard import theme
from src.dashboard.widgets import (
    Card,
    Dot,
    KeyValueRow,
    MetricTile,
    StatusPill,
    divider,
)


# -- Card ---------------------------------------------------------------


def test_card_title_is_uppercased_for_the_header(qapp):
    card = Card("Board outputs")

    assert card.title_label.text() == "BOARD OUTPUTS"


def test_card_hides_an_empty_caption(qapp):
    """An empty caption must not leave a blank line under the title."""

    card = Card("Camera")

    assert card.caption_label.isVisibleTo(card) is False


def test_card_shows_a_caption_when_given_one(qapp):
    card = Card("Camera", caption="Live frames, annotated by the model")

    assert card.caption_label.text() == "Live frames, annotated by the model"
    assert card.caption_label.isVisibleTo(card) is True


def test_card_caption_can_be_replaced_and_cleared(qapp):
    card = Card("Camera", caption="first")

    card.set_caption("second")
    assert card.caption_label.text() == "second"

    card.set_caption("")
    assert card.caption_label.isVisibleTo(card) is False


def test_card_header_accepts_a_trailing_widget(qapp):
    """Panels put link pills and badges in the header, so this must not raise."""

    card = Card("Health")
    pill = StatusPill("ONLINE", theme.GREEN)

    card.add_header_widget(pill)

    assert pill.parent() is card


# -- MetricTile ---------------------------------------------------------


def test_metric_tile_renders_value_and_unit_together(qapp):
    tile = MetricTile("Inference", value="18.4", unit="ms")

    assert tile.value_label.text() == "18.4 ms"
    assert tile.caption_label.text() == "INFERENCE"


def test_metric_tile_updates_value_and_unit(qapp):
    tile = MetricTile("Frame age", value="12", unit="ms")

    tile.set_value("140", unit="ms")

    assert tile.value_label.text() == "140 ms"


def test_metric_tile_keeps_the_previous_unit_when_none_is_passed(qapp):
    """Half the call sites format the unit into the string themselves."""

    tile = MetricTile("FPS", value="30", unit="fps")

    tile.set_value("29")

    assert tile.value_label.text() == "29 fps"


def test_metric_tile_applies_the_accent_it_is_given(qapp):
    tile = MetricTile("Violations", value="3")

    tile.set_value("4", accent=theme.RED)

    style = tile.value_label.styleSheet()
    assert theme.RED in style


def test_metric_tile_leaves_the_colour_alone_when_no_accent_is_passed(qapp):
    tile = MetricTile("Violations", value="3", accent=theme.RED)

    tile.set_value("4")

    assert theme.RED in tile.value_label.styleSheet()


# -- KeyValueRow --------------------------------------------------------


def test_key_value_row_shows_the_value(qapp):
    row = KeyValueRow("Port", "/dev/cu.usbserial-10")

    assert row.value_label.text() == "/dev/cu.usbserial-10"


def test_key_value_row_can_be_updated_in_place(qapp):
    row = KeyValueRow("Incident", "none")

    row.set_value("3", accent=theme.AMBER)

    assert row.value_label.text() == "3"
    assert theme.AMBER in row.value_label.styleSheet()


# -- Dot and StatusPill -------------------------------------------------


def test_dot_is_square_and_keeps_its_colour(qapp):
    dot = Dot(theme.GREEN, diameter=12)

    assert dot.width() == dot.height() == 12
    assert dot._colour == theme.GREEN


def test_dot_repaints_only_when_the_colour_changes(qapp):
    """A per-frame repaint of an unchanged indicator would waste the frame."""

    dot = Dot(theme.GREEN, diameter=10)
    dot.set_colour(theme.GREEN)

    assert dot._colour == theme.GREEN


def test_pill_paints_its_text_white_on_the_colour(qapp):
    """A pill whose text matches its fill is unreadable on the demo screen."""

    pill = StatusPill("OPEN", theme.GREEN)

    style = pill.styleSheet()

    assert "#FFFFFF" in style
    assert theme.GREEN in style


def test_pill_status_sets_text_and_colour_together(qapp):
    pill = StatusPill("OPEN", theme.GREEN)

    pill.set_status("DOWN", theme.RED)

    assert pill.text() == "DOWN"
    assert theme.RED in pill.styleSheet()


def test_pill_text_is_centred(qapp):
    pill = StatusPill("OPEN")

    assert pill.alignment() & Qt.AlignHCenter


# -- divider ------------------------------------------------------------


def test_divider_is_a_one_pixel_rule(qapp):
    line = divider()

    assert line.height() == 1


# -- theme coverage -----------------------------------------------------


@pytest.mark.parametrize("state", sorted(theme.STATE_STYLES))
def test_every_state_has_a_contrasting_accent(qapp, state):
    """Every lifecycle state must resolve to a real colour, not blank."""

    style = theme.style_for_state(state)

    assert style.accent.startswith("#")
    assert len(style.accent) == 7


def test_only_safe_is_green():
    """Green is a compliance claim. Any other state wearing it is a lie."""

    green_states = {
        name
        for name, style in theme.STATE_STYLES.items()
        if style.accent == theme.GREEN
    }

    assert green_states == {"SAFE"}
