"""Online voices: Microsoft Edge's neural voices and TikTok's own voices. Both return MP3 audio.

- **Edge**: the voices behind Edge's "Read aloud", through the ``edge-tts``
  library. Free, no account or key; about 300 natural voices in many languages.
- **TikTok**: the voices from TikTok videos (Jessie, C-3PO, Stitch, Ghostface…),
  through TikTok's in-app speech endpoint. It only answers logged-in requests,
  so the streamer pastes their TikTok ``sessionid`` cookie (kept in the keychain).

Neither is an official API, so both can change without notice; errors come
back as ``VoiceError`` with a message that's safe to show.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from urllib.parse import urlencode

import aiohttp

PROVIDERS = {
    "edge": "Microsoft Edge voices (natural, free, online)",
    "tiktok": "TikTok voices (needs your TikTok session ID)",
    "system": "This computer's voices (offline)",
}
DEFAULT_EDGE_VOICE = "en-US-AriaNeural"
DEFAULT_TIKTOK_VOICE = "en_us_002"

# TikTok's voice IDs (from its video editor). Any other ID can be typed in Preferences.
TIKTOK_VOICES = {
    "en_us_002": "Jessie",
    "en_us_001": "Female (US)",
    "en_us_006": "Joey",
    "en_us_007": "Professor",
    "en_us_009": "Scientist",
    "en_us_010": "Confidence",
    "en_male_narration": "Story Teller",
    "en_male_funny": "Wacky",
    "en_female_emotional": "Peaceful",
    "en_male_cody": "Serious",
    "en_female_samc": "Empathetic",
    "en_uk_001": "Narrator (UK)",
    "en_uk_003": "Male (UK)",
    "en_au_001": "Female (Australia)",
    "en_au_002": "Male (Australia)",
    "en_us_ghostface": "Ghostface",
    "en_us_chewbacca": "Chewbacca",
    "en_us_c3po": "C-3PO",
    "en_us_stitch": "Stitch",
    "en_us_stormtrooper": "Stormtrooper",
    "en_us_rocket": "Rocket",
    "en_female_f08_salut_damour": "Alto (singing)",
    "en_male_m03_lobby": "Tenor (singing)",
    "en_male_m03_sunshine_soon": "Sunshine Soon (singing)",
    "fr_001": "French (male)",
    "de_001": "German (female)",
    "es_002": "Spanish (male)",
    "es_mx_002": "Spanish, Mexico (male)",
    "br_003": "Portuguese, Brazil (female)",
    "jp_001": "Japanese (female)",
    "kr_002": "Korean (male)",
}
TIKTOK_URL = "https://api16-normal-c-useast1a.tiktokv.com/media/api/text/speech/invoke/"
TIKTOK_USER_AGENT = ("com.zhiliaoapp.musically/2022600030 (Linux; U; Android 7.1.2; es_ES; SM-G988N; "
                     "Build/NRD90M;tt-ok/3.12.13.1)")
TIKTOK_CHUNK = 200  # TikTok refuses long texts; longer messages are spoken in parts.
TIKTOK_ERRORS = {
    1: "TikTok didn't accept the session ID. Paste a fresh one in Preferences → Text to speech.",
    2: "That message is too long for TikTok's voices.",
    4: "TikTok doesn't have that voice. Pick another in Preferences → Text to speech.",
    5: "Add your TikTok session ID in Preferences → Text to speech.",
}
_SESSION = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


class VoiceError(Exception):
    """Safe to show (never includes the session ID).

    ``fatal`` means retrying won't help until the settings change (bad session ID, unknown voice).
    """

    def __init__(self, message: str, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


@dataclass(frozen=True)
class Voice:
    id: str
    name: str  # e.g. "Aria · Female"
    language: str  # e.g. "English (United States)"


def valid_session(session: str) -> bool:
    return bool(_SESSION.match(session.strip()))


def split_text(text: str, limit: int = TIKTOK_CHUNK) -> list[str]:
    """Split at sentence or word boundaries into parts no longer than ``limit``."""
    parts, current = [], ""
    for word in text.split():
        while len(word) > limit:  # A single very long "word".
            if current:
                parts.append(current)
                current = ""
            parts.append(word[:limit])
            word = word[limit:]
        if current and len(current) + 1 + len(word) > limit:
            parts.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        parts.append(current)
    return parts


def edge_rate(rate: int) -> str:
    """Preferences' -100..100 speed as Edge's percentage (-50%..+100%)."""
    rate = max(-100, min(100, rate))
    return f"{rate // 2 if rate < 0 else rate:+d}%"


def edge_pitch(pitch: int) -> str:
    return f"{max(-100, min(100, pitch)) // 2:+d}Hz"


async def edge_speech(text: str, voice: str, rate: int = 0, pitch: int = 0) -> bytes:
    import edge_tts  # Imported here so the app starts even if it's missing.

    try:
        communicate = edge_tts.Communicate(text, voice or DEFAULT_EDGE_VOICE, rate=edge_rate(rate), pitch=edge_pitch(pitch))
        audio = bytearray()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                audio += chunk["data"]
    except ValueError as error:  # e.g. an unknown voice name.
        raise VoiceError(f"Edge voices: {error}", fatal=True) from error
    except Exception as error:  # edge-tts raises its own exception types for network and service errors.
        raise VoiceError("Couldn't reach Microsoft's voice service. Check your internet connection.") from error
    if not audio:
        raise VoiceError("Microsoft's voice service returned no audio.")
    return bytes(audio)


async def edge_voices() -> list[Voice]:
    import edge_tts

    try:
        found = await edge_tts.list_voices()
    except Exception as error:
        raise VoiceError("Couldn't load the voice list from Microsoft.") from error
    voices = []
    for item in found:
        short = str(item.get("ShortName", ""))
        name = short.rsplit("-", 1)[-1].removesuffix("Neural").removesuffix("Multilingual")
        voices.append(Voice(short, f"{name} · {item.get('Gender', '')}", str(item.get("LocaleName") or item.get("Locale", ""))))
    return sorted(voices, key=lambda v: (v.language, v.name))


def tiktok_voices() -> list[Voice]:
    return [Voice(voice_id, name, "TikTok") for voice_id, name in TIKTOK_VOICES.items()]


async def tiktok_speech(text: str, voice: str, session: str, http: aiohttp.ClientSession | None = None) -> bytes:
    session = session.strip()
    if not session:
        raise VoiceError(TIKTOK_ERRORS[5], fatal=True)
    own = http is None
    http = http or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
    audio = bytearray()
    try:
        for part in split_text(text):
            query = urlencode({"text_speaker": voice or DEFAULT_TIKTOK_VOICE, "req_text": part,
                               "speaker_map_type": 0, "aid": 1233})
            try:
                async with http.post(f"{TIKTOK_URL}?{query}",
                                     headers={"User-Agent": TIKTOK_USER_AGENT, "Cookie": f"sessionid={session}"}) as response:
                    payload = await response.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, ValueError) as error:
                raise VoiceError("Couldn't reach TikTok's voice service.") from error
            code = payload.get("status_code") if isinstance(payload, dict) else None
            if code != 0:
                raise VoiceError(TIKTOK_ERRORS.get(code, f"TikTok's voice service refused the request (code {code})."),
                                 fatal=code in (1, 4, 5))
            try:
                audio += base64.b64decode(payload["data"]["v_str"])
            except (KeyError, TypeError, ValueError) as error:
                raise VoiceError("TikTok's voice service returned no audio.") from error
    finally:
        if own:
            await http.close()
    return bytes(audio)
