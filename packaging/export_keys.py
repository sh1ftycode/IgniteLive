"""Copy your API keys from this computer's keychain into packaging/bundled_keys.json,
which the next build bundles into the app (build.py --bundle-keys does this for you).

On first start, the built app copies them into that computer's keychain, but never
over keys that are already there. Only API keys are bundled, never passwords or sign-ins.

Anyone who has a build made this way can extract these keys. Keep it to yourself
and people you trust, and restrict your YouTube key in Google Cloud to the
YouTube Data API v3.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ignite_live.core import keychain  # noqa: E402
from ignite_live.core.bundled import BUNDLABLE  # noqa: E402

OUTPUT = Path(__file__).resolve().parent / "bundled_keys.json"


def export(output: Path = OUTPUT) -> list[str]:
    keys = {name: value for name in BUNDLABLE if (value := keychain.load(name))}
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(keys, handle)
    return sorted(keys)


if __name__ == "__main__":
    names = export()
    print(f"Bundling: {', '.join(names) if names else 'nothing (no keys saved in this keychain)'}")
