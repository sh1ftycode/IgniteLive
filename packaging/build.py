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
    Ignite-<version>.flatpak       Linux Flatpak bundle, if flatpak-builder is installed (on PATH, or the
                                       org.flatpak.Builder Flatpak); also flatpak-repo/, the repository Ignite's
                                       Flatpak updates from (the release workflow publishes it on GitHub Pages)

--bundle-keys bakes your API keys (YouTube, Euler) into the build, so a fresh
install doesn't ask for them. Anyone with that build can extract them: keep it
private. See packaging/export_keys.py.

To build the Windows version from Linux, use packaging/build-windows-on-linux.sh.
"""

from __future__ import annotations

import argparse
import datetime
import json
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


FLATPAK_ID = "io.github.sh1ftycode.Ignite"  # Also in src/ignite_live/core/updates.py.
FLATPAK_GPG_PUBLIC = ROOT / "packaging" / "flatpak" / "ignite.gpg"  # Also in src/ignite_live/core/flatpak_key.py.
FLATPAK_RUNTIME = "26.08"  # org.freedesktop.Platform: what the bundled Qt and Python need from the system.
FLATPAK_PAGES_URL = "https://sh1ftycode.github.io/IgniteLive/flatpak/"  # Where the release workflow publishes it.
# What Ignite needs from outside its sandbox. Each line is why.
FLATPAK_FINISH_ARGS = [
    "--share=network",  # Chat, OBS (its WebSocket), the Ignite server, music.
    "--share=ipc",  # X11 shared memory (Qt).
    "--socket=wayland",
    "--socket=fallback-x11",
    "--socket=pulseaudio",  # Text to speech and alert sounds.
    "--device=dri",  # GPU drawing.
    "--talk-name=org.freedesktop.secrets",  # The keychain: stream keys and tokens stay in the system keyring.
    "--talk-name=org.kde.StatusNotifierWatcher",  # The tray icon.
    "--talk-name=org.freedesktop.Flatpak",  # Updates: Ignite runs "flatpak update" on the host (core/updates.py).
    "--filesystem=xdg-pictures/Ignite:create",  # Screenshots (Preferences → OBS), the default folder.
    # Discord Rich Presence: the Discord app's socket (installed normally, or as a Flatpak itself).
    "--filesystem=xdg-run/discord-ipc-0",
    "--filesystem=xdg-run/app/com.discordapp.Discord:create",
]


def flatpak_repo_url() -> str:
    """Where an installed Flatpak updates from: the main Ignite server's /flatpak/ (it forwards to the copy on
    GitHub Pages), so it's on the server everything else goes through; without one, GitHub Pages itself."""
    url = os.environ.get("IGNITE_MAIN_SERVER", "").strip()
    if not url:
        try:
            data = json.loads((ROOT / "packaging" / "bundled_config.json").read_text("utf-8"))
            url = str(data.get("main_server", "")).strip()
        except (OSError, ValueError, AttributeError):
            url = ""
    return url.rstrip("/") + "/flatpak/" if url else FLATPAK_PAGES_URL


def flatpak_builder() -> list[str] | None:
    """flatpak-builder on PATH, else the org.flatpak.Builder Flatpak (Bazzite, Silverblue: no packages)."""
    if os.environ.get("FLATPAK_BUILDER"):
        return os.environ["FLATPAK_BUILDER"].split()
    if shutil.which("flatpak-builder"):
        return ["flatpak-builder"]
    if shutil.which("flatpak"):
        found = subprocess.run(["flatpak", "info", "org.flatpak.Builder"], capture_output=True)
        if found.returncode == 0:
            return ["flatpak", "run", "org.flatpak.Builder"]
    return None


