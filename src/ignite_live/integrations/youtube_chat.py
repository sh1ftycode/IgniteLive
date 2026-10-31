"""YouTube live chat without an API key.

The official Data API's chat polling costs quota on every request, so this
reads chat the way youtube.com's own pop-out chat does: find the channel's live
video (``/@handle/live``), open its chat page for a continuation token, then
poll ``youtubei/v1/live_chat/get_live_chat`` at the interval YouTube asks for.
These endpoints are undocumented and may change; links and errors fail
gracefully into "retrying" rather than crashing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable

import aiohttp

from ignite_live.core.backoff import backoff_delay
from ignite_live.core.events import ChatComment, ChatEvent, ChatGift, ChatUser, ConnectionState, StatusChange
from ignite_live.music.youtube import video_id_from

log = logging.getLogger(__name__)

SOURCE = "youtube"
BASE = "https://www.youtube.com"
OFFLINE_POLL_SECONDS = 60.0
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/130.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Cookie": "SOCS=CAI",  # Skips the EU cookie-consent page.
}
_INITIAL_DATA = re.compile(r'(?:window\["ytInitialData"\]|var ytInitialData)\s*=\s*(\{.+?\});\s*</script>', re.S)
_CLIENT_VERSION = re.compile(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"')
_CANONICAL = re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})"')
_HANDLE = re.compile(r"^@[\w.\-]{3,30}$")
_CHANNEL_PATH = re.compile(r"^/(@[\w.\-]+|channel/UC[\w-]{22}|c/[\w.\-]+|user/[\w.\-]+)")


class YouTubeChatError(Exception):
    """Safe to show: what's wrong with the channel or link."""


def channel_path(text: str) -> str:
    """"@ember", "youtube.com/@ember/streams", ".../channel/UC…" -> "/@ember" (or "/channel/UC…").

    Returns "" for a video link (handled separately) or anything unrecognised.
    """
    text = text.strip()
    if _HANDLE.match(text) or _HANDLE.match("@" + text):
        return "/@" + text.lstrip("@")
    if "://" not in text:
        text = "https://" + text
    match = re.match(r"^https://(?:www\.|m\.)?youtube\.com(/[^?#]*)", text)
    path = _CHANNEL_PATH.match(match.group(1)) if match else None
    return "/" + path.group(1) if path else ""


def _text(value: Any) -> str:
    """Text of a YouTube "runs"/"simpleText"/"content" object; emoji become their shortcut or character."""
    if not isinstance(value, dict):
        return ""
    if "simpleText" in value:
        return str(value["simpleText"])
    if "content" in value:
        return str(value["content"])
    parts = []
    for run in value.get("runs") or []:
        if not isinstance(run, dict):
            continue
        if "text" in run:
            parts.append(str(run["text"]))
        elif isinstance(run.get("emoji"), dict):
            emoji = run["emoji"]
            shortcuts = emoji.get("shortcuts") or []
            parts.append(str(shortcuts[0]) if emoji.get("isCustomEmoji") and shortcuts else str(emoji.get("emojiId", "")))
    return "".join(parts)


def _photo(value: Any) -> str:
    thumbnails = (value or {}).get("thumbnails") or (value or {}).get("sources") or []
    url = str(thumbnails[-1].get("url", "")) if thumbnails and isinstance(thumbnails[-1], dict) else ""
    return "https:" + url if url.startswith("//") else url


def _user(item: dict) -> ChatUser:
    name = (_text(item.get("authorName")) or "Someone").strip().lstrip("@")
    icons, member = set(), False
    for badge in item.get("authorBadges") or []:
        renderer = (badge or {}).get("liveChatAuthorBadgeRenderer") or {}
        icon = (renderer.get("icon") or {}).get("iconType")
        if icon:
            icons.add(icon)
        elif renderer.get("customThumbnail"):
            member = True  # Channel members get the channel's own badge image.
    avatar = item.get("authorPhoto") or ((item.get("authorAvatar") or {}).get("avatarViewModel") or {}).get("image")
    return ChatUser(
        unique_id=str(item.get("authorExternalChannelId") or name),
        nickname=name,
        is_moderator="MODERATOR" in icons,
        is_host="OWNER" in icons,
        avatar_url=_photo(avatar),
        platform=SOURCE,
        is_subscriber=member,
    )


