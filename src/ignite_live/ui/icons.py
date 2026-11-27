"""Icons drawn in code: the Ignite logo and small line glyphs (check, warning, lock…).

Drawn rather than typed as symbols, so they look the same on every platform and font,
and follow the theme's colours.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

from ignite_live.ui.theme import LOGO_BASE, LOGO_EDGE, LOGO_TILE, LOGO_TIP, colors

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


def paint_logo(painter: QPainter, rect: QRectF, tile: bool = True) -> None:
    """The app icon: a flame with a hollow core, cyan at the tip to violet at the base, on a midnight tile.
    ``tile=False``: the flame alone (phone icons put it on a full-bleed LOGO_TILE that the phone shapes itself)."""
    s = rect.width()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if tile:
        painter.setPen(QPen(QColor(LOGO_EDGE), max(1.0, s / 128)))  # An edge on dark backgrounds.
        painter.setBrush(QColor(LOGO_TILE))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), s * 0.22, s * 0.22)
    painter.setPen(Qt.PenStyle.NoPen)
    fire = QLinearGradient(rect.x(), rect.y() + s * 0.11, rect.x(), rect.y() + s * 0.89)
    fire.setColorAt(0, QColor(LOGO_TIP))
    fire.setColorAt(1, QColor(LOGO_BASE))
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
    elif name == "play":
        path.moveTo(5, 3.5)
        path.lineTo(12.5, 8)
        path.lineTo(5, 12.5)
        path.closeSubpath()
    elif name == "pause":
        path.addRoundedRect(QRectF(4, 3.5, 2.5, 9), 0.8, 0.8)
        path.addRoundedRect(QRectF(9.5, 3.5, 2.5, 9), 0.8, 0.8)
    elif name in ("next", "previous"):
        # A triangle pointing at a bar; "previous" is its mirror image.
        x = (lambda value: value) if name == "next" else (lambda value: 16 - value)
        path.moveTo(x(3.5), 3.5)
        path.lineTo(x(10), 8)
        path.lineTo(x(3.5), 12.5)
        path.closeSubpath()
        path.moveTo(x(12.5), 3.5)
        path.lineTo(x(12.5), 12.5)
    elif name == "eye":
        path.moveTo(1.5, 8)
        path.quadTo(8, 1.5, 14.5, 8)
        path.quadTo(8, 14.5, 1.5, 8)
        path.addEllipse(QRectF(6, 6, 4, 4))
    elif name == "people":
        path.addEllipse(QRectF(3.7, 3, 4.6, 4.6))
        path.moveTo(1.8, 13.5)
        path.cubicTo(1.8, 10.2, 4, 9.2, 6, 9.2)
        path.cubicTo(8, 9.2, 10.2, 10.2, 10.2, 13.5)
        path.addEllipse(QRectF(9.6, 4.2, 3.4, 3.4))
        path.moveTo(11.8, 9.4)
        path.cubicTo(13.4, 9.6, 14.4, 11, 14.4, 13.5)
    elif name == "heart":
        path.moveTo(8, 13.2)
        path.cubicTo(2.5, 9.6, 1.6, 6.2, 3.6, 4.2)
        path.cubicTo(5.2, 2.7, 7.2, 3.3, 8, 5)
        path.cubicTo(8.8, 3.3, 10.8, 2.7, 12.4, 4.2)
        path.cubicTo(14.4, 6.2, 13.5, 9.6, 8, 13.2)
    elif name == "chat":
        path.addRoundedRect(QRectF(2, 3, 12, 8), 2, 2)
        path.moveTo(5, 11)
        path.lineTo(5, 13.5)
        path.lineTo(8, 11)
    elif name == "menu":
        for y in (4.5, 8, 11.5):
            path.moveTo(3, y)
            path.lineTo(13, y)
    elif name in ("connect", "disconnect"):  # A plug; crossed out to disconnect.
        path.addRoundedRect(QRectF(4.5, 5.5, 7, 5), 1.5, 1.5)
        for x in (6.5, 9.5):
            path.moveTo(x, 2.5)
            path.lineTo(x, 5.5)
        path.moveTo(8, 10.5)
        path.lineTo(8, 14)
        if name == "disconnect":
            path.moveTo(2.5, 13.5)
            path.lineTo(13.5, 2.5)
    elif name == "clear":  # An eraser on a line.
        path.moveTo(3.5, 10)
        path.lineTo(8.5, 5)
        path.lineTo(12.5, 9)
        path.lineTo(8, 13.5)
        path.lineTo(5, 13.5)
        path.closeSubpath()
        path.moveTo(6, 7.5)
        path.lineTo(10.5, 12)
        path.moveTo(8, 13.5)
        path.lineTo(14, 13.5)
    elif name == "popout":  # A window with an arrow leaving it.
        path.moveTo(7, 3.5)
        path.lineTo(3.5, 3.5)
        path.lineTo(3.5, 12.5)
        path.lineTo(12.5, 12.5)
        path.lineTo(12.5, 9)
        path.moveTo(8, 8)
        path.lineTo(13, 3)
        path.moveTo(9.5, 3)
        path.lineTo(13, 3)
        path.lineTo(13, 6.5)
    elif name == "broadcast":  # A dot with two signal arcs, for the sidebar's Stream item.
        path.addEllipse(QRectF(6.8, 10.8, 2.4, 2.4))
        path.arcMoveTo(QRectF(4, 8, 8, 8), 40)
        path.arcTo(QRectF(4, 8, 8, 8), 40, 100)
        path.arcMoveTo(QRectF(1.5, 5.5, 13, 13), 40)
        path.arcTo(QRectF(1.5, 5.5, 13, 13), 40, 100)
    elif name == "music":  # A quarter note, for the sidebar's Music item.
        path.addEllipse(QRectF(3.3, 10.3, 3.6, 3))
        path.moveTo(6.7, 11.4)
        path.lineTo(6.7, 3)
        path.lineTo(12, 2)
        path.lineTo(12, 8.8)
    elif name == "layers":  # A stacked diamond and two chevrons, for the sidebar's Overlays item.
        path.moveTo(8, 2.5)
        path.lineTo(14, 6.5)
        path.lineTo(8, 10.5)
        path.lineTo(2, 6.5)
        path.closeSubpath()
        path.moveTo(2, 9.3)
        path.lineTo(8, 13.3)
        path.lineTo(14, 9.3)
        path.moveTo(2, 11.8)
        path.lineTo(8, 15.3)
        path.lineTo(14, 11.8)
    elif name == "zap":  # A lightning bolt, for the sidebar's Actions item.
        path.moveTo(9, 1.5)
        path.lineTo(4, 9)
        path.lineTo(7.3, 9)
        path.lineTo(6, 14.5)
        path.lineTo(12, 7)
        path.lineTo(8.5, 7)
        path.closeSubpath()
    elif name == "chart":  # A bar chart, for the sidebar's Streams item.
        path.moveTo(2.5, 13.5)
        path.lineTo(13.5, 13.5)
        path.moveTo(4.5, 13.5)
        path.lineTo(4.5, 9)
        path.moveTo(8, 13.5)
        path.lineTo(8, 5)
        path.moveTo(11.5, 13.5)
        path.lineTo(11.5, 7)
    elif name == "controller":  # A game controller, for the sidebar's Games item.
        path.addRoundedRect(QRectF(1.5, 5, 13, 7), 3.3, 3.3)
        path.moveTo(4.8, 6.8)
        path.lineTo(4.8, 10.2)
        path.moveTo(3.1, 8.5)
        path.lineTo(6.5, 8.5)
        path.addEllipse(QRectF(9.8, 6.8, 1, 1))
        path.addEllipse(QRectF(11.7, 8.8, 1, 1))
    elif name == "clock":  # For the Overview page's Stream Uptime tile.
        path.addEllipse(QRectF(2.5, 2.5, 11, 11))
        path.moveTo(8, 8)
        path.lineTo(8, 4.3)
        path.moveTo(8, 8)
        path.lineTo(10.8, 9.3)
    elif name == "star":  # For the Overview page's Subscribers tile and Recent Events card.
        for x, y in ((8.00, 1.70), (9.47, 5.98), (13.99, 6.05), (10.38, 8.77), (11.70, 13.10),
                     (8.00, 10.50), (4.30, 13.10), (5.62, 8.77), (2.01, 6.05), (6.53, 5.98)):
            path.lineTo(x, y) if path.elementCount() else path.moveTo(x, y)
        path.closeSubpath()
    elif name == "home":  # A simple house, for the sidebar's Overview item.
        path.moveTo(2, 8.3)
        path.lineTo(8, 3)
        path.lineTo(14, 8.3)
        path.moveTo(3.8, 7)
        path.lineTo(3.8, 13.5)
        path.lineTo(12.2, 13.5)
        path.lineTo(12.2, 7)
        path.moveTo(6.3, 13.5)
        path.lineTo(6.3, 9.5)
        path.lineTo(9.7, 9.5)
        path.lineTo(9.7, 13.5)
    elif name == "bell":  # For the sidebar's Alerts item.
        path.moveTo(4, 11.5)
        path.cubicTo(4, 7.5, 5, 5, 8, 5)
        path.cubicTo(11, 5, 12, 7.5, 12, 11.5)
        path.lineTo(13.3, 13)
        path.lineTo(2.7, 13)
        path.closeSubpath()
        path.moveTo(6.6, 13)
        path.cubicTo(6.6, 14, 7.2, 14.5, 8, 14.5)
        path.cubicTo(8.8, 14.5, 9.4, 14, 9.4, 13)
        path.moveTo(8, 3.3)
        path.lineTo(8, 5)
    elif name == "puzzle":  # A jigsaw piece, for the sidebar's Integrations item.
        path.moveTo(3, 3)
        path.lineTo(6.6, 3)
        path.arcTo(QRectF(6.2, 1.2, 3.6, 3.6), 180, -180)
        path.lineTo(13, 3)
        path.lineTo(13, 6.6)
        path.arcTo(QRectF(11.2, 5.2, 3.6, 3.6), 90, 180)
        path.lineTo(13, 13)
        path.lineTo(3, 13)
        path.closeSubpath()
    elif name == "gear":  # A hex nut, for the sidebar's Settings item.
        path.moveTo(8, 3)
        path.lineTo(12.33, 5.5)
        path.lineTo(12.33, 10.5)
        path.lineTo(8, 13)
        path.lineTo(3.67, 10.5)
        path.lineTo(3.67, 5.5)
        path.closeSubpath()
        path.addEllipse(QRectF(6.3, 6.3, 3.4, 3.4))
    elif name in ("chevron-left", "chevron-right"):  # The sidebar's collapse toggle.
        x = (lambda value: value) if name == "chevron-left" else (lambda value: 16 - value)
        path.moveTo(x(10), 3)
        path.lineTo(x(5), 8)
        path.lineTo(x(10), 13)
    else:
        raise ValueError(name)
    return path


def glyph_pixmap(name: str, color: str, size: int = 16) -> QPixmap:
    from ignite_live.ui import lucide

    if lucide.name_of(name) in lucide.ICONS:  # The app's icon set; the drawings below are the fallback.
        return lucide.pixmap(lucide.name_of(name), color, size)
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


def glyph(name: str, color: str | None = None, size: int = 16) -> QIcon:
    return QIcon(glyph_pixmap(name, color or colors().text_muted, size))
