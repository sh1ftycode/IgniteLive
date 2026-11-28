"""What the OBS panel shows: scenes, audio and outputs. Pure logic, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ignite_live.integrations.obs import AudioInput
from ignite_live.integrations.obs_targets import VerticalCanvas


@dataclass(frozen=True)
class Health:
    """Only what OBS says about the running stream; no performance metrics."""

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
