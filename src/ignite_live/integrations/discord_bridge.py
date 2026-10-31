"""Forward LIVE chat (TikTok, Twitch, YouTube) to a Discord channel through a webhook.

Discord allows about 5 webhook posts per 2 seconds, and a busy LIVE is far
faster than that, so lines are batched: every ``flush_interval`` the queued
lines are packed into as few messages as fit (2,000 characters each).

Chat text comes from strangers, so every post blocks mentions
(``allowed_mentions``), escapes Discord markdown and link syntax, turns off
link previews, and is sent silently so the channel isn't pinged constantly.
The line format comes from the ``discord.*`` message templates; only the
viewer-supplied values are escaped, so streamers can use markdown in theirs.
Docs: https://discord.com/developers/docs/resources/webhook#execute-webhook
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from dataclasses import dataclass
from typing import Callable

import aiohttp

from ignite_live.core.events import ChatComment, ChatEvent, ChatFollow, ChatGift, ChatUser, ConnectionState, StatusChange
from ignite_live.core.templates import Texts

log = logging.getLogger(__name__)

SOURCE = "discord"
MAX_CONTENT = 2000
MAX_QUEUED_LINES = 400
MAX_POSTS_PER_FLUSH = 2  # With a 2.5 s flush, stays well under 5 posts / 2 s.
FLAG_SUPPRESS_EMBEDS = 1 << 2
FLAG_SUPPRESS_NOTIFICATIONS = 1 << 12

WEBHOOK_URL = re.compile(
    r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/(?:v\d+/)?webhooks/\d+/[A-Za-z0-9_-]+$"
)
_MARKDOWN = re.compile(r"([\\*_~`|>#\[\]()<:-])")


class DiscordError(Exception):
    """A safe-to-display Discord error (never includes the webhook URL)."""


def valid_webhook_url(url: str) -> bool:
    return bool(WEBHOOK_URL.match(url.strip()))


def escape(text: str) -> str:
    """Neutralise Discord markdown, masked links, and custom-emoji/mention syntax."""
    text = _MARKDOWN.sub(r"\\\1", " ".join(text.split()))[:500]
    return text.replace("@", "@\u200b")  # Zero-width space: @everyone can't even render as a mention.


def _values(user: ChatUser) -> dict:
    return {
        "user": escape(user.nickname or user.unique_id),
        "badge": " (host)" if user.is_host else " (mod)" if user.is_moderator else "",
        "platform": user.platform_name,
    }


@dataclass
class Forwarding:
    comments: bool = True
    gifts: bool = True
    follows: bool = False


def format_event(event: ChatEvent, forwarding: Forwarding, texts: Texts | None = None) -> str | None:
    texts = texts or Texts()
    if isinstance(event, ChatComment) and forwarding.comments:
        return texts.format("discord.comment", message=escape(event.text), **_values(event.user))
    if isinstance(event, ChatGift) and forwarding.gifts:
        value = (f" ({escape(event.amount)})" if event.amount
                 else f" ({event.diamonds * event.count:,} diamonds)" if event.diamonds else "")
        return texts.format(
            "discord.gift", gift=escape(event.gift_name), count=event.count, value=value,
            times=f" ×{event.count}" if event.count != 1 else "",
            amount=escape(event.amount), message=escape(event.message), **_values(event.user),
        )
    if isinstance(event, ChatFollow) and forwarding.follows:
        return texts.format("discord.follow", **_values(event.user))
    return None


def _webhook_name(name: str) -> str:
    name = " ".join(name.split())[:80]
    return name if 1 <= len(name) and "discord" not in name.lower() and "clyde" not in name.lower() else "Ignite"


def pack(lines: list[str], limit: int = MAX_CONTENT) -> list[str]:
    """Join lines into as few messages as fit within Discord's length limit."""
    messages, current = [], ""
    for line in lines:
        line = line[:limit]
        if current and len(current) + 1 + len(line) > limit:
            messages.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        messages.append(current)
    return messages


