"""Who's here right now, and the channel numbers each platform publishes. Pure logic, no I/O.

No platform hands an anonymous reader its full viewer list, so "who's watching"
is put together from what each one does say:

- TikTok announces viewers as they join, and everyone who chats, gifts, likes,
  follows or shares. It never says who left.
- Twitch lists who is in chat (up to about 1,000 names) and says when they
  join and leave. Logged-out viewers are never listed.
- YouTube only shows who chats.

So a viewer is listed while Twitch says they're in chat, or for ``IDLE_SECONDS``
after they were last seen doing something. The viewer *counts* next to the list
are the platforms' own numbers and are usually higher than the list.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from ignite_live.core.events import PLATFORMS, ChannelStats, ChatUser

IDLE_SECONDS = 600.0  # The same "around lately" window loyalty points use.
MAX_LISTED = 3000  # Across all platforms; the longest-idle are dropped first.
STAT_KEYS = ("viewers", "followers", "likes")


@dataclass
class Presence:
    user: ChatUser
    last_seen: float
    in_chat: bool = False  # Twitch says they're in the room: listed until it says they left.
    active: bool = False  # Has chatted, gifted, liked, followed or shared (not just arrived).


class Audience:
    def __init__(self, clock: Callable[[], float] = time.monotonic, idle_s: float = IDLE_SECONDS,
                 max_listed: int = MAX_LISTED) -> None:
        self._clock = clock
        self.idle_s = idle_s
        self.max_listed = max_listed
        self._people: dict[str, Presence] = {}
        self.stats: dict[str, dict[str, int | None]] = {p: dict.fromkeys(STAT_KEYS) for p in PLATFORMS}

    # Who's here.

    def seen(self, user: ChatUser, joined: bool = False) -> bool:
        """Someone arrived (``joined``) or did something. Returns whether the list changed."""
        if not user.unique_id or user.platform not in PLATFORMS:
            return False
        now = self._clock()
        known = self._people.get(user.key)
        if known is None:
            self._people[user.key] = Presence(user, now, in_chat=joined and user.platform == "twitch", active=not joined)
            if len(self._people) > self.max_listed:
                self._trim()
            return True
        changed = False
        if joined:
            if user.platform == "twitch" and not known.in_chat:
                known.in_chat = changed = True
        else:
            if not known.active:
                known.active = changed = True
            # A chat line knows more than a name in a member list (display name, badges, picture).
            if user != known.user:
                known.user, changed = user, True
        known.last_seen = now
        return changed

    def left(self, user: ChatUser) -> bool:
        return self._people.pop(user.key, None) is not None

    def clear(self, platform: str) -> bool:
        """Chat for a platform disconnected: nobody from there is known to be here any more."""
        gone = [key for key, presence in self._people.items() if presence.user.platform == platform]
        for key in gone:
            del self._people[key]
        return bool(gone)

    def prune(self) -> bool:
        """Drop everyone not seen for a while (unless Twitch still lists them)."""
        cutoff = self._clock() - self.idle_s
        gone = [key for key, presence in self._people.items() if not presence.in_chat and presence.last_seen < cutoff]
        for key in gone:
            del self._people[key]
        return bool(gone)

    def _trim(self) -> None:
        extra = len(self._people) - self.max_listed
        for key, _presence in sorted(self._people.items(), key=lambda entry: entry[1].last_seen)[:extra]:
            del self._people[key]

    def snapshot(self) -> list[dict]:
        """Everyone listed, hosts and moderators first, then the most recently seen."""
        now = self._clock()
        people = sorted(self._people.values(),
                        key=lambda p: (not p.user.is_host, not p.user.is_moderator, -p.last_seen))
        return [{"user": p.user, "active": p.active, "in_chat": p.in_chat, "idle_s": max(0.0, now - p.last_seen)}
                for p in people]

    def listed(self) -> dict[str, int]:
        counts = dict.fromkeys(PLATFORMS, 0)
        for presence in self._people.values():
            counts[presence.user.platform] += 1
        return counts

    # Channel numbers.

    def update(self, change: ChannelStats) -> bool:
        """Take in the fields that are set. Returns whether anything changed."""
        row = self.stats.get(change.platform)
        if row is None:
            return False
        changed = False
        for key in STAT_KEYS:
            value = getattr(change, key)
            if value is not None and value != row[key]:
                row[key] = max(0, int(value))
                changed = True
        return changed

    def offline(self, platform: str) -> bool:
        """Not live (or not connected) there: no viewers and no stream likes; the follower count stays."""
        row = self.stats.get(platform)
        if row is None or (row["viewers"] is None and row["likes"] is None):
            return False
        row["viewers"] = row["likes"] = None
        return True

    def stats_snapshot(self) -> dict:
        """``{"platforms": {platform: {viewers, followers, likes, listed}}, "total": {...}}``.

        A total is None when no platform has reported that number (not the same as zero).
        """
        listed = self.listed()
        platforms = {p: {**row, "listed": listed[p]} for p, row in self.stats.items()}
        total: dict[str, int | None] = {}
        for key in (*STAT_KEYS, "listed"):
            values = [row[key] for row in platforms.values() if row[key] is not None]
            total[key] = sum(values) if values else None
        return {"platforms": platforms, "total": total}


def compact(number: int | None) -> str:
    """1234 -> "1,234", 15_800_000 -> "15.8M"; None -> "–" (a number nobody has told us)."""
    if number is None:
        return "–"
    if number < 10_000:
        return f"{number:,}"
    for size, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if number >= size:
            text = f"{number / size:.1f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return str(number)
