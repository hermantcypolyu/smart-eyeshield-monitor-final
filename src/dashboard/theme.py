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

#: The official PolyU site sets its body copy in Arial and falls back through
#: the platform sans-serifs. Naming the same stack here keeps the dashboard's
#: academic register consistent instead of picking up a different face per OS.
FONT_FAMILY = 'Arial, Helvetica, "Helvetica Neue", "Segoe UI", sans-serif'
#: Family name for ``QFont`` constructors. Canvas painting cannot take a CSS
#: fallback list, so it gets the primary family and the OS resolves the rest.
FONT_FAMILY_QT = "Arial"
#: Monospace stays for numbers that must not shuffle horizontally as they change.
FONT_FAMILY_MONO = '"SF Mono", "Menlo", "Consolas", monospace'


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
# Typography scale
# ---------------------------------------------------------------------------

#: Named sizes in points. The dashboard is read from a projector across a room
#: as well as at a desk, so the important text is deliberately large and the
#: quiet text is never smaller than 9pt.
TYPE_SCALE: dict[str, int] = {
    "micro": 9,
    "caption": 10,
    "body": 12,
    "label": 11,
    "value": 20,
    "metric": 24,
    "state": 34,
    "banner": 13,
    "tab": 15,
}


def font_size(token: str) -> int:
    """Point size for a scale token, defaulting to body size."""

    return TYPE_SCALE.get(token, TYPE_SCALE["body"])


#: Multiplier applied to ``TYPE_SCALE`` while presentation mode is on. Only the
#: sizes grow: no colour, no layout order and no behaviour changes.
PRESENTATION_SCALE = 1.35


def scaled(token: str, *, presentation: bool = False) -> int:
    """Point size for ``token``, enlarged for presentation mode."""

    size = font_size(token)
    if not presentation:
        return size
    return max(size + 1, round(size * PRESENTATION_SCALE))


# ---------------------------------------------------------------------------
# Tab shell
# ---------------------------------------------------------------------------

#: The two top-level views. Named here so the window and the tests agree.
TAB_LIVE = "LIVE OPERATIONS"
TAB_ANALYTICS = "ANALYTICS & EVIDENCE"
TAB_TITLES: tuple[str, str] = (TAB_LIVE, TAB_ANALYTICS)

#: Subtitle shown under each tab, one short sentence each as the brief demands.
TAB_SUBTITLES: dict[str, str] = {
    TAB_LIVE: "Camera, verdict, hardware outputs and operator controls.",
    TAB_ANALYTICS: "Model evidence, rolling telemetry, counters and the full event record.",
}


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------

