"""Camera panel: the live frame with detection boxes drawn on top.

The overlay is painted by Qt rather than by OpenCV. That keeps the dashboard a
single-process, single-window application instead of spawning an OpenCV window
alongside it, and it means the boxes can be drawn with the same palette the rest
of the UI uses.

Two rules the panel follows:

- ``UNKNOWN`` frames are labelled as unknown, never blank. An empty frame with no
  caption looks like "nothing wrong"; the caption makes it read as "no
  information", which is what the safety policy actually says.
- Detection colour encodes the *class*, not the confidence, so a red box always
  means ``no_goggles`` and a green box always means ``goggles``. Mixing severity
  into the same channel as class would make the overlay ambiguous.

**Demo fixtures.** ``--demo-data`` has no camera, which is what makes the
dashboard testable and repeatable. Drawing a plausible camera frame anyway would
be a lie told at exactly the wrong moment, so the fixture path paints an
obviously-synthetic scene - flat silhouette, no texture - and stamps
``DEMO FIXTURE - NOT LIVE CAMERA`` across it. It is deterministic in the step
index, so two runs of the same storyline produce identical pictures.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import theme
from .widgets import Card


#: Class name -> box colour. ``goggles`` is the only class allowed to use green,
#: because seeing goggles is the only accepted evidence of compliance.
CLASS_COLOURS: dict[str, str] = {
    "goggles": theme.GREEN,
    "no_goggles": theme.RED,
}

#: Observation -> colour of the corner badge.
OBSERVATION_COLOURS: dict[str, str] = {
    "SAFE": theme.GREEN,
    "VIOLATION": theme.RED,
    "UNKNOWN": theme.GREY,
}


class FrameView(QLabel):
    """Draws one frame scaled to fit, with boxes mapped into the display."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FrameView")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(320, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(
            f"QLabel {{ background-color: {theme.NAVY};"
            f" border: 1px solid {theme.BORDER}; border-radius: 6px; }}"
        )

        self._image: QImage | None = None
        self._boxes: tuple[tuple[str, float, int, int, int, int], ...] = ()
        self._observation = "UNKNOWN"
        self._placeholder = "No camera frame yet"
        self._fixture: tuple[int, str, int] | None = None

    def set_frame(self, frame) -> None:  # noqa: ANN001 - FrameResult
        """Accept a ``FrameResult`` and repaint."""

        image = QImage(
            frame.image_bytes,
            frame.width,
            frame.height,
            frame.width * 3,
            QImage.Format_RGB888,
        )

        # Copy: the bytes object dies with the signal, and QImage does not own it.
        self._image = image.copy()
        self._boxes = tuple(frame.detections)
        self._observation = frame.observation
        self._fixture = None
        self.update()

    def set_fixture(self, step: int, *, observation: str = "UNKNOWN", detections: int = 0) -> None:
        """Paint the deterministic demo illustration instead of a camera frame.

        Exists so ``--demo-data`` never shows a blank rectangle that could be
        mistaken for a dark room, and never shows anything that could be mistaken
        for a real camera. The banner is not optional.
        """

        self._fixture = (int(step), observation, int(max(0, detections)))
        self._image = None
        self._boxes = ()
        self._observation = observation
        self.update()

    def clear_frame(self, message: str = "No camera frame yet") -> None:
        self._image = None
        self._boxes = ()
        self._fixture = None
        self._placeholder = message
        self.update()

    @property
    def is_fixture(self) -> bool:
        return self._fixture is not None

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        try:
            if self._fixture is not None:
                self._paint_fixture(painter)
                return

            if self._image is None or self._image.isNull():
                painter.setPen(QColor(theme.TEXT_MUTED))
                painter.setFont(QFont(theme.FONT_FAMILY_QT, 11))
                painter.drawText(self.rect(), Qt.AlignCenter, self._placeholder)
                return

            target = self._fit_rect()
            painter.drawImage(target, self._image)

            scale = target.width() / max(1, self._image.width())

            for name, score, x1, y1, x2, y2 in self._boxes:
                colour = CLASS_COLOURS.get(name, theme.ORANGE)
                box = QRectF(
                    target.left() + x1 * scale,
                    target.top() + y1 * scale,
                    max(2.0, (x2 - x1) * scale),
                    max(2.0, (y2 - y1) * scale),
                )

                pen = QPen(QColor(colour), 2.0)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(box)

                label = f"{name} {score:.2f}"
                font = QFont(theme.FONT_FAMILY_QT, 8)
                font.setBold(True)
                painter.setFont(font)

                metrics = painter.fontMetrics()
                width = metrics.horizontalAdvance(label) + 8
                height = metrics.height() + 2
                tag = QRectF(box.left(), max(target.top(), box.top() - height), width, height)

                painter.fillRect(tag, QColor(colour))
                painter.setPen(QColor("#FFFFFF"))
                painter.drawText(tag, Qt.AlignCenter, label)

            self._draw_badge(painter, target)
        finally:
            painter.end()

    def _paint_fixture(self, painter: QPainter) -> None:
        """Deterministic, obviously-synthetic scene with a mandatory banner.

        Deliberately flat and geometric. The goal is that nobody can mistake it
        for a camera still, while still showing the operator what the layout and
        the overlay will look like once a camera is attached.
        """

        assert self._fixture is not None
        step, observation, detections = self._fixture

        area = self.rect().adjusted(2, 2, -2, -2)
        if area.width() < 20 or area.height() < 20:
            return

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#0B1A33"))
        painter.drawRoundedRect(QRectF(area), 5, 5)

        # A 16:9 stage, letterboxed, so the geometry matches a real 640x360 frame.
        stage_h = min(area.height() - 46, (area.width() - 24) * 9 / 16)
        stage_w = stage_h * 16 / 9
        stage = QRectF(
            area.center().x() - stage_w / 2,
            area.center().y() - stage_h / 2 - 6,
            stage_w,
            stage_h,
        )

        painter.setBrush(QColor("#132B4F"))
        painter.setPen(QPen(QColor(theme.BORDER), 1.0))
        painter.drawRect(stage)

        # Head-and-shoulders silhouette. Two shapes is enough to read as "a
        # person" without pretending to be a photograph.
        head_r = stage_h * 0.16
        head = QRectF(
            stage.center().x() - head_r,
            stage.top() + stage_h * 0.18,
            head_r * 2,
            head_r * 2,
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#1E3B6B"))
        painter.drawEllipse(head)

        shoulders = QRectF(
            stage.center().x() - stage_w * 0.22,
            head.bottom() - 2,
            stage_w * 0.44,
            stage_h * 0.42,
        )
        painter.drawRoundedRect(shoulders, 18, 18)

        # Goggles bar across the eyes when the fixture reports compliance, present
        # or absent, matching the storyline so the picture agrees with the state.
        bar = QRectF(
            head.left() - head.width() * 0.12,
            head.top() + head.height() * 0.30,
            head.width() * 1.24,
            head.height() * 0.22,
        )

        wearing = detections > 0 and observation == "SAFE"
        painter.setBrush(QColor(theme.GREEN if wearing else theme.RED))
        painter.drawRoundedRect(bar, 4, 4)

        # Synthetic-looking annotation boxes, drawn only when the storyline has
        # detections, so the overlay wiring is exercised without fake confidence.
        if detections:
            boxes = (
                (head.adjusted(-6, -6, 6, 6), "goggles" if wearing else "no_goggles"),
            )
            for index in range(min(detections, 3)):
                offset = 10 + index * 16
                boxes = boxes + (
                    (
                        QRectF(
                            stage.left() + 14,
                            stage.bottom() - 40 - offset,
                            stage_w * 0.22,
                            26,
                        ),
                        "person",
                    ),
                )

            for rect, name in boxes:
                colour = CLASS_COLOURS.get(name, theme.TEXT_MUTED)
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor(colour), 1.6, Qt.DashLine))
                painter.drawRect(rect)

        # Cross-hatch, so the picture can never be mistaken for footage.
        painter.setPen(QPen(QColor(255, 255, 255, 14), 1.0))
        stride = 22
        for x in range(int(stage.left()), int(stage.right()), stride):
            painter.drawLine(
                int(x), int(stage.top()), int(x), int(stage.bottom())
            )

        self._paint_fixture_banner(painter, area)

        if step >= 0:
            marker = QFont(theme.FONT_FAMILY_QT, 8)
            painter.setFont(marker)
            painter.setPen(QColor(theme.TEXT_MUTED))
            painter.drawText(
                area.adjusted(8, 0, -8, -4),
                Qt.AlignRight | Qt.AlignBottom,
                f"fixture step {step}",
            )

    def _paint_fixture_banner(self, painter: QPainter, area) -> None:  # noqa: ANN001
        banner = QRectF(
            area.left() + 8,
            area.bottom() - 30,
            min(area.width() - 16, 340),
            22,
        )

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.ORANGE))
        painter.drawRoundedRect(banner, 3, 3)

        font = QFont(theme.FONT_FAMILY_QT, 8)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(banner, Qt.AlignCenter, "DEMO FIXTURE \u2014 NOT LIVE CAMERA")

    def _fit_rect(self) -> QRectF:
        """Aspect-preserving rect centred inside the widget."""

        assert self._image is not None
        available_w = max(1, self.width() - 4)
        available_h = max(1, self.height() - 4)

        image_ratio = self._image.width() / max(1, self._image.height())
        widget_ratio = available_w / available_h

        if widget_ratio > image_ratio:
            height = available_h
            width = height * image_ratio
        else:
            width = available_w
            height = width / image_ratio

        return QRectF((self.width() - width) / 2.0, (self.height() - height) / 2.0, width, height)

    def _draw_badge(self, painter: QPainter, target: QRectF) -> None:
        """Corner badge showing the frame decision.

        Includes the word ``UNKNOWN`` rather than hiding itself, so a frame with
        no detections is visibly "no verdict" instead of looking calm.
        """

        text = f"RAW  {self._observation}"
        colour = OBSERVATION_COLOURS.get(self._observation, theme.GREY)

        font = QFont(theme.FONT_FAMILY_QT, 9)
        font.setBold(True)
        painter.setFont(font)

        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 16
        height = metrics.height() + 8

        badge = QRectF(target.left() + 8, target.top() + 8, width, height)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colour))
        painter.drawRoundedRect(badge, 4, 4)

        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(badge, Qt.AlignCenter, text)


