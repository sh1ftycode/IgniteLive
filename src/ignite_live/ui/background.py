"""The dashboard background: a picture (one of a few built-in ones, or your own) with hue, blur and dim, drawn behind
the screens. Overlays stay transparent: they sit on top of your scene in OBS, so a picture there would cover it.

``render`` does the work (and remembers the last few results, so moving a slider and the window showing it don't both
pay for it); ``BackgroundHost`` is the widget that paints the result behind everything in it.
"""

from __future__ import annotations

import math
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QRadialGradient
from PySide6.QtWidgets import QWidget

WORK = QSize(960, 540)  # Everything is worked out at this size, then scaled to fit wherever it is shown.
HUE_WIDTH = 400  # The hue is turned at a smaller size: it's per pixel, and the picture is soft anyway.
MAX_FILE = 25 * 1024 * 1024
MODES = ("full", "subtle", "off")
OPACITY = {"full": 1.0, "subtle": 0.45}  # How much of the picture shows through.
CARD_ALPHA = {"full": 0.80, "subtle": 0.92}  # How solid the panels over it are.
PRESETS = {"aurora": "Aurora", "sunset": "Sunset", "forest": "Forest", "ocean": "Ocean"}
FOLDER = "backgrounds"
_cache: dict[tuple, QImage] = {}


def preset_image(key: str, size: QSize = WORK) -> QImage:
    """One of the built-in pictures, drawn (nothing to download, nothing to store)."""
    image = QImage(size, QImage.Format.Format_RGB32)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    w, h = size.width(), size.height()
    if key == "sunset":
        g = QLinearGradient(0, 0, 0, h)
        for at, color in ((0, "#1a0f3a"), (0.45, "#6b2a9c"), (0.7, "#d63a8e"), (1, "#ffb347")):
            g.setColorAt(at, QColor(color))
        p.fillRect(image.rect(), g)
        sun = QRadialGradient(QPointF(w * 0.5, h * 0.78), h * 0.55)
        sun.setColorAt(0, QColor(255, 220, 150, 230))
        sun.setColorAt(1, QColor(255, 120, 80, 0))
        p.fillRect(image.rect(), sun)
    elif key == "forest":
        g = QLinearGradient(0, 0, w, h)
        for at, color in ((0, "#0b1f1a"), (0.5, "#1f6f4a"), (1, "#0d3b2e")):
            g.setColorAt(at, QColor(color))
        p.fillRect(image.rect(), g)
        light = QRadialGradient(QPointF(w * 0.3, h * 0.25), w * 0.45)
        light.setColorAt(0, QColor(120, 255, 170, 90))
        light.setColorAt(1, QColor(120, 255, 170, 0))
        p.fillRect(image.rect(), light)
    elif key == "ocean":
        p.fillRect(image.rect(), QColor("#0a2540"))
        sphere = QRadialGradient(QPointF(w * 0.62, h * 0.42), h * 0.8)
        sphere.setColorAt(0, QColor("#8fdcff"))
        sphere.setColorAt(0.45, QColor("#2a78b8"))
        sphere.setColorAt(1, QColor(10, 37, 64, 0))
        p.fillRect(image.rect(), sphere)
    else:  # "aurora"
        g = QLinearGradient(0, h, w, 0)
        for at, color in ((0, "#2b1f7a"), (0.4, "#7a2cc0"), (0.75, "#ff4fd8"), (1, "#4fc3ff")):
            g.setColorAt(at, QColor(color))
        p.fillRect(image.rect(), g)
    p.end()
    return image


def folder_of(settings_path: Path) -> Path:
    return settings_path.parent / FOLDER


def load_source(source: str, folder: Path) -> QImage | None:
    """The picture a setting names: "preset:<name>", or "file:<name>" in ``folder``. None for none, or if it's gone."""
    kind, _, name = source.partition(":")
    if kind == "preset" and name in PRESETS:
        return preset_image(name)
    if kind == "file" and name and Path(name).name == name:  # A plain file name: never a path out of the folder.
        image = QImage(str(folder / name))
        return None if image.isNull() else image
    return None


def add_file(path: Path, folder: Path) -> str:
    """Copy a picture of the user's into the backgrounds folder; returns its setting ("file:<name>"). Raises
    ``ValueError`` with something fit to show if it isn't a usable picture."""
    try:
        size = path.stat().st_size
    except OSError as error:
        raise ValueError("Couldn't read that file.") from error
    if size > MAX_FILE:
        raise ValueError("That picture is too big (25 MB at most).")
    if QImage(str(path)).isNull():
        raise ValueError("That file isn't a picture Ignite can open.")
    folder.mkdir(parents=True, exist_ok=True)
    name = f"bg-{int(time.time())}{path.suffix.lower()[:6] or '.img'}"
    shutil.copyfile(path, folder / name)
    return f"file:{name}"


