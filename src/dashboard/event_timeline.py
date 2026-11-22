"""Event timeline: one chronological record of everything that happened.

The timeline merges four sources into one ordering, which is the only way a
demo-day incident can actually be reconstructed afterwards:

- ``lifecycle`` - state transitions and operator requests
- ``tx`` - commands written to the port
- ``rx`` - lines returned by the firmware
- ``operator`` - key presses and session control

Keeping them in one list, in one time order, is what lets an operator answer
"did the board reply before or after the buzzer started?" without cross-reading
three panes.

The row cap is deliberate. A timeline that grows without limit will eventually
make the dashboard slow exactly when it is needed; the oldest rows are dropped
and the count of dropped rows is displayed so the truncation is visible.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .state_models import TimelineEntry
from .widgets import Card


#: How many rows are kept. Enough for a full demo, small enough to stay cheap.
MAX_ROWS = 300

#: Colour for each event source. TX is cyan (we spoke), RX is muted (the board
#: spoke), operator is orange (a human acted), lifecycle uses its state colour.
_SOURCE_COLOURS: dict[str, str] = {
    "tx": theme.CYAN,
    "rx": theme.TEXT_MUTED,
    "operator": theme.ORANGE,
    "lifecycle": theme.TEXT,
}


class EventTimeline(Card):
    """Scrolling, colour-coded log of lifecycle, operator, TX and RX events."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "Event timeline",
            caption="Lifecycle transitions, operator presses and every serial "
            "line in one time order. Newest at the bottom.",
            parent=parent,
        )

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["TIME", "SOURCE", "STATE", "EVENT"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 74)
        self.table.setColumnWidth(2, 152)

        self.body.addWidget(self.table, 1)

        self.footer = QLabel("No events yet")
        self.footer.setObjectName("CardCaption")
        self.body.addWidget(self.footer)

        self._dropped = 0

    # -- mutation -----------------------------------------------------------

    def add(self, entry: TimelineEntry) -> None:
        """Append one entry, trimming the oldest rows when over the cap."""

        row = self.table.rowCount()
        self.table.insertRow(row)

        colour = (
            theme.state_accent(_state_from_name(entry.state))
            if entry.source == "lifecycle" and entry.state
            else _SOURCE_COLOURS.get(entry.source, theme.TEXT)
        )

        values = (
            f"{entry.at:,.1f}",
            entry.source.upper(),
            entry.state or "--",
            entry.message,
        )

        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)

            if column == 0:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            item.setForeground(_brush(colour))
            self.table.setItem(row, column, item)

        while self.table.rowCount() > MAX_ROWS:
            self.table.removeRow(0)
            self._dropped += 1

        self.table.scrollToBottom()
        self._refresh_footer()

    def add_many(self, entries) -> None:  # noqa: ANN001
        for entry in entries:
            self.add(entry)

    def clear(self) -> None:
        self.table.setRowCount(0)
        self._dropped = 0
        self._refresh_footer()

    def _refresh_footer(self) -> None:
        visible = self.table.rowCount()

        if not visible:
            self.footer.setText("No events yet")
            return

        text = f"{visible} events shown"
        if self._dropped:
            text += f"  \u00b7  {self._dropped} older events dropped"
        self.footer.setText(text)

    # -- inspection ---------------------------------------------------------

    def row_texts(self) -> list[tuple[str, str, str, str]]:
        """All rows as tuples. Used by the export and by the tests."""

        rows = []

        for row in range(self.table.rowCount()):
            rows.append(
                tuple(
                    self.table.item(row, column).text()
                    if self.table.item(row, column) is not None
                    else ""
                    for column in range(4)
                )
            )

        return rows


def _brush(colour: str):
    from PySide6.QtGui import QBrush, QColor

    return QBrush(QColor(colour))


def _state_from_name(name: str):
    """Best-effort state name to ``OperatorState`` for colour lookup."""

    from tools.incident_lifecycle import OperatorState

    try:
        return OperatorState[name]
    except KeyError:
        return None
