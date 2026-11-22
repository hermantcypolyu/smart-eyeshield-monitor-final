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
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .state_models import DashboardSnapshot, HealthItem
from .widgets import Card, Dot, divider


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

        detail = QLabel(item.detail)
        detail.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        detail.setWordWrap(True)
        layout.addWidget(detail, 1)


class HealthPanel(Card):
    """Subsystem health table with the safety-input rows marked."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "Subsystem health",
            caption="Camera, model and serial link are safety inputs: losing any "
            "of them raises FAULT and parks the outputs. Blynk and Wi-Fi are not "
            "part of this build, so they are NOT CONFIGURED rather than offline.",
            parent=parent,
        )

        self.rows_layout = QVBoxLayout()
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(0)
        self.body.addLayout(self.rows_layout)

        self.body.addWidget(divider())

        legend = QLabel(
            "Safety inputs: "
            + ", ".join(sorted(SAFETY_INPUTS))
            + ". Informational rows never affect the verdict."
        )
        legend.setWordWrap(True)
        legend.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 10px;")
        self.body.addWidget(legend)

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

        for item in snapshot.health:
            row = HealthRow(item)
            self.rows_layout.addWidget(row)
            self._rows.append(row)
