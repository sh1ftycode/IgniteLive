"""Icons drawn in code: the Ignite logo and small line glyphs (check, warning, lock…).

Drawn rather than typed as symbols, so they look the same on every platform and font,
and follow the theme's colours.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

from ignite_live.ui.theme import COLORS

SCALE = 2  # Glyphs are drawn at twice their size, for high-DPI screens.


# The flame, in unit coordinates (0..1): a start point, then cubic curves as (control 1, control 2, end).
FLAME = ((0.50, 0.89), [
    ((0.31, 0.89), (0.20, 0.75), (0.22, 0.59)),
    ((0.24, 0.45), (0.36, 0.39), (0.40, 0.27)),
    ((0.42, 0.21), (0.41, 0.16), (0.39, 0.11)),
    ((0.56, 0.17), (0.64, 0.31), (0.62, 0.43)),
    ((0.68, 0.39), (0.70, 0.33), (0.69, 0.28)),
    ((0.78, 0.37), (0.81, 0.51), (0.78, 0.63)),
    ((0.75, 0.79), (0.64, 0.89), (0.50, 0.89)),
])
CORE = ((0.50, 0.85), [  # Cut out of the flame, so the tile shows through.
    ((0.40, 0.85), (0.34, 0.77), (0.36, 0.68)),
    ((0.38, 0.59), (0.47, 0.55), (0.49, 0.45)),
    ((0.58, 0.53), (0.65, 0.61), (0.64, 0.70)),
    ((0.63, 0.79), (0.58, 0.85), (0.50, 0.85)),
])


def _shape(shape, rect: QRectF) -> QPainterPath:
    (x, y), curves = shape
    at = lambda px, py: QPointF(rect.x() + px * rect.width(), rect.y() + py * rect.height())  # noqa: E731
    path = QPainterPath(at(x, y))
    for c1, c2, end in curves:
        path.cubicTo(at(*c1), at(*c2), at(*end))
    path.closeSubpath()
    return path


def paint_logo(painter: QPainter, rect: QRectF) -> None:
    """The app icon: a flame with a hollow core, cyan at the tip to violet at the base, on a midnight tile."""
    s = rect.width()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(COLORS["line_strong"]), max(1.0, s / 128)))  # An edge on dark backgrounds.
    painter.setBrush(QColor(COLORS["surface"]))
    painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), s * 0.22, s * 0.22)
    painter.setPen(Qt.PenStyle.NoPen)
    fire = QLinearGradient(rect.x(), rect.y() + s * 0.11, rect.x(), rect.y() + s * 0.89)
    fire.setColorAt(0, QColor(COLORS["accent_2"]))
    fire.setColorAt(1, QColor(COLORS["accent"]))
    painter.setBrush(fire)
    painter.drawPath(_shape(FLAME, rect).subtracted(_shape(CORE, rect)))
    painter.restore()


def logo(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    paint_logo(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


def _glyph_path(name: str) -> QPainterPath:
    """Line drawings on a 16×16 grid."""
    path = QPainterPath()
    if name == "check":
        path.moveTo(3.5, 8.5)
        path.lineTo(6.5, 11.5)
        path.lineTo(12.5, 4.5)
    elif name in ("cross", "close"):
        inset = 4.5 if name == "close" else 3.5
        path.moveTo(inset, inset)
        path.lineTo(16 - inset, 16 - inset)
        path.moveTo(16 - inset, inset)
        path.lineTo(inset, 16 - inset)
    elif name == "circle":
        path.addEllipse(QRectF(3, 3, 10, 10))
    elif name == "info":
        path.addEllipse(QRectF(2, 2, 12, 12))
        path.moveTo(8, 7.5)
        path.lineTo(8, 11)
        path.moveTo(8, 5)
        path.lineTo(8, 5.1)
    elif name == "warn":
        path.moveTo(8, 2.5)
        path.lineTo(14, 13)
        path.lineTo(2, 13)
        path.closeSubpath()
        path.moveTo(8, 6.5)
        path.lineTo(8, 9.3)
        path.moveTo(8, 11)
        path.lineTo(8, 11.1)
    elif name in ("lock", "unlock"):
        path.addRoundedRect(QRectF(3.5, 7.5, 9, 6.5), 1.5, 1.5)
        path.moveTo(5.5, 7.5)
        path.lineTo(5.5, 5)
        path.arcTo(QRectF(5.5, 2.5, 5, 5), 180, -180)
        path.lineTo(10.5, 7.5 if name == "lock" else 5)
    else:
        raise ValueError(name)
    return path


def glyph_pixmap(name: str, color: str, size: int = 16) -> QPixmap:
    pixmap = QPixmap(size * SCALE, size * SCALE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size * SCALE / 16, size * SCALE / 16)
    pen = QPen(QColor(color), 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawPath(_glyph_path(name))
    painter.end()
    pixmap.setDevicePixelRatio(SCALE)
    return pixmap


def glyph(name: str, color: str = COLORS["muted"], size: int = 16) -> QIcon:
    return QIcon(glyph_pixmap(name, color, size))
