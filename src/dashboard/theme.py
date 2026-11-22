"""Colour palette and stylesheet for the Smart Eye-Shield Safety Operations Centre.

Every colour the dashboard paints comes from this module so the safety
semantics stay in one place. The rules they encode are taken from
``SYSTEM_STATE_REFERENCE.md``:

- **Green** is reserved for a verified SAFE verdict. Nothing else may use it,
  not a healthy link, not a connected camera, not a passing test.
- **Amber** marks a pending confirmation - a timer is running, nothing latched.
- **Orange** marks an acknowledged incident - the operator has answered and the
  buzzer is quiet, but compliance is still withdrawn.
- **Red** marks a latched, unanswered alarm.
- **Cyan** is telemetry and safe-recovery evidence, never compliance.
- **Magenta** is FAULT only, so an ambiguous system is never mistaken for green.

The branding colours are a student project's approximation of the PolyU
identity. They are not official University assets.
"""

from __future__ import annotations

from dataclasses import dataclass

from tools.incident_lifecycle import AlarmIntent, OperatorState


# ---------------------------------------------------------------------------
# Brand palette
# ---------------------------------------------------------------------------

#: PolyU-style burgundy. Used for the header rule, titles and emphasis.
BURGUNDY = "#A6192E"
#: Darker burgundy for gradients and pressed states.
BURGUNDY_DARK = "#7F1024"
#: Header background. White background is required by the brand brief.
HEADER_WHITE = "#FFFFFF"
#: Page background - deep navy, not black, so red still reads as red.
NAVY = "#07152F"
#: Card / panel background.
CARD = "#102650"
#: Slightly raised surface for nested groups and table headers.
CARD_RAISED = "#16325F"
#: Hairline borders.
BORDER = "#1E3B6B"
#: Primary text on navy.
TEXT = "#E8EEF9"
#: Secondary text - labels, units, captions.
TEXT_MUTED = "#93A6C8"
#: Text on white header.
TEXT_ON_WHITE = "#16233A"


# ---------------------------------------------------------------------------
# Safety palette
# ---------------------------------------------------------------------------

GREEN = "#1f9d55"
GREEN_DIM = "#14663a"
AMBER = "#d98b00"
AMBER_DIM = "#8a5800"
ORANGE = "#ef6c00"
ORANGE_DIM = "#9c4600"
RED = "#c62828"
RED_DIM = "#7f1a1a"
GREY = "#6b7280"
GREY_DIM = "#3f4653"
MAGENTA = "#8e24aa"
MAGENTA_DIM = "#5c1770"
CYAN = "#0e9aa7"
CYAN_DIM = "#0a656e"


@dataclass(frozen=True)
class StateStyle:
    """How one operator state is presented.

    ``accent`` is the only colour the UI is allowed to use for that state.
    ``label`` is the operator-facing name; ``hint`` is a one-line instruction
    telling the operator what to do next, which is the difference between a
    status board and a usable control room.
    """

    accent: str
    dim: str
    label: str
    hint: str


