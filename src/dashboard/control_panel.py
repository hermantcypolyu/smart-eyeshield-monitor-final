"""Control panel: the operator's four actions and the lifecycle diagram.

Two deliberate choices:

- **Buttons mirror the shortcut keys, and stop when they cannot act.** A disabled
  button is the honest signal that the lifecycle would reject the request, so the
  operator learns the rule instead of pressing a key that silently does nothing.
  The enabled state is derived from the lifecycle's own gate, not guessed.
- **The diagram is built from widgets, not an image.** An embedded picture cannot
  show which node is live, and a stale highlight is worse than none. Nodes carry
  their own colour and the current state is filled while the rest are outlined.

Reset is offered only when ``reset_blocker()`` is ``NONE``; acknowledge only
while an incident is open and unacknowledged. Silence is not offered at all as a
separate action, because acknowledging is what silences the buzzer and inventing
a second silence path would create two ways to do one thing.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from tools.incident_lifecycle import OperatorState

from . import theme
from .adapters import reset_blocker_text
from .state_models import DashboardSnapshot
from .widgets import Card, divider


#: Diagram layout. Each entry is (row, column) on a 3-column grid so the two
#: decision branches and the recovery path are visually separate.
_DIAGRAM_LAYOUT: tuple[tuple[OperatorState, int, int], ...] = (
    (OperatorState.STARTUP, 0, 1),
    (OperatorState.WAITING_FOR_TARGET, 1, 1),
    (OperatorState.SAFE, 2, 0),
    (OperatorState.VIOLATION_PENDING, 2, 2),
    (OperatorState.ALARM_LATCHED, 3, 2),
    (OperatorState.ACKNOWLEDGED, 4, 2),
    (OperatorState.SAFE_CLEAR_PENDING, 4, 0),
    (OperatorState.SAFE_RECOVERED, 5, 0),
    (OperatorState.FAULT, 5, 2),
)


class StateNode(QLabel):
    """One state in the diagram: outlined normally, filled when current."""

    def __init__(self, state: OperatorState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        style = theme.style_for_state(state)

        self.setText(style.label)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumHeight(34)
        self.set_active(False)

    def set_active(self, active: bool) -> None:
        style = theme.style_for_state(self.state)

        if active:
            self.setStyleSheet(
                "QLabel {"
                f"background-color: {style.accent};"
                "color: #FFFFFF;"
                "border: 2px solid #FFFFFF;"
                "border-radius: 4px;"
                "padding: 4px 6px;"
                "font-size: 10px;"
                "font-weight: 800;"
                "}"
            )
        else:
            self.setStyleSheet(
                "QLabel {"
                f"background-color: transparent;"
                f"color: {style.accent};"
                f"border: 1px solid {style.accent};"
                "border-radius: 4px;"
                "padding: 4px 6px;"
                "font-size: 10px;"
                "font-weight: 600;"
                "}"
            )


class LifecycleDiagram(QWidget):
    """Widget-based state diagram with the live state filled in."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)

        self.nodes: dict[OperatorState, StateNode] = {}

        for state, row, column in _DIAGRAM_LAYOUT:
            node = StateNode(state)
            layout.addWidget(node, row, column)
            self.nodes[state] = node

        for column in range(3):
            layout.setColumnStretch(column, 1)

        self.current: OperatorState | None = None

    def set_state(self, state: OperatorState) -> None:
        if state is self.current:
            return

        if self.current is not None and self.current in self.nodes:
            self.nodes[self.current].set_active(False)

        if state in self.nodes:
            self.nodes[state].set_active(True)
        else:
            # A state with no node would silently show nothing; better to say so.
            for node in self.nodes.values():
                node.set_active(False)

        self.current = state


class ControlPanel(Card):
    """Operator actions plus the lifecycle diagram."""

    silence_requested = Signal()
    acknowledge_requested = Signal()
    reset_requested = Signal()
    export_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "Operator actions",
            caption="A disabled button means the lifecycle would reject the "
            "request. Keys: S silence, A acknowledge, R reset, Ctrl+E export.",
            parent=parent,
        )

        self.diagram = LifecycleDiagram()
        self.body.addWidget(self.diagram)

        self.body.addWidget(divider())

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)

        self.silence_button = QPushButton("Silence  (S)")
        self.silence_button.clicked.connect(self.silence_requested.emit)

        self.acknowledge_button = QPushButton("Acknowledge  (A)")
        self.acknowledge_button.setObjectName("PrimaryButton")
        self.acknowledge_button.clicked.connect(self.acknowledge_requested.emit)

        self.reset_button = QPushButton("Request reset  (R)")
        self.reset_button.clicked.connect(self.reset_requested.emit)

        for button in (
            self.silence_button,
            self.acknowledge_button,
            self.reset_button,
        ):
            buttons.addWidget(button)

        self.body.addLayout(buttons)

        self.gate_label = QLabel("--")
        self.gate_label.setWordWrap(True)
        self.gate_label.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        self.body.addWidget(self.gate_label)

        self.body.addStretch(1)

    # -- updates ------------------------------------------------------------

    def update_from(self, snapshot: DashboardSnapshot) -> None:
        self.diagram.set_state(snapshot.state)

        lifecycle = snapshot.lifecycle
        open_incident = lifecycle.incident_id is not None

        can_acknowledge = open_incident and not lifecycle.acknowledged
        can_reset = snapshot.reset_blocker.name == "NONE"

        self.acknowledge_button.setEnabled(can_acknowledge)
        self.reset_button.setEnabled(can_reset)

        # Silence is an alias for acknowledge: the buzzer is silenced by the
        # acknowledgement, so it is only offered when acknowledging is possible.
        self.silence_button.setEnabled(can_acknowledge)

        self.silence_button.setToolTip(
            "Silence the buzzer. Equivalent to acknowledging: the board silences "
            "on ACKNOWLEDGE, and silence never restores green."
            if can_acknowledge
            else "Nothing to silence."
        )

        if not can_acknowledge:
            if not open_incident:
                self.gate_label.setText("Acknowledge: no open incident.")
            else:
                self.gate_label.setText(
                    "Acknowledge: already acknowledged - the buzzer is silenced."
                )
        elif not can_reset:
            self.gate_label.setText(f"Reset: {reset_blocker_text(snapshot.reset_blocker)}")
        else:
            self.gate_label.setText("Reset gate open: request reset to restore green.")

        self.gate_label.setStyleSheet(
            f"color: {theme.GREEN if can_reset else theme.TEXT_MUTED};"
            "font-size: 11px;"
        )
