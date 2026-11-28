"""Desktop client for the Ignite backend (see ``ignite_live.server``)."""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass

from ignite_live.core.http import Transport, TransportError, urllib_transport
from ignite_live.integrations.streamlabs import StreamCredentials
from ignite_live.music.youtube import Track

log = logging.getLogger("ignite")

# A read that can't get through is tried again after these waits (seconds); writes are never repeated.
RETRY_DELAYS = (0.5, 1.0)


class BackendError(Exception):
    """A safe-to-display backend error (never includes the device token)."""


class BackendUnreachable(BackendError):
    """The server didn't answer (offline, DNS, TLS): worth trying again."""


@dataclass(frozen=True)
class CachedKey:
    credentials: StreamCredentials
    expires_at: float


LAN_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal")


def server_url_allowed(url: str) -> bool:
    """https:// anywhere; plain http:// only on this PC or the local network (a self-hosted LAN server)."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme == "https" and parts.hostname:
        return True
    if parts.scheme != "http" or not parts.hostname:
        return False
    host = parts.hostname.lower()
    if host == "localhost" or host.endswith(LAN_SUFFIXES):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def register_device(base_url: str, name: str = "", transport: Transport = urllib_transport) -> str:
    """Hosted widgets on a server that makes accounts by itself: register this PC; returns its device token."""
    base_url = base_url.strip().rstrip("/")
    if not server_url_allowed(base_url):
        raise BackendError("Use an https:// server URL (plain http only works on this PC or your local network).")
    try:
        status, raw = transport("POST", base_url + "/v1/register",
                                {"Content-Type": "application/json", "Accept": "application/json"},
                                json.dumps({"name": name[:28]}).encode())
    except TransportError as error:
        raise BackendError("Could not reach the Ignite server.") from error
    try:
        payload = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    if status == 404:
        raise BackendError("This server doesn't host widgets (it's an older or plain Ignite server).")
    if status >= 400 or not isinstance(payload.get("token"), str):
        raise BackendError(str(payload.get("error") or f"The server returned HTTP {status}."))
    return payload["token"]


# Signing in (Ignite Web servers): like signing in a TV. Ignite asks for a code, opens the website's /link page
# and shows the code; the streamer logs in there (password or Discord) and approves; Ignite gets its own token.


@dataclass(frozen=True)
class SignInCode:
    device_code: str  # Ignite's secret for asking whether it's approved yet: never shown.
    user_code: str  # Shown in Ignite, and on the website, so the streamer can check they match.
    verification_uri: str
    verification_uri_complete: str  # /link?code=…: opens with the code filled in.
    expires_in: float
    interval: float


def device_name() -> str:
    """What the website lists this Ignite as."""
    try:
        return socket.gethostname().split(".")[0][:60] or "Ignite"
    except OSError:
        return "Ignite"


def device_platform() -> str:
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux" if sys.platform.startswith("linux") else sys.platform)


def _post(base_url: str, path: str, body: dict, transport: Transport) -> tuple[int, dict]:
    base_url = base_url.strip().rstrip("/")
    if not server_url_allowed(base_url):
        raise BackendError("Use an https:// server URL (plain http only works on this PC or your local network).")
    try:
        status, raw = transport("POST", base_url + path, {"Content-Type": "application/json",
                                                          "Accept": "application/json"}, json.dumps(body).encode())
    except TransportError as error:
        raise BackendUnreachable("Could not reach the Ignite server.") from error
    try:
        payload = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}
    return status, payload if isinstance(payload, dict) else {}


def start_sign_in(base_url: str, name: str = "", platform: str = "",
                  transport: Transport = urllib_transport) -> SignInCode:
    status, payload = _post(base_url, "/v1/auth/device",
                            {"name": name or device_name(), "platform": platform or device_platform()}, transport)
    if status == 404:
        raise BackendError("This server doesn't have sign-in (it's a plain Ignite server: use a device token).")
    if status >= 400 or not isinstance(payload.get("device_code"), str):
        raise BackendError(str(payload.get("message") or payload.get("error") or f"The server returned HTTP {status}."))
    try:
        return SignInCode(payload["device_code"], str(payload["user_code"]), str(payload["verification_uri"]),
                          str(payload["verification_uri_complete"]), float(payload.get("expires_in", 600)),
                          max(2.0, float(payload.get("interval", 5))))
    except (KeyError, TypeError, ValueError) as error:
        raise BackendError("The server's sign-in answer didn't make sense.") from error


def poll_sign_in(base_url: str, device_code: str, transport: Transport = urllib_transport) -> tuple[str, str] | None:
    """(token, username) once approved; None while waiting. Raises BackendError when refused or expired."""
    status, payload = _post(base_url, "/v1/auth/token", {"device_code": device_code}, transport)
    if status == 200 and isinstance(payload.get("token"), str):
        return payload["token"], str(payload.get("username", ""))
    if payload.get("error") in ("authorization_pending", "slow_down"):
        return None
    if payload.get("error") == "access_denied":
        raise BackendError("The sign-in was refused on the website.")
    if payload.get("error") == "expired_token":
        raise BackendError("The sign-in code expired before it was approved. Click Sign In again.")
    raise BackendError(str(payload.get("message") or f"The server returned HTTP {status}."))


def wait_for_sign_in(base_url: str, code: SignInCode, cancelled: threading.Event,
                     transport: Transport = urllib_transport) -> tuple[str, str] | None:
    """Blocking: polls until approved (token, username), cancelled (None), or refused/expired (BackendError)."""
    waited = 0.0
    while waited < code.expires_in:
        if cancelled.wait(code.interval):
            return None
        waited += code.interval
        try:
            found = poll_sign_in(base_url, code.device_code, transport)
        except BackendUnreachable:  # A dropped connection mid-wait: keep trying until the code expires.
            continue
        if found is not None:
            return found
    raise BackendError("The sign-in code expired before it was approved. Click Sign In again.")


class BackendClient:
    def __init__(self, base_url: str, device_token: str, transport: Transport = urllib_transport) -> None:
        base_url = base_url.strip().rstrip("/")
        if not server_url_allowed(base_url):
            raise BackendError("Use an https:// server URL (plain http only works on this PC or your local network).")
        if not device_token.strip():
            raise BackendError("Enter the device token from ignite-server add-user.")
        self._base = base_url
        self._token = device_token.strip()
        self._transport = transport
        self._sleep = time.sleep  # Tests make the waits instant.

    # A server can choose not to keep stream keys (HTTP 410): then they stay in this PC's keychain, as without one.

    def cached_key(self) -> CachedKey | None:
        status, payload = self._call("GET", "/v1/stream-key")
        if status == 410:
            from ignite_live.core.key_cache import LocalKeyCache  # (It imports this module.)

            return LocalKeyCache().cached_key()
        if status == 404:
            return None
        return self._cached(payload)

    def store_key(self, credentials: StreamCredentials) -> CachedKey:
        status, payload = self._call(
            "PUT",
            "/v1/stream-key",
            {"server": credentials.server, "key": credentials.key, "session_id": credentials.session_id},
        )
        if status == 410:
            from ignite_live.core.key_cache import LocalKeyCache

            return LocalKeyCache().store_key(credentials)
        return self._cached(payload)

    def mark_key_used(self) -> None:
        status, _ = self._call("POST", "/v1/stream-key/used")
        if status == 410:
            from ignite_live.core.key_cache import LocalKeyCache

            LocalKeyCache().mark_key_used()

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

    # Hosted widgets (Ignite Web servers).

    def widgets(self) -> dict:
        """{"base": https://server/w/<key>, "api": wss://…/api, "has_control_key": bool, "pages": {…}}."""
        return self._hosted("GET", "/v1/widgets")

    def new_widget_links(self) -> dict:
        return self._hosted("POST", "/v1/widgets/new-links")

    def new_control_key(self) -> dict:
        """The hosted API's control key, shown once: {"control_key": "ctl_…", …links}."""
        return self._hosted("POST", "/v1/widgets/control-key")

    def login_link(self) -> str:
        """A one-time link that opens this account on the server's website, signed in."""
        return str(self._hosted("POST", "/v1/login-link").get("url", ""))

    def me(self) -> dict:
        """Who this Ignite is signed in as: {"username", "site", "device": {"id", "name"} | None}."""
        status, payload = self._call("GET", "/v1/me")
        if status == 404:
            raise BackendError("This server doesn't have sign-in (it's a plain Ignite server).")
        return payload

    def sign_out(self) -> None:
        """End this Ignite's sign-in on the server: its token stops working everywhere."""
        self._call("POST", "/v1/auth/sign-out", {})

    def tiktok_status(self) -> dict:
        """{"available", "connected", "username", "sign_in_url"}: whether the streamer signed TikTok in on the website."""
        status, payload = self._call("GET", "/v1/tiktok/account")
        return {"available": False, "connected": False} if status == 404 else payload

    def tiktok_action(self, action: str, user_id: str = "", text: str = "", duration: int = 300) -> None:
        """Act as the streamer in their TikTok LIVE: chat, mod, unmod, mute, unmute, kick or unkick."""
        status, _payload = self._call("POST", "/v1/tiktok/action",
                                      {"action": action, "user_id": user_id, "text": text, "duration": duration})
        if status == 404:
            raise BackendError("Your Ignite server can't act on TikTok yet (it needs updating).")

    def _hosted(self, method: str, path: str) -> dict:
        status, payload = self._call(method, path, {} if method == "POST" else None)
        if status == 404:
            raise BackendError("This server doesn't host widgets (it's an older or plain Ignite server).")
        return payload

    def _call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        delays = iter(RETRY_DELAYS if method == "GET" else ())  # Only reads: a repeated write could happen twice.
        while True:
            try:
                status, raw = self._transport(method, self._base + path, headers, data)
                break
            except TransportError as error:
                wait = next(delays, None)
                if wait is None:
                    log.warning("Ignite server unreachable: %s %s (%s)", method, path, error)
                    raise BackendUnreachable("Could not reach the Ignite server. Check your connection; Ignite "
                                             "keeps trying.") from error
                log.info("Ignite server did not answer %s %s (%s); trying again in %.1f s", method, path, error, wait)
                self._sleep(wait)
        try:
            payload = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}
        if status == 401 and isinstance(payload, dict) and "signed_out" in payload:
            raise BackendError(str(payload.get("error") or "Sign in to TikTok again on the website."))
        if status == 401:
            raise BackendError("The backend rejected this device token.")
        if status >= 500:
            log.warning("Ignite server error: %s %s -> HTTP %s", method, path, status)
        if status >= 400 and status not in (404, 410):
            raise BackendError(str(payload.get("error") or f"Backend returned HTTP {status}."))
        return status, payload if isinstance(payload, dict) else {}
