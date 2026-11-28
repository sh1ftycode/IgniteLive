"""Discord Rich Presence: what the streamer's Discord profile shows while Ignite runs. Pure logic, no I/O
(integrations/discord_ipc.py sends it; the controller feeds it a snapshot every few seconds).

Modular: each module adds placeholders, and only the modules that are on fill theirs in:

    stream    {platform} {platforms} {tiktok} {twitch} {youtube}   (where you're live, your usernames)
    audience  {viewers} {likes} {followers}
    music     {song} {artist} {requester}
    game      {game}

The two lines (details, state) are templates, from a preset or written by hand. A line is split into segments at
" · ", " | " or " • ", and a segment whose placeholder came out empty is dropped, so "{viewers} watching ·
♪ {song}" shows just "12 watching" when nothing is playing. Up to two buttons (label, https link; links can use
the stream placeholders, e.g. https://www.tiktok.com/@{tiktok}/live).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ignite_live.core.events import PLATFORMS
from ignite_live.core.templates import render

MODULES = {  # key -> (label, its placeholders)
    "stream": ("Where you're live", ("platform", "platforms", "tiktok", "twitch", "youtube")),
    "audience": ("Viewers, likes, followers", ("viewers", "likes", "followers")),
    "music": ("The song playing", ("song", "artist", "requester")),
    "game": ("The viewer game running", ("game",)),
}
PRESETS = {  # key -> (label, details, state)
    "streaming": ("Live with viewers", "Live on {platforms}", "{viewers} watching · {likes} likes"),
    "music": ("Live with the song", "Live on {platforms}", "♪ {song} · {artist}"),
    "everything": ("Everything", "Live on {platforms} · {viewers} watching", "♪ {song} · Playing {game}"),
    "minimal": ("Just “live”", "Streaming live", ""),
}
IDLE_MODES = ("hide", "show")  # Not live: no presence, or the idle lines.
LARGE_IMAGES = ("ignite", "none")  # Or an https:// image link.
SMALL_IMAGES = ("platform", "none")  # platform: the app's art assets named tiktok, twitch, youtube.
MAX_LINE = 128  # Discord's limit for details and state.
MAX_BUTTON_LABEL = 32
UPDATE_EVERY_S = 15  # Discord accepts about 5 updates in 20 s; changes are sent at most this often.
_SEPARATOR = re.compile(r"(\s+[·|•]\s+)")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_APP_ID = re.compile(r"^\d{15,25}$")
# Ignite's own Discord application: profiles show "Playing Ignite". Not a secret (every game ships its own).
# A streamer can set their own application instead, to show another name.
DEFAULT_APP_ID = "1571544913332412456"


@dataclass(frozen=True)
class PresenceConfig:
    enabled: bool = False
    app_id: str = ""  # Their own Discord application ("Playing <its name>"); "" uses Ignite's.
    details: str = PRESETS["streaming"][1]
    state: str = PRESETS["streaming"][2]
    modules: tuple[str, ...] = tuple(MODULES)
    show_time: bool = True  # Time since the stream started.
    large_image: str = "ignite"
    large_text: str = "Streaming with Ignite"
    small_image: str = "platform"
    buttons: tuple[tuple[str, str], ...] = ()
    idle: str = "hide"
    idle_details: str = "Getting ready to stream"
    idle_state: str = ""

    @property
    def app(self) -> str:
        """The Discord application to show as: theirs, or Ignite's. DEPRECATED: use ``app_for`` with the server's
        application, which this ignores. Kept for callers that only know the built-in default."""
        return self.app_for()

    def app_for(self, server_default: str = "") -> str:
        """Theirs if they set one, else the one the Ignite server gives out, else Ignite's own built in."""
        return self.app_id or (server_default if _APP_ID.match(server_default) else "") or DEFAULT_APP_ID


@dataclass
class Snapshot:
    """What's going on, from the controller."""
    live: dict[str, bool] = field(default_factory=dict)  # Platform key -> live there.
    usernames: dict[str, str] = field(default_factory=dict)  # Platform key -> the streamer's name there.
    viewers: int | None = None
    likes: int | None = None
    followers: int | None = None
    song: str = ""
    artist: str = ""
    requester: str = ""
    game: str = ""
    started: float | None = None  # When the stream started (epoch seconds).
    icon_url: str = ""  # Ignite's icon, as a link (the main server's), for large_image "ignite".


def _text(raw: dict, key: str, default: str, limit: int = MAX_LINE) -> str:
    value = raw.get(key, default)
    return " ".join(value.split())[:limit] if isinstance(value, str) else default


def _https(url: str) -> bool:
    return url.startswith("https://") and len(url) <= 512 and " " not in url