def _created_ms(item: dict, fallback_ms: int) -> int:
    value = str(item.get("timestampUsec", ""))
    return int(value) // 1000 if value.isdigit() else fallback_ms


def event_from(item: dict, now_ms: int) -> ChatEvent | None:
    """One ``addChatItemAction`` item -> a chat event (or None for system messages)."""
    if "liveChatTextMessageRenderer" in item:
        data = item["liveChatTextMessageRenderer"]
        text = _text(data.get("message")).strip()
        return ChatComment(_user(data), text, _created_ms(data, now_ms)) if text else None
    if "liveChatPaidMessageRenderer" in item:
        data = item["liveChatPaidMessageRenderer"]
        return ChatGift(_user(data), "Super Chat", 1, amount=_text(data.get("purchaseAmountText")),
                        message=_text(data.get("message")).strip())
    if "liveChatPaidStickerRenderer" in item:
        data = item["liveChatPaidStickerRenderer"]
        return ChatGift(_user(data), "Super Sticker", 1, amount=_text(data.get("purchaseAmountText")))
    if "liveChatMembershipItemRenderer" in item:
        data = item["liveChatMembershipItemRenderer"]
        title = _text(data.get("headerPrimaryText")) or "membership"  # "Member for 6 months" on milestones.
        return ChatGift(_user(data), title, 1, message=_text(data.get("message")).strip())
    if "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer" in item:
        data = item["liveChatSponsorshipsGiftPurchaseAnnouncementRenderer"]
        header = ((data.get("header") or {}).get("liveChatSponsorshipsHeaderRenderer")) or {}
        digits = re.search(r"\d+", _text(header.get("primaryText")))
        return ChatGift(_user({**header, "authorExternalChannelId": data.get("authorExternalChannelId")}),
                        "gift membership", int(digits.group(0)) if digits else 1)
    if "giftMessageViewModel" in item:  # Jewels gifts.
        data = item["giftMessageViewModel"]
        gift = _text(data.get("text")).removeprefix("sent ").strip() or "a gift"
        return ChatGift(_user(data), gift, 1, image_url=_photo(data.get("giftImage")))
    return None


def events_from(actions: list, now_ms: int) -> list[ChatEvent]:
    events = []
    for action in actions or []:
        item = ((action or {}).get("addChatItemAction") or {}).get("item")
        if isinstance(item, dict):
            event = event_from(item, now_ms)
            if event is not None:
                events.append(event)
    return events


def continuation_from(continuations: list) -> tuple[str, float] | None:
    """The next token and how long YouTube wants us to wait (seconds), if chat continues."""
    for entry in continuations or []:
        for value in (entry or {}).values():
            if isinstance(value, dict) and value.get("continuation"):
                wait = value.get("timeoutMs", 5000)
                return str(value["continuation"]), min(max(float(wait) / 1000, 1.0), 10.0)
    return None


def chat_page(html: str) -> tuple[str, str] | None:
    """(continuation, client version) from a ``/live_chat`` page, or None if the video has no live chat.

    Prefers the unfiltered "Live chat" view over the default "Top chat", which hides
    messages YouTube thinks are spam (that can include song requests). Its first
    response replays recent messages; the caller skips those.
    """
    match = _INITIAL_DATA.search(html)
    version = _CLIENT_VERSION.search(html)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    renderer = ((data.get("contents") or {}).get("liveChatRenderer")) or {}
    found = continuation_from(renderer.get("continuations") or [])
    header = ((renderer.get("header") or {}).get("liveChatHeaderRenderer")) or {}
    menu = (((header.get("viewSelector") or {}).get("sortFilterSubMenuRenderer")) or {}).get("subMenuItems") or []
    if found and len(menu) > 1 and isinstance(menu[1], dict):
        everything = continuation_from([menu[1].get("continuation") or {}])
        found = everything or found
    return (found[0], version.group(1) if version else "2.20250101.00.00") if found else None


def live_video(html: str) -> str | None:
    """The live video ID from a channel's ``/live`` page, or None if it isn't live right now."""
    match = _CANONICAL.search(html)
    return match.group(1) if match and '"isLiveNow":true' in html else None


