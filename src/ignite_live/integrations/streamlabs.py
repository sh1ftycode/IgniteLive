"""Credential providers for the stream setup screen.

The Streamlabs routes used here are undocumented and may change without notice.
Keep this adapter isolated so manual credentials remain usable if they fail.
"""

from __future__ import annotations

import json
import base64
import hashlib
import http.server
import logging
import secrets
import socket
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass
from typing import Callable

from ignite_live.core.http import Transport, TransportError, urllib_transport


log = logging.getLogger(__name__)


class ProviderError(Exception):
    """A safe-to-display provider error that never includes credentials."""


@dataclass(frozen=True)
class StreamCredentials:
    server: str
    key: str
    source: str
    session_id: str | None = None


@dataclass(frozen=True)
class AccountInfo:
    username: str
    application_status: str
    can_be_live: bool


def _http_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, bytes]:
    try:
        return urllib_transport(method, url, headers, body)
    except TransportError as error:
        raise ProviderError("Could not reach Streamlabs. Check your connection.") from error


class StreamlabsExperimentalProvider:
    """Opt-in adapter for the undocumented routes used by the reference app."""

    base_url = "https://streamlabs.com/api/v5/slobs/tiktok"
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) StreamlabsDesktop/1.17.0 Chrome/122.0.6261.156 "
        "Electron/29.3.1 Safari/537.36"
    )

    def __init__(self, token: str, transport: Transport = _http_transport) -> None:
        if not token or not token.strip():
            raise ProviderError("Enter a Streamlabs access token first.")
        self._token = token.strip()
        self._transport = transport

    def account_info(self) -> AccountInfo:
        """Whether this account may go LIVE through Streamlabs at all."""
        payload = self._request("GET", f"{self.base_url}/info")
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        status = payload.get("application_status")
        status = status if isinstance(status, dict) else {}
        return AccountInfo(
            username=str(user.get("username") or "Unknown"),
            application_status=str(status.get("status") or "Unknown"),
            can_be_live=payload.get("can_be_live") is True,
        )

    def categories(self, query: str = "") -> list[dict[str, str]]:
        url = f"{self.base_url}/info"
        if query.strip():
            url += "?" + urllib.parse.urlencode({"category": query.strip()[:25]})
        payload = self._request("GET", url)
        categories = payload.get("categories", [])
        return [
            {
                "name": str(item.get("full_name", "")),
                "id": str(item.get("game_mask_id", "")),
            }
            for item in categories
            if isinstance(item, dict) and item.get("full_name")
        ]

    def generate(
        self, title: str, category_id: str = "", audience_type: str = "0"
    ) -> StreamCredentials:
        if not title.strip():
            raise ProviderError("Add a stream title before generating credentials.")
        boundary = "----Ignite" + secrets.token_hex(12)
        values = {
            "title": title.strip(),
            "device_platform": "win32",
            "category": category_id,
            "audience_type": audience_type,
        }
        body = b"".join(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
            for name, value in values.items()
        ) + f"--{boundary}--\r\n".encode("ascii")
        payload = self._request(
            "POST",
            f"{self.base_url}/stream/start",
            body,
            {"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        server = payload.get("rtmp")
        key = payload.get("key")
        if not isinstance(server, str) or not isinstance(key, str) or not server or not key:
            raise ProviderError("Streamlabs did not return a server and key.")
        session_id = payload.get("id")
        return StreamCredentials(
            server=server,
            key=key,
            source="Streamlabs (experimental)",
            session_id=str(session_id) if session_id else None,
        )

    def end(self, session_id: str) -> bool:
        if not session_id:
            raise ProviderError("There is no Streamlabs session to end.")
        payload = self._request("POST", f"{self.base_url}/stream/{urllib.parse.quote(session_id, safe='')}/end")
        if payload.get("success") is not True:
            raise ProviderError("Streamlabs did not confirm that the LIVE session ended.")
        return True

    def _request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": self.user_agent,
        }
        headers.update(extra_headers or {})
        status, response_body = self._transport(method, url, headers, body)
        if status < 200 or status >= 300:
            if status in (401, 403):
                raise ProviderError("Streamlabs rejected the access token or denied access.")
            if status == 429:
                raise ProviderError("Streamlabs rate limit reached. Try again later.")
            raise ProviderError(f"Streamlabs returned HTTP {status}.")
        try:
            payload = json.loads(response_body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ProviderError("Streamlabs returned an unreadable response.") from error
        if not isinstance(payload, dict):
            raise ProviderError("Streamlabs returned an unexpected response.")
        return payload


class StreamlabsTokenRetriever:
    """Browser-based PKCE login for Streamlabs' undocumented desktop flow."""

    auth_url = "https://streamlabs.com/slobs/login"
    token_url = "https://streamlabs.com/api/v5/slobs/auth/data"
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) StreamlabsDesktop/1.20.4 Chrome/122.0.6261.156 "
        "Electron/29.3.1 Safari/537.36"
    )

    def __init__(self, transport: Transport = _http_transport) -> None:
        self._transport = transport
        self.code_verifier = secrets.token_hex(64)
        digest = hashlib.sha256(self.code_verifier.encode("ascii")).digest()
        self.code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    @staticmethod
    def _callback_servers() -> tuple[list[http.server.HTTPServer], "_CallbackState"]:
        """Local servers Streamlabs redirects the browser to, on IPv4 and (if available) IPv6."""
        state = _CallbackState()

        class CallbackHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if "code" not in params and "success" not in params:
                    # e.g. the browser's /favicon.ico request: must not touch the result.
                    self.send_error(404)
                    return
                code = params.get("code", [""])[0]
                accepted = state.accept(code if params.get("success", [""])[0] == "true" else "")
                log.info("Streamlabs login: callback received (%s)", "with a code" if accepted else "without a code")
                body = (
                    b"<h2>Signed in. You can close this tab and go back to Ignite.</h2>"
                    if accepted
                    else b"<h2>Streamlabs didn't sign you in. Go back to Ignite and try again.</h2>"
                )
                self.send_response(200 if accepted else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args) -> None:
                return

        class Server4(http.server.ThreadingHTTPServer):
            allow_reuse_address = True

        class Server6(Server4):
            address_family = socket.AF_INET6

        servers: list[http.server.HTTPServer] = [Server4(("127.0.0.1", 0), CallbackHandler)]
        try:
            # The browser may resolve "localhost" to ::1 first.
            servers.append(Server6(("::1", servers[0].server_address[1]), CallbackHandler))
        except OSError:
            pass
        return servers, state

    def login_url(self, port: int) -> str:
        # Bare "tiktok" / "force_verify" flags, exactly as Streamlabs Desktop sends them.
        return (
            f"{self.auth_url}?skip_splash=true&external=electron&tiktok&force_verify&origin=slobs"
            f"&port={port}&code_challenge={self.code_challenge}&code_flow=true"
        )

    def retrieve_token(self, timeout: int = 300, open_url: Callable[[str], bool] = webbrowser.open) -> str:
        """Open the login page, wait for Streamlabs to redirect back, and exchange the code.

        ``open_url`` returns whether a browser opened; a terminal caller can print the link too.
        """
        servers, state = self._callback_servers()
        port = servers[0].server_address[1]
        threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
        for thread in threads:
            thread.start()
        log.info("Streamlabs login: waiting for the browser on port %s (IPv6: %s)", port, len(servers) > 1)
        try:
            if not open_url(self.login_url(port)):
                raise ProviderError("Could not open a browser. Try: ignite-streamlabs-login")
            if not state.done.wait(timeout=timeout):
                raise ProviderError("Streamlabs login timed out after 5 minutes. Try again.")
            if not state.code:
                raise ProviderError("Streamlabs didn't sign you in (no authorization code came back).")
            return self._exchange_code(state.code)
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()

    def _exchange_code(self, code: str) -> str:
        query = urllib.parse.urlencode(
            {"code_verifier": self.code_verifier, "code": code}
        )
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Accept-Language": "en-US",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }
        status, response_body = self._transport(
            "GET", f"{self.token_url}?{query}", headers, None
        )
        if status != 200:
            log.warning("Streamlabs login: token exchange returned HTTP %s", status)
            raise ProviderError(f"Streamlabs token exchange failed with HTTP {status}.")
        try:
            payload = json.loads(response_body)
            token = payload.get("data", {}).get("oauth_token")
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError) as error:
            raise ProviderError("Streamlabs returned an unreadable login response.") from error
        if payload.get("success") is not True or not isinstance(token, str) or not token:
            log.warning("Streamlabs login: exchange answered without a token (keys: %s)", sorted(payload))
            raise ProviderError("Streamlabs did not return an access token.")
        log.info("Streamlabs login: got a token")
        return token


class _CallbackState:
    """First real callback wins; later requests can't overwrite it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.code = ""
        self.done = threading.Event()

    def accept(self, code: str) -> bool:
        with self._lock:
            if not self.done.is_set():
                self.code = code
                self.done.set()
            return bool(self.code)


def manual_credentials(server: str, key: str) -> StreamCredentials:
    server = server.strip()
    key = key.strip()
    if not server or not key:
        raise ProviderError("Enter both the RTMP server and stream key.")
    if not server.startswith(("rtmp://", "rtmps://")):
        raise ProviderError("The server must start with rtmp:// or rtmps://.")
    return StreamCredentials(server=server, key=key, source="Manual")