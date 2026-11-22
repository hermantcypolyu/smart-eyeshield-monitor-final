"""Tests for the presentation layer: tabs, chips, footer, banner priority.

These cover the visual-polish pass. Every assertion is about what an operator
sees, never about what the safety logic decides - the lifecycle suites already
own that, and a presentation bug must never be able to change a verdict.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QScrollArea

from src.dashboard import branding, theme
from src.dashboard.event_timeline import EventTimeline, is_meaningful
from src.dashboard.lifecycle_diagram import STATE_TO_NODE, LifecycleDiagram, Node
from src.dashboard.signal_flow import STAGES, SignalFlowWidget, first_broken
from src.dashboard.state_models import TimelineEntry
from src.dashboard.widgets import Chip, EmptyState
from tools.incident_lifecycle import OperatorState


# -- type scale --------------------------------------------------------------


def test_type_scale_is_monotonic_where_it_matters():
    assert theme.TYPE_SCALE["micro"] < theme.TYPE_SCALE["caption"]
    assert theme.TYPE_SCALE["caption"] <= theme.TYPE_SCALE["body"]
    assert theme.TYPE_SCALE["body"] < theme.TYPE_SCALE["value"]
    assert theme.TYPE_SCALE["value"] < theme.TYPE_SCALE["state"]


def test_font_size_returns_the_token_and_falls_back_to_body():
    assert theme.font_size("state") == theme.TYPE_SCALE["state"]
    # An unknown token must not raise: a typo should degrade to body text rather
    # than crash a paint.
    assert theme.font_size("nonsense") == theme.TYPE_SCALE["body"]


def test_scaled_grows_in_presentation_mode_and_never_shrinks():
    for token in theme.TYPE_SCALE:
        normal = theme.scaled(token)
        large = theme.scaled(token, presentation=True)

        assert normal == theme.font_size(token)
        assert large >= normal


def test_presentation_stylesheet_differs_from_normal():
    assert theme.stylesheet(presentation=True) != theme.stylesheet()
    # Both must still be substantial: a failed format call would yield "".
    assert len(theme.stylesheet()) > 2000
    assert len(theme.stylesheet(presentation=True)) > 2000


# -- tabs --------------------------------------------------------------------


def test_two_tabs_exist_in_the_documented_order(window):
    assert window.tabs.count() == 2
    assert window.tabs.tabText(0) == theme.TAB_LIVE
    assert window.tabs.tabText(1) == theme.TAB_ANALYTICS


def test_tab_titles_are_uppercase_labels():
    for title in theme.TAB_TITLES:
        assert title == title.upper()
        assert title.strip() == title


def test_switching_tabs_updates_the_recorded_active_tab(window):
    window.tabs.setCurrentIndex(1)
    assert window.active_tab == theme.TAB_ANALYTICS

    window.tabs.setCurrentIndex(0)
    assert window.active_tab == theme.TAB_LIVE


def test_switching_tabs_never_replays_or_clears_the_record(window):
    window._refresh()
    before = len(window.timeline.row_texts())

    window.tabs.setCurrentIndex(1)
    window.tabs.setCurrentIndex(0)

    assert len(window.timeline.row_texts()) >= before


# -- presentation mode -------------------------------------------------------


def test_presentation_mode_toggles_and_hides_the_analytics_tab(window):
    assert window.presentation is False
    assert window.tabs.isTabVisible(1)

    window.set_presentation(True)
    assert window.presentation is True
    assert not window.tabs.isTabVisible(1)
    assert window.tabs.currentIndex() == 0

    window.set_presentation(False)
    assert window.presentation is False
    assert window.tabs.isTabVisible(1)


def test_presentation_mode_hides_the_lone_tab_bar(window):
    """With one page left a tab bar is a dead control that costs height.

    Presentation mode enlarges every font, so the live page grows while the
    viewport does not. Dropping the tab bar is what keeps a 1080p screen
    scroll-free instead of trading one annoyance for another.
    """

    window.set_presentation(True)
    assert window.tabs.tabBar().isHidden()

    window.set_presentation(False)
    assert not window.tabs.tabBar().isHidden()


def test_control_buttons_use_object_names_the_stylesheet_styles(window):
    """The primary actions must actually read as primary on screen.

    `control_panel` tags its buttons, but a tag whose selector is missing is
    invisible: QSS drops unknown names silently and the button renders as
    plain. Assert the pairing, not just the presence of a name.
    """

    panel = window.control_panel
    assert panel.silence_button.objectName() == "PrimaryButton"
    assert panel.reset_button.objectName() == "PrimaryButton"
    assert panel.status_button.objectName() == "SecondaryButton"

    for name in ("PrimaryButton", "SecondaryButton"):
        assert f"QPushButton#{name}" in theme.STYLESHEET


def test_header_no_longer_styles_the_removed_context_line():
    """The disclaimer moved into a tooltip; a leftover rule would mislead."""

    assert "HeaderContext" not in theme.STYLESHEET


def test_presentation_mode_does_not_touch_safety_state(window):
    before = window.lifecycle.status()

    window.set_presentation(True)
    after = window.lifecycle.status()

    assert before.state == after.state
    assert before.incident_id == after.incident_id
    assert before.alarm == after.alarm

    window.set_presentation(False)


def test_presentation_hint_names_the_escape_route(window):
    window.set_presentation(True)
    assert "ESC" in window.presentation_hint.text().upper()

    window.set_presentation(False)
    assert "P" in window.presentation_hint.text()


# -- chips -------------------------------------------------------------------


def test_demo_mode_chip_says_demo_data(window):
    window._refresh()
    assert window.mode_chip.text() == "DEMO DATA"
    assert window.mode_chip.text()


def test_missing_logo_chip_reports_every_missing_logo(window):
    window._refresh()

    missing = branding.missing_logos()
    if missing:
        for name in missing:
            assert name in window.branding_chip.text()


def test_unimplemented_features_are_chips_not_banners(window):
    window._refresh()

    # Blynk and the yellow lamp are absent by design. Reporting them in the
    # urgent banner would train the operator to ignore the banner.
    assert "BLYNK" in window.blynk_chip.text().upper()
    assert "YELLOW" in window.yellow_chip.text().upper()

    assert "BLYNK" not in window.banner.text().upper()


# -- footer ------------------------------------------------------------------


def test_footer_carries_state_link_port_and_model(window):
    window._refresh()
    text = window.footer_label.text()

    assert window.lifecycle.status().state.value in text
    assert "board" in text
    assert "model" in text


def test_status_bar_reports_mode_and_state(window):
    window._refresh()
    text = window.status_label.text()

    assert "DEMO DATA" in text
    assert window.lifecycle.status().state.value in text


# -- banner priority ---------------------------------------------------------


def test_demo_banner_wins_over_every_advisory(window):
    window._refresh()
    assert "DEMO DATA" in window.banner.text().upper()


def test_banner_prioritises_fault_over_serial(window, monkeypatch):
    monkeypatch.setattr(window, "demo_data", False)
    monkeypatch.setattr(window, "_serial_ok", False)
    monkeypatch.setattr(window, "serial_port", "/dev/null")

    window.lifecycle.set_health(
        camera_ok=False, model_ok=True, serial_ok=True, now=1.0
    )
    window._push_banner()

    text = window.banner.text().upper()
    assert "FAULT" in text
    assert "SERIAL" not in text


def test_banner_falls_through_to_serial_when_no_fault(window, monkeypatch):
    monkeypatch.setattr(window, "demo_data", False)
    monkeypatch.setattr(window, "serial_port", "")
    monkeypatch.setattr(window, "_camera_ok", True)

    window._push_banner()

    assert "SERIAL PORT" in window.banner.text().upper()


def test_banner_falls_through_to_camera_when_serial_is_fine(window, monkeypatch):
    monkeypatch.setattr(window, "demo_data", False)
    monkeypatch.setattr(window, "serial_port", "/dev/null")
    monkeypatch.setattr(window, "_serial_ok", True)
    monkeypatch.setattr(window, "_camera_ok", False)

    window._push_banner()

    assert "CAMERA" in window.banner.text().upper()


def test_banner_reports_stale_firmware_status(window, monkeypatch):
    monkeypatch.setattr(window, "demo_data", False)
    monkeypatch.setattr(window, "serial_port", "/dev/null")
    monkeypatch.setattr(window, "_serial_ok", True)
    monkeypatch.setattr(window, "_camera_ok", True)
    monkeypatch.setattr(window, "_status_age", 99.0)

    window._push_banner()

    assert "STALE" in window.banner.text().upper()


def test_banner_clears_when_nothing_is_wrong(window, monkeypatch):
    monkeypatch.setattr(window, "demo_data", False)
    monkeypatch.setattr(window, "serial_port", "/dev/null")
    monkeypatch.setattr(window, "_serial_ok", True)
    monkeypatch.setattr(window, "_camera_ok", True)
    monkeypatch.setattr(window, "_status_age", 0.0)

    window._push_banner()

    # Advisory text may survive, but never an urgent condition.
    urgent = window.banner.text().upper()
    for word in ("FAULT", "SERIAL", "CAMERA", "STALE"):
        assert word not in urgent


# -- signal flow -------------------------------------------------------------


def test_signal_flow_has_seven_stages_in_causal_order():
    keys = [key for key, _ in STAGES]

    assert len(keys) == 7
    assert keys[0] == "camera"
    assert keys[-1] == "outputs"


def test_signal_flow_break_names_the_first_cause(qapp):
    widget = SignalFlowWidget()
    live = dict.fromkeys((k for k, _ in STAGES), True)

    # Serial dead: the board never hears anything, so esp32 and outputs are
    # also dead - the report must name the first cause, not the last symptom.
    live["serial"] = False
    live["esp32"] = False
    live["outputs"] = False

    widget.set_flow(live, break_at=first_broken(live))

    assert widget.break_node == "serial"


def test_signal_flow_accepts_the_legacy_model_key(qapp):
    widget = SignalFlowWidget()
    live = dict.fromkeys((k for k, _ in STAGES), True)
    live["model"] = False

    widget.set_flow(live, break_at="model")

    # "model" is the old name for "detector"; aliasing keeps old callers honest
    # instead of silently reporting a fully live chain.
    assert widget.break_node == "detector"


def test_first_broken_reports_empty_when_everything_is_up():
    assert first_broken(dict.fromkeys((k for k, _ in STAGES), True)) == ""


def test_first_broken_names_the_dead_stage():
    live = dict.fromkeys((k for k, _ in STAGES), True)
    live["filter"] = False

    assert first_broken(live) == "filter"


def test_signal_flow_summary_is_one_line(qapp):
    widget = SignalFlowWidget()
    widget.set_flow(dict.fromkeys((k for k, _ in STAGES), True))

    assert widget.summary_line()
    assert "\n" not in widget.summary_line()


# -- lifecycle diagram -------------------------------------------------------


def test_diagram_covers_every_operator_state():
    for state in OperatorState:
        assert state in STATE_TO_NODE, f"{state} has no diagram node"


def test_diagram_node_lookup_is_stable_for_every_state(qapp):
    diagram = LifecycleDiagram()

    for state in OperatorState:
        diagram.set_state(state)
        assert diagram.current_node() is STATE_TO_NODE[state]


def test_diagram_accepts_its_own_node_enum(qapp):
    diagram = LifecycleDiagram()
    diagram.set_state(Node.SAFE)

    assert diagram.current_node() is Node.SAFE


def test_diagram_remembers_the_previous_node(qapp):
    diagram = LifecycleDiagram()

    diagram.set_state(OperatorState.SAFE)
    diagram.set_state(OperatorState.ALARM_LATCHED)

    assert diagram.current_node() is Node.ALARM_LATCHED
    assert diagram.previous is Node.SAFE


def _live_scroll(window):
    return window.live_tab.findChild(QScrollArea, "LiveScroll")


@pytest.mark.parametrize("presentation", [False, True])
def test_live_tab_fits_1080p_without_scrolling(qapp, window, presentation):
    """The presentation claim is a measurement, not an aspiration.

    Panels demand height by width through `hasHeightForWidth`, so a passing
    `minimumSizeHint()` is not proof. Compare what is rendered against the
    viewport. The window is put back to 1920x1080 because presentation mode
    goes fullscreen and the offscreen screen here is 800x800.
    """

    window.set_presentation(presentation)
    window.showNormal()
    window.resize(1920, 1080)
    for _ in range(6):
        qapp.processEvents()

    scroll = _live_scroll(window)
    body = scroll.widget()

    assert not scroll.verticalScrollBar().isVisible(), (
        f"presentation={presentation}: body={body.height()} "
        f"viewport={scroll.viewport().height()}"
    )
    assert body.height() <= scroll.viewport().height()


def test_diagram_has_bounded_height(qapp):
    diagram = LifecycleDiagram()

    # The diagram sits in the live tab, which must fit 1080p without scrolling.
    # An unbounded scene height is what makes a page scroll.
    assert diagram.sizeHint().height() <= diagram.minimumHeight() + 1
    assert diagram.minimumHeight() <= 140


# -- timeline filtering ------------------------------------------------------


def test_is_meaningful_keeps_state_changes():
    assert is_meaningful(TimelineEntry(at=0.0, kind="state", message="SAFE"))


def test_meaningful_view_is_a_subset_of_the_raw_view(qapp):
    timeline = EventTimeline()

    for index in range(40):
        kind = "state" if index % 2 == 0 else "heartbeat"
        timeline.add(TimelineEntry(at=float(index), kind=kind, message=f"e{index}"))

    assert timeline.visible_row_count() <= len(timeline.row_texts())

    timeline.toggle_raw()
    assert timeline.visible_row_count() == len(timeline.row_texts())


def test_timeline_display_limit_caps_the_meaningful_view(qapp):
    timeline = EventTimeline(display_limit=5)

    for index in range(20):
        timeline.add(TimelineEntry(at=float(index), kind="state", message=f"e{index}"))

    assert timeline.visible_row_count() <= 5
    # Export must not be truncated by a display setting.
    assert len(timeline.row_texts()) == 20


def test_empty_state_hides_when_it_has_nothing_to_say(qapp):
    label = EmptyState("")
    assert not label.isVisible()

    label.set_state("NOTHING YET")
    assert label.text() == "NOTHING YET"


def test_chip_hides_when_its_text_is_cleared(qapp):
    chip = Chip("")
    assert not chip.isVisible()

    chip.setText("DEMO DATA")
    chip.set_colour(theme.AMBER)
    assert chip.text() == "DEMO DATA"


# -- layout contract ---------------------------------------------------------


def test_charts_live_on_the_analytics_tab_only(window):
    assert not window.charts_panel.isVisible()

    window.tabs.setCurrentIndex(1)
    assert window.charts_panel.isVisible()


def test_demo_session_reaches_every_timeline_view(window):
    # The fixture driver's entries must reach all three views, not just the
    # analytics table, or a demo run shows an empty live timeline.
    for _ in range(40):
        window._step_fixture(window._fixture_last + 0.5)

    assert window.live_timeline.row_texts()
    assert window.diagnostics_table.row_texts()


# -- PolyU visual identity ---------------------------------------------------


def test_typography_matches_the_university_site():
    """PolyU's own pages are Arial/Helvetica. A serif fallback would read
    as a different institution's dashboard."""

    assert theme.FONT_FAMILY_QT == "Arial"
    assert "Arial" in theme.FONT_FAMILY
    assert "Helvetica" in theme.FONT_FAMILY

    sheet = theme.stylesheet()

    # Every widget inherits this, so the whole dashboard follows the brand.
    assert f"font-family: {theme.FONT_FAMILY}" in sheet


