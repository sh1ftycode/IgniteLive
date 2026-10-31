"""Profiles: a whole setup in one shareable file. Pure logic, no Qt.

A ``.igniteprofile`` is a zip with ``profile.json`` and the sound files its rules
use under ``sounds/``. It carries how the stream behaves and looks (rules, goals,
texts, commands, overlay style, text-to-speech and song-request rules), never who
or where you are (usernames, OBS and server addresses, the overlay token) and
never secrets, which live in the keychain anyway. Profiles never contain code.

Imports are untrusted: sizes are capped before anything is read, only plain
sound file names are accepted, and every value goes through ``Settings.apply``.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path

from ignite_live.actions.rules import SOUND_NAME, new_id, parse_rules
from ignite_live.core.settings import Settings

FORMAT = "ignite-profile"
LEGACY_FORMAT = "shiftytool-profile"  # Profiles made before the rename to Ignite.
VERSION = 1
EXTENSION = ".igniteprofile"
LEGACY_EXTENSION = ".shiftyprofile"
MAX_PROFILE_BYTES = 30 * 1024 * 1024
MAX_SOUND_BYTES = 5 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024

PROFILE_FIELDS = (
    "action_rules",
    "goals",
    "points",
    "games",
    "blocked_words",
    "mod_hide_links",
    "mod_chat_mode",
    "mod_filter_tts",
    "mod_filter_overlay",
    "mod_filter_discord",
    "mod_filter_songs",
    "mod_filter_rules",
    "refuse_unknown_length",
    "refuse_explicit",
    "auto_ban",
    "panic_tts",
    "panic_alerts",
    "panic_music",
    "panic_rules",
    "panic_scene",
    "panic_chat_command",
    "texts",
    "command_prefix",
    "command_names",
    "obs_chat_clip",
    "obs_chat_scene",
    "overlay_theme",
    "overlay_accent",
    "overlay_accent_2",
    "overlay_font",
    "overlay_css",
    "volume",
    "request_role",
    "skip_role",
    "user_cooldown_s",
    "max_queue",
    "max_per_user",
    "max_minutes",
    "banned_video_ids",
    "tts_platforms",
    "tts_comments",
    "tts_gifts",
    "tts_follows",
    "tts_role",
    "tts_command",
    "tts_skip_commands",
    "tts_skip_links",
    "tts_max_chars",
    "tts_user_cooldown_s",
    "tts_max_queue",
    "tts_provider",
    "tts_edge_voice",
    "tts_tiktok_voice",
    "tts_rate",
    "tts_pitch",
    "tts_volume",
)
# CSS that fetches from the internet (url(https://…), @import "//…") reveals the viewer's IP address to that host.
_REMOTE_CSS = re.compile(r"""(?:url\(\s*['"]?|@import\s+(?:url\(\s*)?['"]?)(?:https?:)?//([^/'"\s)]+)""", re.IGNORECASE)


class ProfileError(Exception):
    """A safe-to-display problem with a profile file."""


@dataclass
class Profile:
    name: str
    created: str
    settings: dict  # Only PROFILE_FIELDS, already validated.
    sounds: dict[str, bytes] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)  # Files in the zip that were ignored.


def profile_values(settings: Settings) -> dict:
    return {name: getattr(settings, name) for name in PROFILE_FIELDS}


def used_sounds(settings: Settings) -> list[str]:
    names = []
    for rule in parse_rules(settings.action_rules):
        for action in rule.actions:
            if action.kind == "sound" and action.target not in names:
                names.append(action.target)
    return names


def build(settings: Settings, sounds_dir: Path, name: str, created: str) -> bytes:
    """The profile file for these settings, with every sound file its rules use that exists."""
    out = io.BytesIO()
    total = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        document = {"format": FORMAT, "version": VERSION, "name": name.strip()[:60] or "Profile",
                    "created": created, "settings": profile_values(settings)}
        archive.writestr("profile.json", json.dumps(document, indent=2, ensure_ascii=False))
        for sound in used_sounds(settings):
            path = sounds_dir / sound
            if not SOUND_NAME.match(sound) or not path.is_file():
                continue
            size = path.stat().st_size
            if size > MAX_SOUND_BYTES:
                raise ProfileError(f"“{sound}” is over {MAX_SOUND_BYTES // 1024 // 1024} MB, too big to share.")
            total += size
            if total > MAX_PROFILE_BYTES:
                raise ProfileError(f"The sounds add up to over {MAX_PROFILE_BYTES // 1024 // 1024} MB, too big to share.")
            archive.write(path, f"sounds/{sound}")
    return out.getvalue()


def _validated(raw: dict) -> dict:
    """Keep the values Settings accepts; drop the ones it would ignore."""
    defaults, checked = Settings(), Settings()
    checked.apply(raw, only=PROFILE_FIELDS)
    kept = {}
    for name in PROFILE_FIELDS:
        if name not in raw:
            continue
        value = getattr(checked, name)
        # Unchanged from the default means "rejected", unless the profile asked for the default.
        if value != getattr(defaults, name) or raw[name] == getattr(defaults, name):
            kept[name] = value
    return kept


def read(data: bytes) -> Profile:
    if len(data) > MAX_PROFILE_BYTES:
        raise ProfileError(f"That file is over {MAX_PROFILE_BYTES // 1024 // 1024} MB: not an Ignite profile.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise ProfileError("That isn't an Ignite profile (not a .igniteprofile file).") from error
    with archive:
        document, sounds, skipped, total = None, {}, [], 0
        for info in archive.infolist():
            if info.is_dir():
                continue
            if info.filename == "profile.json":
                if info.file_size > MAX_JSON_BYTES:
                    raise ProfileError("The profile's settings are too large.")
                try:
                    document = json.loads(archive.read(info).decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as error:
                    raise ProfileError("The profile's settings are damaged.") from error
                continue
            folder, _, sound = info.filename.partition("/")
            if folder != "sounds" or not SOUND_NAME.match(sound) or info.file_size > MAX_SOUND_BYTES:
                skipped.append(info.filename[:100])
                continue
            total += info.file_size  # Declared sizes; zipfile never reads past them.
            if total > MAX_PROFILE_BYTES:
                raise ProfileError("The profile's sounds are too large.")
            try:
                sounds[sound] = archive.read(info)
            except (zipfile.BadZipFile, OSError) as error:
                raise ProfileError(f"The sound “{sound}” in the profile is damaged.") from error
    if not isinstance(document, dict) or document.get("format") not in (FORMAT, LEGACY_FORMAT):
        raise ProfileError("That isn't an Ignite profile (no profile.json).")
    version = document.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ProfileError("The profile's settings are damaged.")
    if version > VERSION:
        raise ProfileError("This profile was made by a newer Ignite. Update the app to import it.")
    raw = document.get("settings")
    name, created = document.get("name"), document.get("created")
    return Profile(
        name=" ".join(name.split())[:60] if isinstance(name, str) and name.strip() else "Imported profile",
        created=created[:40] if isinstance(created, str) else "",
        settings=_validated(raw) if isinstance(raw, dict) else {},
        sounds=sounds,
        skipped=skipped,
    )


def apply(current: Settings, profile: Profile, add_rules: bool = False) -> Settings:
    """``current`` with the profile's values. ``add_rules`` keeps the current rules and goals and adds the profile's."""
    values = dict(profile.settings)
    if add_rules:
        goals = values.get("goals", [])
        rules = values.get("action_rules", [])
        taken = {goal["id"] for goal in current.goals}
        renamed = {}
        for goal in goals:  # A clashing goal gets a new id, and the profile's rules follow it.
            if goal["id"] in taken:
                renamed[goal["id"]] = new_id()
        if renamed:
            goals = [{**g, "id": renamed.get(g["id"], g["id"])} for g in goals]
            rules = [{**r, "trigger": {**r["trigger"], "goal": renamed.get(r["trigger"].get("goal", ""),
                                                                            r["trigger"].get("goal", ""))}}
                     for r in rules]
        values["goals"] = current.goals + goals
        values["action_rules"] = current.action_rules + rules  # parse_rules re-ids duplicate rule ids.
    updated = replace(current)
    updated.apply(values, only=PROFILE_FIELDS)
    return updated


def install_sounds(profile: Profile, sounds_dir: Path, overwrite: bool = False) -> tuple[list[str], list[str]]:
    """Copy the profile's sounds in; returns (installed, kept because a file by that name exists)."""
    installed, kept = [], []
    if not profile.sounds:
        return installed, kept
    sounds_dir.mkdir(parents=True, exist_ok=True)
    for name, data in profile.sounds.items():
        path = sounds_dir / name
        if path.exists() and not overwrite:
            kept.append(name)
            continue
        temp = path.with_name(path.name + ".part")
        temp.write_bytes(data)
        temp.replace(path)
        installed.append(name)
    return installed, kept


