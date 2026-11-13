"""TikTok LIVE chat adapter. The only module that imports TikTokLive.

TikTokLive is an unofficial, reverse-engineered client. Each connection asks
the Euler Stream signing service for a signed WebSocket URL, which is rate
limited. This adapter owns reconnection and turns library payloads into
``ignite_live.core.events`` types.

Licensing: TikTokLive's modified AGPL permits importing it into this desktop
app, but not running it inside a hosted service (its section 19). Keep this
module out of ``ignite_live.server``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from TikTokLive import TikTokLiveClient
from TikTokLive.client.errors import (
    AgeRestrictedError,
    SignAPIError,
    SignatureRateLimitError,
    UserNotFoundError,
    UserOfflineError,
    WebcastBlockedError,
)
from TikTokLive.client.web.web_settings import WebDefaults
from TikTokLive.events import (
    CommentEvent,
    ConnectEvent,
    FollowEvent,
    GiftEvent,
    JoinEvent,
    LikeEvent,
    LiveEndEvent,
    RoomUserSeqEvent,
    ShareEvent,
)

from ignite_live.core.backoff import MAX_BACKOFF_SECONDS, backoff_delay  # noqa: F401 (re-exported)
from ignite_live.core.events import (
    ChannelStats,
    ChatComment,
    ChatEvent,
    ChatFollow,
    ChatGift,
    ChatJoin,
    ChatLike,
    ChatShare,
    ChatUser,
    ConnectionState,
    StatusChange,
    ViewerCount,
)
from ignite_live.core.http import tls_context

log = logging.getLogger(__name__)

SOURCE = "tiktok"
OFFLINE_POLL_SECONDS = 60.0
ROOM_INFO_SECONDS = 300.0  # The follower count is read from the room's details this often.


def _count(value: Any) -> int | None:
    """A non-negative count from a payload field, or None when it's missing or not a number."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def room_stats(info: Any) -> ChannelStats | None:
    """Followers and likes from TikTok's room details (undocumented: every field is optional)."""
    if not isinstance(info, dict):
        return None
    owner = info.get("owner") if isinstance(info.get("owner"), dict) else {}
    follow = owner.get("follow_info") if isinstance(owner.get("follow_info"), dict) else {}
    stats = info.get("stats") if isinstance(info.get("stats"), dict) else {}
    followers = _count(follow.get("follower_count"))
    likes = _count(stats.get("like_count"))
    if likes is None:
        likes = _count(info.get("like_count"))
    if followers is None and likes is None:
        return None
    return ChannelStats(SOURCE, followers=followers, likes=likes)


def rate_limit_delay(error: SignatureRateLimitError, now: float) -> float:
    """Seconds until the signing service accepts requests again.

    Uses ``reset_time``: upstream's ``retry_after`` reads ``RateLimit-Remaining``,
    which is a request count, not a duration.
    """
    try:
        reset = float(error.reset_time)
    except (AssertionError, KeyError, TypeError, ValueError):
        return OFFLINE_POLL_SECONDS
    # Accept both a unix timestamp and a delta in seconds.
    delay = reset - now if reset > 1_000_000_000 else reset
    return min(max(delay, 5.0), 3600.0)


def _image_url(image: Any) -> str:
    """First URL of a TikTok ImageModel, or "" (payloads often omit images)."""
    urls = getattr(image, "url_list", None) or []
    return str(urls[0]) if urls else ""


def _user(raw: Any, identity: Any = None) -> ChatUser:
    unique_id = str(getattr(raw, "unique_id", "") or getattr(raw, "display_id", "") or "")
    return ChatUser(
        unique_id=unique_id,
        nickname=str(getattr(raw, "nickname", "") or unique_id),
        is_moderator=bool(getattr(identity, "is_moderator_of_anchor", False)),
        is_follower=bool(getattr(identity, "is_follower_of_anchor", False)),
        is_host=bool(getattr(identity, "is_anchor", False)),
        avatar_url=_image_url(getattr(raw, "avatar_thumb", None)),
    )


