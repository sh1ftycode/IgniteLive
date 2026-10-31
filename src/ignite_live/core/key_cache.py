"""Stream-key cache on this device, for when no backend is configured.

Same interface as ``BackendClient`` (``cached_key``, ``store_key``,
``mark_key_used``), so the app can use either.
"""

from __future__ import annotations

import json
import time
from typing import Callable

from ignite_live.core import keychain
from ignite_live.integrations.backend import CachedKey
from ignite_live.integrations.streamlabs import StreamCredentials

KEY_TTL_S = 48 * 3600


class LocalKeyCache:
    def __init__(self, ttl_s: float = KEY_TTL_S, clock: Callable[[], float] = time.time) -> None:
        self._ttl_s = ttl_s
        self._clock = clock

    def cached_key(self) -> CachedKey | None:
        raw = keychain.load(keychain.STREAM_KEY)
        try:
            data = json.loads(raw) if raw else None
            if not data or data["expires_at"] <= self._clock():
                return None
            credentials = StreamCredentials(data["server"], data["key"], "Saved key (this device)", data.get("session_id"))
            return CachedKey(credentials, float(data["expires_at"]))
        except (ValueError, KeyError, TypeError):
            return None

    def store_key(self, credentials: StreamCredentials) -> CachedKey:
        expires_at = self._clock() + self._ttl_s
        keychain.save(
            keychain.STREAM_KEY,
            json.dumps(
                {
                    "server": credentials.server,
                    "key": credentials.key,
                    "session_id": credentials.session_id,
                    "expires_at": expires_at,
                }
            ),
        )
        return CachedKey(credentials, expires_at)

    def mark_key_used(self) -> None:
        keychain.forget(keychain.STREAM_KEY)
