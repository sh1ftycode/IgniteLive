"""Build the Ignite desktop app for the current OS.

    pip install -e ".[desktop,build]"
    python packaging/build.py [--dist DIR] [--bundle-keys]

Outputs in dist/ (or DIR):
    Ignite/                        the app folder (Ignite.exe on Windows)
    Ignite-<version>-<os>.zip      the same folder, zipped for sharing (.tar.gz on Linux)
    Ignite-Setup-<version>.exe     Windows installer, if Inno Setup is installed
                                       (found on PATH, in Program Files, or via $ISCC)
    Ignite-<version>-x86_64.AppImage   Linux single-file app, if appimagetool is installed
                                       (on PATH or via $APPIMAGETOOL)

--bundle-keys bakes your API keys (YouTube, Euler) into the build, so a fresh
install doesn't ask for them. Anyone with that build can extract them: keep it
private. See packaging/export_keys.py.

To build the Windows version from Linux, use packaging/build-windows-on-linux.sh.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OS_NAMES = {"win32": "windows", "darwin": "macos"}
DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=Ignite
Comment=Run your TikTok LIVE from OBS: stream keys, chat, music, overlays
Exec=Ignite
Icon=ignite
Categories=AudioVideo;Video;
Terminal=false
"""
APP_RUN = """#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/ignite/Ignite" "$@"
"""


def version() -> str:
    text = (ROOT / "src/ignite_live/__init__.py").read_text()
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def find_iscc() -> str | None:
    if os.environ.get("ISCC"):
        return os.environ["ISCC"]
    found = shutil.which("iscc")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        candidate = Path(base or "") / "Inno Setup 6" / "ISCC.exe"
        if base and candidate.exists():
            return str(candidate)
    return None


def build_appimage(app_dir: Path, dist: Path, ver: str) -> None:
    """Wrap the app folder into one Ignite-<version>-x86_64.AppImage with appimagetool."""
    tool = os.environ.get("APPIMAGETOOL") or shutil.which("appimagetool")
    if not tool:
        print("appimagetool not found: skipped the AppImage (https://github.com/AppImage/appimagetool).")
        return
    appdir = ROOT / "build" / "AppDir"
    shutil.rmtree(appdir, ignore_errors=True)
    shutil.copytree(app_dir, appdir / "usr" / "lib" / "ignite", symlinks=True)
    shutil.copy(ROOT / "packaging" / "icon.png", appdir / "ignite.png")
    (appdir / "ignite.desktop").write_text(DESKTOP_ENTRY)
    app_run = appdir / "AppRun"
    app_run.write_text(APP_RUN)
    app_run.chmod(0o755)
    out = dist / f"Ignite-{ver}-x86_64.AppImage"
    subprocess.run([tool, str(appdir), str(out)], check=True, env={**os.environ, "ARCH": "x86_64"})
    print(f"AppImage:   {out}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=ROOT / "dist", help="output folder")
    parser.add_argument("--bundle-keys", action="store_true", help="bake your API keys into this build (keep it private)")
    args = parser.parse_args()
    dist = args.dist.resolve()
    ver = version()
    keys_file = ROOT / "packaging" / "bundled_keys.json"
    if args.bundle_keys:
        if not keys_file.exists():  # build-windows-on-linux.sh exports from the Linux keychain beforehand.
            subprocess.run([sys.executable, str(ROOT / "packaging/export_keys.py")], check=True)
        print("Bundling API keys into this build. Don't share it publicly.")
    else:
        keys_file.unlink(missing_ok=True)  # Never bundle keys by accident.
    try:
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
             "--distpath", str(dist), "--workpath", str(ROOT / "build" / OS_NAMES.get(sys.platform, "linux")),
             str(ROOT / "packaging/ignite.spec")],
            check=True,
        )
    finally:
        keys_file.unlink(missing_ok=True)  # Don't leave keys lying around in the source tree.
    app_dir = dist / "Ignite"
    archive = dist / f"Ignite-{ver}-{OS_NAMES.get(sys.platform, 'linux')}"
    linux = sys.platform.startswith("linux")
    # tar keeps Linux's executable bits for sure; zip is what Windows and macOS open natively.
    packed = shutil.make_archive(str(archive), "gztar" if linux else "zip", dist, "Ignite")
    print(f"\nApp folder: {app_dir}\nArchive:    {packed}")
    if linux:
        build_appimage(app_dir, dist, ver)

    if sys.platform == "win32":
        iscc = find_iscc()
        if iscc:
            subprocess.run(
                [iscc, f"/DAppVersion={ver}", f"/DDistDir={dist}", str(ROOT / "packaging/installer.iss")],
                check=True,
            )
            print(f"Installer:  {dist / f'Ignite-Setup-{ver}.exe'}")
        else:
            print("Inno Setup not found: skipped the installer (https://jrsoftware.org/isdl.php).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