def forget_file(source: str, folder: Path) -> None:
    """Delete an uploaded picture that is no longer used."""
    kind, _, name = source.partition(":")
    if kind == "file" and name and Path(name).name == name:
        try:
            (folder / name).unlink()
        except OSError:
            pass


def _cover(image: QImage, size: QSize) -> QImage:
    scaled = image.scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    left, top = (scaled.width() - size.width()) // 2, (scaled.height() - size.height()) // 2
    return scaled.copy(left, top, size.width(), size.height()).convertToFormat(QImage.Format.Format_RGB32)


def rotate_hue(image: QImage, degrees: int) -> QImage:
    """``image`` with every colour's hue turned by ``degrees`` (brightness kept)."""
    if degrees % 360 == 0:
        return image
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    m = ((0.213 + c * 0.787 - s * 0.213, 0.715 - c * 0.715 - s * 0.715, 0.072 - c * 0.072 + s * 0.928),
         (0.213 - c * 0.213 + s * 0.143, 0.715 + c * 0.285 + s * 0.140, 0.072 - c * 0.072 - s * 0.283),
         (0.213 - c * 0.213 - s * 0.787, 0.715 - c * 0.715 + s * 0.715, 0.072 + c * 0.928 + s * 0.072))
    image = image.convertToFormat(QImage.Format.Format_RGB32)
    width, height = image.width(), image.height()
    out = QImage(width, height, QImage.Format.Format_RGB32)
    source = image.constBits()
    pixels = memoryview(source).cast("I")
    result = bytearray(width * height * 4)
    target = memoryview(result).cast("I")
    (a0, a1, a2), (b0, b1, b2), (c0, c1, c2) = m
    for i in range(width * height):
        px = pixels[i]
        r, g, b = (px >> 16) & 255, (px >> 8) & 255, px & 255
        nr = int(a0 * r + a1 * g + a2 * b)
        ng = int(b0 * r + b1 * g + b2 * b)
        nb = int(c0 * r + c1 * g + c2 * b)
        nr = 0 if nr < 0 else 255 if nr > 255 else nr
        ng = 0 if ng < 0 else 255 if ng > 255 else ng
        nb = 0 if nb < 0 else 255 if nb > 255 else nb
        target[i] = 0xFF000000 | (nr << 16) | (ng << 8) | nb
    out = QImage(bytes(result), width, height, width * 4, QImage.Format.Format_RGB32).copy()
    return out


def blurred(image: QImage, amount: int) -> QImage:
    """Soften by scaling down and back up: cheap, and it looks like blur at any size."""
    if amount <= 0:
        return image
    factor = 1 + amount * 0.6
    small = image.scaled(max(2, int(image.width() / factor)), max(2, int(image.height() / factor)),
                         Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return small.scaled(image.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


def render(source: QImage, key: str, hue: int, blur: int, dim: int) -> QImage:
    """The picture as it will be drawn: cropped to fit, turned, softened and darkened. ``key`` says which picture
    it came from, so a repeat is answered from memory."""
    cache_key = (key, hue % 360, blur, dim)
    if cache_key in _cache:
        return _cache[cache_key]
    image = _cover(source, WORK)
    if hue % 360:
        small = image.scaledToWidth(HUE_WIDTH, Qt.TransformationMode.SmoothTransformation)
        image = rotate_hue(small, hue).scaled(WORK, Qt.AspectRatioMode.IgnoreAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation)
    image = blurred(image, blur)
    if dim > 0:
        painter = QPainter(image)
        painter.fillRect(image.rect(), QColor(0, 0, 0, round(255 * min(90, dim) / 100)))
        painter.end()
    while len(_cache) >= 6:
        _cache.pop(next(iter(_cache)))
    _cache[cache_key] = image
    return image


class BackgroundHost(QWidget):
    """Paints the picture behind whatever is inside it. Nothing set: it paints nothing, so the window colour shows."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._opacity = 1.0

    def set_picture(self, image: QImage | None, opacity: float = 1.0) -> None:
        self._image, self._opacity = image, opacity
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        if self._image is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(self._opacity)
        target = QRectF(self.rect())
        ratio = self._image.width() / self._image.height()
        width = max(target.width(), target.height() * ratio)
        height = width / ratio
        painter.drawImage(QRectF(target.center().x() - width / 2, target.center().y() - height / 2, width, height),
                          self._image)
