"""Control panel: operator actions, grouped by consequence.

The layout answers one question before the operator presses anything: *which of
these can I press right now, and why not?*

- **Primary actions** are the three that change safety state: silence,
  acknowledge, request reset. They sit together, they are large, and each one
  carries its own disable reason so a greyed-out button is never a mystery.
- **Secondary actions** are read-only or housekeeping: request a board status
  poll, export the session, open help. They cannot change safety state, so they
  are visually quieter.

Two deliberate constraints:

- **Silence is acknowledge.** The board silences the buzzer on ``ACKNOWLEDGE``;
  there is no separate silence command. Offering two buttons for one firmware
  action would imply a distinction the hardware does not make, so both buttons
  exist for discoverability but report the same gate.
- **Disable reasons are permanent labels, not tooltips.** A tooltip requires a
  hover, and a hover is exactly what an operator does not have time for mid-demo.
  Each button keeps a reserved reason line beneath it, which also stops the card
  from jumping when a reason appears.

The diagram is display-only: it is a ``QGraphicsView`` over the state machine and
accepts no mouse buttons. Requesting a reset must go through ``reset_button`` and
therefore through the lifecycle's gate.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .adapters import reset_blocker_text
from .lifecycle_diagram import LifecycleDiagram
from .state_models import DashboardSnapshot
from .widgets import Card


#: Fixed reason-line height, so enabling a button cannot resize the card.
_REASON_HEIGHT = 24

#: Operator buttons are the primary mouse target during a demo, so they are
#: deliberately taller than a stock button. These heights are also the reason
#: the diagram below gives back 12 px: the live page has only ~20 px of vertical
#: slack at 1080p, and a page whose minimum exceeds the viewport sends Qt's
#: layout engine into unbounded setGeometry recursion instead of scrolling.
_PRIMARY_HEIGHT = 42
_SECONDARY_HEIGHT = 34


class _ActionGroup(QWidget):
    """A titled row of buttons, each with a permanent reason line underneath."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        self.heading = QLabel(title)
        self.heading.setObjectName("CardCaption")
        layout.addWidget(self.heading)

        self._buttons = QHBoxLayout()
        self._buttons.setContentsMargins(0, 0, 0, 0)
        self._buttons.setSpacing(8)
        layout.addLayout(self._buttons)

        self._reasons = QHBoxLayout()
        self._reasons.setContentsMargins(0, 0, 0, 0)
        self._reasons.setSpacing(8)
        layout.addLayout(self._reasons)

        self.reason_labels: dict[QPushButton, QLabel] = {}

    @property
    def buttons(self) -> tuple[QPushButton, ...]:
        return tuple(self.reason_labels)

    def add_button(self, button: QPushButton, *, primary: bool = False) -> None:
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        button.setMinimumHeight(
            _PRIMARY_HEIGHT if primary else _SECONDARY_HEIGHT
        )

        button.setObjectName("PrimaryButton" if primary else "SecondaryButton")

        self._buttons.addWidget(button, 1)

        reason = QLabel("")
        reason.setWordWrap(True)
        reason.setFixedHeight(_REASON_HEIGHT)
        reason.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        reason.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; "
            f"font-size: {theme.font_size('micro')}px;"
        )
        self._reasons.addWidget(reason, 1)

        self.reason_labels[button] = reason

    def set_reason(self, button: QPushButton, text: str) -> None:
        label = self.reason_labels.get(button)

        if label is not None and label.text() != text:
            label.setText(text)