class YouTubeChat:
    """Keeps one channel's live chat connected until ``stop()``. Same interface as TikTokChat."""

    def __init__(
        self,
        channel: str,
        on_event: Callable[[ChatEvent], None],
        on_status: Callable[[StatusChange], None],
        sleep: Callable[[float], object] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.video_id = video_id_from(channel) if "watch" in channel or "youtu.be" in channel or "/live/" in channel else None
        self.path = "" if self.video_id else channel_path(channel)
        if not self.video_id and not self.path:
            raise ValueError("Enter a YouTube @handle, channel link, or live video link.")
        self.name = self.path.removeprefix("/").removeprefix("channel/") if self.path else "this video"
        self._on_event = on_event
        self._on_status = on_status
        self._sleep = sleep
        self._clock = clock
        self._stopping = False
        self._failed = False
        self._wake = asyncio.Event()
        self._session: aiohttp.ClientSession | None = None

    def _status(self, state: ConnectionState, detail: str = "") -> None:
        self._on_status(StatusChange(SOURCE, state, detail))

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()

    async def _wait(self, seconds: float) -> None:
        """Sleep, but wake early when stopped."""
        try:
            await asyncio.wait_for(self._wake.wait(), seconds)
        except asyncio.TimeoutError:
            pass

    async def run(self) -> None:
        attempt = 0
        self._session = aiohttp.ClientSession(headers=HEADERS, timeout=aiohttp.ClientTimeout(total=20))
        try:
            while not self._stopping and not self._failed:
                self._status(ConnectionState.CONNECTING, f"Looking for {self.name}'s live stream…")
                try:
                    delay = await self._watch()
                    attempt = 0
                except YouTubeChatError as error:
                    self._status(ConnectionState.ERROR, str(error))
                    self._failed = True
                    break
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as error:
                    attempt += 1
                    delay = backoff_delay(attempt)
                    log.info("YouTube chat failed: %s", error)
                    self._status(ConnectionState.DEGRADED, f"Can't reach YouTube chat. Retrying in {int(delay)}s.")
                if not self._stopping:
                    await self._wait(delay)
        finally:
            await self._session.close()
            self._session = None
        if not self._failed:
            self._status(ConnectionState.OFFLINE)

    async def _watch(self) -> float:
        """Find the live video and read its chat until it ends; returns the wait before looking again."""
        video_id = self.video_id
        if video_id is None:
            status, html = await self._get(f"{BASE}{self.path}/live")
            if status == 404:
                raise YouTubeChatError(f"YouTube has no channel {self.name}.")
            video_id = live_video(html) if status == 200 else None
            if video_id is None:
                self._status(ConnectionState.DEGRADED, f"{self.name} is not live. Checking again in 60s.")
                return OFFLINE_POLL_SECONDS
        status, html = await self._get(f"{BASE}/live_chat?is_popout=1&v={video_id}")
        found = chat_page(html) if status == 200 else None
        if found is None:
            self._status(ConnectionState.DEGRADED, "That stream has no live chat (ended, or chat is off). Checking again in 60s.")
            return OFFLINE_POLL_SECONDS
        continuation, version = found
        self._status(ConnectionState.CONNECTED, f"Reading chat for {self.name}")
        backlog = True  # The first response replays recent messages: skip them, so old commands don't rerun.
        while not self._stopping:
            payload = await self._post(
                f"{BASE}/youtubei/v1/live_chat/get_live_chat?prettyPrint=false",
                {"context": {"client": {"clientName": "WEB", "clientVersion": version, "hl": "en"}},
                 "continuation": continuation},
            )
            chat = ((payload.get("continuationContents") or {}).get("liveChatContinuation")) or {}
            events = events_from(chat.get("actions") or [], int(self._clock() * 1000))
            if not backlog:
                for event in events:
                    self._on_event(event)
            backlog = False
            following = continuation_from(chat.get("continuations") or [])
            if following is None:
                self._status(ConnectionState.DEGRADED, f"{self.name}'s stream ended. Waiting for the next one.")
                return OFFLINE_POLL_SECONDS
            continuation, wait = following
            await self._wait(wait)
        return 0.0

    async def _get(self, url: str) -> tuple[int, str]:
        async with self._session.get(url) as response:
            return response.status, await response.text()

    async def _post(self, url: str, body: dict) -> dict:
        async with self._session.post(url, json=body) as response:
            if response.status != 200:
                raise aiohttp.ClientResponseError(response.request_info, (), status=response.status)
            payload = await response.json(content_type=None)
            return payload if isinstance(payload, dict) else {}
