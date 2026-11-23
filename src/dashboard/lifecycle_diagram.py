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

#: Node box size in scene units. The scene is drawn at whatever scale
#: ``fitInView`` can afford, so these are not pixels: the smaller the scene, the
#: larger the scale and the more readable the text. ``NODE_WIDTH`` is bounded
#: from below by the longest node name ("SAFE CLEAR PENDING" at font 9), and the
#: gaps only need to clear the edge labels and arrow heads.
NODE_WIDTH = 146.0
NODE_HEIGHT = 32.0

#: Gaps between node boxes.
H_GAP = 34.0
V_GAP = 18.0

#: Where each node sits, as (row, column). Three columns, not four: the live tab
#: gives this view only ~446x140 px, so the scene's *width* is what limits the
#: scale, not its height. A fourth column added 146+34 px of width the widget
#: never had and cost ~25% of the rendered text size. Rows 1 and 2 run
#: right-to-left so the chain snakes, and ``RESET ACCEPTED`` can climb back to
#: ``SAFE`` without a line crossing the whole drawing.
NODE_GRID: dict[Node, tuple[int, int]] = {
    Node.WAITING_FOR_TARGET: (0, 0),
    Node.SAFE: (0, 1),
    Node.VIOLATION_PENDING: (0, 2),
    Node.ALARM_LATCHED: (1, 2),
    Node.SAFE_CLEAR_PENDING: (1, 1),
    Node.SAFE_RECOVERED: (1, 0),
    Node.ACKNOWLEDGED: (2, 0),
    Node.RESET_ACCEPTED: (2, 1),
    Node.FAULT: (2, 2),
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
    (Node.VIOLATION_PENDING, Node.ALARM_LATCHED, "confirm"),
    (Node.ALARM_LATCHED, Node.SAFE_CLEAR_PENDING, "goggles"),
    (Node.SAFE_CLEAR_PENDING, Node.SAFE_RECOVERED, "cleared"),
    (Node.SAFE_RECOVERED, Node.ACKNOWLEDGED, "silence"),
    (Node.ACKNOWLEDGED, Node.RESET_ACCEPTED, "gate"),
    (Node.RESET_ACCEPTED, Node.SAFE, "armed"),
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

        name_font = QFont(theme.FONT_FAMILY_QT, 9)
        name_font.setBold(True)
        painter.setFont(name_font)
        painter.setPen(
            QColor(theme.HEADER_WHITE if self._active else self._accent)
        )
        painter.drawText(
            QRectF(box.left() + 3, box.top() + 2, box.width() - 6, box.height() / 2),
            Qt.AlignCenter,
            self._label,
        )

        sub_font = QFont(theme.FONT_FAMILY_QT, 7)
        painter.setFont(sub_font)
        sub_colour = QColor(
            theme.HEADER_WHITE if self._active else theme.TEXT_MUTED
        )
        if self._active:
            sub_colour.setAlpha(220)
        painter.setPen(sub_colour)
        painter.drawText(
            QRectF(
                box.left() + 3,
                box.top() + box.height() / 2 - 3,
                box.width() - 6,
                box.height() / 2,
            ),
            Qt.AlignCenter,
            self._subtitle,
        )

        if self._blocked:
            painter.setFont(QFont(theme.FONT_FAMILY_QT, 7))
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

    #: Scene padding around the node grid. The view is height-bound inside the
    #: live tab, so every pixel of padding here is charged twice: once in the
    #: scene rect and again when ``fitInView`` shrinks everything to suit it.
    #: 4 px is enough to keep arrow heads and the selection ring off the edge.
    MARGIN = 4.0

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
        # 140 is the tallest the diagram can be before the enclosing
        # scroll area's layout stops converging and Qt recurses itself
        # into a stack overflow on show(). Verified per resolution.
        self.setMinimumHeight(140)

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

        source_row, source_col = NODE_GRID[source]
        target_row, target_col = NODE_GRID[target]

        # A same-column edge skips a row, so it cannot travel in a straight line:
        # whatever sits between the two boxes is in the way. Step sideways into
        # the gap beside the column, run down that gap, and come back in. The gap
        # is half a H_GAP wide, so the detour reads as part of the flow.
        if abs(start.y() - end.y()) > 1.0:
            if source_col == target_col and abs(source_row - target_row) > 1:
                # Pick the side of the column that is clear. The outside gaps are
                # always free; an inside gap runs along its neighbour column, so
                # it is only usable when no box in that column spans the detour.
                candidates = [
                    start.x() - NODE_WIDTH / 2 - H_GAP / 2.0,
                    start.x() + NODE_WIDTH / 2 + H_GAP / 2.0,
                ]
                low_y, high_y = sorted((start.y(), end.y()))
                detour_x = next(
                    (
                        x
                        for x in candidates
                        if not any(
                            _centre(row, col).y() + NODE_HEIGHT / 2 > low_y
                            and _centre(row, col).y() - NODE_HEIGHT / 2 < high_y
                            and abs(_centre(row, col).x() - x) < NODE_WIDTH / 2 + 1.0
                            for row, col in NODE_GRID.values()
                        )
                    ),
                    candidates[0],
                )
                path.lineTo(QPointF(detour_x, start.y()))
                path.lineTo(QPointF(detour_x, end.y()))
                path.lineTo(end)
            else:
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
        """Place the label on the path where it hits nothing.

        Drawing every label at the 50% point looks tidy but collides with node
        boxes as soon as the gaps are tight: an edge label is up to 34 px wide and
        the boxes are only a gap apart. So walk the path and take the candidate
        with the most clearance, preferring points near the middle so the result
        still reads as attached to its own edge.
        """

        text = self._scene.addSimpleText(label)
        text.setBrush(QBrush(QColor(theme.TEXT_MUTED)))
        text.setFont(QFont(theme.FONT_FAMILY_QT, 7))
        text.setAcceptedMouseButtons(Qt.NoButton)

        bounds = text.boundingRect()
        width, height = bounds.width(), bounds.height()
        obstacles = [
            item.sceneBoundingRect()
            for item in self.nodes.values()
            if item.isVisible()
        ]

        best: tuple[float, QPointF] | None = None

        for step in range(6, 95):
            fraction = step / 100.0
            point = path.pointAtPercent(fraction)
            box = QRectF(point.x() - width / 2, point.y() - height, width, height)

            if any(box.intersects(rect) for rect in obstacles):
                continue

            clearance = min(
                min(
                    abs(box.left() - rect.right()),
                    abs(rect.left() - box.right()),
                    abs(box.top() - rect.bottom()),
                    abs(rect.top() - box.bottom()),
                )
                for rect in obstacles
            ) if obstacles else 0.0

            score = clearance - abs(fraction - 0.5) * 24.0

            if best is None or score > best[0]:
                best = (score, point)

        anchor = best[1] if best is not None else path.pointAtPercent(0.5)
        text.setPos(anchor.x() - width / 2, anchor.y() - height)

    def _add_fault_branch(self) -> None:
        """Dashed drops from every safety-critical node into the FAULT node.

        One shared spine rather than five separate arrows: five dashed diagonals
        would cross the whole diagram and read as noise, and the meaning ("any of
        these can fault") is carried by the shared line just as well.

        FAULT sits in the bottom-right corner, so a vertical channel on the right
        of the grid collects every feed. Each source first steps down into the
        empty lane between its own row and the next, then runs right along that
        lane -- the one horizontal corridor guaranteed to be free of boxes.
        """

        fault_centre = _centre(*NODE_GRID[Node.FAULT])

        right_edge = max(
            _centre(row, column).x() + NODE_WIDTH / 2
            for row, column in NODE_GRID.values()
        )
        channel_x = right_edge + H_GAP / 2.0
        lane_offset = NODE_HEIGHT / 2 + V_GAP / 2.0

        lane_ys: list[float] = []

        for node in FAULT_SOURCES:
            centre = _centre(*NODE_GRID[node])
            lane_y = centre.y() + lane_offset
            lane_ys.append(lane_y)

            route = QPainterPath(QPointF(centre.x(), centre.y() + NODE_HEIGHT / 2))
            route.lineTo(QPointF(centre.x(), lane_y))
            route.lineTo(QPointF(channel_x, lane_y))

            item = QGraphicsPathItem(route)
            item.setPen(QPen(QColor(theme.MAGENTA_DIM), 1.0, Qt.DashLine))
            item.setToolTip("FAULT: any safety subsystem becomes untrusted")
            item.setAcceptedMouseButtons(Qt.NoButton)
            self._scene.addItem(item)

        if not lane_ys:
            return

        spine = QPainterPath(QPointF(channel_x, min(lane_ys)))
        spine.lineTo(QPointF(channel_x, fault_centre.y()))
        spine.lineTo(QPointF(fault_centre.x() + NODE_WIDTH / 2, fault_centre.y()))

        spine_item = QGraphicsPathItem(spine)
        spine_item.setPen(QPen(QColor(theme.MAGENTA_DIM), 1.0, Qt.DashLine))
        spine_item.setAcceptedMouseButtons(Qt.NoButton)
        self._scene.addItem(spine_item)

        self._add_arrow_head(
            QPointF(fault_centre.x() + NODE_WIDTH / 2, fault_centre.y()), spine
        )

        note = self._scene.addSimpleText("any subsystem untrusted")
        note.setBrush(QBrush(QColor(theme.MAGENTA)))
        note.setFont(QFont(theme.FONT_FAMILY_QT, 7))
        note.setAcceptedMouseButtons(Qt.NoButton)
        bounds = note.boundingRect()
        note.setPos(
            fault_centre.x() - bounds.width() / 2,
            fault_centre.y() - NODE_HEIGHT / 2 - bounds.height() - 3.0,
        )

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
