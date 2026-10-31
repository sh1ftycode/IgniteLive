"""Event → action rules: the data model and its validation. Pure logic, no Qt.

Rules are stored in ``Settings.action_rules`` as plain JSON dicts (so they sync and
export like every other setting). ``parse_rules`` turns them into frozen ``Rule``
objects, dropping anything malformed instead of raising, the same way
``Settings.apply`` treats bad values.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field, replace

from ignite_live.games.base import GAMES
from ignite_live.viewers.leaderboard import BOARDS

# kind -> label shown in the editor.
TRIGGERS = {
    "gift": "Gift",
    "follow": "Follow",
    "like": "Likes",
    "share": "Share",
    "command": "Chat command",
    "viewers": "Viewer milestone",
    "goal": "Goal reached",
    "spike": "Chat spike",
    "leader": "New #1",
    "game_end": "Game ended",
}
ACTIONS = {
    "alert": "Show alert",
    "sound": "Play sound",
    "tts": "Text to speech",
    "scene": "Switch scene",
    "source": "Show or hide source",
    "song": "Add song request",
    "clip": "Save clip",
    "points": "Give points",
    "game": "Start or stop a game",
}
SOURCE_MODES = ("show", "hide", "toggle")
ROLE_NAMES = ("everyone", "followers", "mods")  # The same as settings.ROLES.
PLATFORM_KEYS = ("tiktok", "twitch", "youtube")
# Placeholders each trigger fills in action texts.
PLACEHOLDERS = {
    "gift": ("user", "platform", "gift", "count", "coins", "message"),
    "follow": ("user", "platform"),
    "like": ("user", "platform", "likes", "count"),
    "share": ("user", "platform"),
    "command": ("user", "platform", "args"),
    "viewers": ("viewers",),
    "goal": ("goal", "current", "target", "user"),
    "spike": ("count",),
    "leader": ("user", "platform", "board", "value"),
    "game_end": ("game", "winner", "mvp", "amount"),
}
DEFAULT_TEXTS = {"alert": "{user} triggered {rule}", "tts": "", "song": "{args}"}

MAX_RULES = 100
MAX_ACTIONS = 8
MAX_GIFTS = 20
MAX_TEXT = 200
MAX_NAME = 80
MAX_SECONDS = 600.0
MAX_COOLDOWN_S = 86_400
SPIKE_WINDOW_S = 10
CLIP_DELAY_S = 10.0  # Default wait before saving a clip, so it includes the reaction.
MAX_CLIP_DELAY_S = 60.0
# Sound files live in <config dir>/sounds; rules refer to them by file name only.
SOUND_NAME = re.compile(r"^[\w][\w .()'&,+-]{0,78}\.(?:mp3|wav|ogg|m4a|webm)$", re.IGNORECASE)
_COMMAND = re.compile(r"^[\w-]{1,30}$")
_ID = re.compile(r"^[\w-]{1,40}$")


@dataclass(frozen=True)
class Trigger:
    kind: str = "gift"
    gifts: tuple[str, ...] = ()  # gift: names to match (any case); empty matches every gift.
    min_coins: int = 0  # gift: diamonds × count of this one gift event.
    every: int = 0  # like: every N likes this stream; viewers: the milestone; spike: at least N in SPIKE_WINDOW_S.
    factor: int = 3  # spike: this many times the stream's usual rate.
    command: str = ""  # command: the name, without the prefix.
    goal: str = ""  # goal: the goal's id; empty matches every goal.
    board: str = ""  # leader: which leaderboard (viewers/leaderboard.py BOARDS); empty matches every board.
    game: str = ""  # game_end: which game (games/base.py GAMES); empty matches every game.
    role: str = "everyone"
    platforms: tuple[str, ...] = PLATFORM_KEYS


@dataclass(frozen=True)
class Action:
    kind: str = "alert"
    text: str = ""  # alert, tts, song: a template with {user}, {gift}, … (see PLACEHOLDERS).
    target: str = ""  # sound: file name; scene: scene name; source: source name.
    scene: str = ""  # source: the scene it's in; empty means the current scene.
    mode: str = "show"  # source: show, hide or toggle.
    seconds: float = 0.0  # scene: switch back after; source: undo after (0 keeps the change); clip: wait before saving.
    volume: int = 100  # sound: 0..100.
    amount: int = 0  # points: how many the viewer gets (negative takes them away).


@dataclass(frozen=True)
class Rule:
    id: str = ""
    name: str = ""
    enabled: bool = True
    trigger: Trigger = field(default_factory=Trigger)
    actions: tuple[Action, ...] = ()
    cooldown_s: int = 0  # The whole rule.
    user_cooldown_s: int = 0  # Per viewer; moderators skip it.


def new_id() -> str:
    return secrets.token_hex(4)


def _text(raw: dict, key: str, limit: int = MAX_TEXT) -> str:
    value = raw.get(key, "")
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _int(raw: dict, key: str, low: int, high: int, default: int = 0) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return int(max(low, min(high, value)))


def _strings(raw: dict, key: str, limit: int) -> tuple[str, ...]:
    value = raw.get(key, ())
    if not isinstance(value, (list, tuple)):
        return ()
    found = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip() not in found:
            found.append(item.strip()[:60])
    return tuple(found[:limit])


def parse_trigger(raw: object) -> Trigger | None:
    if not isinstance(raw, dict) or raw.get("kind") not in TRIGGERS:
        return None
    kind = raw["kind"]
    platforms = tuple(p for p in _strings(raw, "platforms", len(PLATFORM_KEYS)) if p in PLATFORM_KEYS)
    role = raw.get("role") if raw.get("role") in ROLE_NAMES else "everyone"
    trigger = Trigger(kind=kind, role=role, platforms=platforms if "platforms" in raw else PLATFORM_KEYS)
    if kind == "gift":
        return replace(trigger, gifts=_strings(raw, "gifts", MAX_GIFTS), min_coins=_int(raw, "min_coins", 0, 10_000_000))
    if kind in ("like", "viewers"):
        every = _int(raw, "every", 0, 100_000_000)
        if every < 1:
            return None
        return replace(trigger, every=every)
    if kind == "game_end":
        game = raw.get("game", "")
        return replace(trigger, game=game if game in GAMES else "")
    if kind == "leader":
        board = raw.get("board", "")
        return replace(trigger, board=board if board in BOARDS else "")
    if kind == "spike":
        return replace(trigger, every=_int(raw, "every", 3, 10_000, 15), factor=_int(raw, "factor", 2, 20, 3))
    if kind == "command":
        command = _text(raw, "command", 40).lstrip("!").lower()
        if not _COMMAND.match(command):
            return None
        return replace(trigger, command=command)
    if kind == "goal":
        goal = raw.get("goal", "")
        return replace(trigger, goal=goal if isinstance(goal, str) and _ID.match(goal) else "")
    return trigger


def parse_action(raw: object) -> Action | None:
    if not isinstance(raw, dict) or raw.get("kind") not in ACTIONS:
        return None
    kind = raw["kind"]
    text = _text(raw, "text") or DEFAULT_TEXTS.get(kind, "")
    target = _text(raw, "target", 120)
    seconds = raw.get("seconds", 0)
    seconds = 0.0 if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) else float(seconds)
    if kind == "clip" and "seconds" not in raw:
        seconds = CLIP_DELAY_S
    seconds = round(max(0.0, min(MAX_CLIP_DELAY_S if kind == "clip" else MAX_SECONDS, seconds)), 1)
    if kind in ("tts", "song") and not text:
        return None
    if kind == "sound" and not SOUND_NAME.match(target):
        return None
    if kind in ("scene", "source") and not target:
        return None
    if kind == "game" and target not in (*GAMES, "stop"):
        return None
    amount = _int(raw, "amount", -1_000_000, 1_000_000, 0) if kind == "points" else 0
    if kind == "points" and not amount:
        return None
    return Action(
        kind=kind,
        text=text if kind in ("alert", "tts", "song") else "",
        target=target if kind in ("sound", "scene", "source", "game") else "",
        scene=_text(raw, "scene", 120) if kind == "source" else "",
        mode=raw.get("mode") if kind == "source" and raw.get("mode") in SOURCE_MODES else "show",
        seconds=seconds if kind in ("scene", "source", "clip") else 0.0,
        volume=_int(raw, "volume", 0, 100, 100) if kind == "sound" else 100,
        amount=amount,
    )


def parse_rule(raw: object) -> Rule | None:
    if not isinstance(raw, dict):
        return None
    trigger = parse_trigger(raw.get("trigger"))
    if trigger is None:
        return None
    actions = raw.get("actions")
    parsed = [parse_action(item) for item in actions] if isinstance(actions, list) else []
    rule_id = raw.get("id")
    enabled = raw.get("enabled", True)
    return Rule(
        id=rule_id if isinstance(rule_id, str) and _ID.match(rule_id) else new_id(),
        name=_text(raw, "name", MAX_NAME) or TRIGGERS[trigger.kind],
        enabled=enabled if isinstance(enabled, bool) else True,
        trigger=trigger,
        actions=tuple(action for action in parsed if action is not None)[:MAX_ACTIONS],
        cooldown_s=_int(raw, "cooldown_s", 0, MAX_COOLDOWN_S),
        user_cooldown_s=_int(raw, "user_cooldown_s", 0, MAX_COOLDOWN_S),
    )


def parse_rules(raw: object) -> list[Rule]:
    if not isinstance(raw, list):
        return []
    rules, seen = [], set()
    for item in raw:
        rule = parse_rule(item)
        if rule is None:
            continue
        if rule.id in seen:  # Pasted or imported twice: keep both, under different ids.
            rule = replace(rule, id=new_id())
        seen.add(rule.id)
        rules.append(rule)
        if len(rules) == MAX_RULES:
            break
    return rules


def to_dict(rule: Rule) -> dict:
    """JSON-ready, with only the fields that matter for each kind."""
    trigger = {"kind": rule.trigger.kind, "role": rule.trigger.role, "platforms": list(rule.trigger.platforms)}
    if rule.trigger.kind == "gift":
        trigger.update(gifts=list(rule.trigger.gifts), min_coins=rule.trigger.min_coins)
    elif rule.trigger.kind in ("like", "viewers"):
        trigger["every"] = rule.trigger.every
    elif rule.trigger.kind == "spike":
        trigger.update(every=rule.trigger.every, factor=rule.trigger.factor)
    elif rule.trigger.kind == "command":
        trigger["command"] = rule.trigger.command
    elif rule.trigger.kind == "goal":
        trigger["goal"] = rule.trigger.goal
    elif rule.trigger.kind == "leader":
        trigger["board"] = rule.trigger.board
    elif rule.trigger.kind == "game_end":
        trigger["game"] = rule.trigger.game
    actions = []
    for action in rule.actions:
        item: dict = {"kind": action.kind}
        if action.kind in ("alert", "tts", "song"):
            item["text"] = action.text
        if action.kind in ("sound", "scene", "source", "game"):
            item["target"] = action.target
        if action.kind == "source":
            item.update(scene=action.scene, mode=action.mode)
        if action.kind in ("scene", "source", "clip"):
            item["seconds"] = action.seconds
        if action.kind == "sound":
            item["volume"] = action.volume
        if action.kind == "points":
            item["amount"] = action.amount
        actions.append(item)
    return {"id": rule.id, "name": rule.name, "enabled": rule.enabled, "trigger": trigger, "actions": actions,
            "cooldown_s": rule.cooldown_s, "user_cooldown_s": rule.user_cooldown_s}


def clean_rules(raw: object) -> list[dict]:
    """What ``Settings`` stores: valid rules only, in their canonical JSON form."""
    return [to_dict(rule) for rule in parse_rules(raw)]


def describe_trigger(trigger: Trigger, prefix: str = "!", goal_names: dict[str, str] | None = None) -> str:
    """One line for the rule list, e.g. "Gift: Rose, Lion · 100+ coins"."""
    if trigger.kind == "gift":
        parts = [", ".join(trigger.gifts) or "any gift"]
        if trigger.min_coins:
            parts.append(f"{trigger.min_coins:,}+ coins")
        text = "Gift: " + " · ".join(parts)
    elif trigger.kind == "like":
        text = f"Every {trigger.every:,} likes"
    elif trigger.kind == "viewers":
        text = f"{trigger.every:,} viewers"
    elif trigger.kind == "command":
        text = f"{prefix}{trigger.command}"
    elif trigger.kind == "game_end":
        return "Game ended: " + (GAMES[trigger.game].lower() if trigger.game else "any game")
    elif trigger.kind == "leader":
        return "New #1: " + (BOARDS[trigger.board][0].lower() if trigger.board else "any leaderboard")
    elif trigger.kind == "spike":
        text = f"Chat spike: {trigger.factor}× usual, {trigger.every}+ in {SPIKE_WINDOW_S} s"
    elif trigger.kind == "goal":
        name = (goal_names or {}).get(trigger.goal, "a deleted goal") if trigger.goal else "any goal"
        return f"Goal reached: {name}"
    else:
        text = TRIGGERS[trigger.kind]
    if trigger.role != "everyone":
        text += f" ({trigger.role} only)"
    return text


def describe_actions(actions: tuple[Action, ...]) -> str:
    if not actions:
        return "No actions yet"
    def target(a: Action) -> str:
        if a.kind == "game":
            return ": stop" if a.target == "stop" else f": {GAMES[a.target].lower()}"
        return f": {a.target}" if a.target else ""

    return ", ".join(ACTIONS[a.kind] + target(a) for a in actions)
