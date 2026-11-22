"""Signal-flow diagram: the honest path from pixels to pins.

Drawn as a chain of nodes, each annotated with the real technical artefact it
produces and whether it is currently live:

``webcam`` -> ``yolov8n`` -> ``temporal filter`` -> ``lifecycle`` -> ``serial``
-> ``esp32`` -> ``outputs``

The point of drawing it is to locate a fault. When the buzzer does not sound, the
operator needs to know which node stopped: no frames, no detections, a cancelled
timer, a latched incident with no TX, TX with no reply, or a reply the board
contradicts in ``STATUS``. A single "hardware fault" banner would hide all six of
those.

Everything is drawn with ``QPainter`` primitives - no images, no icon fonts, no
network assets - so the diagram renders identically on any machine and inside a
screenshot for the report.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import theme


#: Node id -> display name. Order is the flow order and is what gets painted.
STAGES: tuple[tuple[str, str], ...] = (
    ("camera", "WEBCAM"),
    ("detector", "YOLOv8n V2"),
    ("filter", "TEMPORAL FILTER"),
    ("lifecycle", "INCIDENT LIFECYCLE"),
    ("serial", "USB SERIAL"),
    ("esp32", "ESP32-S3"),
    ("outputs", "PHYSICAL OUTPUT"),
)

#: Order used to decide where the chain breaks. ``outputs`` is judged from the
#: firmware's own ``STATUS`` reply, never from the laptop's belief.
_BREAK_ORDER: tuple[str, ...] = tuple(key for key, _ in STAGES)

#: Keys kept from the previous single-row layout, so an older ``set_flow`` call
#: that speaks of ``model`` or ``pins`` still lands on a real node.
_ALIASES: dict[str, str] = {
    "model": "detector",
    "yolo": "detector",
    "detection": "detector",
    "pins": "outputs",
    "board": "esp32",
    "usb": "serial",
}


class SignalFlowWidget(QWidget):
    """Seven nodes, drawn live or blocked, with the break point marked."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(96)
        self._live: dict[str, bool] = {key: False for key, _ in STAGES}
        #: The break marker exactly as the caller supplied it. Kept verbatim
        #: because callers and tests speak of the node they named, and silently
        #: rewriting it would make ``break_at="model"`` unreadable to them.
        self._break_at: str = ""
        #: The node the break marker resolved to, for painting.
        self._break_node: str = ""
        self._notes: dict[str, str] = {}

    @property
    def stages(self) -> tuple[tuple[str, str], ...]:
        return STAGES

    def set_flow(
        self,
        live: dict[str, bool],
        *,
        break_at: str = "",
        notes: dict[str, str] | None = None,
    ) -> None:
        resolved: dict[str, bool] = {key: False for key, _ in STAGES}

        for key, value in live.items():
            resolved[_ALIASES.get(key, key)] = bool(value)

        self._live = resolved

        # Normalise the break marker separately, so ``break_at="model"`` still
        # highlights the detector node while ``_break_at`` stays as supplied.
        self._break_at = break_at
        self._break_node = _ALIASES.get(break_at, break_at)

        self._notes = {
            _ALIASES.get(key, key): value for key, value in (notes or {}).items()
        }
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        try:
            width = max(1, self.width())
            count = len(STAGES)
            gap = 8.0
            node_width = max(40.0, (width - gap * (count - 1)) / count)
            node_height = 26.0
            top = 3.0

            title_font = QFont("Helvetica Neue", 6)
            title_font.setBold(True)
            note_font = QFont("Helvetica Neue", 6)
            value_font = QFont("Helvetica Neue", 7)
            value_font.setBold(True)

            for index, (key, name) in enumerate(STAGES):
                left = index * (node_width + gap)
                box = QRectF(left, top, node_width, node_height)
                live = self._live.get(key, False)
                broken = key == self._break_node

                if broken:
                    stroke = QColor(theme.RED)
                    fill = QColor(theme.RED_DIM)
                    text = theme.HEADER_WHITE
                elif live:
                    stroke = QColor(theme.CYAN)
                    fill = QColor(theme.CYAN_DIM)
                    text = theme.HEADER_WHITE
                else:
                    stroke = QColor(theme.GREY_DIM)
                    fill = QColor(0, 0, 0, 0)
                    text = theme.GREY

                painter.setPen(QPen(stroke, 1.4, Qt.SolidLine if live else Qt.DashLine))
                painter.setBrush(fill)
                painter.drawRoundedRect(box, 4, 4)

                painter.setFont(title_font)
                painter.setPen(QColor(text))
                painter.drawText(
                    box.adjusted(3, 0, -3, 0),
                    Qt.AlignCenter,
                    name.upper(),
                )

                # The concrete figure this node is producing right now. Without
                # it the boxes are decoration; with it they are instrumentation.
                note = self._notes.get(key, "")
                if note:
                    painter.setFont(value_font)
                    painter.setPen(
                        QColor(theme.RED if broken else theme.TEXT_MUTED)
                    )
                    painter.drawText(
                        QRectF(left, top + node_height + 2, node_width, 12),
                        Qt.AlignHCenter | Qt.AlignTop,
                        note,
                    )

                if index < count - 1:
                    self._draw_arrow(
                        painter,
                        left + node_width,
                        top + node_height / 2,
                        gap,
                        blocked=broken,
                        live=live,
                    )
        finally:
            painter.end()

    def _draw_arrow(
        self,
        painter: QPainter,
        x: float,
        y: float,
        width: float,
        *,
        blocked: bool,
        live: bool,
    ) -> None:
        colour = QColor(theme.RED if blocked else (theme.CYAN if live else theme.GREY_DIM))

        painter.setPen(QPen(colour, 1.4))
        painter.drawLine(QPointF(x + 1, y), QPointF(x + width - 1, y))

        if blocked:
            # A dashed cross reads as "flow stops here" without needing a legend.
            painter.setPen(QPen(colour, 1.4, Qt.DashLine))
            painter.drawLine(QPointF(x + 1, y - 4), QPointF(x + width - 1, y + 4))

    # -- inspection ---------------------------------------------------------

    @property
    def break_at(self) -> str:
        return self._break_at

    @property
    def break_node(self) -> str:
        """The node the break marker resolved to. ``detector`` for ``model``."""

        return self._break_node

    def node_note(self, key: str) -> str:
        return self._notes.get(_ALIASES.get(key, key), "")

    def is_live(self, key: str) -> bool:
        return self._live.get(_ALIASES.get(key, key), False)

    @property
    def stopped_after(self) -> str:
        """The node after which the chain stops, or "" when everything is live."""

        return self._break_node

    def summary_line(self) -> str:
        """One sentence naming the first broken node, for the card caption."""

        if not self._break_node:
            return "Every node in the chain is reporting live."

        name = dict(STAGES).get(self._break_node, self._break_node)
        note = self._notes.get(self._break_node, "")
        detail = f" ({note})" if note else ""
        return f"FLOW STOPPED AFTER: {name.upper()}{detail}"


def first_broken(live: dict[str, bool]) -> str:
    """The first node in flow order that is not live, or "" when all are.

    Exposed as a function because both the widget and the window need the same
    answer, and two implementations would eventually disagree.
    """

    resolved = {_ALIASES.get(key, key): value for key, value in live.items()}

    for key in _BREAK_ORDER:
        if not resolved.get(key, False):
            return key

    return ""
