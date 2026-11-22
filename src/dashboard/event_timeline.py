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

**Meaningful versus raw.** Only the rows that carry the story are shown by
default: state transitions, operator actions, refusals and faults. The raw
serial chatter - every ``STATUS`` line, every ``HEARTBEAT``, every boot banner -
is B-roll. It is still recorded, still exported, and still visible in the
Diagnostics drawer, because deleting evidence to tidy a screen would be a very
bad trade. ``showing`` selects which of the two views is painted; ``row_texts``
always returns everything so exports are unaffected.

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

#: Raw serial lines that are, on their own, not part of the narrative. They stay
#: in the record and in the export; they do not get a row in the LIVE timeline.
_RAW_PREFIXES: tuple[str, ...] = (
    "OK HEARTBEAT",
    "HEARTBEAT",
    "OK STATUS",
    "STATUS ",
    "OK RESET_READY",
    "BOOT",
    "ESP-ROM",
    "READY",
    "Phase ",
)

#: Substrings that promote an otherwise-raw line back into the narrative, because
#: a refusal or a fault is never noise.
_SIGNIFICANT_SUBSTRINGS: tuple[str, ...] = (
    "ERR ",
    "FAULT",
    "TIMEOUT",
    "BLOCKED",
    "QUEUED",
    "refused",
    "failure",
    "mismatch",
)


def is_meaningful(entry: TimelineEntry) -> bool:
    """True when ``entry`` belongs in the operator-facing narrative.

    Lifecycle transitions and operator actions always count. ``tx`` counts when
    it carries a lifecycle command rather than a poll. ``rx`` counts only when it
    is a refusal, a fault, or a reply to something the operator actually did.
    """

    if entry.source in ("lifecycle", "operator"):
        return True

    message = entry.message.strip()
    upper = message.upper()

    if any(token in upper for token in _SIGNIFICANT_SUBSTRINGS):
        return True

    if entry.source == "tx":
        # Polls are bookkeeping; lifecycle commands are the story.
        return not upper.startswith(("STATUS", "HEARTBEAT"))

    if entry.source == "rx":
        return not any(upper.startswith(prefix.upper()) for prefix in _RAW_PREFIXES)

    return True


class _EventTable(QTableWidget):
    """The four-column table, shared by both timeline views."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, 4, parent)
        self.setHorizontalHeaderLabels(["TIME", "SOURCE", "STATE", "EVENT"])
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setShowGrid(False)

        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.setColumnWidth(1, 74)
        self.setColumnWidth(2, 152)


class EventTimeline(Card):
    """Scrolling, colour-coded log of lifecycle, operator, TX and RX events.

    The same object owns both views so the record cannot diverge: ``table`` is the
    meaningful view, ``raw_table`` is the diagnostics view, and both are fed from
    the single ``_entries`` list.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "Event timeline",
        display_limit: int | None = None,
    ) -> None:
        super().__init__(
            title,
            caption="Meaningful transitions only. Raw serial traffic is kept in "
            "full under Diagnostics.",
            parent=parent,
        )

        #: Rows shown at once. ``None`` shows the whole record. The LIVE tab uses
        #: a small limit because an operator watching a lamp does not need 300
        #: rows, and the ANALYTICS tab shows everything.
        self.display_limit = display_limit

        self.table = _EventTable()
        self.body.addWidget(self.table, 1)

        self.raw_table = _EventTable()
        self.raw_table.setVisible(False)
        self.body.addWidget(self.raw_table, 1)

        self.footer = QLabel("No events yet")
        self.footer.setObjectName("CardCaption")
        self.body.addWidget(self.footer)

        #: Every entry ever added, in order. The source of truth for both views
        #: and for the export.
        self._entries: list[TimelineEntry] = []
        self._dropped = 0
        self._showing_raw = False

    # -- mutation -----------------------------------------------------------

    def add(self, entry: TimelineEntry) -> None:
        """Record one entry and repaint whichever view is showing."""

        self._entries.append(entry)

        while len(self._entries) > MAX_ROWS:
            self._entries.pop(0)
            self._dropped += 1

        if self._showing_raw or is_meaningful(entry):
            self._append_row(
                self.raw_table if self._showing_raw else self.table, entry
            )
            self._trim(self.raw_table if self._showing_raw else self.table)
            self._scroll_active()
            self._refresh_footer()

    def add_many(self, entries) -> None:  # noqa: ANN001
        for entry in entries:
            self.add(entry)

    def clear(self) -> None:
        self._entries.clear()
        self.table.setRowCount(0)
        self.raw_table.setRowCount(0)
        self._dropped = 0
        self._refresh_footer()

    # -- views --------------------------------------------------------------

    @property
    def showing(self) -> str:
        return "raw" if self._showing_raw else "meaningful"

    def show_raw(self, raw: bool) -> None:
        """Switch between the meaningful narrative and the full raw record."""

        self._showing_raw = bool(raw)

        self.table.setVisible(not self._showing_raw)
        self.raw_table.setVisible(self._showing_raw)

        target = self.raw_table if self._showing_raw else self.table
        target.setRowCount(0)

        for entry in self._entries:
            if self._showing_raw or is_meaningful(entry):
                self._append_row(target, entry)

        self._scroll_active()
        self._refresh_footer()

    def toggle_raw(self) -> None:
        self.show_raw(not self._showing_raw)

    def _scroll_active(self) -> None:
        (self.raw_table if self._showing_raw else self.table).scrollToBottom()

    def _append_row(self, table: QTableWidget, entry: TimelineEntry) -> None:
        row = table.rowCount()
        table.insertRow(row)

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
            table.setItem(row, column, item)

    def _trim(self, table: QTableWidget) -> None:
        limit = (
            self.display_limit
            if self.display_limit and not self._showing_raw
            else MAX_ROWS
        )

        while table.rowCount() > limit:
            table.removeRow(0)

    def _refresh_footer(self) -> None:
        total = len(self._entries)

        if not total:
            self.footer.setText("No events yet")
            return

        meaningful = sum(1 for entry in self._entries if is_meaningful(entry))

        if self._showing_raw:
            text = f"{total} raw events shown"
        else:
            text = f"{meaningful} of {total} events shown as meaningful"
            if total > meaningful:
                text += f"  \u00b7  {total - meaningful} raw lines held in Diagnostics"

        if self._dropped:
            text += f"  \u00b7  {self._dropped} older events dropped"

        self.footer.setText(text)

    # -- inspection ---------------------------------------------------------

    @property
    def entries(self) -> tuple[TimelineEntry, ...]:
        return tuple(self._entries)

    def meaningful_entries(self) -> tuple[TimelineEntry, ...]:
        return tuple(entry for entry in self._entries if is_meaningful(entry))

    def row_texts(self) -> list[tuple[str, str, str, str]]:
        """Every row as a tuple, in order. Used by the export and the tests.

        Always the full record, never the filtered view: the CSV is an evidence
        artefact and must not depend on which tab happened to be open.
        """

        return [
            (
                f"{entry.at:,.1f}",
                entry.source.upper(),
                entry.state or "--",
                entry.message,
            )
            for entry in self._entries
        ]

    def visible_row_count(self) -> int:
        return (
            self.raw_table.rowCount() if self._showing_raw else self.table.rowCount()
        )


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
