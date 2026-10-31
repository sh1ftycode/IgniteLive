"""What the OBS panel shows: scenes, audio, outputs, and stream health. Pure logic, no I/O.

Health comes from polling ``GetStats`` and ``GetStreamStatus`` every couple of
seconds. OBS reports running totals, so rates (bitrate, % of frames dropped)
are computed from the change since the previous sample. Three different
problems are told apart, because each has a different fix:

- **network**: frames dropped because the upload can't keep up (lower the bitrate);
- **rendering**: frames OBS couldn't draw in time (the GPU is overloaded);
- **encoding**: frames the encoder skipped (the CPU or encoder is overloaded).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ignite_live.integrations.obs import AudioInput, ObsStats, StreamStatus
from ignite_live.integrations.obs_targets import VerticalCanvas

WARN_COOLDOWN_S = 90.0
LOW_DISK_MB = 5 * 1024


@dataclass(frozen=True)
class Health:
    fps: float = 0.0
    cpu: float = 0.0
    memory_mb: float = 0.0
    disk_mb: float = 0.0
    bitrate_kbps: int = 0
    network_dropped_pct: float = 0.0  # Over the last sample interval.
    render_lag_pct: float = 0.0
    encode_lag_pct: float = 0.0
    stream_duration_ms: int = 0
    reconnecting: bool = False


@dataclass(frozen=True)
class Studio:
    """A snapshot for the UI; replaced (never mutated) on every change."""

    connected: bool = False
    scene: str = ""
    scenes: tuple[str, ...] = ()
    audio: tuple[AudioInput, ...] = ()
    recording: bool = False
    record_paused: bool = False
    record_duration_ms: int = 0
    replay: bool | None = None  # None: the replay buffer isn't enabled in OBS.
    virtualcam: bool | None = None
    streaming: bool = False
    health: Health = field(default_factory=Health)
    vertical: VerticalCanvas | None = None  # Aitum Vertical Canvas, when installed.

    def find_scene(self, wanted: str) -> tuple[str, bool] | None:
        """A scene by name from chat or settings: (name, is_vertical). Main scenes win ties."""
        main = match_scene(wanted, self.scenes)
        if main:
            return main, False
        vertical = match_scene(wanted, self.vertical.scenes) if self.vertical else None
        return (vertical, True) if vertical else None

    def with_audio(self, name: str, **changes) -> "Studio":
        return replace(self, audio=tuple(replace(a, **changes) if a.name == name else a for a in self.audio))


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole > 0 else 0.0


class HealthMonitor:
    """Turns OBS's running totals into rates, and decides when to warn."""

    def __init__(self, warn_dropped_pct: float = 5.0) -> None:
        self.warn_dropped_pct = warn_dropped_pct
        self._last: tuple[float, ObsStats, StreamStatus] | None = None
        self._warned: dict[str, float] = {}

    def reset(self) -> None:
        self._last = None

    def sample(self, now: float, stats: ObsStats, stream: StreamStatus) -> tuple[Health, list[str]]:
        previous, self._last = self._last, (now, stats, stream)
        bitrate = network = render = encode = 0.0
        if previous is not None:
            then, old_stats, old_stream = previous
            seconds = max(0.001, now - then)
            if stream.active and old_stream.active and stream.bytes >= old_stream.bytes:
                bitrate = (stream.bytes - old_stream.bytes) * 8 / 1000 / seconds
                network = _pct(stream.skipped_frames - old_stream.skipped_frames,
                               stream.total_frames - old_stream.total_frames)
            if stats.render_total >= old_stats.render_total:
                render = _pct(stats.render_skipped - old_stats.render_skipped, stats.render_total - old_stats.render_total)
            if stats.encode_total >= old_stats.encode_total:
                encode = _pct(stats.encode_skipped - old_stats.encode_skipped, stats.encode_total - old_stats.encode_total)
        health = Health(
            fps=round(stats.fps, 1), cpu=round(stats.cpu, 1), memory_mb=stats.memory_mb, disk_mb=stats.disk_mb,
            bitrate_kbps=round(bitrate), network_dropped_pct=network, render_lag_pct=render, encode_lag_pct=encode,
            stream_duration_ms=stream.duration_ms if stream.active else 0, reconnecting=stream.reconnecting,
        )
        return health, self._warnings(now, health, stream.active)

    def _warnings(self, now: float, health: Health, live: bool) -> list[str]:
        found = []
        limit = self.warn_dropped_pct
        if limit > 0 and live and health.network_dropped_pct >= limit:
            found.append(("network", f"OBS is dropping {health.network_dropped_pct}% of frames: your upload can't keep "
                                     "up. Lower the bitrate in OBS → Settings → Output."))
        if limit > 0 and health.render_lag_pct >= limit:
            found.append(("render", f"OBS is skipping {health.render_lag_pct}% of frames while rendering: the GPU is "
                                    "overloaded. Lower the game's graphics or cap its FPS."))
        if limit > 0 and health.encode_lag_pct >= limit:
            found.append(("encode", f"The encoder is skipping {health.encode_lag_pct}% of frames: switch to a hardware "
                                    "encoder (NVENC, AMF, QuickSync) or a faster preset."))
        if 0 < health.disk_mb < LOW_DISK_MB:
            found.append(("disk", f"Only {health.disk_mb / 1024:.1f} GB of disk space left for recordings."))
        messages = []
        for kind, message in found:
            if now - self._warned.get(kind, float("-inf")) >= WARN_COOLDOWN_S:
                self._warned[kind] = now
                messages.append(message)
        return messages


def match_scene(wanted: str, scenes: tuple[str, ...]) -> str | None:
    """A scene by name from chat: exact, then case-insensitive, then a unique prefix."""
    wanted = wanted.strip()
    if not wanted:
        return None
    if wanted in scenes:
        return wanted
    lowered = wanted.lower()
    exact = [s for s in scenes if s.lower() == lowered]
    if exact:
        return exact[0]
    prefix = [s for s in scenes if s.lower().startswith(lowered)]
    return prefix[0] if len(prefix) == 1 else None


def clock(ms: int) -> str:
    seconds = max(0, ms // 1000)
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"