#: One row per ``OperatorState``. Adding a state to the lifecycle without adding
#: it here is a programming error, caught by ``style_for_state``.
STATE_STYLES: dict[OperatorState, StateStyle] = {
    OperatorState.STARTUP: StateStyle(
        GREY,
        GREY_DIM,
        "STARTUP",
        "Waiting for camera, model and serial link to each prove healthy.",
    ),
    OperatorState.WAITING_FOR_TARGET: StateStyle(
        GREY,
        GREY_DIM,
        "WAITING FOR TARGET",
        "No verdict. An empty frame is missing information, never safety.",
    ),
    OperatorState.SAFE: StateStyle(
        GREEN,
        GREEN_DIM,
        "SAFE",
        "Goggles verified. This is the only state that reads as compliant.",
    ),
    OperatorState.VIOLATION_PENDING: StateStyle(
        AMBER,
        AMBER_DIM,
        "VIOLATION PENDING",
        "Confirmation timer running. A single noisy frame cannot alarm.",
    ),
    OperatorState.ALARM_LATCHED: StateStyle(
        RED,
        RED_DIM,
        "ALARM LATCHED",
        "Compliance withdrawn. Press A to acknowledge, then satisfy the reset gate.",
    ),
    OperatorState.SAFE_CLEAR_PENDING: StateStyle(
        RED,
        RED_DIM,
        "SAFE CLEAR PENDING",
        "Goggles seen. Clearance timer running and can still be interrupted.",
    ),
    OperatorState.SAFE_RECOVERED: StateStyle(
        AMBER,
        AMBER_DIM,
        "SAFE RECOVERED",
        "Recovery proven, alarm still latched. Press A to acknowledge.",
    ),
    OperatorState.ACKNOWLEDGED: StateStyle(
        ORANGE,
        ORANGE_DIM,
        "ACKNOWLEDGED",
        "Operator answered, buzzer silenced. Press R once the reset gate is clear.",
    ),
    OperatorState.FAULT: StateStyle(
        MAGENTA,
        MAGENTA_DIM,
        "FAULT",
        "A subsystem is untrusted. Outputs are quiet on purpose - never green.",
    ),
}


def style_for_state(state: OperatorState) -> StateStyle:
    """Return the presentation for ``state``.

    Raises ``KeyError`` with a clear message rather than silently painting an
    unknown state grey: a new lifecycle state must be given a deliberate colour.
    """

    try:
        return STATE_STYLES[state]
    except KeyError:  # pragma: no cover - guards future lifecycle edits
        raise KeyError(
            f"No dashboard style registered for OperatorState {state!r}. "
            "Add it to STATE_STYLES in src/dashboard/theme.py."
        ) from None


#: Alarm intent -> accent. ``SILENCED`` is orange because the buzzer is quiet
#: but the incident is not closed.
ALARM_COLOURS: dict[AlarmIntent, str] = {
    AlarmIntent.SILENT: GREY,
    AlarmIntent.WAITING: GREY,
    AlarmIntent.SOUNDING: RED,
    AlarmIntent.SILENCED: ORANGE,
    AlarmIntent.FAULT: MAGENTA,
}


# Health statuses, kept as plain strings so the models stay Qt-free.
ONLINE = "ONLINE"
OFFLINE = "OFFLINE"
DEGRADED = "DEGRADED"
FAULT = "FAULT"
NOT_CONFIGURED = "NOT CONFIGURED"
DISABLED = "DISABLED"

HEALTH_COLOURS: dict[str, str] = {
    ONLINE: GREEN,
    OFFLINE: RED,
    DEGRADED: AMBER,
    FAULT: MAGENTA,
    NOT_CONFIGURED: GREY,
    DISABLED: GREY,
}


def health_colour(status: str) -> str:
    """Colour for a subsystem health status."""

    return HEALTH_COLOURS.get(status, GREY)


def state_accent(state: OperatorState | None) -> str:
    """Accent colour for a state, grey when there is no state yet."""

    if state is None:
        return GREY
    return style_for_state(state).accent


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------

