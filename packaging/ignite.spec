# PyInstaller spec for the Ignite desktop app. Build with:
#   python packaging/build.py
# One-folder build: faster startup than one-file, fewer antivirus false
# positives, and Qt stays as separate shared libraries (LGPL).
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "src/ignite_live/__init__.py").read_text()).group(1)

datas = collect_data_files("ignite_live")  # the overlay pages
datas += copy_metadata("keyring")  # keyring finds its OS backends via entry points
datas += copy_metadata("edge_tts")  # Includes its LGPL licence text.

# API keys from `build.py --bundle-keys` (see packaging/export_keys.py). Never present otherwise.
BUNDLED_KEYS = ROOT / "packaging" / "bundled_keys.json"
if BUNDLED_KEYS.exists():
    datas.append((str(BUNDLED_KEYS), "ignite_live"))

hiddenimports = (
    collect_submodules("keyring.backends")
    # TikTokLive and its protobuf bindings resolve some modules dynamically.
    + collect_submodules("TikTokLive")
    + collect_submodules("TikTokLiveProto")
)

a = Analysis(
    [str(ROOT / "packaging/launch.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "tkinter",
        # Not "cryptography": the Linux keychain (secretstorage) needs it.
        "ignite_live.server",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        # Not QtMultimedia: text to speech (QtTextToSpeech) needs it.
        "PySide6.Qt3DCore",
    ],
    noarchive=False,
    # edge-tts is LGPL: ship it as plain .py files people can replace, not inside the archive.
    module_collection_mode={"edge_tts": "py"},
)

# Qt plugins drag in QtQuick/QML/PDF, which the app never uses, plus Qt's
# translations (the UI is English-only). Dropping them saves ~30 MB. Keep
# QtMultimedia's FFmpeg plugin: it plays the Edge and TikTok voices (MP3).
UNUSED_QT = ("qt6quick", "qt6qml", "qt6pdf", "qpdf", "qt6virtualkeyboard")


def wanted(entry):
    dest = entry[0].replace("\\", "/").lower()
    name = dest.rsplit("/", 1)[-1]
    return not (any(part in name for part in UNUSED_QT) or "/qt/translations/" in f"/{dest}")


a.binaries = [entry for entry in a.binaries if wanted(entry)]
a.datas = [entry for entry in a.datas if wanted(entry)]
pyz = PYZ(a.pure)

version_file = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
    )

    numbers = tuple(int(part) for part in (VERSION.split(".") + ["0"] * 4)[:4])
    version_file = VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "Ignite"),
                StringStruct("FileDescription", "Ignite"),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", "Ignite"),
                StringStruct("OriginalFilename", "Ignite.exe"),
                StringStruct("ProductName", "Ignite"),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Ignite",
    console=False,
    icon=str(ROOT / ("packaging/ignite.ico" if sys.platform == "win32" else "packaging/icon.png")),
    version=version_file,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Ignite", upx=False)
