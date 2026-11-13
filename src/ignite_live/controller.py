"""Application controller: owns every integration, the settings, and the runtime.

The UI calls methods here and listens to the Qt signals; it never touches an
integration directly. Network work runs on the runtime's asyncio thread, and
every failure surfaces as a ``notice`` instead of a modal dialog.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from urllib.parse import urlsplit
from dataclasses import asdict, fields, replace
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ignite_live.actions.engine import ActionEngine, Fired
from ignite_live.actions.goals import GoalReached, GoalTracker, parse_goals
from ignite_live.actions.rules import SOUND_NAME, Action, Rule, parse_rules
from ignite_live.core import keychain, profiles, seasons, settings as settings_store
from ignite_live.core.events import (
    PLATFORMS,
    ChannelStats,
    ChatComment,
    ChatEvent,
    ChatFollow,
    ChatGift,
    ChatJoin,
    ChatLeave,
    ChatLike,
    ChatShare,
    ChatUser,
    ConnectionState,
    LiveStatus,
    StatusChange,
    ViewerCount,
)
from ignite_live.core.images import allowed_image_url
from ignite_live.core.key_cache import LocalKeyCache
from ignite_live.core.runtime import Runtime
from ignite_live.core.settings import COMMAND_DEFAULTS, PLATFORM_FIELDS, Settings
from ignite_live.core.templates import Texts, render
from ignite_live.integrations.backend import BackendClient, BackendError, CachedKey
from ignite_live.integrations.relay import RelayClient
from ignite_live.integrations.discord_bridge import DiscordBridge, DiscordError, Forwarding, escape
from ignite_live.integrations import obs_targets
from ignite_live.integrations.obs import ObsClient, ObsError
from ignite_live.integrations.obs_studio import HealthMonitor, Studio
from ignite_live.integrations.obs_targets import StreamTarget
from ignite_live.live import analytics, checklist
from ignite_live.live.analytics import StreamRecorder
from ignite_live.live.audience import Audience
from ignite_live.music.ytmusic_account import AccountError, YouTubeMusicAccount
from ignite_live.integrations.streamlabs import (
    AccountInfo,
    ProviderError,
    StreamlabsExperimentalProvider,
    StreamlabsTokenRetriever,
    manual_credentials,
)
from ignite_live.music.commands import (
    POINTS_COMMANDS,
    Command,
    CommandConfig,
    CommandEngine,
    CommandKind,
    Role,
    parse_names,
)
from ignite_live.music.queue import QueueRejected, QueueRules, SongQueue
from ignite_live.music.session import MusicSession
from ignite_live.music.spotify import Playlist, PlaylistSong, SpotifyError, fetch_public_playlist
from ignite_live.music.youtube import (
    LinkRequest,
    Track,
    TrackLookupError,
    YouTubeClient,
    parse_request,
    playlist_id_from,
    video_id_from,
)
from ignite_live.overlay.chat import chat_payload
from ignite_live.overlay.server import PAGES, OverlayServer
from ignite_live.speech.rules import TtsRules, clean, shorten
from ignite_live.moderation.filter import Moderation
from ignite_live.viewers.points import Points, parse_config as points_config
from ignite_live.games.base import GAMES, GameEnded
from ignite_live.games.manager import GameManager
from ignite_live.viewers.leaderboard import BOARDS, LeaderChanged, Leaderboards
from ignite_live.viewers.store import ViewerStore

log = logging.getLogger("ignite")

SOURCES = {
    "tiktok": "TikTok chat",
    "twitch": "Twitch chat",
    "youtube": "YouTube chat",
    "obs": "OBS",
    "overlay": "Music overlay",
    "streamlabs": "Streamlabs",
    "backend": "Backend",
    "discord": "Discord",
    "tts": "Text to speech",
    "remote": "Remote control",
}
CHAT_HINTS = {
    "tiktok": "Not connected. Add your username in the Chat panel.",
    "twitch": "Not connected. Add your channel in the Chat panel.",
    "youtube": "Not connected. Add your @handle in the Chat panel.",
}
OBS_RETRY_SECONDS = 5.0
HEALTH_POLL_SECONDS = 2.0
# Plugin outputs send no events, so their live state is polled.
TARGET_POLL_SECONDS = 5.0
RELAUNCH_WAIT_SECONDS = 20.0  # relaunch_live: how long OBS gets to stop, and then to start, the output.
HOST = ChatUser("host", "Host", is_host=True)
SIMULATED_USER = ChatUser("test-viewer", "Test viewer", is_follower=True)
TEST_SHOW_SECONDS = 6.0  # How long a test result stays on the overlays.
# Rule-text values that come from viewers: masked on alerts when they contain blocked words.
VIEWER_VALUES = {"user", "args", "message", "mvp"}
AUTO_BAN_REASONS = {"queue.too_long": "Too long", "queue.explicit": "Explicit",
                    "queue.blocked_word": "Blocked word"}
# The same failing action (e.g. OBS closed) is reported once per this many seconds, not on every gift.
ACTION_ERROR_QUIET_SECONDS = 60.0
# Goal progress is written at most this often (likes arrive several times a second).
GOAL_SAVE_SECONDS = 5.0
# The stream report is written this often while live, so a crash keeps most of it.
REPORT_SAVE_SECONDS = 30.0
VIEWERS_FLUSH_SECONDS = 10.0  # Viewer stats and points are written this often.
LEADERBOARD_PUSH_SECONDS = 1.5  # Overlays get leaderboard updates at most this often.
GAME_PUSH_SECONDS = 0.25  # And game updates at most this often.
TEST_GAME_SECONDS = 30  # A game's Test button runs it this long with simulated players.
AUDIENCE_PUSH_SECONDS = 1.5  # The viewer list and the stats row are redrawn at most this often.
RESTART_AFTER_SECONDS = 4.0  # Previous starts the song over when it's this far in; earlier, it goes back a song.
_SAFE_ERRORS = (
    profiles.ProfileError,
    ProviderError, ObsError, BackendError, DiscordError, SpotifyError, TrackLookupError,
    QueueRejected, AccountError, ValueError,
)


def friendly(error: BaseException) -> str:
    if isinstance(error, _SAFE_ERRORS):
        return str(error)
    log.error("Unexpected error", exc_info=error)
    return "Something went wrong. Check the log for details."


def command_aliases(s: Settings) -> dict[CommandKind, tuple[str, ...]]:
    """Command names from Preferences → Commands; a blank entry falls back to the default."""
    return {
        CommandKind(kind): parse_names(s.command_names.get(kind, "")) or parse_names(default)
        for kind, default in COMMAND_DEFAULTS.items()
    }


def command_config(s: Settings) -> CommandConfig:
    aliases = command_aliases(s)
    if not s.obs_chat_clip:
        aliases.pop(CommandKind.CLIP)
    if not s.obs_chat_scene:
        aliases.pop(CommandKind.SCENE)
    if not s.panic_chat_command:
        aliases.pop(CommandKind.PANIC)
    if not points_config(s.points).enabled:
        for kind in POINTS_COMMANDS:
            aliases.pop(kind)
    return CommandConfig(
        prefix=(s.command_prefix or "!").strip()[:3] or "!",
        aliases=aliases,
        request_role=Role[s.request_role.upper()],
        skip_role=Role[s.skip_role.upper()],
        user_request_cooldown_s=float(s.user_cooldown_s),
        texts=Texts(s.texts),
    )


def action_engine_config(s: Settings) -> tuple[list[Rule], str, set[str]]:
    """Rules, command prefix, and the built-in command names that rules can't take over."""
    reserved = {name for names in command_aliases(s).values() for name in names}
    return parse_rules(s.action_rules), command_config(s).prefix, reserved


def tuning_problems(rec, keys: set[str], after) -> list[str]:
    """What OBS didn't keep after tuning (e.g. an encoder this PC or OBS build doesn't have)."""
    from ignite_live.setup.tuning import ENCODER_LABELS

    problems = []
    if "output" in keys and after.output != rec.output:
        problems.append(f"OBS kept the output size at {after.output[0]}×{after.output[1]}.")
    if "fps" in keys and round(after.fps) != rec.fps:
        problems.append(f"OBS kept {after.fps:g} FPS.")
    if after.mode == "Simple":
        if "encoder" in keys and after.encoder != rec.encoder:
            problems.append(f"OBS didn't take the {ENCODER_LABELS[rec.encoder]} encoder: it may not be available "
                            "on this PC or OBS version. Pick one in OBS → Settings → Output.")
        if "bitrate" in keys and after.bitrate != rec.bitrate:
            problems.append("OBS didn't keep the new bitrate.")
    return problems


def queue_rules(s: Settings) -> QueueRules:
    return QueueRules(
        max_size=s.max_queue,
        max_per_user=s.max_per_user,
        max_duration_s=s.max_minutes * 60,
        banned_video_ids=set(s.banned_video_ids),
        texts=Texts(s.texts),
        refuse_unknown_length=s.refuse_unknown_length,
        refuse_explicit=s.refuse_explicit,
        blocked=Moderation.from_settings(s).words.matches if s.mod_filter_songs and s.blocked_words else None,
    )


def creator_name(s: Settings) -> str:
    """How the streamer is named in Discord messages: the first platform that's set up."""
    if s.tiktok_username:
        return f"@{s.tiktok_username}"
    return s.twitch_channel or s.youtube_channel or "your stream"


def overlay_style_of(s: Settings) -> dict:
    return {"accent": s.overlay_accent, "accent2": s.overlay_accent_2, "font": s.overlay_font, "css": s.overlay_css}


