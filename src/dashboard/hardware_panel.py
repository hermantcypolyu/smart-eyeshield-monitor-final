"""Hardware panel: what the board is actually doing versus what we asked.

This panel exists to make one distinction impossible to miss:

- **Requested** - the command the laptop wrote to the port.
- **Acknowledged** - the reply code the firmware returned for it.
- **Physical** - the pin states the firmware reports in its own ``STATUS`` line.

A dashboard that showed all three as one green dot would hide exactly the class
of bug this project spent weeks chasing, where the laptop believed the alarm was
acknowledged while the board was still buzzing. So the three are drawn as three
separate columns, each with its own provenance label.

The LED and buzzer indicators are driven by the firmware ``STATUS`` values, never
by lifecycle intent. When no ``STATUS`` has arrived, they read ``UNKNOWN`` rather
than defaulting to off, because "we do not know" and "the lamp is out" are
different facts.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .state_models import BoardSnapshot, DashboardSnapshot, LinkState, OutputState
from .widgets import Card, Dot, KeyValueRow, StatusPill, divider


#: Output state -> (colour, text). ``UNKNOWN`` is grey and says so.
_OUTPUT_STYLE: dict[OutputState, tuple[str, str]] = {
    OutputState.ON: (theme.TEXT, "ON"),
    OutputState.OFF: (theme.TEXT_MUTED, "off"),
    OutputState.UNKNOWN: (theme.GREY, "UNKNOWN"),
}

#: Colour for each lamp when it is on. Green lamp uses green, red lamp uses red;
#: this is the one place green is allowed to appear for a lamp, and it is the
#: lamp's own colour, not a compliance claim.
_LAMP_COLOUR = {
    "green": theme.GREEN,
    "red": theme.RED,
    "buzzer": theme.RED,
}


class LampRow(QWidget):
    """One physical output: dot, name, state word, and its GPIO."""

    def __init__(
        self,
        name: str,
        gpio: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.dot = Dot(theme.GREY, 11)
        layout.addWidget(self.dot)

        label = QLabel(name)
        label.setStyleSheet(f"color: {theme.TEXT}; font-size: 12px; font-weight: 600;")
        layout.addWidget(label)

        gpio_label = QLabel(gpio)
        gpio_label.setObjectName("CardCaption")
        layout.addWidget(gpio_label)

        layout.addStretch(1)

        self.value_label = QLabel("UNKNOWN")
        self.value_label.setObjectName("MonoValue")
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.value_label)

        self._key = name.split()[0].lower()

    def set_output(self, state: OutputState) -> None:
        colour, text = _OUTPUT_STYLE[state]
        self.value_label.setText(text)
        self.value_label.setStyleSheet(f"color: {colour};")

        if state is OutputState.ON:
            self.dot.set_colour(_LAMP_COLOUR.get(self._key, theme.CYAN))
        elif state is OutputState.OFF:
            self.dot.set_colour(theme.GREY_DIM)
        else:
            self.dot.set_colour(theme.GREY)


class ProvenanceColumn(QWidget):
    """One column of the request / acknowledgement / physical triple."""

    def __init__(
        self,
        heading: str,
        provenance: str,
        accent: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        title = QLabel(heading.upper())
        title.setObjectName("MetricLabel")
        title.setStyleSheet(f"color: {accent}; font-size: 10px; font-weight: 700;")
        layout.addWidget(title)

        sub = QLabel(provenance)
        sub.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 10px;")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        self.value_label = QLabel("--")
        self.value_label.setObjectName("MonoValue")
        self.value_label.setWordWrap(True)
        self.value_label.setStyleSheet(f"color: {theme.TEXT}; font-size: 12px;")
        layout.addWidget(self.value_label)
        layout.addStretch(1)

    def set_value(self, text: str, *, accent: str | None = None) -> None:
        self.value_label.setText(text or "--")
        self.value_label.setStyleSheet(
            f"color: {accent or theme.TEXT}; font-size: 12px;"
        )


class HardwarePanel(Card):
    """ESP32 node: link, three lamps, and the provenance triple."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "ESP32 node",
            caption="Lamp states come from the firmware's own STATUS line, not "
            "from laptop intent. Requested / acknowledged / physical are shown "
            "separately because they can disagree.",
            parent=parent,
        )

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)

        self.link_pill = StatusPill(LinkState.CLOSED.label, theme.GREY)
        top.addWidget(self.link_pill)
        top.addStretch(1)

        self.port_label = QLabel("--")
        self.port_label.setObjectName("CardCaption")
        top.addWidget(self.port_label)

        self.body.addLayout(top)

        self.green_row = LampRow("Green LED", "GPIO11")
        self.red_row = LampRow("Red LED", "GPIO12")
        self.buzzer_row = LampRow("Buzzer", "GPIO14")

        for row in (self.green_row, self.red_row, self.buzzer_row):
            self.body.addWidget(row)

        self.body.addWidget(divider())

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)

        self.requested_col = ProvenanceColumn(
            "Requested", "lifecycle TX", theme.CYAN
        )
        self.acked_col = ProvenanceColumn(
            "Acknowledged", "firmware reply", theme.ORANGE
        )
        self.physical_col = ProvenanceColumn(
            "Physical", "firmware STATUS", theme.GREEN
        )

        for column, index in (
            (self.requested_col, 0),
            (self.acked_col, 1),
            (self.physical_col, 2),
        ):
            grid.addWidget(column, 0, index)

        self.body.addLayout(grid)

        self.body.addWidget(divider())

        self.rows = {
            "firmware": KeyValueRow("Firmware build"),
            "incident": KeyValueRow("Board incident"),
            "ack": KeyValueRow("Board ACK"),
            "reset_ready": KeyValueRow("Reset armed"),
            "rearm": KeyValueRow("Re-arms"),
            "status_age": KeyValueRow("Last STATUS"),
        }

        for row in self.rows.values():
            self.body.addWidget(row)

        self.body.addStretch(1)

    def update_from(
        self,
        snapshot: DashboardSnapshot,
        *,
        last_requested: str = "",
        last_acknowledged: str = "",
        now: float | None = None,
    ) -> None:
        board: BoardSnapshot = snapshot.board

        self.link_pill.set_status(
            board.link.label,
            theme.GREEN if board.link is LinkState.OPEN else theme.GREY,
        )

        self.port_label.setText(
            f"{board.port} @ {board.baud}" if board.port else "no port configured"
        )

        self.green_row.set_output(board.outputs.green)
        self.red_row.set_output(board.outputs.red)
        self.buzzer_row.set_output(board.outputs.buzzer)

        self.requested_col.set_value(last_requested, accent=theme.CYAN)
        self.acked_col.set_value(last_acknowledged, accent=theme.ORANGE)

        if board.outputs.reported:
            bits = []
            if board.outputs.green is OutputState.ON:
                bits.append("green")
            if board.outputs.red is OutputState.ON:
                bits.append("red")
            if board.outputs.buzzer is OutputState.ON:
                bits.append("buzzer")
            self.physical_col.set_value(
                ", ".join(bits) if bits else "all quiet",
                accent=theme.GREEN if bits else theme.TEXT_MUTED,
            )
        else:
            self.physical_col.set_value("not reported", accent=theme.GREY)

        self.rows["firmware"].set_value(board.firmware_version or "not reported")

        if board.incident_id is None:
            self.rows["incident"].set_value("--")
        else:
            self.rows["incident"].set_value(
                f"{board.incident_id} (open)" if board.incident_id else "0 (none)",
                accent=theme.RED if board.incident_id else theme.TEXT_MUTED,
            )

        self.rows["ack"].set_value(
            "--" if board.acknowledged is None else ("yes" if board.acknowledged else "no"),
            accent=theme.ORANGE if board.acknowledged else theme.TEXT_MUTED,
        )
        self.rows["reset_ready"].set_value(
            "--" if board.reset_ready is None else ("yes" if board.reset_ready else "no"),
            accent=theme.CYAN if board.reset_ready else theme.TEXT_MUTED,
        )
        self.rows["rearm"].set_value(
            "--" if board.rearm_count is None else str(board.rearm_count)
        )

        if board.status_at is None or now is None:
            self.rows["status_age"].set_value("--")
        else:
            age = max(0.0, now - board.status_at)
            stale = age > 3.0
            self.rows["status_age"].set_value(
                f"{age:.1f} s ago",
                accent=theme.MAGENTA if stale else theme.TEXT_MUTED,
            )
