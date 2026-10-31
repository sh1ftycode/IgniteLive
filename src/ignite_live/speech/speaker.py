"""The voice: a short queue of messages, skip, mute, and status, for three kinds of voices.

- **Edge** and **TikTok** voices (``speech/voices.py``) are fetched online as MP3
  in the background (the next message is fetched while one plays) and played
  with Qt Multimedia.
- **System** voices use Qt's text to speech: SAPI and WinRT on Windows, the
  system voices on macOS, and speech-dispatcher on Linux.

OBS hears it like any other app (Desktop Audio, or an Application Audio Capture
of Ignite). Lives on the GUI thread.
"""

from __future__ import annotations

import sys
from collections import deque
from dataclasses import dataclass, replace
from typing import Callable

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QTimer, QUrl, Signal

from ignite_live.core.events import ConnectionState, StatusChange
from ignite_live.core.settings import Settings
from ignite_live.speech.voices import PROVIDERS, TIKTOK_VOICES, VoiceError

SOURCE = "tts"
HIDDEN_ENGINES = {"mock"}  # Qt's test engine.
LINUX_HINT = "Install speech-dispatcher and a voice (e.g. espeak-ng), then restart Ignite."
PREFETCH = 2  # Online voices: how many queued messages are fetched ahead.

# fetch(settings, text, on_audio, on_error): get MP3 audio off the GUI thread; callbacks arrive on the GUI thread.
Fetch = Callable[[Settings, str, Callable[[bytes], None], Callable[[BaseException], None]], None]


def available_engines() -> list[str]:
    from PySide6.QtTextToSpeech import QTextToSpeech

    return [name for name in QTextToSpeech.availableEngines() if name not in HIDDEN_ENGINES]


def provider_of(s: Settings) -> str:
    return s.tts_provider if s.tts_provider in PROVIDERS else "edge"


class AudioPlayer(QObject):
    """Plays one MP3 from memory at a time; ``finished`` fires when it ends, fails, or is stopped."""

    finished = Signal()

    def __init__(self) -> None:
        super().__init__()
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

        self._player = QMediaPlayer(self)
        self._output = QAudioOutput(self)
        self._player.setAudioOutput(self._output)
        self._buffer: QBuffer | None = None
        self._playing = False
        self._player.playbackStateChanged.connect(self._state)
        self._player.errorOccurred.connect(lambda *_: self._done())

    def play(self, audio: bytes) -> None:
        buffer = QBuffer(self)
        buffer.setData(QByteArray(audio))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self._buffer = buffer
        self._playing = True
        self._player.setSourceDevice(buffer, QUrl("speech.mp3"))
        self._player.play()

    def stop(self) -> None:
        self._player.stop()
        self._done()

    def set_volume(self, volume: float) -> None:
        self._output.setVolume(volume)

    def set_rate(self, rate: float) -> None:
        self._player.setPlaybackRate(rate)

    def _state(self, state) -> None:
        if getattr(state, "name", "") == "StoppedState":
            self._done()

    def _done(self) -> None:
        if self._playing:
            self._playing = False
            # Not straight away: starting the next sound from inside QMediaPlayer's own state-change
            # signal deadlocks its FFmpeg backend.
            QTimer.singleShot(0, self.finished.emit)


@dataclass(eq=False)
class Message:
    text: str
    state: str = "waiting"  # waiting → loading → ready | failed (online voices only)
    audio: bytes = b""