def remote_css_hosts(css: str) -> list[str]:
    return sorted({host.lower() for host in _REMOTE_CSS.findall(css or "")})


def summary(profile: Profile, sounds_dir: Path) -> list[str]:
    """What importing would change, one line each, for the preview."""
    from ignite_live.overlay.server import THEMES  # Avoids importing aiohttp when only reading profiles.

    s, lines = profile.settings, []
    if "action_rules" in s:
        lines.append(f"{len(s['action_rules'])} action rule{'s' if len(s['action_rules']) != 1 else ''}")
    if "goals" in s:
        lines.append(f"{len(s['goals'])} goal{'s' if len(s['goals']) != 1 else ''}")
    if profile.sounds:
        existing = sum(1 for name in profile.sounds if (sounds_dir / name).exists())
        line = f"{len(profile.sounds)} sound file{'s' if len(profile.sounds) != 1 else ''}"
        lines.append(line + (f" ({existing} already in your sounds folder)" if existing else ""))
    if "overlay_theme" in s:
        lines.append(f"Overlay theme: {THEMES.get(s['overlay_theme'], s['overlay_theme'])}")
    if any(s.get(name) for name in ("overlay_accent", "overlay_accent_2", "overlay_font", "overlay_css")):
        lines.append("Overlay colours, font or custom CSS")
    if s.get("texts"):
        lines.append(f"{len(s['texts'])} custom message text{'s' if len(s['texts']) != 1 else ''}")
    if "command_prefix" in s or s.get("command_names"):
        lines.append("Chat command names")
    if any(name.startswith("tts_") for name in s):
        lines.append("Text-to-speech settings (it stays on or off as you have it)")
    if any(name in s for name in ("request_role", "max_queue", "max_per_user", "max_minutes", "banned_video_ids")):
        lines.append("Song request rules")
    return lines


def warnings(profile: Profile) -> list[str]:
    found = []
    hosts = remote_css_hosts(profile.settings.get("overlay_css", ""))
    if hosts:
        found.append("Its custom CSS loads files from " + ", ".join(hosts[:5]) +
                     ". Those sites will see your IP address whenever an overlay is open.")
    if profile.settings.get("tts_provider") == "tiktok":
        found.append("It uses TikTok voices, which need your own TikTok session ID (Preferences → Text to speech).")
    if profile.skipped:
        found.append(f"Ignored {len(profile.skipped)} file{'s' if len(profile.skipped) != 1 else ''} that "
                     "aren't settings or sounds: " + ", ".join(profile.skipped[:5]))
    return found


def file_name(name: str) -> str:
    """A safe file name for a saved profile."""
    cleaned = re.sub(r"[^\w\- ]+", "", name).strip()[:60]
    return (cleaned or "Profile") + EXTENSION


def saved_profiles(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    suffixes = (EXTENSION, LEGACY_EXTENSION)
    return sorted((p for p in folder.iterdir() if p.suffix in suffixes and p.is_file()), key=lambda p: p.name.lower())
