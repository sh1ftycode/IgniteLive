"""Local overlay server: pages for OBS Browser Sources plus a control WebSocket.

Security: binds to the loopback interface only, requires a per-install token
on every request, and checks Host/Origin so web pages open in a normal browser
can't drive the player (DNS rebinding, cross-site WebSocket).

Pages are addressed as ``localhost``, not ``127.0.0.1``: YouTube refuses to
embed most label-owned music (error 150, "embedding disabled") on a page whose
origin is a bare IP, but plays it on ``localhost``.
"""

from __future__ import annotations

import json
import re
import secrets
from importlib import resources
from pathlib import Path
from typing import Callable

from aiohttp import WSMsgType, web

from ignite_live.actions.rules import SOUND_NAME
from ignite_live.games.base import IMAGE_NAME

# name -> (title, suggested OBS Browser Source width, height)
PAGES = {
    "player": ("Music player", 1280, 720),
    "nowplaying": ("Now playing", 700, 160),
    "queue": ("Up next", 520, 420),
    "visualizer": ("Visualizer", 1280, 240),
    "chat": ("Chat", 460, 720),
    "alerts": ("Alerts", 900, 320),
    "goals": ("Goals", 800, 220),
    "leaderboard": ("Leaderboard", 460, 520),
    "game": ("Game", 800, 420),
}
THEMES = {
    "minimal": "Minimal",
    "neon": "Neon Arcade",
    "esports": "Esports HUD",
    "retro": "Retro 8-bit",
    "cozy": "Cozy Lo-fi",
    "synthwave": "Synthwave Sunset",
    "cyberpunk": "Cyberpunk",
    "glass": "Frosted Glass",
    "terminal": "Hacker Terminal",
    "fantasy": "Fantasy Quest",
    "kawaii": "Pastel Kawaii",
    "bloodmoon": "Blood Moon",
    "ocean": "Deep Ocean",
    "brutal": "Neo-Brutalist",
}
# Pages for the app's previews, not for OBS: every theme side by side, and the page each tile shows.
PREVIEW_PAGES = ("gallery", "showcase")
DEFAULT_PORT = 17_845
MAX_CSS = 20_000
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_FONT = re.compile(r"^[\w .\-]{1,60}$")


def overlay_style(accent: str = "", accent_2: str = "", font: str = "", css: str = "") -> dict:
    """The streamer's overlay customisation, validated (colours as #rrggbb, a plain font name)."""
    return {
        "accent": accent if _COLOR.match(accent or "") else "",
        "accent2": accent_2 if _COLOR.match(accent_2 or "") else "",
        "font": font.strip() if _FONT.match((font or "").strip()) else "",
        "css": (css or "")[:MAX_CSS],
    }


def _static(name: str) -> str:
    return resources.files("ignite_live.overlay").joinpath("static", name).read_text("utf-8")


def render_page(page: str) -> str:
    """A page with the shared theme stylesheet and connection script inlined."""
    return (
        _static(f"{page}.html")
        .replace("/*{{THEMES}}*/", _static("themes.css"))
        .replace("/*{{COMMON}}*/", _static("common.js"))
        .replace("/*{{THEME_LIST}}*/", json.dumps(THEMES))
    )


