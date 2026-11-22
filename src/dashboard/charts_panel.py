"""Charts panel: rolling telemetry and session statistics.

Qt ``QPainter`` widgets rather than QtCharts. The figures are small sparkline-style
plots that repaint every few hundred milliseconds; a chart framework would add
layout and animation cost for no benefit, and would make the exact pixel
coordinates used in the tests unstable.

Every series is bounded before it is drawn. A rolling window that grows with
runtime is the classic way for a dashboard to become unusable at the end of a demo,
and it also makes a spike from hour one sit permanently in the graph.
"""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from . import theme
from .state_models import DashboardSnapshot, GaugeSnapshot, SessionCounters
from .widgets import Card, divider


#: How many telemetry samples are kept for the rolling plots.
HISTORY = 60


class SeriesPlot(QWidget):
    """A minimal multi-series line plot with a shared vertical axis.

    Series are drawn as polylines with a light fill, which is readable at small
    sizes and needs no legend - the caption names the colour order.
    """

    def __init__(
        self,
        title: str,
        series: tuple[tuple[str, str], ...],
        *,
        minimum: float = 0.0,
        maximum: float = 0.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setMinimumHeight(96)
        self._title = title
        self._series = series
        self._minimum = minimum
        self._maximum = maximum
        self._data: dict[str, deque[float]] = {
            name: deque(maxlen=HISTORY) for name, _ in series
        }

    def push(self, values: dict[str, float]) -> None:
        for name, _ in self._series:
            if name in values:
                self._data[name].append(float(values[name]))
        self.update()

    def clear(self) -> None:
        for queue in self._data.values():
            queue.clear()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        try:
            outer = self.rect().adjusted(0, 0, -1, -1)

            title_font = QFont("Helvetica Neue", 8)
            title_font.setBold(True)
            painter.setFont(title_font)
            painter.setPen(QColor(theme.TEXT_MUTED))
            painter.drawText(
                QRectF(outer.left(), outer.top(), outer.width(), 12),
                Qt.AlignLeft | Qt.AlignVCenter,
                self._title.upper(),
            )

            plot = QRectF(
                outer.left() + 2,
                outer.top() + 15,
                max(1.0, outer.width() - 4),
                max(1.0, outer.height() - 17),
            )

            painter.setPen(QPen(QColor(theme.BORDER), 1))
            painter.drawLine(
                QPointF(plot.left(), plot.bottom()),
                QPointF(plot.right(), plot.bottom()),
            )

            upper = self._upper_bound()

            for name, colour in self._series:
                queue = self._data[name]
                if len(queue) < 2:
                    continue

                path = QPainterPath()
                step = plot.width() / (HISTORY - 1)

                for index, value in enumerate(queue):
                    x = plot.left() + index * step
                    ratio = 0.0 if upper <= 0 else min(1.0, max(0.0, value / upper))
                    y = plot.bottom() - ratio * plot.height()
                    point = QPointF(x, y)

                    if index == 0:
                        path.moveTo(point)
                    else:
                        path.lineTo(point)

                painter.setPen(QPen(QColor(colour), 1.6))
                painter.drawPath(path)

            self._draw_latest(painter, plot)
        finally:
            painter.end()

    def _upper_bound(self) -> float:
        if self._maximum > 0:
            return self._maximum

        observed = [value for queue in self._data.values() for value in queue]
        peak = max(observed) if observed else 0.0
        return max(self._minimum, peak * 1.15, 1.0)

    def _draw_latest(self, painter: QPainter, plot: QRectF) -> None:
        """Print the newest value of each series at the right edge."""

        font = QFont("Helvetica Neue", 8)
        painter.setFont(font)

        text = "  ".join(
            f"{name} {self._data[name][-1]:.1f}"
            for name, _ in self._series
            if self._data[name]
        )

        if not text:
            return

        painter.setPen(QColor(theme.TEXT_MUTED))
        painter.drawText(plot, Qt.AlignRight | Qt.AlignTop, text)


class GaugeWidget(QWidget):
    """Horizontal 0..1 gauge for the temporal decision evidence.

    Two markers: the current violation ratio (the moving bar) and the required
    threshold (the tick). Seeing both is what makes the filter legible - the
    operator can tell whether a pending state is about to confirm or about to be
    cancelled.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(58)
        self._ratio = 0.0
        self._required = 0.8
        self._label = "UNKNOWN"
        self._colour = theme.GREY

    def set_gauge(self, gauge: GaugeSnapshot) -> None:
        self._ratio = max(0.0, min(1.0, gauge.violation_ratio))
        self._required = max(0.0, min(1.0, gauge.violation_ratio_required))
        self._label = gauge.observation or "UNKNOWN"
        self._colour = {
            "SAFE": theme.GREEN,
            "VIOLATION": theme.RED,
            "UNKNOWN": theme.GREY,
        }.get(self._label, theme.AMBER)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        try:
            outer = self.rect().adjusted(2, 2, -3, -3)

            font = QFont("Helvetica Neue", 8)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(theme.TEXT_MUTED))
            painter.drawText(
                QRectF(outer.left(), outer.top(), 120, 12),
                Qt.AlignLeft | Qt.AlignVCenter,
                "VIOLATION RATIO",
            )

            painter.setPen(QColor(self._colour))
            painter.drawText(
                QRectF(outer.right() - 160, outer.top(), 160, 12),
                Qt.AlignRight | Qt.AlignVCenter,
                f"{self._label}  {self._ratio * 100:.0f}%"
                f"  (needs {self._required * 100:.0f}%)",
            )

            trough = QRectF(
                outer.left(),
                outer.top() + 20,
                max(1.0, outer.width()),
                10,
            )

            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.GREY_DIM))
            painter.drawRoundedRect(trough, 3, 3)

            filled = QRectF(trough)
            filled.setWidth(max(0.0, trough.width() * self._ratio))
            painter.setBrush(QColor(self._colour))
            painter.drawRoundedRect(filled, 3, 3)

            tick_x = trough.left() + trough.width() * self._required
            painter.setPen(QPen(QColor(theme.HEADER_WHITE), 2))
            painter.drawLine(
                QPointF(tick_x, trough.top() - 2),
                QPointF(tick_x, trough.bottom() + 2),
            )
        finally:
            painter.end()


class CounterGrid(QWidget):
    """Six session counters in a 3x2 grid."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(6)

        self.labels: dict[str, QLabel] = {}

        specs = (
            ("frames", "FRAMES"),
            ("violations", "VIOLATIONS"),
            ("incidents", "INCIDENTS"),
            ("acknowledged", "ACKNOWLEDGED"),
            ("resets", "RESETS"),
            ("safe", "SAFE VERDICTS"),
        )

        for index, (key, caption) in enumerate(specs):
            box = QWidget()
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(0, 0, 0, 0)
            box_layout.setSpacing(0)

            value = QLabel("0")
            value.setObjectName("MetricValue")
            value.setStyleSheet(f"color: {theme.TEXT}; font-size: 18px;")
            box_layout.addWidget(value)

            label = QLabel(caption)
            label.setObjectName("MetricLabel")
            box_layout.addWidget(label)

            layout.addWidget(box, index // 3, index % 3)
            self.labels[key] = value

    def set_counters(self, counters: SessionCounters) -> None:
        values = {
            "frames": (counters.frames, theme.TEXT),
            "violations": (counters.violation_frames, theme.RED),
            "incidents": (counters.incidents, theme.RED),
            "acknowledged": (counters.acknowledgements, theme.ORANGE),
            "resets": (counters.resets, theme.CYAN),
            "safe": (counters.safe_frames, theme.GREEN),
        }

        for key, (value, colour) in values.items():
            label = self.labels[key]
            label.setText(str(value))
            label.setStyleSheet(f"color: {colour}; font-size: 18px;")


class DurationBars(QWidget):
    """Horizontal bars for the share of session time spent in each state.

    Only states that were actually visited are drawn, so a healthy run shows a
    short list rather than eleven near-empty rows.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(90)
        self._shares: tuple[tuple[str, float], ...] = ()

    def set_shares(self, shares: tuple[tuple[str, float], ...]) -> None:
        self._shares = shares
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        try:
            if not self._shares:
                painter.setPen(QColor(theme.TEXT_MUTED))
                painter.setFont(QFont("Helvetica Neue", 9))
                painter.drawText(self.rect(), Qt.AlignCenter, "No session time yet")
                return

            font = QFont("Helvetica Neue", 8)
            painter.setFont(font)

            top = 2
            row_height = 15
            name_width = 132

            for name, share in self._shares:
                if share <= 0:
                    continue

                colour = _state_colour(name)

                painter.setPen(QColor(theme.TEXT))
                painter.drawText(
                    QRectF(2, top, name_width, row_height),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    name,
                )

                bar = QRectF(
                    name_width + 4,
                    top + 4,
                    max(1.0, self.width() - name_width - 70),
                    row_height - 8,
                )

                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(theme.GREY_DIM))
                painter.drawRoundedRect(bar, 2, 2)

                filled = QRectF(bar)
                filled.setWidth(max(0.0, bar.width() * share))
                painter.setBrush(QColor(colour))
                painter.drawRoundedRect(filled, 2, 2)

                painter.setPen(QColor(theme.TEXT_MUTED))
                painter.drawText(
                    QRectF(self.width() - 58, top, 56, row_height),
                    Qt.AlignRight | Qt.AlignVCenter,
                    f"{share * 100:.0f}%",
                )

                top += row_height

                if top > self.height():
                    break
        finally:
            painter.end()


def _state_colour(name: str) -> str:
    from tools.incident_lifecycle import OperatorState

    try:
        return theme.state_accent(OperatorState[name])
    except KeyError:
        return theme.GREY


class ChartsPanel(Card):
    """Container for the rolling plots, gauge, counters and duration bars."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "Session telemetry",
            caption="Rolling window of the last 60 samples. Scaling is fixed per "
            "plot so a spike is comparable across the session.",
            parent=parent,
        )

        self.performance_plot = SeriesPlot(
            "Throughput",
            (("fps", theme.CYAN),),
            minimum=60.0,
        )
        self.body.addWidget(self.performance_plot)

        self.latency_plot = SeriesPlot(
            "Inference / frame age (ms)",
            (("inference", theme.AMBER), ("frame_age", theme.MAGENTA)),
            minimum=80.0,
        )
        self.body.addWidget(self.latency_plot)

        self.confidence_plot = SeriesPlot(
            "Peak detection confidence",
            (("confidence", theme.GREEN),),
            minimum=1.0,
            maximum=1.0,
        )
        self.body.addWidget(self.confidence_plot)

        self.body.addWidget(divider())

        gauge_title = QLabel("TEMPORAL DECISION")
        gauge_title.setObjectName("MetricLabel")
        self.body.addWidget(gauge_title)

        self.gauge = GaugeWidget()
        self.body.addWidget(self.gauge)

        self.body.addWidget(divider())

        counters_title = QLabel("SESSION COUNTERS")
        gauge_title.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        self.body.addWidget(counters_title)

        self.counters = CounterGrid()
        self.body.addWidget(self.counters)

        self.body.addWidget(divider())

        durations_title = QLabel("STATE DURATION SHARE")
        durations_title.setObjectName("MetricLabel")
        self.body.addWidget(durations_title)

        self.durations = DurationBars()
        self.body.addWidget(self.durations, 1)

    # -- updates ------------------------------------------------------------

    def push_telemetry(
        self,
        *,
        fps: float,
        inference_ms: float,
        frame_age_ms: float,
        max_confidence: float,
    ) -> None:
        self.performance_plot.push({"fps": fps})
        self.latency_plot.push({"inference": inference_ms, "frame_age": frame_age_ms})
        self.confidence_plot.push({"confidence": max_confidence})

    def update_from(
        self,
        snapshot: DashboardSnapshot,
        *,
        shares: tuple[tuple[str, float], ...] = (),
    ) -> None:
        self.gauge.set_gauge(snapshot.gauge)
        self.counters.set_counters(snapshot.counters)
        self.durations.set_shares(shares)

    def clear(self) -> None:
        self.performance_plot.clear()
        self.latency_plot.clear()
        self.confidence_plot.clear()
