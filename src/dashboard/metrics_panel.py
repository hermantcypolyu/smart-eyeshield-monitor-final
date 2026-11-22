"""Metrics panel: the locked-test evidence, read from the experiment CSV.

This panel shows measurements that already exist in ``experiments/ai_metrics.csv``.
It does not compute, estimate or interpolate anything: a metrics panel that
invented a number would be worse than an empty one, because the whole point of
the locked test split is that the figure was measured once and never touched
again.

If the file is missing or malformed the panel says so and shows nothing. It never
falls back to a plausible-looking default.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .adapters import MetricsTable, format_metric, load_metrics
from .widgets import Card, MetricTile, divider


class MetricsPanel(Card):
    """Precision, recall, F1 and mAP from the locked test split."""

    def __init__(
        self,
        table: MetricsTable | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            "Model evidence",
            caption="Measured once on the locked test split, read directly from "
            "experiments/ai_metrics.csv. No figure here is computed by the "
            "dashboard.",
            parent=parent,
        )

        self.table = table if table is not None else load_metrics()

        if not self.table.available:
            self._build_unavailable()
            return

        self._build_available()

    # -- states -------------------------------------------------------------

    def _build_unavailable(self) -> None:
        message = QLabel(self.table.error or "Metrics unavailable.")
        message.setWordWrap(True)
        message.setStyleSheet(
            f"color: {theme.AMBER}; font-size: 11px; font-weight: 600;"
        )
        self.body.addWidget(message)

        path_label = QLabel(str(self.table.path) if self.table.path else "")
        path_label.setObjectName("CardCaption")
        path_label.setWordWrap(True)
        self.body.addWidget(path_label)

        note = QLabel(
            "The dashboard will not substitute an estimate. Restore the CSV or "
            "re-run the evaluation to repopulate this panel."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        self.body.addWidget(note)
        self.body.addStretch(1)

    def _build_available(self) -> None:
        row = self.table.rows[0]

        # Provenance first: which test split these numbers came from, and which
        # model they describe. The distinction matters because the demo runs V2
        # while the locked figures are V1, and showing one under the other's name
        # would overstate what has actually been measured.
        provenance = QLabel(
            f"V1 LOCKED TEST \u2014 {row.test_id} / {row.test_images} TEST IMAGES"
        )
        provenance.setObjectName("Chip")
        provenance.setWordWrap(True)
        provenance.setStyleSheet(
            f"color: {theme.HEADER_WHITE};"
            f"background-color: {theme.CYAN_DIM};"
            f"border: 1px solid {theme.CYAN};"
            "border-radius: 3px;"
            "padding: 2px 8px;"
            "font-size: 10px;"
            "font-weight: 700;"
        )
        self.body.addWidget(provenance, 0, Qt.AlignLeft)
        self.provenance_label = provenance

        subtitle = QLabel(
            f"{row.model}  \u00b7  {row.dataset}/{row.split}"
        )
        subtitle.setObjectName("CardCaption")
        subtitle.setWordWrap(True)
        self.body.addWidget(subtitle)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        tiles = (
            ("Precision", format_metric(row.precision)),
            ("Recall", format_metric(row.recall)),
            ("F1", format_metric(row.f1)),
            ("mAP@50", format_metric(row.map50)),
            ("mAP@50-95", format_metric(row.map50_95)),
        )

        self.tiles: dict[str, MetricTile] = {}

        for index, (label, value) in enumerate(tiles):
            tile = MetricTile(label, value=value, accent=theme.CYAN)
            grid.addWidget(tile, index // 3, index % 3)
            self.tiles[label] = tile

        self.body.addLayout(grid)
        self.body.addWidget(divider())

        # The subject of the locked test is not the model on the demo laptop.
        # Saying so on the panel is the difference between evidence and a claim.
        target = QLabel(
            "V2 TARGET-DOMAIN MODEL \u2014 LIVE ACCEPTANCE: PRELIMINARY / "
            "NOT FORMALLY PROMOTED"
        )
        target.setWordWrap(True)
        target.setStyleSheet(
            f"color: {theme.AMBER}; font-size: 10px; font-weight: 600;"
        )
        self.body.addWidget(target)
        self.target_label = target

        recorded = QLabel(
            f"Recorded {row.recorded_at}" if row.recorded_at else ""
        )
        recorded.setObjectName("CardCaption")
        recorded.setWordWrap(True)
        self.body.addWidget(recorded)

        if row.notes:
            notes = QLabel(row.notes)
            notes.setWordWrap(True)
            notes.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 10px;")
            self.body.addWidget(notes)

        self.body.addStretch(1)