class OverlayServer:
    def __init__(
        self,
        token: str | None = None,
        port: int = DEFAULT_PORT,
        on_player_message: Callable[[dict], None] | None = None,
        sounds_dir: Path | None = None,
        images_dir: Path | None = None,
    ) -> None:
        self.token = token or secrets.token_urlsafe(18)
        self.port = port
        self.sounds_dir = sounds_dir  # Sound files for alerts and actions, served by name only.
        self.images_dir = images_dir  # Pictures for games (the boss), served by name only.
        self.on_player_message = on_player_message
        # Optional observers for the app: open overlay count, and every toast shown.
        self.on_clients_changed: Callable[[int], None] | None = None
        self.on_toast: Callable[[str], None] | None = None
        self.on_choose_theme: Callable[[str], None] | None = None  # "Use this theme" in the gallery.
        self._sockets: set[web.WebSocketResponse] = set()
        self._previews: set[web.WebSocketResponse] = set()  # Gallery and &demo=1 pages: not counted as overlays.
        self._pages: dict[web.WebSocketResponse, str] = {}  # Which overlay each socket is (for the checklist).
        self._state: dict = {
            "type": "state", "queue": {"current": None, "upcoming": []}, "volume": 60, "theme": "minimal",
            "texts": {}, "style": overlay_style(), "quiet": False, "paused": False,
        }
        self._progress: dict | None = None
        self._goals: dict = {"type": "goals", "goals": []}
        self._leaderboards: dict = {"type": "leaderboards", "boards": {}}
        self._game: dict = {"type": "game", "game": None}
        self._runner: web.AppRunner | None = None
        self.app = web.Application(middlewares=[self._guard])
        self.app.router.add_get("/overlay/{page}", self._page)
        self.app.router.add_get("/ws", self._socket)
        self.app.router.add_get("/sounds/{name}", self._sound)
        self.app.router.add_get("/images/{name}", self._image)

    def url(self, page: str = "player", theme: str = "") -> str:
        """``theme`` pins this one overlay to a theme instead of following the app's choice."""
        extra = f"&theme={theme}" if theme in THEMES else ""
        return f"http://localhost:{self.port}/overlay/{page}?token={self.token}{extra}"

    async def start(self) -> None:
        self._runner = web.AppRunner(self.app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", self.port).start()
        if self.port == 0:  # Ephemeral port (tests): learn the real one.
            self.port = self._runner.addresses[0][1]
        try:
            # "localhost" resolves to ::1 first on some Windows/macOS setups.
            await web.TCPSite(self._runner, "::1", self.port).start()
        except OSError:
            pass  # No IPv6 loopback; 127.0.0.1 is enough.

    async def stop(self) -> None:
        for ws in list(self._sockets):
            await ws.close()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    async def publish_state(self, queue_snapshot: dict, volume: int | None = None) -> None:
        """Push the queue to every open overlay; late joiners get the latest copy."""
        self._state = {
            **self._state,
            "queue": queue_snapshot,
            "volume": self._state["volume"] if volume is None else max(0, min(100, volume)),
        }
        await self._broadcast(self._state)

    async def set_theme(self, theme: str) -> None:
        if theme in THEMES:
            self._state = {**self._state, "theme": theme}
            await self._broadcast(self._state)

    async def set_texts(self, texts: dict[str, str], command: str) -> None:
        """Overlay labels (the ``overlay.*`` templates) and the request command they mention."""
        self._state = {**self._state, "texts": {**texts, "command": command}}
        await self._broadcast(self._state)

    async def set_style(self, style: dict) -> None:
        self._state = {**self._state, "style": overlay_style(
            style.get("accent", ""), style.get("accent2", ""), style.get("font", ""), style.get("css", ""))}
        await self._broadcast(self._state)

    async def push_chat(self, payload: dict) -> None:
        """Send a chat line, gift, or follow to the chat and alerts overlays (already sanitised)."""
        await self._broadcast({"type": "chat", **payload})

    async def alert(self, title: str, text: str, image: str = "", sound: str = "", volume: int = 100) -> None:
        """A custom alert from an action rule, queued with the gift and follow alerts."""
        await self._broadcast({"type": "alert", "title": title[:80], "text": text[:300], "image": image,
                               "sound": sound if SOUND_NAME.match(sound) else "", "volume": max(0, min(100, volume))})

    async def play_sound(self, name: str, volume: int = 100) -> None:
        """Played by the alerts overlay, so it lands in OBS's audio mix."""
        if SOUND_NAME.match(name):
            await self._broadcast({"type": "sound", "sound": name, "volume": max(0, min(100, volume))})

    async def set_goals(self, bars: list[dict]) -> None:
        """Goal bars (GoalTracker.snapshot()); pages opened later get the latest copy."""
        self._goals = {"type": "goals", "goals": bars}
        await self._broadcast(self._goals)

    async def set_leaderboards(self, boards: dict) -> None:
        """Leaderboards.snapshot(); pages opened later get the latest copy."""
        self._leaderboards = {"type": "leaderboards", "boards": boards}
        await self._broadcast(self._leaderboards)

    async def leader(self, board: str, name: str, value: int) -> None:
        """Someone took first place: the leaderboard overlay showing that board celebrates."""
        await self._broadcast({"type": "leader", "board": board, "name": name[:40], "value": value})

    async def set_game(self, snapshot: dict | None) -> None:
        """The running game (None: none); pages opened later get the latest copy."""
        self._game = {"type": "game", "game": snapshot}
        await self._broadcast(self._game)

    async def game_over(self, result: dict) -> None:
        """A game or a boss round ended: the game overlay shows the winner."""
        await self._broadcast({"type": "game_over", **result})

    def sound_path(self, name: str) -> Path | None:
        """The file for a sound name, if it's a safe name and exists."""
        return _media(self.sounds_dir, name, SOUND_NAME)

    def image_path(self, name: str) -> Path | None:
        return _media(self.images_dir, name, IMAGE_NAME)

    async def set_panic(self, quiet_alerts: bool, paused: bool) -> None:
        """Panic mode: the Alerts overlay drops what's showing and waiting; the music player pauses."""
        self._state = {**self._state, "quiet": quiet_alerts, "paused": paused}
        await self._broadcast(self._state)

    async def set_volume(self, volume: int) -> None:
        self._state = {**self._state, "volume": max(0, min(100, volume))}
        await self._broadcast(self._state)

    @property
    def client_count(self) -> int:
        return len(self._sockets - self._previews)

    def open_pages(self) -> set[str]:
        """Overlays open right now (in OBS or a browser), not counting previews."""
        return {page for ws, page in self._pages.items() if ws in self._sockets and ws not in self._previews}

    async def toast(self, text: str) -> None:
        if self.on_toast:
            self.on_toast(text)
        await self._broadcast({"type": "toast", "text": text[:160]})

    def _clients_changed(self) -> None:
        if self.on_clients_changed:
            self.on_clients_changed(self.client_count)

    async def _broadcast(self, message: dict, skip: web.WebSocketResponse | None = None) -> None:
        for ws in list(self._sockets):
            if ws is skip:
                continue
            try:
                await ws.send_json(message)
            except ConnectionError:
                self._sockets.discard(ws)
                self._previews.discard(ws)
                self._pages.pop(ws, None)
                self._clients_changed()

    def _allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"}

    @web.middleware
    async def _guard(self, request: web.Request, handler):
        if request.host not in self._allowed_hosts():
            raise web.HTTPForbidden(text="Unknown host")
        if not secrets.compare_digest(request.query.get("token", ""), self.token):
            raise web.HTTPForbidden(text="Missing or wrong overlay token")
        origin = request.headers.get("Origin")
        if origin and origin.removeprefix("http://") not in self._allowed_hosts():
            raise web.HTTPForbidden(text="Cross-origin request refused")
        response = await handler(request)
        response.headers["Cache-Control"] = "no-store"
        # The YouTube embed refuses to play (error 153) without a referrer.
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    async def _page(self, request: web.Request) -> web.Response:
        page = request.match_info["page"]
        if page not in PAGES and page not in PREVIEW_PAGES:
            raise web.HTTPNotFound()
        return web.Response(text=render_page(page), content_type="text/html")

    async def _sound(self, request: web.Request) -> web.StreamResponse:
        path = self.sound_path(request.match_info["name"])
        if path is None:
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def _image(self, request: web.Request) -> web.StreamResponse:
        path = self.image_path(request.match_info["name"])
        if path is None:
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def _socket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4096)
        await ws.prepare(request)
        if request.query.get("preview") == "1":
            self._previews.add(ws)
        if request.query.get("page") in PAGES:
            self._pages[ws] = request.query["page"]
        self._sockets.add(ws)
        self._clients_changed()
        try:
            await ws.send_json(self._state)
            if self._progress:
                await ws.send_json(self._progress)
            if self._goals["goals"]:
                await ws.send_json(self._goals)
            if self._leaderboards["boards"]:
                await ws.send_json(self._leaderboards)
            if self._game["game"] is not None:
                await ws.send_json(self._game)
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(message.data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("type") in {"ended", "error", "playing"} and self.on_player_message:
                    self.on_player_message(data)
                elif data.get("type") == "choose_theme" and data.get("theme") in THEMES and self.on_choose_theme:
                    self.on_choose_theme(data["theme"])
                elif data.get("type") == "progress":
                    # From the player page; the now-playing and visualizer overlays follow it.
                    self._progress = _progress(data)
                    await self._broadcast(self._progress, skip=ws)
        finally:
            self._sockets.discard(ws)
            self._previews.discard(ws)
            self._pages.pop(ws, None)
            self._clients_changed()
        return ws


def _media(folder: Path | None, name: str, pattern) -> Path | None:
    """A file in one of our media folders, by plain name only (no paths, no other types)."""
    if folder is None or not pattern.match(name):
        return None
    path = folder / name
    return path if path.is_file() and path.parent.resolve() == folder.resolve() else None


def _progress(data: dict) -> dict:
    """Keep only well-formed numbers from a page's progress report."""

    def number(key: str) -> float:
        value = data.get(key)
        return float(value) if isinstance(value, (int, float)) and value >= 0 else 0.0

    item = data.get("item_id")
    return {
        "type": "progress",
        "item_id": item if isinstance(item, int) else None,
        "position": number("position"),
        "duration": number("duration"),
        "playing": data.get("playing") is True,
    }
