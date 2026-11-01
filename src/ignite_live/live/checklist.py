"""Pre-stream checklist: is everything ready to go live? Pure logic, no I/O.

The controller gathers a ``Preflight`` snapshot (OBS state, the key, which overlays
are open…) and ``evaluate`` turns it into checks. A check's ``fix`` is an action id
the UI knows how to run (e.g. ``send_key``, ``unmute:Mic/Aux``).
"""

from __future__ import annotations

from dataclasses import dataclass

from ignite_live.core.events import ConnectionState
from ignite_live.integrations.obs import AudioInput
from ignite_live.integrations.obs_studio import LOW_DISK_MB
from ignite_live.integrations.obs_targets import StreamTarget

OK, INFO, WARN, FAIL = "ok", "info", "warn", "fail"
KEY_WARN_S = 2 * 3600
LAG_WARN_PCT = 5.0
REPLAY_SECONDS = (30, 60)  # A clip length that suits TikTok.
SILENT_DB = -99.0


@dataclass(frozen=True)
class Check:
    id: str
    title: str
    status: str
    detail: str = ""
    fix: str = ""  # Action id; empty: nothing the app can do for you.
    fix_label: str = ""


@dataclass(frozen=True)
class PlayerSource:
    name: str
    reroute_audio: bool  # "Control audio via OBS": otherwise the music can't be heard on stream reliably.
    muted: bool | None  # None: not in the OBS mixer (audio not rerouted).


@dataclass
class Preflight:
    obs_connected: bool = False
    obs_detail: str = ""
    key_expires_in_s: float | None = None  # None: no key.
    key_in_obs: bool | None = None  # None: can't read it back from this output.
    streamlabs_signed_in: bool = False
    can_go_live: bool | None = None
    target: StreamTarget | None = None
    target_exists: bool = True
    target_live: bool = False
    audio: tuple[AudioInput, ...] = ()
    pages_open: frozenset[str] = frozenset()
    player_sources: tuple[PlayerSource, ...] = ()
    needs_alerts: bool = False  # Rules show alerts or play sounds.
    needs_replay: bool = False  # Clip rules or !clip.
    replay: bool | None = None  # The buffer clips come from: OBS's, or the vertical canvas's backtrack.
    replay_is_vertical: bool = False
    replay_starts_with_stream: bool = False
    replay_seconds: int | None = None
    tiktok_username: str = ""
    tts_enabled: bool = False
    tts_state: ConnectionState = ConnectionState.OFFLINE
    tts_detail: str = ""
    disk_mb: float = 0.0
    render_lag_pct: float = 0.0
    encode_lag_pct: float = 0.0


def _hours(seconds: float) -> str:
    hours = seconds / 3600
    return f"{hours:.0f} hours" if hours >= 2 else f"{max(1, round(seconds / 60))} minutes"