#: Object names the stylesheet targets. Panels set these so a single sheet can
#: dress the whole app without each widget hand-rolling colours.
STYLESHEET = f"""
QWidget {{
    background-color: {NAVY};
    color: {TEXT};
    font-family: {FONT_FAMILY};
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

QLabel#HeaderClock {{
    font-size: 12px;
    font-weight: 600;
    font-family: "SF Mono", "Menlo", monospace;
    color: {TEXT_ON_WHITE};
}}

/* --- Tabs -------------------------------------------------------------- */

QTabWidget::pane {{
    border: none;
    background-color: {NAVY};
    top: -1px;
}}

QTabBar {{
    qproperty-drawBase: 0;
    background-color: transparent;
}}

QTabBar::tab {{
    background-color: {CARD};
    color: {TEXT_MUTED};
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    padding: 9px 30px;
    margin-right: 4px;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.8px;
    min-width: 220px;
}}

QTabBar::tab:hover {{
    color: {TEXT};
    background-color: {CARD_RAISED};
}}

/* The active tab is a solid burgundy block with white text, the way the
   University site marks the current section. It carries no border so the block
   reads as one uninterrupted colour rather than a outlined box. */
QTabBar::tab:selected {{
    background-color: {BURGUNDY};
    color: {HEADER_WHITE};
    border: 1px solid {BURGUNDY};
}}

QTabBar::tab:selected QLabel {{
    color: {HEADER_WHITE};
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

/* 5px is wide enough to read as a deliberate brand rule at a glance, while
   still being obviously narrower and darker than the alarm-red state blocks -
   burgundy and red must never be confusable on a safety display. */
QLabel#CardTitle {{
    font-size: 11px;
    font-weight: 700;
    color: {TEXT};
    letter-spacing: 1.1px;
    border-left: 5px solid {BURGUNDY};
    padding-left: 9px;
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

QFrame#MetricTile {{
    background-color: {CARD_RAISED};
    border-top: 2px solid {BURGUNDY};
    border-radius: 6px;
}}

QLabel#MonoValue {{
    font-family: "SF Mono", "Menlo", monospace;
    font-size: 12px;
}}

QLabel#Chip {{
    border-radius: 9px;
    padding: 3px 9px;
    font-size: 10px;
    font-weight: 700;
}}

QLabel#Banner {{
    border-radius: 6px;
    padding: 5px 14px;
    font-size: 12px;
    font-weight: 600;
}}

QLabel#Footer {{
    color: {TEXT_MUTED};
    font-size: 11px;
    font-weight: 600;
    font-family: "SF Mono", "Menlo", monospace;
    background-color: {CARD};
    border-top: 1px solid {BORDER};
    padding: 3px 12px;
}}

QLabel#EmptyState {{
    color: {TEXT_MUTED};
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.6px;
    padding: 10px 0;
}}

QLabel#TabSubtitle {{
    color: {TEXT_MUTED};
    font-size: 11px;
    padding: 2px 0 6px 2px;
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

/* Primary actions are what an operator reaches for mid-incident, so they get
   the burgundy fill and the largest type in the card. Secondary actions stay
   quiet on purpose: read-only housekeeping should never compete for the eye
   with the three buttons that change safety state. */
QPushButton#Primary, QPushButton#PrimaryButton {{
    background-color: {BURGUNDY};
    border: 1px solid {BURGUNDY};
    color: #FFFFFF;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.3px;
    padding: 9px 16px;
}}

QPushButton#Primary:hover, QPushButton#PrimaryButton:hover {{
    background-color: {BURGUNDY_DARK};
    border-color: {BURGUNDY_DARK};
}}

QPushButton#Primary:pressed, QPushButton#PrimaryButton:pressed {{
    background-color: #5E0A18;
}}

QPushButton#SecondaryButton {{
    background-color: {CARD};
    border: 1px solid {BORDER};
    color: {TEXT};
    font-size: 12px;
    font-weight: 600;
    padding: 6px 14px;
}}

QPushButton#SecondaryButton:hover {{
    border-color: {TEXT_MUTED};
    background-color: {CARD_RAISED};
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

/* --- Focus ------------------------------------------------------------- */

QPushButton:focus {{
    outline: none;
    border: 2px solid {BURGUNDY};
}}

QPushButton#Primary:focus, QPushButton#PrimaryButton:focus {{
    border: 2px solid {TEXT};
}}

QTableWidget:focus {{
    border: 1px solid {BURGUNDY};
}}

QScrollArea:focus {{
    border: none;
}}
"""


#: Extra rules applied only while presentation mode is on. Kept as a separate
#: string so the normal sheet stays byte-identical and the switch is a single
#: ``setStyleSheet`` call that cannot half-apply.
PRESENTATION_STYLESHEET = f"""
QLabel#CardTitle {{
    font-size: 14px;
}}

QLabel#CardCaption {{
    font-size: 12px;
}}

QLabel#MetricValue {{
    font-size: 30px;
}}

QLabel#MetricLabel {{
    font-size: 12px;
}}

QLabel#Banner {{
    font-size: 17px;
    padding: 12px 18px;
}}

QLabel#Footer {{
    font-size: 13px;
    padding: 9px 14px;
}}

QTableWidget, QTableView {{
    font-size: 14px;
}}

QHeaderView::section {{
    font-size: 12px;
}}

QTabBar::tab {{
    font-size: 17px;
    padding: 13px 34px;
}}

QLabel#EmptyState {{
    font-size: 14px;
}}
"""


def stylesheet(*, presentation: bool = False) -> str:
    """The full sheet, optionally with the presentation-mode overrides."""

    if not presentation:
        return STYLESHEET
    return STYLESHEET + PRESENTATION_STYLESHEET
