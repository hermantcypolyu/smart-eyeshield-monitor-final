"""Safety state panel: the single most important thing on the screen.

It answers three questions in order of importance, because an operator under
pressure reads top-down:

1. **Is compliance withdrawn?** The big state block, colour-coded from
   ``theme.STATE_STYLES`` so the colour always means the same thing.
2. **What should I do?** The hint line, which states the next action rather than
   restating the state name.
3. **Why can I not reset?** The reset gate list, showing the *first* unmet
   precondition only, matching the order the lifecycle evaluates them.

The pending-timer bar is deliberately separate from the headline. A pending
violation is amber, not red, and the bar's purpose is to show that a single noisy
frame cannot alarm: the operator can watch the timer get cancelled.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tools.incident_lifecycle import OperatorState

from . import theme
from .adapters import reset_blocker_text
from .state_models import DashboardSnapshot
from .widgets import Card, KeyValueRow, StatusPill, divider


class StateBlock(QWidget):
    """The headline: state name, colour wash, and the action hint."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)

        self.state_label = QLabel("STARTUP")
        self.state_label.setStyleSheet(
            "font-size: 30px; font-weight: 800; letter-spacing: 1.2px;"
        )
        top.addWidget(self.state_label)

        self.pill = StatusPill("ALARM: SILENT", theme.GREY)
        top.addWidget(self.pill, 0, Qt.AlignTop)
        top.addStretch(1)

        layout.addLayout(top)

        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet(f"color: {theme.TEXT}; font-size: 12px;")
        layout.addWidget(self.hint_label)

        self.incident_label = QLabel("")
        self.incident_label.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 11px;"
            "font-family: 'SF Mono','Menlo',monospace;"
        )
        layout.addWidget(self.incident_label)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._apply_style(OperatorState.STARTUP)

    def _apply_style(self, state: OperatorState) -> None:
        style = theme.style_for_state(state)
        self.setStyleSheet(
            "QWidget {"
            f"background-color: {style.dim};"
            f"border-left: 6px solid {style.accent};"
            "border-radius: 6px;"
            "}"
        )
        self.state_label.setText(style.label)
        self.state_label.setStyleSheet(
            "font-size: 30px; font-weight: 800; letter-spacing: 1.2px;"
            f"color: {style.accent};"
        )
        self.hint_label.setText(style.hint)

    def update_from(self, snapshot: DashboardSnapshot) -> None:
        state = snapshot.state
        self._apply_style(state)

        alarm = snapshot.alarm
        alarm_colour = theme.ALARM_COLOURS.get(alarm, theme.GREY)
        self.pill.set_status(f"ALARM: {alarm.value}", alarm_colour)

        incident = snapshot.lifecycle.incident_id

        if incident:
            ack = "yes" if snapshot.lifecycle.acknowledged else "no"
            recovered = "yes" if snapshot.lifecycle.safe_recovered else "no"
            self.incident_label.setText(
                f"incident {incident}   acknowledged {ack}   recovered {recovered}"
            )
        else:
            self.incident_label.setText("no open incident")


