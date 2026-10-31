from __future__ import annotations

import os
import struct
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402

from ignite_live.ui.icons import logo  # noqa: E402

HERE = Path(__file__).parent
SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size: int) -> QImage:
    return logo(size)  # Drawn by the app itself (ui/icons.py), so the window, tray and installer match.


def png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def write_ico(path: Path) -> None:
    """Multi-size .ico with PNG-compressed entries (supported since Windows Vista)."""
    images = [png_bytes(render(size)) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for size, data in zip(SIZES, images):
        dim = 0 if size >= 256 else size  # 0 means 256 in the ICO format.
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path.write_bytes(header + entries + b"".join(images))


if __name__ == "__main__":
    app = QGuiApplication([])
    render(512).save(str(HERE / "icon.png"))
    write_ico(HERE / "ignite.ico")
    print("Wrote icon.png and ignite.ico")
