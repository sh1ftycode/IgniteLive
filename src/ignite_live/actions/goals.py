"""Goal bars (likes, coins, follows, shares, one gift): the data model and the tracker. Pure logic, no Qt.

Goals are stored in ``Settings.goals`` (synced, exported in profiles). Progress is
not a setting: the controller saves ``GoalTracker.state()`` to a local file so a
restart mid-stream doesn't empty the bar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from ignite_live.actions.rules import new_id
from ignite_live.core.events import ChatEvent, ChatFollow, ChatGift, ChatLike, ChatShare, ChatUser
from ignite_live.core.templates import render

GOAL_KINDS = {"likes": "Likes", "coins": "Coins (gift value)", "follows": "Follows", "shares": "Shares",
              "gift": "One gift"}
WHEN_DONE = {"stop": "Stay full", "repeat": "Start over", "raise": "Raise the target"}
DEFAULT_LABELS = {
    "likes": "{current} / {target} likes",
    "coins": "{current} / {target} coins",
    "follows": "{current} / {target} follows",
    "shares": "{current} / {target} shares",
    "gift": "{current} / {target} {gift}",
}
LABEL_PLACEHOLDERS = ("current", "target", "remaining", "name", "gift")
MAX_GOALS = 20
MAX_TARGET = 1_000_000_000
_ID = re.compile(r"^[\w-]{1,40}$")


@dataclass(frozen=True)
class Goal:
    id: str = ""
    name: str = ""
    kind: str = "likes"
    target: int = 1000
    gift: str = ""  # gift: which gift counts (any case).
    label: str = ""  # Shown on the bar; empty uses DEFAULT_LABELS.
    when_done: str = "stop"
    step: int = 0  # raise: how much the target goes up; 0 means by the original target.
    keep: bool = False  # Keep progress across streams instead of starting over.


@dataclass(frozen=True)
class GoalReached:
    """Fed to the action engine, so a rule can celebrate a goal."""

    goal_id: str
    name: str
    current: int
    target: int  # The target that was reached (before a "raise").
    user: ChatUser | None = None  # Whoever pushed it over the line.


def _text(raw: dict, key: str, limit: int) -> str:
    value = raw.get(key, "")
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _int(raw: dict, key: str, low: int, high: int, default: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return int(max(low, min(high, value)))


def parse_goal(raw: object) -> Goal | None:
    if not isinstance(raw, dict) or raw.get("kind") not in GOAL_KINDS:
        return None
    kind = raw["kind"]
    gift = _text(raw, "gift", 60)
    if kind == "gift" and not gift:
        return None
    goal_id = raw.get("id")
    keep = raw.get("keep", False)
    return Goal(
        id=goal_id if isinstance(goal_id, str) and _ID.match(goal_id) else new_id(),
        name=_text(raw, "name", 60) or GOAL_KINDS[kind],
        kind=kind,
        target=_int(raw, "target", 1, MAX_TARGET, 1000),
        gift=gift if kind == "gift" else "",
        label=_text(raw, "label", 120),
        when_done=raw.get("when_done") if raw.get("when_done") in WHEN_DONE else "stop",
        step=_int(raw, "step", 0, MAX_TARGET, 0),
        keep=keep if isinstance(keep, bool) else False,
    )


def parse_goals(raw: object) -> list[Goal]:
    if not isinstance(raw, list):
        return []
    goals, seen = [], set()
    for item in raw:
        goal = parse_goal(item)
        if goal is None:
            continue
        if goal.id in seen:
            goal = replace(goal, id=new_id())
        seen.add(goal.id)
        goals.append(goal)
        if len(goals) == MAX_GOALS:
            break
    return goals


def goal_to_dict(goal: Goal) -> dict:
    return dict(goal.__dict__)


def clean_goals(raw: object) -> list[dict]:
    return [goal_to_dict(goal) for goal in parse_goals(raw)]


def describe_goal(goal: Goal) -> str:
    what = goal.gift if goal.kind == "gift" else GOAL_KINDS[goal.kind].split(" ")[0].lower()
    return f"{goal.target:,} {what} · {WHEN_DONE[goal.when_done].lower()}" + (" · kept across streams" if goal.keep else "")


def _amount(goal: Goal, event: ChatEvent) -> int:
    if goal.kind == "likes" and isinstance(event, ChatLike):
        return event.count
    if goal.kind == "coins" and isinstance(event, ChatGift):
        return event.diamonds * event.count
    if goal.kind == "follows" and isinstance(event, ChatFollow):
        return 1
    if goal.kind == "shares" and isinstance(event, ChatShare):
        return 1
    if goal.kind == "gift" and isinstance(event, ChatGift) and event.gift_name.lower() == goal.gift.lower():
        return event.count
    return 0


class GoalTracker:
    def __init__(self, goals: list[Goal] | None = None, state: object = None) -> None:
        self.goals: list[Goal] = []
        # goal id -> {"current", "target", "base" (the configured target), "kind", "done"}
        self.progress: dict[str, dict] = {}
        self.changed = False  # Whether the last feed() moved a bar.
        if isinstance(state, dict):
            for goal_id, entry in state.items():
                if isinstance(entry, dict) and all(isinstance(entry.get(k), int) and not isinstance(entry.get(k), bool)
                                                   for k in ("current", "target", "base")):
                    self.progress[goal_id] = {"current": max(0, entry["current"]), "target": max(1, entry["target"]),
                                              "base": entry["base"], "kind": str(entry.get("kind", "")),
                                              "done": entry.get("done") is True}
        self.update(goals or [])

    def update(self, goals: list[Goal]) -> None:
        """New goal settings: keep progress for goals that still count the same thing."""
        self.goals = list(goals)
        progress = {}
        for goal in self.goals:
            entry = self.progress.get(goal.id)
            if entry is None or entry["kind"] != goal.kind:
                entry = self._fresh(goal)
            elif entry["base"] != goal.target:  # The streamer changed the target: use it.
                entry = {**entry, "target": goal.target, "base": goal.target, "done": False}
            progress[goal.id] = entry
        self.progress = progress

    @staticmethod
    def _fresh(goal: Goal, current: int = 0) -> dict:
        return {"current": current, "target": goal.target, "base": goal.target, "kind": goal.kind, "done": False}

    def feed(self, event: ChatEvent) -> list[GoalReached]:
        """Count the event; returns the goals it completed. ``changed`` says whether any bar moved."""
        self.changed = False
        reached = []
        for goal in self.goals:
            amount = _amount(goal, event)
            if amount <= 0:
                continue
            self.changed = True
            entry = self.progress[goal.id]
            entry["current"] = min(MAX_TARGET * 10, entry["current"] + amount)
            if entry["done"] or entry["current"] < entry["target"]:
                continue
            # One celebration per event even if a big gift crosses the line several times.
            reached.append(GoalReached(goal.id, goal.name, entry["current"], entry["target"], getattr(event, "user", None)))
            if goal.when_done == "stop":
                entry["done"] = True
            elif goal.when_done == "repeat":
                entry["current"] %= entry["target"]
            else:
                step = goal.step or goal.target
                while entry["current"] >= entry["target"]:
                    entry["target"] += step
        return reached

    def reset_stream(self) -> None:
        """A new stream: goals that don't keep their progress start over."""
        for goal in self.goals:
            if not goal.keep:
                self.progress[goal.id] = self._fresh(goal)

    def reset(self, goal_id: str) -> None:
        for goal in self.goals:
            if goal.id == goal_id:
                self.progress[goal.id] = self._fresh(goal)

    def state(self) -> dict:
        return {goal_id: dict(entry) for goal_id, entry in self.progress.items()}

    def snapshot(self) -> list[dict]:
        """What the goals overlay shows."""
        bars = []
        for goal in self.goals:
            entry = self.progress[goal.id]
            current, target = entry["current"], entry["target"]
            values = {"current": f"{current:,}", "target": f"{target:,}", "remaining": f"{max(0, target - current):,}",
                      "name": goal.name, "gift": goal.gift}
            bars.append({
                "id": goal.id,
                "name": goal.name,
                "label": render(goal.label or DEFAULT_LABELS[goal.kind], values)[:160],
                "current": current,
                "target": target,
                "percent": round(min(100.0, 100.0 * current / target), 1),
                "done": entry["done"] or current >= target,
            })
        return bars
