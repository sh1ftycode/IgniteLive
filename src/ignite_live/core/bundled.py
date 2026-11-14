"""API keys baked into a build (packaging/build.py --bundle-keys), and the main server's address.

On start, each bundled key is copied into this computer's keychain unless a
key is already saved there. Only API keys are bundled; passwords and sign-ins
(OBS, Streamlabs, server token, Discord webhook) never are.

The main Ignite server (hosted widget links) isn't in the source code either:
builds get it from packaging/bundled_config.json (kept out of git; the release
workflow writes it from the IGNITE_MAIN_SERVER secret), and running from source
reads $IGNITE_MAIN_SERVER.
"""

from __future__ import annotations

import json
import os
from importlib import resources

from ignite_live.core import keychain

BUNDLABLE = (keychain.YOUTUBE_API_KEY, keychain.SIGN_API_KEY)
FILE = "bundled_keys.json"
CONFIG_FILE = "bundled_config.json"


def main_server(source=None) -> str:
    """The Ignite server Hosted links uses when none is set in Preferences → Server ("" if this build has none)."""
    if os.environ.get("IGNITE_MAIN_SERVER"):
        return os.environ["IGNITE_MAIN_SERVER"].strip().rstrip("/")
    try:
        source = source if source is not None else resources.files("ignite_live").joinpath(CONFIG_FILE)
        data = json.loads(source.read_text("utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return ""
    value = data.get("main_server") if isinstance(data, dict) else None
    return value.strip().rstrip("/") if isinstance(value, str) else ""


def import_bundled_keys(source=None) -> list[str]:
    """Names of the keys copied into the keychain (values are never logged).

    ``source`` is the bundled file; it defaults to the one packaged with the app.
    """
    try:
        source = source if source is not None else resources.files("ignite_live").joinpath(FILE)
        data = json.loads(source.read_text("utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return []
    imported = []
    for name in BUNDLABLE:
        value = data.get(name) if isinstance(data, dict) else None
        if isinstance(value, str) and value.strip() and not keychain.load(name):
            if keychain.save(name, value.strip()):
                imported.append(name)
    return imported
