"""Updates from Ignite's GitHub releases.

Ignite asks GitHub for the latest release and compares its version with its own.
When it's newer and this copy can update itself, it downloads the right file in
the background, checks it against the release's SHA256SUMS.txt (the build adds
one to every release), and installs it:

- Windows, installed with Ignite-Setup: the new setup runs silently, replaces
  the program in place, and (when asked) starts Ignite again.
- Linux AppImage: the new AppImage replaces the old file, at the same path.

Other copies (the Windows zip, the Linux tar.gz, running from source) can't
safely replace themselves: they only learn that an update exists.

The checksum guards against a broken or swapped download. It comes from the
same release, so it can't protect against someone who controls the GitHub
repository itself; only signing the builds could.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable

from ignite_live.core.http import Transport, TransportError, tls_context, urllib_transport

REPO = "sh1ftycode/IgniteLive"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
CHECKSUMS = "SHA256SUMS.txt"
MAX_DOWNLOAD = 800 * 1024 * 1024
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

# How this copy was installed, so how (and whether) it can update itself.
INSTALLER = "installer"  # Windows, with Ignite-Setup: run the new setup.
APPIMAGE = "appimage"  # Linux: replace the AppImage file.
MANUAL = "manual"  # Zip, tar.gz, or source: point to the download.


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
    kind: str  # INSTALLER, APPIMAGE or MANUAL.
    target: Path | None = None  # The AppImage file to replace; for the installer, the program folder.


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION.match((text or "").strip())
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def is_newer(candidate: str, current: str) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    return new is not None and old is not None and new > old


def latest_release(transport: Transport = urllib_transport, user_agent: str = "Ignite") -> Release:
    """The newest published release (GitHub's "latest": no drafts or pre-releases). Blocking."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": user_agent, "X-GitHub-Api-Version": "2022-11-28"}
    try:
        status, body = transport("GET", LATEST_URL, headers, None)
    except TransportError as error:
        raise UpdateError(f"Couldn't reach GitHub: {error}.") from error
    if status == 404:
        raise UpdateError("No release has been published yet.")
    if status == 403:
        raise UpdateError("GitHub is limiting update checks right now; Ignite tries again later.")
    if status != 200:
        raise UpdateError(f"GitHub returned HTTP {status}.")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise UpdateError("GitHub returned an unreadable answer.") from error
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
    if platform == "win32":
        folder = Path(executable).parent
        if (folder / "unins000.exe").is_file():  # Inno Setup's uninstaller: installed with Ignite-Setup.
            return Install(INSTALLER, folder)
        return Install(MANUAL)
    appimage = environ.get("APPIMAGE")
    if platform.startswith("linux") and appimage:
        path = Path(appimage)
        if path.is_file() and os.access(path.parent, os.W_OK) and os.access(path, os.W_OK):
            return Install(APPIMAGE, path)
    return Install(MANUAL)


def asset_name(release: Release, install: Install) -> str | None:
    """The file this copy needs from the release (None: it can't update itself)."""
    if install.kind == INSTALLER:
        return f"Ignite-Setup-{release.version}.exe"
    if install.kind == APPIMAGE:
        return f"Ignite-{release.version}-x86_64.AppImage"
    return None


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
            subprocess.Popen([str(how.target)], start_new_session=True, close_fds=True)
    else:
        raise UpdateError("This copy of Ignite can't update itself; download the new version instead.")


def download_folder(how: Install, settings_dir: Path) -> Path:
    """AppImages download next to the one they replace (the swap must stay on one drive); setups to settings."""
    if how.kind == APPIMAGE and how.target is not None:
        return how.target.parent
    return settings_dir / "updates"
