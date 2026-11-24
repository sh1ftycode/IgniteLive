"""Minecraft through ServerTap (https://servertap.io), a plugin that gives a Paper/Spigot server a local web API.

Action rules (actions/rules.py, kind "minecraft") run console commands or broadcast a message through it, e.g. a
gift running ``ttinteractive gift {each} {user}`` for TT Interactive Stream Games. Checked against ServerTap 0.6.1:

    POST /v1/server/exec       form: command, time (ms to wait for the command's output)
    POST /v1/chat/broadcast    form: message
    GET  /v1/server            name, version, …
    GET  /v1/players           online players: displayName, uuid, …

Every request carries the ``key`` header (plugins/ServerTap/config.yml: ``key``; it ships as ``change_me``).

Viewers' names and messages go into commands, so placeholders are cleaned first (command_value): only letters,
digits and ``_ . -`` survive. A name can't add arguments, selectors or a second command.
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable

import aiohttp

DEFAULT_URL = "http://localhost:4567"
TIMEOUT_S = 8
OUTPUT_WAIT_MS = 500  # How long ServerTap waits for a command's output before answering.
MAX_COMMAND = 256  # Characters: generous for real commands, stops a runaway template.
MAX_MESSAGE = 256
_UNSAFE = re.compile(r"[^A-Za-z0-9_.\-]+")
_FORMAT = re.compile(r"[§\x00-\x1f\x7f]")  # Minecraft's § formatting codes and control characters.


class ServerTapError(Exception):
    """A safe-to-display problem."""


@dataclass(frozen=True)
class ServerInfo:
    name: str
    version: str
    players: tuple[str, ...]  # Online players' names.


def command_value(value: Any) -> str:
    """A placeholder's value as it goes into a command: one plain word, never more."""
    text = _UNSAFE.sub("_", str(value)).strip("_.-")[:40]
    return text or "viewer"


def message_value(value: Any) -> str:
    """A placeholder's value as it goes into a broadcast: plain text, no formatting codes."""
    return " ".join(_FORMAT.sub("", str(value)).split())[:120]


def check_url(url: str) -> str:
    """The base address, or raises: http(s) only; a key sent in the clear only to this PC or the local network."""
    url = (url or DEFAULT_URL).strip().rstrip("/")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ServerTapError("Enter ServerTap's address, like http://localhost:4567.")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ServerTapError("Enter only ServerTap's address and port, like http://localhost:4567.")
    return url


class ServerTap:
    def __init__(self, url: str, key: str, session_factory: Callable[..., aiohttp.ClientSession] | None = None):
        self.url = check_url(url)
        self._key = key
        if session_factory is None:
            from ignite_live.core.http import tls_session

            session_factory = tls_session
        self._session_factory = session_factory

    def __repr__(self) -> str:  # Never the key.
        return f"ServerTap({self.url!r})"

    async def run(self, command: str) -> str:
        """Run a console command; returns what it printed (may be empty)."""
        command = " ".join(command.split()).lstrip("/")[:MAX_COMMAND]
        if not command:
            raise ServerTapError("The Minecraft command is empty.")
        status, payload = await self._request("POST", "/v1/server/exec",
                                              {"command": command, "time": str(OUTPUT_WAIT_MS)})
        return payload if isinstance(payload, str) else ""

    async def broadcast(self, message: str) -> None:
        message = message_value(message)[:MAX_MESSAGE]
        if message:
            await self._request("POST", "/v1/chat/broadcast", {"message": message})

    async def info(self) -> ServerInfo:
        """For Preferences → Minecraft → Test Connection."""
        _status, server = await self._request("GET", "/v1/server")
        _status, players = await self._request("GET", "/v1/players")
        server = server if isinstance(server, dict) else {}
        names = tuple(str(p.get("displayName") or p.get("name") or "")[:40] for p in players
                      if isinstance(p, dict)) if isinstance(players, list) else ()
        return ServerInfo(str(server.get("name") or "Minecraft server")[:60], str(server.get("version") or "")[:80],
                          tuple(name for name in names if name))

    async def _request(self, method: str, path: str, form: dict | None = None) -> tuple[int, Any]:
        headers = {"key": self._key} if self._key else {}
        try:
            async with self._session_factory(timeout=aiohttp.ClientTimeout(total=TIMEOUT_S)) as session:
                async with session.request(method, self.url + path, headers=headers, data=form) as response:
                    text = await response.text()
                    status = response.status
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as error:
            raise ServerTapError(f"Can't reach the Minecraft server at {self.url}. Is it running, with the ServerTap "
                                 "plugin?") from error
        if status in (401, 403):
            raise ServerTapError("ServerTap refused the key. Copy “key” from plugins/ServerTap/config.yml on the "
                                 "Minecraft server into File → Preferences → Minecraft.")
        if status == 404:
            raise ServerTapError("That address answered, but it isn't ServerTap (or it's an old version).")
        if status >= 400:
            raise ServerTapError(f"ServerTap answered HTTP {status}: {text.strip()[:160]}")
        try:
            return status, json.loads(text) if text.strip()[:1] in ("{", "[", '"') else text
        except ValueError:
            return status, text