def evaluate(p: Preflight) -> list[Check]:
    checks: list[Check] = []
    add = checks.append

    # OBS and the output TikTok gets.
    if p.obs_connected:
        add(Check("obs", "OBS is connected", OK))
    else:
        add(Check("obs", "OBS isn't connected", FAIL,
                  p.obs_detail or "Open OBS and turn on its WebSocket server.", "open:settings", "Open Preferences"))
    if p.obs_connected:
        target = p.target
        if target is None or not p.target_exists:
            add(Check("output", "Pick the output TikTok streams from", FAIL,
                      "The chosen OBS output is gone. Choose one on the Stream page.", "open:stream", "Open Stream"))
        elif target.kind == "multistream":
            add(Check("output", f"Output: {target.label}", WARN,
                      "Aitum Multistream doesn't let other apps set its key: paste the key into that output yourself."))
        elif p.target_live:
            add(Check("output", f"Already live on {target.label}", INFO))
        else:
            add(Check("output", f"Output: {target.label}", OK))

    # The key.
    if p.key_expires_in_s is None:
        fix = ("get_key", "Get Stream Key") if p.streamlabs_signed_in else ("open:stream", "Open Stream")
        add(Check("key", "No stream key", FAIL, "Get one from Streamlabs or paste one on the Stream page.", *fix))
    elif p.key_expires_in_s <= 0:
        add(Check("key", "The stream key has expired", FAIL, "Get a new one.", "get_key", "Get Stream Key"))
    elif p.key_expires_in_s < KEY_WARN_S:
        add(Check("key", "The stream key expires soon", WARN,
                  f"It's kept for {_hours(p.key_expires_in_s)} more. A fresh key avoids a cut-off mid-stream.",
                  "get_key", "Get Stream Key"))
    else:
        add(Check("key", "Stream key ready", OK, f"Kept for {_hours(p.key_expires_in_s)} more."))
    if p.obs_connected and p.key_expires_in_s and p.key_expires_in_s > 0:
        if p.key_in_obs is False:
            add(Check("key_obs", "OBS has a different key", WARN,
                      "OBS would stream somewhere else.", "send_key", "Send Key to OBS"))
        elif p.key_in_obs is None and p.target is not None and p.target.kind == "vertical":
            add(Check("key_obs", "Key in the vertical canvas", INFO,
                      "Aitum Vertical Canvas can't report its key back: send it again if in doubt.",
                      "send_key", "Send Key to OBS"))
        elif p.key_in_obs:
            add(Check("key_obs", "OBS has this key", OK))
    if p.streamlabs_signed_in and p.can_go_live is False:
        add(Check("eligible", "Streamlabs says this account can't go LIVE", FAIL,
                  "TikTok hasn't approved LIVE access through Streamlabs yet. A key from elsewhere still works."))

    # Audio.
    if p.obs_connected:
        players = {source.name for source in p.player_sources}
        inputs = [a for a in p.audio if a.name not in players]
        silent = [a for a in inputs if a.muted or a.volume_db <= SILENT_DB]
        if inputs and len(silent) == len(inputs):
            add(Check("audio", "Every audio source is muted", FAIL, "Viewers won't hear anything.",
                      f"unmute:{silent[0].name}", f"Unmute {silent[0].name}"))
        else:
            for a in silent:
                add(Check(f"audio:{a.name}", f"“{a.name}” is muted", WARN,
                          "Fine if that's on purpose.", f"unmute:{a.name}", "Unmute"))
            if inputs and not silent:
                add(Check("audio", "Audio sources are on", OK))

    # Overlays.
    if p.obs_connected:
        if not p.player_sources:
            add(Check("player", "The Now Playing overlay isn't in OBS", WARN,
                      "It plays the song requests: without it they won't be seen or heard.",
                      "add_overlay:nowplaying", "Add to OBS"))
        else:
            # With copies in several scenes, judge the best set-up one (only one copy plays the sound).
            source = min(p.player_sources, key=lambda s: (not s.reroute_audio, bool(s.muted)))
            if not source.reroute_audio:
                add(Check("player", "Music audio isn't controlled by OBS", WARN,
                          f"In OBS, open “{source.name}” and tick Control audio via OBS."))
            elif source.muted:
                add(Check("player", "The music is muted in OBS", WARN, "", f"unmute:{source.name}", "Unmute"))
            elif "nowplaying" not in p.pages_open:
                add(Check("player", "The Now Playing overlay isn't loaded", WARN,
                          f"“{source.name}” is in OBS but not running: show it in your scene, or untick "
                          "“Shutdown source when not visible”."))
            else:
                add(Check("player", "Music player loaded", OK))
        if p.needs_alerts and "alerts" not in p.pages_open:
            add(Check("alerts", "Your rules need the Alerts overlay", WARN,
                      "Alerts and sounds from the Actions tab show up there.", "add_overlay:alerts", "Add to OBS"))

    # Clips.
    if p.obs_connected and p.needs_replay:
        what = "vertical backtrack" if p.replay_is_vertical else "replay buffer"
        if p.replay is None:
            add(Check("replay", "Clips need the replay buffer", WARN,
                      "Turn it on in OBS → Settings → Output → Replay Buffer."))
        elif not p.replay and p.replay_starts_with_stream:
            add(Check("replay", f"The {what} starts when you go live", OK))
        elif not p.replay:
            add(Check("replay", f"The {what} is off", WARN, "Clips can't be saved until it runs.",
                      "start_replay", "Start It"))
        else:
            add(Check("replay", f"The {what} is running", OK))
        if (not p.replay_is_vertical and p.replay is not None and p.replay_seconds is not None
                and not REPLAY_SECONDS[0] <= p.replay_seconds <= REPLAY_SECONDS[1]):
            add(Check("replay_length", f"Clips are {p.replay_seconds} s long", INFO,
                      "30–60 s suits TikTok. Changing it needs the buffer stopped.",
                      "" if p.replay else "replay_45", "" if p.replay else "Set to 45 s"))

    # Chat and voice.
    if not p.tiktok_username:
        add(Check("chat", "No TikTok username", WARN, "Chat, gifts and song requests won't come in.",
                  "open:chat", "Add It"))
    else:
        add(Check("chat", f"Chat ready for @{p.tiktok_username}", OK,
                  "It connects by itself once the LIVE starts."))
    if p.tts_enabled and p.tts_state is ConnectionState.ERROR:
        add(Check("tts", "Text to speech has a problem", WARN, p.tts_detail, "open:prefs-Text to speech", "Open"))

    # OBS health.
    if p.obs_connected:
        if 0 < p.disk_mb < LOW_DISK_MB:
            add(Check("disk", "Low disk space", WARN,
                      f"{p.disk_mb / 1024:.1f} GB left for recordings and clips."))
        if p.render_lag_pct >= LAG_WARN_PCT or p.encode_lag_pct >= LAG_WARN_PCT:
            add(Check("lag", "OBS is struggling to keep up", WARN,
                      "Frames are being skipped. Lower the resolution or frame rate, or close other apps."))
    return checks


def counts(checks: list[Check]) -> tuple[int, int]:
    """(failures, warnings)"""
    return sum(c.status == FAIL for c in checks), sum(c.status == WARN for c in checks)