class Speaker(QObject):
    status = Signal(object)  # StatusChange

    def __init__(
        self,
        factory: Callable[[str], object] | None = None,
        fetch: Fetch | None = None,
        player_factory: Callable[[], object] | None = None,
    ) -> None:
        super().__init__()
        self._factory = factory or self._qt_engine
        self._fetch = fetch
        self._player_factory = player_factory or AudioPlayer
        self._engine = None
        self._engine_name: str | None = None
        self._player = None
        self._provider = "edge"
        self._settings = Settings()
        self._ready = False  # A voice is set up (for chat, or for a Preferences test).
        self._enabled = False  # Chat is read aloud (Preferences → Text to speech → Read chat aloud).
        self._queue: deque[Message] = deque()
        self._max_queue = 10
        self._muted = False
        self._speaking = False
        self._troubled = False  # The last online fetch failed; the next success clears the status.

    @staticmethod
    def _qt_engine(name: str):
        from PySide6.QtTextToSpeech import QTextToSpeech

        return QTextToSpeech(name) if name else QTextToSpeech()

    # Settings.

    def configure(self, s: Settings) -> None:
        self._enabled = s.tts_enabled
        if not s.tts_enabled:
            self.clear()
            self._ready = False
            self._status(ConnectionState.OFFLINE, "Off. Turn it on in File → Preferences → Text to speech.")
            return
        provider = provider_of(s)
        if provider != self._provider:
            self.clear()
        self._provider, self._settings = provider, s
        self._max_queue = max(1, s.tts_max_queue)
        self._ready = self._configure_system(s) if provider == "system" else self._configure_online(s)

    def _configure_online(self, s: Settings) -> bool:
        if self._player is None:
            self._player = self._player_factory()
            self._player.finished.connect(self._finished)
        self._player.set_volume(max(0, min(100, s.tts_volume)) / 100)
        if self._provider == "tiktok":
            # TikTok's voices have no speed setting: play them faster or slower instead.
            self._player.set_rate(1 + max(-100, min(100, s.tts_rate)) / 200)
            voice = TIKTOK_VOICES.get(s.tts_tiktok_voice, s.tts_tiktok_voice)
            self._status(ConnectionState.CONNECTED, f"Ready (TikTok: {voice})")
        else:
            self._player.set_rate(1.0)  # Edge applies speed and pitch itself.
            self._status(ConnectionState.CONNECTED, f"Ready (Edge: {s.tts_edge_voice})")
        return True

    def _configure_system(self, s: Settings) -> bool:
        engine = self._ensure(s.tts_engine)
        if engine is None:
            return False
        voice = next((v for v in engine.availableVoices() if v.name() == s.tts_voice), None)
        if voice is not None:
            engine.setVoice(voice)
        engine.setRate(max(-100, min(100, s.tts_rate)) / 100)
        engine.setPitch(max(-100, min(100, s.tts_pitch)) / 100)
        engine.setVolume(max(0, min(100, s.tts_volume)) / 100)
        current = engine.voice().name() if hasattr(engine, "voice") else ""
        self._status(ConnectionState.CONNECTED, f"Ready ({current})" if current else "Ready")
        return True

    def _ensure(self, name: str):
        if self._engine is not None and self._engine_name == name:
            return self._engine
        if self._engine is not None:
            self._engine.stop()
            self._engine.deleteLater()
        self._engine, self._engine_name = None, name
        try:
            engine = self._factory(name)
        except Exception as error:  # A broken OS speech plugin mustn't take the app down.
            self._failed(str(error))
            return None
        if engine.state().name == "Error" or (not engine.availableVoices() and sys.platform.startswith("linux")):
            self._failed(engine.errorString() if hasattr(engine, "errorString") else "")
            return None
        engine.stateChanged.connect(self._state_changed)
        self._engine = engine
        return engine

    def _failed(self, detail: str) -> None:
        hint = LINUX_HINT if sys.platform.startswith("linux") else "No speech engine is available."
        self._status(ConnectionState.ERROR, f"{detail}. {hint}" if detail else hint)

    def voices(self, engine_name: str) -> list[str]:
        """This computer's voices for one engine (Preferences, system voices)."""
        engine = self._ensure(engine_name)
        return [voice.name() for voice in engine.availableVoices()] if engine is not None else []

    # Speaking.

    @property
    def queued(self) -> int:
        return len(self._queue)

    def say(self, text: str) -> None:
        if not self._ready or not self._enabled or self._muted:
            return
        if len(self._queue) >= self._max_queue:
            self._queue.popleft()  # Keep up with chat: drop the oldest waiting message.
        self._queue.append(Message(text))
        self._pump()

    def test(self, s: Settings, text: str) -> None:
        """Say ``text`` with these settings now, even if text to speech is off (chat stays quiet then)."""
        self.configure(replace(s, tts_enabled=True))
        self._enabled = s.tts_enabled
        self._queue.clear()
        self._stop_current()
        self._queue.append(Message(text))
        self._pump()

    def skip(self) -> None:
        self._stop_current()

    def clear(self) -> None:
        self._queue.clear()
        self._stop_current()

    @property
    def muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        if muted:
            self.clear()

    def _stop_current(self) -> None:
        if self._provider == "system":
            if self._engine is not None:
                self._engine.stop()
        elif self._player is not None:
            self._player.stop()

    def _pump(self) -> None:
        if not self._ready:
            return
        online = self._provider != "system"
        if online:
            for message in list(self._queue)[:PREFETCH]:
                if message.state == "waiting":
                    self._load(message)
        while not self._speaking and self._queue:
            head = self._queue[0]
            if not online:
                self._queue.popleft()
                self._speaking = True
                self._engine.say(head.text)
            elif head.state == "failed":
                self._queue.popleft()
                continue
            elif head.state == "ready":
                self._queue.popleft()
                self._speaking = True
                self._player.play(head.audio)
            break

    def _load(self, message: Message) -> None:
        if self._fetch is None:
            message.state = "failed"
            return
        message.state = "loading"

        def loaded(audio: bytes) -> None:
            message.state, message.audio = "ready", audio
            if self._troubled:
                self._troubled = False
                self._configure_online(self._settings)  # Back to "Ready".
            self._pump()

        def failed(error: BaseException) -> None:
            message.state = "failed"
            self._troubled = True
            safe = isinstance(error, VoiceError)
            self._status(ConnectionState.ERROR if safe and error.fatal else ConnectionState.DEGRADED,
                         str(error) if safe else "Couldn't get that message's audio.")
            self._pump()

        self._fetch(self._settings, message.text, loaded, failed)

    def _finished(self) -> None:
        self._speaking = False
        self._pump()

    def _state_changed(self, state) -> None:
        name = getattr(state, "name", str(state))
        if name in ("Ready", "Error"):
            self._speaking = False
            if name == "Error":
                self._failed(self._engine.errorString() if self._engine is not None else "")
            self._pump()
        elif name in ("Speaking", "Synthesizing"):
            self._speaking = True

    def _status(self, state: ConnectionState, detail: str) -> None:
        self.status.emit(StatusChange(SOURCE, state, detail))
