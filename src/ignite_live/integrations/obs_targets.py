"""Which OBS output gets the TikTok key: OBS's main stream, or an output of the
Aitum Vertical Canvas / Aitum Multistream plugins.

What the plugins let other apps do over obs-websocket (vendor requests):

- aitum-vertical-canvas: ``update_stream_server`` / ``update_stream_key`` for a
  stream output by index, ``status``, ``start_streaming`` (starts every enabled
  vertical output). It can't list its outputs.
- aitum-multistream: ``get_outputs`` (names of its own outputs and the vertical
  canvas outputs, in vertical-canvas order), ``start_output`` / ``stop_output``
  by name. It can't change an output's server or key.

So the key can go to the main stream or to a vertical output; Multistream's own
(horizontal) outputs can only be started and stopped.

Vertical Canvas also has ``get_scenes`` / ``current_scene`` / ``switch_scene``,
``status`` (streaming, recording, backtrack), ``start_``/``stop_recording``,
``start_``/``stop_backtrack`` and ``save_backtrack`` (its replay buffer). Its scenes
live on their own OBS canvas, so adding a source to one needs that canvas's UUID
(``GetCanvasList``, obs-websocket 5.7+); older OBS versions can switch vertical
scenes but not add sources to them.

Sources: github.com/Aitum/obs-vertical-canvas (vertical-canvas.cpp) and
github.com/Aitum/obs-aitum-multistream (multistream.cpp), October 2026.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from ignite_live.integrations.obs import ObsClient, ObsError

VERTICAL = "aitum-vertical-canvas"
MULTISTREAM = "aitum-multistream"
MAIN_ID = "main"


@dataclass(frozen=True)
class StreamTarget:
    id: str  # "main", "vertical:<name>", or "multistream:<name>"
    label: str
    kind: str  # "main" | "vertical" | "multistream"
    name: str = ""
    index: int = -1  # position in the vertical canvas's stream outputs
    can_set_key: bool = True


MAIN = StreamTarget(MAIN_ID, "OBS main stream", "main")


def _vertical(index: int, name: str) -> StreamTarget:
    label = f"Vertical: {name}" if name else f"Vertical output {index + 1}"
    return StreamTarget(f"vertical:{name or index}", label, "vertical", name, index)


def obs_config_dirs() -> list[Path]:
    """Where OBS keeps its settings on this computer (Flatpak included)."""
    home = Path.home()
    if sys.platform == "win32":
        return [Path(os.environ.get("APPDATA", home)) / "obs-studio"]
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "obs-studio"]
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return [config / "obs-studio", home / ".var/app/com.obsproject.Studio/config/obs-studio"]


def vertical_outputs_from_config(config_dirs: list[Path] | None = None) -> list[str] | None:
    """Vertical output names from Vertical Canvas's saved config, or None if not found.

    Used only when Aitum Multistream isn't installed to list them. OBS saves
    this file whenever its settings change, so it can lag a just-made edit.
    """
    for base in config_dirs if config_dirs is not None else obs_config_dirs():
        path = base / "plugin_config" / "vertical-canvas" / "config.json"
        try:
            canvases = json.loads(path.read_text("utf-8")).get("canvas") or []
        except (OSError, ValueError, AttributeError):
            continue
        if canvases and isinstance(canvases[0], dict):
            outputs = canvases[0].get("stream_outputs") or []
            return [str(o.get("name") or "") for o in outputs if isinstance(o, dict)]
    return None


async def _vendor(obs: ObsClient, vendor: str, request: str, data: dict | None = None) -> dict:
    """Call a plugin's request; raises ObsError if the plugin is missing or says no."""
    response = await obs.request(
        "CallVendorRequest", {"vendorName": vendor, "requestType": request, "requestData": data or {}}
    )
    payload = response.get("responseData") or {}
    if payload.get("success") is False:
        raise ObsError(f"{vendor} refused {request}: {payload.get('error') or 'no reason given'}")
    return payload


async def discover(obs: ObsClient, config_dirs: list[Path] | None = None) -> tuple[list[StreamTarget], bool]:
    """All outputs that can carry the stream, and whether Aitum Multistream is installed."""
    targets = [MAIN]
    vertical_names: list[str] | None = None
    try:
        outputs = (await _vendor(obs, MULTISTREAM, "get_outputs")).get("outputs") or []
    except ObsError:
        outputs = None  # Multistream not installed.
    if outputs is not None:
        vertical_names = [str(o.get("name") or "") for o in outputs if o.get("vertical")]
        for o in outputs:
            if not o.get("vertical"):
                name = str(o.get("name") or "")
                targets.append(
                    StreamTarget(f"multistream:{name}", f"Multistream: {name}", "multistream", name, can_set_key=False)
                )
    if vertical_names is None:
        try:
            await _vendor(obs, VERTICAL, "version")
        except ObsError:
            return targets, False  # Vertical Canvas not installed either.
        vertical_names = vertical_outputs_from_config(config_dirs) or []
    targets[1:1] = [_vertical(index, name) for index, name in enumerate(vertical_names)]
    return targets, outputs is not None


