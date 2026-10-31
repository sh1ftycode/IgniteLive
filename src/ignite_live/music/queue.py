"""Song request queue. Pure logic, no I/O: the player drives ``advance()``."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Callable

from ignite_live.core.events import ChatUser
from ignite_live.core.templates import Texts
from ignite_live.music.youtube import Track, is_explicit


class QueueRejected(Exception):
    """Why a request was refused, worded for the requester. ``key`` is the template (which rule refused it)."""

    def __init__(self, text: str, key: str = "") -> None:
        super().__init__(text)
        self.key = key


@dataclass(frozen=True)
class QueueItem:
    id: int
    track: Track
    requested_by: ChatUser


@dataclass
class QueueRules:
    max_size: int = 25
    max_per_user: int = 2
    max_duration_s: int = 600
    banned_video_ids: set[str] = field(default_factory=set)
    texts: Texts = field(default_factory=Texts)
    refuse_unknown_length: bool = False
    refuse_explicit: bool = False
    # Moderation: whether a title (or request) has a blocked word in it. None: no word filter for songs.
    blocked: Callable[[str], bool] | None = None


class SongQueue:
    def __init__(
        self,
        rules: QueueRules | None = None,
        on_change: Callable[["SongQueue"], None] | None = None,
    ) -> None:
        self.rules = rules or QueueRules()
        self.current: QueueItem | None = None
        self.upcoming: list[QueueItem] = []
        self.on_change = on_change
        self._ids = itertools.count(1)

    def add(self, track: Track, user: ChatUser, privileged: bool = False) -> int:
        """Queue a track and return its position (1 = plays next)."""
        rules = self.rules
        if track.video_id in rules.banned_video_ids:
            raise self._rejected("queue.banned")
        if track.embeddable is False:
            raise self._rejected("queue.not_embeddable")
        if track.age_restricted:
            raise self._rejected("queue.age_restricted")
        if track.duration_s is not None and track.duration_s > rules.max_duration_s:
            raise self._rejected("queue.too_long", minutes=rules.max_duration_s // 60)
        if track.duration_s is None and rules.refuse_unknown_length:
            raise self._rejected("queue.unknown_length", minutes=rules.max_duration_s // 60)
        if rules.refuse_explicit and is_explicit(track):
            raise self._rejected("queue.explicit")
        if rules.blocked and rules.blocked(f"{track.title} {track.channel}"):
            raise self._rejected("queue.blocked_word")
        if any(item.track.video_id == track.video_id for item in self._all()):
            raise self._rejected("queue.duplicate")
        if len(self.upcoming) >= rules.max_size:
            raise self._rejected("queue.full")
        mine = sum(1 for item in self._all() if item.requested_by.key == user.key)
        if not privileged and mine >= rules.max_per_user:
            raise self._rejected("queue.per_user", count=mine)
        self.upcoming.append(QueueItem(next(self._ids), track, user))
        position = len(self.upcoming)
        if self.current is None:
            self.advance()
            position = 0
        else:
            self._changed()
        return position

    def _rejected(self, key: str, **values) -> QueueRejected:
        return QueueRejected(self.rules.texts.format(key, **values), key)

    def advance(self) -> QueueItem | None:
        """Move to the next song (called when the player finishes or skips)."""
        self.current = self.upcoming.pop(0) if self.upcoming else None
        self._changed()
        return self.current

    def skip(self, user: ChatUser, privileged: bool) -> QueueItem | None:
        if self.current is None:
            raise self._rejected("queue.nothing_playing")
        if not privileged and self.current.requested_by.key != user.key:
            raise self._rejected("queue.not_yours")
        return self.advance()

    def revoke(self, user: ChatUser) -> QueueItem:
        """Remove the user's most recent upcoming request."""
        for index in range(len(self.upcoming) - 1, -1, -1):
            if self.upcoming[index].requested_by.key == user.key:
                item = self.upcoming.pop(index)
                self._changed()
                return item
        raise self._rejected("queue.no_upcoming")

    def bump(self, user: ChatUser) -> QueueItem:
        """Move the user's first upcoming request to play next (a points reward)."""
        for index, item in enumerate(self.upcoming):
            if item.requested_by.key == user.key:
                if index:
                    self.upcoming.insert(0, self.upcoming.pop(index))
                    self._changed()
                return item
        raise self._rejected("queue.no_upcoming")

    def clear(self) -> None:
        """Drop upcoming songs; the current one keeps playing."""
        self.upcoming = []
        self._changed()

    def remove(self, item_id: int) -> None:
        self.upcoming = [item for item in self.upcoming if item.id != item_id]
        self._changed()

    def snapshot(self) -> dict:
        def item(entry: QueueItem) -> dict:
            return {
                "id": entry.id,
                "video_id": entry.track.video_id,
                "title": entry.track.title,
                "channel": entry.track.channel,
                "duration_s": entry.track.duration_s,
                "image": entry.track.cover if entry.track.cover.startswith("https://") else "",
                "requested_by": entry.requested_by.nickname,
            }

        return {
            "current": item(self.current) if self.current else None,
            "upcoming": [item(entry) for entry in self.upcoming],
        }

    def _all(self) -> list[QueueItem]:
        return ([self.current] if self.current else []) + self.upcoming

    def _changed(self) -> None:
        if self.on_change:
            self.on_change(self)
