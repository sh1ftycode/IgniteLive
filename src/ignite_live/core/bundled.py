"""API keys baked into a build (packaging/build.py --bundle-keys).

On start, each bundled key is copied into this computer's keychain unless a
key is already saved there. Only API keys are bundled; passwords and sign-ins
(OBS, Streamlabs, server token, Discord webhook) never are.
"""

from __future__ import annotations

import json
from importlib import resources

from ignite_live.core import keychain

BUNDLABLE = (keychain.YOUTUBE_API_KEY, keychain.SIGN_API_KEY)
FILE = "bundled_keys.json"


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
