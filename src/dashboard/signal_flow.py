"""Signal-flow diagram: the honest path from pixels to pins.

Drawn as a chain of stages, each annotated with the artefact it produces and
whether it is currently live:

``camera`` -> ``detector`` -> ``temporal filter`` -> ``lifecycle`` -> ``serial`` -> ``pins``

The point of drawing it is to locate a fault. When the buzzer does not sound, the
operator needs to know which stage stopped: no frames, no detections, a
cancelled timer, a latched incident with no TX, TX with no reply, or a reply the
board contradicts in ``STATUS``. A single "hardware fault" banner would hide all
six of those.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import theme


#: Stage id -> display name. Order is the flow order and is what gets painted.
STAGES: tuple[tuple[str, str], ...] = (
    ("camera", "CAMERA"),
    ("detector", "YOLO"),
    ("filter", "FILTER"),
    ("lifecycle", "LIFECYCLE"),
    ("serial", "SERIAL"),
    ("pins", "PINS"),
)


class SignalFlowWidget(QWidget):
    """Six stages in a row, drawn live/blocked, with the break point marked."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(74)
        self._live: dict[str, bool] = {key: False for key, _ in STAGES}
        self._break_at: str = ""
        self._notes: dict[str, str] = {}

    def set_flow(
        self,
        live: dict[str, bool],
        *,
        break_at: str = "",
        notes: dict[str, str] | None = None,
    ) -> None:
        self._live = {key: bool(live.get(key, False)) for key, _ in STAGES}
        self._break_at = break_at
        self._notes = dict(notes or {})
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        try:
            width = max(1, self.width())
            count = len(STAGES)
            gap = 10.0
            box_width = max(40.0, (width - gap * (count - 1)) / count)
            box_height = 30.0
            top = 4.0

            label_font = QFont("Helvetica Neue", 7)
            label_font.setBold(True)

            for index, (key, name) in enumerate(STAGES):
                left = index * (box_width + gap)
                box = QRectF(left, top, box_width, box_height)
                live = self._live.get(key, False)

                if live:
                    fill = theme.CYAN
                    text = theme.NAVY
                    pen = QPen(QColor(theme.CYAN), 1.4)
                else:
                    fill = QColor(0, 0, 0, 0)
                    text = theme.GREY
                    pen = QPen(QColor(theme.GREY_DIM), 1.4)

                painter.setPen(pen)
                painter.setBrush(fill)
                painter.drawRoundedRect(box, 4, 4)

                painter.setFont(label_font)
                painter.setPen(QColor(text))
                painter.drawText(box, Qt.AlignCenter, name)

                if index < count - 1:
                    arrow_start = QRectF(left + box_width, top + box_height / 2 - 1, gap, 2)
                    blocked = key == self._break_at
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(
                        QColor(theme.RED if blocked else theme.GREY_DIM)
                    )
                    painter.drawRect(arrow_start)

                note = self._notes.get(key, "")
                if note:
                    painter.setPen(QColor(theme.TEXT_MUTED))
                    painter.setFont(QFont("Helvetica Neue", 7))
                    painter.drawText(
                        QRectF(left, top + box_height + 4, box_width, 14),
                        Qt.AlignHCenter | Qt.AlignTop,
                        note,
                    )

            if self._break_at:
                painter.setPen(QColor(theme.RED))
                painter.setFont(QFont("Helvetica Neue", 8))
                painter.drawText(
                    QRectF(0, self.height() - 14, width, 14),
                    Qt.AlignHCenter | Qt.AlignVCenter,
                    f"FLOW STOPPED AFTER: {self._break_at.upper()}",
                )
        finally:
            painter.end()
