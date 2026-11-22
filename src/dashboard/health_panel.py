"""Health panel: every subsystem, and explicitly what is not configured.

The value of this panel is the honest middle states. A build that shows only
ONLINE and OFFLINE forces a lie: Blynk is not offline, it simply does not exist
in this project, and Wi-Fi is not degraded, it is unused. Both read
``NOT CONFIGURED``.

Subsystem health is also a safety input, not just diagnostics - losing the camera
or the serial link raises ``FAULT`` and parks the outputs, so the panel names the
lifecycle's own reason and marks that row as a safety input.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .state_models import DashboardSnapshot, HealthItem
from .widgets import Card, Dot


#: Subsystems whose failure raises FAULT. Everything else is informational.
SAFETY_INPUTS = frozenset({"Camera", "Model", "Serial link"})


class HealthRow(QWidget):
    """One subsystem: dot, name, status word, detail."""

    def __init__(
        self,
        item: HealthItem,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(8)

        colour = theme.health_colour(item.status)
        self.dot = Dot(colour, 9)
        layout.addWidget(self.dot)

        name = QLabel(item.name)
        name.setStyleSheet(f"color: {theme.TEXT}; font-size: 12px; font-weight: 600;")
        name.setMinimumWidth(88)
        layout.addWidget(name)

        status = QLabel(item.status)
        status.setStyleSheet(f"color: {colour}; font-size: 10px; font-weight: 700;")
        status.setMinimumWidth(104)
        layout.addWidget(status)

        # Detail text is elided rather than wrapped: a wrapped line makes the
        # whole page height-for-width dependent, and the extra line at the
        # bottom of the right column was enough to force a scroll bar at 1080p.
        # The full text stays reachable as a tooltip.
        self.detail = QLabel(item.detail)
        self.detail.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        self.detail.setWordWrap(False)
        self.detail.setToolTip(item.detail)
        layout.addWidget(self.detail, 1)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        metrics = QFontMetrics(self.detail.font())
        self.detail.setText(
            metrics.elidedText(
                self.detail.toolTip(),
                Qt.ElideRight,
                max(self.detail.width(), 0),
            )
        )


class HealthPanel(Card):
    """Subsystem health table with the safety-input rows marked."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "Subsystem health",
            caption="Camera, model and serial are safety inputs: losing one raises "
            "FAULT and parks the outputs.",
            parent=parent,
        )

        # Two columns: eight one-line rows stacked would dominate the right-hand
        # column, and every row is short enough to halve the width without
        # wrapping.
        self.rows_layout = QGridLayout()
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setHorizontalSpacing(14)
        self.rows_layout.setVerticalSpacing(1)
        self.body.addLayout(self.rows_layout)

        self.empty_label = QLabel("NO HARDWARE STATUS YET")
        self.empty_label.setObjectName("EmptyState")
        self.empty_label.setWordWrap(True)
        self.body.addWidget(self.empty_label)

        self.body.addStretch(1)

        self._rows: list[HealthRow] = []
        self._signature: tuple[tuple[str, str, str], ...] = ()

    def update_from(self, snapshot: DashboardSnapshot) -> None:
        signature = tuple((h.name, h.status, h.detail) for h in snapshot.health)

        # Health changes rarely; rebuilding on every frame would burn CPU for no
        # visible difference and would fight the layout.
        if signature == self._signature:
            return

        self._signature = signature

        for row in self._rows:
            row.setParent(None)
            row.deleteLater()

        self._rows = []

        for index, item in enumerate(snapshot.health):
            row = HealthRow(item)
            self.rows_layout.addWidget(row, index // 2, index % 2)
            self._rows.append(row)

        # An empty health list is a genuine state - the window opens before the
        # first poll - and must say so rather than render an empty card that looks
        # like "everything is fine".
        self.empty_label.setVisible(not snapshot.health)