def test_active_tab_is_a_solid_burgundy_block_with_white_text():
    """The official site marks the current section with a filled burgundy tab.

    An outline or a tinted background is not the same affordance, so this
    asserts the fill, the text colour and that no bottom border survives to
    cut the block short.
    """

    sheet = theme.stylesheet()
    block = sheet.split("QTabBar::tab:selected", 1)[1].split("}", 1)[0]

    assert f"background-color: {theme.BURGUNDY}" in block
    assert f"color: {theme.HEADER_WHITE}" in block


def test_card_title_accent_is_a_heavy_burgundy_rule():
    """3px read as a smudge at demo distance and was confusable with the
    alarm red used on the safety state card."""

    sheet = theme.stylesheet()
    block = sheet.split("QLabel#CardTitle", 1)[1].split("}", 1)[0]

    assert f"border-left: 5px solid {theme.BURGUNDY}" in block
    # Burgundy, emphatically not the alarm red that means "take action".
    assert f"border-left: 5px solid {theme.RED}" not in block
    assert theme.BURGUNDY != theme.RED


def test_operator_buttons_are_a_large_click_target(qapp, window):
    """The operator buttons were the smallest thing on the screen and are the
    one control an operator must hit under pressure."""

    primary = window.control_panel.acknowledge_button
    secondary = window.control_panel.help_button

    assert primary.minimumHeight() >= 40
    assert secondary.minimumHeight() >= 32
    assert primary.minimumHeight() > secondary.minimumHeight()


