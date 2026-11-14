"""Local overlay server: pages for OBS Browser Sources plus a control WebSocket.

Security: binds to the loopback interface only, requires a per-install token
on every request, and checks Host/Origin so web pages open in a normal browser
can't drive the player (DNS rebinding, cross-site WebSocket).

Pages are addressed as ``localhost``, not ``127.0.0.1``: YouTube refuses to
embed most label-owned music (error 150, "embedding disabled") on a page whose
origin is a bare IP, but plays it on ``localhost``.

Music plays in the Now Playing overlay. When it's open more than once (say in a
horizontal and a vertical scene), only one copy plays the audio, so it's never
heard twice; the others show the album cover and follow along.
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
from ignite_live.overlay import api
from ignite_live.overlay.api import ApiHub

# name -> (title, suggested OBS Browser Source width, height)
PAGES = {
    "nowplaying": ("Now playing", 760, 240),  # Also plays the music (see MUSIC_PAGE).
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
    "spring": "Spring",
    "summer": "Summer",
    "autumn": "Autumn",
    "winter": "Winter",
    "halloween": "Halloween",
    "thanksgiving": "Thanksgiving",
    "christmas": "Christmas",
}
# The app also offers "Seasonal" (core/seasons.py): it resolves that to one of the themes above before sending it.
# Pages for the app's previews, not for OBS: every theme side by side, and the page each tile shows.
PREVIEW_PAGES = ("gallery", "showcase")
MUSIC_PAGE = "nowplaying"  # The overlay that plays the song requests.
# Old page names, still served so Browser Sources added before keep working.
PAGE_ALIASES = {"player": MUSIC_PAGE}
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
        # music_state() changed: a Now Playing overlay opened or closed, or the volume or pause changed.
        self.on_music_changed: Callable[[dict], None] | None = None
        self.on_toast: Callable[[str], None] | None = None
        self.on_choose_theme: Callable[[str], None] | None = None  # "Use this theme" in the gallery.
        # Everything sent to every overlay, for hosted widgets on an Ignite server (through the relay).
        self.on_broadcast: Callable[[dict], None] | None = None
        # Origins of the Ignite server whose hosted pages may fall back to this PC (still with the token).
        self.trusted_origins: set[str] = set()
        # The app's own music buttons: where the song is (twice a second), and whether anything can play it.
        self.on_progress: Callable[[dict], None] | None = None
        self.on_player_changed: Callable[[bool], None] | None = None
        self._panic_paused = False  # The music is paused while either is set.
        self._user_paused = False
        self._sockets: set[web.WebSocketResponse] = set()
        self._previews: set[web.WebSocketResponse] = set()  # Gallery and &demo=1 pages: not counted as overlays.
        self._pages: dict[web.WebSocketResponse, str] = {}  # Which overlay each socket is (for the checklist).
        self._players: list[web.WebSocketResponse] = []  # Open Now Playing overlays, oldest first.
        self._audio: web.WebSocketResponse | None = None  # The one of them that plays the music.
        self._state: dict = {
            "type": "state", "queue": {"current": None, "upcoming": []}, "volume": 60, "theme": "minimal",
            "texts": {}, "style": overlay_style(), "quiet": False, "paused": False,
        }
        self._progress: dict | None = None
        self._goals: dict = {"type": "goals", "goals": []}
        self._leaderboards: dict = {"type": "leaderboards", "boards": {}}
        self._game: dict = {"type": "game", "game": None}
        self._runner: web.AppRunner | None = None
        # The local API (overlay/api.py): events, state and commands for any widget or app on this PC.
        self.api = ApiHub()
        self.api_key = ""  # The API's control key; empty: no commands.
        self.app = web.Application(middlewares=[self._guard])
        # Pages reach their socket and files next to themselves (/overlay/ws), so the same pages work hosted on an
        # Ignite server (/w/<key>/ws). The root ones stay for pages opened before that. Before /overlay/{page}.
        for prefix in ("/overlay", ""):
            self.app.router.add_get(f"{prefix}/ws", self._socket)
            self.app.router.add_get(f"{prefix}/sounds/{{name}}", self._sound)
            self.app.router.add_get(f"{prefix}/images/{{name}}", self._image)
        self.app.router.add_get("/overlay/{page}", self._page)
        self.app.router.add_get(api.PREFIX, lambda r: self.api.http_info(r, r["control"]))
        self.app.router.add_get(f"{api.PREFIX}/ws", lambda r: self.api.socket(r, r["control"]))
        self.app.router.add_get(f"{api.PREFIX}/state", lambda r: self.api.http_state(r, r["control"]))
        self.app.router.add_get(f"{api.PREFIX}/state/{{kind}}", lambda r: self.api.http_state(r, r["control"]))
        self.app.router.add_route("GET", f"{api.PREFIX}/commands/{{name}}",
                                  lambda r: self.api.http_command(r, r["control"]))
        self.app.router.add_route("POST", f"{api.PREFIX}/commands/{{name}}",
                                  lambda r: self.api.http_command(r, r["control"]))

    def api_url(self, path: str = "", control: bool = False, websocket: bool = False) -> str:
        """The API's address (with the overlay token, or the control key), for the Overlays tab."""
        scheme = "ws" if websocket else "http"
        credential = f"key={self.api_key}" if control else f"token={self.token}"
        return f"{scheme}://localhost:{self.port}{api.PREFIX}{path}?{credential}"

    def local_fallback(self) -> dict:
        """Where a hosted page reaches this PC when the Ignite server doesn't answer (common.js IGNITE_LOCAL)."""
        base = f"localhost:{self.port}"
        return {"socket": f"ws://{base}/overlay/ws", "files": f"http://{base}/overlay/", "token": self.token,
                "api": f"ws://{base}{api.PREFIX}/ws"}

    def url(self, page: str = MUSIC_PAGE, theme: str = "") -> str:
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
        await self.api.close()
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

    async def clear_chat(self) -> None:
        """Clear All Chats: the chat overlays drop every line they show (alerts already queued still play)."""
        self.api.emit("chat_clear", {})
        await self._broadcast({"type": "chat_clear"})

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
        self.api.set_state("goals", bars)
        await self._broadcast(self._goals)

    async def set_leaderboards(self, boards: dict) -> None:
        """Leaderboards.snapshot(); pages opened later get the latest copy."""
        self._leaderboards = {"type": "leaderboards", "boards": boards}
        self.api.set_state("leaderboards", boards)
        await self._broadcast(self._leaderboards)

    async def leader(self, board: str, name: str, value: int) -> None:
        """Someone took first place: the leaderboard overlay showing that board celebrates."""
        await self._broadcast({"type": "leader", "board": board, "name": name[:40], "value": value})

    async def set_game(self, snapshot: dict | None) -> None:
        """The running game (None: none); pages opened later get the latest copy."""
        self._game = {"type": "game", "game": snapshot}
        self.api.set_state("game", snapshot)
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
        """Panic mode: the Alerts overlay drops what's showing and waiting; the music pauses."""
        self._panic_paused = paused
        self._state = {**self._state, "quiet": quiet_alerts, "paused": paused or self._user_paused}
        self._music_changed()
        await self._broadcast(self._state)

    async def set_paused(self, paused: bool) -> None:
        """The Pause button. Kept apart from panic's pause, so Resume doesn't start music you paused."""
        self._user_paused = paused
        self._state = {**self._state, "paused": paused or self._panic_paused}
        self._music_changed()
        await self._broadcast(self._state)

    @property
    def user_paused(self) -> bool:
        return self._user_paused

    @property
    def has_player(self) -> bool:
        """Whether a Now Playing overlay is open to play the music."""
        return self._audio is not None

    @property
    def progress(self) -> dict | None:
        return self._progress

    async def seek(self, item_id: int, position: float) -> None:
        """Jump within the current song (0: start it over). Only the copy that plays acts on it."""
        await self._broadcast({"type": "seek", "item_id": item_id, "position": max(0.0, float(position))})

    async def set_volume(self, volume: int) -> None:
        self._state = {**self._state, "volume": max(0, min(100, volume))}
        self._music_changed()
        await self._broadcast(self._state)

    def music_state(self) -> dict:
        """For a player outside OBS (the web dashboard): whether one is needed, and how to play."""
        return {"needed": not self._players, "volume": self._state["volume"], "paused": self._state["paused"]}

    async def outside_progress(self, data: dict) -> None:
        """Progress from a player outside OBS; ignored while a Now Playing overlay plays the music."""
        if self._players:
            return
        self._progress = _progress(data)
        await self._broadcast(self._progress)

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
        self._music_changed()

    def _music_changed(self) -> None:
        if self.on_music_changed:
            self.on_music_changed(self.music_state())

    def snapshot(self) -> list[dict]:
        """What an overlay opened now is sent first: for hosted widgets, after the relay (re)connects."""
        sticky = [self._state, self._progress, self._goals, self._leaderboards, self._game]
        return [message for message in sticky if message]

    async def _broadcast(self, message: dict, skip: web.WebSocketResponse | None = None) -> None:
        if self.on_broadcast is not None:
            self.on_broadcast(message)
        for ws in list(self._sockets):
            if ws is skip:
                continue
            try:
                await ws.send_json(message)
            except ConnectionError:
                self._sockets.discard(ws)
                self._previews.discard(ws)
                self._pages.pop(ws, None)
                if ws in self._players:
                    self._players.remove(ws)  # The socket's own cleanup chooses another copy to play.
                self._clients_changed()

    def _allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"}

    @web.middleware
    async def _guard(self, request: web.Request, handler):
        if request.host not in self._allowed_hosts():
            raise web.HTTPForbidden(text="Unknown host")
        if request.path == api.PREFIX or request.path.startswith(api.PREFIX + "/"):
            return await self._api_guard(request, handler)
        if not secrets.compare_digest(request.query.get("token", ""), self.token):
            raise web.HTTPForbidden(text="Missing or wrong overlay token")
        origin = request.headers.get("Origin")
        if origin and origin.removeprefix("http://") not in self._allowed_hosts() and origin not in self.trusted_origins:
            raise web.HTTPForbidden(text="Cross-origin request refused")
        response = await handler(request)
        response.headers["Cache-Control"] = "no-store"
        # The YouTube embed refuses to play (error 153) without a referrer.
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    async def _api_guard(self, request: web.Request, handler) -> web.StreamResponse:
        """The API takes any Origin (widgets live anywhere), but always the overlay token or the control key."""
        if request.method == "OPTIONS":  # A browser's preflight carries no key; it learns nothing from this.
            response: web.StreamResponse = web.Response(status=204)
        else:
            bearer = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            offered = [request.query.get("key", ""), request.query.get("token", ""), bearer]
            control = bool(self.api_key) and any(secrets.compare_digest(o, self.api_key) for o in offered if o)
            if not control and not any(secrets.compare_digest(o, self.token) for o in offered if o):
                response = web.json_response(
                    {"ok": False, "message": "Add ?token= (the overlay token) or ?key= (the control key)."},
                    status=403)
            else:
                request["control"] = control
                response = await handler(request)
        if not response.prepared:  # A WebSocket has already sent its headers.
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Cache-Control"] = "no-store"
        return response

    async def _page(self, request: web.Request) -> web.Response:
        page = PAGE_ALIASES.get(request.match_info["page"], request.match_info["page"])
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
        page = PAGE_ALIASES.get(request.query.get("page", ""), request.query.get("page", ""))
        if page in PAGES:
            self._pages[ws] = page
        self._sockets.add(ws)
        if page == MUSIC_PAGE and ws not in self._previews:
            self._players.append(ws)
        self._clients_changed()
        try:
            await ws.send_json(self._state)
            if ws in self._players:
                await ws.send_json({"type": "role", "audio": False})  # Until told otherwise.
                await self._choose_audio()
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
                kind = data.get("type")
                if kind in {"ended", "error", "playing", "progress"} and ws is not self._audio:
                    continue  # Only the copy that plays the music reports on it.
                if kind in {"ended", "error", "playing"} and self.on_player_message:
                    self.on_player_message(data)
                elif data.get("type") == "choose_theme" and data.get("theme") in THEMES and self.on_choose_theme:
                    self.on_choose_theme(data["theme"])
                elif data.get("type") == "progress":
                    # From the copy that plays; other Now Playing copies and the visualizer follow it.
                    self._progress = _progress(data)
                    if self.on_progress:
                        self.on_progress(self._progress)
                    await self._broadcast(self._progress, skip=ws)
        finally:
            self._sockets.discard(ws)
            self._previews.discard(ws)
            self._pages.pop(ws, None)
            was_player = ws in self._players or ws is self._audio
            if ws in self._players:
                self._players.remove(ws)
            if was_player:
                await self._choose_audio()  # Another open copy takes over the music.
            self._clients_changed()
        return ws

    async def _choose_audio(self) -> None:
        """The oldest open Now Playing overlay plays the music; tell it when that changes."""
        leader = self._players[0] if self._players else None
        if leader is self._audio:
            return
        had_player = self._audio is not None
        self._audio = leader
        if (leader is not None) != had_player and self.on_player_changed:
            self.on_player_changed(leader is not None)
        if leader is not None:
            try:
                await leader.send_json({"type": "role", "audio": True})
            except ConnectionError:
                pass  # It's closing; its own cleanup chooses again.


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
