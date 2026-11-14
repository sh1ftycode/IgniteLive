"""Ignite's local API: everything Ignite does, for any overlay, widget, or streaming app.

Ignite's own overlays only work as Browser Sources; this lets anything use Ignite: a custom widget in OBS,
Streamlabs Desktop, TikTok LIVE Studio, Meld or vMix, a Stream Deck button, a bot. It runs on the overlay
server (this PC only, ``http://localhost:<port>/api/v1``) and speaks JSON. See docs/API.md.

Two keys:

- The **overlay token** (the ``token=`` in every overlay URL) reads: events and state.
- The **control key** (API tab) also runs commands: go live, scenes, clips, music, alerts…
  A widget that only shows chat never needs it, so a widget can't end your LIVE unless you give it this.

Either goes in the URL (``?token=…`` or ``?key=…``) or as ``Authorization: Bearer …``. Any web page may
connect (widgets are often served from somewhere else, or opened from a file), but the key is required and
the Host must be this PC, which keeps out other web pages and DNS rebinding.

WebSocket ``/api/v1/ws``: on connecting, a ``hello`` (what this Ignite offers) and the whole ``state``;
then ``event`` messages as they happen and ``state`` messages as things change. A client may send
``subscribe`` (only some events), ``command`` (with the control key; answered with a ``result``) and
``ping``. HTTP: ``GET /api/v1``, ``GET /api/v1/state[/kind]``, ``GET|POST /api/v1/commands/<name>``.

Never sent: stream keys, tokens, passwords, file paths.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from aiohttp import WSMsgType, web

from ignite_live import __version__
from ignite_live.core.events import (
    ChannelStats,
    ChatComment,
    ChatEvent,
    ChatFollow,
    ChatGift,
    ChatJoin,
    ChatLeave,
    ChatLike,
    ChatShare,
    ChatUser,
    LiveStatus,
    ViewerCount,
)
from ignite_live.core.images import allowed_image_url

log = logging.getLogger(__name__)

API_VERSION = 1
PREFIX = f"/api/v{API_VERSION}"
MAX_MESSAGE = 64 * 1024
QUEUE_LIMIT = 2000  # Messages waiting for one slow client; past that it's disconnected rather than lagging.


@dataclass(frozen=True)
class Command:
    args: tuple[str, ...]
    help: str


# What the API runs (with the control key), each like the button in the app does it.
COMMANDS: dict[str, Command] = {
    # Going live.
    "toggle_stream": Command((), "Start or stop the TikTok output in OBS (like the Start Streaming button)"),
    "start_stream": Command((), "Start the TikTok output in OBS"),
    "stop_stream": Command((), "Stop the TikTok output in OBS"),
    "send_key": Command((), "Put the current stream key on the chosen OBS output"),
    "end_live": Command((), "End the TikTok LIVE and retire its key"),
    "relaunch_live": Command(("title", "category_id"),
                             "Experimental: restart the LIVE with a new title and category"),
    # OBS.
    "switch_scene": Command(("name",), "Switch to an OBS scene (or a vertical canvas scene) by name"),
    "toggle_record": Command((), "Start or stop recording"),
    "pause_record": Command((), "Pause or resume the recording"),
    "toggle_replay": Command((), "Start or stop the replay buffer"),
    "save_replay": Command((), "Save a clip from the replay buffer"),
    "toggle_vertical_record": Command((), "Start or stop the vertical canvas recording (Aitum)"),
    "toggle_vertical_backtrack": Command((), "Start or stop the vertical canvas backtrack (Aitum)"),
    "save_vertical_clip": Command((), "Save a vertical clip from the backtrack (Aitum)"),
    "toggle_virtualcam": Command((), "Start or stop the virtual camera"),
    "screenshot": Command((), "Save a screenshot of the current scene"),
    "refresh_overlays": Command((), "Reload every Ignite overlay"),
    "set_overlay_theme": Command(("theme",), "Change the theme of every overlay"),
    # Chat.
    "connect_chat": Command((), "Connect every chat that has a name set"),
    "disconnect_chat": Command(("platform",), "Disconnect one chat (tiktok, twitch, youtube), or all of them"),
    "clear_chats": Command((), "Clear the chat panel, the pop-out and the chat overlays"),
    "simulate": Command(("kind", "user", "text", "gift", "count", "diamonds", "platform"),
                        "Send a test event (comment, gift, follow, like, share, join) through Ignite"),
    # Alerts and text to speech.
    "alert": Command(("title", "text", "image", "sound", "volume"), "Show an alert on the Alerts overlay"),
    "play_sound": Command(("name", "volume"), "Play a sound from Ignite's sounds folder through the overlays"),
    "say": Command(("text",), "Read text aloud with text to speech"),
    "skip_tts": Command((), "Stop the message being read aloud"),
    "mute_tts": Command(("muted",), "Mute or unmute text to speech"),
    # Music.
    "request_song": Command(("text",), "Request a song (a name and artist, or a YouTube link)"),
    "skip_song": Command((), "Skip the current song"),
    "previous_song": Command((), "Start the song over; again right away, go back one"),
    "pause_music": Command(("paused",), "Pause or play the music (no value: toggle)"),
    "set_volume": Command(("volume",), "Music volume, 0 to 100"),
    "remove_song": Command(("item_id",), "Remove a song from the queue"),
    "clear_queue": Command((), "Empty the song queue"),
    "stop_playlist": Command((), "Stop the playlist"),
    # Rules and safety.
    "run_rule": Command(("id",), "Run an action rule's actions now, like its Test button"),
    "panic": Command((), "Panic: mute text to speech, clear alerts, pause music and rules, switch to BRB"),
    "resume": Command((), "End panic mode"),
}
# Things that happen; each "event" message has one of these names.
EVENTS = ("comment", "gift", "follow", "like", "share", "join", "leave", "viewers", "stats", "live",
          "chat_clear", "notice")
# What Ignite looks like right now; the latest of each is in "state", and sent again when it changes.
STATE_KINDS = ("live", "panic", "status", "studio", "queue", "playlist", "tts_muted", "music", "goals",
               "leaderboards", "game", "stats")


def user_payload(user: ChatUser) -> dict:
    badges = (["host"] if user.is_host else []) + (["moderator"] if user.is_moderator else [])
    if user.is_subscriber:
        badges.append("member" if user.platform == "youtube" else "subscriber")
    if user.is_follower:
        badges.append("follower")
    return {
        "id": user.unique_id[:80],
        "name": (user.nickname or user.unique_id)[:40],
        "avatar": user.avatar_url if allowed_image_url(user.avatar_url) else "",
        "platform": user.platform,
        "platform_name": user.platform_name,
        "badges": badges,
        "moderator": user.is_moderator,
        "subscriber": user.is_subscriber,
        "follower": user.is_follower,
        "host": user.is_host,
    }


def event_payload(event: ChatEvent) -> tuple[str, dict] | None:
    """An event's API name and data, or None for events the API doesn't carry."""
    if isinstance(event, ChatComment):
        return "comment", {"user": user_payload(event.user), "text": event.text[:500]}
    if isinstance(event, ChatGift):
        return "gift", {
            "user": user_payload(event.user),
            "gift": event.gift_name[:60],
            "count": event.count,
            "diamonds": event.diamonds,  # One gift's worth.
            "total_diamonds": event.diamonds * event.count,
            "amount": event.amount[:20],  # Money as text, e.g. a Super Chat's "$5.00".
            "message": event.message[:500],
            "image": event.image_url if allowed_image_url(event.image_url) else "",
            # A TikTok combo still going: `count` so far. Its last gift comes with streaking false and the total;
            # count that one (as Ignite does) to count each combo once.
            "streaking": event.streaking,
        }
    if isinstance(event, ChatFollow):
        return "follow", {"user": user_payload(event.user)}
    if isinstance(event, ChatLike):
        return "like", {"user": user_payload(event.user), "count": event.count}
    if isinstance(event, ChatShare):
        return "share", {"user": user_payload(event.user)}
    if isinstance(event, ChatJoin):
        # listed: already there when chat connected (Twitch's member list), not just arriving.
        return "join", {"user": user_payload(event.user), "listed": event.listed}
    if isinstance(event, ChatLeave):
        return "leave", {"user": user_payload(event.user)}
    if isinstance(event, ViewerCount):
        return "viewers", {"platform": event.platform, "count": event.count}
    if isinstance(event, ChannelStats):
        return "stats", {"platform": event.platform, "viewers": event.viewers, "followers": event.followers,
                         "likes": event.likes}
    if isinstance(event, LiveStatus):
        return "live", {"platform": event.platform, "live": event.live}
    return None


def new_key() -> str:
    return secrets.token_urlsafe(24)


# Command arguments arrive as JSON from anywhere: read them defensively.

def text_arg(args: dict, key: str, limit: int) -> str:
    return " ".join(str(args.get(key, "")).split())[:limit]


def int_arg(args: dict, key: str, default: int, low: int, high: int) -> int:
    value = args.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(low, min(high, int(value)))


SIMULATED_KINDS = ("comment", "gift", "follow", "like", "share", "join")


def simulated_event(args: dict) -> ChatEvent:
    """The "simulate" command's test event (Ignite treats it as simulated: no points or reports)."""
    kind = str(args.get("kind", "comment"))
    if kind not in SIMULATED_KINDS:
        raise ValueError(f"Simulate one of: {', '.join(SIMULATED_KINDS)}.")
    name = text_arg(args, "user", 40) or "Test viewer"
    platform = str(args.get("platform", "tiktok"))
    if platform not in ("tiktok", "twitch", "youtube"):
        raise ValueError("The platform is tiktok, twitch or youtube.")
    user = ChatUser("test-" + name.lower().replace(" ", "-")[:30], name, is_follower=True, platform=platform)
    count = int_arg(args, "count", 1, 1, 10_000)
    if kind == "comment":
        return ChatComment(user, text_arg(args, "text", 300) or "Hello from the API!", int(time.time() * 1000))
    if kind == "gift":
        return ChatGift(user, text_arg(args, "gift", 60) or "Rose", count,
                        diamonds=int_arg(args, "diamonds", 1, 0, 100_000))
    if kind == "follow":
        return ChatFollow(user)
    if kind == "like":
        return ChatLike(user, count)
    if kind == "share":
        return ChatShare(user)
    return ChatJoin(user)


