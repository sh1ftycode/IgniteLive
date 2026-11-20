"""TikTok LIVE through Euler Stream's WebSocket API (wss://ws.eulerstream.com).

Euler Stream connects to TikTok for us and sends each LIVE's messages as JSON:
``{"timestamp": …, "messages": [{"type": "WebcastChatMessage", "data": {…}}, …]}``
(or one ``{"type", "data"}`` per frame without bundling). ``type`` is TikTok's
protobuf message name, or one of Euler's own: ``roomInfo`` (always first),
``workerInfo``, ``SyntheticJoinMessage`` / ``SyntheticLeaveMessage``,
``room.status``, ``tiktok.connect`` / ``tiktok.disconnect``. Field names follow
the protobuf schema version asked for; v2 and v3 name several fields
differently (``comment`` / ``content``, ``likeCount`` / ``count``,
``giftDetails`` / ``gift``), and 64-bit numbers arrive as strings, so every
field here is read defensively.

Two ways in, one reader (EulerChat):

* **Through your Ignite server** (signed in, Preferences → Server). The
  server holds the Euler API key, keeps one Euler connection per account, and
  sends this PC the *wire* form below, already trimmed (``/v1/tiktok``).
* **Straight to Euler** with your own key (Preferences → Chat platforms →
  Euler API key): the same frames Euler sends, parsed here.

The wire form (``normalize``) is small and only carries what Ignite uses::

    {"kind": "comment", "user": {...}, "text": "hi", "ts": 1731600000000}
    {"kind": "gift", "user": {...}, "name": "Rose", "count": 5, "diamonds": 1, "image": "", "streaking": false}
    {"kind": "like", "user": {...}, "count": 15, "total": 2000}
    {"kind": "follow" | "share" | "join" | "leave", "user": {...}}
    {"kind": "viewers", "count": 120}
    {"kind": "stats", "followers": 5100, "likes": 2000, "viewers": 120}
    {"kind": "end"}

    user: {"id": uniqueId, "name": nickname, "avatar": url, "mod", "follower", "host", "sub": bools}

Unlike TikTokLive, Euler's service may run inside a hosted server, so the
Ignite server can read TikTok chat for its accounts.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import time
import urllib.parse
from typing import Any, Callable

import aiohttp

from ignite_live.core.backoff import backoff_delay
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
    ConnectionState,
    StatusChange,
    ViewerCount,
)

log = logging.getLogger(__name__)

SOURCE = "tiktok"
EULER_WS = "wss://ws.eulerstream.com"
SCHEMA_VERSION = "v2"
OFFLINE_RETRY_S = 60.0
BUSY_RETRY_S = 120.0
MAX_TEXT = 500
REPLAY_WINDOW_MS = 5000  # TikTok's replay of recent comments comes within this long of connecting.
SEEN_COMMENTS = 2000  # Comment ids remembered, to recognise TikTok's replay after a reconnect.

# Euler's close codes (its WebSocket SDK's ClientCloseCode).
STREAM_END = 4005
NO_MESSAGES_TIMEOUT = 4006
INVALID_OPTIONS = 4400
INVALID_AUTH = 4401
NO_PERMISSION = 4403
NOT_LIVE = 4404
KEEPALIVE_TIMEOUT = 4408
TOO_MANY_CONNECTIONS = 4429
TIKTOK_CLOSED = 4500
MAX_LIFETIME = 4555  # 8 hours: just connect again.
OFFLINE_CODES = frozenset({STREAM_END, NO_MESSAGES_TIMEOUT, NOT_LIVE})
FATAL_CODES = frozenset({INVALID_OPTIONS, INVALID_AUTH, NO_PERMISSION})


def euler_url(unique_id: str, api_key: str = "", jwt: str = "", schema: str = SCHEMA_VERSION) -> str:
    params = {"uniqueId": clean_unique_id(unique_id), "features.schemaVersion": schema,
              "features.bundleEvents": "true", "features.syntheticPresence": "true"}
    if jwt:
        params["jwtKey"] = jwt
    elif api_key:
        params["apiKey"] = api_key
    return EULER_WS + "?" + urllib.parse.urlencode(params)


def clean_unique_id(text: str) -> str:
    """A TikTok username from "@name", "name", or a tiktok.com/@name/live link."""
    text = text.strip()
    if "tiktok.com/" in text:
        text = text.split("tiktok.com/", 1)[1]
    return text.split("/", 1)[0].split("?", 1)[0].lstrip("@").strip()


# Reading Euler's JSON.


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _first(data: dict, *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _int(value: Any) -> int | None:
    """A non-negative count (int64s come as strings), or None."""
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _image(value: Any) -> str:
    """First https URL of a TikTok image (v2 ``url``, v3 ``urlList``, raw ``url_list``)."""
    image = _dict(value)
    for key in ("url", "urlList", "url_list"):
        urls = image.get(key)
        if isinstance(urls, list):
            for url in urls:
                if isinstance(url, str) and url.startswith("https://"):
                    return url[:500]
    return ""


def _user(raw: Any, identity: Any = None) -> dict:
    user, who = _dict(raw), _dict(identity)
    unique_id = str(_first(user, "uniqueId", "displayId", "unique_id", "display_id") or "")[:64]
    avatar = ""
    for key in ("avatarThumb", "profilePicture", "avatarMedium", "avatarLarge", "avatarJpg", "avatar_thumb"):
        avatar = _image(user.get(key))
        if avatar:
            break
    numeric = str(_first(user, "userId", "id_str", "id") or "")
    return {
        "id": unique_id,
        "uid": numeric if numeric.isdigit() and len(numeric) <= 25 else "",
        "name": str(user.get("nickname") or unique_id)[:64],
        "avatar": avatar,
        "mod": bool(who.get("isModeratorOfAnchor")),
        "follower": bool(who.get("isFollowerOfAnchor") or user.get("isFollower")),
        "host": bool(who.get("isAnchor")),
        "sub": bool(who.get("isSubscriberOfAnchor") or user.get("isSubscribe")),
    }


def _display_key(data: dict) -> str:
    text = _dict(_dict(data.get("common")).get("displayText"))
    return str(text.get("key") or text.get("displayType") or "")


def _enum_is(value: Any, number: int, name: str) -> bool:
    """Protobuf enums come as their number or their name, depending on the encoder."""
    return value == number or (isinstance(value, str) and (value == str(number) or value.endswith(name)))


def normalize(message: Any) -> dict | None:
    """One Euler message ({"type", "data"}) in Ignite's wire form, or None for what Ignite doesn't use."""
    message = _dict(message)
    kind, data = message.get("type"), _dict(message.get("data"))
    if kind == "WebcastChatMessage":
        text = str(_first(data, "comment", "content") or "").strip()
        if not text:
            return None
        common = _dict(data.get("common"))
        created = _int(common.get("createTime")) or 0
        message_id = str(common.get("msgId") or "")
        return {"kind": "comment", "user": _user(data.get("user"), data.get("userIdentity")), "text": text[:MAX_TEXT],
                "ts": created, "mid": message_id if message_id.isdigit() and len(message_id) <= 25 else ""}
    if kind == "WebcastGiftMessage":
        gift = _dict(_first(data, "giftDetails", "gift"))
        name = str(_first(gift, "giftName", "name") or "").strip()
        if not name:
            return None
        streakable = _int(_first(gift, "giftType", "type")) == 1 or gift.get("combo") is True
        ended = data.get("repeatEnd") in (1, True, "1")
        return {"kind": "gift", "user": _user(data.get("user"), data.get("userIdentity")), "name": name[:64],
                "count": max(1, _int(data.get("repeatCount")) or 1), "diamonds": _int(gift.get("diamondCount")) or 0,
                "image": _image(_first(gift, "giftImage", "image", "icon")) or _image(data.get("giftImage")),
                "streaking": streakable and not ended}
    if kind == "WebcastLikeMessage":
        return {"kind": "like", "user": _user(data.get("user")),
                "count": _int(_first(data, "likeCount", "count")) or 0,
                "total": _int(_first(data, "totalLikeCount", "total"))}
    if kind == "WebcastSocialMessage":
        key = _display_key(data)
        if "follow" in key:
            return {"kind": "follow", "user": _user(data.get("user"))}
        if "share" in key:
            return {"kind": "share", "user": _user(data.get("user"))}
        return None
    if kind == "WebcastMemberMessage":
        if _enum_is(data.get("action"), 1, "JOINED") or "join" in _display_key(data):
            user = _user(data.get("user"), data.get("userIdentity"))
            return {"kind": "join", "user": user} if user["id"] else None
        return None
    if kind in ("SyntheticJoinMessage", "SyntheticLeaveMessage"):
        user = _user(data.get("user"))
        if not user["id"]:
            return None
        return {"kind": "join" if kind == "SyntheticJoinMessage" else "leave", "user": user}
    if kind == "WebcastRoomUserSeqMessage":
        count = _int(_first(data, "viewerCount", "total"))
        return {"kind": "viewers", "count": count} if count is not None else None
    if kind == "WebcastControlMessage":
        return {"kind": "end"} if _enum_is(data.get("action"), 3, "STREAM_ENDED") else None
    if kind == "roomInfo":
        return room_stats(data)
    return None


def room_stats(info: Any) -> dict | None:
    """Followers (and likes and viewers, when present) from Euler's room info.

    Seen from Euler (2026-11): ``{"roomInfo": {"status", "isLive", "id", "title", …}, "user": {"followers", …},
    "uniqueId", "raw"}``: followers only; likes and viewers come from their own messages. TikTok's own room
    details (``owner.follow_info``, ``stats``, ``user_count``) are read too, in case Euler sends them."""
    info = _dict(info)
    room = _dict(info.get("roomInfo")) or _dict(info.get("data")) or info
    owner = _dict(room.get("owner"))
    follow = _dict(_first(owner, "follow_info", "followInfo"))
    stats = _dict(room.get("stats"))
    followers = _int(_first(follow, "follower_count", "followerCount"))
    if followers is None:
        followers = _int(_dict(info.get("user")).get("followers"))
    likes = _int(_first(stats, "like_count", "likeCount")) or _int(_first(room, "like_count", "likeCount"))
    viewers = _int(_first(room, "user_count", "userCount"))
    if followers is None and likes is None and viewers is None:
        return None
    return {"kind": "stats", "followers": followers, "likes": likes, "viewers": viewers}


def frame_messages(raw: Any) -> list[dict]:
    """The messages in one Euler frame: a bundle, or a single message."""
    frame = _dict(raw)
    if isinstance(frame.get("messages"), list):
        return [m for m in frame["messages"] if isinstance(m, dict)]
    return [frame] if "type" in frame else []


# The wire form, back into Ignite's events (on the PC).


def _chat_user(raw: Any) -> ChatUser:
    user = _dict(raw)
    unique_id = str(user.get("id") or "")[:64]
    return ChatUser(unique_id=unique_id, nickname=str(user.get("name") or unique_id)[:64],
                    is_moderator=user.get("mod") is True, is_follower=user.get("follower") is True,
                    is_host=user.get("host") is True, avatar_url=str(user.get("avatar") or "")[:500],
                    is_subscriber=user.get("sub") is True, user_id=str(user.get("uid") or "")[:25])


def to_event(wire: Any, now_ms: int = 0) -> ChatEvent | None:
    wire = _dict(wire)
    kind = wire.get("kind")
    if kind == "comment" and isinstance(wire.get("text"), str) and wire["text"].strip():
        return ChatComment(_chat_user(wire.get("user")), wire["text"][:MAX_TEXT], _int(wire.get("ts")) or now_ms,
                           str(wire.get("mid") or "")[:25])
    if kind == "gift" and isinstance(wire.get("name"), str):
        return ChatGift(_chat_user(wire.get("user")), wire["name"][:64], max(1, _int(wire.get("count")) or 1),
                        str(wire.get("image") or "")[:500], _int(wire.get("diamonds")) or 0,
                        streaking=wire.get("streaking") is True)
    if kind == "like" and (_int(wire.get("count")) or 0) > 0:
        return ChatLike(_chat_user(wire.get("user")), _int(wire["count"]))
    if kind == "follow":
        return ChatFollow(_chat_user(wire.get("user")))
    if kind == "share":
        return ChatShare(_chat_user(wire.get("user")))
    if kind == "join":
        return ChatJoin(_chat_user(wire.get("user")))
    if kind == "leave":
        return ChatLeave(_chat_user(wire.get("user")))
    if kind == "viewers" and _int(wire.get("count")) is not None:
        return ViewerCount(_int(wire["count"]))
    if kind == "stats":
        stats = ChannelStats(SOURCE, viewers=_int(wire.get("viewers")), followers=_int(wire.get("followers")),
                             likes=_int(wire.get("likes")))
        return stats if (stats.viewers, stats.followers, stats.likes) != (None, None, None) else None
    return None


def close_status(code: int | None, unique_id: str) -> tuple[ConnectionState, str, float | None]:
    """What a closed Euler connection means: (state, detail, retry in seconds; None = don't retry)."""
    if code in OFFLINE_CODES:
        return ConnectionState.DEGRADED, f"@{unique_id} is not live. Checking again in {int(OFFLINE_RETRY_S)}s.", OFFLINE_RETRY_S
    if code in FATAL_CODES:
        return ConnectionState.ERROR, "Euler Stream refused the API key, or it doesn't allow this.", None
    if code == TOO_MANY_CONNECTIONS:
        return ConnectionState.DEGRADED, f"Euler Stream is busy (too many connections). Retrying in {int(BUSY_RETRY_S)}s.", BUSY_RETRY_S
    if code == MAX_LIFETIME:
        return ConnectionState.CONNECTING, "Reconnecting (Euler connections last 8 hours)…", 0.0
    return ConnectionState.DEGRADED, "TikTok chat connection lost.", -1.0  # -1: back off.


class EulerChat:
    """Keeps one creator's LIVE chat connected through Euler Stream until stopped (``run()`` / ``stop()``,
    like TikTokChat). ``server``: (Ignite server URL, its token) to go through the server; else ``api_key``.

    ``fallback``: a reader to use instead (TikTokLive) when the server doesn't read TikTok chat (404) or has
    no Euler key: then this hands over to it for good.
    """

    def __init__(
        self,
        unique_id: str,
        on_event: Callable[[ChatEvent], None],
        on_status: Callable[[StatusChange], None],
        api_key: str = "",
        server: tuple[str, str] | None = None,
        fallback: Callable[[], Any] | None = None,
        session_factory: Callable[[], aiohttp.ClientSession] | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.unique_id = clean_unique_id(unique_id)
        if not self.unique_id:
            raise ValueError("Enter a TikTok username.")
        if not api_key and server is None:
            raise ValueError("Needs an Euler Stream API key or a signed-in Ignite server.")
        self._on_event = on_event
        self._on_status = on_status
        self._api_key = api_key
        self._server = server
        self._fallback = fallback
        if session_factory is None:
            from ignite_live.core.http import tls_session

            session_factory = tls_session
        self._session_factory = session_factory
        self._sleep = sleep
        self._clock = clock
        self._stopping = False
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._handed_over: Any = None
        self._connected_at_ms = 0
        self._likes = -1
        # Every comment, across reconnects (comment_is_new): which were handled, and where Ignite started reading.
        self._connections = 0
        self._seen: collections.deque[str] = collections.deque(maxlen=SEEN_COMMENTS)
        self._seen_set: set[str] = set()
        self._started_ts = 0  # TikTok's time of the newest comment from before Ignite started reading.

    @property
    def via_server(self) -> bool:
        return self._server is not None

    def _status(self, state: ConnectionState, detail: str = "") -> None:
        self._on_status(StatusChange(SOURCE, state, detail))

    async def stop(self) -> None:
        self._stopping = True
        if self._handed_over is not None:
            await self._handed_over.stop()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()

    def _target(self) -> tuple[str, dict]:
        if self._server is not None:
            base, token = self._server
            base = base.strip().rstrip("/")
            socket = ("wss://" + base.removeprefix("https://")) if base.startswith("https://") else (
                "ws://" + base.removeprefix("http://"))
            return (socket + "/v1/tiktok?" + urllib.parse.urlencode({"username": self.unique_id}),
                    {"Authorization": f"Bearer {token}"})
        return euler_url(self.unique_id, self._api_key), {}

    async def run(self) -> None:
        attempt = 0
        while not self._stopping:
            url, headers = self._target()
            where = "your Ignite server" if self.via_server else "Euler Stream"
            self._status(ConnectionState.CONNECTING, f"Connecting to @{self.unique_id} through {where}…")
            code: int | None = None
            try:
                async with self._session_factory() as session:
                    async with session.ws_connect(url, headers=headers, heartbeat=30,
                                                  max_msg_size=4 * 1024 * 1024) as ws:
                        self._ws = ws
                        if not self.via_server:  # The server says when TikTok is connected; Euler's first frame does.
                            self._connected()
                        attempt = 0
                        result = await self._listen(ws)
                        if result == "fallback":
                            await self._hand_over()
                            return
                        code = ws.close_code
            except asyncio.CancelledError:
                self._status(ConnectionState.OFFLINE)
                raise
            except aiohttp.WSServerHandshakeError as error:
                if self.via_server and error.status in (404, 501):
                    await self._hand_over()  # An older server, or one without an Euler key.
                    return
                if error.status in (401, 403):
                    self._status(ConnectionState.ERROR, "Your Ignite server didn't accept this PC's sign-in."
                                 if self.via_server else "Euler Stream refused the API key.")
                    return
                log.info("TikTok (Euler): handshake failed: %s", error.status)
            except (aiohttp.ClientError, OSError, asyncio.TimeoutError, ValueError) as error:
                log.info("TikTok (Euler): connection failed: %s", type(error).__name__)
            finally:
                self._ws = None
            if self._stopping:
                break
            state, detail, retry = close_status(code, self.unique_id)
            if retry is None:
                self._status(state, detail)
                return
            if retry < 0:
                attempt += 1
                retry = backoff_delay(attempt)
                detail = f"{detail} Retrying in {int(retry)}s."
            self._status(state, detail)
            await self._sleep(retry)
        self._status(ConnectionState.OFFLINE)

    def _connected(self) -> None:
        self._connections += 1
        self._connected_at_ms = int(self._clock() * 1000)
        self._likes = -1
        self._status(ConnectionState.CONNECTED, f"Reading chat for @{self.unique_id}")

    async def _hand_over(self) -> None:
        if self._fallback is None or self._stopping:
            self._status(ConnectionState.ERROR, "Your Ignite server doesn't read TikTok chat. Sign out of it, or "
                                                "add an Euler Stream API key.")
            return
        log.info("TikTok: the server doesn't read TikTok chat; using this PC's own connection")
        self._handed_over = self._fallback()
        await self._handed_over.run()

    async def _listen(self, ws: aiohttp.ClientWebSocketResponse) -> str:
        async for message in ws:
            if message.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                frame = json.loads(message.data)
            except json.JSONDecodeError:
                continue
            if not isinstance(frame, dict):
                continue
            if self.via_server:
                if frame.get("type") == "status":
                    if frame.get("fallback") is True:
                        return "fallback"
                    self._server_status(frame)
                    continue
                wires = frame.get("events") if frame.get("type") == "tiktok" else None
                for wire in wires if isinstance(wires, list) else []:
                    self._emit(wire)
            else:
                for item in frame_messages(frame):
                    wire = normalize(item)
                    if wire is not None:
                        self._emit(wire)
        return "closed"

    def _server_status(self, frame: dict) -> None:
        try:
            state = ConnectionState(frame.get("state"))
        except ValueError:
            return
        if state is ConnectionState.CONNECTED:
            self._connected()
        else:
            self._status(state, str(frame.get("detail", ""))[:200])

    def _comment_is_new(self, wire: dict) -> bool:
        """Every comment once, including the ones said while the connection was down.

        Each time it connects, TikTok first replays the room's recent comments. The first time, those are from before
        Ignite started reading: they're skipped, so an old !sr or !skip doesn't run. After a reconnect the replay also
        holds what was said while disconnected: those are shown, and only comments already handled are skipped (by
        TikTok's message id, or its author, time and text). Times are TikTok's own, never this PC's clock, except
        for the first burst before any comment has been seen.
        """
        created = _int(wire.get("ts")) or 0
        user = _dict(wire.get("user"))
        key = str(wire.get("mid") or "") or f"{user.get('id')}|{created}|{wire.get('text')}"
        if key in self._seen_set:
            return False
        if len(self._seen) == self._seen.maxlen:
            self._seen_set.discard(self._seen[0])
        self._seen.append(key)
        self._seen_set.add(key)
        if self._connections <= 1:
            now_ms = int(self._clock() * 1000)
            replay = bool(created) and created < self._connected_at_ms and now_ms - self._connected_at_ms < REPLAY_WINDOW_MS
            if replay:
                self._started_ts = max(self._started_ts, created)
            return not replay
        return not (created and self._started_ts and created <= self._started_ts)

    def _emit(self, wire: Any) -> None:
        wire = _dict(wire)
        if wire.get("kind") == "end":
            self._status(ConnectionState.DEGRADED, f"@{self.unique_id} ended the LIVE.")
            return
        if wire.get("kind") == "comment" and not self._comment_is_new(wire):
            return
        if wire.get("kind") == "like":
            total = _int(wire.get("total"))
            if total and total != self._likes:
                self._likes = total
                self._on_event(ChannelStats(SOURCE, likes=total))
        event = to_event(wire, int(self._clock() * 1000))
        if event is not None:
            self._on_event(event)