class TikTokChat:
    """Keeps one creator's LIVE chat connected until stopped.

    Run ``await chat.run()`` as a task and end it with ``await chat.stop()``.
    Don't cancel the task while connected: TikTokLive 7's cancellation handler
    calls ``loop.run_until_complete()`` inside the running loop, which raises,
    and would then cancel every other task on the loop.
    """

    def __init__(
        self,
        unique_id: str,
        on_event: Callable[[ChatEvent], None],
        on_status: Callable[[StatusChange], None],
        sign_api_key: str | None = None,
        client_factory: Callable[[str], Any] | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.unique_id = "@" + unique_id.strip().lstrip("@")
        if self.unique_id == "@":
            raise ValueError("Enter a TikTok username.")
        self._on_event = on_event
        self._on_status = on_status
        if sign_api_key:
            WebDefaults.tiktok_sign_api_key = sign_api_key
        self._client_factory = client_factory or (lambda uid: TikTokLiveClient(unique_id=uid, ws_kwargs={"ssl": tls_context()}))
        self._sleep = sleep
        self._clock = clock
        self._connected_at_ms = 0
        self._client: Any = None
        self._stopping = False
        self._room_task: asyncio.Task | None = None
        self._likes = -1  # The stream's like total as last reported, so it's sent on only when it changes.

    def _status(self, state: ConnectionState, detail: str = "") -> None:
        self._on_status(StatusChange(SOURCE, state, detail))

    async def stop(self) -> None:
        """Disconnect gracefully; ``run()`` then returns on its own."""
        self._stopping = True
        if self._client is not None:
            await self._quietly_disconnect(self._client)

    async def run(self) -> None:
        attempt = 0
        while not self._stopping:
            client = self._client = self._client_factory(self.unique_id)
            self._register(client)
            self._status(ConnectionState.CONNECTING, f"Connecting to {self.unique_id}…")
            try:
                await client.connect(fetch_live_check=True)
                if self._stopping:
                    break
                attempt = 0
                delay = OFFLINE_POLL_SECONDS
                self._status(ConnectionState.DEGRADED, f"{self.unique_id} ended the LIVE. Waiting for the next one.")
            except UserOfflineError:
                attempt = 0
                delay = OFFLINE_POLL_SECONDS
                self._status(ConnectionState.DEGRADED, f"{self.unique_id} is not live. Checking again in 60s.")
            except UserNotFoundError:
                self._status(ConnectionState.ERROR, f"TikTok has no user {self.unique_id}.")
                return
            except AgeRestrictedError:
                self._status(ConnectionState.ERROR, "This LIVE is age-restricted and can't be read anonymously.")
                return
            except SignatureRateLimitError as error:
                delay = rate_limit_delay(error, self._clock())
                self._status(ConnectionState.DEGRADED, f"Chat signing is rate limited. Retrying in {int(delay)}s.")
            except (SignAPIError, WebcastBlockedError):
                attempt += 1
                delay = backoff_delay(attempt)
                self._status(ConnectionState.DEGRADED, f"TikTok or the signing service refused the connection. Retrying in {int(delay)}s.")
            except asyncio.CancelledError:
                await self._quietly_disconnect(client)
                self._status(ConnectionState.OFFLINE)
                raise
            except Exception:
                task = asyncio.current_task()
                if self._stopping or (task is not None and task.cancelling()):
                    # TikTokLive can turn a cancellation into a RuntimeError; never retry then.
                    await self._quietly_disconnect(client)
                    self._status(ConnectionState.OFFLINE)
                    if self._stopping:
                        return
                    raise asyncio.CancelledError from None
                log.warning("Chat connection failed", exc_info=True)
                attempt += 1
                delay = backoff_delay(attempt)
                self._status(ConnectionState.DEGRADED, f"Chat connection lost. Retrying in {int(delay)}s.")
            self._stop_room_info()
            await self._quietly_disconnect(client)
            if self._stopping:
                break
            await self._sleep(delay)
        self._stop_room_info()
        self._client = None
        self._status(ConnectionState.OFFLINE)

    def _stop_room_info(self) -> None:
        if self._room_task is not None:
            self._room_task.cancel()  # Our own task (plain HTTP), safe to cancel: not the library's connection.
            self._room_task = None

    async def _watch_room_info(self, client: Any) -> None:
        """Follower count (and the like total so far) from the room's details. Optional: TikTok refuses it
        for age-restricted streams, and then the numbers are simply left out."""
        while not self._stopping and self._client is client:
            try:
                stats = room_stats(await client.web.fetch_room_info())
            except asyncio.CancelledError:
                raise
            except Exception as error:  # Unofficial API: any failure just means no follower count.
                log.info("TikTok room details unavailable: %s", type(error).__name__)
                return
            if stats is not None and self._client is client:
                self._on_event(stats)
            await asyncio.sleep(ROOM_INFO_SECONDS)

    @staticmethod
    async def _quietly_disconnect(client: Any) -> None:
        try:
            if getattr(client, "connected", False):
                await client.disconnect()
        except Exception:
            pass

    def _register(self, client: Any) -> None:
        client.add_listener(ConnectEvent, self._handle_connect)
        client.add_listener(CommentEvent, self._handle_comment)
        client.add_listener(GiftEvent, self._handle_gift)
        client.add_listener(FollowEvent, self._handle_follow)
        client.add_listener(LikeEvent, self._handle_like)
        client.add_listener(ShareEvent, self._handle_share)
        client.add_listener(JoinEvent, self._handle_join)
        client.add_listener(RoomUserSeqEvent, self._handle_viewers)
        client.add_listener(LiveEndEvent, self._handle_live_end)

    async def _handle_connect(self, _event: Any) -> None:
        if self._stopping:  # stop() ran while this connection was being set up.
            asyncio.get_running_loop().create_task(self._quietly_disconnect(self._client))
            return
        self._connected_at_ms = int(self._clock() * 1000)
        self._status(ConnectionState.CONNECTED, f"Reading chat for {self.unique_id}")
        self._likes = -1
        self._stop_room_info()
        if getattr(self._client, "web", None) is not None:
            self._room_task = asyncio.get_running_loop().create_task(self._watch_room_info(self._client))

    async def _handle_comment(self, event: Any) -> None:
        created = int(getattr(getattr(event, "common", None), "create_time", 0) or 0)
        # TikTok replays recent comments right after connecting. Dropping them
        # stops a reconnect from re-running old !sr / !skip commands.
        if created and created < self._connected_at_ms:
            return
        text = str(getattr(event, "content", "") or "")
        if text:
            user = _user(event.user, getattr(event, "user_identity", None))
            self._on_event(ChatComment(user, text, created or int(self._clock() * 1000)))

    async def _handle_gift(self, event: Any) -> None:
        # Streakable gifts fire on every combo tick: each one floods the chat, and only the last (the total) counts.
        self._on_event(
            ChatGift(
                _user(event.user, getattr(event, "user_identity", None)),
                str(event.gift.name),
                int(event.repeat_count or 1),
                _image_url(getattr(event.gift, "image", None)),
                int(getattr(event.gift, "diamond_count", 0) or 0),
                streaking=bool(event.gift.streakable and event.streaking),
            )
        )

    async def _handle_follow(self, event: Any) -> None:
        self._on_event(ChatFollow(_user(event.user)))

    async def _handle_like(self, event: Any) -> None:
        count = int(getattr(event, "count", 0) or 0)
        if count > 0:
            self._on_event(ChatLike(_user(event.user), count))
        total = _count(getattr(event, "total", None))
        if total and total != self._likes:  # The stream's running total, for the stats row.
            self._likes = total
            self._on_event(ChannelStats(SOURCE, likes=total))

    async def _handle_share(self, event: Any) -> None:
        self._on_event(ChatShare(_user(event.user)))

    async def _handle_join(self, event: Any) -> None:
        raw = getattr(event, "user", None)
        if raw is not None:
            user = _user(raw, getattr(event, "user_identity", None))
            if user.unique_id:
                self._on_event(ChatJoin(user))

    async def _handle_viewers(self, event: Any) -> None:
        self._on_event(ViewerCount(int(getattr(event, "total", 0) or 0)))

    async def _handle_live_end(self, _event: Any) -> None:
        self._status(ConnectionState.DEGRADED, f"{self.unique_id} ended the LIVE.")