#: Object names the stylesheet targets. Panels set these so a single sheet can
#: dress the whole app without each widget hand-rolling colours.
STYLESHEET = f"""
QWidget {{
    background-color: {NAVY};
    color: {TEXT};
    font-family: "SF Pro Text", "Segoe UI", "Helvetica Neue", sans-serif;
    font-size: 13px;
}}

QMainWindow, QDialog {{
    background-color: {NAVY};
}}

/* --- Header ------------------------------------------------------------ */

QFrame#Header {{
    background-color: {HEADER_WHITE};
    border-bottom: 3px solid {BURGUNDY};
}}

QFrame#Header QLabel {{
    background-color: transparent;
    color: {TEXT_ON_WHITE};
}}

QLabel#HeaderTitle {{
    font-size: 21px;
    font-weight: 700;
    color: {BURGUNDY};
    letter-spacing: 0.4px;
}}

QLabel#HeaderSubtitle {{
    font-size: 11px;
    font-weight: 500;
    color: {TEXT_ON_WHITE};
}}

QLabel#HeaderContext {{
    font-size: 10px;
    color: #5A6B85;
}}

QLabel#HeaderClock {{
    font-size: 12px;
    font-weight: 600;
    font-family: "SF Mono", "Menlo", monospace;
    color: {TEXT_ON_WHITE};
}}

/* --- Cards ------------------------------------------------------------- */

QFrame#Card {{
    background-color: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}

QFrame#Card QLabel {{
    background-color: transparent;
}}

QLabel#CardTitle {{
    font-size: 11px;
    font-weight: 700;
    color: {TEXT_MUTED};
    letter-spacing: 1.1px;
}}

QLabel#CardCaption {{
    font-size: 11px;
    color: {TEXT_MUTED};
}}

QLabel#MetricValue {{
    font-size: 22px;
    font-weight: 700;
    font-family: "SF Mono", "Menlo", monospace;
}}

QLabel#MetricLabel {{
    font-size: 10px;
    font-weight: 600;
    color: {TEXT_MUTED};
    letter-spacing: 0.8px;
}}

QLabel#MonoValue {{
    font-family: "SF Mono", "Menlo", monospace;
    font-size: 12px;
}}

QLabel#Banner {{
    border-radius: 6px;
    padding: 9px 14px;
    font-size: 13px;
    font-weight: 600;
}}

/* --- Tables ------------------------------------------------------------ */

QTableWidget, QTableView {{
    background-color: {CARD};
    alternate-background-color: {CARD_RAISED};
    border: 1px solid {BORDER};
    border-radius: 6px;
    gridline-color: {BORDER};
    selection-background-color: {BURGUNDY_DARK};
    selection-color: {TEXT};
    font-size: 12px;
}}

QHeaderView::section {{
    background-color: {CARD_RAISED};
    color: {TEXT_MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 5px 7px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.7px;
}}

QTableWidget::item {{
    padding: 3px 6px;
}}

/* --- Controls ---------------------------------------------------------- */

QPushButton {{
    background-color: {CARD_RAISED};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 12px;
    font-weight: 600;
}}

QPushButton:hover {{
    border-color: {BURGUNDY};
}}

QPushButton:pressed {{
    background-color: {BURGUNDY_DARK};
}}

QPushButton:disabled {{
    color: {GREY};
    border-color: {BORDER};
    background-color: {NAVY};
}}

QPushButton#Primary {{
    background-color: {BURGUNDY};
    border-color: {BURGUNDY};
    color: #FFFFFF;
}}

QPushButton#Primary:hover {{
    background-color: {BURGUNDY_DARK};
}}

QCheckBox, QRadioButton {{
    font-size: 12px;
    color: {TEXT};
    spacing: 7px;
}}

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {CARD};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 5px 8px;
    font-size: 12px;
}}

QComboBox QAbstractItemView {{
    background-color: {CARD};
    color: {TEXT};
    selection-background-color: {BURGUNDY_DARK};
    border: 1px solid {BORDER};
}}

/* --- Scrollbars -------------------------------------------------------- */

QScrollArea {{
    border: none;
    background-color: transparent;
}}

QScrollBar:vertical {{
    background-color: {NAVY};
    width: 10px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background-color: {BORDER};
    border-radius: 5px;
    min-height: 26px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {BURGUNDY_DARK};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar:horizontal {{
    background-color: {NAVY};
    height: 10px;
    margin: 0;
}}

QScrollBar::handle:horizontal {{
    background-color: {BORDER};
    border-radius: 5px;
    min-width: 26px;
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* --- Misc -------------------------------------------------------------- */

QToolTip {{
    background-color: {CARD_RAISED};
    color: {TEXT};
    border: 1px solid {BURGUNDY};
    padding: 5px;
    font-size: 11px;
}}

QSplitter::handle {{
    background-color: {BORDER};
}}

QLabel#PlaceholderNote {{
    color: {AMBER};
    font-size: 10px;
    font-weight: 600;
}}
"""
