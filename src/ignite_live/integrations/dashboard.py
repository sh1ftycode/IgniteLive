"""What the web dashboard shows beyond chat and the stream, and which settings it may change.

Sent over the relay (integrations/relay.py) as state: "dashboard" (rules, games, overlay pages and themes,
sounds, the settings below), "audience" (who's watching), "reports" (recent streams). Never secrets, ports,
the server address, or anything that would let the dashboard lock the PC out of its own settings.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from ignite_live.actions.rules import TRIGGERS, parse_rules
from ignite_live.core.settings import Settings
from ignite_live.games.base import GAMES
from ignite_live.live import analytics
from ignite_live.overlay import api

# Settings the dashboard's Settings tab may change, with the range of each number. Text is capped at MAX_TEXT.
REMOTE_SETTINGS: dict[str, tuple[int, int] | None] = {
    "tiktok_username": None, "twitch_channel": None, "youtube_channel": None, "stream_title": None,
    "volume": (0, 100), "request_role": None, "skip_role": None, "user_cooldown_s": (0, 3600),
    "max_queue": (1, 500), "max_per_user": (1, 50), "max_minutes": (1, 60),
    "music_radio": None, "music_radio_start": None, "music_playlist_url": None, "music_playlist_shuffle": None,
    "refuse_explicit": None, "refuse_unknown_length": None, "auto_ban": None,
    "tts_enabled": None, "tts_comments": None, "tts_gifts": None, "tts_follows": None, "tts_role": None,
    "tts_command": None, "tts_skip_commands": None, "tts_skip_links": None, "tts_max_chars": (20, 500),
    "tts_volume": (0, 100), "command_prefix": None,
    "obs_record_while_live": None, "obs_replay_while_live": None, "obs_live_scene": None,
    "obs_chat_clip": None, "obs_chat_scene": None,
    "mod_hide_links": None, "mod_chat_mode": None, "blocked_words": None,
    "panic_tts": None, "panic_alerts": None, "panic_music": None, "panic_rules": None, "panic_scene": None,
    "panic_chat_command": None,
    "discord_comments": None, "discord_gifts": None, "discord_follows": None,
}
MAX_TEXT = 200
MAX_LIST = 200
AUDIENCE_KEPT = 200  # People sent to the dashboard's Viewers tab (the busiest first).
REPORTS_KEPT = 10


def settings_payload(settings: Settings) -> dict:
    return {name: getattr(settings, name) for name in REMOTE_SETTINGS}


def apply_remote(settings: Settings, values: object) -> tuple[Settings, list[str]]:
    """A copy of ``settings`` with the dashboard's changes that are allowed and valid; and the names changed."""
    if not isinstance(values, dict):
        raise ValueError("Send the settings to change as an object.")
    cleaned: dict = {}
    for name, value in values.items():
        if name not in REMOTE_SETTINGS:
            continue
        limits = REMOTE_SETTINGS[name]
        if isinstance(value, str):
            value = value.strip()[:MAX_TEXT]
        elif isinstance(value, list):
            value = [v.strip()[:MAX_TEXT] for v in value if isinstance(v, str) and v.strip()][:MAX_LIST]
        elif limits is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
            value = max(limits[0], min(limits[1], int(value)))
        cleaned[name] = value
    if "tiktok_username" in cleaned and isinstance(cleaned["tiktok_username"], str):
        cleaned["tiktok_username"] = cleaned["tiktok_username"].lstrip("@")
    new = dataclasses.replace(settings)
    new.apply(cleaned, only=tuple(cleaned))
    changed = [name for name in cleaned if getattr(new, name) != getattr(settings, name)]
    return new, changed


def rules_payload(settings: Settings) -> list[dict]:
    return [{"id": rule.id, "name": rule.name or TRIGGERS.get(rule.trigger.kind, rule.trigger.kind),
             "enabled": rule.enabled, "trigger": TRIGGERS.get(rule.trigger.kind, rule.trigger.kind),
             "actions": len(rule.actions)}
            for rule in parse_rules(settings.action_rules)]


def dashboard_payload(settings: Settings, overlay_pages: dict, themes: dict, sounds: list[str]) -> dict:
    """``overlay_pages``: {page: {"title", "url"}}, the links to paste into OBS (hosted ones when they're on)."""
    return {
        "settings": settings_payload(settings),
        "rules": rules_payload(settings),
        "games": dict(GAMES),
        "overlays": {"theme": settings.overlay_theme, "themes": themes, "pages": overlay_pages},
        "sounds": sounds[:MAX_LIST],
        "hosted": settings.hosted_widgets,
    }


def audience_payload(snapshot: list[dict], stats: dict | None = None) -> dict:
    """The Viewers tab: who's here (as far as the platforms say) and the counts."""
    people = []
    for entry in snapshot[:AUDIENCE_KEPT]:
        people.append({**api.user_payload(entry["user"]), "active": bool(entry.get("active")),
                       "in_chat": bool(entry.get("in_chat")), "idle_s": int(entry.get("idle_s", 0))})
    return {"people": people, "total": len(snapshot), "stats": stats}


def report_summaries(paths: list[Path]) -> list[dict]:
    """The latest streams' numbers, newest first (blocking: reads files)."""
    found = []
    for path in sorted(paths, reverse=True)[:REPORTS_KEPT]:
        data = analytics.load(path)
        if data is None:
            continue
        found.append({"started": data.get("started"), "title": str(data.get("title", ""))[:100],
                      **analytics.summary(data), "songs": analytics.song_stats(data)[:5],
                      "games": analytics.game_stats(data)[:5], "clips": len(analytics.clips(data))})
    return found
