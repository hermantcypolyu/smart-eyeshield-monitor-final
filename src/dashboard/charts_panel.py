"""Charts panel: rolling telemetry and session statistics.

Qt ``QPainter`` widgets rather than QtCharts. The figures are small sparkline-style
plots that repaint every few hundred milliseconds; a chart framework would add
layout and animation cost for no benefit, and would make the exact pixel
coordinates used in the tests unstable.

Every series is bounded before it is drawn. A rolling window that grows with
runtime is the classic way for a dashboard to become unusable at the end of a demo,
and it also makes a spike from hour one sit permanently in the graph.

Two honesty rules govern this module:

- A plot with no samples says **NO TELEMETRY SAMPLES YET**. It never draws a
  plausible-looking line, and it never seeds itself with random numbers. A blank
  chart is a true statement; a fabricated one is a lie that looks like evidence.
- In demo mode the samples come from ``fixture_telemetry``, which is a pure
  function of the step index, so the same demo always produces the same picture.
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

#: Left gutter reserved for the value-axis labels, in pixels.
_AXIS_GUTTER = 30

#: Height of the legend / current-value strip above each plot.
_LEGEND_HEIGHT = 13


class SeriesPlot(QWidget):
    """A multi-series line plot with an axis, a legend and a current marker.

    Each series is drawn as a polyline. The legend row doubles as the
    current-value readout, which is what the operator actually looks at during a
    demo, and the newest point of every series gets a dot so "where am I now" is
    answerable without reading the number.
    """

    def __init__(
        self,
        title: str,
        series: tuple[tuple[str, str], ...],
        *,
        minimum: float = 0.0,
        maximum: float = 0.0,
        unit: str = "",
        threshold: float = 0.0,
        threshold_label: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setMinimumHeight(96)
        self._title = title
        self._series = series
        self._minimum = minimum
        self._maximum = maximum
        self._unit = unit
        self._threshold = threshold
        self._threshold_label = threshold_label
        self._data: dict[str, deque[float]] = {
            name: deque(maxlen=HISTORY) for name, _ in series
        }

    @property
    def sample_count(self) -> int:
        """Samples held; ``0`` is what makes the plot say there is no data yet."""

        return max((len(queue) for queue in self._data.values()), default=0)

    @property
    def empty_text(self) -> str:
        return "NO TELEMETRY SAMPLES YET"

    def push(self, values: dict[str, float]) -> None:
        for name, _ in self._series:
            if name in values:
                self._data[name].append(float(values[name]))
        self.update()

    def clear(self) -> None:
        for queue in self._data.values():
            queue.clear()
        self.update()

    # -- painting -----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        try:
            outer = QRectF(self.rect()).adjusted(0, 0, -1, -1)

            self._draw_title(painter, outer)

            if self.sample_count == 0:
                self._draw_empty(painter, outer)
                return

            self._draw_legend(painter, outer)

            plot = QRectF(
                outer.left() + _AXIS_GUTTER,
                outer.top() + _LEGEND_HEIGHT + 4,
                max(1.0, outer.width() - _AXIS_GUTTER - 6),
                max(1.0, outer.height() - _LEGEND_HEIGHT - 20),
            )

            upper = max(self._upper_bound(), self._threshold * 1.02, 0.001)

            self._draw_axes(painter, plot, upper)
            self._draw_threshold(painter, plot, upper)
            self._draw_series(painter, plot, upper)
            self._draw_window_label(painter, outer)
        finally:
            painter.end()

    def _draw_title(self, painter: QPainter, outer: QRectF) -> None:
        font = QFont("Helvetica Neue", 8)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(
            QRectF(outer.left(), outer.top(), outer.width() * 0.6, 12),
            Qt.AlignLeft | Qt.AlignVCenter,
            self._title.upper(),
        )

        if self._unit:
            painter.setPen(QColor(theme.TEXT_MUTED))
            painter.drawText(
                QRectF(
                    outer.left() + outer.width() * 0.5,
                    outer.top(),
                    outer.width() * 0.28,
                    12,
                ),
                Qt.AlignLeft | Qt.AlignVCenter,
                f"[{self._unit}]",
            )

    def _draw_empty(self, painter: QPainter, outer: QRectF) -> None:
        """A plot with no samples states that fact instead of drawing nothing."""

        font = QFont("Helvetica Neue", 9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT_MUTED))
        painter.drawText(outer, Qt.AlignCenter, self.empty_text)

    def _draw_legend(self, painter: QPainter, outer: QRectF) -> None:
        font = QFont("Helvetica Neue", 8)
        painter.setFont(font)

        x = outer.left() + _AXIS_GUTTER
        y = outer.top() + 13

        for name, colour in self._series:
            queue = self._data[name]
            text = f"{name} {queue[-1]:.2f}" if queue else f"{name} --"

            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(colour))
            painter.drawEllipse(QPointF(x + 3, y + 5), 3.0, 3.0)

            painter.setPen(QColor(theme.TEXT_MUTED))
            width = painter.fontMetrics().horizontalAdvance(text) + 6
            painter.drawText(
                QRectF(x + 10, y, width, 12),
                Qt.AlignLeft | Qt.AlignVCenter,
                text,
            )
            x += width + 18

    def _draw_axes(self, painter: QPainter, plot: QRectF, upper: float) -> None:
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.drawLine(
            QPointF(plot.left(), plot.bottom()),
            QPointF(plot.right(), plot.bottom()),
        )
        painter.drawLine(
            QPointF(plot.left(), plot.top()),
            QPointF(plot.left(), plot.bottom()),
        )

        font = QFont("Helvetica Neue", 7)
        painter.setFont(font)

        for fraction in (0.0, 0.5, 1.0):
            y = plot.bottom() - fraction * plot.height()
            painter.setPen(QColor(theme.TEXT_MUTED))
            painter.drawText(
                QRectF(0, y - 6, _AXIS_GUTTER - 4, 12),
                Qt.AlignRight | Qt.AlignVCenter,
                f"{upper * fraction:.0f}",
            )
            if fraction > 0:
                painter.setPen(QPen(QColor(theme.BORDER), 1, Qt.DotLine))
                painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

    def _draw_threshold(self, painter: QPainter, plot: QRectF, upper: float) -> None:
        if self._threshold <= 0 or upper <= 0:
            return

        ratio = min(1.0, max(0.0, self._threshold / upper))
        y = plot.bottom() - ratio * plot.height()

        painter.setPen(QPen(QColor(theme.ORANGE), 1, Qt.DashLine))
        painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        if self._threshold_label:
            font = QFont("Helvetica Neue", 7)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(theme.ORANGE))
            painter.drawText(
                QRectF(plot.left() + 3, y - 12, plot.width() - 6, 11),
                Qt.AlignLeft | Qt.AlignVCenter,
                self._threshold_label,
            )

    def _draw_series(self, painter: QPainter, plot: QRectF, upper: float) -> None:
        for name, colour in self._series:
            queue = self._data[name]
            if not queue:
                continue

            step = plot.width() / max(1, HISTORY - 1)
            # Right-align a short series so it grows leftwards from "now" rather
            # than pretending old, missing samples were zero.
            offset = plot.width() - step * (len(queue) - 1)

            path = QPainterPath()
            last = QPointF(plot.left(), plot.bottom())

            for index, value in enumerate(queue):
                x = plot.left() + offset + index * step
                ratio = 0.0 if upper <= 0 else min(1.0, max(0.0, value / upper))
                point = QPointF(x, plot.bottom() - ratio * plot.height())
                last = point

                if index == 0:
                    path.moveTo(point)
                else:
                    path.lineTo(point)

            painter.setPen(QPen(QColor(colour), 1.6))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)

            # Current marker: the one coordinate the operator actually wants.
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(colour))
            painter.drawEllipse(last, 2.6, 2.6)

    def _draw_window_label(self, painter: QPainter, outer: QRectF) -> None:
        font = QFont("Helvetica Neue", 7)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT_MUTED))
        painter.drawText(
            QRectF(outer.left(), outer.bottom() - 12, outer.width(), 12),
            Qt.AlignRight | Qt.AlignVCenter,
            f"{self.sample_count}/{HISTORY} samples \u00b7 newest at right",
        )

    def _upper_bound(self) -> float:
        if self._maximum > 0:
            return self._maximum

        observed = [value for queue in self._data.values() for value in queue]
        peak = max(observed) if observed else 0.0
        return max(self._minimum, peak * 1.15, 1.0)


class GaugeWidget(QWidget):
    """Horizontal 0..1 gauge for the temporal decision evidence.

    Two markers: the current violation ratio (the moving bar) and the required
    threshold (the tick). Seeing both is what makes the filter legible - the
    operator can tell whether a pending state is about to confirm or about to be
    cancelled.

    The confirmation timer is only drawn when a timer is genuinely running.
    Showing ``0.00 / 2.00 s`` next to SAFE would imply the filter is counting
    towards an alarm that does not exist.
    """

    #: States in which the confirmation or clearance clock is actually moving.
    TIMED_STATES = frozenset({"VIOLATION_PENDING", "SAFE_CLEAR_PENDING"})

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(70)
        self._ratio = 0.0
        self._required = 0.8
        self._label = "UNKNOWN"
        self._state = ""
        self._colour = theme.GREY
        self._elapsed = 0.0
        self._target = 0.0
        self._timing = False

    @property
    def timer_running(self) -> bool:
        return self._timing

    def set_gauge(self, gauge: GaugeSnapshot) -> None:
        self._ratio = max(0.0, min(1.0, gauge.violation_ratio))
        self._required = max(0.0, min(1.0, gauge.violation_ratio_required))
        self._label = gauge.observation or "UNKNOWN"
        self._state = gauge.state or ""
        self._colour = {
            "SAFE": theme.GREEN,
            "VIOLATION": theme.RED,
            "UNKNOWN": theme.GREY,
        }.get(self._label, theme.AMBER)

        if self._state == "VIOLATION_PENDING":
            self._timing = True
            self._elapsed = gauge.pending_elapsed
            self._target = gauge.confirmation_seconds
        elif self._state == "SAFE_CLEAR_PENDING":
            self._timing = True
            self._elapsed = gauge.safe_clear_elapsed
            self._target = gauge.clearance_seconds
        else:
            self._timing = False
            self._elapsed = 0.0
            self._target = 0.0

        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        try:
            outer = QRectF(self.rect()).adjusted(2, 2, -3, -3)

            font = QFont("Helvetica Neue", 8)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(theme.TEXT))
            painter.drawText(
                QRectF(outer.left(), outer.top(), 200, 12),
                Qt.AlignLeft | Qt.AlignVCenter,
                "VIOLATION RATIO",
            )

            painter.setPen(QColor(self._colour))
            painter.drawText(
                QRectF(outer.left(), outer.top(), outer.width(), 12),
                Qt.AlignRight | Qt.AlignVCenter,
                f"{self._label}  {self._ratio * 100:.0f}%"
                f"  \u00b7  needs {self._required * 100:.0f}%",
            )

            trough = QRectF(
                outer.left(),
                outer.top() + 19,
                max(1.0, outer.width()),
                12,
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

            self._draw_timer(painter, outer, trough)
        finally:
            painter.end()

    def _draw_timer(self, painter: QPainter, outer: QRectF, trough: QRectF) -> None:
        font = QFont("Helvetica Neue", 9)
        font.setBold(True)
        painter.setFont(font)

        y = trough.bottom() + 5

        if not self._timing:
            painter.setPen(QColor(theme.TEXT_MUTED))
            painter.drawText(
                QRectF(outer.left(), y, outer.width(), 14),
                Qt.AlignLeft | Qt.AlignVCenter,
                "NO TIMER RUNNING",
            )
            return

        progress = 0.0 if self._target <= 0 else min(1.0, self._elapsed / self._target)
        painter.setPen(QColor(theme.AMBER))
        painter.drawText(
            QRectF(outer.left(), y, outer.width() * 0.7, 14),
            Qt.AlignLeft | Qt.AlignVCenter,
            f"{self._elapsed:.2f} / {self._target:.2f} s",
        )

        painter.setPen(QColor(theme.TEXT_MUTED))
        painter.drawText(
            QRectF(outer.left(), y, outer.width(), 14),
            Qt.AlignRight | Qt.AlignVCenter,
            f"{progress * 100:.0f}% of timer",
        )


class CounterGrid(QWidget):
    """The nine session counters the brief asks for, in a 3x3 grid.

    Colour carries meaning consistently: red only ever marks an alarm or a
    fault, orange marks an operator action that is still open, cyan is neutral
    bookkeeping and green only ever means verified-safe.
    """

    #: (key, caption, accent) in display order.
    SPECS: tuple[tuple[str, str, str], ...] = (
        ("frames", "FRAMES", theme.TEXT),
        ("detections", "DETECTIONS", theme.CYAN),
        ("violations", "VIOLATIONS", theme.RED),
        ("incidents", "INCIDENTS", theme.RED),
        ("safe_recoveries", "SAFE RECOVERIES", theme.GREEN),
        ("acknowledged", "ACKNOWLEDGED", theme.ORANGE),
        ("resets", "RESETS ACCEPTED", theme.CYAN),
        ("rejected_resets", "RESETS REJECTED", theme.AMBER),
        ("serial_faults", "SERIAL FAULTS", theme.MAGENTA),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(6)

        self.labels: dict[str, QLabel] = {}

        for index, (key, caption, _accent) in enumerate(self.SPECS):
            box = QWidget()
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(0, 0, 0, 0)
            box_layout.setSpacing(0)

            value = QLabel("0")
            value.setObjectName("MetricValue")
            value.setStyleSheet(f"color: {theme.TEXT}; font-size: 18px;")
            value.setToolTip(caption)
            box_layout.addWidget(value)

            label = QLabel(caption)
            label.setObjectName("MetricLabel")
            label.setWordWrap(True)
            box_layout.addWidget(label)

            layout.addWidget(box, index // 3, index % 3)
            self.labels[key] = value

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(key for key, _caption, _accent in self.SPECS)

    def set_counters(self, counters: SessionCounters) -> None:
        values: dict[str, float] = {
            "frames": counters.frames,
            "detections": counters.detections,
            "violations": counters.violation_frames,
            "incidents": counters.incidents,
            "safe_recoveries": counters.safe_recoveries,
            # Only acknowledgements the lifecycle accepted are counted, so this
            # matches what the correlator actually saw on the wire.
            "acknowledged": counters.acknowledgements,
            "resets": counters.resets,
            "rejected_resets": counters.rejected_resets,
            "serial_faults": counters.serial_faults,
        }

        for key, _caption, colour in self.SPECS:
            label = self.labels[key]
            label.setText(str(int(values.get(key, 0))))
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
                font = QFont("Helvetica Neue", 9)
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(self.rect(), Qt.AlignCenter, "NO SESSION TIME YET")
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
    """Rolling plots, gauge, counters and duration bars for the analytics tab."""

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
            unit="fps",
        )
        self.body.addWidget(self.performance_plot)

        self.latency_plot = SeriesPlot(
            "Latency",
            (("inference", theme.AMBER), ("frame_age", theme.MAGENTA)),
            minimum=80.0,
            unit="ms",
        )
        self.body.addWidget(self.latency_plot)

        # Both classes matter: a frame where the goggles confidence falls is as
        # diagnostic as one where the no_goggles confidence rises.
        self.confidence_plot = SeriesPlot(
            "Detection confidence",
            (
                ("goggles", theme.GREEN),
                ("no_goggles", theme.RED),
                ("peak", theme.CYAN),
            ),
            minimum=1.0,
            maximum=1.0,
            unit="0-1",
            threshold=0.60,
            threshold_label="goggles threshold 0.60",
        )
        self.body.addWidget(self.confidence_plot)

        self.body.addWidget(divider())

        gauge_title = QLabel("TEMPORAL DECISION")
        gauge_title.setObjectName("CardTitle")
        self.body.addWidget(gauge_title)

        self.gauge = GaugeWidget()
        self.body.addWidget(self.gauge)

        self.body.addWidget(divider())

        counters_title = QLabel("SESSION COUNTERS")
        counters_title.setObjectName("CardTitle")
        self.body.addWidget(counters_title)

        self.counters = CounterGrid()
        self.body.addWidget(self.counters)

        self.body.addWidget(divider())

        durations_title = QLabel("STATE DURATION SHARE")
        durations_title.setObjectName("CardTitle")
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
        goggles_confidence: float | None = None,
        no_goggles_confidence: float | None = None,
    ) -> None:
        self.performance_plot.push({"fps": fps})
        self.latency_plot.push({"inference": inference_ms, "frame_age": frame_age_ms})
        self.confidence_plot.push(
            {
                "goggles": max_confidence
                if goggles_confidence is None
                else goggles_confidence,
                "no_goggles": 0.0
                if no_goggles_confidence is None
                else no_goggles_confidence,
                "peak": max_confidence,
            }
        )

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
