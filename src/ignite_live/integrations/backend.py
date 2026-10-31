"""Desktop client for the Ignite backend (see ``ignite_live.server``)."""

from __future__ import annotations

import json
from dataclasses import dataclass

from ignite_live.core.http import Transport, TransportError, urllib_transport
from ignite_live.integrations.streamlabs import StreamCredentials
from ignite_live.music.youtube import Track


class BackendError(Exception):
    """A safe-to-display backend error (never includes the device token)."""


@dataclass(frozen=True)
class CachedKey:
    credentials: StreamCredentials
    expires_at: float


class BackendClient:
    def __init__(self, base_url: str, device_token: str, transport: Transport = urllib_transport) -> None:
        base_url = base_url.strip().rstrip("/")
        if not base_url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise BackendError("Use an https:// backend URL (plain http is only allowed for localhost).")
        if not device_token.strip():
            raise BackendError("Enter the device token from ignite-server add-user.")
        self._base = base_url
        self._token = device_token.strip()
        self._transport = transport

    def cached_key(self) -> CachedKey | None:
        status, payload = self._call("GET", "/v1/stream-key")
        if status == 404:
            return None
        return self._cached(payload)

    def store_key(self, credentials: StreamCredentials) -> CachedKey:
        _, payload = self._call(
            "PUT",
            "/v1/stream-key",
            {"server": credentials.server, "key": credentials.key, "session_id": credentials.session_id},
        )
        return self._cached(payload)

    def mark_key_used(self) -> None:
        self._call("POST", "/v1/stream-key/used")

    def resolve_song(self, request_text: str) -> Track:
        _, payload = self._call("POST", "/v1/music/resolve", {"request": request_text})
        track = payload.get("track") or {}
        return Track(**{name: track[name] for name in Track.__dataclass_fields__ if name in track})

    def settings(self) -> dict | None:
        status, payload = self._call("GET", "/v1/settings")
        return None if status == 404 else payload.get("settings")

    def save_settings(self, settings: dict) -> None:
        self._call("PUT", "/v1/settings", {"settings": settings})

    @staticmethod
    def _cached(payload: dict) -> CachedKey:
        return CachedKey(
            StreamCredentials(
                server=payload["server"],
                key=payload["key"],
                source="Saved key (backend)",
                session_id=payload.get("session_id"),
            ),
            float(payload["expires_at"]),
        )

    def _call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        try:
            status, raw = self._transport(method, self._base + path, headers, data)
        except TransportError as error:
            raise BackendError("Could not reach the Ignite backend.") from error
        try:
            payload = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}
        if status == 401:
            raise BackendError("The backend rejected this device token.")
        if status >= 400 and status != 404:
            raise BackendError(str(payload.get("error") or f"Backend returned HTTP {status}."))
        return status, payload if isinstance(payload, dict) else {}