@dataclass(frozen=True)
class VerticalCanvas:
    """Aitum Vertical Canvas, as the OBS panel shows it."""

    scene: str = ""
    scenes: tuple[str, ...] = ()
    canvas_uuid: str = ""  # Empty: this OBS can't address the canvas, so sources can't be added to it.
    streaming: bool = False
    recording: bool = False
    backtrack: bool = False


async def vertical_state(obs: ObsClient, canvas_uuid: str = "") -> VerticalCanvas | None:
    """Scenes and outputs of the vertical canvas; None if the plugin isn't installed."""
    try:
        status = await _vendor(obs, VERTICAL, "status")
    except ObsError:
        return None
    scenes = [str(scene.get("name", "")) for scene in (await _vendor(obs, VERTICAL, "get_scenes")).get("scenes") or []
              if isinstance(scene, dict)]
    current = str((await _vendor(obs, VERTICAL, "current_scene")).get("scene") or "")
    return VerticalCanvas(current, tuple(scenes), canvas_uuid, bool(status.get("streaming")),
                          bool(status.get("recording")), bool(status.get("backtrack")))


async def vertical_canvas_uuid(obs: ObsClient, scenes: tuple[str, ...]) -> str:
    """The OBS canvas holding the vertical scenes ("" on obs-websocket before 5.7, or if not found)."""
    try:
        canvases = (await obs.request("GetCanvasList")).get("canvases") or []
    except ObsError:
        return ""
    wanted = set(scenes)
    for canvas in canvases:
        uuid = str((canvas or {}).get("canvasUuid") or "")
        if not uuid:
            continue
        try:
            listed = await obs.request("GetSceneList", {"canvasUuid": uuid})
        except ObsError:
            continue
        if wanted & {str(scene.get("sceneName", "")) for scene in listed.get("scenes") or []}:
            return uuid
    return ""


async def switch_vertical_scene(obs: ObsClient, name: str) -> None:
    await _vendor(obs, VERTICAL, "switch_scene", {"scene": name})


async def set_vertical_recording(obs: ObsClient, start: bool) -> None:
    await _vendor(obs, VERTICAL, "start_recording" if start else "stop_recording")


async def set_vertical_backtrack(obs: ObsClient, start: bool) -> None:
    await _vendor(obs, VERTICAL, "start_backtrack" if start else "stop_backtrack")


async def save_vertical_backtrack(obs: ObsClient) -> None:
    await _vendor(obs, VERTICAL, "save_backtrack")


async def apply_key(obs: ObsClient, target: StreamTarget, server: str, key: str) -> None:
    if target.kind == "main":
        await obs.set_stream_destination(server, key)
    elif target.kind == "vertical":
        await _vendor(obs, VERTICAL, "update_stream_server", {"stream_server": server, "index": target.index})
        await _vendor(obs, VERTICAL, "update_stream_key", {"stream_key": key, "index": target.index})
    else:
        raise ObsError(
            f"Aitum Multistream doesn't let other apps change the key of “{target.name}”. "
            "Pick the main stream or a Vertical Canvas output, or paste the key into that output yourself."
        )


async def set_streaming(obs: ObsClient, target: StreamTarget, start: bool, multistream: bool) -> None:
    """Start or stop only the chosen output."""
    if target.kind == "main":
        await (obs.start_stream() if start else obs.stop_stream())
    elif multistream and target.name:
        await _vendor(obs, MULTISTREAM, "start_output" if start else "stop_output", {"name": target.name})
    elif target.kind == "vertical":
        # Without Multistream there's no per-output control: this starts every enabled vertical output.
        await _vendor(obs, VERTICAL, "start_streaming" if start else "stop_streaming")
    else:
        raise ObsError("Can't start this output from Ignite.")


async def is_streaming(obs: ObsClient, target: StreamTarget) -> bool:
    """Main: exact. Vertical: whether any vertical output is live. Multistream: unknown (False)."""
    if target.kind == "main":
        return (await obs.stream_status()).active
    if target.kind == "vertical":
        return bool((await _vendor(obs, VERTICAL, "status")).get("streaming"))
    return False