class Controller(QObject):
    status_changed = Signal(object)  # StatusChange
    chat_event = Signal(object)  # ChatEvent
    chats_cleared = Signal()  # Clear All Chats: every chat panel empties.
    queue_changed = Signal(dict)  # SongQueue.snapshot()
    notice = Signal(str, str)  # level ("info" | "error"), text
    key_changed = Signal(object)  # CachedKey | None
    account_changed = Signal(object)  # AccountInfo | None
    categories_loaded = Signal(list)
    obs_streaming_changed = Signal(bool)
    targets_changed = Signal(list)  # [StreamTarget]
    playlist_changed = Signal(object)  # MusicSession.playlist_info() or None
    radio_changed = Signal(str)  # A line about autoplay for the Music tab.
    settings_changed = Signal(object)  # Settings
    busy_changed = Signal(str, bool)  # action name, busy
    studio_changed = Signal(object)  # integrations.obs_studio.Studio
    tts_skip_requested = Signal()  # From a moderator's !skiptts on the loop thread; handled on the GUI thread.
    goals_changed = Signal(list)  # GoalTracker.snapshot()
    stream_report_ready = Signal(object)  # Path of the finished stream's report
    game_changed = Signal(object)  # GameManager.snapshot(), or None when no game runs
    panic_changed = Signal(bool)  # Panic mode on or off
    panic_requested = Signal()  # A moderator's !panic, from the loop thread
    tts_mute_changed = Signal(bool)
    action_speech = Signal(str)  # A text-to-speech action, from the loop thread to the speaker on the GUI thread.
    audience_changed = Signal(object)  # Audience.snapshot(): who's here, as far as the platforms say
    stats_changed = Signal(object)  # Audience.stats_snapshot(): viewers, followers and likes per platform
    playback_changed = Signal(object)  # playback_state(): paused, position, duration…
    ytmusic_changed = Signal(object)  # The connected YouTube Music account (see self.ytmusic), or None

    def __init__(self, runtime: Runtime, settings_path: Path | None = None) -> None:
        super().__init__()
        self.runtime = runtime
        self.settings_path = settings_path or settings_store.default_path()
        self.settings = settings_store.load(self.settings_path)
        if not self.settings.overlay_token:
            self.settings.overlay_token = secrets.token_urlsafe(18)
            settings_store.save(self.settings, self.settings_path)
        self.statuses: dict[str, StatusChange] = {
            **{p: StatusChange(p, ConnectionState.OFFLINE, hint) for p, hint in CHAT_HINTS.items()},
            "obs": StatusChange("obs", ConnectionState.OFFLINE, "Not connected"),
            "overlay": StatusChange("overlay", ConnectionState.OFFLINE, "Starting…"),
            "streamlabs": StatusChange("streamlabs", ConnectionState.OFFLINE, "Not signed in"),
            "backend": StatusChange("backend", ConnectionState.OFFLINE, "Not set up"),
            "discord": StatusChange("discord", ConnectionState.OFFLINE, "Off"),
            "tts": StatusChange("tts", ConnectionState.OFFLINE, "Off"),
        }
        # Who is live right now. TikTok and YouTube chat only exist during a stream, so connected = live;
        # Twitch chat is open even offline, so its client reports LiveStatus separately.
        self.live: dict[str, bool] = dict.fromkeys(PLATFORMS, False)
        # Who's here and each platform's numbers (live/audience.py). Changed on the loop thread only; the GUI
        # reads the snapshots, which are replaced (never edited) when something changes.
        self.audience = Audience()
        self.audience_snapshot: list[dict] = []
        self.stats_snapshot: dict = self.audience.stats_snapshot()
        self._audience_pending = False
        self.music_paused = False  # The Pause button (panic pauses separately).
        # A connected YouTube Music account: {"name", "playlists": [AccountPlaylist], "error"}; None: not connected.
        self.ytmusic: dict | None = None
        self._ytmusic_account: YouTubeMusicAccount | None = None
        self._ytmusic_read = False  # Whether the keychain has been asked for a saved cookie yet.
        self.account: AccountInfo | None = None
        self.active_key: CachedKey | None = None
        self.obs_streaming = False
        self.queue_snapshot: dict = {"current": None, "upcoming": []}
        self._provider: StreamlabsExperimentalProvider | None = None
        self._obs: ObsClient | None = None
        self.targets: list[StreamTarget] = [obs_targets.MAIN]
        # Restarting the LIVE for a new title or category: the output's stop and start are one stream, not two.
        self._relaunching = False
        self._relaunch_sleep = asyncio.sleep  # Tests make the waits instant.
        self.studio = Studio()
        self._health = HealthMonitor(self.settings.obs_warn_dropped_pct)
        self._started_with_stream: set[str] = set()  # Outputs our automations started ("record", "replay").
        self._has_multistream = False
        self._obs_task: asyncio.Task | None = None
        # One chat client per platform (TikTokChat, TwitchChat, YouTubeChat), all with run()/stop().
        self._chats: dict[str, object] = {}
        self._chat_tasks: dict[str, asyncio.Task] = {}
        self._retired_chats: dict[asyncio.Task, object] = {}  # Replaced chats still closing.
        self._discord: DiscordBridge | None = None
        self._discord_task: asyncio.Task | None = None
        # Remote control from the web dashboard, through the Ignite server (integrations/relay.py).
        self._relay: RelayClient | None = None
        self._relay_task: asyncio.Task | None = None
        self._relay_target: tuple[str, str | None] | None = None  # (server URL, token) it connected with.
        self._tasks: set[asyncio.Task] = set()

        self.sounds_dir = self.settings_path.parent / "sounds"
        self.images_dir = self.settings_path.parent / "images"
        self.overlay = OverlayServer(self.settings.overlay_token, self.settings.overlay_port, sounds_dir=self.sounds_dir,
                                     images_dir=self.images_dir)
        # Viewer games: one at a time, on the loop thread. They react to simulated events too (Test buttons).
        self.games = GameManager()
        self._game_push_pending = False
        # Event → action rules (Actions tab). Used on the loop thread only.
        self.action_engine = ActionEngine(*action_engine_config(self.settings))
        self._action_errors: dict[tuple[str, str], float] = {}
        self._tts_off_warned = float("-inf")
        # Moderation (blocked words, links, viewers) and the panic button.
        self.moderation = Moderation.from_settings(self.settings)
        self.panic_state: dict | None = None  # What panic changed, so resume() puts back only that.
        self.rules_paused = False
        # Goal bars. Used on the loop thread; progress survives restarts in a local file (not synced).
        self.goal_progress_path = self.settings_path.parent / "goal_progress.json"
        self.goals = GoalTracker(parse_goals(self.settings.goals), self._load_goal_progress())
        self.goal_snapshot: list[dict] = self.goals.snapshot()
        self._goal_save_pending = False
        self.profiles_dir = self.settings_path.parent / "profiles"
        # Post-stream analytics: recorded on the loop thread while OBS streams.
        self.streams_dir = self.settings_path.parent / "streams"
        self.recorder: StreamRecorder | None = None
        # True while a simulated event (Simulate panel, Test buttons) runs through _on_chat_event:
        # rules, goals, games and overlays react; stream reports, points and leaderboards ignore it.
        self.simulating = False
        self._simulated_users: set[str] = set()  # Their chat commands run later, after `simulating` is reset.
        # Viewers: all-time stats and loyalty points (local SQLite; used on the loop thread, and the Viewers tab).
        self.viewers = ViewerStore(self.settings_path.parent / "viewers.sqlite3")
        self.points = Points(self.viewers, points_config(self.settings.points))
        self._viewers_task: asyncio.Task | None = None
        self.leaderboards = Leaderboards(self.viewers)
        self.leaderboards.currency = self.points.config.currency
        self._leaderboards_pending = False
        self._recorder_task: asyncio.Task | None = None
        self.music = MusicSession(
            self.overlay,
            self._resolve_song,
            CommandEngine(command_config(self.settings)),
            SongQueue(queue_rules(self.settings)),
            radio=self._radio_songs,
        )
        # Autoplay remembers what chat requested, between runs, to follow the same genre.
        self.music.radio_seeds = [v for v in self.settings.music_radio_seeds if video_id_from(v) == v][:20]
        self.music.on_seeds_change = lambda seeds: self.runtime.to_ui(self._radio_seeds_changed, seeds)
        self.radio_status = ""
        self.music.on_radio_change = lambda text: self.runtime.to_ui(self._radio_status_changed, text)
        publish = self.music.queue.on_change
        self.music.queue.on_change = lambda queue: (publish(queue), self._queue_changed(queue))
        self.playlist_info: dict | None = None
        self.music.on_playlist_change = self._playlist_info_changed
        self.overlay.on_clients_changed = self._overlay_clients_changed
        # Without a Now Playing overlay, a web dashboard tab can play the music (integrations/relay.py).
        self.overlay.on_music_changed = lambda state: self._relay_publish("music", state)
        self.overlay.on_toast = lambda text: self.notice.emit("info", text)
        self.overlay.on_choose_theme = lambda theme: self.runtime.to_ui(self.set_overlay_theme, theme)
        self.overlay.on_progress = lambda _progress: self._playback_changed()
        self.overlay.on_player_changed = lambda _open: self._playback_changed()
        # "Seasonal" overlays move on to the next season or holiday when the date gets there.
        self._overlay_season = seasons.resolve(self.settings.overlay_theme)
        self._season_timer = QTimer(self, interval=30 * 60 * 1000)
        self._season_timer.timeout.connect(self._overlay_season_tick)
        self._season_timer.start()
        self.music.on_other_command = self._other_command
        self.music.charge = self._charge
        self.music.on_refused = self._song_refused
        self.music.refund = self._refund

        # Text to speech runs on the GUI thread: chat events reach it through the chat_event signal.
        self.tts_rules = TtsRules(self.settings)
        self.speaker = None  # Speaker, created when text to speech is first turned on.
        self._tiktok_session = ""  # TikTok voices: the streamer's sessionid (from the keychain).
        self._edge_voice_list: list = []
        self.chat_event.connect(self._speak)
        self.tts_skip_requested.connect(self.skip_tts)
        self.action_speech.connect(self._speak_action)
        self.panic_requested.connect(self.panic)
        # What the web dashboard shows, sent through the relay when remote control is on.
        self.status_changed.connect(lambda _change: self._relay_publish("status", self._statuses_payload()))
        self.chat_event.connect(lambda event: (lambda line: line and self._relay_publish("chat", line))(chat_payload(event)))
        self.queue_changed.connect(lambda snapshot: self._relay_publish("queue", snapshot))
        self.queue_changed.connect(lambda _snapshot: self._playback_changed())
        self.panic_changed.connect(lambda _on: self._playback_changed())
        self.studio_changed.connect(lambda studio: self._relay_publish("studio", self._studio_payload(studio)))
        self.obs_streaming_changed.connect(lambda live: self._relay_publish("live", live))
        self.panic_changed.connect(lambda on: self._relay_publish("panic", on))
        self.playlist_changed.connect(lambda info: self._relay_publish("playlist", info))
        self.tts_mute_changed.connect(lambda muted: self._relay_publish("tts_muted", muted))
        self.notice.connect(lambda level, text: self._relay_publish("notice", {"level": level, "text": text}))

    # Lifecycle.

    def start(self) -> None:
        self.runtime.submit(self._start_async(), on_error=self._error)
        token = keychain.load(keychain.STREAMLABS_TOKEN)
        if token:
            self.use_streamlabs_token(token, remember=False)
        self.refresh_key()
        self.check_backend(pull_settings=True)
        if self.settings.music_playlist_url:
            self.load_playlist(self.settings.music_playlist_url, self.settings.music_playlist_shuffle, quiet=True)
        self.refresh_ytmusic(quiet=True)
        self.apply_tts()

    def shutdown(self) -> None:
        """Close connections. Deliberately does not end the LIVE: the key stays saved."""
        self.runtime.shutdown(self._stop_async())

    async def _start_async(self) -> None:
        try:
            await self.overlay.start()
            await self.overlay.set_volume(self.settings.volume)
            await self.overlay.set_theme(seasons.resolve(self.settings.overlay_theme))
            await self._push_overlay_texts()
            await self.overlay.set_style(overlay_style_of(self.settings))
            await self.overlay.set_goals(self.goal_snapshot)
            self._overlay_clients_changed(0)
            self.music.set_radio(self.settings.music_radio, self.settings.music_radio_start)  # Starts autoplay.
        except OSError:
            self._status("overlay", ConnectionState.ERROR, f"Port {self.overlay.port} is in use. Change it in Settings.")
        self._obs_task = asyncio.create_task(self._obs_loop())
        self._viewers_task = asyncio.create_task(self._viewers_loop())
        self._spawn_task(self._games_loop())
        self._restart_relay()
        for platform, field_name in PLATFORM_FIELDS.items():
            if getattr(self.settings, field_name) and platform not in self._chat_tasks:  # Not connected meanwhile.
                self._start_chat(platform, getattr(self.settings, field_name))
        self._restart_discord()

    async def _stop_async(self) -> None:
        chats = [(self._chats[p], task) for p, task in self._chat_tasks.items()]
        chats += [(chat, task) for task, chat in self._retired_chats.items() if not task.done()]
        await asyncio.gather(*(self._close_chat(chat, task) for chat, task in chats))
        if self._discord is not None:
            try:  # Send what's still queued before closing.
                await asyncio.wait_for(self._discord.flush(), 3)
            except asyncio.TimeoutError:
                pass
        if self._relay_task is not None:
            self._relay_task.cancel()
            await asyncio.wait([self._relay_task], timeout=2)
        discord_task = self._discord_task
        self._stop_discord()
        if discord_task is not None:  # Let it close its HTTP session.
            await asyncio.wait([discord_task], timeout=2)
        tasks = [task for task in (self._obs_task, *self._tasks) if task]
        for task in tasks:
            task.cancel()
        if tasks:  # Let them finish closing their sockets before the loop stops.
            await asyncio.wait(tasks, timeout=3)
        if self._obs:
            await self._obs.close()
        await self.overlay.stop()
        if self._goal_save_pending:
            self._save_goal_progress()
        if self._viewers_task is not None:
            self._viewers_task.cancel()
        self.viewers.close()
        if self._recorder_task is not None:
            self._recorder_task.cancel()
        if self.recorder is not None:  # Still live: keep what's there; it's finished as far as we know.
            self.recorder.finish(time.time())
            self._save_report()

    # Status plumbing (callable from any thread; Qt queues cross-thread signals).

    def _status(self, source: str, state: ConnectionState, detail: str = "") -> None:
        change = StatusChange(source, state, detail)
        if source in ("tiktok", "youtube"):
            self.live[source] = state is ConnectionState.CONNECTED
        elif source == "twitch" and state is not ConnectionState.CONNECTED:
            self.live[source] = False  # Not reading the channel: don't claim it's live.
        if change != self.statuses.get(source):
            log.info("%s: %s %s", source, state.value, detail)
        self.statuses[source] = change
        self.status_changed.emit(change)

    def _error(self, error: BaseException) -> None:
        self.notice.emit("error", friendly(error))

    def _run(self, name: str, fn, *args, on_success=None) -> None:
        """Run a blocking call off the GUI thread, flagging ``name`` busy meanwhile."""
        self.busy_changed.emit(name, True)

        def succeeded(result) -> None:
            self.busy_changed.emit(name, False)
            if on_success:
                on_success(result)

        def failed(error: BaseException) -> None:
            self.busy_changed.emit(name, False)
            self._error(error)

        self.runtime.blocking(fn, *args, on_success=succeeded, on_error=failed)

    # Chat: TikTok, Twitch and YouTube all feed the same events, commands, overlays, Discord, and TTS.

    def connect_chat(self, username: str) -> None:
        """TikTok (kept for older callers); see connect_platform."""
        self.connect_platform("tiktok", username)

    def connect_platform(self, platform: str, name: str) -> None:
        name = name.strip()
        if platform == "tiktok":
            name = name.lstrip("@")
        if not name:
            label = {"tiktok": "TikTok username", "twitch": "Twitch channel", "youtube": "YouTube @handle"}[platform]
            self.notice.emit("error", f"Enter your {label} first.")
            return
        field_name = PLATFORM_FIELDS[platform]
        if name != getattr(self.settings, field_name):
            setattr(self.settings, field_name, name)
            settings_store.save(self.settings, self.settings_path)
            self.settings_changed.emit(self.settings)
            if self.settings.discord_enabled and self._discord is None:
                self.restart_discord()  # Discord may have been waiting for a platform.
        self.runtime.loop.call_soon_threadsafe(self._start_chat, platform, name)

    def connect_all_chats(self) -> None:
        """Connect every platform that has a name set."""
        names = {p: getattr(self.settings, f) for p, f in PLATFORM_FIELDS.items() if getattr(self.settings, f)}
        if not names:
            self.notice.emit("error", "Add your TikTok username, Twitch channel, or YouTube @handle first.")
        for platform, name in names.items():
            self.runtime.loop.call_soon_threadsafe(self._start_chat, platform, name)

    def disconnect_chat(self, platform: str | None = None) -> None:
        """One platform, or all of them."""
        for name in [platform] if platform else list(PLATFORMS):
            self.runtime.loop.call_soon_threadsafe(self._stop_chat, name)

    def chat_connected(self, platform: str) -> bool:
        return platform in self._chat_tasks

    def _make_chat(self, platform: str, name: str, on_status):
        if platform == "tiktok":
            from ignite_live.integrations.tiktok_chat import TikTokChat  # TikTokLive is slow to import.

            return TikTokChat(name, self._on_chat_event, on_status, keychain.load(keychain.SIGN_API_KEY))
        if platform == "twitch":
            from ignite_live.integrations.twitch_chat import TwitchChat

            return TwitchChat(name, self._on_chat_event, on_status, stats=self.settings.twitch_stats)
        from ignite_live.integrations.youtube_chat import YouTubeChat

        return YouTubeChat(name, self._on_chat_event, on_status)

    def _start_chat(self, platform: str, name: str) -> None:
        self._stop_chat(platform)
        current: dict = {}

        def on_status(change: StatusChange) -> None:
            # A chat that's shutting down mustn't overwrite the new one's status.
            if self._chats.get(platform) is not current.get("chat"):
                return
            was_connected = self.statuses[platform].state is ConnectionState.CONNECTED
            self._status(change.source, change.state, change.detail)
            if change.state is not ConnectionState.CONNECTED and was_connected:
                self._platform_gone(platform)
            # Twitch chat connects while offline too; its "went live" comes from LiveStatus instead.
            if platform != "twitch" and change.state is ConnectionState.CONNECTED and not was_connected:
                self._announce_live(platform, name)

        try:
            chat = self._make_chat(platform, name, on_status)
        except ValueError as error:  # Not a valid name for that platform.
            self._status(platform, ConnectionState.ERROR, str(error))
            return
        current["chat"] = self._chats[platform] = chat
        self._chat_tasks[platform] = asyncio.create_task(chat.run())

    def _announce_live(self, platform: str, name: str) -> None:
        if self._discord is not None:
            creator = f"@{name}" if platform == "tiktok" else name
            self._discord.push_line(self._discord.texts.format(
                "discord.live", creator=escape(creator), platform=PLATFORMS[platform]))

    def _stop_chat(self, platform: str) -> None:
        task, chat = self._chat_tasks.pop(platform, None), self._chats.pop(platform, None)
        if task is not None:
            self._retired_chats[task] = chat
            task.add_done_callback(lambda done: self._retired_chats.pop(done, None))
            self._spawn_task(self._close_chat(chat, task))
        self._status(platform, ConnectionState.OFFLINE, "Not connected")
        self._platform_gone(platform)

    # Who's here, and each platform's numbers (loop thread).

    def _platform_gone(self, platform: str) -> None:
        """Chat there stopped (disconnected, or the stream ended): its viewers and counts no longer hold."""
        if self.audience.clear(platform) | self.audience.offline(platform):
            self._audience_changed()

    def _audience_event(self, event: ChatEvent) -> None:
        if isinstance(event, ChatJoin):
            changed = self.audience.seen(event.user, joined=True)
        elif isinstance(event, ChatLeave):
            changed = self.audience.left(event.user)
        else:
            changed = self.audience.update(event)
        if changed:
            self._audience_changed()

    def _audience_changed(self) -> None:
        """Hand the GUI fresh snapshots, at most every AUDIENCE_PUSH_SECONDS (joins and likes come in bursts)."""
        if self._audience_pending:
            return
        self._audience_pending = True

        def push() -> None:
            self._audience_pending = False
            self.audience.prune()
            self.audience_snapshot = self.audience.snapshot()
            self.stats_snapshot = self.audience.stats_snapshot()
            self.audience_changed.emit(self.audience_snapshot)
            self.stats_changed.emit(self.stats_snapshot)

        asyncio.get_running_loop().call_later(AUDIENCE_PUSH_SECONDS, push)

    @staticmethod
    async def _close_chat(chat, task: asyncio.Task) -> None:
        """Graceful disconnect first; cancelling only if it hangs (see TikTokChat)."""
        await chat.stop()
        _done, pending = await asyncio.wait([task], timeout=3)
        if pending:
            task.cancel()
            await asyncio.wait([task], timeout=2)

    def _on_chat_event(self, event: ChatEvent) -> None:
        if isinstance(event, ChatJoin):
            self._audience_event(event)
            if not event.listed:  # An arrival: a "joined" line in chat. Not counted, not for rules or Discord.
                self._show_line(event, discord=False)
            return
        if isinstance(event, (ChatLeave, ChannelStats)):
            self._audience_event(event)  # Only the viewer list and the stats row: not chat, rules, or reports.
            return
        if isinstance(event, ChatGift) and event.streaking:
            self._show_line(event, discord=False)  # A combo tick floods chat; the combo's last gift counts.
            return
        if isinstance(event, ViewerCount):
            self._audience_event(ChannelStats(event.platform, viewers=event.count))
        elif isinstance(event, (ChatComment, ChatGift, ChatFollow, ChatLike, ChatShare)):
            changed = self.audience.seen(event.user)
            followers = self.audience.stats.get(event.user.platform, {}).get("followers")
            if isinstance(event, ChatFollow) and followers is not None and not self.simulating:
                # Count it right away; the platform's own number replaces it at the next check.
                changed |= self.audience.update(ChannelStats(event.user.platform, followers=followers + 1))
            if changed:
                self._audience_changed()
        if isinstance(event, LiveStatus):
            went_live = event.live and not self.live.get(event.platform)
            self.live[event.platform] = event.live
            if went_live:
                self._announce_live(event.platform, getattr(self.settings, PLATFORM_FIELDS[event.platform]))
            elif not event.live:
                self._audience_event(ChannelStats(event.platform, viewers=0))
        if isinstance(event, ChatComment) and self.moderation.blocked_user(event.user):
            self.chat_event.emit(event)  # Still in your own chat panel (text to speech skips them); nowhere else.
            return
        for fired in self.action_engine.match(event):
            self._spawn_task(self._run_actions(fired))
        if not self.simulating:
            if self.recorder is not None:
                self.recorder.feed(event)
            self.points.feed(event)
            for change in self.leaderboards.feed(event):
                self._leader_changed(change)
            if isinstance(event, (ChatComment, ChatGift, ChatLike)):
                self._push_leaderboards()
        changed, results = self.games.feed(event)
        if changed:
            self._game_changed()
        for result in results:
            self._game_ended(result)
        reached = self.goals.feed(event)
        if self.goals.changed:
            self._goals_changed()
        for goal in reached:
            if self.recorder is not None:
                self.recorder.moment("goal", f"{goal.name} reached ({goal.target:,})")
            for fired in self.action_engine.match(goal):
                self._spawn_task(self._run_actions(fired))
        if isinstance(event, (ChatLike, ChatShare)):
            return  # Not chat lines (likes arrive several a second): only action rules use them.
        self._show_line(event)
        if isinstance(event, ChatComment):
            task = asyncio.create_task(self.music.handle_comment(event))
            self._tasks.add(task)
            task.add_done_callback(self._task_done)

    def _show_line(self, event: ChatEvent, discord: bool = True) -> None:
        """A chat line: the chat panels (and text to speech), the chat overlays, and Discord."""
        self.chat_event.emit(event)
        shown = self.moderation.shown(event, "overlay")
        payload = chat_payload(shown) if shown is not None else None
        if payload is not None:
            self._spawn_task(self.overlay.push_chat(payload))
        if discord and self._discord is not None:
            shown = self.moderation.shown(event, "discord")
            if shown is not None:
                self._discord.push(shown)

    def clear_chats(self) -> None:
        """Clear All Chats: the chat panel, the pop-out and the chat overlays in OBS.

        Only what Ignite shows: chat on TikTok, Twitch and YouTube themselves stays (apps can't delete it).
        """
        self.chats_cleared.emit()

        async def clear() -> None:
            await self.overlay.clear_chat()

        self.runtime.submit(clear(), on_error=self._error)

    def _other_command(self, command: Command) -> None:
        """Commands that aren't about music (runs on the loop thread)."""
        if command.kind is CommandKind.TTS_SKIP:
            self.tts_skip_requested.emit()
        elif command.kind in (CommandKind.CLIP, CommandKind.SCENE):
            self._spawn_task(self._obs_chat_command(command))
        elif command.kind in POINTS_COMMANDS:
            self._spawn_task(self._points_command(command))
        elif command.kind in (CommandKind.BLOCK, CommandKind.UNBLOCK):
            self._spawn_task(self._block_command(command))
        elif command.kind is CommandKind.PANIC:
            self.panic_requested.emit()

    def _clip_buffer(self) -> str:
        """Where clips come from: "vertical" (Aitum backtrack), "replay" (OBS), or "" if neither runs.

        The canvas TikTok streams from wins; otherwise whichever buffer is running.
        """
        vertical = self.studio.vertical
        vertical_live = bool(vertical and vertical.backtrack)
        prefer_vertical = vertical_live and self.stream_target is not None and self.stream_target.kind == "vertical"
        if prefer_vertical or (self.studio.replay is not True and vertical_live):
            return "vertical"
        return "replay" if self.studio.replay is True else ""

    async def _save_clip(self, reason: str) -> None:
        obs = self._require_obs()
        buffer = self._clip_buffer()
        if not buffer:
            raise ObsError("Clips need the replay buffer running (OBS → Start Replay Buffer).")
        # Logged first: OBS's "saved" event (with the file) can arrive before the request returns.
        moment = None
        if self.recorder is not None:
            self.recorder.moment("clip", reason)
            moment = self.recorder.moments[-1]
        try:
            if buffer == "vertical":
                await obs_targets.save_vertical_backtrack(obs)
            else:
                await obs.save_replay()
        except ObsError:
            if moment is not None and moment in self.recorder.moments:
                self.recorder.moments.remove(moment)
            raise

    async def _obs_chat_command(self, command: Command) -> None:
        texts, user = Texts(self.settings.texts), command.user.nickname
        if self._obs is None:
            return
        if command.kind is CommandKind.CLIP:
            try:
                await self._save_clip(f"!clip from {user}")
            except ObsError:
                await self.overlay.toast(texts.format("obs.clip_unavailable", user=user))
                return
            await self.overlay.toast(texts.format("obs.clip_saved", user=user))
            return
        found = self.studio.find_scene(command.args)
        if found is None:
            await self.overlay.toast(texts.format("obs.scene_unknown", user=user, scene=command.args.strip()))
            return
        await self._switch_scene(self._obs, *found)
        await self.overlay.toast(texts.format("obs.scene_switched", user=user, scene=found[0]))

    # Action rules (loop thread).

    async def _run_actions(self, fired: Fired) -> None:
        if self.rules_paused:  # Panic mode.
            return
        actions = fired.rule.actions
        # A sound in a rule with an alert plays when that alert shows, not while an earlier one is on screen.
        has_alert = any(action.kind == "alert" for action in actions)
        paired = next((action for action in actions if action.kind == "sound"), None) if has_alert else None
        for action in actions:
            if action is paired:
                continue
            try:
                await self._run_action(action, fired, paired if action.kind == "alert" else None)
            except _SAFE_ERRORS as error:
                self._action_error(fired.rule, str(error))
            if action.kind == "alert":
                paired = None  # Only the first alert gets the sound.

    async def _run_action(self, action: Action, fired: Fired, sound: Action | None = None) -> None:
        values = fired.values
        if action.kind == "alert":
            if sound is not None and self.overlay.sound_path(sound.target) is None:
                self._action_error(fired.rule, self._missing_sound(sound.target))
                sound = None  # Still show the alert, silently.
            image = fired.image if allowed_image_url(fired.image) else ""
            shown = {key: self.moderation.clean(str(value), "rules") if key in VIEWER_VALUES else value
                     for key, value in values.items()}
            await self.overlay.alert(fired.rule.name, render(action.text, shown), image,
                                     sound.target if sound else "", sound.volume if sound else 100)
        elif action.kind == "sound":
            self._check_sound(action.target)
            await self.overlay.play_sound(action.target, action.volume)
        elif action.kind == "tts":
            s = self.settings
            text = clean(render(action.text, values), s.tts_skip_links)
            if text and not self.moderation.says_blocked(text, "tts"):
                self.action_speech.emit(shorten(text, s.tts_max_chars))
        elif action.kind == "song":
            text = render(action.text, values).strip()
            if text:
                await self.music.request(fired.user or HOST, text)
        elif action.kind == "points":
            # Only real viewers: not milestones (no one to give them to) or simulated events.
            if self.points.config.enabled and fired.user is not None and not self._free(fired.user) \
                    and not self.simulating:
                self.viewers.add(fired.user, points=action.amount)
        elif action.kind == "game":
            if action.target == "stop":
                self._stop_game()
            elif self.games.running != action.target:  # Already running: leave it be.
                self._start_game(action.target)
        elif action.kind == "clip":
            self._spawn_task(self._clip_action(action, fired))  # Its delay mustn't hold up the rule's other actions.
        elif action.kind == "scene":
            await self._scene_action(action)
        elif action.kind == "source":
            await self._source_action(action)

    async def _clip_action(self, action: Action, fired: Fired) -> None:
        if action.seconds:
            await asyncio.sleep(action.seconds)
        user = fired.values.get("user")
        try:
            await self._save_clip(fired.rule.name + (f" ({user})" if user else ""))
        except _SAFE_ERRORS as error:
            self._action_error(fired.rule, str(error))

    def _check_sound(self, name: str) -> None:
        if self.overlay.sound_path(name) is None:
            raise ValueError(self._missing_sound(name))

    def _missing_sound(self, name: str) -> str:
        return f"Sound “{name}” isn't in {self.sounds_dir}."

    async def _scene_action(self, action: Action) -> None:
        obs = self._require_obs()
        found = self.studio.find_scene(action.target)
        if found is None:
            raise ObsError(f"OBS has no scene called “{action.target}”.")
        name, vertical = found
        studio = self.studio
        before = (studio.vertical.scene if vertical and studio.vertical else studio.scene)
        await self._switch_scene(obs, name, vertical)
        if action.seconds and before and before != name:
            await asyncio.sleep(action.seconds)
            now = self.studio.vertical.scene if vertical and self.studio.vertical else self.studio.scene
            if self._obs is obs and now == name:  # Unless someone switched scenes meanwhile.
                await self._switch_scene(obs, before, vertical)

    async def _source_action(self, action: Action) -> None:
        obs = self._require_obs()
        if action.mode == "toggle":
            visible = not await obs.source_visible(action.scene, action.target)
        else:
            visible = action.mode == "show"
        await obs.set_source_visible(action.scene, action.target, visible)
        if action.seconds:
            await asyncio.sleep(action.seconds)
            if self._obs is obs:
                await obs.set_source_visible(action.scene, action.target, not visible)

    def _action_error(self, rule: Rule, text: str) -> None:
        key, now = (rule.id, text), time.monotonic()
        if now - self._action_errors.get(key, float("-inf")) >= ACTION_ERROR_QUIET_SECONDS:
            self._action_errors[key] = now
            self.notice.emit("error", f"Action “{rule.name}”: {text}")

    def test_rule(self, rule: Rule) -> None:
        """The Actions tab's Test button: run a rule's actions with sample values, ignoring cooldowns."""
        trigger = rule.trigger
        values = {"user": "Ember", "platform": "TikTok", "rule": rule.name, "gift": (trigger.gifts or ("Rose",))[0],
                  "count": 1, "coins": max(1, trigger.min_coins), "message": "", "likes": trigger.every,
                  "viewers": trigger.every, "args": "Never Gonna Give You Up - Rick Astley"}
        self.runtime.submit(self._run_actions(Fired(rule, values, HOST)), on_error=self._error)

    def open_sounds_folder(self) -> Path:
        self.sounds_dir.mkdir(parents=True, exist_ok=True)
        return self.sounds_dir

    # Simulated events and tests.

    def simulate(self, *events: ChatEvent) -> None:
        """Run fake events through the app as if they came from chat (callable from the GUI)."""

        def run() -> None:
            self.simulating = True
            self._simulated_users.update(e.user.key for e in events if getattr(e, "user", None) is not None)
            try:
                for event in events:
                    self._on_chat_event(event)
            finally:
                self.simulating = False

        self.runtime.loop.call_soon_threadsafe(run)

    def test_goal(self, goal_id: str = "") -> None:
        """Show a goal (or every goal) as reached and run its "Goal reached" rules; the real progress comes back after."""

        async def test() -> None:
            bars = [dict(bar) for bar in self.goal_snapshot]
            for bar in bars:
                if not goal_id or bar["id"] == goal_id:
                    bar.update(current=bar["target"], percent=100.0, done=True)
                    reached = GoalReached(bar["id"], bar["name"], bar["target"], bar["target"], SIMULATED_USER)
                    for fired in self.action_engine.match(reached):
                        self._spawn_task(self._run_actions(fired))
            await self.overlay.set_goals(bars)
            await asyncio.sleep(TEST_SHOW_SECONDS)
            await self.overlay.set_goals(self.goal_snapshot)

        self.runtime.submit(test(), on_error=self._error)

    def play_sound(self, name: str) -> None:
        """The sounds' Play button: through the Alerts overlay, like a rule would."""
        if self.overlay.sound_path(name) is None:
            self.notice.emit("error", self._missing_sound(name))
            return
        if "alerts" not in self.overlay.open_pages():
            self.notice.emit("info", "Playing in the Alerts overlay, but none is open. Add it from the Overlays tab.")
        self.runtime.submit(self.overlay.play_sound(name))

    # Viewers and loyalty points (loop thread).

    async def _viewers_loop(self) -> None:
        ticks, first = 0, True
        while True:
            if not first:
                await asyncio.sleep(VIEWERS_FLUSH_SECONDS)
            ticks += 1
            if ticks % 6 == 0:  # About once a minute.
                self.points.tick(live=self.obs_streaming or any(self.live.values()))
            if self.audience.prune():  # Viewers not seen for a while leave the list.
                self._audience_changed()
            try:
                await asyncio.to_thread(self.viewers.flush)
                changes = self.leaderboards.refresh_alltime(announce=not first)
            except Exception as error:  # A locked or broken database mustn't stop the stream.
                log.warning("Could not save viewer stats: %s", error)
            else:
                for change in changes:
                    self._leader_changed(change)
                self._push_leaderboards()
            first = False

    def _push_leaderboards(self) -> None:
        if self._leaderboards_pending:
            return
        self._leaderboards_pending = True

        def push() -> None:
            self._leaderboards_pending = False
            self._spawn_task(self.overlay.set_leaderboards(self.leaderboards.snapshot()))

        asyncio.get_running_loop().call_later(LEADERBOARD_PUSH_SECONDS, push)

    def _leader_changed(self, change: LeaderChanged) -> None:
        self._spawn_task(self.overlay.leader(change.board, change.user.nickname, change.value))
        if self.recorder is not None and change.board.startswith("gifters"):
            self.recorder.moment("leader", f"{change.user.nickname} took #1 ({change.label.lower()})")
        for fired in self.action_engine.match(change):
            self._spawn_task(self._run_actions(fired))

    def test_leaderboard(self) -> None:
        """The leaderboard overlay's Send Test: sample boards and a "new #1" callout, then the real boards again."""
        names = ("NightOwl", "Rin", "Bo", "Kai", "Luna")

        async def test() -> None:
            sample = {board: {"title": label, "unit": self.leaderboards.currency if unit == "points" else unit,
                              "rows": [{"name": n, "platform": "tiktok", "value": (5 - i) * 1200}
                                       for i, n in enumerate(names)]}
                      for board, (label, unit, _counter) in BOARDS.items()}
            await self.overlay.set_leaderboards(sample)
            for board in BOARDS:
                await self.overlay.leader(board, names[0], 6000)
            await asyncio.sleep(TEST_SHOW_SECONDS)
            await self.overlay.set_leaderboards(self.leaderboards.snapshot())

        self.runtime.submit(test(), on_error=self._error)

    def _free(self, user: ChatUser) -> bool:
        """Simulated viewers (and the streamer) never pay."""
        return user.key in self._simulated_users or user.is_host

    def _cost(self, kind: str) -> int:
        c = self.points.config
        return {"request": c.cost_request, "bump": c.cost_bump, "say": c.cost_say}.get(kind, 0) if c.enabled else 0

    def _not_enough(self, user: ChatUser, cost: int) -> str:
        return Texts(self.settings.texts).format("points.not_enough", user=user.nickname, cost=f"{cost:,}",
                                                 points=f"{self.points.balance(user):,}",
                                                 currency=self.points.config.currency)

    def _charge(self, user: ChatUser, kind: str, cost: int | None = None) -> str:
        """Take the points for something; returns why not ("" when paid or free)."""
        cost = self._cost(kind) if cost is None else cost
        if cost <= 0 or self._free(user) or self.points.spend(user, cost):
            return ""
        return self._not_enough(user, cost)

    def _refund(self, user: ChatUser, kind: str, cost: int | None = None) -> None:
        if not self._free(user):
            self.points.refund(user, self._cost(kind) if cost is None else cost)

    def _sound_names(self) -> list[str]:
        folder = self.sounds_dir
        return sorted(p.name for p in folder.iterdir() if SOUND_NAME.match(p.name)) if folder.is_dir() else []

    async def _points_command(self, command: Command) -> None:
        texts, user, c = Texts(self.settings.texts), command.user, self.points.config
        if command.kind is CommandKind.POINTS:
            await self.overlay.toast(texts.format("points.balance", user=user.nickname,
                                                  points=f"{self.points.balance(user):,}", currency=c.currency))
        elif command.kind is CommandKind.SAY:
            s = self.settings
            if not s.tts_enabled:
                await self.overlay.toast(texts.format("points.tts_off", user=user.nickname))
                return
            message = clean(command.args, s.tts_skip_links)
            if not message or self.moderation.says_blocked(message, "tts"):
                return
            refused = self._charge(user, "say")
            if refused:
                await self.overlay.toast(refused)
                return
            self.action_speech.emit(shorten(texts.format("tts.comment", user=user.nickname, platform=user.platform_name,
                                                         message=message), s.tts_max_chars))
        elif command.kind is CommandKind.SOUND:
            names = self._sound_names()
            wanted = command.args.strip().lower()
            name = next((n for n in names if wanted in (n.lower(), n.rsplit(".", 1)[0].lower())), None)
            if name is None:
                listing = ", ".join(n.rsplit(".", 1)[0] for n in names[:10]) or "(none yet)"
                await self.overlay.toast(texts.format("points.sound_unknown", user=user.nickname, sound=wanted,
                                                      sounds=listing))
                return
            refused = self._charge(user, "sound", c.sound_price(name))
            if refused:
                await self.overlay.toast(refused)
                return
            await self.overlay.play_sound(name)
        elif command.kind is CommandKind.GIVE:
            target, _, amount = command.args.strip().partition(" ")
            try:
                count = int(amount.strip().replace(",", ""))
            except ValueError:
                return
            viewer = self.viewers.find(user.platform, target)
            if viewer is None:
                await self.overlay.toast(texts.format("points.unknown_viewer", user=user.nickname, target=target))
                return
            self.viewers.add(ChatUser(viewer.unique_id, viewer.name, platform=viewer.platform), points=count)
            await self.overlay.toast(texts.format("points.given", user=user.nickname, count=f"{count:,}",
                                                  currency=c.currency, target=viewer.name))

    def viewer_list(self, search: str = "") -> list:
        """For the Viewers tab (any thread)."""
        return self.viewers.search(search)

    def set_viewer_points(self, key: str, points: int) -> None:
        self.viewers.set_points(key, points)

    def reset_all_points(self) -> None:
        self.viewers.reset_points()

    def test_points(self) -> None:
        """The Viewers tab's Test: what viewers see for !points and when they can't afford something."""
        texts, c = Texts(self.settings.texts), self.points.config

        async def test() -> None:
            await self.overlay.toast(texts.format("points.balance", user="Test viewer", points="1,250",
                                                  currency=c.currency))
            await asyncio.sleep(2.5)
            await self.overlay.toast(texts.format("points.not_enough", user="Test viewer", cost="2,000",
                                                  points="1,250", currency=c.currency))

        self.runtime.submit(test(), on_error=self._error)

    # Setup guide: hardware and OBS tuning.

    def detect_hardware(self, on_done) -> None:
        from ignite_live.setup.hardware import detect

        self.runtime.blocking(detect, on_success=on_done, on_error=self._error)

    def read_tuning(self, on_done, on_error=None) -> None:
        """What OBS has now (ObsTuning), on the GUI thread."""
        self.runtime.submit(self._read_tuning(), on_done, on_error or self._error)

    async def _read_tuning(self):
        from ignite_live.setup.tuning import ObsTuning

        obs = self._require_obs()
        video = await obs.video_settings()
        mode = await obs.output_mode()
        encoder, bitrate, preset = await obs.simple_stream_settings() if mode == "Simple" else ("", None, "")
        denominator = video.get("fpsDenominator") or 1
        return ObsTuning(base=(int(video.get("baseWidth") or 0), int(video.get("baseHeight") or 0)),
                         output=(int(video.get("outputWidth") or 0), int(video.get("outputHeight") or 0)),
                         fps=round(float(video.get("fpsNumerator") or 0) / denominator, 2),
                         mode=mode, encoder=encoder, bitrate=bitrate, preset=preset)

    def apply_tuning(self, rec, keys: set[str], on_done) -> None:
        """Set the chosen rows (output, fps, encoder, bitrate, preset). ``on_done(problems)``: what OBS didn't keep."""

        async def apply() -> list[str]:
            if self.studio.streaming or self.studio.recording:
                raise ObsError("Stop streaming and recording first: OBS can't change these while they run.")
            before = await self._read_tuning()
            obs = self._require_obs()
            if not self.settings.tuning_backup:  # Keep the settings from before the first tuning, for Undo.
                backup = {"output_w": str(before.output[0]), "output_h": str(before.output[1]),
                          "fps": f"{before.fps:g}", "mode": before.mode, "encoder": before.encoder,
                          "bitrate": str(before.bitrate or ""), "preset": before.preset}
                self.runtime.to_ui(self._set_tuning_backup, backup)
            if keys & {"output", "fps"}:
                output = rec.output if "output" in keys else before.output
                fps = rec.fps if "fps" in keys else round(before.fps)
                await obs.set_video_settings(output, fps)
            if before.mode == "Simple" and keys & {"encoder", "bitrate", "preset"}:
                await obs.set_simple_stream_settings(
                    rec.encoder if "encoder" in keys else before.encoder,
                    rec.bitrate if "bitrate" in keys else (before.bitrate or rec.bitrate),
                    rec.preset if "preset" in keys else "")
            after = await self._read_tuning()
            return tuning_problems(rec, keys, after)

        self.runtime.submit(apply(), on_done, self._error)

    def _set_tuning_backup(self, backup: dict) -> None:
        self.save_settings(replace(self.settings, tuning_backup=backup))

    def undo_tuning(self, on_done=None) -> None:
        backup = dict(self.settings.tuning_backup)
        if not backup:
            return

        async def undo() -> None:
            if self.studio.streaming or self.studio.recording:
                raise ObsError("Stop streaming and recording first: OBS can't change these while they run.")
            obs = self._require_obs()
            await obs.set_video_settings((int(backup["output_w"]), int(backup["output_h"])),
                                         round(float(backup["fps"])))
            if backup.get("mode") == "Simple" and backup.get("encoder") and backup.get("bitrate", "").isdigit():
                await obs.set_simple_stream_settings(backup["encoder"], int(backup["bitrate"]), backup.get("preset", ""))

        def done(_result) -> None:
            self.save_settings(replace(self.settings, tuning_backup={}))
            self.notice.emit("info", "OBS is back to the settings it had before tuning.")
            if on_done:
                on_done()

        self.runtime.submit(undo(), done, self._error)

    # Moderation: blocked viewers, auto-ban, and the panic button.

    def block_viewer(self, entry: str, blocked: bool = True) -> None:
        """Block or unblock a viewer ("platform:id", or a name for any platform). GUI thread."""
        entry = entry.strip().lstrip("@")
        current = [e for e in self.settings.blocked_users if e.lower() != entry.lower()]
        if blocked and entry:
            current.append(entry)
        if current != self.settings.blocked_users:
            self.save_settings(replace(self.settings, blocked_users=current))

    async def _block_command(self, command: Command) -> None:
        texts, target = Texts(self.settings.texts), command.args.strip().split(" ")[0].lstrip("@")
        if not target:
            return
        viewer = self.viewers.find(command.user.platform, target)
        entry = viewer.key if viewer is not None else target
        blocked = command.kind is CommandKind.BLOCK
        self.runtime.to_ui(self.block_viewer, entry, blocked)
        await self.overlay.toast(texts.format("mod.blocked" if blocked else "mod.unblocked",
                                              user=command.user.nickname, target=viewer.name if viewer else target))

    def _song_refused(self, track: Track, key: str) -> None:
        """Loop thread: a song refused for what it is. With auto-ban on, it's banned for good."""
        if self.settings.auto_ban and track.video_id not in self.settings.banned_video_ids:
            self.runtime.to_ui(self._auto_ban, track.video_id, f"{AUTO_BAN_REASONS[key]}: {track.title}"[:120])

    def _auto_ban(self, video_id: str, reason: str) -> None:
        s = self.settings
        if video_id in s.banned_video_ids:
            return
        self.save_settings(replace(s, banned_video_ids=[*s.banned_video_ids, video_id],
                                   banned_reasons={**s.banned_reasons, video_id: reason}))

    @property
    def panicking(self) -> bool:
        return self.panic_state is not None

    def panic(self) -> None:
        """The panic button (GUI thread): quiet everything at once. resume() undoes exactly what this did."""
        if self.panic_state is not None:
            return
        s = self.settings
        state = {"tts": False, "rules": s.panic_rules, "alerts": s.panic_alerts, "music": s.panic_music,
                 "scene": None, "inputs": []}
        if s.panic_tts and self.speaker is not None and not self.speaker.muted:
            self.set_tts_muted(True)
            state["tts"] = True
        self.panic_state = state
        self.panic_changed.emit(True)
        self.runtime.submit(self._panic_async(state), on_error=self._error)

    def resume(self) -> None:
        state = self.panic_state
        if state is None:
            return
        if state["tts"]:
            self.set_tts_muted(False)
        self.panic_state = None
        self.panic_changed.emit(False)
        self.runtime.submit(self._resume_async(state), on_error=self._error)

    async def _panic_async(self, state: dict) -> None:
        if state["rules"]:
            self.rules_paused = True
            self._stop_game()
        await self.overlay.set_panic(quiet_alerts=state["alerts"], paused=state["music"])
        if self.recorder is not None:
            self.recorder.moment("panic", "Panic button")
        obs, s = self._obs, self.settings
        if obs is None:
            if s.panic_scene or s.panic_mute_inputs:
                self.notice.emit("error", "Panic: OBS isn't connected, so the scene and audio weren't changed.")
            return
        try:
            found = self.studio.find_scene(s.panic_scene) if s.panic_scene else None
            if s.panic_scene and found is None:
                self.notice.emit("error", f"Panic: OBS has no scene called “{s.panic_scene}”.")
            if found is not None:
                name, vertical = found
                before = (self.studio.vertical.scene if vertical and self.studio.vertical else self.studio.scene)
                if before != name:
                    await self._switch_scene(obs, name, vertical)
                    state["scene"] = (before, name, vertical)
            muted = {a.name: a.muted for a in self.studio.audio}
            for name in s.panic_mute_inputs:
                if muted.get(name) is False:
                    await obs.set_mute(name, True)
                    state["inputs"].append(name)
        except ObsError as error:
            self.notice.emit("error", f"Panic: {error}")

    async def _resume_async(self, state: dict) -> None:
        self.rules_paused = False
        await self.overlay.set_panic(quiet_alerts=False, paused=False)
        if self.recorder is not None:
            self.recorder.moment("panic", "Resumed after panic")
        obs = self._obs
        if obs is None:
            return
        try:
            if state["scene"] is not None:
                before, name, vertical = state["scene"]
                now = self.studio.vertical.scene if vertical and self.studio.vertical else self.studio.scene
                if now == name and before:  # Unless you've switched scenes yourself since.
                    await self._switch_scene(obs, before, vertical)
            for name in state["inputs"]:
                await obs.set_mute(name, False)
        except ObsError as error:
            self.notice.emit("error", f"Resume: {error}")

    # Viewer games (loop thread; start and stop are callable from the GUI).

    def start_game(self, kind: str, seconds: int | None = None) -> None:
        self.runtime.loop.call_soon_threadsafe(self._start_game, kind, seconds)

    def stop_game(self) -> None:
        self.runtime.loop.call_soon_threadsafe(self._stop_game)

    def _start_game(self, kind: str, seconds: int | None = None) -> None:
        replaced = self.games.start(kind, self.settings.games[kind], command_config(self.settings).prefix, seconds)
        if replaced is not None:
            self._game_ended(replaced)
        if self.recorder is not None and not self.simulating:
            self.recorder.game(GAMES[kind])
        self._game_changed()

    def _stop_game(self) -> None:
        ended = self.games.stop()
        if ended is not None:
            self._game_ended(ended)
        self._game_changed()

    async def _games_loop(self) -> None:
        while True:
            await asyncio.sleep(1)
            self._games_tick()

    def _games_tick(self) -> None:
        """Once a second: the timer on the overlay, and the end when it runs out."""
        showing, ended = self.games.tick()
        if ended is not None:
            self._game_ended(ended)
        if showing or ended is not None:
            self._game_changed(now=True)

    def _game_changed(self, now: bool = False) -> None:
        """Overlays get game updates at most a few times a second (likes arrive in bursts)."""
        if self._game_push_pending and not now:
            return
        self._game_push_pending = True

        def push() -> None:
            self._game_push_pending = False
            snapshot = self.games.snapshot()
            self.game_changed.emit(snapshot)
            self._spawn_task(self.overlay.set_game(snapshot))

        if now:
            push()
        else:
            asyncio.get_running_loop().call_later(GAME_PUSH_SECONDS, push)

    def _game_ended(self, result: GameEnded) -> None:
        mvp = (result.mvp.nickname or result.mvp.unique_id) if result.mvp else ""
        self._spawn_task(self.overlay.game_over({"game": result.game, "label": result.label, "winner": result.winner,
                                                 "mvp": mvp, "amount": result.mvp_amount}))
        if self.recorder is not None:
            self.recorder.moment("game", f"{result.label}: {result.winner}" + (" won" if result.winner != "Nobody" else ""))
            if result.final:
                self.recorder.game("")
        for fired in self.action_engine.match(result):
            self._spawn_task(self._run_actions(fired))

    def _simulate_now(self, events: list) -> None:
        """Like simulate(), from the loop thread."""
        self.simulating = True
        self._simulated_users.update(e.user.key for e in events if getattr(e, "user", None) is not None)
        try:
            for event in events:
                self._on_chat_event(event)
        finally:
            self.simulating = False

    def test_game(self, kind: str) -> None:
        """A game's Test button: 30 seconds with simulated players (nothing counts for points or reports)."""

        async def play() -> None:
            self._start_game(kind, TEST_GAME_SECONDS)
            game = self.games.game
            players = [ChatUser(f"test-player-{n}", name, is_follower=True)
                       for n, name in enumerate(("NightOwl", "Rin", "Bo", "Kai"))]
            config = self.settings.games[kind]
            prefix = command_config(self.settings).prefix
            if kind == "tug" and config["mode"] == "join":
                self._simulate_now([ChatComment(p, f"{prefix}join {(config['left_name'], config['right_name'])[i % 2]}", 0)
                                    for i, p in enumerate(players)])
            for step in range(TEST_GAME_SECONDS * 2):
                await asyncio.sleep(0.5)
                if self.games.game is not game:
                    return  # Over, stopped, or replaced.
                player = players[step % len(players)]
                if kind == "tug":
                    side = "left_gifts" if step % 3 else "right_gifts"
                    gift = (config[side] or ["Rose"])[0]
                    event = ChatGift(player, gift, 1 + step % 3, diamonds=5)
                elif kind == "boss":
                    event = ChatGift(player, "Rose", 1, diamonds=3) if step % 4 == 0 else ChatLike(player, 15)
                else:
                    event = ChatComment(player, str(1 + (step * 7) % len(config["options"])), 0)
                self._simulate_now([event])

        self.runtime.submit(play(), on_error=self._error)

    def open_images_folder(self) -> Path:
        self.images_dir.mkdir(parents=True, exist_ok=True)
        return self.images_dir

    # Goals (loop thread).

    # Pre-stream checklist.

    def run_checklist(self, on_done) -> None:
        """Gather what the checklist needs (on the loop) and call ``on_done(checks)`` on the GUI thread."""
        self.runtime.submit(self._preflight(), on_done, self._error)

    async def _preflight(self) -> list[checklist.Check]:
        s, studio, obs, key = self.settings, self.studio, self._obs, self.active_key
        target = self.stream_target
        rules = [rule for rule in parse_rules(s.action_rules) if rule.enabled]
        kinds = {action.kind for rule in rules for action in rule.actions}
        vertical_clips = self._clip_buffer() == "vertical" or bool(
            target and target.kind == "vertical" and studio.vertical is not None)
        p = checklist.Preflight(
            obs_connected=obs is not None,
            obs_detail=self.statuses["obs"].detail,
            key_expires_in_s=(key.expires_at - time.time()) if key else None,
            streamlabs_signed_in=self.account is not None,
            can_go_live=self.account.can_be_live if self.account else None,
            target=target,
            target_exists=target is not None,
            target_live=self.obs_streaming,
            audio=studio.audio,
            pages_open=frozenset(self.overlay.open_pages()),
            needs_alerts=bool(kinds & {"alert", "sound"}),
            needs_replay="clip" in kinds,  # Not for !clip alone: it's on by default and many never use it.
            replay=(studio.vertical.backtrack if vertical_clips and studio.vertical else studio.replay),
            replay_is_vertical=vertical_clips,
            replay_starts_with_stream=s.obs_replay_while_live,
            tiktok_username=s.tiktok_username,
            tts_enabled=s.tts_enabled,
            tts_state=self.statuses["tts"].state,
            tts_detail=self.statuses["tts"].detail,
            disk_mb=studio.health.disk_mb,
            render_lag_pct=studio.health.render_lag_pct,
            encode_lag_pct=studio.health.encode_lag_pct,
        )
        if obs is not None:
            try:
                if key and target is not None and target.kind == "main":
                    p.key_in_obs = await obs.stream_destination() == (key.credentials.server, key.credentials.key)
                muted = {a.name: a.muted for a in studio.audio}
                p.player_sources = tuple(
                    checklist.PlayerSource(name, bool(settings.get("reroute_audio")), muted.get(name))
                    for name, settings in (await obs.browser_sources()).items()
                    # Now Playing plays the music ("/player" is its name from before).
                    if (self._current_overlay_url(str(settings.get("url", ""))) or "").split("?")[0].endswith(
                        ("/nowplaying", "/player"))
                )
                if p.needs_replay and not vertical_clips and studio.replay is not None:
                    p.replay_seconds = await obs.replay_seconds()
            except ObsError as error:
                log.warning("Checklist: could not read OBS: %s", error)
        return checklist.evaluate(p)

    def start_clip_buffer(self) -> None:
        """The checklist's fix: start whichever buffer clips come from."""
        target, vertical = self.stream_target, self.studio.vertical
        if target is not None and target.kind == "vertical" and vertical is not None:
            if not vertical.backtrack:
                self.toggle_vertical_backtrack()
        elif self.studio.replay is False:
            self.toggle_replay()

    def set_replay_seconds(self, seconds: int) -> None:
        self._obs_action(lambda obs: obs.set_replay_seconds(seconds),
                         f"Clips are now {seconds} s long (from the next time the replay buffer starts).")

    # Stream reports (loop thread).

    def _start_report(self) -> None:
        self.recorder = StreamRecorder(time.time(), self.settings.stream_title)
        current = self.music.queue.current
        if current is not None:
            self.recorder.song(current.track.title, current.requested_by.nickname)
        self._recorder_task = asyncio.get_running_loop().create_task(self._autosave_report())

    async def _autosave_report(self) -> None:
        while True:
            await asyncio.sleep(REPORT_SAVE_SECONDS)
            self._save_report()

    def _save_report(self):
        if self.recorder is None:
            return None
        try:
            return analytics.save(self.recorder.to_dict(), self.streams_dir)
        except OSError as error:
            log.warning("Could not save the stream report: %s", error)
            return None

    def _finish_report(self) -> None:
        if self._recorder_task is not None:
            self._recorder_task.cancel()
            self._recorder_task = None
        if self.recorder is None:
            return
        self.recorder.finish(time.time())
        path = self._save_report()
        self.recorder = None
        if path is not None:
            self.stream_report_ready.emit(path)
            self.notice.emit("info", "Stream over. Its report is on the Streams tab.")

    def saved_streams(self) -> list[Path]:
        return analytics.saved_streams(self.streams_dir)

    def _load_goal_progress(self) -> dict:
        try:
            data = json.loads(self.goal_progress_path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _goals_changed(self) -> None:
        self.goal_snapshot = self.goals.snapshot()
        self.goals_changed.emit(self.goal_snapshot)
        self._spawn_task(self.overlay.set_goals(self.goal_snapshot))
        if not self._goal_save_pending:
            self._goal_save_pending = True
            asyncio.get_running_loop().call_later(GOAL_SAVE_SECONDS, self._save_goal_progress)

    def _save_goal_progress(self) -> None:
        self._goal_save_pending = False
        temp = self.goal_progress_path.with_suffix(".tmp")
        try:
            self.goal_progress_path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(json.dumps(self.goals.state()), "utf-8")
            os.replace(temp, self.goal_progress_path)
        except OSError as error:
            log.warning("Could not save goal progress: %s", error)

    def reset_goal(self, goal_id: str) -> None:
        def reset() -> None:
            self.goals.reset(goal_id)
            self._goals_changed()

        self.runtime.loop.call_soon_threadsafe(reset)

    # Profiles (GUI thread; small local files).

    def export_profile(self, path: Path, name: str) -> None:
        data = profiles.build(self.settings, self.sounds_dir, name, time.strftime("%Y-%m-%d %H:%M"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def save_profile(self, name: str) -> Path:
        """File → Profiles → Save Current: kept in the profiles folder to switch back to later."""
        path = self.profiles_dir / profiles.file_name(name)
        self.export_profile(path, name)
        return path

    def saved_profiles(self) -> list[Path]:
        return profiles.saved_profiles(self.profiles_dir)

    def read_profile(self, path: Path) -> profiles.Profile:
        try:
            if path.stat().st_size > profiles.MAX_PROFILE_BYTES:
                raise profiles.ProfileError("That file is too large to be an Ignite profile.")
            return profiles.read(path.read_bytes())
        except OSError as error:
            raise profiles.ProfileError(f"Couldn't open {path.name}.") from error

    def import_profile(self, profile: profiles.Profile, add_rules: bool, overwrite_sounds: bool) -> None:
        installed, kept = profiles.install_sounds(profile, self.sounds_dir, overwrite_sounds)
        self.save_settings(profiles.apply(self.settings, profile, add_rules))
        extra = f" Kept your own copy of {len(kept)} sound{'s' if len(kept) != 1 else ''}." if kept else ""
        self.notice.emit("info", f"Imported “{profile.name}”.{extra}")

    # Text to speech (GUI thread).

    def _speak_action(self, text: str) -> None:
        if self.speaker is None or not self.settings.tts_enabled:
            now = time.monotonic()
            if now - self._tts_off_warned >= ACTION_ERROR_QUIET_SECONDS:
                self._tts_off_warned = now
                self.notice.emit("error", "A rule wants to speak, but text to speech is off "
                                          "(File → Preferences → Text to speech).")
            return
        self.speaker.say(text)

    def _speak(self, event: ChatEvent) -> None:
        if self.speaker is None:
            return
        text = self.tts_rules.text_for(event)
        if text:
            self.speaker.say(text)

    def _ensure_speaker(self):
        if self.speaker is None:
            from ignite_live.speech.speaker import Speaker  # Loads the OS speech plugin: only when needed.

            self.speaker = Speaker(fetch=self._fetch_speech)
            self.speaker.status.connect(lambda change: self._status(change.source, change.state, change.detail))
        return self.speaker

    def _fetch_speech(self, s: Settings, text: str, on_audio, on_error) -> None:
        """Online voices (Edge, TikTok): fetch MP3 audio on the event loop; callbacks run on the GUI thread."""
        from ignite_live.speech import voices
        from ignite_live.speech.speaker import provider_of

        if provider_of(s) == "tiktok":
            speech = voices.tiktok_speech(text, s.tts_tiktok_voice, self._tiktok_session)
        else:
            speech = voices.edge_speech(text, s.tts_edge_voice, s.tts_rate, s.tts_pitch)
        self.runtime.submit(speech, on_audio, on_error)

    def edge_voices(self, on_voices, on_error=None) -> None:
        """Microsoft's voice list (loaded once, then cached)."""
        if self._edge_voice_list:
            on_voices(self._edge_voice_list)
            return
        from ignite_live.speech.voices import edge_voices

        def loaded(found) -> None:
            self._edge_voice_list = found
            on_voices(found)

        self.runtime.submit(edge_voices(), loaded, on_error or self._error)

    def apply_tts(self) -> None:
        self._tiktok_session = keychain.load(keychain.TIKTOK_SESSION) or ""
        self.tts_rules.update(self.settings)
        if self.settings.tts_enabled:
            self._ensure_speaker().configure(self.settings)
        elif self.speaker is not None:
            self.speaker.configure(self.settings)
        else:
            self._status("tts", ConnectionState.OFFLINE, "Off. Turn it on in File → Preferences → Text to speech.")

    def test_tts(self, settings: Settings, text: str = "", tiktok_session: str = "") -> None:
        """Preferences' Test button: speaks a sample with unsaved settings."""
        from ignite_live.core.events import ChatUser as User

        rules = TtsRules(replace(settings, tts_enabled=True, tts_platforms=list(PLATFORMS), tts_user_cooldown_s=0,
                                 tts_comments=True, tts_role="everyone", tts_command=""))
        sample = ChatComment(User("viewer", "Ember", platform="twitch"), text or "this song is fire", 0)
        # A session ID typed in Preferences but not saved yet works for the test too.
        self._tiktok_session = tiktok_session or keychain.load(keychain.TIKTOK_SESSION) or ""
        self._ensure_speaker().test(settings, rules.text_for(sample) or "Text to speech works.")

    def tts_voices(self, engine: str) -> list[str]:
        return self._ensure_speaker().voices(engine)

    def skip_tts(self) -> None:
        if self.speaker is not None:
            self.speaker.skip()

    def set_tts_muted(self, muted: bool) -> None:
        if self.speaker is not None:
            self.speaker.set_muted(muted)
        self.tts_mute_changed.emit(muted)

    async def _push_overlay_texts(self) -> None:
        await self.overlay.set_texts(Texts(self.settings.texts).group("overlay."), command_config(self.settings).request_usage)

    def _spawn_task(self, coroutine) -> None:
        """Run a coroutine on the loop (from the loop thread), keeping a reference until done."""
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            self._error(task.exception())

    # Discord bridge (runs on the event loop thread).

    def restart_discord(self) -> None:
        self.runtime.loop.call_soon_threadsafe(self._restart_discord)

    def _restart_discord(self) -> None:
        self._stop_discord()
        s = self.settings
        if not s.discord_enabled:
            self._status("discord", ConnectionState.OFFLINE, "Off. Turn it on in File → Preferences → Discord.")
            return
        url = keychain.load(keychain.DISCORD_WEBHOOK) or ""
        if not url:
            self._status("discord", ConnectionState.ERROR, "Add a webhook URL in File → Preferences → Discord.")
            return
        if not any(getattr(s, field_name) for field_name in PLATFORM_FIELDS.values()):
            self._status("discord", ConnectionState.DEGRADED, "Waiting for a TikTok, Twitch or YouTube name in the Chat panel.")
            return
        forwarding = Forwarding(s.discord_comments, s.discord_gifts, s.discord_follows)
        bridge: DiscordBridge | None = None

        def on_status(change: StatusChange) -> None:
            if self._discord is bridge:  # Ignore a replaced bridge.
                self._status(change.source, change.state, change.detail)

        try:
            bridge = DiscordBridge(url, creator_name(s), forwarding, on_status, texts=Texts(s.texts))
        except DiscordError as error:
            self._status("discord", ConnectionState.ERROR, str(error))
            return
        self._discord = bridge
        self._discord_task = asyncio.create_task(bridge.run())

    def _stop_discord(self) -> None:
        if self._discord_task is not None:
            self._discord_task.cancel()  # Our own code: safe to cancel; run() closes its session.
        self._discord = self._discord_task = None

    def test_discord(self, url: str) -> None:
        async def test() -> None:
            bridge = DiscordBridge(url, creator_name(self.settings), texts=Texts(self.settings.texts))
            try:
                await bridge.send_test()
            finally:
                await bridge.close()

        self.runtime.submit(test(), lambda _: self.notice.emit("info", "Test message sent to Discord."), self._error)

    # OBS: keep a connection while the app runs, reconnecting when OBS restarts.

    def reconnect_obs(self) -> None:
        def restart() -> None:
            if self._obs_task:
                self._obs_task.cancel()
            self._obs_task = asyncio.create_task(self._obs_loop())

        self.runtime.loop.call_soon_threadsafe(restart)

    async def _obs_loop(self) -> None:
        retrying = False
        while True:
            closed = asyncio.Event()
            password = await asyncio.to_thread(keychain.load, keychain.OBS_PASSWORD) or ""
            client = ObsClient(self.settings.obs_url, password, self._on_obs_event, on_close=closed.set)
            if not retrying:  # While OBS stays closed, retry quietly.
                self._status("obs", ConnectionState.CONNECTING, "Connecting to OBS…")
            try:
                await client.connect()
                retrying = False
                self._obs = client
                self._status("obs", ConnectionState.CONNECTED, f"OBS {client.obs_version}")
                await self._repoint_overlay_sources(client)
                await self._discover_targets(client)
                await self._refresh_studio(client)
                await self._refresh_streaming(client)
                health = asyncio.create_task(self._poll_health(client))
                try:
                    while not closed.is_set():
                        try:
                            await asyncio.wait_for(closed.wait(), TARGET_POLL_SECONDS)
                        except asyncio.TimeoutError:
                            if self.stream_target and self.stream_target.kind != "main":
                                await self._refresh_streaming(client)
                finally:
                    health.cancel()
                    await asyncio.wait([health], timeout=2)
                self._status("obs", ConnectionState.OFFLINE, "OBS closed. Waiting for it to come back…")
            except ObsError as error:
                retrying = True
                state = ConnectionState.ERROR if "password" in str(error) else ConnectionState.OFFLINE
                self._status("obs", state, str(error))
            finally:
                self._obs = None
                self._set_streaming(False)
                self._set_studio(Studio())
                self._health.reset()
                await client.close()
            await asyncio.sleep(OBS_RETRY_SECONDS)

    async def _repoint_overlay_sources(self, client: ObsClient) -> None:
        """Fix OBS sources that point at an old overlay address (e.g. 127.0.0.1 or another port)."""
        try:
            urls = await client.browser_source_urls()
            stale = {}
            for name, url in urls.items():
                fixed = self._current_overlay_url(url)
                if fixed and fixed != url:
                    stale[name] = fixed
            for name, fixed in stale.items():
                await client.set_browser_source_url(name, fixed)
        except ObsError as error:
            log.warning("Could not check OBS browser sources: %s", error)
            return
        if stale:
            self.notice.emit("info", f"Updated the music overlay link in OBS ({', '.join(stale)}).")

    async def _discover_targets(self, client: ObsClient) -> None:
        try:
            self.targets, self._has_multistream = await obs_targets.discover(client)
        except ObsError as error:
            log.warning("Could not list OBS outputs: %s", error)
            self.targets, self._has_multistream = [obs_targets.MAIN], False
        log.info("OBS outputs: %s", ", ".join(target.label for target in self.targets))
        self.targets_changed.emit(list(self.targets))

    async def _refresh_streaming(self, client: ObsClient) -> None:
        target = self.stream_target
        if target is None:
            self._set_streaming(False)
            return
        try:
            self._set_streaming(await obs_targets.is_streaming(client, target))
        except ObsError as error:
            log.warning("Could not read the stream state of %s: %s", target.label, error)

    def _current_overlay_url(self, url: str) -> str | None:
        """Same overlay page and options, at the current address; None if it isn't one of ours."""
        parts = urlsplit(url)
        if not parts.path.startswith("/overlay/") or self.overlay.token not in parts.query:
            return None
        return f"http://localhost:{self.overlay.port}{parts.path}?{parts.query}"

    def _on_obs_event(self, kind: str, data: dict) -> None:
        target = self.stream_target
        studio = self.studio
        if kind == "StreamStateChanged":
            if target is not None and target.kind == "main":
                self._set_streaming(bool(data.get("outputActive")))
            if data.get("outputState") == "OBS_WEBSOCKET_OUTPUT_RECONNECTING":
                self.notice.emit("error", "OBS lost the connection to the stream server and is reconnecting…")
            elif data.get("outputState") == "OBS_WEBSOCKET_OUTPUT_RECONNECTED":
                self.notice.emit("info", "OBS reconnected to the stream server.")
        elif kind == "CurrentProgramSceneChanged":
            self._set_studio(replace(studio, scene=str(data.get("sceneName", ""))))
            if self.recorder is not None:
                self.recorder.moment("scene", f"Switched to {data.get('sceneName', '')}")
        elif kind in ("SceneListChanged", "SceneNameChanged", "SceneCreated", "SceneRemoved",
                      "InputCreated", "InputRemoved", "InputNameChanged"):
            if self._obs is not None:
                self._spawn_task(self._refresh_studio(self._obs))
        elif kind == "InputMuteStateChanged":
            self._set_studio(studio.with_audio(str(data.get("inputName")), muted=bool(data.get("inputMuted"))))
        elif kind == "InputVolumeChanged":
            db = max(-100.0, float(data.get("inputVolumeDb") or 0.0))
            self._set_studio(studio.with_audio(str(data.get("inputName")), volume_db=db))
        elif kind == "RecordStateChanged":
            state = data.get("outputState", "")
            self._set_studio(replace(studio, recording=bool(data.get("outputActive")),
                                     record_paused=state == "OBS_WEBSOCKET_OUTPUT_PAUSED"))
        elif kind == "ReplayBufferStateChanged":
            self._set_studio(replace(studio, replay=bool(data.get("outputActive"))))
        elif kind == "ReplayBufferSaved":
            path = str(data.get("savedReplayPath", ""))
            self.notice.emit("info", f"Replay saved: {path}")
            if self.recorder is not None:
                self.recorder.clip_saved(path)
        elif kind == "VirtualcamStateChanged":
            self._set_studio(replace(studio, virtualcam=bool(data.get("outputActive"))))

    def _set_streaming(self, active: bool) -> None:
        if self._relaunching:
            return  # relaunch_live: still the same stream (goals, report, record-while-live carry on).
        if active != self.obs_streaming:
            if active:  # A new stream: like totals, viewer milestones and goals start over.
                self.action_engine.reset()
                self.points.reset_stream()
                self.leaderboards.reset_stream()
                self.goals.reset_stream()
                self._goals_changed()
                self._start_report()
            else:
                self._finish_report()
            self.obs_streaming = active
            self.obs_streaming_changed.emit(active)
            self._set_studio(replace(self.studio, streaming=active))
            if self._obs is not None:
                self._spawn_task(self._stream_automations(self._obs, active))

    # OBS panel: scenes, audio, outputs, health (state kept on the loop thread, shown via studio_changed).

    def _set_studio(self, studio: Studio) -> None:
        if studio != self.studio:
            self.studio = studio
            self.studio_changed.emit(studio)

    async def _refresh_studio(self, client: ObsClient) -> None:
        try:
            scene, scenes = await client.scenes()
            audio = await client.audio_inputs()
            recording, paused, duration = await client.record_status()
            replay = await client.replay_status()
            virtualcam = await client.virtualcam_status()
            vertical = await obs_targets.vertical_state(client)
            if vertical is not None:
                vertical = replace(vertical, canvas_uuid=await obs_targets.vertical_canvas_uuid(client, vertical.scenes))
        except ObsError as error:
            log.warning("Could not read OBS scenes and outputs: %s", error)
            return
        self._set_studio(replace(
            self.studio, connected=True, scene=scene, scenes=tuple(scenes), audio=tuple(audio), recording=recording,
            record_paused=paused, record_duration_ms=duration, replay=replay, virtualcam=virtualcam,
            streaming=self.obs_streaming, vertical=vertical,
        ))

    async def _poll_health(self, client: ObsClient) -> None:
        while True:
            try:
                stats, stream = await client.stats(), await client.stream_status()
                recording = await client.record_status()
                vertical = self.studio.vertical
                if vertical is not None:  # The plugin sends no scene events: follow it here.
                    vertical = await obs_targets.vertical_state(client, vertical.canvas_uuid)
            except ObsError as error:
                log.info("OBS stats unavailable: %s", error)
            else:
                health, warnings = self._health.sample(time.monotonic(), stats, stream)
                self._set_studio(replace(self.studio, health=health, recording=recording[0],
                                         record_paused=recording[1], record_duration_ms=recording[2],
                                         vertical=vertical))
                for warning in warnings:
                    self.notice.emit("error", warning)
            await asyncio.sleep(HEALTH_POLL_SECONDS)

    async def _stream_automations(self, client: ObsClient, live: bool) -> None:
        """Preferences → OBS: the go-live scene, and recording / replay buffer while live.

        When TikTok streams from the Aitum vertical canvas, recording and the replay buffer
        mean the vertical canvas's own recording and backtrack.
        """
        s = self.settings
        vertical = self.studio.vertical if self.stream_target and self.stream_target.kind == "vertical" else None
        try:
            if live:
                found = self.studio.find_scene(s.obs_live_scene) if s.obs_live_scene else None
                if found:
                    await self._switch_scene(client, *found)
                if vertical is not None:
                    if s.obs_record_while_live and not vertical.recording:
                        await obs_targets.set_vertical_recording(client, True)
                        self._started_with_stream.add("vertical-record")
                    if s.obs_replay_while_live and not vertical.backtrack:
                        await obs_targets.set_vertical_backtrack(client, True)
                        self._started_with_stream.add("vertical-backtrack")
                else:
                    if s.obs_record_while_live and not (await client.record_status())[0]:
                        await client.set_recording(True)
                        self._started_with_stream.add("record")
                    if s.obs_replay_while_live and await client.replay_status() is False:
                        await client.set_replay(True)
                        self._started_with_stream.add("replay")
            else:
                # Only stop what the automation started: a recording you started yourself keeps going.
                started = self._started_with_stream
                if "record" in started and (await client.record_status())[0]:
                    await client.set_recording(False)
                if "replay" in started and await client.replay_status():
                    await client.set_replay(False)
                if "vertical-record" in started:
                    await obs_targets.set_vertical_recording(client, False)
                if "vertical-backtrack" in started:
                    await obs_targets.set_vertical_backtrack(client, False)
                started.clear()
        except ObsError as error:
            self.notice.emit("error", f"OBS automation: {error}")

    async def _switch_scene(self, client: ObsClient, name: str, vertical: bool) -> None:
        if vertical:
            await obs_targets.switch_vertical_scene(client, name)
            if self.studio.vertical is not None:  # The plugin sends no event: show it right away.
                self._set_studio(replace(self.studio, vertical=replace(self.studio.vertical, scene=name)))
        else:
            await client.set_scene(name)

    def _obs_action(self, make, done: str = "") -> None:
        """Run ``make(client)`` against OBS from the GUI; errors become notices."""

        async def run():
            await make(self._require_obs())

        self.runtime.submit(run(), (lambda _: self.notice.emit("info", done)) if done else None, self._error)

    def switch_scene(self, name: str, vertical: bool = False) -> None:
        """A main scene, or (``vertical``) a scene of the Aitum vertical canvas."""
        self._obs_action(lambda obs: self._switch_scene(obs, name, vertical))

    def toggle_vertical_recording(self) -> None:
        vertical = self.studio.vertical
        if vertical is not None:
            self._obs_action(lambda obs: obs_targets.set_vertical_recording(obs, not vertical.recording))

    def toggle_vertical_backtrack(self) -> None:
        vertical = self.studio.vertical
        if vertical is not None:
            self._obs_action(lambda obs: obs_targets.set_vertical_backtrack(obs, not vertical.backtrack))

    def save_vertical_clip(self) -> None:
        vertical = self.studio.vertical
        if vertical is None or not vertical.backtrack:
            self.notice.emit("error", "Start the vertical canvas's backtrack first (OBS menu → Start Vertical Backtrack).")
            return
        self._obs_action(obs_targets.save_vertical_backtrack, "Saved a vertical clip (Aitum backtrack).")

    def set_input_mute(self, name: str, muted: bool) -> None:
        self._obs_action(lambda obs: obs.set_mute(name, muted))

    def toggle_recording(self) -> None:
        self._obs_action(lambda obs: obs.set_recording(not self.studio.recording))

    def toggle_record_pause(self) -> None:
        self._obs_action(lambda obs: obs.set_record_paused(not self.studio.record_paused))

    def toggle_replay(self) -> None:
        self._obs_action(lambda obs: obs.set_replay(not self.studio.replay))

    def save_replay(self) -> None:
        if self.studio.replay is None:
            self.notice.emit("error", "Turn on the replay buffer in OBS → Settings → Output → Replay Buffer first.")
            return
        self._obs_action(lambda obs: obs.save_replay())

    def toggle_virtualcam(self) -> None:
        self._obs_action(lambda obs: obs.set_virtualcam(not self.studio.virtualcam))

    def take_screenshot(self) -> None:
        folder = Path(self.settings.obs_screenshot_dir or Path.home() / "Pictures" / "Ignite").expanduser()
        path = folder / time.strftime("Ignite %Y-%m-%d %H-%M-%S.png")

        async def shoot(obs: ObsClient) -> None:
            await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
            await obs.screenshot(self.studio.scene or await obs.current_scene(), str(path))

        self._obs_action(shoot, f"Screenshot saved to {path}")

    def refresh_overlays(self) -> None:
        """Reload every Ignite overlay in OBS (like each browser source's Refresh button)."""

        async def refresh(obs: ObsClient) -> None:
            ours = [name for name, url in (await obs.browser_source_urls()).items() if self._current_overlay_url(url)]
            for name in ours:
                await obs.refresh_browser_source(name)
            self.notice.emit("info", f"Refreshed {len(ours)} overlay{'s' if len(ours) != 1 else ''} in OBS."
                             if ours else "No Ignite overlays found in OBS.")

        self._obs_action(refresh)

    async def _overlays_in_obs(self, obs: ObsClient) -> set[str]:
        """Which overlay pages already have a browser source in OBS (any scene)."""
        return {urlsplit(url).path.rsplit("/", 1)[-1] for url in (await obs.browser_source_urls()).values()
                if self._current_overlay_url(url)}

    def overlays_in_obs(self, on_pages) -> None:
        """For the Add Overlays dialog: ``on_pages(set of pages)`` on the GUI thread (empty if OBS isn't connected)."""

        async def find() -> set[str]:
            return await self._overlays_in_obs(self._obs) if self._obs is not None else set()

        self.runtime.submit(find(), on_pages, lambda _error: on_pages(set()))

    def add_overlays(self, pages: list[str], scene: str = "", again: bool = False, vertical: bool = False) -> None:
        """Add browser sources for these overlays to ``scene`` (default: the current one).

        ``vertical`` means a scene of the Aitum vertical canvas. Overlays already in OBS are skipped
        unless ``again``; then the new source gets a numbered name, since OBS needs unique source names.
        """
        canvas = self.studio.vertical.canvas_uuid if vertical and self.studio.vertical else ""
        if vertical and not canvas:
            self.notice.emit("error", "This OBS can't add sources to vertical scenes from other apps yet. Update OBS "
                                      "(it needs obs-websocket 5.7 or newer), or add the overlay URL there by hand.")
            return

        async def add(obs: ObsClient) -> None:
            target = scene or (self.studio.vertical.scene if vertical else await obs.current_scene())
            existing = await self._overlays_in_obs(obs)
            names = set((await obs.browser_source_urls()).keys())
            added, skipped = [], []
            for page in pages:
                if page not in PAGES:
                    continue
                if page in existing and not again:
                    skipped.append(PAGES[page][0])
                    continue
                title, width, height = PAGES[page]
                name, number = f"Ignite {title}", 2
                while name in names:
                    name, number = f"Ignite {title} {number}", number + 1
                names.add(name)
                await obs.add_browser_source(target, name, self.overlay.url(page), width, height, canvas)
                added.append(title)
            where = f"the vertical scene “{target}”" if vertical else f"“{target}”"
            message = f"Added {', '.join(added)} to {where}. Arrange them in OBS." if added else "Nothing added."
            if skipped:
                message += f" Already in OBS: {', '.join(skipped)}."
            self.notice.emit("info", message)

        self._obs_action(add)

    def _require_obs(self) -> ObsClient:
        if self._obs is None:
            raise ObsError("OBS isn't connected. Open OBS and check File → Preferences → OBS.")
        return self._obs

    @property
    def stream_target(self) -> StreamTarget | None:
        """The output chosen for TikTok, or None if it's no longer in OBS."""
        return next((t for t in self.targets if t.id == self.settings.stream_target), None)

    def set_stream_target(self, target_id: str) -> None:
        if target_id == self.settings.stream_target:
            return
        self.settings.stream_target = target_id
        settings_store.save(self.settings, self.settings_path)
        self.settings_changed.emit(self.settings)
        if self._obs is not None:
            self.runtime.submit(self._refresh_streaming(self._obs))

    def refresh_targets(self) -> None:
        async def refresh() -> None:
            await self._discover_targets(self._require_obs())

        self.runtime.submit(refresh(), on_error=self._error)

    def _require_target(self) -> StreamTarget:
        target = self.stream_target
        if target is None:
            raise ObsError("The chosen OBS output isn't there anymore. Pick one on the Stream tab.")
        return target

    def send_key_to_obs(self) -> None:
        if self.active_key is None:
            self.notice.emit("error", "Get a stream key first.")
            return
        credentials = self.active_key.credentials

        async def send() -> str:
            target = self._require_target()
            await obs_targets.apply_key(self._require_obs(), target, credentials.server, credentials.key)
            return target.label

        self.runtime.submit(
            send(), lambda label: self.notice.emit("info", f"{label} is set to stream to TikTok."), self._error
        )

    def set_obs_streaming(self, start: bool) -> None:
        async def toggle() -> None:
            obs, target = self._require_obs(), self._require_target()
            await obs_targets.set_streaming(obs, target, start, self._has_multistream)
            if target.kind != "main":  # No events from plugins: check once it has had a moment.
                await asyncio.sleep(2)
                await self._refresh_streaming(obs)

        self.runtime.submit(toggle(), on_error=self._error)

    def add_overlay_to_obs(self, page: str = "nowplaying") -> None:
        self.add_overlays([page])

    # Streamlabs and stream keys.

    def login_streamlabs(self) -> None:
        self._run(
            "streamlabs",
            lambda: StreamlabsTokenRetriever().retrieve_token(),
            on_success=lambda token: self.use_streamlabs_token(token, remember=True),
        )

    def use_streamlabs_token(self, token: str, remember: bool) -> None:
        try:
            self._provider = StreamlabsExperimentalProvider(token)
        except ProviderError as error:
            self._error(error)
            return
        if remember and not keychain.save(keychain.STREAMLABS_TOKEN, token.strip()):
            self.notice.emit("error", "No OS keychain found, so you'll need to sign in again next time.")
        self._status("streamlabs", ConnectionState.CONNECTING, "Checking TikTok LIVE access…")
        self._run("streamlabs", self._provider.account_info, on_success=self._account_loaded)

    def _account_loaded(self, info: AccountInfo) -> None:
        self.account = info
        self.account_changed.emit(info)
        if info.can_be_live:
            self._status("streamlabs", ConnectionState.CONNECTED, f"@{info.username}")
            self.search_categories("")
        else:
            self._status(
                "streamlabs",
                ConnectionState.ERROR,
                f"@{info.username} can't go LIVE through Streamlabs yet ({info.application_status.lower()}).",
            )

    def sign_out_streamlabs(self) -> None:
        keychain.forget(keychain.STREAMLABS_TOKEN)
        self._provider = None
        self.account = None
        self.account_changed.emit(None)
        self._status("streamlabs", ConnectionState.OFFLINE, "Signed out")

    def search_categories(self, query: str) -> None:
        if self._provider is None:
            return
        self.runtime.blocking(self._provider.categories, query, on_success=self.categories_loaded.emit)

    def _key_cache(self) -> BackendClient | LocalKeyCache:
        token = keychain.load(keychain.BACKEND_TOKEN)
        if self.settings.backend_url and token:
            return BackendClient(self.settings.backend_url, token)
        return LocalKeyCache()

    def refresh_key(self) -> None:
        self._run("key", lambda: self._key_cache().cached_key(), on_success=self._key_loaded)

    def get_stream_key(self, title: str, category_id: str, audience: str, force_new: bool = False) -> None:
        """Reuse the saved key while it's valid; otherwise generate and save a new one."""
        provider = self._provider
        if title.strip() and title.strip() != self.settings.stream_title:
            self.save_settings(replace(self.settings, stream_title=title.strip()))

        def obtain() -> CachedKey:
            cache = self._key_cache()
            cached = None if force_new else cache.cached_key()
            if cached:
                return cached
            if provider is None:
                raise ProviderError("Sign in to Streamlabs first, or enter a key manually.")
            return cache.store_key(provider.generate(title, category_id, audience))

        self._run("key", obtain, on_success=self._key_loaded)

    def use_manual_key(self, server: str, key: str) -> None:
        try:
            credentials = manual_credentials(server, key)
        except ProviderError as error:
            self._error(error)
            return
        self._run("key", lambda: self._key_cache().store_key(credentials), on_success=self._key_loaded)

    def end_live(self) -> None:
        """End the TikTok LIVE session and retire its key everywhere."""
        key, provider = self.active_key, self._provider

        def end() -> None:
            if key and key.credentials.session_id and provider:
                provider.end(key.credentials.session_id)
            self._key_cache().mark_key_used()

        self._run("key", end, on_success=lambda _: (self._key_loaded(None), self.notice.emit("info", "LIVE ended.")))

    def _key_loaded(self, key: CachedKey | None) -> None:
        self.active_key = key
        self.key_changed.emit(key)

    # Experimental: a new title or category while live.

    def relaunch_live(self, title: str, category_id: str, audience: str = "0") -> None:
        """Change the TikTok title or category mid-stream (Preferences → Advanced; experimental).

        Streamlabs can't edit a LIVE that's running, so this gets a new key with the new title and category,
        stops the chosen OBS output, ends the old LIVE, puts the new key on the output and starts it again.
        Viewers see the LIVE end and a new one begin. In Ignite it stays one stream: goals, points,
        leaderboards, the stream report and record-while-live carry on.
        """
        if not self.settings.experimental_relaunch:
            self.notice.emit("error", "Turn on “Change title and category while live” in Preferences → Advanced first.")
            return
        provider = self._provider
        if provider is None:
            self.notice.emit("error", "Sign in to Streamlabs first: a new title or category needs a new key from it.")
            return
        if self._relaunching:
            self.notice.emit("error", "Already restarting the LIVE…")
            return
        if title.strip() and title.strip() != self.settings.stream_title:
            self.save_settings(replace(self.settings, stream_title=title.strip()))
        old = self.active_key

        async def relaunch() -> str:
            obs, target = self._require_obs(), self._require_target()
            if not target.can_set_key or target.kind == "multistream":
                raise ObsError(f"Ignite can't change the key of “{target.label}”. Pick the main stream or a "
                               "Vertical Canvas output on the Stream tab.")
            if not await obs_targets.is_streaming(obs, target):
                raise ObsError(f"{target.label} isn't live. Before going live, Generate New Key sets the title "
                               "and category.")
            # The new key first: if Streamlabs refuses, the LIVE carries on untouched.
            fresh = await asyncio.to_thread(
                lambda: self._key_cache().store_key(provider.generate(title, category_id, audience)))
            self._key_loaded(fresh)
            self.notice.emit("info", f"New key ready. Restarting the LIVE on {target.label}…")
            self._relaunching = True
            try:
                await obs_targets.set_streaming(obs, target, False, self._has_multistream)
                await self._wait_streaming(obs, target, False)  # Failing here, the old LIVE is still on.
                try:
                    old_session = old.credentials.session_id if old else None
                    if old_session and old_session != fresh.credentials.session_id:
                        try:
                            await asyncio.to_thread(provider.end, old_session)
                        except ProviderError as error:  # The new LIVE matters more than tidying up the old one.
                            log.warning("Could not end the old LIVE session: %s", error)
                    await obs_targets.apply_key(obs, target, fresh.credentials.server, fresh.credentials.key)
                    await obs_targets.set_streaming(obs, target, True, self._has_multistream)
                    await self._wait_streaming(obs, target, True)
                except Exception as error:
                    raise ObsError(f"The LIVE stopped but didn't start again ({friendly(error)}). The new key is "
                                   "saved: press Send to OBS, then Start Streaming.") from error
            finally:
                self._relaunching = False
                if self._obs is not None:
                    await self._refresh_streaming(self._obs)  # If it didn't come back, the stream did end.
                else:
                    self._set_streaming(False)
            return target.label

        self.runtime.submit(
            relaunch(), lambda label: self.notice.emit("info", f"Back LIVE on {label} with the new title and category."),
            self._error,
        )

    async def _wait_streaming(self, obs: ObsClient, target: StreamTarget, live: bool,
                              timeout: float = RELAUNCH_WAIT_SECONDS) -> None:
        """Until the output is (or isn't) streaming; OBS takes a moment to stop and connect."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while await obs_targets.is_streaming(obs, target) != live:
            if loop.time() >= deadline:
                raise ObsError(f"{target.label} didn't {'start' if live else 'stop'} within {timeout:.0f} seconds.")
            await self._relaunch_sleep(0.5)

    # Backend.

    # Remote control (the web dashboard on your Ignite server). The app connects out; see integrations/relay.py.

    def _restart_relay(self) -> None:
        """On the event loop: (re)connect to the server's relay, or stop, to match the settings."""
        token = keychain.load(keychain.BACKEND_TOKEN)
        wanted = (self.settings.backend_url, token) if self.settings.remote_control else None
        running = self._relay_task is not None and not self._relay_task.done()
        if running and wanted == self._relay_target:
            return  # Same server and token: keep the connection it has.
        self._relay_target = wanted
        if self._relay_task is not None:
            self._relay_task.cancel()
        self._relay = self._relay_task = None
        if not self.settings.remote_control:
            self._status("remote", ConnectionState.OFFLINE, "Off. Turn it on in File → Preferences → Server.")
            return
        if not self.settings.backend_url or not token:
            self._status("remote", ConnectionState.DEGRADED, "Needs a server URL and device token (Preferences → Server).")
            return

        def on_status(state: ConnectionState, detail: str) -> None:
            if self._relay is relay:
                self._status("remote", state, detail)

        try:
            relay = RelayClient(self.settings.backend_url, token, self._remote_command, on_status,
                                on_player=self._remote_player)
        except ValueError as error:
            self._status("remote", ConnectionState.ERROR, str(error))
            return
        for kind, data in self._relay_snapshot().items():
            relay.publish(kind, data)
        self._relay = relay
        self._relay_task = asyncio.create_task(relay.run())

    def _relay_snapshot(self) -> dict:
        return {
            "live": self.obs_streaming,
            "panic": self.panicking,
            "status": self._statuses_payload(),
            "studio": self._studio_payload(self.studio),
            "queue": self.queue_snapshot,
            "playlist": self.playlist_info,
            "music": self.overlay.music_state(),
        }

    async def _remote_player(self, report: dict) -> None:
        """On the event loop: a dashboard tab playing the music reports, like the Now Playing overlay would."""
        if not self.overlay.music_state()["needed"]:
            return  # An overlay in OBS plays the music; it's the one that counts.
        if report["type"] == "progress":
            await self.overlay.outside_progress(report)
        else:
            self.music.on_player_message(report)

    def _relay_publish(self, kind: str, data) -> None:
        """From any thread: hand an update to the relay (if remote control is on)."""
        if self.settings.remote_control and self.runtime.loop.is_running():
            self.runtime.loop.call_soon_threadsafe(self._relay_publish_now, kind, data)

    def _relay_publish_now(self, kind: str, data) -> None:
        if self._relay is not None:
            self._relay.publish(kind, data)

    def _statuses_payload(self) -> dict:
        return {source: {"name": SOURCES.get(source, source), "state": change.state.value, "detail": change.detail}
                for source, change in self.statuses.items()}

    @staticmethod
    def _studio_payload(studio: Studio) -> dict:
        vertical = studio.vertical
        return {
            "connected": studio.connected, "streaming": studio.streaming, "recording": studio.recording,
            "record_paused": studio.record_paused, "record_duration_ms": studio.record_duration_ms,
            "replay": studio.replay, "scene": studio.scene, "scenes": list(studio.scenes),
            "vertical": {"scene": vertical.scene, "scenes": list(vertical.scenes)} if vertical else None,
            "health": asdict(studio.health),
        }

    async def _remote_command(self, name: str, args: dict) -> str:
        """A command from the web dashboard (already checked against relay.COMMANDS). Runs like the button would."""
        studio = self.studio
        ui = self.runtime.to_ui
        if name in ("toggle_stream", "toggle_record", "save_replay", "switch_scene") and not studio.connected:
            raise ValueError("OBS isn't connected to Ignite right now.")
        if name == "toggle_stream":
            start = not self.obs_streaming
            ui(self.set_obs_streaming, start)
            return "Starting the stream…" if start else "Stopping the stream…"
        if name == "panic":
            ui(self.panic)
            return "Panic mode on."
        if name == "resume":
            ui(self.resume)
            return "Panic mode off."
        if name == "toggle_record":
            ui(self.toggle_recording)
            return "Stopping the recording…" if studio.recording else "Starting a recording…"
        if name == "save_replay":
            ui(self.save_replay)
            return "Saving a clip…"
        if name == "switch_scene":
            scene = str(args.get("name", ""))
            vertical = studio.vertical
            if scene in studio.scenes:
                ui(self.switch_scene, scene, False)
            elif vertical is not None and scene in vertical.scenes:
                ui(self.switch_scene, scene, True)
            else:
                raise ValueError(f"OBS has no scene called “{scene[:60]}”.")
            return f"Switching to {scene}."
        if name == "skip_song":
            ui(self.skip_song)
            return "Skipped."
        if name == "remove_song":
            item = args.get("item_id")
            if not isinstance(item, int) or isinstance(item, bool):
                raise ValueError("Pick a song in the queue.")
            ui(self.remove_song, item)
            return "Removed from the queue."
        if name == "request_song":
            text = " ".join(str(args.get("text", "")).split())[:200]
            if not text:
                raise ValueError("Type a song and artist, or paste a YouTube link.")
            ui(self.request_song, text)
            return f"Requested {text}."
        if name == "skip_tts":
            ui(self.skip_tts)
            return "Skipped."
        if name == "mute_tts":
            muted = args.get("muted") is True
            ui(self.set_tts_muted, muted)
            return "Text to speech muted." if muted else "Text to speech on."
        raise ValueError("Ignite doesn't take that command remotely.")

    def check_backend(self, pull_settings: bool = False) -> None:
        token = keychain.load(keychain.BACKEND_TOKEN)
        if self.runtime.loop.is_running():
            self.runtime.loop.call_soon_threadsafe(self._restart_relay)  # The server settings may have changed.
        if not self.settings.backend_url or not token:
            self._status("backend", ConnectionState.OFFLINE, "Not set up: keys are saved on this device only")
            return
        url = self.settings.backend_url

        def check() -> dict | None:
            # Fetching settings doubles as an authenticated health check.
            return BackendClient(url, token).settings()

        def ok(remote: dict | None) -> None:
            self._status("backend", ConnectionState.CONNECTED, url)
            if pull_settings and remote:
                self.settings.apply(remote, only=settings_store.SYNCED_FIELDS)
                self._apply_settings()
                self.apply_tts()
                self.runtime.submit(self._push_overlay_texts())
                self.runtime.submit(self.overlay.set_style(overlay_style_of(self.settings)))

        def failed(error: BaseException) -> None:
            self._status("backend", ConnectionState.ERROR, friendly(error))

        self.runtime.blocking(check, on_success=ok, on_error=failed)

    # Music.

    def _resolve_song(self, text: str) -> Track:
        """Blocking; called on a worker thread by MusicSession."""
        token = keychain.load(keychain.BACKEND_TOKEN)
        request = parse_request(text)
        if self.settings.backend_url and token:
            try:
                track = BackendClient(self.settings.backend_url, token).resolve_song(text)
            except BackendError as error:
                raise TrackLookupError(str(error)) from error
            if track.duration_s is None and isinstance(request, LinkRequest):  # Older servers don't look it up.
                track = replace(track, duration_s=YouTubeClient().watch_length(request.video_id))
            return track
        if request is None:
            raise TrackLookupError("Send a YouTube link or song - artist.")
        return YouTubeClient(keychain.load(keychain.YOUTUBE_API_KEY) or "",
                             prefer_clean=self.settings.refuse_explicit).resolve(request)

    def request_song(self, text: str) -> None:
        comment = ChatComment(HOST, f"!sr {text.strip()}", int(time.time() * 1000))
        self.runtime.submit(self.music.handle_comment(comment), on_error=self._error)

    def skip_song(self) -> None:
        self.runtime.loop.call_soon_threadsafe(self.music.queue.advance)

    # Playback buttons (Music tab, Music menu, the pop-out chat). The music itself plays in the Now Playing
    # overlay; these tell it what to do.

    def set_music_paused(self, paused: bool) -> None:
        if paused == self.music_paused:
            return
        self.music_paused = paused
        self.runtime.submit(self.overlay.set_paused(paused), on_error=self._error)
        self._playback_changed()

    def toggle_music_pause(self) -> None:
        self.set_music_paused(not self.music_paused)

    def previous_song(self) -> None:
        """Start the song over; pressed in its first seconds (or with nothing playing), go back a song."""

        def back() -> None:
            queue = self.music.queue
            current, progress = queue.current, self.overlay.progress or {}
            position = progress.get("position", 0.0) if current and progress.get("item_id") == current.id else 0.0
            if current is not None and (position > RESTART_AFTER_SECONDS or not queue.history):
                self._spawn_task(self.overlay.seek(current.id, 0))
            else:
                queue.previous()

        self.runtime.loop.call_soon_threadsafe(back)

    def playback_state(self) -> dict:
        """For the playback buttons. ``player``: whether a Now Playing overlay is open to play anything."""
        current = self.queue_snapshot.get("current")
        progress = self.overlay.progress or {}
        mine = bool(current) and progress.get("item_id") == current["id"]
        panic = bool(self.panic_state and self.panic_state.get("music"))
        return {
            "paused": self.music_paused or panic,
            "panic": panic,
            "player": self.overlay.has_player,
            "current": current,
            "has_previous": bool(self.queue_snapshot.get("has_previous")),
            "position": float(progress.get("position", 0.0)) if mine else 0.0,
            "duration": float(progress.get("duration", 0.0)) if mine else 0.0,
            "playing": bool(mine and progress.get("playing")),
        }

    def _playback_changed(self) -> None:
        """From any thread (the overlay reports progress on the loop thread)."""
        self.playback_changed.emit(self.playback_state())

    # YouTube Music account: your own playlists and Liked Music as background music (music/ytmusic_account.py).

    def _ytmusic(self) -> YouTubeMusicAccount | None:
        """The connected account, read from the keychain the first time. Blocking: call it off the GUI thread."""
        if self._ytmusic_account is None and not self._ytmusic_read:
            self._ytmusic_read = True
            cookie = keychain.load_long(keychain.YTMUSIC_COOKIE)
            if cookie:
                try:
                    self._ytmusic_account = YouTubeMusicAccount(cookie)
                except AccountError:
                    pass  # Not a usable cookie any more: treated as not connected.
        return self._ytmusic_account

    def _ytmusic_loaded(self, account: YouTubeMusicAccount | None, name: str = "", playlists: tuple = (),
                        error: str = "") -> None:
        self._ytmusic_account = account
        self.ytmusic = {"name": name, "playlists": list(playlists), "error": error} if account or error else None
        self.ytmusic_changed.emit(self.ytmusic)

    def connect_ytmusic(self, cookie: str) -> None:
        """Check a pasted music.youtube.com cookie by reading the library, then keep it in the keychain."""

        def connect():
            account = YouTubeMusicAccount(cookie)
            playlists = account.playlists()  # Raises unless YouTube Music treats the cookie as signed in.
            saved = keychain.save_long(keychain.YTMUSIC_COOKIE, account.cookie)
            return account, account.name(), playlists, saved

        def connected(result) -> None:
            account, name, playlists, saved = result
            self._ytmusic_read = True
            self._ytmusic_loaded(account, name, playlists)
            who = f" as {name}" if name else ""
            self.notice.emit("info", f"YouTube Music connected{who}: {len(playlists)} playlists. Pick one on the "
                                     "Music tab to use it as background music.")
            if not saved:
                self.notice.emit("error", "No OS keychain found (or the cookie is too long for it), so you'll "
                                          "need to connect YouTube Music again next time.")

        self._run("ytmusic", connect, on_success=connected)

    def refresh_ytmusic(self, quiet: bool = False) -> None:
        """Read the connected account's playlists again (also at startup, quietly, if one was saved)."""

        def refresh():
            account = self._ytmusic()
            if account is None:
                return None
            try:
                return account, account.name(), account.playlists(), ""
            except AccountError as error:
                return account, "", (), str(error)

        def done(result) -> None:
            if result is None:
                if not quiet:
                    self.notice.emit("error", "YouTube Music isn't connected.")
                return
            account, name, playlists, error = result
            if not error:
                self._ytmusic_loaded(account, name, playlists)
                return
            # Keep the account: it may be a network hiccup, and a saved playlist link can still be tried.
            self._ytmusic_loaded(account, error=error)
            if not quiet:
                self.notice.emit("error", error)

        self._run("ytmusic", refresh, on_success=done)

    def disconnect_ytmusic(self) -> None:
        keychain.forget_long(keychain.YTMUSIC_COOKIE)
        self._ytmusic_read = True
        self._ytmusic_loaded(None)
        self.notice.emit("info", "YouTube Music disconnected: its cookie was removed from this PC's keychain.")

    def remove_song(self, item_id: int) -> None:
        self.runtime.loop.call_soon_threadsafe(self.music.queue.remove, item_id)

    def clear_queue(self) -> None:
        self.runtime.loop.call_soon_threadsafe(self.music.queue.clear)

    def load_playlist(self, link: str, shuffle: bool, quiet: bool = False) -> None:
        """A public Spotify, YouTube or YouTube Music playlist that plays whenever the queue is empty.

        Spotify: reads the playlist's public page for song names and looks each one up on YouTube Music
        right before it plays. YouTube: its songs play as they are, no lookups.
        """

        def loaded(playlist: Playlist) -> None:
            if self.settings.music_playlist_url != link or self.settings.music_playlist_shuffle != shuffle:
                self.save_settings(replace(self.settings, music_playlist_url=link, music_playlist_shuffle=shuffle))
            self.runtime.loop.call_soon_threadsafe(self.music.set_playlist, playlist, shuffle)
            if not quiet:
                self.notice.emit(
                    "info", f"Loaded “{playlist.name}” ({len(playlist.songs)} songs). It plays when the queue is empty."
                )

        self._run("playlist", self._fetch_playlist, link, on_success=loaded)

    def _fetch_playlist(self, link: str) -> Playlist:
        """Blocking: a Spotify or a YouTube / YouTube Music playlist link."""
        playlist_id = playlist_id_from(link)
        if playlist_id is None:
            return fetch_public_playlist(link)  # Refuses anything that isn't a Spotify playlist either.
        account = self._ytmusic()  # Signed in, the account's private playlists and Liked Music load too.
        name, tracks = YouTubeClient(prefer_clean=self.settings.refuse_explicit,
                                     account_headers=account.headers if account else None).playlist(playlist_id)
        songs = tuple(PlaylistSong(t.title, t.channel, (t.duration_s or 0) * 1000, track=t) for t in tracks)
        return Playlist(playlist_id, name, songs)

    # Autoplay.

    def set_radio(self, on: bool, start: str) -> None:
        """Autoplay songs like chat's requests (same genre) whenever nothing is queued and no playlist is on."""
        start = " ".join(start.split())[:100]
        if (on, start) != (self.settings.music_radio, self.settings.music_radio_start):
            self.save_settings(replace(self.settings, music_radio=on, music_radio_start=start))

    def _radio_songs(self, video_id: str) -> list[Track]:
        """Blocking; called on a worker thread by MusicSession."""
        return YouTubeClient(prefer_clean=self.settings.refuse_explicit).radio(video_id)

    def _radio_seeds_changed(self, seeds: list[str]) -> None:
        if seeds != self.settings.music_radio_seeds:
            self.settings.music_radio_seeds = seeds
            settings_store.save(self.settings, self.settings_path)

    def _radio_status_changed(self, text: str) -> None:
        self.radio_status = text
        self.radio_changed.emit(text)

    def stop_playlist(self) -> None:
        if self.settings.music_playlist_url:
            self.save_settings(replace(self.settings, music_playlist_url=""))
        self.runtime.loop.call_soon_threadsafe(self.music.clear_playlist)
        self._playlist_info_changed(None)

    def _overlay_season_tick(self) -> None:
        theme = seasons.resolve(self.settings.overlay_theme)
        if self.settings.overlay_theme == seasons.SEASONAL and theme != self._overlay_season:
            self._overlay_season = theme
            self.runtime.submit(self.overlay.set_theme(theme))

    def set_overlay_theme(self, theme: str) -> None:
        if theme != self.settings.overlay_theme:
            self.save_settings(replace(self.settings, overlay_theme=theme))

    def _playlist_info_changed(self, info: dict | None) -> None:
        self.playlist_info = info
        self.playlist_changed.emit(info)

    def set_volume(self, volume: int) -> None:
        self.settings.volume = volume
        settings_store.save(self.settings, self.settings_path)
        self.runtime.submit(self.overlay.set_volume(volume))

    def _queue_changed(self, queue: SongQueue) -> None:
        if self.recorder is not None:
            current = queue.current
            self.recorder.song(current.track.title if current else "", current.requested_by.nickname if current else "")
        self.queue_snapshot = queue.snapshot()
        self.queue_changed.emit(self.queue_snapshot)

    def _overlay_clients_changed(self, count: int) -> None:
        if count:
            self._status("overlay", ConnectionState.CONNECTED, f"{count} overlay{'s' if count > 1 else ''} open")
        else:
            self._status("overlay", ConnectionState.DEGRADED, "Running. Add overlays to OBS from the Overlays tab.")

    # Settings.

    def save_settings(self, new: Settings) -> None:
        old = replace(self.settings)
        self.settings = new
        self._health.warn_dropped_pct = new.obs_warn_dropped_pct
        settings_store.save(new, self.settings_path)
        self._apply_settings()
        if new.overlay_theme != old.overlay_theme:
            self._overlay_season = seasons.resolve(new.overlay_theme)
            self.runtime.submit(self.overlay.set_theme(self._overlay_season))
        if new.obs_url != old.obs_url:
            self.reconnect_obs()
        discord_fields = ("discord_enabled", "discord_comments", "discord_gifts", "discord_follows", "texts",
                          *PLATFORM_FIELDS.values())
        if any(getattr(new, name) != getattr(old, name) for name in discord_fields):
            self.restart_discord()
        for platform, field_name in PLATFORM_FIELDS.items():
            name = getattr(new, field_name)
            if name != getattr(old, field_name):
                if name:
                    self.runtime.loop.call_soon_threadsafe(self._start_chat, platform, name)
                else:
                    self.disconnect_chat(platform)
        if new.twitch_stats != old.twitch_stats and new.twitch_channel == old.twitch_channel \
                and self.chat_connected("twitch"):
            self.runtime.loop.call_soon_threadsafe(self._start_chat, "twitch", new.twitch_channel)  # Reads them or not.
        if any(getattr(new, n) != getattr(old, n) for n in ("texts", "command_prefix", "command_names")):
            self.runtime.submit(self._push_overlay_texts())
        if overlay_style_of(new) != overlay_style_of(old):
            self.runtime.submit(self.overlay.set_style(overlay_style_of(new)))
        if any(getattr(new, f.name) != getattr(old, f.name) for f in fields(Settings)
               if f.name.startswith("tts_") or f.name in ("texts", "command_prefix")):
            self.apply_tts()
        token = keychain.load(keychain.BACKEND_TOKEN)
        if new.backend_url and token and new.synced() != old.synced():
            synced = new.synced()
            self.runtime.blocking(
                lambda: BackendClient(new.backend_url, token).save_settings(synced), on_error=self._error
            )

    def _apply_settings(self) -> None:
        settings_store.save(self.settings, self.settings_path)
        self.moderation = Moderation.from_settings(self.settings)
        config, rules = command_config(self.settings), queue_rules(self.settings)
        actions = action_engine_config(self.settings)
        goals = parse_goals(self.settings.goals)
        points = points_config(self.settings.points)
        radio = (self.settings.music_radio, self.settings.music_radio_start)

        def apply() -> None:
            self.music.engine.config = config
            self.music.queue.rules = rules
            if radio != (self.music.radio_on, self.music.radio_start):
                self.music.set_radio(*radio)
            self.action_engine.update(*actions)
            self.points.config = points
            self.leaderboards.currency = points.currency
            if goals != self.goals.goals:
                self.goals.update(goals)
                self._goals_changed()

        self.runtime.loop.call_soon_threadsafe(apply)
        self.settings_changed.emit(self.settings)