class TimerBar(QWidget):
    """Labelled progress bar for one temporal-filter timer."""

    def __init__(
        self,
        label: str,
        colour: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.name_label = QLabel(label)
        self.name_label.setObjectName("MetricLabel")
        row.addWidget(self.name_label)
        row.addStretch(1)

        self.value_label = QLabel("idle")
        self.value_label.setObjectName("MonoValue")
        self.value_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        row.addWidget(self.value_label)
        layout.addLayout(row)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(7)
        self._colour = colour
        self.bar.setStyleSheet(self._sheet(colour, theme.GREY_DIM))
        layout.addWidget(self.bar)

    @staticmethod
    def _sheet(fill: str, trough: str) -> str:
        return (
            "QProgressBar {"
            f"background-color: {trough};"
            "border: none; border-radius: 3px;"
            "}"
            "QProgressBar::chunk {"
            f"background-color: {fill};"
            "border-radius: 3px;"
            "}"
        )

    def set_progress(
        self,
        fraction: float,
        text: str,
        *,
        active: bool = True,
        colour: str | None = None,
    ) -> None:
        if not active:
            self.bar.setValue(0)
            self.bar.setStyleSheet(self._sheet(theme.GREY_DIM, theme.GREY_DIM))
            self.value_label.setText("idle")
            self.value_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
            return

        fill = colour or self._colour
        self.bar.setStyleSheet(self._sheet(fill, theme.GREY_DIM))
        self.bar.setValue(int(max(0.0, min(1.0, fraction)) * 1000))
        self.value_label.setText(text)
        self.value_label.setStyleSheet(f"color: {fill};")


class SafetyStatePanel(Card):
    """Compliance verdict, temporal timers and the reset gate.

    Takes a ``DashboardSnapshot`` and renders it; holds no safety state of its
    own, so what it shows can always be traced back to the lifecycle.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "Safety state",
            caption="Colour is a contract, not decoration: green is the only "
            "colour that may be read as compliant, and only a verified SAFE "
            "verdict produces it.",
            parent=parent,
        )

        self.state_block = StateBlock()
        self.body.addWidget(self.state_block)

        timers = QHBoxLayout()
        timers.setContentsMargins(0, 0, 0, 0)
        timers.setSpacing(14)

        self.confirm_bar = TimerBar("CONFIRMATION TIMER", theme.AMBER)
        self.clear_bar = TimerBar("CLEARANCE TIMER", theme.CYAN)
        timers.addWidget(self.confirm_bar, 1)
        timers.addWidget(self.clear_bar, 1)
        self.body.addLayout(timers)

        self.body.addWidget(divider())

        gate_title = QLabel("RESET GATE")
        gate_title.setObjectName("CardTitle")
        self.body.addWidget(gate_title)

        self.gate_label = QLabel("--")
        self.gate_label.setWordWrap(True)
        self.gate_label.setStyleSheet(f"color: {theme.TEXT}; font-size: 12px;")
        self.body.addWidget(self.gate_label)

        self.detail_rows = {
            "acknowledged": KeyValueRow("Acknowledged by board"),
            "recovered": KeyValueRow("Recovery proven"),
            "health": KeyValueRow("Camera / model health"),
            "link": KeyValueRow("Serial link"),
        }

        for row in self.detail_rows.values():
            self.body.addWidget(row)

        self.body.addStretch(1)

    def update_from(self, snapshot: DashboardSnapshot) -> None:
        self.state_block.update_from(snapshot)

        gauge = snapshot.gauge

        self.confirm_bar.set_progress(
            gauge.pending_fraction,
            (
                f"{gauge.pending_elapsed:.2f} / {gauge.confirmation_seconds:.2f} s"
                if gauge.pending_elapsed is not None
                else "idle"
            ),
            active=gauge.pending_elapsed is not None,
            colour=theme.AMBER,
        )

        self.clear_bar.set_progress(
            gauge.clear_fraction,
            (
                f"{gauge.safe_clear_elapsed:.2f} / {gauge.clearance_seconds:.2f} s"
                if gauge.safe_clear_elapsed is not None
                else "idle"
            ),
            active=gauge.safe_clear_elapsed is not None,
            colour=theme.CYAN,
        )

        blocker = snapshot.reset_blocker
        text = reset_blocker_text(blocker)
        self.gate_label.setText(text)
        self.gate_label.setStyleSheet(
            f"color: {theme.GREEN if blocker.name == 'NONE' else theme.AMBER};"
            "font-size: 12px; font-weight: 600;"
        )

        lifecycle = snapshot.lifecycle

        has_incident = lifecycle.incident_id is not None
        self.detail_rows["acknowledged"].set_value(
            "yes" if lifecycle.acknowledged else ("no" if has_incident else "--"),
            accent=theme.ORANGE if lifecycle.acknowledged else theme.TEXT_MUTED,
        )
        self.detail_rows["recovered"].set_value(
            "yes" if lifecycle.safe_recovered else ("no" if has_incident else "--"),
            accent=theme.CYAN if lifecycle.safe_recovered else theme.TEXT_MUTED,
        )

        health_ok = blocker.name not in ("HEALTH_INVALID",)
        self.detail_rows["health"].set_value(
            "ok" if health_ok else "invalid",
            accent=theme.GREEN if health_ok else theme.MAGENTA,
        )

        link_ok = blocker.name != "LINK_INVALID"
        self.detail_rows["link"].set_value(
            "healthy" if link_ok else "untrusted",
            accent=theme.GREEN if link_ok else theme.MAGENTA,
        )
