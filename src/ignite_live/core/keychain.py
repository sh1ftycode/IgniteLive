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
