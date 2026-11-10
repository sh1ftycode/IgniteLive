"""Twitch chat, read anonymously: no Twitch login, app, or key.

Twitch chat is IRC over a WebSocket. Logging in as ``justinfan<number>``
(Twitch's anonymous read-only user) lets anyone read any channel's chat.
Tags (``twitch.tv/tags``) carry display names, badges, bits and sub details.
Docs: https://dev.twitch.tv/docs/chat/irc/

Twitch chat stays open while a channel is offline, so joining it says nothing
about being live. That comes from the channel's public page, which marks a
running stream with ``"isLiveBroadcast":true`` (checked once a minute).

Who's in chat comes from IRC's membership capability (``twitch.tv/membership``):
a NAMES list on joining, then JOIN and PART lines. Twitch only sends those for
channels with up to about 1,000 chatters, batches them (they can lag by a
minute or so), and never lists viewers who are logged out or have chat closed.

Viewer and follower counts aren't in chat, and Twitch's own API gives them
only to registered apps. With ``stats`` on they come from DecAPI
(https://decapi.me), a free public service made for chat bots; it is sent the
channel name and nothing else.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import Callable

import aiohttp

from ignite_live.core.backoff import backoff_delay
from ignite_live.core.events import (
    ChannelStats,
    ChatComment,
    ChatEvent,
    ChatGift,
    ChatJoin,
    ChatLeave,
    ChatUser,
    ConnectionState,
    LiveStatus,
    StatusChange,
    ViewerCount,
)

log = logging.getLogger(__name__)

SOURCE = "twitch"
URL = "wss://irc-ws.chat.twitch.tv:443"
CHANNEL_PAGE = "https://www.twitch.tv/{channel}"
STATS_URL = "https://decapi.me/twitch/{kind}/{channel}"  # kind: "viewercount" or "followcount"; plain text.
LIVE_POLL_SECONDS = 60.0
FOLLOWERS_EVERY = 5  # The follower count is read on every fifth live check.
JOIN_TIMEOUT_S = 15.0
PAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/130.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
_CHANNEL = re.compile(r"^[a-z0-9_]{2,25}$")
_TAG_ESCAPES = {"s": " ", ":": ";", "\\": "\\", "r": "\r", "n": "\n"}
_CHEERMOTE = re.compile(r"^[A-Za-z]+\d+$")
_PLANS = {"Prime": "Prime sub", "1000": "Tier 1 sub", "2000": "Tier 2 sub", "3000": "Tier 3 sub"}


def channel_from(text: str) -> str:
    """"Ember", "#ember" or "https://www.twitch.tv/ember" -> "ember"; "" if it isn't a channel name."""
    text = text.strip().lower()
    match = re.match(r"^(?:https?://)?(?:www\.|m\.)?twitch\.tv/([^/?#]+)", text)
    name = (match.group(1) if match else text).lstrip("#@")
    return name if _CHANNEL.match(name) else ""


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)?", lambda m: _TAG_ESCAPES.get(m.group(1) or "", m.group(1) or ""), value)


def page_is_live(html: str) -> bool:
    """A channel page's structured data marks a running stream as a live broadcast."""
    return '"isLiveBroadcast":true' in html


def parse_line(line: str) -> tuple[dict[str, str], str, str, list[str], str]:
    """One IRC line -> (tags, prefix nick, command, params, trailing text)."""
    tags: dict[str, str] = {}
    if line.startswith("@"):
        raw, _, line = line[1:].partition(" ")
        for part in raw.split(";"):
            key, _, value = part.partition("=")
            tags[key] = _unescape(value)
    nick = ""
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
        nick = prefix.split("!", 1)[0]
    line, _, trailing = line.partition(" :")
    parts = line.split()
    return tags, nick, (parts[0] if parts else ""), parts[1:], trailing


def _badges(tags: dict[str, str]) -> set[str]:
    return {badge.split("/", 1)[0] for badge in tags.get("badges", "").split(",") if badge}


def user_from(tags: dict[str, str], nick: str) -> ChatUser:
    badges = _badges(tags)
    login = tags.get("login") or nick
    return ChatUser(
        unique_id=login,
        nickname=tags.get("display-name") or login,
        is_moderator=tags.get("mod") == "1" or "moderator" in badges,
        is_host="broadcaster" in badges,
        is_subscriber=tags.get("subscriber") == "1" or bool(badges & {"subscriber", "founder", "vip"}),
        platform=SOURCE,
    )


def plain_count(text: str) -> int | None:
    """DecAPI answers with just a number ("1,234" or "1234"); anything else ("x is offline", an error) is None."""
    digits = text.strip().replace(",", "")
    return int(digits) if digits.isdigit() and len(digits) <= 12 else None


