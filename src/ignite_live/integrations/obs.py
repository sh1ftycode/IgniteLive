"""obs-websocket v5 client (built into OBS Studio 28+).

Implements the slice of the protocol Ignite needs: authenticate, send
requests, and receive events (outputs, scenes, inputs). Protocol reference:
https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import itertools
from dataclasses import dataclass
from typing import Any, Callable

import aiohttp

RPC_VERSION = 1

_OP_HELLO, _OP_IDENTIFY, _OP_IDENTIFIED = 0, 1, 2
_OP_EVENT, _OP_REQUEST, _OP_REQUEST_RESPONSE = 5, 6, 7

_SUB_GENERAL = 1 << 0
_SUB_SCENES = 1 << 2
_SUB_INPUTS = 1 << 3
_SUB_OUTPUTS = 1 << 6
# OBS answers this when an input has no audio, or an output (replay buffer) isn't set up.
_NOT_AVAILABLE = ("does not support audio", "not available", "not enabled", "not active")

_CLOSE_REASONS = {
    4009: "OBS rejected the password.",
    4010: "This OBS version speaks an unsupported obs-websocket protocol.",
}


class ObsError(Exception):
    """A safe-to-display OBS error."""


@dataclass(frozen=True)
class StreamStatus:
    active: bool
    reconnecting: bool
    duration_ms: int
    bytes: int = 0
    skipped_frames: int = 0  # Dropped because the connection couldn't keep up.
    total_frames: int = 0


@dataclass(frozen=True)
class AudioInput:
    name: str
    muted: bool
    volume_db: float  # -100 (silent) .. 0


@dataclass(frozen=True)
class ObsStats:
    fps: float
    cpu: float  # Percent.
    memory_mb: float
    disk_mb: float
    render_skipped: int  # Frames OBS couldn't render in time (GPU overloaded).
    render_total: int
    encode_skipped: int  # Frames the encoder couldn't keep up with (CPU/encoder overloaded).
    encode_total: int


def auth_response(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest())
    return base64.b64encode(hashlib.sha256(secret + challenge.encode()).digest()).decode()


class ObsClient:
    def __init__(
        self,
        url: str = "ws://127.0.0.1:4455",
        password: str = "",
        on_event: Callable[[str, dict], None] | None = None,
        timeout: float = 10.0,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        """``on_close`` fires once the connection drops, e.g. when OBS quits."""
        self._url = url
        self._password = password
        self._on_event = on_event
        self._on_close = on_close
        self._timeout = timeout
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self.obs_version = ""

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(connect=self._timeout, sock_connect=self._timeout)
        )
        try:
            self._ws = await self._session.ws_connect(
                self._url,
                protocols=("obswebsocket.json",),
                timeout=aiohttp.ClientWSTimeout(ws_close=self._timeout),
            )
            hello = await self._receive_op(_OP_HELLO)
            self.obs_version = str(hello.get("obsStudioVersion", ""))
            identify: dict[str, Any] = {
                "rpcVersion": RPC_VERSION,
                "eventSubscriptions": _SUB_GENERAL | _SUB_SCENES | _SUB_INPUTS | _SUB_OUTPUTS,
            }
            auth = hello.get("authentication")
            if auth:
                if not self._password:
                    raise ObsError("OBS requires a WebSocket password. Add it in Settings.")
                identify["authentication"] = auth_response(
                    self._password, auth["salt"], auth["challenge"]
                )
            await self._ws.send_json({"op": _OP_IDENTIFY, "d": identify})
            await self._receive_op(_OP_IDENTIFIED)
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as error:
            await self.close()
            raise ObsError(
                "Could not reach OBS. Is it running with Tools → WebSocket Server Settings enabled?"
            ) from error
        except ObsError:
            await self.close()
            raise
        self._reader = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
            self._reader = None
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._session:
            await self._session.close()
            self._session = None
        self._fail_pending(ObsError("Disconnected from OBS."))

    async def request(self, request_type: str, data: dict | None = None) -> dict:
        if not self.connected:
            raise ObsError("Not connected to OBS.")
        request_id = str(next(self._ids))
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload: dict[str, Any] = {"requestType": request_type, "requestId": request_id}
        if data is not None:
            payload["requestData"] = data
        try:
            await self._ws.send_json({"op": _OP_REQUEST, "d": payload})
            return await asyncio.wait_for(future, self._timeout)
        except asyncio.TimeoutError as error:
            raise ObsError(f"OBS did not answer {request_type} in time.") from error
        finally:
            self._pending.pop(request_id, None)

    # High-level operations used by the app.

    async def stream_status(self) -> StreamStatus:
        data = await self.request("GetStreamStatus")
        return StreamStatus(
            active=bool(data.get("outputActive")),
            reconnecting=bool(data.get("outputReconnecting")),
            duration_ms=int(data.get("outputDuration") or 0),
            bytes=int(data.get("outputBytes") or 0),
            skipped_frames=int(data.get("outputSkippedFrames") or 0),
            total_frames=int(data.get("outputTotalFrames") or 0),
        )

    async def stream_destination(self) -> tuple[str, str]:
        data = await self.request("GetStreamServiceSettings")
        settings = data.get("streamServiceSettings") or {}
        return str(settings.get("server", "")), str(settings.get("key", ""))

    async def set_stream_destination(self, server: str, key: str) -> None:
        if (await self.stream_status()).active:
            raise ObsError("Stop the stream in OBS before changing the destination.")
        await self.request(
            "SetStreamServiceSettings",
            {
                "streamServiceType": "rtmp_custom",
                "streamServiceSettings": {"server": server, "key": key, "use_auth": False},
            },
        )
        if await self.stream_destination() != (server, key):
            raise ObsError("OBS did not keep the new destination.")

    async def start_stream(self) -> None:
        await self.request("StartStream")

    async def stop_stream(self) -> None:
        await self.request("StopStream")

    async def add_browser_source(
        self, scene: str, name: str, url: str, width: int, height: int, canvas_uuid: str = ""
    ) -> None:
        """``canvas_uuid`` puts it in a scene of another canvas (e.g. Aitum Vertical Canvas)."""
        await self.request(
            "CreateInput",
            {
                **({"canvasUuid": canvas_uuid} if canvas_uuid else {}),
                "sceneName": scene,
                "inputName": name,
                "inputKind": "browser_source",
                "inputSettings": {
                    "url": url,
                    "width": width,
                    "height": height,
                    # Route page audio into the OBS mixer instead of the desktop.
                    "reroute_audio": True,
                },
            },
        )

    async def browser_sources(self) -> dict[str, dict]:
        """Every browser source's name → its settings (url, reroute_audio, …)."""
        data = await self.request("GetInputList", {"inputKind": "browser_source"})
        sources = {}
        for item in data.get("inputs") or []:
            name = str(item.get("inputName", ""))
            settings = await self.request("GetInputSettings", {"inputName": name})
            sources[name] = dict(settings.get("inputSettings") or {})
        return sources

    async def browser_source_urls(self) -> dict[str, str]:
        """Every browser source's name → URL."""
        return {name: str(settings.get("url", "")) for name, settings in (await self.browser_sources()).items()}

    async def set_browser_source_url(self, name: str, url: str) -> None:
        await self.request("SetInputSettings", {"inputName": name, "inputSettings": {"url": url}, "overlay": True})

    async def current_scene(self) -> str:
        data = await self.request("GetCurrentProgramScene")
        return str(data.get("currentProgramSceneName") or data.get("sceneName") or "")

    # Scenes.

    async def scenes(self) -> tuple[str, list[str]]:
        """(current program scene, every scene in the order OBS lists them)."""
        data = await self.request("GetSceneList")
        names = [str(scene.get("sceneName", "")) for scene in data.get("scenes") or []]
        current = str(data.get("currentProgramSceneName") or "")
        return current, list(reversed(names))  # OBS reports bottom-to-top.

    async def set_scene(self, name: str) -> None:
        await self.request("SetCurrentProgramScene", {"sceneName": name})

    async def _scene_item(self, scene: str, source: str) -> tuple[str, int]:
        scene = scene or await self.current_scene()
        data = await self.request("GetSceneItemId", {"sceneName": scene, "sourceName": source})
        return scene, int(data.get("sceneItemId", 0))

    async def source_visible(self, scene: str, source: str) -> bool:
        """Whether ``source`` shows in ``scene`` (empty: the current program scene)."""
        scene, item = await self._scene_item(scene, source)
        data = await self.request("GetSceneItemEnabled", {"sceneName": scene, "sceneItemId": item})
        return bool(data.get("sceneItemEnabled"))

    async def set_source_visible(self, scene: str, source: str, visible: bool) -> None:
        scene, item = await self._scene_item(scene, source)
        await self.request("SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": item, "sceneItemEnabled": visible})

    # Audio.

    async def audio_inputs(self) -> list[AudioInput]:
        """Every input that has audio (mics, desktop audio, media, browser sources with audio)."""
        data = await self.request("GetInputList")
        found = []
        for item in data.get("inputs") or []:
            name = str(item.get("inputName", ""))
            try:
                muted = (await self.request("GetInputMute", {"inputName": name})).get("inputMuted")
                volume = (await self.request("GetInputVolume", {"inputName": name})).get("inputVolumeDb")
            except ObsError as error:
                if any(text in str(error) for text in _NOT_AVAILABLE):
                    continue  # A video-only source.
                raise
            found.append(AudioInput(name, bool(muted), max(-100.0, float(volume or 0.0))))
        return found

    async def set_mute(self, name: str, muted: bool) -> None:
        await self.request("SetInputMute", {"inputName": name, "inputMuted": muted})

    async def set_volume_db(self, name: str, db: float) -> None:
        await self.request("SetInputVolume", {"inputName": name, "inputVolumeDb": max(-100.0, min(26.0, db))})

    # Recording, replay buffer, virtual camera.

    async def record_status(self) -> tuple[bool, bool, int]:
        """(recording, paused, duration in ms)."""
        data = await self.request("GetRecordStatus")
        return bool(data.get("outputActive")), bool(data.get("outputPaused")), int(data.get("outputDuration") or 0)

    async def set_recording(self, start: bool) -> None:
        await self.request("StartRecord" if start else "StopRecord")

    async def set_record_paused(self, paused: bool) -> None:
        await self.request("PauseRecord" if paused else "ResumeRecord")

    async def replay_status(self) -> bool | None:
        """Whether the replay buffer runs; None when it isn't enabled in OBS's output settings."""
        try:
            return bool((await self.request("GetReplayBufferStatus")).get("outputActive"))
        except ObsError as error:
            if any(text in str(error) for text in _NOT_AVAILABLE):
                return None
            raise

    async def set_replay(self, start: bool) -> None:
        await self.request("StartReplayBuffer" if start else "StopReplayBuffer")

    # Video and output settings (the setup guide's tuning).

    async def video_settings(self) -> dict:
        """baseWidth/baseHeight (the canvas), outputWidth/outputHeight (scaled), fpsNumerator/fpsDenominator."""
        return await self.request("GetVideoSettings")

    async def set_video_settings(self, output: tuple[int, int], fps: int) -> None:
        """Only the scaled output size and frame rate: the canvas (and so every scene's layout) stays as it is."""
        await self.request("SetVideoSettings", {"outputWidth": output[0], "outputHeight": output[1],
                                                "fpsNumerator": int(fps), "fpsDenominator": 1})

    async def _parameter(self, category: str, name: str) -> str:
        data = await self.request("GetProfileParameter", {"parameterCategory": category, "parameterName": name})
        value = data.get("parameterValue")
        return str(value if value is not None else data.get("defaultParameterValue") or "")

    async def output_mode(self) -> str:
        """OBS → Settings → Output: "Simple" or "Advanced"."""
        return "Advanced" if await self._parameter("Output", "Mode") == "Advanced" else "Simple"

    async def simple_stream_settings(self) -> tuple[str, int | None, str]:
        """Simple output mode's (encoder, bitrate in kbps, x264 preset)."""
        encoder = await self._parameter("SimpleOutput", "StreamEncoder")
        bitrate = await self._parameter("SimpleOutput", "VBitrate")
        preset = await self._parameter("SimpleOutput", "Preset")
        return encoder, int(bitrate) if bitrate.isdigit() else None, preset

    async def set_simple_stream_settings(self, encoder: str, bitrate: int, preset: str = "") -> None:
        values = {"StreamEncoder": encoder, "VBitrate": str(int(bitrate))}
        if preset:
            values["Preset"] = preset
        for name, value in values.items():
            await self.request("SetProfileParameter", {"parameterCategory": "SimpleOutput", "parameterName": name,
                                                       "parameterValue": value})

    async def _replay_parameter(self) -> str:
        data = await self.request("GetProfileParameter", {"parameterCategory": "Output", "parameterName": "Mode"})
        return "AdvOut" if str(data.get("parameterValue") or "") == "Advanced" else "SimpleOutput"

    async def replay_seconds(self) -> int | None:
        """How many seconds the replay buffer keeps (OBS → Settings → Output), or None if unknown."""
        category = await self._replay_parameter()
        data = await self.request("GetProfileParameter", {"parameterCategory": category, "parameterName": "RecRBTime"})
        value = data.get("parameterValue") or data.get("defaultParameterValue")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    async def set_replay_seconds(self, seconds: int) -> None:
        """Takes effect the next time the replay buffer starts."""
        category = await self._replay_parameter()
        await self.request("SetProfileParameter", {"parameterCategory": category, "parameterName": "RecRBTime",
                                                   "parameterValue": str(int(seconds))})

    async def save_replay(self) -> None:
        await self.request("SaveReplayBuffer")

    async def virtualcam_status(self) -> bool | None:
        try:
            return bool((await self.request("GetVirtualCamStatus")).get("outputActive"))
        except ObsError as error:
            if any(text in str(error) for text in _NOT_AVAILABLE):
                return None
            raise

    async def set_virtualcam(self, start: bool) -> None:
        await self.request("StartVirtualCam" if start else "StopVirtualCam")

    # Health and tools.

    async def stats(self) -> ObsStats:
        data = await self.request("GetStats")
        return ObsStats(
            fps=float(data.get("activeFps") or 0),
            cpu=float(data.get("cpuUsage") or 0),
            memory_mb=float(data.get("memoryUsage") or 0),
            disk_mb=float(data.get("availableDiskSpace") or 0),
            render_skipped=int(data.get("renderSkippedFrames") or 0),
            render_total=int(data.get("renderTotalFrames") or 0),
            encode_skipped=int(data.get("outputSkippedFrames") or 0),
            encode_total=int(data.get("outputTotalFrames") or 0),
        )

    async def screenshot(self, source: str, path: str) -> None:
        """Save a PNG of a scene or source (the path is on the computer running OBS)."""
        await self.request("SaveSourceScreenshot",
                           {"sourceName": source, "imageFormat": "png", "imageFilePath": path})

    async def refresh_browser_source(self, name: str) -> None:
        """Same as the Refresh button in the browser source's properties."""
        await self.request("PressInputPropertiesButton", {"inputName": name, "propertyName": "refreshnocache"})

    # Internals.

    async def _receive_op(self, op: int) -> dict:
        message = await asyncio.wait_for(self._ws.receive(), self._timeout)
        if message.type == aiohttp.WSMsgType.TEXT:
            frame = message.json()
            if frame.get("op") == op:
                return frame.get("d") or {}
            raise ObsError("OBS sent an unexpected handshake message.")
        code = self._ws.close_code
        raise ObsError(_CLOSE_REASONS.get(code, f"OBS closed the connection (code {code})."))

    async def _read_loop(self) -> None:
        try:
            async for message in self._ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                frame = message.json()
                op, data = frame.get("op"), frame.get("d") or {}
                if op == _OP_REQUEST_RESPONSE:
                    self._resolve(data)
                elif op == _OP_EVENT and self._on_event:
                    self._on_event(str(data.get("eventType")), data.get("eventData") or {})
        finally:
            self._fail_pending(ObsError("Disconnected from OBS."))
            if self._on_close:
                self._on_close()

    def _resolve(self, data: dict) -> None:
        future = self._pending.get(str(data.get("requestId")))
        if future is None or future.done():
            return
        status = data.get("requestStatus") or {}
        if status.get("result"):
            future.set_result(data.get("responseData") or {})
        else:
            comment = status.get("comment") or f"code {status.get('code')}"
            future.set_exception(ObsError(f"OBS refused {data.get('requestType')}: {comment}"))

    def _fail_pending(self, error: ObsError) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