class DiscordBridge:
    def __init__(
        self,
        webhook_url: str,
        creator: str,
        forwarding: Forwarding | None = None,
        on_status: Callable[[StatusChange], None] | None = None,
        flush_interval: float = 2.5,
        sleep: Callable[[float], object] = asyncio.sleep,
        texts: Texts | None = None,
    ) -> None:
        """``creator`` is how the streamer is named in messages, e.g. "@ember"."""
        if not valid_webhook_url(webhook_url):
            raise DiscordError("That isn't a Discord webhook URL (Channel settings → Integrations → Webhooks).")
        self._url = webhook_url.strip()
        self.creator = creator
        self.forwarding = forwarding or Forwarding()
        self.texts = texts or Texts()
        self._on_status = on_status
        self._flush_interval = flush_interval
        self._sleep = sleep
        self._lines: deque[str] = deque()
        self._dropped = 0
        self._session: aiohttp.ClientSession | None = None
        self._broken = False
        self._degraded = False

    # Called from the event loop thread.

    def push(self, event: ChatEvent) -> None:
        line = format_event(event, self.forwarding, self.texts)
        if line:
            self.push_line(line)

    def push_line(self, line: str) -> None:
        if self._broken:
            return
        if len(self._lines) >= MAX_QUEUED_LINES:
            self._lines.popleft()
            self._dropped += 1
        self._lines.append(line)

    async def run(self) -> None:
        self._status(ConnectionState.CONNECTED, "Forwarding chat to Discord")
        try:
            while not self._broken:
                await self._sleep(self._flush_interval)
                await self.flush()
        finally:
            await self.close()

    async def flush(self, max_posts: int = MAX_POSTS_PER_FLUSH) -> None:
        if not self._lines and not self._dropped:
            return
        # Take the batch out first: chat keeps arriving while posts are in flight.
        lines = list(self._lines)
        self._lines.clear()
        if self._dropped:
            lines.insert(0, f"*…{self._dropped} older lines skipped (Discord couldn't keep up)*")
            self._dropped = 0
        sent = 0
        for message in pack(lines)[:max_posts]:
            if not await self._post(message):
                break
            sent += message.count("\n") + 1
        if self._broken:
            self._lines.clear()
            return
        # Unsent lines go back ahead of anything that arrived meanwhile.
        self._lines.extendleft(reversed(lines[sent:]))
        while len(self._lines) > MAX_QUEUED_LINES:
            self._lines.popleft()
            self._dropped += 1

    async def send_test(self) -> None:
        if not await self._post(self.texts.format("discord.test", creator=escape(self.creator))):
            raise DiscordError("Discord didn't accept the test message. Check the webhook URL.")

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    # Internals.

    def _status(self, state: ConnectionState, detail: str) -> None:
        if self._on_status:
            self._on_status(StatusChange(SOURCE, state, detail))

    async def _post(self, content: str, attempts: int = 4) -> bool:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        payload = {
            "content": content,
            # Discord refuses webhook names containing "discord"; fall back rather than fail every post.
            "username": _webhook_name(self.texts.format("discord.name", creator=self.creator)),
            "allowed_mentions": {"parse": []},
            "flags": FLAG_SUPPRESS_EMBEDS | FLAG_SUPPRESS_NOTIFICATIONS,
        }
        for attempt in range(attempts):
            try:
                async with self._session.post(self._url, json=payload) as response:
                    if response.status in (200, 204):
                        if self._degraded:
                            self._degraded = False
                            self._status(ConnectionState.CONNECTED, "Forwarding chat to Discord")
                        if response.headers.get("X-RateLimit-Remaining") == "0":
                            await self._sleep(float(response.headers.get("X-RateLimit-Reset-After", 1)))
                        return True
                    if response.status == 429:
                        body = await response.json(content_type=None)
                        await self._sleep(float(body.get("retry_after", 1)) + 0.1)
                        continue
                    if response.status in (401, 403, 404):
                        self._broken = True
                        self._status(ConnectionState.ERROR, "Discord rejected the webhook. It may have been deleted.")
                        return False
                    log.warning("Discord webhook returned HTTP %s", response.status)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
                log.warning("Discord webhook post failed: %s", type(error).__name__)
            await self._sleep(2 ** attempt)
        self._degraded = True
        self._status(ConnectionState.DEGRADED, "Discord isn't answering. Retrying with the next batch.")
        return False
