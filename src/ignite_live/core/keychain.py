"""Secrets in the OS keychain (Windows Credential Manager, macOS Keychain,
Secret Service on Linux). Never write tokens or keys to settings files."""

from __future__ import annotations

import keyring
from keyring.errors import KeyringError

SERVICE = "Ignite"
LEGACY_SERVICE = "ShiftyTool"  # The app's name before 1.2; read once, then copied to SERVICE.

STREAMLABS_TOKEN = "streamlabs-token"
OBS_PASSWORD = "obs-password"
BACKEND_TOKEN = "backend-device-token"
YOUTUBE_API_KEY = "youtube-api-key"
SIGN_API_KEY = "euler-sign-api-key"
STREAM_KEY = "stream-key"
TIKTOK_SESSION = "tiktok-session-id"  # Only for TikTok's text-to-speech voices.
DISCORD_WEBHOOK = "discord-webhook-url"
HOSTED_CONTROL_KEY = "hosted-control-key"  # Hosted widgets: runs commands through the server's API.
YTMUSIC_COOKIE = "youtube-music-cookie"  # Your own YouTube Music session, for your library's playlists.

# Windows Credential Manager holds about 1,280 characters per entry: longer secrets are stored in parts.
PART_CHARS = 1000
MAX_PARTS = 24


def backend_name() -> str:
    """Which OS keychain is in use (for the log; "fail.Keyring" means none)."""
    backend = keyring.get_keyring()
    return f"{type(backend).__module__}.{type(backend).__name__}"


def load(name: str) -> str | None:
    try:
        value = keyring.get_password(SERVICE, name)
        if value is None:
            value = keyring.get_password(LEGACY_SERVICE, name)
            if value is not None:
                save(name, value)
        return value
    except KeyringError:
        return None


def save(name: str, value: str) -> bool:
    """Returns False when no keychain is available; the caller should say so."""
    try:
        keyring.set_password(SERVICE, name, value)
        return True
    except KeyringError:
        return False


def forget(name: str) -> None:
    for service in (SERVICE, LEGACY_SERVICE):  # Otherwise load() would bring the old copy back.
        try:
            keyring.delete_password(service, name)
        except KeyringError:
            pass


def save_long(name: str, value: str) -> bool:
    """Like save(), for secrets too long for one entry (a browser cookie): ``name`` holds the number of
    parts, ``name-0``, ``name-1``… hold the text."""
    parts = [value[at:at + PART_CHARS] for at in range(0, len(value), PART_CHARS)]
    if not parts or len(parts) > MAX_PARTS:
        return False
    forget_long(name)
    if not all(save(f"{name}-{index}", part) for index, part in enumerate(parts)):
        forget_long(name)
        return False
    return save(name, str(len(parts)))


def load_long(name: str) -> str | None:
    count = load(name)
    if count is None or not count.isdigit() or not 0 < int(count) <= MAX_PARTS:
        return None
    parts = [load(f"{name}-{index}") for index in range(int(count))]
    return "".join(parts) if all(part is not None for part in parts) else None


def forget_long(name: str) -> None:
    count = load(name)
    for index in range(int(count) if count and count.isdigit() else 0):
        if index < MAX_PARTS:
            forget(f"{name}-{index}")
    forget(name)