def build_flatpak(app_dir: Path, dist: Path, ver: str) -> None:
    """The app folder as a Flatpak: dist/Ignite-<version>.flatpak to install by hand, and dist/flatpak-repo, the
    repository installed copies update from (Ignite's Update button runs "flatpak update"). The bundled build is
    copied in as it is: the Freedesktop runtime has the system libraries it expects (glibc, X11, Wayland, GL)."""
    builder = flatpak_builder()
    if not builder:
        print("flatpak-builder not found: skipped the Flatpak (flatpak install flathub org.flatpak.Builder).")
        return
    here = ROOT / "packaging" / "flatpak"
    work = ROOT / "build" / "flatpak"
    shutil.rmtree(work, ignore_errors=True)
    source = work / "source"
    shutil.copytree(app_dir, source / "Ignite", symlinks=True)
    for name in ("ignite.sh", f"{FLATPAK_ID}.desktop"):
        shutil.copy(here / name, source / name)
    shutil.copy(ROOT / "packaging" / "icon.png", source / "icon.png")
    metainfo = (here / f"{FLATPAK_ID}.metainfo.xml").read_text("utf-8")
    (source / f"{FLATPAK_ID}.metainfo.xml").write_text(
        metainfo.replace("{{VERSION}}", ver).replace("{{DATE}}", datetime.date.today().isoformat()), "utf-8")
    manifest = {
        "id": FLATPAK_ID,
        "runtime": "org.freedesktop.Platform",
        "runtime-version": FLATPAK_RUNTIME,
        "sdk": "org.freedesktop.Sdk",
        "command": "ignite",
        "finish-args": FLATPAK_FINISH_ARGS,
        "modules": [{
            "name": "ignite",
            "buildsystem": "simple",
            # Already built (PyInstaller): don't strip it. Stripping the Ignite executable would cut off the
            # archive PyInstaller appends to it, and needs eu-strip, which build machines may not have.
            "build-options": {"strip": False, "no-debuginfo": True},
            "build-commands": [
                "mkdir -p /app/lib/ignite",
                "cp -a Ignite/. /app/lib/ignite/",
                "install -Dm755 ignite.sh /app/bin/ignite",
                f"install -Dm644 {FLATPAK_ID}.desktop /app/share/applications/{FLATPAK_ID}.desktop",
                f"install -Dm644 icon.png /app/share/icons/hicolor/512x512/apps/{FLATPAK_ID}.png",
                f"install -Dm644 {FLATPAK_ID}.metainfo.xml /app/share/metainfo/{FLATPAK_ID}.metainfo.xml",
            ],
            "sources": [{"type": "dir", "path": "source"}],
        }],
    }
    manifest_path = work / f"{FLATPAK_ID}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), "utf-8")
    repo = dist / "flatpak-repo"
    # Signed with Ignite's key (IGNITE_FLATPAK_GPG_KEY: its fingerprint; IGNITE_FLATPAK_GPG_HOME: the keyring
    # holding it). Flatpak only installs updates whose signatures it can check against FLATPAK_GPG_PUBLIC.
    key = os.environ.get("IGNITE_FLATPAK_GPG_KEY", "")
    sign = [f"--gpg-sign={key}"] if key else []
    if key and os.environ.get("IGNITE_FLATPAK_GPG_HOME"):
        sign.append(f"--gpg-homedir={os.environ['IGNITE_FLATPAK_GPG_HOME']}")
    if not key:
        print("No IGNITE_FLATPAK_GPG_KEY: the Flatpak repository is unsigned, so installs can't update from it.")
    subprocess.run([*builder, "--user", "--install-deps-from=flathub", "--force-clean", "--disable-rofiles-fuse",
                    "--default-branch=stable", f"--repo={repo}",
                    "--state-dir", str(work / ".flatpak-builder"), str(work / "app"), str(manifest_path)],
                   check=True, cwd=work)
    if sign:  # Signed here, not by flatpak-builder: it may run sandboxed, away from the keyring.
        subprocess.run(["flatpak", "build-sign", *sign, str(repo), FLATPAK_ID, "stable"], check=True)
        # The summary (the repository's table of contents) is checked too.
        subprocess.run(["flatpak", "build-update-repo", *sign, str(repo)], check=True)
    bundle = dist / f"Ignite-{ver}.flatpak"
    repo_url = flatpak_repo_url()
    subprocess.run(["flatpak", "build-bundle", *sign, f"--repo-url={repo_url}", f"--gpg-keys={FLATPAK_GPG_PUBLIC}",
                    "--runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo",
                    str(repo), str(bundle), FLATPAK_ID, "stable"], check=True)
    print(f"Flatpak:    {bundle}\nRepository: {repo} (installs update from {repo_url})")


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
        build_flatpak(app_dir, dist, ver)

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
