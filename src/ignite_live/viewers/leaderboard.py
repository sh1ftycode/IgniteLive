"""Leaderboards: top gifters, likers and chatters (this stream and all time) and top points. Pure logic over the store.

This stream's boards are counted here as events arrive; all-time boards come from
the viewer store. When first place changes, a ``LeaderChanged`` goes to the
overlays and the action engine ("New #1" rules).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from ignite_live.core.events import PLATFORMS, ChatComment, ChatEvent, ChatGift, ChatLike, ChatUser
from ignite_live.viewers.store import ViewerStore

# board id -> (label, unit, store counter)
BOARDS = {
    "gifters:stream": ("Top gifters", "coins", "coins"),
    "gifters:alltime": ("Top gifters of all time", "coins", "coins"),
    "likers:stream": ("Top likers", "likes", "likes"),
    "likers:alltime": ("Top likers of all time", "likes", "likes"),
    "chatters:stream": ("Top chatters", "messages", "messages"),
    "chatters:alltime": ("Top chatters of all time", "messages", "messages"),
    "points:alltime": ("Most points", "points", "points"),
}
ROWS = 10


@dataclass(frozen=True)
class LeaderChanged:
    board: str
    user: ChatUser
    value: int

    @property
    def label(self) -> str:
        return BOARDS[self.board][0]


class Leaderboards:
    def __init__(self, store: ViewerStore) -> None:
        self.store = store
        self.currency = "points"
        self.boards: dict[str, list[dict]] = {board: [] for board in BOARDS}
        self._users: dict[str, ChatUser] = {}
        self.reset_stream()

    def reset_stream(self) -> None:
        self._stream = {"gifters": Counter(), "likers": Counter(), "chatters": Counter()}
        for board in BOARDS:
            if board.endswith(":stream"):
                self.boards[board] = []

    def feed(self, event: ChatEvent) -> list[LeaderChanged]:
        """Count a real (not simulated) event; returns any change of first place on this stream's boards."""
        user = getattr(event, "user", None)
        if user is None:
            return []
        if isinstance(event, ChatGift) and event.diamonds:
            name, amount = "gifters", event.diamonds * event.count
        elif isinstance(event, ChatLike):
            name, amount = "likers", event.count
        elif isinstance(event, ChatComment):
            name, amount = "chatters", 1
        else:
            return []
        self._users[user.key] = user
        self._stream[name][user.key] += amount
        board = f"{name}:stream"
        rows = [self._row(key, value) for key, value in self._stream[name].most_common(ROWS)]
        return self._set(board, rows)

    def refresh_alltime(self, announce: bool = True) -> list[LeaderChanged]:
        """Re-read the all-time boards from the store (call after it's flushed).

        ``announce=False`` for the first read when the app starts: that isn't anyone taking first place.
        """
        changes = []
        for board, (_label, _unit, counter) in BOARDS.items():
            if board.endswith(":alltime"):
                rows = []
                for viewer in self.store.top(counter, ROWS):
                    user = ChatUser(viewer.unique_id, viewer.name, platform=viewer.platform)
                    self._users.setdefault(user.key, user)
                    rows.append(self._row(user.key, getattr(viewer, counter)))
                changes += self._set(board, rows)
        return changes if announce else []

    def _row(self, key: str, value: int) -> dict:
        user = self._users[key]
        return {"key": key, "name": (user.nickname or user.unique_id)[:40], "platform": user.platform, "value": value}

    def _set(self, board: str, rows: list[dict]) -> list[LeaderChanged]:
        before = self.boards[board][0]["key"] if self.boards[board] else None
        self.boards[board] = rows
        # Ties keep the current leader (Counter and the store both list the earlier one first).
        if rows and rows[0]["key"] != before:
            return [LeaderChanged(board, self._users[rows[0]["key"]], rows[0]["value"])]
        return []

    def snapshot(self) -> dict:
        """For the leaderboard overlay: every board with its title and unit."""
        return {board: {"title": label, "unit": self.currency if unit == "points" else unit,
                        "rows": [{"name": row["name"], "platform": row["platform"], "value": row["value"],
                                  "platform_name": PLATFORMS.get(row["platform"], row["platform"])}
                                 for row in self.boards[board]]}
                for board, (label, unit, _counter) in BOARDS.items()}
