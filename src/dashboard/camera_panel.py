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
        self.setMinimumSize(360, 240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(
            f"QLabel {{ background-color: {theme.NAVY};"
            f" border: 1px solid {theme.BORDER}; border-radius: 6px; }}"
        )

        self._image: QImage | None = None
        self._boxes: tuple[tuple[str, float, int, int, int, int], ...] = ()
        self._observation = "UNKNOWN"
        self._placeholder = "No camera frame yet"

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
        self.update()

    def clear_frame(self, message: str = "No camera frame yet") -> None:
        self._image = None
        self._boxes = ()
        self._placeholder = message
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        try:
            if self._image is None or self._image.isNull():
                painter.setPen(QColor(theme.TEXT_MUTED))
                painter.setFont(QFont("Helvetica Neue", 11))
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
                font = QFont("Helvetica Neue", 8)
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

        font = QFont("Helvetica Neue", 9)
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
            caption="Overlay boxes are drawn by Qt from the same detections the "
            "detector accepted. UNKNOWN is shown explicitly: an empty frame is "
            "missing information, not evidence of safety.",
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
