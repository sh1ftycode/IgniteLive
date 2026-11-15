"""Phone icons from the app's own logo (ui/icons.py), like make_icon.py does for the desktop.

    python packaging/make_mobile_icons.py

Writes the web app's home-screen icons (web/ignite_web/static/icons/, used by its manifest and by iPhone's Add to
Home Screen) and the Android app's launcher icons (android/res/mipmap-*/). Run it again when the logo changes.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ignite_live  # noqa: E402,F401  (first: it fixes an import-order problem on Python 3.15, see its __init__)
from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402

from ignite_live.ui.icons import LOGO_TILE, logo, paint_logo  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web" / "ignite_web" / "static" / "icons"
ANDROID = ROOT / "android" / "res"
ANDROID_SIZES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}


def full_bleed(size: int, flame_share: float) -> QImage:
    """The flame on a square of the logo's tile colour, for phones that cut icons to their own shape (iPhone's
    rounded square, Android's circle or squircle). ``flame_share``: how much of the square the logo's box fills."""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(QColor(LOGO_TILE))
    painter = QPainter(image)
    inset = size * (1 - flame_share) / 2
    paint_logo(painter, QRectF(inset, inset, size - 2 * inset, size - 2 * inset), tile=False)
    painter.end()
    return image


def main() -> None:
    QGuiApplication([])
    WEB.mkdir(parents=True, exist_ok=True)
    logo(192).save(str(WEB / "icon-192.png"))  # "any": shown as it is, the desktop's own icon.
    logo(512).save(str(WEB / "icon-512.png"))
    full_bleed(512, 0.72).save(str(WEB / "maskable-512.png"))  # The flame stays inside the 80% safe zone.
    full_bleed(180, 0.9).save(str(WEB / "apple-touch-icon.png"))  # iPhone rounds the corners itself.
    for density, size in ANDROID_SIZES.items():
        folder = ANDROID / f"mipmap-{density}"
        folder.mkdir(parents=True, exist_ok=True)
        logo(size).save(str(folder / "ic_launcher.png"))
        full_bleed(size, 0.85).save(str(folder / "ic_launcher_round.png"))
    print(f"Wrote {WEB.relative_to(ROOT)} and {ANDROID.relative_to(ROOT)}/mipmap-*")


if __name__ == "__main__":
    main()
