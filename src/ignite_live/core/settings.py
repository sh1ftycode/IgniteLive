"""Non-secret app settings, stored as JSON in the platform config directory.

Secrets (tokens, keys, passwords) live in the OS keychain instead; see
``ignite_live.core.keychain``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ignite_live.actions.goals import clean_goals
from ignite_live.actions.rules import clean_rules
from ignite_live.games.manager import parse_games
from ignite_live.viewers.points import clean_config as clean_points

ROLES = ("everyone", "followers", "mods")

# Preferences that follow the user between machines via the backend.
SYNCED_FIELDS = (
    "stream_title",
    "volume",
    "request_role",
    "skip_role",
    "user_cooldown_s",
    "max_queue",
    "max_per_user",
    "max_minutes",
    "banned_video_ids",
    "music_playlist_url",
    "music_playlist_shuffle",
    "overlay_theme",
    "texts",
    "command_prefix",
    "command_names",
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
    "overlay_accent",
    "overlay_accent_2",
    "overlay_font",
    "overlay_css",
    "action_rules",
    "goals",
    "points",
    "games",
    "blocked_words",
    "blocked_users",
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
    "banned_reasons",
    "panic_tts",
    "panic_alerts",
    "panic_music",
    "panic_rules",
    "panic_scene",
    "panic_mute_inputs",
    "panic_chat_command",
)
CHAT_MODES = ("mask", "hide")

# Chat commands that can be renamed: kind -> default names (comma-separated, without the prefix).
COMMAND_DEFAULTS = {
    "request": "sr, play",
    "skip": "skip",
    "revoke": "revoke, wrongsong",
    "current": "song",
    "queue": "queue",
    "tts_skip": "skiptts",
    "clip": "clip",
    "scene": "scene",
    "points": "points",
    "bump": "bump",
    "say": "say",
    "sound": "sound",
    "give": "give",
    "block": "block",
    "unblock": "unblock",
    "panic": "panic",
}
PLATFORM_FIELDS = {"tiktok": "tiktok_username", "twitch": "twitch_channel", "youtube": "youtube_channel"}


@dataclass
class Settings:
    tiktok_username: str = ""
    twitch_channel: str = ""  # Read anonymously: no Twitch login needed.
    youtube_channel: str = ""  # @handle, channel link, or a live video link.
    obs_url: str = "ws://127.0.0.1:4455"
    # Which OBS output gets the TikTok key (see integrations/obs_targets.py).
    stream_target: str = "main"
    # OBS automations and tools (Preferences → OBS).
    obs_record_while_live: bool = False  # Start recording when the stream starts, stop when it ends.
    obs_replay_while_live: bool = False  # The same for the replay buffer.
    obs_live_scene: str = ""  # Switch to this scene when the stream starts.
    obs_warn_dropped_pct: int = 5  # Warn when OBS drops or skips this % of frames; 0 = never.
    obs_chat_clip: bool = True  # Moderators: !clip saves the replay buffer.
    obs_chat_scene: bool = False  # Moderators: !scene <name> switches scenes.
    obs_screenshot_dir: str = ""  # Empty: Pictures/Ignite.
    backend_url: str = ""
    overlay_port: int = 17_845
    overlay_token: str = ""
    overlay_theme: str = "minimal"  # See overlay/server.py THEMES.
    ui_theme: str = "ignite"  # "ignite" or "system"
    window_state: str = ""  # Qt saveState()/saveGeometry() of the main window, base64
    tab_order: list[str] = field(default_factory=list)  # Page keys in the order the tabs were dragged into.
    closed_tabs: list[str] = field(default_factory=list)  # Reopened from the View menu.
    close_to_tray: bool = True  # Closing the window keeps Ignite running in the system tray (when there is one).
    tray_hint_shown: bool = False  # "Still running in the tray" is said once.
    stream_title: str = ""
    volume: int = 60
    request_role: str = "everyone"
    skip_role: str = "mods"
    user_cooldown_s: int = 30
    max_queue: int = 25
    max_per_user: int = 2
    max_minutes: int = 10
    banned_video_ids: list[str] = field(default_factory=list)
    # Background playlist (public Spotify link) that plays when the queue is empty.
    music_playlist_url: str = ""
    music_playlist_shuffle: bool = True
    chat_popout_geometry: str = ""  # Qt saveGeometry(), base64
    chat_popout_locked: bool = False  # Picture-in-picture chat: locked in place, ignores clicks.
    # Discord bridge (the webhook URL itself is a secret: it lives in the keychain).
    discord_enabled: bool = False
    discord_comments: bool = True
    discord_gifts: bool = True
    discord_follows: bool = False
    # Text to speech (voices come from the OS: SAPI/WinRT on Windows, macOS voices, speech-dispatcher on Linux).
    tts_enabled: bool = False
    tts_platforms: list[str] = field(default_factory=lambda: ["tiktok", "twitch", "youtube"])
    tts_comments: bool = True
    tts_gifts: bool = True
    tts_follows: bool = False
    tts_role: str = "everyone"
    tts_command: str = ""  # e.g. "tts": only read "!tts …" messages. Empty: read every comment.
    tts_skip_commands: bool = True  # Don't read other commands like "!sr …".
    tts_skip_links: bool = True
    tts_max_chars: int = 200
    tts_user_cooldown_s: int = 0
    tts_max_queue: int = 10
    tts_provider: str = "edge"  # "edge", "tiktok" or "system" (see speech/voices.py)
    tts_edge_voice: str = "en-US-AriaNeural"
    tts_tiktok_voice: str = "en_us_002"  # Jessie
    tts_engine: str = ""  # System voices: empty means the OS default.
    tts_voice: str = ""
    tts_rate: int = 0  # -100..100
    tts_pitch: int = 0  # -100..100
    tts_volume: int = 80  # 0..100
    # Customisation: message templates (only changed ones; see core/templates.py) and command names.
    texts: dict[str, str] = field(default_factory=dict)
    command_prefix: str = "!"
    command_names: dict[str, str] = field(default_factory=dict)  # kind -> "name, alias"
    # Overlay style on top of the theme. Empty means the theme's own.
    overlay_accent: str = ""
    overlay_accent_2: str = ""
    overlay_font: str = ""
    overlay_css: str = ""
    # Event → action rules (Actions tab), as JSON dicts; see actions/rules.py for the format.
    action_rules: list[dict] = field(default_factory=list)
    # Goal bars (Overlays tab → Goals); progress is kept separately (see actions/goals.py).
    goals: list[dict] = field(default_factory=list)
    # Loyalty points: how they're earned and spent (viewers/points.py). Balances are in viewers.sqlite3.
    points: dict = field(default_factory=lambda: clean_points(None))
    # Viewer games' settings (Games tab): {"tug": {…}, "boss": {…}, "poll": {…}}; see games/.
    games: dict = field(default_factory=lambda: parse_games(None))
    # Moderation (Preferences → Moderation; moderation/filter.py). Chat itself is read-only: this covers what
    # Ignite shows, says, and plays.
    blocked_words: list[str] = field(default_factory=list)
    blocked_users: list[str] = field(default_factory=list)  # "platform:id", or a name for any platform.
    mod_hide_links: bool = True  # Links become [link] on the overlays, Discord, and in rule texts.
    mod_chat_mode: str = "mask"  # Blocked words in chat: "mask" (***) or "hide" (leave the message out).
    mod_filter_tts: bool = True
    mod_filter_overlay: bool = True
    mod_filter_discord: bool = True
    mod_filter_songs: bool = True
    mod_filter_rules: bool = True
    refuse_unknown_length: bool = True  # When a song's length can't be found, it can't be checked: refuse it.
    refuse_explicit: bool = False  # Titles marked (Explicit); with it on, search prefers clean versions.
    auto_ban: bool = False  # Songs refused for length, explicit, or blocked words are banned for good.
    banned_reasons: dict[str, str] = field(default_factory=dict)  # video id -> why it was auto-banned
    # Panic button: what it does (each undone by Resume).
    panic_tts: bool = True
    panic_alerts: bool = True
    panic_music: bool = True
    panic_rules: bool = True  # Pause every action rule and stop the running game.
    panic_scene: str = ""  # Switch to this ("be right back") scene; empty: stay.
    panic_mute_inputs: list[str] = field(default_factory=list)  # OBS audio inputs to mute, e.g. the mic.
    panic_chat_command: bool = False  # Moderators can type !panic.
    # Setup guide (this computer only): finished once, and OBS's settings from before "Tune OBS" for Undo.
    setup_done: bool = False
    tuning_backup: dict[str, str] = field(default_factory=dict)

    def synced(self) -> dict:
        return {name: getattr(self, name) for name in SYNCED_FIELDS}

    def apply(self, values: dict, only: tuple[str, ...] | None = None) -> None:
        """Copy valid values in, ignoring unknown keys and wrong types."""
        if "tts_blocked_words" in values and "blocked_words" not in values:  # Before 2026-10-29: TTS only.
            values = {**values, "blocked_words": values["tts_blocked_words"]}
        if values.get("ui_theme") == "shiftytool":  # Before the rename to Ignite.
            values = {**values, "ui_theme": "ignite"}
        defaults = Settings()
        for spec in dataclasses.fields(self):
            if spec.name not in values or (only is not None and spec.name not in only):
                continue
            value, expected = values[spec.name], getattr(defaults, spec.name)
            if isinstance(expected, bool) != isinstance(value, bool) or not isinstance(value, type(expected)):
                continue  # bool is an int subclass, so check it separately.
            if spec.name == "action_rules":
                value = clean_rules(value)
            elif spec.name == "goals":
                value = clean_goals(value)
            elif spec.name == "points":
                value = clean_points(value)
            elif spec.name == "games":
                value = parse_games(value)
            elif isinstance(expected, list):
                value = [item for item in value if isinstance(item, str)]
            elif isinstance(expected, dict):
                value = {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}
            if spec.name.endswith("_role") and value not in ROLES:
                continue
            if spec.name == "mod_chat_mode" and value not in CHAT_MODES:
                continue
            setattr(self, spec.name, value)


def _config_dir(name: str) -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / name
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / name.lower()


def default_path() -> Path:
    override = os.environ.get("IGNITE_CONFIG_DIR") or os.environ.get("SHIFTYTOOL_CONFIG_DIR")
    if override:
        return Path(override) / "settings.json"
    base = _config_dir("Ignite")
    legacy = _config_dir("ShiftyTool")  # The app's name before 1.2.
    if not base.exists() and legacy.is_dir():
        # Renamed, so everything in it (sounds, profiles, viewers, the log) comes along. Rules name their
        # sounds by file name only, so nothing inside points at the old path.
        try:
            legacy.rename(base)
        except OSError:  # E.g. ShiftyTool is still running and holds a file open: use it where it is, rename next time.
            return legacy / "settings.json"
    return base / "settings.json"


def log_dir(settings_path: Path) -> Path:
    """Where the log lives: a ``logs`` folder next to settings.json, in the Ignite folder."""
    return settings_path.parent / "logs"


def load(path: Path) -> Settings:
    settings = Settings()
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return settings
    if isinstance(data, dict):
        settings.apply(data)
    return settings


def save(settings: Settings, path: Path) -> None:
    """Atomic write, so a crash mid-save never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(dataclasses.asdict(settings), indent=2), "utf-8")
    os.replace(temp, path)
