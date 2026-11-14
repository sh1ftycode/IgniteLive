"""Remote control through your Ignite server: the web dashboard, from any browser.

The desktop app connects *out* to the server you set in Preferences → Server
(the same URL and device token as the rest of the server features), so nothing
on your PC is reachable from the internet and no port is forwarded. Over that
one WebSocket it sends what the dashboard shows, and runs the commands the
dashboard sends back, but only the ones listed in COMMANDS.

What it sends: live state, connection states, OBS health and scene names, the
music queue, chat lines (the same sanitised form the chat overlay gets), and
notices. Never stream keys, tokens, passwords, or file paths.

Music: while no Now Playing overlay is open, a dashboard tab that allows it
plays the songs instead ("music" says whether one is needed). That tab reports
back like the overlay does: "player" messages (ended, error, playing,
progress), checked by PLAYER_EVENTS.

Hosted widgets (API tab → Hosted links): the same connection also carries
what the local overlay server broadcasts ("overlay") and the local API's
messages ("api"), so the server can serve the overlays and the API to any
streaming app. Those are live only: never queued while disconnected (an alert
shown minutes late is worse than none); the latest state is sent again on
reconnecting. The server asks for alert sounds and game pictures by name
("fetch"); they come back in small pieces ("file").

Commands run only while remote control is on (Preferences → Server).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
from typing import Awaitable, Callable

import aiohttp

from ignite_live import __version__
from ignite_live.core.events import ConnectionState
from ignite_live.core.http import tls_session
from ignite_live.overlay import api

log = logging.getLogger(__name__)

PROTOCOL = 1
# What the server may ask for (the dashboard, and the hosted API with its control key): the local API's commands.
COMMANDS: dict[str, tuple[str, ...]] = {name: command.args for name, command in api.COMMANDS.items()}
FILE_CHUNK = 32 * 1024  # A fetched file goes in pieces this size (before base64), so each message stays small.
FILE_MAX = 2 * 1024 * 1024
# The latest of each is kept and re-sent after a reconnect; chat lines and notices are sent once, as they happen.
STATE_KINDS = ("live", "panic", "status", "studio", "queue", "playlist", "tts_muted", "music")
# What a dashboard tab that plays the music may report.
PLAYER_EVENTS = ("ended", "error", "playing", "progress")
MAX_PENDING = 200  # Chat lines and notices held while disconnected (the oldest go first).
MAX_MESSAGE = 64 * 1024


def player_report(data: object) -> dict | None:
    """A dashboard player's report, reduced to well-formed fields (None: not a report)."""
    if not isinstance(data, dict) or data.get("event") not in PLAYER_EVENTS:
        return None
    item = data.get("item_id")
    if not isinstance(item, int) or isinstance(item, bool):
        return None
    report: dict = {"type": data["event"], "item_id": item}
    if data["event"] == "error":
        code = data.get("code")
        report["code"] = code if isinstance(code, int) and not isinstance(code, bool) else 0
    if data["event"] == "progress":
        for key in ("position", "duration"):
            value = data.get(key)
            report[key] = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else 0.0
        report["playing"] = data.get("playing") is True
    return report


