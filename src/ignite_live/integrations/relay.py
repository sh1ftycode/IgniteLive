"""Remote control through your Ignite server: the web dashboard, from any browser.

The desktop app connects *out* to the server you set in Preferences → Server
(the same URL and device token as the rest of the server features), so nothing
on your PC is reachable from the internet and no port is forwarded. Over that
one WebSocket it sends what the dashboard shows, and runs the commands the
dashboard sends back, but only the ones listed in COMMANDS.

What it sends: live state, connection states, OBS health and scene names, the
music queue, chat lines (the same sanitised form the chat overlay gets), and
notices. Never stream keys, tokens, passwords, or file paths.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Awaitable, Callable

import aiohttp

from ignite_live import __version__
from ignite_live.core.events import ConnectionState

log = logging.getLogger(__name__)

PROTOCOL = 1
# What the dashboard may ask for, and the arguments each takes.
COMMANDS: dict[str, tuple[str, ...]] = {
    "toggle_stream": (),
    "panic": (),
    "resume": (),
    "toggle_record": (),
    "save_replay": (),
    "switch_scene": ("name",),
    "skip_song": (),
    "remove_song": ("item_id",),
    "request_song": ("text",),
    "skip_tts": (),
    "mute_tts": ("muted",),
}
# The latest of each is kept and re-sent after a reconnect; chat lines and notices are sent once, as they happen.
STATE_KINDS = ("live", "panic", "status", "studio", "queue", "playlist", "tts_muted")
MAX_PENDING = 200  # Chat lines and notices held while disconnected (the oldest go first).
MAX_MESSAGE = 64 * 1024


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
        session_factory: Callable[[], aiohttp.ClientSession] = aiohttp.ClientSession,
    ) -> None:
        """``on_command(name, args)`` runs an allowed command and returns a short result for the dashboard
        (raise ValueError for a refusal)."""
        from ignite_live.integrations.backend import server_url_allowed

        if not server_url_allowed(base_url):
            raise ValueError("Use an https:// server URL (plain http only works on this PC or your local network).")
        self.url = relay_url(base_url)
        self._token = device_token
        self._on_command = on_command
        self._on_status = on_status
        self._session_factory = session_factory
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
        await ws.send_json({"type": "hello", "protocol": PROTOCOL, "version": __version__,
                            "commands": sorted(COMMANDS)})
        for kind, data in self._state.items():
            await ws.send_json({"type": "event", "kind": kind, "data": data})
        pending, self._pending = self._pending, []
        for message in pending:
            await ws.send_json(message)
        self._on_status(ConnectionState.CONNECTED, "The web dashboard can control Ignite")

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

    async def _command(self, data: dict) -> None:
        name, request_id = str(data.get("name", "")), data.get("id")
        raw = data.get("args") if isinstance(data.get("args"), dict) else {}
        reply = {"type": "result", "id": request_id, "name": name}
        if name not in COMMANDS:
            await self._send({**reply, "ok": False, "message": "Ignite doesn't take that command remotely."})
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

    async def _send(self, message: dict) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            return
        try:
            await ws.send_json(message)
        except (ConnectionError, RuntimeError, aiohttp.ClientError):
            pass  # The connection is going; run() reconnects and re-sends the state.