class _Client:
    """One WebSocket client: its own send queue, so a slow one can't hold up the others."""

    def __init__(self, ws: web.WebSocketResponse, control: bool) -> None:
        self.ws = ws
        self.control = control
        self.events: frozenset[str] = frozenset(EVENTS)
        self.queue: asyncio.Queue[dict | None] = asyncio.Queue()

    def send(self, message: dict) -> bool:
        if self.queue.qsize() >= QUEUE_LIMIT:
            return False
        self.queue.put_nowait(message)
        return True

    async def writer(self) -> None:
        while (message := await self.queue.get()) is not None:
            try:
                await self.ws.send_json(message)
            except (ConnectionError, RuntimeError):
                return


class ApiHub:
    """The API's clients and state; lives on the overlay server's event loop."""

    def __init__(self) -> None:
        self.state: dict[str, object] = {}
        self._clients: set[_Client] = set()
        # Runs a command (already checked against COMMANDS) and returns a short result; ValueError: refused.
        self.on_command: Callable[[str, dict], Awaitable[str]] | None = None
        self.on_clients_changed: Callable[[int], None] | None = None
        # Every state and event message, for the hosted API on an Ignite server (through the relay).
        self.on_message: Callable[[dict], None] | None = None

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def hello(self, control: bool) -> dict:
        info = {"type": "hello", "api": API_VERSION, "app": "Ignite", "version": __version__,
                "events": list(EVENTS), "state": list(STATE_KINDS), "control": control}
        if control:
            info["commands"] = {name: {"args": list(c.args), "help": c.help} for name, c in COMMANDS.items()}
        return info

    # From the app (on the event loop).

    def set_state(self, kind: str, data: object) -> None:
        if kind not in STATE_KINDS:
            raise ValueError(f"Not a state kind: {kind}")
        self.state[kind] = data
        self._broadcast({"type": "state", "kind": kind, "data": data})

    def emit(self, event: str, data: dict, simulated: bool = False) -> None:
        if event not in EVENTS:
            raise ValueError(f"Not an event: {event}")
        message = {"type": "event", "event": event, "data": data, "simulated": simulated,
                   "time": int(time.time() * 1000)}
        self._broadcast(message, event)

    async def close(self) -> None:
        for client in list(self._clients):
            self._drop(client)  # Stops its writer.
            await client.ws.close()

    def _broadcast(self, message: dict, event: str | None = None) -> None:
        if self.on_message is not None:
            self.on_message(message)
        for client in list(self._clients):
            if event is not None and event not in client.events:
                continue
            if not client.send(message):
                log.warning("API: a client fell %d messages behind; disconnecting it", QUEUE_LIMIT)
                self._drop(client)
                asyncio.get_running_loop().create_task(client.ws.close(message=b"too slow"))

    def _drop(self, client: _Client) -> None:
        if client in self._clients:
            self._clients.discard(client)
            client.queue.put_nowait(None)
            if self.on_clients_changed:
                self.on_clients_changed(self.client_count)

    # Requests (the overlay server has checked the Host and the token or key; `control` says which).

    async def socket(self, request: web.Request, control: bool) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=MAX_MESSAGE)
        await ws.prepare(request)
        client = _Client(ws, control)
        client.send(self.hello(control))
        client.send({"type": "state", "state": dict(self.state)})
        self._clients.add(client)
        if self.on_clients_changed:
            self.on_clients_changed(self.client_count)
        writer = asyncio.get_running_loop().create_task(client.writer())
        try:
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(message.data)
                except json.JSONDecodeError:
                    client.send({"type": "error", "message": "Not JSON."})
                    continue
                if isinstance(data, dict):
                    await self._client_message(client, data)
        finally:
            self._drop(client)
            await asyncio.wait([writer], timeout=2)
        return ws

    async def _client_message(self, client: _Client, data: dict) -> None:
        kind = data.get("type")
        if kind == "ping":
            client.send({"type": "pong", "id": data.get("id")})
        elif kind == "subscribe":
            wanted = data.get("events")
            if isinstance(wanted, list) and wanted:
                client.events = frozenset(e for e in wanted if e in EVENTS)
            else:
                client.events = frozenset(EVENTS)  # Empty or missing: everything again.
            client.send({"type": "subscribed", "events": sorted(client.events)})
        elif kind == "command":
            result = await self.run(str(data.get("name", "")), data.get("args"), client.control)
            result.pop("status", None)  # HTTP's; a result message says ok or not.
            client.send({"type": "result", "id": data.get("id"), "name": str(data.get("name", ""))[:60], **result})
        else:
            client.send({"type": "error", "message": "Unknown message type."})

    async def run(self, name: str, raw_args: object, control: bool) -> dict:
        """A command from a client: {"ok": bool, "message": str} (and "status" for HTTP)."""
        if not control:
            return {"ok": False, "status": 403,
                    "message": "Commands need the control key (API tab), not the overlay token."}
        command = COMMANDS.get(name)
        if command is None:
            return {"ok": False, "status": 404, "message": f"No command called “{name[:60]}”."}
        raw = raw_args if isinstance(raw_args, dict) else {}
        args = {key: raw[key] for key in command.args if key in raw}
        if self.on_command is None:
            return {"ok": False, "status": 503, "message": "Ignite is still starting."}
        try:
            return {"ok": True, "status": 200, "message": await self.on_command(name, args)}
        except ValueError as error:
            return {"ok": False, "status": 400, "message": str(error)}
        except Exception:  # noqa: BLE001 - one bad command mustn't take the API down
            log.exception("API: %s failed", name)
            return {"ok": False, "status": 500, "message": "Something went wrong. Check Ignite's log."}

    async def http_info(self, request: web.Request, control: bool) -> web.Response:
        return web.json_response(self.hello(control))

    async def http_state(self, request: web.Request, control: bool) -> web.Response:
        kind = request.match_info.get("kind")
        if kind is None:
            return web.json_response(self.state)
        if kind not in STATE_KINDS:
            return web.json_response({"ok": False, "message": f"No state called “{kind[:60]}”."}, status=404)
        return web.json_response(self.state.get(kind))

    async def http_command(self, request: web.Request, control: bool) -> web.Response:
        """Arguments from the query string (Stream Deck "open URL" buttons) and/or a JSON body."""
        args: dict = {key: _query_value(value) for key, value in request.query.items() if key not in ("token", "key")}
        if request.method == "POST" and request.can_read_body:
            try:
                body = await request.json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                return web.json_response({"ok": False, "message": "The body must be JSON."}, status=400)
            if isinstance(body, dict):
                args.update(body)
        result = await self.run(request.match_info["name"], args, control)
        status = result.pop("status", 200)
        return web.json_response(result, status=status)


def _query_value(text: str) -> object:
    """Query strings are text: "true"/"false" and whole numbers become what they say."""
    if text in ("true", "false"):
        return text == "true"
    if text.lstrip("-").isdigit() and len(text) < 12:
        return int(text)
    return text
