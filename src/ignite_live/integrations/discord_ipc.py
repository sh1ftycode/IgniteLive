"""Talking to the Discord app on this PC, for Rich Presence (live/presence.py says what to show).

Discord listens on a local socket (Linux, macOS: ``discord-ipc-0`` in the runtime folder, or inside the Flatpak or
Snap Discord's own folder) or a named pipe (Windows: ``\\\\?\\pipe\\discord-ipc-0``). Each message is an 8-byte
header (opcode, length; little-endian) and JSON: a handshake with the application's ID, then SET_ACTIVITY.
Nothing leaves this PC from here; Discord shows the activity on the streamer's profile.
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
import sys
import uuid
from pathlib import Path

HANDSHAKE, FRAME, CLOSE = 0, 1, 2
MAX_FRAME = 64 * 1024
TIMEOUT_S = 5


class DiscordIpcError(Exception):
    """A safe-to-display problem."""


def socket_paths(environ: dict | None = None) -> list[str]:
    """Where Discord's socket can be, most likely first."""
    environ = os.environ if environ is None else environ
    if sys.platform == "win32":
        return [rf"\\?\pipe\discord-ipc-{i}" for i in range(10)]
    folders = [environ.get(name) for name in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP")] + ["/tmp"]
    found: list[str] = []
    for folder in dict.fromkeys(f for f in folders if f):
        for sub in ("", "app/com.discordapp.Discord", "snap.discord", ".flatpak/com.discordapp.Discord/xdg-run"):
            for i in range(10):
                path = str(Path(folder, sub, f"discord-ipc-{i}"))
                if path not in found:
                    found.append(path)
    return found


def pack(opcode: int, payload: dict) -> bytes:
    data = json.dumps(payload, separators=(",", ":")).encode()
    return struct.pack("<II", opcode, len(data)) + data


class DiscordIpc:
    """One connection to the Discord app. Async; on Windows the pipe's blocking reads run in a thread."""

    def __init__(self, app_id: str, paths: list[str] | None = None) -> None:
        if not (app_id.isdigit() and 15 <= len(app_id) <= 25):
            raise DiscordIpcError("Enter your Discord application's ID (a long number from the Developer Portal).")
        self.app_id = app_id
        self._paths = paths
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pipe = None  # Windows: the open pipe file.

    @property
    def connected(self) -> bool:
        return self._writer is not None or self._pipe is not None

    async def connect(self) -> None:
        for path in self._paths if self._paths is not None else socket_paths():
            try:
                if sys.platform == "win32":
                    self._pipe = await asyncio.to_thread(open, path, "r+b", 0)
                else:
                    if not Path(path).exists():
                        continue
                    self._reader, self._writer = await asyncio.wait_for(asyncio.open_unix_connection(path), TIMEOUT_S)
            except (OSError, asyncio.TimeoutError):
                self._pipe = self._reader = self._writer = None
                continue
            try:
                await self._send(HANDSHAKE, {"v": 1, "client_id": self.app_id})
                opcode, reply = await self._receive()
            except (OSError, asyncio.TimeoutError, DiscordIpcError):
                await self.close()
                continue
            if opcode == CLOSE or reply.get("evt") == "ERROR":
                await self.close()
                message = str((reply.get("data") or {}).get("message") or reply.get("message") or "")
                raise DiscordIpcError(f"Discord refused the application ID{f': {message}' if message else ''}.")
            return
        raise DiscordIpcError("The Discord app isn't running on this PC (the browser version can't show it).")

    async def set_activity(self, activity: dict | None) -> None:
        """Show ``activity`` (None clears it)."""
        if not self.connected:
            await self.connect()
        payload = {"cmd": "SET_ACTIVITY", "args": {"pid": os.getpid(), "activity": activity}, "nonce": str(uuid.uuid4())}
        try:
            await self._send(FRAME, payload)
            _opcode, reply = await self._receive()
        except (OSError, asyncio.TimeoutError) as error:
            await self.close()
            raise DiscordIpcError("Lost the connection to the Discord app.") from error
        if reply.get("evt") == "ERROR":
            message = str((reply.get("data") or {}).get("message") or "")
            raise DiscordIpcError(f"Discord didn't accept the presence{f': {message}' if message else ''}.")

    async def close(self) -> None:
        writer, pipe = self._writer, self._pipe
        self._reader = self._writer = self._pipe = None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
        if pipe is not None:
            await asyncio.to_thread(pipe.close)

    async def _send(self, opcode: int, payload: dict) -> None:
        data = pack(opcode, payload)
        if self._pipe is not None:
            await asyncio.wait_for(asyncio.to_thread(self._pipe.write, data), TIMEOUT_S)
        elif self._writer is not None:
            self._writer.write(data)
            await asyncio.wait_for(self._writer.drain(), TIMEOUT_S)
        else:
            raise OSError("not connected")

    async def _read(self, size: int) -> bytes:
        if self._pipe is not None:
            data = await asyncio.wait_for(asyncio.to_thread(self._pipe.read, size), TIMEOUT_S)
            if len(data) < size:
                raise OSError("pipe closed")
            return data
        if self._reader is None:
            raise OSError("not connected")
        return await asyncio.wait_for(self._reader.readexactly(size), TIMEOUT_S)

    async def _receive(self) -> tuple[int, dict]:
        try:
            opcode, length = struct.unpack("<II", await self._read(8))
        except asyncio.IncompleteReadError as error:
            raise OSError("closed") from error
        if length > MAX_FRAME:
            raise DiscordIpcError("Discord sent something unexpected.")
        try:
            payload = json.loads(await self._read(length)) if length else {}
        except (ValueError, asyncio.IncompleteReadError) as error:
            raise DiscordIpcError("Discord sent something unexpected.") from error
        return opcode, payload if isinstance(payload, dict) else {}