def parse_config(raw: object) -> PresenceConfig:
    raw = raw if isinstance(raw, dict) else {}
    default = PresenceConfig()
    app_id = str(raw.get("app_id", "")).strip()
    modules = raw.get("modules", default.modules)
    modules = tuple(m for m in MODULES if isinstance(modules, (list, tuple)) and m in modules)
    large = _text(raw, "large_image", default.large_image, 512)
    buttons = []
    for item in raw.get("buttons", ()) if isinstance(raw.get("buttons"), (list, tuple)) else ():
        if isinstance(item, (list, tuple)) and len(item) == 2:
            label, url = (" ".join(str(part).split()) for part in item)
            if label and _https(url):
                buttons.append((label[:MAX_BUTTON_LABEL], url))
    return PresenceConfig(
        enabled=raw.get("enabled") is True,
        app_id=app_id if _APP_ID.match(app_id) else "",
        details=_text(raw, "details", default.details),
        state=_text(raw, "state", default.state),
        modules=modules,
        show_time=raw.get("show_time", True) is not False,
        large_image=large if large in LARGE_IMAGES or _https(large) else default.large_image,
        large_text=_text(raw, "large_text", default.large_text),
        small_image=raw.get("small_image") if raw.get("small_image") in SMALL_IMAGES else default.small_image,
        buttons=tuple(buttons[:2]),
        idle=raw.get("idle") if raw.get("idle") in IDLE_MODES else default.idle,
        idle_details=_text(raw, "idle_details", default.idle_details),
        idle_state=_text(raw, "idle_state", default.idle_state),
    )


def config_to_dict(config: PresenceConfig) -> dict:
    return {"enabled": config.enabled, "app_id": config.app_id, "details": config.details, "state": config.state,
            "modules": list(config.modules), "show_time": config.show_time, "large_image": config.large_image,
            "large_text": config.large_text, "small_image": config.small_image,
            "buttons": [list(button) for button in config.buttons], "idle": config.idle,
            "idle_details": config.idle_details, "idle_state": config.idle_state}


def values(config: PresenceConfig, snapshot: Snapshot) -> dict[str, str]:
    """Every placeholder: filled for the modules that are on, empty for the rest."""
    found = {name: "" for _label, names in MODULES.values() for name in names}
    on = set(config.modules)
    if "stream" in on:
        live = [PLATFORMS.get(key, key) for key, is_live in snapshot.live.items() if is_live]
        found.update(platform=live[0] if live else "", platforms=" + ".join(live),
                     **{key: snapshot.usernames.get(key, "") for key in ("tiktok", "twitch", "youtube")})
    if "audience" in on:
        for name in ("viewers", "likes", "followers"):
            number = getattr(snapshot, name)
            found[name] = f"{number:,}" if isinstance(number, int) and number > 0 else ""
    if "music" in on:
        found.update(song=snapshot.song, artist=snapshot.artist, requester=snapshot.requester)
    if "game" in on:
        found["game"] = snapshot.game
    return found


def fill(template: str, found: dict[str, str]) -> str:
    """The template with its values; segments whose placeholder came out empty are left out."""
    parts = _SEPARATOR.split(template)
    kept: list[str] = []
    for index in range(0, len(parts), 2):
        segment = parts[index]
        names = _PLACEHOLDER.findall(segment)
        if any(name in found and not found[name] for name in names):
            continue  # Its information isn't there (or its module is off).
        text = render(segment, found).strip()
        if text:
            if kept:
                kept.append(parts[index - 1] if index else " · ")
            kept.append(text)
    return " ".join("".join(kept).split())[:MAX_LINE]


def build(config: PresenceConfig, snapshot: Snapshot) -> dict | None:
    """The activity for Discord, or None to show nothing."""
    if not config.enabled:
        return None
    live = any(snapshot.live.values())
    if not live and config.idle == "hide":
        return None
    found = values(config, snapshot)
    details = fill(config.details if live else config.idle_details, found)
    state = fill(config.state if live else config.idle_state, found)
    activity: dict = {}
    if len(details) >= 2:
        activity["details"] = details
    if len(state) >= 2:
        activity["state"] = state
    if live and config.show_time and snapshot.started:
        activity["timestamps"] = {"start": int(snapshot.started)}
    assets: dict = {}
    large = snapshot.icon_url if config.large_image == "ignite" else config.large_image
    if large and large != "none":
        assets["large_image"] = large
        if len(config.large_text) >= 2:
            assets["large_text"] = config.large_text
    first_live = next((key for key, is_live in snapshot.live.items() if is_live), "")
    if live and config.small_image == "platform" and first_live:
        assets.update(small_image=first_live, small_text=PLATFORMS.get(first_live, first_live))
    if assets:
        activity["assets"] = assets
    buttons = []
    for label, url in config.buttons:
        link = render(url, found)
        if _https(link) and "{" not in link and not link.endswith(("@/live", "@")):  # A placeholder came out empty.
            buttons.append({"label": label, "url": link})
    if buttons and live:
        activity["buttons"] = buttons
    return activity or {"details": "Using Ignite"}


def clean_presence(raw: object) -> dict:
    """What ``Settings`` stores."""
    return config_to_dict(parse_config(raw))
