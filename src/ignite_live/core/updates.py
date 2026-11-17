"""Updates from Ignite's GitHub releases, installed from inside Ignite.

Ignite asks GitHub for the latest release and compares its version with its own.
When it's newer, it downloads the right file in the background, checks it
against the release's SHA256SUMS.txt (the build adds one to every release), and
installs it. Nobody is sent to the GitHub page:

- Windows, installed with Ignite-Setup: the new setup runs silently, replaces
  the program in place, and (when asked) starts Ignite again.
- Windows zip: unpacked next to the program folder; once Ignite has closed, a
  small script copies it over the folder (Windows won't let a running program's
  files be replaced) and starts Ignite again.
- Linux AppImage: the new AppImage replaces the old file, at the same path.
- Linux tar.gz: unpacked next to the program folder, then the two folders swap
  places (Linux lets a running program's folder be renamed); the old one is
  deleted at the next start.
- Flatpak: Flatpak itself updates it from Ignite's Flatpak repository
  (``flatpak update``), through the host (flatpak-spawn). The release workflow
  publishes that repository with every release.

Running from source can't update itself: it says so (git pull).

The checksum guards against a broken or swapped download. It comes from the
same release, so it can't protect against someone who controls the GitHub
repository itself; only signing the builds could.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable

from ignite_live.core.bundled import main_server
from ignite_live.core.http import Transport, TransportError, tls_context, urllib_transport

REPO = "sh1ftycode/IgniteLive"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
RELEASE_PATH = "/v1/releases/latest"  # On the main Ignite server (ignite_web/releases.py).
CHECKSUMS = "SHA256SUMS.txt"
MAX_DOWNLOAD = 800 * 1024 * 1024
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

# How this copy was installed, so how (and whether) it can update itself.
INSTALLER = "installer"  # Windows, with Ignite-Setup: run the new setup.
APPIMAGE = "appimage"  # Linux: replace the AppImage file.
ZIP = "zip"  # Windows zip: copy the new folder over this one after Ignite closes.
TARBALL = "tarball"  # Linux tar.gz: swap the folders.
FLATPAK = "flatpak"  # Flatpak updates it.
MANUAL = "manual"  # Running from source: git pull.
FLATPAK_ID = "io.github.sh1ftycode.Ignite"
WAIT_PID = "IGNITE_WAIT_PID"  # A restarted Ignite waits for the copy it replaces to close first (app.py).
OLD_PREFIX = ".ignite-old-"  # A replaced program folder (tar.gz), deleted at the next start.


class UpdateError(Exception):
    """A safe-to-display update problem."""


@dataclass(frozen=True)
class Release:
    version: str  # "1.4.0"
    page: str  # The release page on GitHub.
    notes: str
    assets: dict[str, str]  # File name -> download URL.


@dataclass(frozen=True)
class Install:
    kind: str  # INSTALLER, APPIMAGE, ZIP, TARBALL, FLATPAK or MANUAL.
    target: Path | None = None  # The AppImage file to replace; for the others, the program folder.


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION.match((text or "").strip())
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def is_newer(candidate: str, current: str) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    return new is not None and old is not None and new > old


def latest_release(transport: Transport = urllib_transport, user_agent: str = "Ignite",
                   server: str | None = None) -> Release:
    """The newest published release (GitHub's "latest": no drafts or pre-releases). Blocking.

    Asked of the main Ignite server first (its /v1/releases/latest keeps GitHub's answer for everyone, so update
    checks don't run into GitHub's hourly limit), then of GitHub itself. Either way the files come from this
    repository's GitHub releases, checked against its SHA256SUMS, and only a newer version is ever installed.
    """
    server = main_server() if server is None else server.rstrip("/")
    if server:
        try:
            return _release_from(transport, server + RELEASE_PATH, user_agent, "the Ignite server")
        except UpdateError:
            pass  # Older server, or it's down: GitHub.
    return _release_from(transport, LATEST_URL, user_agent, "GitHub")


def _release_from(transport: Transport, url: str, user_agent: str, source: str) -> Release:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": user_agent, "X-GitHub-Api-Version": "2022-11-28"}
    try:
        status, body = transport("GET", url, headers, None)
    except TransportError as error:
        raise UpdateError(f"Couldn't reach {source}: {error}.") from error
    if status == 404:
        raise UpdateError("No release has been published yet.")
    if status == 403:
        raise UpdateError("GitHub is limiting update checks right now; Ignite tries again later.")
    if status != 200:
        raise UpdateError(f"{source[0].upper() + source[1:]} returned HTTP {status}.")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise UpdateError(f"{source[0].upper() + source[1:]} returned an unreadable answer.") from error
    if not isinstance(payload, dict):
        raise UpdateError(f"{source[0].upper() + source[1:]} returned an unreadable answer.")
    version = str(payload.get("tag_name", "")).lstrip("v")
    if parse_version(version) is None:
        raise UpdateError("The latest release has no version number Ignite understands.")
    assets = {}
    for asset in payload.get("assets") or []:
        name, url = str(asset.get("name", "")), str(asset.get("browser_download_url", ""))
        if name and url.startswith(DOWNLOAD_PREFIX):  # Only files attached to this repository's releases.
            assets[name] = url
    return Release(version, str(payload.get("html_url") or f"https://github.com/{REPO}/releases/latest"),
                   str(payload.get("body") or ""), assets)


def how_installed(frozen: bool | None = None, platform: str = sys.platform, executable: str = sys.executable,
                  environ: dict | None = None) -> Install:
    """Whether this copy can update itself, and what it would replace."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    environ = os.environ if environ is None else environ
    if not frozen:
        return Install(MANUAL)  # Running from source: update with git.
    if platform.startswith("linux") and environ.get("FLATPAK_ID") == FLATPAK_ID:
        return Install(FLATPAK)
    folder = Path(executable).parent
    if platform == "win32":
        if (folder / "unins000.exe").is_file():  # Inno Setup's uninstaller: installed with Ignite-Setup.
            return Install(INSTALLER, folder)
        return Install(ZIP, folder) if _writable(folder) else Install(MANUAL)
    appimage = environ.get("APPIMAGE")
    if platform.startswith("linux") and appimage:
        path = Path(appimage)
        if path.is_file() and os.access(path.parent, os.W_OK) and os.access(path, os.W_OK):
            return Install(APPIMAGE, path)
        return Install(MANUAL)
    if platform.startswith("linux") and _writable(folder):
        return Install(TARBALL, folder)
    return Install(MANUAL)


def _writable(folder: Path) -> bool:
    """The program folder and the folder it's in can both be changed (the update goes beside it)."""
    return os.access(folder, os.W_OK) and os.access(folder.parent, os.W_OK)


def asset_name(release: Release, install: Install) -> str | None:
    """The file this copy needs from the release (None: it can't update itself)."""
    if install.kind == INSTALLER:
        return f"Ignite-Setup-{release.version}.exe"
    if install.kind == APPIMAGE:
        return f"Ignite-{release.version}-x86_64.AppImage"
    if install.kind == ZIP:
        return f"Ignite-{release.version}-windows.zip"
    if install.kind == TARBALL:
        return f"Ignite-{release.version}-linux.tar.gz"
    return None  # Flatpak updates itself; source can't.


def parse_checksums(text: str) -> dict[str, str]:
    """``sha256sum`` output: "<hash>  <file name>" per line."""
    sums = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and _SHA256.match(parts[0].lower()):
            sums[parts[1].lstrip("*").strip()] = parts[0].lower()
    return sums


def _open(url: str) -> BinaryIO:
    request = urllib.request.Request(url, headers={"User-Agent": "Ignite"})
    return urllib.request.urlopen(request, timeout=60, context=tls_context())


def download(release: Release, name: str, folder: Path, opener: Callable[[str], BinaryIO] = _open,
             progress: Callable[[int, int], None] | None = None, save_as: str = "") -> Path:
    """Download one file of the release into ``folder`` (as ``save_as``, default its own name) and check it
    against SHA256SUMS.txt. Blocking."""
    if name not in release.assets:
        raise UpdateError(f"The {release.version} release has no {name}.")
    if CHECKSUMS not in release.assets:
        raise UpdateError(f"The {release.version} release has no checksums, so it can't be installed automatically.")
    try:
        with opener(release.assets[CHECKSUMS]) as response:
            expected = parse_checksums(response.read(1024 * 1024).decode("utf-8", "replace")).get(name)
        if expected is None:
            raise UpdateError(f"The {release.version} checksums don't list {name}.")
        folder.mkdir(parents=True, exist_ok=True)
        part = folder / f".{save_as or name}.part"
        digest, size = hashlib.sha256(), 0
        with opener(release.assets[name]) as response, open(part, "wb") as out:
            total = int(getattr(response, "headers", {}).get("Content-Length") or 0)
            while chunk := response.read(1024 * 256):
                size += len(chunk)
                if size > MAX_DOWNLOAD:
                    raise UpdateError("The update is larger than expected; not installing it.")
                digest.update(chunk)
                out.write(chunk)
                if progress:
                    progress(size, total)
    except OSError as error:
        raise UpdateError(f"The download failed: {error}.") from error
    if digest.hexdigest() != expected:
        part.unlink(missing_ok=True)
        raise UpdateError("The download doesn't match the release's checksum; not installing it.")
    final = folder / (save_as or name)
    os.replace(part, final)
    return final


UPDATE_PREFIX = ".ignite-update-"  # Downloads waiting to be installed, next to an AppImage: clearly ours.


def clean_downloads(folder: Path, keep: str = "") -> None:
    """Remove Ignite's own leftover downloads (older updates, broken parts), never anything else."""
    if not folder.is_dir():
        return
    for path in folder.iterdir():
        name = path.name
        ours = (name.startswith(UPDATE_PREFIX) or (name.startswith(".") and name.endswith(".part") and "Ignite" in name)
                or (folder.name == "updates" and name.startswith("Ignite-Setup-")))
        if ours and name != keep and path.is_file():
            path.unlink(missing_ok=True)
        elif name.startswith((UPDATE_PREFIX, OLD_PREFIX)) and name != keep and path.is_dir():
            shutil.rmtree(path, ignore_errors=True)  # An unpacked update not used, or a replaced program folder.


def unpack(archive: Path, how: Install, version: str) -> Path:
    """Unpack a checked zip or tar.gz beside the program folder; returns the new Ignite folder in it. Blocking.

    Only plain files and folders under one top-level "Ignite" folder are accepted: nothing that would land
    outside it (absolute paths, "..", links pointing out)."""
    staging = archive.parent / f"{UPDATE_PREFIX}{version}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        if how.kind == ZIP:
            with zipfile.ZipFile(archive) as packed:
                for member in packed.namelist():
                    parts = Path(member).parts
                    if Path(member).is_absolute() or ".." in parts or not parts or parts[0] != "Ignite":
                        raise UpdateError("The update archive has unexpected files; not installing it.")
                packed.extractall(staging)
        else:
            with tarfile.open(archive, "r:gz") as packed:
                for member in packed.getmembers():
                    parts = Path(member.name).parts
                    if not parts or parts[0] != "Ignite":
                        raise UpdateError("The update archive has unexpected files; not installing it.")
                packed.extractall(staging, filter="data")  # Refuses paths and links that leave the folder.
    except (OSError, zipfile.BadZipFile, tarfile.TarError) as error:
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError(f"The update couldn't be unpacked: {error}.") from error
    except UpdateError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        archive.unlink(missing_ok=True)
    new = staging / "Ignite"
    executable = new / ("Ignite.exe" if how.kind == ZIP else "Ignite")
    if not executable.is_file():
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError("The update archive has no Ignite program in it; not installing it.")
    return new


def flatpak_update(run: Callable = subprocess.run) -> None:
    """Ask Flatpak on the host to update Ignite (downloads and installs; the running copy is untouched). Blocking."""
    try:
        result = run(["flatpak-spawn", "--host", "flatpak", "update", "--noninteractive", "-y", FLATPAK_ID],
                     capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError(f"Flatpak couldn't update Ignite: {error}.") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise UpdateError("Flatpak couldn't update Ignite" + (f": {detail[-1][:200]}" if detail else "."))


def _restart_env() -> dict:
    return {**os.environ, WAIT_PID: str(os.getpid())}


def wait_for_previous(environ: dict | None = None, timeout: float = 20.0, sleep: Callable[[float], None] | None = None,
                      alive: Callable[[int], bool] | None = None) -> None:
    """A copy started by an update waits for the one it replaces to close; otherwise it would find that copy
    still running, ask it to come forward, and quit (ui/single_instance.py). Linux and macOS only: on Windows
    the update script itself waits."""
    import time

    environ = os.environ if environ is None else environ
    raw = environ.pop(WAIT_PID, "") if environ is os.environ else environ.get(WAIT_PID, "")
    if not raw.isdigit() or sys.platform == "win32":
        return
    pid, sleep = int(raw), sleep or time.sleep

    def running(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    alive = alive or running
    waited = 0.0
    while alive(pid) and waited < timeout:
        sleep(0.2)
        waited += 0.2


def _windows_swap_script(new: Path, target: Path, relaunch: bool) -> Path:
    """A .cmd that waits for this Ignite to close, copies the new folder over the old one, cleans up, and (when
    asked) starts Ignite again. Windows won't replace a running program's files, so it has to happen after."""
    exe = target / "Ignite.exe"
    lines = [
        "@echo off",
        "chcp 65001 >nul",  # UTF-8: folder names with accents (a user name in the path) stay intact.
        "setlocal",
        ":wait",
        f'tasklist /FI "PID eq {os.getpid()}" 2>nul | find "{os.getpid()}" >nul && (timeout /t 1 /nobreak >nul & goto wait)',
        f'robocopy "{new}" "{target}" /MIR /R:5 /W:1 /NFL /NDL /NJH /NJS /NP >nul',
        f'if errorlevel 8 (echo Ignite update failed > "{target.parent / "Ignite-update-failed.txt"}") else '
        f'(rmdir /s /q "{new.parent}")',
    ]
    if relaunch:
        lines.append(f'start "" "{exe}"')
    lines.append('del "%~f0"')
    script = Path(tempfile.gettempdir()) / f"ignite-update-{secrets.token_hex(4)}.cmd"
    script.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8", newline="")  # As written: no extra \r.
    return script


def install(file: Path, how: Install, relaunch: bool) -> None:
    """Hand over to the downloaded update. The caller quits Ignite straight after."""
    if how.kind == INSTALLER:
        # Silent: no wizard. The setup waits for Ignite to close (CloseApplications), replaces it in place,
        # and with /RELAUNCH=1 starts it again (see packaging/installer.iss).
        args = [str(file), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]
        if relaunch:
            args.append("/RELAUNCH=1")
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(args, creationflags=flags, close_fds=True)
    elif how.kind == APPIMAGE and how.target is not None:
        os.chmod(file, 0o755)
        os.replace(file, how.target)  # Same folder, so it's one atomic swap; the running copy keeps its mount.
        if relaunch:
            subprocess.Popen([str(how.target)], start_new_session=True, close_fds=True, env=_restart_env())
    elif how.kind == TARBALL and how.target is not None:
        # ``file`` is the unpacked Ignite folder, beside the program folder: swap them. The running copy keeps
        # its open files; the old folder is deleted at the next start (clean_downloads).
        old = how.target.parent / f"{OLD_PREFIX}{secrets.token_hex(4)}"
        os.rename(how.target, old)
        try:
            os.rename(file, how.target)
        except OSError:
            os.rename(old, how.target)  # Put it back: never leave no Ignite at all.
            raise
        shutil.rmtree(file.parent, ignore_errors=True)  # The emptied staging folder.
        if relaunch:
            subprocess.Popen([str(how.target / "Ignite")], start_new_session=True, close_fds=True, env=_restart_env())
    elif how.kind == ZIP and how.target is not None:
        script = _windows_swap_script(file, how.target, relaunch)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=flags, close_fds=True)
    elif how.kind == FLATPAK:
        # Already installed by flatpak_update(); this only starts the new version once this one has closed.
        if relaunch:
            subprocess.Popen(["flatpak-spawn", "--host", "sh", "-c", f"sleep 3; flatpak run {FLATPAK_ID}"],
                             start_new_session=True, close_fds=True)
    else:
        raise UpdateError("This copy of Ignite runs from source: update it with git pull.")


def download_folder(how: Install, settings_dir: Path) -> Path:
    """AppImages and archives download next to what they replace (the swap must stay on one drive); setups to
    the settings folder."""
    if how.kind == APPIMAGE and how.target is not None:
        return how.target.parent
    if how.kind in (ZIP, TARBALL) and how.target is not None:
        return how.target.parent  # Unpacked beside the program folder: the swap stays on one drive.
    return settings_dir / "updates"
