"""Typed events shared by every integration.

Adapters translate third-party payloads into these, so the UI, the command
engine, and the overlays never import TikTokLive, aiohttp, or YouTube types.
Every chat event says which platform it came from (``ChatUser.platform``).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

# Chat platforms: key -> display name.
PLATFORMS = {"tiktok": "TikTok", "twitch": "Twitch", "youtube": "YouTube"}


class ConnectionState(enum.Enum):
    OFFLINE = "offline"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    # Up but impaired: e.g. waiting for the creator to go live, or backing off.
    DEGRADED = "degraded"
    ERROR = "error"


@dataclass(frozen=True)
class StatusChange:
    source: str
    state: ConnectionState
    detail: str = ""


@dataclass(frozen=True)
class ChatUser:
    unique_id: str
    nickname: str
    is_moderator: bool = False
    is_follower: bool = False
    is_host: bool = False
    avatar_url: str = ""
    platform: str = "tiktok"
    is_subscriber: bool = False  # Twitch subscriber or VIP, YouTube channel member.

    @property
    def key(self) -> str:
        """Unique across platforms: the same name on Twitch and TikTok is two people."""
        return f"{self.platform}:{self.unique_id}"

    @property
    def platform_name(self) -> str:
        return PLATFORMS.get(self.platform, self.platform)


@dataclass(frozen=True)
class ChatComment:
    user: ChatUser
    text: str
    created_ms: int


@dataclass(frozen=True)
class ChatGift:
    user: ChatUser
    gift_name: str
    count: int
    image_url: str = ""
    diamonds: int = 0  # Value of one gift; the total is diamonds * count.
    amount: str = ""  # Money shown as text, e.g. a YouTube Super Chat's "$5.00".
    message: str = ""  # Text sent with it (Super Chat, cheer, resub message).


@dataclass(frozen=True)
class ChatFollow:
    user: ChatUser


@dataclass(frozen=True)
class ChatLike:
    """TikTok sends likes in batches: ``count`` is this batch, not the stream total."""

    user: ChatUser
    count: int


@dataclass(frozen=True)
class ChatShare:
    user: ChatUser


@dataclass(frozen=True)
class ViewerCount:
    count: int
    platform: str = "tiktok"


@dataclass(frozen=True)
class LiveStatus:
    """Whether a channel is live, for platforms whose chat also works offline (Twitch)."""

    platform: str
    live: bool


ChatEvent = ChatComment | ChatGift | ChatFollow | ChatLike | ChatShare | ViewerCount | LiveStatus
