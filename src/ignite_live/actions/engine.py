"""Matches chat events against the streamer's rules. Pure logic, no I/O.

The controller feeds every event to ``ActionEngine.match`` and runs the actions of
what fired. Stream-scoped counters (like totals, viewer milestones) start over
with ``reset()`` when a new LIVE connects.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable

from ignite_live.actions.goals import GoalReached
from ignite_live.actions.rules import SPIKE_WINDOW_S, Rule
from ignite_live.core.events import (
    PLATFORMS,
    ChatComment,
    ChatEvent,
    ChatFollow,
    ChatGift,
    ChatLike,
    ChatShare,
    ChatUser,
    ViewerCount,
)
from ignite_live.music.commands import Role, role_of
from ignite_live.games.base import GameEnded
from ignite_live.viewers.leaderboard import LeaderChanged


# A spike needs this much of the stream behind it to know what "usual" is.
SPIKE_WARMUP_S = 120.0
# A spike lasts a while: don't fire again for at least this long, whatever the rule's own cooldown.
SPIKE_MIN_COOLDOWN_S = 30


@dataclass(frozen=True)
class Fired:
    rule: Rule
    values: dict  # Template values for the action texts.
    user: ChatUser | None  # None for viewer milestones.
    image: str = ""  # The gift's picture, for alerts.


def _kind(event: ChatEvent) -> str | None:
    if isinstance(event, ChatGift):
        return "gift"
    if isinstance(event, ChatFollow):
        return "follow"
    if isinstance(event, ChatLike):
        return "like"
    if isinstance(event, ChatShare):
        return "share"
    if isinstance(event, ChatComment):
        return "command"
    if isinstance(event, ViewerCount):
        return "viewers"
    if isinstance(event, GoalReached):
        return "goal"
    if isinstance(event, LeaderChanged):
        return "leader"
    if isinstance(event, GameEnded):
        return "game_end"
    return None


class ActionEngine:
    def __init__(
        self,
        rules: Iterable[Rule] = (),
        prefix: str = "!",
        reserved: Iterable[str] = (),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._last_rule: dict[str, float] = {}
        self._last_user: dict[tuple[str, str], float] = {}
        self.update(rules, prefix, reserved)
        self.reset()

    def update(self, rules: Iterable[Rule], prefix: str = "!", reserved: Iterable[str] = ()) -> None:
        """``reserved``: built-in command names (``sr``, ``skip``, …); rules can't take them over."""
        self.rules = list(rules)
        self.prefix = prefix or "!"
        self.reserved = {name.lower() for name in reserved}

    def reset(self) -> None:
        """A new stream: like totals and viewer milestones start over."""
        self.likes: dict[str, int] = {}
        self._peak_viewers: dict[str, int] = {}
        self._milestones: set[str] = set()
        self._recent: deque[float] = deque()  # Chat activity in the last SPIKE_WINDOW_S.
        self._activity_total = 0
        self._activity_since: float | None = None

    def _spikes(self, event: ChatEvent, now: float) -> list[Fired]:
        """Chat spike rules: comments, gifts and like batches in the last few seconds vs. the stream's usual rate."""
        if not isinstance(event, (ChatComment, ChatGift, ChatLike)):
            return []
        if self._activity_since is None:
            self._activity_since = now
        self._activity_total += 1
        self._recent.append(now)
        while self._recent and self._recent[0] <= now - SPIKE_WINDOW_S:
            self._recent.popleft()
        elapsed = now - self._activity_since
        if elapsed < SPIKE_WARMUP_S:
            return []
        count = len(self._recent)
        # The usual amount per window, not counting the window itself.
        usual = (self._activity_total - count) / max(elapsed - SPIKE_WINDOW_S, 1.0) * SPIKE_WINDOW_S
        fired = []
        for rule in self.rules:
            trigger = rule.trigger
            if not rule.enabled or trigger.kind != "spike" or count < trigger.every:
                continue
            if count < trigger.factor * max(usual, 1.0):
                continue
            if self._cooled_down(rule, None, now, SPIKE_MIN_COOLDOWN_S):
                fired.append(Fired(rule, {"count": count, "rule": rule.name, "user": "", "platform": ""}, None))
        return fired

    def match(self, event: ChatEvent) -> list[Fired]:
        now = self._clock()
        fired = self._spikes(event, now)
        kind = _kind(event)
        if kind is None:
            return fired
        user: ChatUser | None = event.mvp if isinstance(event, GameEnded) else getattr(event, "user", None)
        platform = user.platform if user else getattr(event, "platform", "tiktok")
        values: dict = {"platform": PLATFORMS.get(platform, platform)}
        if user is not None:
            values["user"] = user.nickname or user.unique_id
        before, image = 0, ""
        if isinstance(event, ChatLike):
            before = self.likes.get(platform, 0)
            self.likes[platform] = before + event.count
            values.update(likes=self.likes[platform], count=event.count)
        elif isinstance(event, ViewerCount):
            before = self._peak_viewers.get(platform, 0)
            self._peak_viewers[platform] = max(before, event.count)
            values["viewers"] = event.count
        elif isinstance(event, GameEnded):
            values.update(game=event.label, winner=event.winner, amount=f"{event.mvp_amount:,}",
                          mvp=(event.mvp.nickname or event.mvp.unique_id) if event.mvp else "nobody")
            values["user"] = values["mvp"]
        elif isinstance(event, LeaderChanged):
            values.update(board=event.label, value=f"{event.value:,}")
        elif isinstance(event, GoalReached):
            values.update(goal=event.name, current=f"{event.current:,}", target=f"{event.target:,}")
            values.setdefault("user", "")
        elif isinstance(event, ChatGift):
            values.update(gift=event.gift_name, count=event.count, coins=event.diamonds * event.count,
                          message=event.message)
            image = event.image_url
        command, args = "", ""
        if isinstance(event, ChatComment):
            text = event.text.strip()
            if not text.startswith(self.prefix):
                return fired
            word, _, args = text[len(self.prefix):].partition(" ")
            command, args = word.lower(), args.strip()
            values["args"] = args

        for rule in self.rules:
            trigger = rule.trigger
            if not rule.enabled or trigger.kind != kind:
                continue
            if kind == "goal":  # Not a viewer's action: no platform or role filter.
                if trigger.goal and trigger.goal != event.goal_id:
                    continue
            elif kind == "leader":
                if trigger.board and trigger.board != event.board:
                    continue
            elif kind == "game_end":
                if trigger.game and trigger.game != event.game:
                    continue
            elif platform not in trigger.platforms:
                continue
            elif user is not None and role_of(user) < Role[trigger.role.upper()]:
                continue
            if kind == "gift":
                if trigger.gifts and event.gift_name.lower() not in {g.lower() for g in trigger.gifts}:
                    continue
                if event.diamonds * event.count < trigger.min_coins:
                    continue
            elif kind == "like":
                # A batch can cross several multiples at once; fire once, not a burst.
                if self.likes[platform] // trigger.every == before // trigger.every:
                    continue
            elif kind == "viewers":
                if rule.id in self._milestones or not before < trigger.every <= event.count:
                    continue
            elif kind == "command":
                if command != trigger.command or command in self.reserved:
                    continue
            if not self._cooled_down(rule, user, now):
                continue
            if kind == "viewers":
                self._milestones.add(rule.id)
            fired.append(Fired(rule, {**values, "rule": rule.name}, user, image))
        return fired

    def _cooled_down(self, rule: Rule, user: ChatUser | None, now: float, min_cooldown: float = 0) -> bool:
        cooldown = max(rule.cooldown_s, min_cooldown)
        if cooldown and now - self._last_rule.get(rule.id, float("-inf")) < cooldown:
            return False
        key = (rule.id, user.key) if user is not None else None
        if key and rule.user_cooldown_s and role_of(user) < Role.MODS:
            if now - self._last_user.get(key, float("-inf")) < rule.user_cooldown_s:
                return False
            self._last_user[key] = now
        self._last_rule[rule.id] = now
        return True
