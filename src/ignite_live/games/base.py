"""What every viewer game shares: settings validation, the result, and the running state. Pure logic, no I/O.

One game runs at a time (games/manager.py). Games see simulated events too (that's
how the Test buttons work); points, reports and leaderboards don't.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from ignite_live.core.events import ChatEvent, ChatUser

GAMES = {"tug": "Tug-of-war", "boss": "Boss fight", "poll": "Chat poll"}
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
# Boss pictures live in <config>/images and are served by name, like sounds.
IMAGE_NAME = re.compile(r"^[\w][\w .()'&,+-]{0,78}\.(?:png|jpe?g|gif|webp)$", re.IGNORECASE)


@dataclass(frozen=True)
class GameEnded:
    """A game (or a boss round) is over. Fed to the action engine ("Game ended" rules)."""

    game: str  # GAMES key
    winner: str  # Team name, poll option, "Chat" (boss defeated), the boss's name (it got away), or "Nobody".
    mvp: ChatUser | None = None  # Who did the most: pulled hardest, dealt most damage.
    mvp_amount: int = 0
    final: bool = True  # False for a boss round: the next boss appears straight away.

    @property
    def label(self) -> str:
        return GAMES[self.game]


def text(raw: dict, key: str, default: str, limit: int = 60) -> str:
    value = raw.get(key)
    return " ".join(value.split())[:limit] if isinstance(value, str) and value.strip() else default


def number(raw: dict, key: str, default: int | float, low: int | float, high: int | float, kind=int):
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return kind(max(low, min(high, value)))


def color(raw: dict, key: str, default: str) -> str:
    value = raw.get(key)
    return value if isinstance(value, str) and _COLOR.match(value) else default


def names(raw: dict, key: str, limit: int = 20) -> list[str]:
    value = raw.get(key)
    if not isinstance(value, list):
        return []
    found = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip()[:60] not in found:
            found.append(item.strip()[:60])
    return found[:limit]


def display(user: ChatUser) -> str:
    return (user.nickname or user.unique_id)[:40]


class Game:
    """A running game. Subclasses: tug.TugOfWar, boss.BossFight, poll.Poll."""

    kind = ""

    def __init__(self, config: dict, clock: Callable[[], float], prefix: str = "!") -> None:
        self.config = config
        self._clock = clock
        self.prefix = prefix
        self.started = clock()
        self.seconds = int(config.get("seconds", 0) or 0)
        self.contributions: Counter = Counter()  # user key -> amount (pull, damage)
        self.users: dict[str, ChatUser] = {}
        self.changed = False  # Whether the last feed() changed what the overlay shows.

    def remaining(self) -> int | None:
        if not self.seconds:
            return None
        return max(0, int(round(self.started + self.seconds - self._clock())))

    def credit(self, user: ChatUser, amount: int) -> None:
        self.users[user.key] = user
        self.contributions[user.key] += amount

    def mvp(self) -> tuple[ChatUser | None, int]:
        if not self.contributions:
            return None, 0
        key, amount = self.contributions.most_common(1)[0]
        return self.users[key], amount

    def top(self, count: int = 3) -> list[dict]:
        return [{"name": display(self.users[key]), "value": amount} for key, amount in self.contributions.most_common(count)]

    def feed(self, event: ChatEvent) -> list[GameEnded]:
        """Returns results (a boss round, or the end); ``changed`` says whether the overlay should update."""
        raise NotImplementedError

    def tick(self) -> GameEnded | None:
        """Once a second: the end when the timer runs out."""
        remaining = self.remaining()
        return self.finish() if remaining == 0 else None

    def finish(self) -> GameEnded:
        """Stopped (by the timer or the streamer): who won."""
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError
