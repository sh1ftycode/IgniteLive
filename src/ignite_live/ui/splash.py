"""The loading screen shown while Ignite starts: logo, name, what it's doing, and a progress bar."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

from ignite_live import __version__
from ignite_live.ui.icons import paint_logo
from ignite_live.ui.theme import DARK

# The loading screen comes up before settings are read, so it is always in the dark palette.
P = DARK

WIDTH, HEIGHT = 480, 300


def _background(ratio: float) -> QPixmap:
    pixmap = QPixmap(int(WIDTH * ratio), int(HEIGHT * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(QColor(P.window))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    glow = QLinearGradient(0, 0, WIDTH, HEIGHT)
    glow.setColorAt(0, QColor(P.panel))
    glow.setColorAt(1, QColor(P.window))
    painter.fillRect(QRectF(0, 0, WIDTH, HEIGHT), glow)
    painter.setPen(QPen(QColor(P.divider), 1))
    painter.drawRect(QRectF(0.5, 0.5, WIDTH - 1, HEIGHT - 1))
    paint_logo(painter, QRectF(WIDTH / 2 - 36, 52, 72, 72))
    title = QFont(QApplication.font())
    title.setPixelSize(30)
    title.setWeight(QFont.Weight.Bold)
    painter.setFont(title)
    painter.setPen(QColor(P.text))
    painter.drawText(QRectF(0, 136, WIDTH, 40), Qt.AlignmentFlag.AlignCenter, "Ignite")
    small = QFont(QApplication.font())
    small.setPixelSize(13)
    painter.setFont(small)
    painter.setPen(QColor(P.text_muted))
    painter.drawText(QRectF(0, 174, WIDTH, 22), Qt.AlignmentFlag.AlignCenter,
                     "Your whole LIVE, in one place")
    painter.setPen(QColor(P.text_disabled))
    painter.drawText(QRectF(0, 12, WIDTH - 16, 18), Qt.AlignmentFlag.AlignRight, f"v{__version__}")
    painter.end()
    return pixmap


class Splash(QSplashScreen):
    def __init__(self) -> None:
        screen = QApplication.primaryScreen()
        super().__init__(_background(screen.devicePixelRatio() if screen is not None else 1.0))
        self.progress = 0.0
        self.message = ""

    def step(self, message: str, progress: float) -> None:
        self.message, self.progress = message, max(0.0, min(1.0, progress))
        self.repaint()
        QApplication.processEvents()

    def drawContents(self, painter: QPainter) -> None:  # noqa: N802 - Qt override
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        small = QFont(QApplication.font())
        small.setPixelSize(12)
        painter.setFont(small)
        painter.setPen(QColor(P.text_muted))
        painter.drawText(QRectF(40, 226, WIDTH - 80, 20), Qt.AlignmentFlag.AlignLeft, self.message)
        track = QRectF(40, 252, WIDTH - 80, 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(P.divider))
        painter.drawRoundedRect(track, 2, 2)
        if self.progress > 0:
            fill = QRectF(track.x(), track.y(), track.width() * self.progress, track.height())
            painter.setBrush(QColor(P.accent))
            painter.drawRoundedRect(fill, 2, 2)