def _member(login: str) -> ChatUser | None:
    login = login.strip().lower().lstrip("@+%~&")
    if not _CHANNEL.match(login) or login.startswith("justinfan"):  # Anonymous readers, like this app.
        return None
    return ChatUser(unique_id=login, nickname=login, platform=SOURCE)


def members_from(line: str) -> list[ChatEvent]:
    """Who's in chat, from the membership lines: 353 (the NAMES list), JOIN and PART."""
    _tags, nick, command, _params, text = parse_line(line)
    if command == "353":
        return [ChatJoin(user) for user in map(_member, text.split()) if user is not None]
    if command in ("JOIN", "PART"):
        user = _member(nick)
        if user is not None:
            return [ChatJoin(user) if command == "JOIN" else ChatLeave(user)]
    return []


def events_from(line: str, now_ms: int) -> list[ChatEvent]:
    """Chat events in one IRC line (most lines have none)."""
    tags, nick, command, _params, text = parse_line(line)
    created = int(tags.get("tmi-sent-ts") or now_ms)
    if command == "PRIVMSG":
        user = user_from(tags, nick)
        bits = int(tags["bits"]) if tags.get("bits", "").isdigit() else 0
        if bits:
            message = " ".join(word for word in text.split() if not _CHEERMOTE.match(word))
            return [ChatGift(user, "Bits", bits, message=message)]
        if text.startswith("\x01ACTION ") and text.endswith("\x01"):  # /me
            text = text[8:-1]
        return [ChatComment(user, text, created)] if text else []
    if command == "USERNOTICE":
        user = user_from(tags, nick)
        kind = tags.get("msg-id", "")
        plan = _PLANS.get(tags.get("msg-param-sub-plan", ""), "sub")
        if kind in ("sub", "resub"):
            months = tags.get("msg-param-cumulative-months", "")
            name = f"{plan} ({months} months)" if months.isdigit() and int(months) > 1 else plan
            return [ChatGift(user, name, 1, message=text)]
        if kind == "submysterygift":
            count = tags.get("msg-param-mass-gift-count", "1")
            return [ChatGift(user, f"{plan.replace(' sub', '')} gift sub", int(count) if count.isdigit() else 1)]
        if kind == "subgift" and not tags.get("msg-param-community-gift-id"):
            # Gifts that are part of a "gifted N subs" bundle were already counted above.
            recipient = tags.get("msg-param-recipient-display-name", "")
            name = f"{plan.replace(' sub', '')} gift sub" + (f" to {recipient}" if recipient else "")
            return [ChatGift(user, name, 1)]
    return []