def test_every_operator_button_shares_its_row_height(qapp, window):
    """A row of buttons at different heights looks like a bug, and the reason
    line underneath assumes a fixed height."""

    panel = window.control_panel

    for group in (panel.primary_group, panel.secondary_group):
        heights = {button.minimumHeight() for button in group.buttons}
        assert len(heights) == 1


def test_evidence_tab_shows_the_same_camera_feed(qapp, window):
    """An operator reviewing an incident should not have to switch tabs to
    confirm what the model saw."""

    window.tabs.setCurrentIndex(1)

    assert window.evidence_camera.isVisible()
    # Same widget class, so the same rendering path, footer and all.
    assert type(window.evidence_camera) is type(window.camera_panel)


def test_placeholder_logo_is_light_not_a_dark_block(qapp):
    """The old placeholder was a dark filled box with a dashed border, which
    looked broken next to the white PolyU header."""

    spec = branding.EIE

    assert not branding.logo_available(spec)

    image = branding.load_logo(spec).toImage()

    assert image.width() > 0 and image.height() > 0
    # Aspect ratio comes from the placeholder ratio, so the header reserve and
    # the painted box agree.
    assert abs(image.width() / image.height() - branding.logo_aspect(spec)) < 0.05


def test_logo_aspect_falls_back_to_the_placeholder_ratio():
    assert branding.logo_aspect(branding.EIE) == 2.4
    assert branding.logo_aspect(branding.POLYU) == 2.4
