"""Branding assets for the dashboard header.

The PolyU and EIE marks are the University's property and are **not** shipped in
this repository. This module therefore never assumes a logo exists:

- If the file is present, it is loaded and scaled with its aspect ratio
  preserved.
- If it is missing, a clearly labelled placeholder is drawn instead, so the
  header keeps its layout and the operator can see that the real asset is
  absent rather than looking at a silently empty box.

The dashboard must never crash because an image is missing, and it must never
substitute a different logo: a placeholder that says "LOGO MISSING" is honest,
whereas a home-made imitation of a University mark would be a trademark problem.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy

from . import theme


#: Repository root, resolved from this file so the dashboard works no matter
#: what the process working directory is.
REPO_ROOT = Path(__file__).resolve().parents[2]
BRANDING_DIR = REPO_ROOT / "assets" / "branding"

POLYU_LOGO = BRANDING_DIR / "polyu_logo.png"
EIE_LOGO = BRANDING_DIR / "eie_logo.png"

#: Header text. The third line exists because this is coursework and must not
#: read as an official University product.
TITLE = "Smart Eye-Shield Safety Operations Centre"
SUBTITLE = "Student AIoT Mini Project \u00b7 Department of Electrical and Electronic Engineering"
CONTEXT = "Coursework demonstration build \u00b7 not an official PolyU product or service"


@dataclass(frozen=True)
class LogoSpec:
    """How one logo should be presented."""

    path: Path
    label: str
    height: int


POLYU = LogoSpec(POLYU_LOGO, "POLYU", 42)
EIE = LogoSpec(EIE_LOGO, "EIE", 42)


def logo_available(spec: LogoSpec) -> bool:
    """True when the real asset exists on disk."""

    return spec.path.is_file()


def missing_logos() -> tuple[str, ...]:
    """Labels of every branding asset that is not present.

    Used by the header to decide whether to show the placeholder notice, and by
    the health panel so a missing asset is visible rather than folklore.
    """

    return tuple(
        spec.label for spec in (POLYU, EIE) if not logo_available(spec)
    )


def placeholder_pixmap(width: int, height: int, label: str) -> QPixmap:
    """Draw the reserved space for a mark that is not present.

    The header sits on white, so the placeholder does too. It is an empty slot
    with a hairline rule and a small name - not a dark filled box, which at
    header scale reads as a heavy block competing with the title.

    Deliberately plain and obviously not a logo, so it can never be mistaken for
    the real mark in a screenshot.
    """

    pixmap = QPixmap(max(1, width), max(1, height))
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)

        # Hairline outline only: enough to say "a slot is reserved here", faint
        # enough that it does not read as a mark of its own.
        painter.setPen(QPen(QColor("#D6DCE5"), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(0.5, 0.5, width - 1.0, height - 1.0))

        font = QFont(theme.FONT_FAMILY_QT, 7)
        font.setBold(True)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 0.6)
        painter.setFont(font)
        painter.setPen(QColor(theme.BURGUNDY))
        painter.drawText(
            QRectF(0, height * 0.20, width, height * 0.42),
            Qt.AlignCenter,
            label,
        )

        font.setPointSize(6)
        font.setBold(False)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 0.0)
        painter.setFont(font)
        painter.setPen(QColor("#7A8699"))
        painter.drawText(
            QRectF(0, height * 0.58, width, height * 0.34),
            Qt.AlignCenter,
            "LOGO NOT ADDED",
        )
    finally:
        painter.end()

    return pixmap


def logo_aspect(spec: LogoSpec) -> float:
    """Width/height of the real asset, or 2.4 for the placeholder.

    The header reserves its width from this, so adding the real logo cannot
    reflow the title. Derived from the file rather than hard-coded, because the
    two University marks do not share a ratio.
    """

    if not logo_available(spec):
        return 2.4

    source = QPixmap(str(spec.path))

    if source.isNull() or source.height() <= 0:
        return 2.4

    return source.width() / source.height()


def load_logo(spec: LogoSpec, max_height: int | None = None) -> QPixmap:
    """Load ``spec`` scaled to ``max_height``, or return a placeholder.

    Aspect ratio is preserved by scaling on height only and letting the width
    follow, which is what stops the University marks from being stretched.
    """

    height = max_height or spec.height

    if not logo_available(spec):
        return placeholder_pixmap(
            int(round(height * logo_aspect(spec))), height, spec.label
        )

    source = QPixmap(str(spec.path))

    if source.isNull() or source.height() <= 0:
        # Present but unreadable - corrupt file, wrong format, no image plugin.
        # logo_aspect() takes the same branch, so the placeholder is the size
        # the header already reserved for it.
        return placeholder_pixmap(
            int(round(height * logo_aspect(spec))), height, spec.label
        )

    scaled = source.scaledToHeight(height, Qt.SmoothTransformation)
    return scaled


class LogoLabel(QLabel):
    """A header logo that always renders something and never stretches.

    The widget reports its placeholder state through ``is_placeholder`` so the
    header can show the "add the real assets" notice once rather than per logo.
    """

    def __init__(
        self,
        spec: LogoSpec,
        *,
        align: Qt.AlignmentFlag = Qt.AlignLeft | Qt.AlignVCenter,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.spec = spec
        self.is_placeholder = not logo_available(spec)

        self.setObjectName("HeaderLogo")
        self.setAlignment(align)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setStyleSheet("background: transparent;")
        self.setToolTip(
            f"{spec.label} logo not found.\n"
            f"Place the real asset at: {spec.path}\n"
            "The dashboard runs without it; this is a labelled placeholder."
            if self.is_placeholder
            else f"{spec.label} logo"
        )
        self.refresh()

    def refresh(self) -> None:
        """Re-read the asset, so adding a logo needs no restart of the layout."""

        self.is_placeholder = not logo_available(self.spec)
        pixmap = load_logo(self.spec)
        self.setPixmap(pixmap)
        self.setFixedSize(QSize(pixmap.width(), pixmap.height()))


def header_notice() -> str:
    """One-line notice for the header, or "" when every asset is present."""

    missing = missing_logos()

    if not missing:
        return ""

    return (
        "Asset placeholder in use ("
        + ", ".join(missing)
        + f"). Add real logo files to {BRANDING_DIR.relative_to(REPO_ROOT)}/ "
        "to replace them."
    )


def ensure_branding_dir() -> Path:
    """Create ``assets/branding`` if absent and return it.

    Needed because the directory is committed with only a README; creating it on
    demand keeps a fresh clone from raising on first run.
    """

    BRANDING_DIR.mkdir(parents=True, exist_ok=True)
    return BRANDING_DIR