class TwitchChat:
    """Keeps one channel's chat connected until ``stop()``. Same interface as TikTokChat."""

    def __init__(
        self,
        channel: str,
        on_event: Callable[[ChatEvent], None],
        on_status: Callable[[StatusChange], None],
        url: str = URL,
        sleep: Callable[[float], object] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
        page_url: str = CHANNEL_PAGE,
        stats: bool = True,
        stats_url: str = STATS_URL,
    ) -> None:
        self.channel = channel_from(channel)
        if not self.channel:
            raise ValueError("Enter a Twitch channel name (letters, numbers and _).")
        self._on_event = on_event
        self._on_status = on_status
        self._url = url
        self._page_url = page_url
        self._stats = stats  # Viewer and follower counts from DecAPI (see the module docstring).
        self._stats_url = stats_url
        self._viewers: int | None = None
        self._followers: int | None = None
        self._sleep = sleep
        self._joined = False
        self.live: bool | None = None  # Unknown until the channel page has been checked.
        self._clock = clock
        self._stopping = False
        self._failed = False  # A problem retrying won't fix (unknown channel): keep the error shown.
        self._ws: aiohttp.ClientWebSocketResponse | None = None

    def _status(self, state: ConnectionState, detail: str = "") -> None:
        self._on_status(StatusChange(SOURCE, state, detail))

    def _connected_status(self) -> None:
        live = {True: " (live)", False: " (offline: chat still works)"}.get(self.live, "")
        self._status(ConnectionState.CONNECTED, f"Reading chat for #{self.channel}{live}")

    async def _watch_live(self, session: aiohttp.ClientSession) -> None:
        """Check the channel page once a minute and report changes."""
        checks = 0
        while not self._stopping:
            try:
                async with session.get(self._page_url.format(channel=self.channel), headers=PAGE_HEADERS,
                                       timeout=aiohttp.ClientTimeout(total=20)) as response:
                    live = page_is_live(await response.text()) if response.status == 200 else self.live
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as error:
                log.info("Twitch live check failed: %s", error)
                live = self.live
            if live is not None and live != self.live:
                self.live = live
                self._on_event(LiveStatus(SOURCE, live))
                if self._joined:
                    self._connected_status()
            if self._stats:
                await self._read_stats(session, followers=checks % FOLLOWERS_EVERY == 0)
            checks += 1
            await asyncio.sleep(LIVE_POLL_SECONDS)

    async def _stat(self, session: aiohttp.ClientSession, kind: str) -> int | None:
        try:
            async with session.get(self._stats_url.format(kind=kind, channel=self.channel),
                                   timeout=aiohttp.ClientTimeout(total=15)) as response:
                return plain_count(await response.text()) if response.status == 200 else None
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, UnicodeDecodeError) as error:
            log.info("Twitch %s unavailable: %s", kind, error)
            return None

    async def _read_stats(self, session: aiohttp.ClientSession, followers: bool) -> None:
        """Viewers while live (0 when offline), and now and then the follower count."""
        viewers = None
        if self.live is not None:  # Unknown until the channel page has answered once.
            viewers = await self._stat(session, "viewercount") if self.live else 0
        if viewers is not None and viewers != self._viewers:
            self._viewers = viewers
            self._on_event(ViewerCount(viewers, SOURCE))
        if followers or self._followers is None:
            count = await self._stat(session, "followcount")
            if count is not None and count != self._followers:
                self._followers = count
                self._on_event(ChannelStats(SOURCE, followers=count))

    async def stop(self) -> None:
        self._stopping = True
        if self._ws is not None:
            await self._ws.close()

    async def run(self) -> None:
        attempt = 0
        async with aiohttp.ClientSession() as session:
            watcher = asyncio.create_task(self._watch_live(session))
            try:
                await self._connect_loop(session, attempt)
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
        if self.live:
            self._on_event(LiveStatus(SOURCE, False))  # Not watching any more: don't keep showing LIVE.
        if not self._failed:
            self._status(ConnectionState.OFFLINE)

    async def _connect_loop(self, session: aiohttp.ClientSession, attempt: int) -> None:
        while not self._stopping and not self._failed:
            self._status(ConnectionState.CONNECTING, f"Connecting to #{self.channel}…")
            try:
                joined = await self._session(session)
                attempt = 0 if joined else attempt + 1
                if self._stopping or self._failed:
                    break
                delay = backoff_delay(attempt)
                self._status(ConnectionState.DEGRADED, f"Twitch chat disconnected. Reconnecting in {int(delay)}s.")
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as error:
                attempt += 1
                delay = backoff_delay(attempt)
                log.info("Twitch chat connection failed: %s", error)
                self._status(ConnectionState.DEGRADED, f"Can't reach Twitch chat. Retrying in {int(delay)}s.")
            if self._stopping:
                break
            await self._sleep(delay)

    async def _session(self, session: aiohttp.ClientSession) -> bool:
        """One connection; returns whether the channel was joined."""
        joined = False
        async with session.ws_connect(self._url, heartbeat=None, timeout=aiohttp.ClientWSTimeout(ws_close=5)) as ws:
            self._ws = ws
            try:
                await ws.send_str("CAP REQ :twitch.tv/tags twitch.tv/commands twitch.tv/membership")
                await ws.send_str("PASS SCHMOOPIIE")  # Ignored for anonymous users, but expected.
                await ws.send_str(f"NICK justinfan{random.randint(10_000, 99_999)}")
                await ws.send_str(f"JOIN #{self.channel}")
                deadline = self._clock() + JOIN_TIMEOUT_S
                while not self._stopping:
                    timeout = max(0.5, deadline - self._clock()) if not joined else 360  # Twitch pings every ~5 min.
                    try:
                        message = await ws.receive(timeout=timeout)
                    except asyncio.TimeoutError:
                        if not joined:
                            self._status(ConnectionState.ERROR, f"Twitch has no chat for #{self.channel}. Check the name.")
                            self._failed = True
                        return joined
                    if message.type != aiohttp.WSMsgType.TEXT:
                        return joined
                    for line in message.data.split("\r\n"):
                        if not line:
                            continue
                        if line.startswith("PING"):
                            await ws.send_str("PONG :tmi.twitch.tv")
                            continue
                        _tags, _nick, command, _params, text = parse_line(line)
                        if command == "ROOMSTATE" and not joined:
                            joined = self._joined = True
                            self._connected_status()
                        elif command == "RECONNECT":
                            return joined
                        elif command == "NOTICE" and not joined:
                            self._status(ConnectionState.ERROR, f"Twitch: {text or 'channel unavailable'}")
                            self._failed = True
                            return joined
                        for event in members_from(line) if command in ("353", "JOIN", "PART") else \
                                events_from(line, int(self._clock() * 1000)):
                            self._on_event(event)
            finally:
                self._ws = None
                self._joined = False
        return joined
