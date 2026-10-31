"""Loyalty points: how viewers earn them and what things cost. Pure logic over ``ViewerStore``.

Settings live in ``Settings.points`` (one dict, synced and part of profiles); balances
live in the viewer store (local only). TikTok and Twitch don't say who's watching
silently, so "being around" means having commented, liked, or gifted recently.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from ignite_live.actions.rules import SOUND_NAME
from ignite_live.core.events import ChatComment, ChatEvent, ChatFollow, ChatGift, ChatLike, ChatShare, ChatUser
from ignite_live.viewers.store import ViewerStore

# key -> (default, highest). Costs of 0 mean "free" (or, for the paid-only commands, "off").
NUMBERS = {
    "active": (10, 100_000),  # Points for being around, every ACTIVE_EVERY_MIN minutes.
    "chat": (1, 100_000),  # Per message, at most once per CHAT_COOLDOWN_S.
    "per_coin": (1, 100_000),
    "follow": (50, 1_000_000),  # Once per stream.
    "share": (20, 1_000_000),  # Once per stream.
    "cost_request": (0, 10_000_000),
    "cost_bump": (0, 10_000_000),
    "cost_say": (0, 10_000_000),
    "cost_sound": (0, 10_000_000),
}
ACTIVE_EVERY_MIN = 5
ACTIVE_WINDOW_MIN = 10
CHAT_COOLDOWN_S = 30


@dataclass(frozen=True)
class PointsConfig:
    enabled: bool = False
    currency: str = "points"
    active: int = 10
    chat: int = 1
    per_coin: int = 1
    follow: int = 50
    share: int = 20
    cost_request: int = 0
    cost_bump: int = 0
    cost_say: int = 0
    cost_sound: int = 0
    sound_prices: dict = field(default_factory=dict)  # sound file name -> price (instead of cost_sound)

    def sound_price(self, name: str) -> int:
        return self.sound_prices.get(name, self.cost_sound)


def parse_config(raw: object) -> PointsConfig:
    raw = raw if isinstance(raw, dict) else {}
    values: dict = {}
    for key, (default, high) in NUMBERS.items():
        value = raw.get(key, default)
        values[key] = int(max(0, min(high, value))) if isinstance(value, (int, float)) and not isinstance(value, bool) \
            else default
    currency = raw.get("currency")
    prices = raw.get("sound_prices")
    return PointsConfig(
        enabled=raw.get("enabled") is True,
        currency=" ".join(currency.split())[:30] if isinstance(currency, str) and currency.strip() else "points",
        sound_prices={name: int(max(0, min(10_000_000, price))) for name, price in prices.items()
                      if isinstance(name, str) and SOUND_NAME.match(name)
                      and isinstance(price, (int, float)) and not isinstance(price, bool)}
        if isinstance(prices, dict) else {},
        **values,
    )


def config_to_dict(config: PointsConfig) -> dict:
    return dict(config.__dict__)


def clean_config(raw: object) -> dict:
    return config_to_dict(parse_config(raw))


class Points:
    """Counts what viewers do (always, for leaderboards) and awards points (when turned on)."""

    def __init__(self, store: ViewerStore, config: PointsConfig | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.store = store
        self.config = config or PointsConfig()
        self._clock = clock
        self._active: dict[str, tuple[ChatUser, float]] = {}  # key -> (user, last activity)
        self._last_chat: dict[str, float] = {}
        self._bonus: set[tuple[str, str]] = set()  # (kind, key): follow/share bonuses given this stream.
        self._last_award: float | None = None

    def reset_stream(self) -> None:
        self._bonus.clear()
        self._last_award = None

    def feed(self, event: ChatEvent) -> None:
        user = getattr(event, "user", None)
        if user is None or not isinstance(event, (ChatComment, ChatGift, ChatLike, ChatFollow, ChatShare)):
            return
        now, c = self._clock(), self.config
        earn = c.enabled
        if isinstance(event, ChatComment):
            points = 0
            if earn and now - self._last_chat.get(user.key, float("-inf")) >= CHAT_COOLDOWN_S:
                self._last_chat[user.key] = now
                points = c.chat
            self.store.add(user, messages=1, points=points)
        elif isinstance(event, ChatGift):
            coins = event.diamonds * event.count
            self.store.add(user, coins=coins, points=coins * c.per_coin if earn else 0)
        elif isinstance(event, ChatLike):
            self.store.add(user, likes=event.count)
        else:
            kind = "follow" if isinstance(event, ChatFollow) else "share"
            bonus = 0
            if earn and (kind, user.key) not in self._bonus:
                self._bonus.add((kind, user.key))
                bonus = c.follow if kind == "follow" else c.share
            self.store.add(user, points=bonus)
            if kind == "follow":
                return  # Following isn't "being around" by itself.
        self._active[user.key] = (user, now)

    def tick(self, live: bool) -> int:
        """Call about once a minute: every ACTIVE_EVERY_MIN minutes of a live stream, active viewers earn points.

        Returns how many viewers were paid.
        """
        now = self._clock()
        cutoff = now - ACTIVE_WINDOW_MIN * 60
        self._active = {key: entry for key, entry in self._active.items() if entry[1] >= cutoff}
        if not live:
            self._last_award = None
            return 0
        if self._last_award is None:
            self._last_award = now
            return 0
        if now - self._last_award < ACTIVE_EVERY_MIN * 60:
            return 0
        self._last_award = now
        for user, _seen in self._active.values():
            self.store.add(user, minutes=ACTIVE_EVERY_MIN, points=self.config.active if self.config.enabled else 0)
        return len(self._active)

    def balance(self, user: ChatUser) -> int:
        return self.store.balance(user.key)

    def spend(self, user: ChatUser, cost: int) -> bool:
        """Take ``cost`` points if the viewer has them."""
        if cost <= 0:
            return True
        if self.store.balance(user.key) < cost:
            return False
        self.store.add(user, points=-cost)
        return True

    def refund(self, user: ChatUser, cost: int) -> None:
        if cost > 0:
            self.store.add(user, points=cost)
