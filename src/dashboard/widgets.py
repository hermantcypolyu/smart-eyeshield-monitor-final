"""Reusable card and metric widgets.

Every panel is built from these so the visual language stays consistent and the
stylesheet has a small set of object names to target. Nothing here knows about
safety semantics - a card is just a titled box - which keeps the styling
separate from the meaning.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme


class Card(QFrame):
    """A titled panel: header row, optional caption, then arbitrary content."""

    def __init__(
        self,
        title: str,
        *,
        caption: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(9)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("CardTitle")
        header.addWidget(self.title_label)
        header.addStretch(1)

        self.header_extra = header
        outer.addLayout(header)

        self.caption_label = QLabel(caption)
        self.caption_label.setObjectName("CardCaption")
        self.caption_label.setWordWrap(True)
        self.caption_label.setVisible(bool(caption))
        outer.addWidget(self.caption_label)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        outer.addLayout(self.body, 1)

    def set_caption(self, text: str) -> None:
        self.caption_label.setText(text)
        self.caption_label.setVisible(bool(text))

    def add_header_widget(self, widget: QWidget) -> None:
        self.header_extra.addWidget(widget)


class MetricTile(QWidget):
    """A large number with a small caption, optionally colour-coded.

    ``set_value`` takes the colour explicitly rather than deriving it, because
    the caller knows whether the number is a compliance figure (green allowed) or
    a timing figure (green must not be used).
    """

    def __init__(
        self,
        label: str,
        *,
        value: str = "--",
        unit: str = "",
        accent: str = theme.TEXT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        self.value_label = QLabel(value + (f" {unit}" if unit else ""))
        self.value_label.setObjectName("MetricValue")
        self.value_label.setStyleSheet(f"color: {accent};")
        layout.addWidget(self.value_label)

        self.caption_label = QLabel(label.upper())
        self.caption_label.setObjectName("MetricLabel")
        layout.addWidget(self.caption_label)

        self._unit = unit
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def set_value(self, value: str, *, accent: str | None = None, unit: str | None = None) -> None:
        if unit is not None:
            self._unit = unit

        text = value + (f" {self._unit}" if self._unit else "")
        self.value_label.setText(text)

        if accent is not None:
            self.value_label.setStyleSheet(f"color: {accent};")


class KeyValueRow(QWidget):
    """A label / value pair on one line, with the value in a monospace face.

    Used for hardware and incident detail so numbers line up and are scannable
    at a glance during a demo.
    """

    def __init__(
        self,
        label: str,
        value: str = "--",
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.name_label = QLabel(label)
        self.name_label.setObjectName("CardCaption")
        layout.addWidget(self.name_label)
        layout.addStretch(1)

        self.value_label = QLabel(value)
        self.value_label.setObjectName("MonoValue")
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.value_label)

    def set_value(self, value: str, *, accent: str | None = None) -> None:
        self.value_label.setText(value)
        self.value_label.setStyleSheet(
            f"color: {accent};" if accent else ""
        )


class Dot(QWidget):
    """A small filled circle used as an indicator next to a label."""

    def __init__(
        self,
        colour: str = theme.GREY,
        diameter: int = 10,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._colour = colour
        self._diameter = diameter
        self.setFixedSize(diameter, diameter)

    def set_colour(self, colour: str) -> None:
        if colour != self._colour:
            self._colour = colour
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        from PySide6.QtGui import QColor, QPainter, QPen

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QPen(QColor(self._colour).darker(140), 1))
            painter.setBrush(QColor(self._colour))
            inset = 1
            painter.drawEllipse(
                inset,
                inset,
                self._diameter - 2 * inset,
                self._diameter - 2 * inset,
            )
        finally:
            painter.end()


class StatusPill(QLabel):
    """A rounded, colour-coded status label."""

    def __init__(
        self,
        text: str = "",
        colour: str = theme.GREY,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.set_colour(colour)

    def set_colour(self, colour: str) -> None:
        self.setStyleSheet(
            "QLabel {"
            f"background-color: {colour};"
            "color: #FFFFFF;"
            "border-radius: 4px;"
            "padding: 2px 9px;"
            "font-size: 10px;"
            "font-weight: 700;"
            "letter-spacing: 0.6px;"
            "}"
        )

    def set_status(self, text: str, colour: str) -> None:
        self.setText(text)
        self.set_colour(colour)


def divider() -> QFrame:
    """A one-pixel horizontal rule matching the card border."""

    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background-color: {theme.BORDER}; border: none;")
    return line