def relay_url(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base.removeprefix("https://") + "/v1/relay"
    return "ws://" + base.removeprefix("http://") + "/v1/relay"


class RelayClient:
    def __init__(
        self,
        base_url: str,
        device_token: str,
        on_command: Callable[[str, dict], Awaitable[str]],
        on_status: Callable[[ConnectionState, str], None],
        session_factory: Callable[[], aiohttp.ClientSession] = tls_session,
        on_player: Callable[[dict], Awaitable[None]] | None = None,
        allow_commands: Callable[[], bool] = lambda: True,
        snapshot: Callable[[], list[dict]] | None = None,
        on_fetch: Callable[[str, str], tuple[bytes, str] | None] | None = None,
        local: Callable[[], dict | None] | None = None,
    ) -> None:
        """``on_command(name, args)`` runs an allowed command and returns a short result for the dashboard
        (raise ValueError for a refusal). ``on_player(report)`` gets a dashboard player's report (player_report)."""
        from ignite_live.integrations.backend import server_url_allowed

        if not server_url_allowed(base_url):
            raise ValueError("Use an https:// server URL (plain http only works on this PC or your local network).")
        self.url = relay_url(base_url)
        self._token = device_token
        self._on_command = on_command
        self._on_status = on_status
        self._session_factory = session_factory
        self._on_player = on_player
        self._allow_commands = allow_commands  # Remote control on: else the server only gets to watch.
        self._snapshot = snapshot  # Hosted widgets: what to send again after (re)connecting.
        self._on_fetch = on_fetch  # (kind "sound" | "image", name) -> (bytes, content type), or None.
        self._local = local  # Hosted widgets: where their pages fall back to on this PC.
        self._state: dict[str, object] = {}
        self._pending: list[dict] = []
        self._ws: aiohttp.ClientWebSocketResponse | None = None

    # Called on the event loop by the controller.

    def publish(self, kind: str, data: object) -> None:
        message = {"type": "event", "kind": kind, "data": data}
        if kind in STATE_KINDS:
            self._state[kind] = data
        elif self._ws is None:
            self._pending = (self._pending + [message])[-MAX_PENDING:]
            return
        if self._ws is not None and not self._ws.closed:
            asyncio.get_running_loop().create_task(self._send(message))

    def send_live(self, kind: str, message: dict) -> None:
        """Hosted widgets: an overlay ("overlay") or API ("api") message, now or never."""
        if self._ws is not None and not self._ws.closed:
            asyncio.get_running_loop().create_task(self._send({"type": kind, "message": message}))

    async def run(self) -> None:
        delay = 1.0
        while True:
            connected = False
            try:
                async with self._session_factory() as session:
                    self._on_status(ConnectionState.CONNECTING, "Connecting to your Ignite server…")
                    async with session.ws_connect(
                        self.url, headers={"Authorization": f"Bearer {self._token}"}, heartbeat=25,
                        max_msg_size=MAX_MESSAGE, timeout=aiohttp.ClientWSTimeout(ws_close=10),
                    ) as ws:
                        self._ws = ws
                        connected = True
                        delay = 1.0
                        await self._hello(ws)
                        await self._listen(ws)
            except asyncio.CancelledError:
                raise
            except aiohttp.WSServerHandshakeError as error:
                if error.status in (401, 403):
                    self._on_status(ConnectionState.ERROR, "The server didn't accept this device token.")
                    return  # Won't get better by retrying; Preferences → Server restarts it.
                if error.status == 404:
                    self._on_status(ConnectionState.ERROR, "This server has no web dashboard (it's an older Ignite server).")
                    return
                log.info("Remote control: handshake failed: %s", error)
            except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as error:
                log.info("Remote control: connection lost: %s", error)
            finally:
                self._ws = None
            delay = 1.0 if connected else min(delay * 2, 60.0)
            wait = delay * random.uniform(0.8, 1.2)
            self._on_status(ConnectionState.DEGRADED, f"Can't reach your Ignite server. Retrying in {int(wait) or 1}s.")
            await asyncio.sleep(wait)

    async def _hello(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        hello = {"type": "hello", "protocol": PROTOCOL, "version": __version__, "commands": sorted(COMMANDS)}
        local = self._local() if self._local else None
        if local:
            hello["local"] = local
        await ws.send_json(hello)
        for kind, data in self._state.items():
            await ws.send_json({"type": "event", "kind": kind, "data": data})
        pending, self._pending = self._pending, []
        for message in pending:
            await ws.send_json(message)
        for kind, message in (self._snapshot() if self._snapshot else []):
            await ws.send_json({"type": kind, "message": message})
        self._on_status(ConnectionState.CONNECTED, "Connected to your Ignite server")

    async def _listen(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for message in ws:
            if message.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                data = json.loads(message.data)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("type") == "command":
                await self._command(data)
            elif isinstance(data, dict) and data.get("type") == "fetch":
                asyncio.get_running_loop().create_task(self._fetch(data))
            elif isinstance(data, dict) and data.get("type") == "player" and self._on_player is not None:
                report = player_report(data)
                if report is not None:
                    try:
                        await self._on_player(report)
                    except Exception:  # noqa: BLE001 - a bad report mustn't drop the connection
                        log.exception("Remote control: player report failed")

    async def _command(self, data: dict) -> None:
        name, request_id = str(data.get("name", "")), data.get("id")
        raw = data.get("args") if isinstance(data.get("args"), dict) else {}
        reply = {"type": "result", "id": request_id, "name": name}
        if name not in COMMANDS:
            await self._send({**reply, "ok": False, "message": "Ignite doesn't take that command remotely."})
            return
        if not self._allow_commands():
            await self._send({**reply, "ok": False, "message": "Remote control is off on this PC (Ignite → "
                                                                "Preferences → Server)."})
            return
        args = {key: raw[key] for key in COMMANDS[name] if key in raw}
        try:
            text = await self._on_command(name, args)
            await self._send({**reply, "ok": True, "message": text})
        except ValueError as error:
            await self._send({**reply, "ok": False, "message": str(error)})
        except Exception:  # noqa: BLE001 - one bad command mustn't drop the connection
            log.exception("Remote control: %s failed", name)
            await self._send({**reply, "ok": False, "message": "Something went wrong. Check Ignite's log."})

    async def _fetch(self, data: dict) -> None:
        """A sound or picture a hosted widget asked for, by name only (the PC decides what that name means)."""
        fetch_id, kind, name = data.get("id"), str(data.get("kind", "")), str(data.get("name", ""))
        found = None
        if self._on_fetch is not None and kind in ("sound", "image"):
            try:
                found = await asyncio.to_thread(self._on_fetch, kind, name)
            except OSError:
                found = None
        if found is None or len(found[0]) > FILE_MAX:
            await self._send({"type": "file", "id": fetch_id, "ok": False})
            return
        body, content_type = found
        pieces = [body[i:i + FILE_CHUNK] for i in range(0, len(body), FILE_CHUNK)] or [b""]
        for index, piece in enumerate(pieces):
            await self._send({"type": "file", "id": fetch_id, "ok": True, "content_type": content_type,
                              "data": base64.b64encode(piece).decode(), "last": index == len(pieces) - 1})

    async def _send(self, message: dict) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            return
        try:
            await ws.send_json(message)
        except (ConnectionError, RuntimeError, aiohttp.ClientError):
            pass  # The connection is going; run() reconnects and re-sends the state.