class CameraPanel(Card):
    """Card wrapper around ``FrameView`` with a footer of frame statistics."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "Live camera",
            caption="UNKNOWN means no evidence, not safety.",
            parent=parent,
        )

        self.view = FrameView()
        self.body.addWidget(self.view, 1)

        footer = QLabel("Waiting for a frame\u2026")
        footer.setObjectName("CardCaption")
        self.body.addWidget(footer)
        self.footer = footer

    def show_frame(self, frame) -> None:  # noqa: ANN001 - FrameResult
        self.view.set_frame(frame)
        self.footer.setText(
            f"frame {frame.width}\u00d7{frame.height}   "
            f"{frame.fps:5.1f} fps   "
            f"inference {frame.inference_ms:5.1f} ms   "
            f"detections {len(frame.detections)}"
        )

    def show_message(self, message: str) -> None:
        self.view.clear_frame(message)
        self.footer.setText(message)

    def show_fixture(self, step: int, *, observation: str = "UNKNOWN", detections: int = 0) -> None:
        """Demo path: deterministic illustration plus an explicit footer label."""

        self.view.set_fixture(step, observation=observation, detections=detections)
        self.footer.setText(
            f"DEMO FIXTURE \u2014 NOT LIVE CAMERA   "
            f"fixture step {step}   "
            f"observation {observation}   "
            f"detections {detections}"
        )