class ControlPanel(Card):
    """Operator actions plus the lifecycle diagram."""

    silence_requested = Signal()
    acknowledge_requested = Signal()
    reset_requested = Signal()
    export_requested = Signal()
    status_requested = Signal()
    help_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "Operator actions",
            caption="Keys: S silence, A acknowledge, R reset, T status, "
            "Ctrl+E export, F1 help.",
            parent=parent,
        )

        self.diagram = LifecycleDiagram()
        self.body.addWidget(self.diagram, 1)

        self.primary_group = _ActionGroup("PRIMARY \u2014 CHANGES SAFETY STATE")
        self.secondary_group = _ActionGroup("SECONDARY \u2014 READ-ONLY")

        self.silence_button = QPushButton("Silence  (S)")
        self.silence_button.clicked.connect(self.silence_requested.emit)

        self.acknowledge_button = QPushButton("Acknowledge  (A)")
        self.acknowledge_button.clicked.connect(self.acknowledge_requested.emit)

        self.reset_button = QPushButton("Request reset  (R)")
        self.reset_button.clicked.connect(self.reset_requested.emit)

        for button in (
            self.silence_button,
            self.acknowledge_button,
            self.reset_button,
        ):
            self.primary_group.add_button(button, primary=True)

        self.status_button = QPushButton("Status  (T)")
        self.status_button.clicked.connect(self.status_requested.emit)

        self.export_button = QPushButton("Export  (Ctrl+E)")
        self.export_button.clicked.connect(self.export_requested.emit)

        self.help_button = QPushButton("Help  (F1)")
        self.help_button.clicked.connect(self.help_requested.emit)

        for button in (
            self.status_button,
            self.export_button,
            self.help_button,
        ):
            self.secondary_group.add_button(button)

        self.body.addWidget(self.primary_group)
        self.body.addWidget(self.secondary_group)

        # The paragraph summary kept from the previous version. Still the fastest
        # read when only one thing is wrong, so it stays.
        self.gate_label = QLabel("--")
        self.gate_label.setWordWrap(True)
        self.gate_label.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        self.body.addWidget(self.gate_label)

    # -- updates ------------------------------------------------------------

    def update_from(self, snapshot: DashboardSnapshot) -> None:
        lifecycle = snapshot.lifecycle
        open_incident = lifecycle.incident_id is not None

        can_acknowledge = open_incident and not lifecycle.acknowledged
        can_reset = snapshot.reset_blocker.name == "NONE"

        self.acknowledge_button.setEnabled(can_acknowledge)
        self.reset_button.setEnabled(can_reset)

        # Silence is an alias for acknowledge: the buzzer is silenced by the
        # acknowledgement, so it is only offered when acknowledging is possible.
        self.silence_button.setEnabled(can_acknowledge)

        self.diagram.set_state(snapshot.state, reset_blocked=not can_reset)

        self.silence_button.setToolTip(
            "Silence the buzzer. Equivalent to acknowledging: the board silences "
            "on ACKNOWLEDGE, and silence never restores green."
            if can_acknowledge
            else "Nothing to silence."
        )

        if can_acknowledge:
            self.primary_group.set_reason(self.silence_button, "ready")
            self.primary_group.set_reason(self.acknowledge_button, "ready")
        elif not open_incident:
            self.primary_group.set_reason(self.silence_button, "no incident")
            self.primary_group.set_reason(self.acknowledge_button, "no incident")
        else:
            self.primary_group.set_reason(self.silence_button, "already silenced")
            self.primary_group.set_reason(
                self.acknowledge_button, "already acknowledged"
            )

        if can_reset:
            self.primary_group.set_reason(self.reset_button, "gate open")
        else:
            self.primary_group.set_reason(
                self.reset_button, reset_blocker_text(snapshot.reset_blocker)
            )

        self.secondary_group.set_reason(self.status_button, "polls the board")
        self.secondary_group.set_reason(self.export_button, "writes a CSV")
        self.secondary_group.set_reason(self.help_button, "shortcut keys")

        if not can_acknowledge:
            if not open_incident:
                self.gate_label.setText("Acknowledge: no open incident.")
            else:
                self.gate_label.setText(
                    "Acknowledge: already acknowledged - the buzzer is silenced."
                )
        elif not can_reset:
            self.gate_label.setText(
                f"Reset: {reset_blocker_text(snapshot.reset_blocker)}"
            )
        else:
            self.gate_label.setText("Reset gate open: request reset to restore green.")

        self.gate_label.setStyleSheet(
            f"color: {theme.GREEN if can_reset else theme.TEXT_MUTED};"
            "font-size: 11px;"
        )
