"""Lifecycle diagram: the operator state machine, drawn with Qt graphics.

The diagram is a real ``QGraphicsScene`` rather than a grid of styled labels, for
three reasons that all matter during a demo:

- The *edges* are the subject. The state machine's rules live in the transitions,
  and a grid of boxes shows states but not rules.
- A blocked reset can be marked on the specific node it blocks, instead of being
  reduced to a sentence somewhere else.
- Geometry is fixed in scene coordinates and scaled by the view, so the picture
  looks identical at 1280x720 and at 1920x1080 instead of reflowing into a
  different diagram.

It is display-only. Nothing here mutates the lifecycle: clicking a node must not
be able to request a reset, because the reset path has its own gate and a diagram
that doubles as a control surface is a second way to do a dangerous thing.

``OperatorState`` has nine members and no ``RESET_ACCEPTED``: the board's accepted
reset returns the system to ``WAITING_FOR_TARGET``. The diagram therefore has its
own small node vocabulary so it can show the reset step the operator experiences,
and maps each ``OperatorState`` onto the node that represents it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)

from tools.incident_lifecycle import OperatorState

from . import theme


class Node(str, Enum):
    """Diagram nodes.

    Superset of ``OperatorState``: adds ``RESET_ACCEPTED``, which is a board event
    rather than a laptop state, and drops ``STARTUP`` because it is transient and
    would add a node that is never on screen for more than a second.
    """

    WAITING_FOR_TARGET = "WAITING_FOR_TARGET"
    SAFE = "SAFE"
    VIOLATION_PENDING = "VIOLATION_PENDING"
    ALARM_LATCHED = "ALARM_LATCHED"
    SAFE_CLEAR_PENDING = "SAFE_CLEAR_PENDING"
    SAFE_RECOVERED = "SAFE_RECOVERED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESET_ACCEPTED = "RESET_ACCEPTED"
    FAULT = "FAULT"


#: State -> diagram node. ``STARTUP`` is shown on ``WAITING_FOR_TARGET`` because
#: that is where the machine lands once health checks pass, and a node that is
#: never current would only add clutter.
STATE_TO_NODE: dict[OperatorState, Node] = {
    OperatorState.STARTUP: Node.WAITING_FOR_TARGET,
    OperatorState.WAITING_FOR_TARGET: Node.WAITING_FOR_TARGET,
    OperatorState.SAFE: Node.SAFE,
    OperatorState.VIOLATION_PENDING: Node.VIOLATION_PENDING,
    OperatorState.ALARM_LATCHED: Node.ALARM_LATCHED,
    OperatorState.SAFE_CLEAR_PENDING: Node.SAFE_CLEAR_PENDING,
    OperatorState.SAFE_RECOVERED: Node.SAFE_RECOVERED,
    OperatorState.ACKNOWLEDGED: Node.ACKNOWLEDGED,
    OperatorState.FAULT: Node.FAULT,
}

#: Node box size in scene units.
NODE_WIDTH = 156.0
NODE_HEIGHT = 36.0

#: Gaps between node boxes.
H_GAP = 34.0
V_GAP = 62.0

#: Where each node sits, as (row, column). Row 1 runs right-to-left so the chain
#: snakes, and ``RESET ACCEPTED`` can climb back to ``SAFE`` without a line
#: crossing the whole drawing.
NODE_GRID: dict[Node, tuple[int, int]] = {
    Node.WAITING_FOR_TARGET: (0, 0),
    Node.SAFE: (0, 1),
    Node.VIOLATION_PENDING: (0, 2),
    Node.ALARM_LATCHED: (0, 3),
    Node.SAFE_CLEAR_PENDING: (1, 3),
    Node.SAFE_RECOVERED: (1, 2),
    Node.ACKNOWLEDGED: (1, 1),
    Node.RESET_ACCEPTED: (1, 0),
    Node.FAULT: (2, 0),
}

#: One short subtitle per node, shown under the name. The brief asks for one
#: sentence at most, and these are deliberately clipped to a few words: the long
#: explanation lives in ``theme.STATE_STYLES[...].hint`` and in the panel hint.
NODE_SUBTITLES: dict[Node, str] = {
    Node.WAITING_FOR_TARGET: "no verdict",
    Node.SAFE: "goggles verified",
    Node.VIOLATION_PENDING: "2.00 s confirm",
    Node.ALARM_LATCHED: "outputs latched",
    Node.SAFE_CLEAR_PENDING: "2.00 s clear",
    Node.SAFE_RECOVERED: "recovery proven",
    Node.ACKNOWLEDGED: "buzzer silenced",
    Node.RESET_ACCEPTED: "board returned OK",
    Node.FAULT: "subsystem untrusted",
}

#: Display label per node. Read from the lifecycle's own operator vocabulary where
#: one exists, so the diagram and the rest of the UI cannot drift apart.
NODE_LABELS: dict[Node, str] = {
    **{
        node: theme.style_for_state(state).label
        for state, node in STATE_TO_NODE.items()
        if node is not Node.RESET_ACCEPTED
    },
    Node.RESET_ACCEPTED: "RESET ACCEPTED",
}

#: Transition edges, as (source, target, label).
EDGES: tuple[tuple[Node, Node, str], ...] = (
    (Node.WAITING_FOR_TARGET, Node.SAFE, "target"),
    (Node.SAFE, Node.VIOLATION_PENDING, "violation"),
    (Node.VIOLATION_PENDING, Node.ALARM_LATCHED, "confirmed"),
    (Node.ALARM_LATCHED, Node.SAFE_CLEAR_PENDING, "goggles"),
    (Node.SAFE_CLEAR_PENDING, Node.SAFE_RECOVERED, "cleared"),
    (Node.SAFE_RECOVERED, Node.ACKNOWLEDGED, "A / silence"),
    (Node.ACKNOWLEDGED, Node.RESET_ACCEPTED, "R + gate"),
    (Node.RESET_ACCEPTED, Node.SAFE, "rearmed"),
)

#: Nodes that can fall into FAULT. Every safety-critical stage, per the brief.
FAULT_SOURCES: tuple[Node, ...] = (
    Node.SAFE,
    Node.VIOLATION_PENDING,
    Node.ALARM_LATCHED,
    Node.SAFE_CLEAR_PENDING,
    Node.SAFE_RECOVERED,
)


def node_accent(node: Node) -> tuple[str, str]:
    """``(accent, dim)`` for a diagram node."""

    if node is Node.RESET_ACCEPTED:
        return theme.CYAN, theme.CYAN_DIM

    state = OperatorState[node.value]
    style = theme.style_for_state(state)
    return style.accent, style.dim


def _centre(row: int, column: int) -> QPointF:
    x = column * (NODE_WIDTH + H_GAP) + NODE_WIDTH / 2
    y = row * (NODE_HEIGHT + V_GAP) + NODE_HEIGHT / 2
    return QPointF(x, y)


class DiagramNode(QGraphicsItem):
    """One state box: outlined when inactive, filled when current."""

    def __init__(
        self,
        node: Node,
        *,
        active: bool = False,
        recent: bool = False,
        blocked: bool = False,
    ) -> None:
        super().__init__()
        self.node = node
        accent, _dim = node_accent(node)

        self._label = NODE_LABELS.get(node, node.value)
        self._subtitle = NODE_SUBTITLES.get(node, "")
        self._accent = accent
        self._active = active
        self._recent = recent
        self._blocked = blocked
        self.setToolTip(self._tooltip())

    def _tooltip(self) -> str:
        if self.node is Node.RESET_ACCEPTED:
            return (
                "RESET ACCEPTED \u2014 the board answered RESET with "
                "RESET_ACCEPTED. The machine returns to WAITING FOR TARGET, never "
                "straight to SAFE."
            )

        style = theme.style_for_state(OperatorState[self.node.value])
        return f"{style.label} \u2014 {style.hint}"

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt naming
        return QRectF(-NODE_WIDTH / 2, -NODE_HEIGHT / 2, NODE_WIDTH, NODE_HEIGHT)

    def set_display(
        self,
        *,
        active: bool,
        recent: bool = False,
        blocked: bool = False,
    ) -> None:
        if (
            active == self._active
            and recent == self._recent
            and blocked == self._blocked
        ):
            return

        self._active = active
        self._recent = recent
        self._blocked = blocked
        self.update()

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setRenderHint(QPainter.Antialiasing, True)
        box = self.boundingRect().adjusted(1, 1, -1, -1)

        accent = QColor(self._accent)
        _accent, dim = node_accent(self.node)

        if self._active:
            painter.setBrush(QBrush(accent))
            painter.setPen(QPen(QColor(theme.HEADER_WHITE), 2.0))
        elif self._recent:
            fill = QColor(dim)
            fill.setAlpha(210)
            painter.setBrush(QBrush(fill))
            painter.setPen(QPen(accent, 1.6))
        else:
            painter.setBrush(QBrush(QColor(0, 0, 0, 0)))
            muted = QColor(accent)
            muted.setAlpha(150)
            painter.setPen(QPen(muted, 1.2))

        if self._blocked and not self._active:
            painter.setPen(QPen(QColor(theme.RED), 1.2, Qt.DashLine))

        painter.drawRoundedRect(box, 5.0, 5.0)

        name_font = QFont(theme.FONT_FAMILY_QT, 8)
        name_font.setBold(True)
        painter.setFont(name_font)
        painter.setPen(
            QColor(theme.HEADER_WHITE if self._active else self._accent)
        )
        painter.drawText(
            QRectF(box.left() + 4, box.top() + 3, box.width() - 8, box.height() / 2),
            Qt.AlignCenter,
            self._label,
        )

        sub_font = QFont(theme.FONT_FAMILY_QT, 6)
        painter.setFont(sub_font)
        sub_colour = QColor(
            theme.HEADER_WHITE if self._active else theme.TEXT_MUTED
        )
        if self._active:
            sub_colour.setAlpha(220)
        painter.setPen(sub_colour)
        painter.drawText(
            QRectF(
                box.left() + 4,
                box.top() + box.height() / 2 - 3,
                box.width() - 8,
                box.height() / 2,
            ),
            Qt.AlignCenter,
            self._subtitle,
        )

        if self._blocked:
            painter.setFont(QFont(theme.FONT_FAMILY_QT, 6))
            painter.setPen(QColor(theme.RED))
            painter.drawText(
                QRectF(box.left(), box.bottom() + 1, box.width(), 10),
                Qt.AlignHCenter | Qt.AlignTop,
                "RESET BLOCKED",
            )


@dataclass
class _Edge:
    source: Node
    target: Node
    label: str
    item: QGraphicsPathItem


class LifecycleDiagram(QGraphicsView):
    """The state machine, drawn in a ``QGraphicsScene`` and fitted to the view.

    ``nodes`` and ``set_state`` mirror the previous widget-grid API so callers do
    not need to know the implementation changed. ``set_state`` also remembers the
    previous node, which is what lets the most recent transition stay emphasised
    instead of blinking out the moment it lands.
    """

    #: Scene padding around the node grid.
    MARGIN = 14.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setRenderHint(QPainter.TextAntialiasing, True)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setBackgroundBrush(QBrush(QColor(0, 0, 0, 0)))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumHeight(120)

        self.nodes: dict[Node, DiagramNode] = {}
        self.current: Node | None = None
        self.previous: Node | None = None

        self._edges: list[_Edge] = []
        self._reset_blocked = False

        self._build_scene()

    # -- construction -------------------------------------------------------

    def _build_scene(self) -> None:
        for node, (row, column) in NODE_GRID.items():
            item = DiagramNode(node)
            item.setPos(_centre(row, column))
            item.setAcceptedMouseButtons(Qt.NoButton)
            self._scene.addItem(item)
            self.nodes[node] = item

        for source, target, label in EDGES:
            self._add_edge(source, target, label)

        self._add_fault_branch()

        self._scene.setSceneRect(
            self._scene.itemsBoundingRect().adjusted(
                -self.MARGIN, -self.MARGIN, self.MARGIN, self.MARGIN
            )
        )

    def _add_edge(self, source: Node, target: Node, label: str) -> None:
        start, end = self._exit_and_entry(source, target)

        path = QPainterPath(start)

        if abs(start.y() - end.y()) > 1.0:
            # Elbow: travel horizontally to the target column, then vertically.
            path.lineTo(QPointF(end.x(), start.y()))
            path.lineTo(end)
        else:
            path.lineTo(end)

        item = QGraphicsPathItem(path)
        item.setPen(QPen(QColor(theme.GREY_DIM), 1.0))
        item.setBrush(Qt.NoBrush)
        item.setToolTip(f"{source.value} \u2192 {target.value} ({label})")
        item.setAcceptedMouseButtons(Qt.NoButton)
        self._scene.addItem(item)

        self._add_arrow_head(end, path)
        self._add_edge_label(path, label)

        self._edges.append(_Edge(source, target, label, item))

    def _exit_and_entry(self, source: Node, target: Node) -> tuple[QPointF, QPointF]:
        """Shorten an edge so it starts and ends on the box borders."""

        start = _centre(*NODE_GRID[source])
        end = _centre(*NODE_GRID[target])

        start_row, start_col = NODE_GRID[source]
        end_row, end_col = NODE_GRID[target]

        half_w = NODE_WIDTH / 2
        half_h = NODE_HEIGHT / 2

        if start_row == end_row:
            direction = 1 if end_col > start_col else -1
            return (
                QPointF(start.x() + direction * half_w, start.y()),
                QPointF(end.x() - direction * half_w, end.y()),
            )

        if start_col == end_col:
            direction = 1 if end_row > start_row else -1
            return (
                QPointF(start.x(), start.y() + direction * half_h),
                QPointF(end.x(), end.y() - direction * half_h),
            )

        horizontal = 1 if end_col > start_col else -1
        return (
            QPointF(start.x() + horizontal * half_w, start.y()),
            QPointF(end.x(), end.y() - half_h),
        )

    def _add_arrow_head(self, tip: QPointF, path: QPainterPath) -> None:
        # A small triangle, orientated by the last segment of the path. Six
        # pixels is enough to read at projector distance without dominating the
        # line.
        before = path.pointAtPercent(0.94)
        size = 5.0

        if abs(tip.x() - before.x()) >= abs(tip.y() - before.y()):
            sign = 1.0 if tip.x() >= before.x() else -1.0
            points = (
                QPointF(tip.x(), tip.y()),
                QPointF(tip.x() - sign * size, tip.y() - size * 0.7),
                QPointF(tip.x() - sign * size, tip.y() + size * 0.7),
            )
        else:
            sign = 1.0 if tip.y() >= before.y() else -1.0
            points = (
                QPointF(tip.x(), tip.y()),
                QPointF(tip.x() - size * 0.7, tip.y() - sign * size),
                QPointF(tip.x() + size * 0.7, tip.y() - sign * size),
            )

        head = self._scene.addPolygon(
            QPolygonF(list(points)),
            QPen(QColor(theme.GREY), 1.0),
            QBrush(QColor(theme.GREY)),
        )
        head.setAcceptedMouseButtons(Qt.NoButton)
        head.setToolTip("")

    def _add_edge_label(self, path: QPainterPath, label: str) -> None:
        midpoint = path.pointAtPercent(0.5)

        text = self._scene.addSimpleText(label)
        text.setBrush(QBrush(QColor(theme.TEXT_MUTED)))
        text.setFont(QFont(theme.FONT_FAMILY_QT, 6))
        text.setAcceptedMouseButtons(Qt.NoButton)

        bounds = text.boundingRect()
        text.setPos(midpoint.x() - bounds.width() / 2, midpoint.y() - bounds.height())

    def _add_fault_branch(self) -> None:
        """Dashed drops from every safety-critical node into the FAULT node.

        One shared bus rather than five separate arrows: five dashed diagonals
        would cross the whole diagram and read as noise, and the meaning ("any of
        these can fault") is carried by the shared line just as well.
        """

        fault_centre = _centre(*NODE_GRID[Node.FAULT])
        bus_y = fault_centre.y() - NODE_HEIGHT / 2 - 14.0

        xs: list[float] = []

        for node in FAULT_SOURCES:
            centre = _centre(*NODE_GRID[node])
            bottom = centre.y() + NODE_HEIGHT / 2
            xs.append(centre.x())

            drop = QPainterPath(QPointF(centre.x(), bottom))
            drop.lineTo(QPointF(centre.x(), bus_y))

            item = QGraphicsPathItem(drop)
            item.setPen(QPen(QColor(theme.MAGENTA_DIM), 1.0, Qt.DashLine))
            item.setToolTip("FAULT: any safety subsystem becomes untrusted")
            item.setAcceptedMouseButtons(Qt.NoButton)
            self._scene.addItem(item)

        if not xs:
            return

        low = min(xs + [fault_centre.x()])
        high = max(xs + [fault_centre.x()])

        bus = QPainterPath(QPointF(low, bus_y))
        bus.lineTo(QPointF(high, bus_y))

        bus_item = QGraphicsPathItem(bus)
        bus_item.setPen(QPen(QColor(theme.MAGENTA_DIM), 1.0, Qt.DashLine))
        bus_item.setAcceptedMouseButtons(Qt.NoButton)
        self._scene.addItem(bus_item)

        stem = QPainterPath(QPointF(fault_centre.x(), bus_y))
        stem.lineTo(QPointF(fault_centre.x(), fault_centre.y() - NODE_HEIGHT / 2))

        stem_item = QGraphicsPathItem(stem)
        stem_item.setPen(QPen(QColor(theme.MAGENTA_DIM), 1.0, Qt.DashLine))
        stem_item.setAcceptedMouseButtons(Qt.NoButton)
        self._scene.addItem(stem_item)

        self._add_arrow_head(
            QPointF(fault_centre.x(), fault_centre.y() - NODE_HEIGHT / 2), stem
        )

        note = self._scene.addSimpleText("any subsystem untrusted")
        note.setBrush(QBrush(QColor(theme.MAGENTA)))
        note.setFont(QFont(theme.FONT_FAMILY_QT, 6))
        note.setAcceptedMouseButtons(Qt.NoButton)
        bounds = note.boundingRect()
        note.setPos(high + 6.0, bus_y - bounds.height() / 2)

    # -- updates ------------------------------------------------------------

    def set_state(
        self,
        state: OperatorState | Node,
        *,
        reset_blocked: bool | None = None,
    ) -> None:
        """Highlight the node for ``state`` and keep the previous one visible."""

        node = state if isinstance(state, Node) else STATE_TO_NODE.get(state, Node.FAULT)

        if reset_blocked is not None:
            self._reset_blocked = bool(reset_blocked)

        if node is not self.current:
            self.previous = self.current
            self.current = node

        for key, item in self.nodes.items():
            item.set_display(
                active=key is self.current,
                recent=(key is self.previous and key is not self.current),
                blocked=(key is Node.RESET_ACCEPTED and self._reset_blocked),
            )

        for edge in self._edges:
            self._paint_edge(edge)

    def current_node(self) -> Node:
        """The highlighted node. ``current`` is the raw attribute."""

        return self.current

    def _paint_edge(self, edge: _Edge) -> None:
        """Emphasise the edge that produced the current node."""

        entering = edge.target is self.current and edge.source is self.previous

        if entering:
            edge.item.setPen(QPen(QColor(node_accent(self.current)[0]), 2.2))
            edge.item.setZValue(2)
        elif edge.source is self.current:
            edge.item.setPen(QPen(QColor(theme.BORDER), 1.4))
            edge.item.setZValue(1)
        else:
            edge.item.setPen(QPen(QColor(theme.GREY_DIM), 1.0))
            edge.item.setZValue(0)

    @property
    def reset_blocked(self) -> bool:
        return self._reset_blocked

    def set_reset_blocked(self, blocked: bool) -> None:
        if bool(blocked) == self._reset_blocked:
            return

        self._reset_blocked = bool(blocked)
        item = self.nodes.get(Node.RESET_ACCEPTED)

        if item is not None:
            item.set_display(
                active=self.current is Node.RESET_ACCEPTED,
                recent=(
                    self.previous is Node.RESET_ACCEPTED
                    and self.current is not Node.RESET_ACCEPTED
                ),
                blocked=self._reset_blocked,
            )

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        # The scene rect describes the diagram at full node size. Letting that
        # become the size hint would force the whole live page taller than a
        # 1080p viewport for a control that scales itself to whatever it gets.
        return QSize(int(self._scene.sceneRect().width()), self.minimumHeight())
