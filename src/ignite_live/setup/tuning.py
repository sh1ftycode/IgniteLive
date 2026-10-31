"""Recommended OBS settings for TikTok from what the PC can do. Pure logic, no I/O.

Only OBS's *output* (scaled) resolution and frame rate are changed, never the canvas
(base) resolution: that would rearrange every scene. Encoder and bitrate can only be
set in OBS's Simple output mode; in Advanced mode they're shown to set by hand.
"""

from __future__ import annotations

from dataclasses import dataclass

from ignite_live.setup.hardware import Hardware

TIERS = {"light": "Light", "balanced": "Balanced", "strong": "Strong"}
# Upload speed choices: key -> (label, Mbit/s used for the cap). Unknown picks the safe bitrate.
UPLOADS = {
    "unknown": ("I don't know", None),
    "under3": ("Under 3 Mbps", 2.5),
    "3to6": ("3–6 Mbps", 4.0),
    "6to10": ("6–10 Mbps", 7.0),
    "over10": ("More than 10 Mbps", 12.0),
}
TIER_BITRATE = {"light": 2500, "balanced": 4000, "strong": 6000}
SAFE_BITRATE = 2500
MIN_BITRATE = 2000
UPLOAD_SHARE = 0.75  # Leave room for everything else on the connection.
HD_MIN_BITRATE = 3500  # Below this, 1080p looks worse than a clean 720p.
HIGH_FPS_MIN_BITRATE = 4500
# OBS Simple output mode's encoder ids, by graphics card vendor.
ENCODERS = {"nvidia": ("nvenc", "NVIDIA NVENC"), "amd": ("amd", "AMD AMF"), "intel": ("qsv", "Intel QuickSync"),
            "apple": ("apple_h264", "Apple VideoToolbox")}
X264 = ("x264", "x264 (CPU)")
ENCODER_LABELS = dict([X264, *ENCODERS.values()])
OVERLAYS = {
    "light": ("player", "alerts", "chat"),
    "balanced": ("player", "nowplaying", "alerts", "chat", "goals"),
    "strong": ("player", "nowplaying", "queue", "alerts", "chat", "goals", "leaderboard"),
}


@dataclass(frozen=True)
class ObsTuning:
    """What OBS has now."""

    base: tuple[int, int] = (1080, 1920)
    output: tuple[int, int] = (1080, 1920)
    fps: float = 30.0
    mode: str = "Simple"  # OBS output mode: "Simple" or "Advanced".
    encoder: str = ""
    bitrate: int | None = None
    preset: str = ""


@dataclass(frozen=True)
class Recommendation:
    tier: str
    output: tuple[int, int]
    fps: int
    encoder: str
    bitrate: int
    preset: str  # x264 only.
    overlays: tuple[str, ...]
    notes: tuple[str, ...]
    system_voice: bool  # Suggest the computer's own voices (no extra network work) on light PCs.


@dataclass(frozen=True)
class Row:
    key: str  # "output", "fps", "encoder", "bitrate", "preset"
    label: str
    current: str
    recommended: str
    settable: bool  # False: show it, the user sets it by hand.


def tier_of(hw: Hardware) -> str:
    threads = hw.threads or 4
    ram = hw.ram_gb or 8.0
    gpu = bool(hw.gpus)
    if gpu and threads >= 8 and ram >= 16:
        return "strong"
    if not gpu and (threads <= 4 or ram <= 8):
        return "light"
    return "balanced"


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def output_size(base: tuple[int, int], short_side: int) -> tuple[int, int]:
    """The base canvas scaled so its short side is ``short_side`` (never upscaled)."""
    width, height = base
    short = min(width, height)
    if short <= short_side:
        return width, height
    scale = short_side / short
    return _even(width * scale), _even(height * scale)


def recommend(hw: Hardware, upload: str = "unknown", base: tuple[int, int] = (1080, 1920)) -> Recommendation:
    tier = tier_of(hw)
    notes = []
    if not hw.detected:
        notes.append("Couldn't read all of this PC's hardware, so these settings play it safe.")
    mbps = UPLOADS.get(upload, UPLOADS["unknown"])[1]
    bitrate = TIER_BITRATE[tier]
    if mbps is None:
        bitrate = min(bitrate, SAFE_BITRATE)
        notes.append("Upload speed unknown: using a bitrate almost any connection handles. A speed test site "
                     "tells you your upload speed.")
    else:
        bitrate = min(bitrate, max(MIN_BITRATE, int(mbps * 1000 * UPLOAD_SHARE / 100) * 100))
    short_side = 720 if tier == "light" else 1080
    fps = 60 if tier == "strong" else 30
    if short_side == 1080 and bitrate < HD_MIN_BITRATE:
        short_side = 720
        notes.append("Using 720p: at this bitrate it looks sharper than 1080p.")
    if fps == 60 and bitrate < HIGH_FPS_MIN_BITRATE:
        fps = 30
        notes.append("Using 30 FPS: 60 needs more upload speed.")
    gpu = hw.gpus[0] if hw.gpus and tier != "light" else None
    encoder = ENCODERS[gpu][0] if gpu else X264[0]
    if base[0] > base[1]:
        notes.append("Your OBS canvas is landscape: TikTok shows it with bars above and below. For full screen, use "
                     "a vertical (9:16) canvas, e.g. the Aitum Vertical Canvas plugin.")
    return Recommendation(
        tier=tier,
        output=output_size(base, short_side),
        fps=fps,
        encoder=encoder,
        bitrate=bitrate,
        preset="veryfast" if encoder == X264[0] else "",
        overlays=OVERLAYS[tier],
        notes=tuple(notes),
        system_voice=tier == "light",
    )


def _fps(value: float) -> str:
    return f"{value:g}"


def rows(current: ObsTuning, rec: Recommendation, vertical_target: bool = False) -> list[Row]:
    """Current vs recommended, for the table. ``vertical_target``: TikTok streams from the Aitum vertical canvas,
    whose settings other apps can't change."""
    video = not vertical_target
    simple = current.mode == "Simple" and video
    found = [
        Row("output", "Output resolution", "×".join(map(str, current.output)), "×".join(map(str, rec.output)), video),
        Row("fps", "Frame rate", f"{_fps(current.fps)} FPS", f"{rec.fps} FPS", video),
        Row("encoder", "Encoder", ENCODER_LABELS.get(current.encoder, current.encoder or "?"),
            ENCODER_LABELS[rec.encoder], simple),
        Row("bitrate", "Bitrate", f"{current.bitrate:,} kbps" if current.bitrate else "?", f"{rec.bitrate:,} kbps",
            simple),
    ]
    if rec.preset:
        found.append(Row("preset", "x264 preset", current.preset or "?", rec.preset, simple))
    return found


def why_by_hand(current: ObsTuning, vertical_target: bool) -> str:
    if vertical_target:
        return ("TikTok streams from the Aitum Vertical Canvas, which other apps can't change: set these in its "
                "settings (the gear on the Vertical Canvas dock).")
    if current.mode != "Simple":
        return ("OBS is in Advanced output mode, where other apps can't set the encoder or bitrate: set those in "
                "OBS → Settings → Output → Streaming.")
    return ""
