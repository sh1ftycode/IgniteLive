"""Ignite backend HTTP API.

Holds each user's latest TikTok stream key for 48 hours (or until that LIVE
is marked used), syncs non-secret settings, and resolves song requests
through a shared, cached YouTube search.

The server never sees Streamlabs tokens and never runs TikTokLive: the
desktop app generates keys itself and only stores the result here.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from collections import defaultdict
from typing import Callable

from aiohttp import web

from ignite_live.music.youtube import LinkRequest, TrackLookupError, YouTubeClient, parse_request
from ignite_live.server.store import Store

KEY_TTL_S = 48 * 3600
SEARCH_CACHE_S = 24 * 3600
SEARCHES_PER_USER_PER_DAY = 80
MAX_SETTINGS_BYTES = 64 * 1024

STORE = web.AppKey("store", Store)
YOUTUBE = web.AppKey("youtube", YouTubeClient)


def _error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _user(request: web.Request) -> int:
    """The authenticated user's id, from the ``Authorization: Bearer`` device token."""
    header = request.headers.get("Authorization", "")
    token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    user_id = request.app[STORE].user_for_token(token) if token else None
    if user_id is None:
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "Missing or invalid device token."}), content_type="application/json"
        )
    return user_id


async def _json_body(request: web.Request, limit: int = 16 * 1024) -> dict:
    raw = b""
    while len(raw) <= limit:
        chunk = await request.content.read(limit + 1 - len(raw))
        if not chunk:
            break
        raw += chunk
    if len(raw) > limit:
        raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=len(raw))
    try:
        body = json.loads(raw or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text="Body must be JSON")
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="Body must be a JSON object")
    return body


def create_app(
    store: Store,
    youtube: YouTubeClient | None = None,
    key_ttl_s: float = KEY_TTL_S,
    clock: Callable[[], float] = time.time,
) -> web.Application:
    app = web.Application(client_max_size=MAX_SETTINGS_BYTES + 1024)
    app[STORE] = store
    app[YOUTUBE] = youtube or YouTubeClient()
    search_counts: dict[tuple[int, int], int] = defaultdict(int)

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "search": app[YOUTUBE].can_search})

    def key_payload(stored) -> dict:
        return {
            "server": stored.server,
            "key": stored.key,
            "session_id": stored.session_id,
            "created_at": stored.created_at,
            "expires_at": stored.expires_at,
        }

    async def get_key(request: web.Request) -> web.Response:
        stored = store.get_key(_user(request))
        if stored is None:
            return _error(404, "No active stream key. Generate a new one.")
        return web.json_response(key_payload(stored))

    async def put_key(request: web.Request) -> web.Response:
        user_id = _user(request)
        body = await _json_body(request)
        server, key = body.get("server"), body.get("key")
        session_id = body.get("session_id")
        if not isinstance(server, str) or not server.startswith(("rtmp://", "rtmps://")):
            return _error(400, "server must be an rtmp:// or rtmps:// URL.")
        if not isinstance(key, str) or not key.strip() or len(key) > 512:
            return _error(400, "key is required.")
        if session_id is not None and not isinstance(session_id, str):
            return _error(400, "session_id must be a string.")
        stored = store.put_key(user_id, server, key.strip(), session_id, key_ttl_s)
        return web.json_response(key_payload(stored), status=201)

    async def key_used(request: web.Request) -> web.Response:
        store.mark_key_used(_user(request))
        return web.Response(status=204)

    async def delete_key(request: web.Request) -> web.Response:
        store.delete_key(_user(request))
        return web.Response(status=204)

    async def get_settings(request: web.Request) -> web.Response:
        found = store.get_settings(_user(request))
        if found is None:
            return _error(404, "No settings saved yet.")
        body, updated_at = found
        return web.json_response({"settings": body, "updated_at": updated_at})

    async def put_settings(request: web.Request) -> web.Response:
        user_id = _user(request)
        body = await _json_body(request, MAX_SETTINGS_BYTES)
        settings = body.get("settings")
        if not isinstance(settings, dict):
            return _error(400, "settings must be a JSON object.")
        return web.json_response({"updated_at": store.put_settings(user_id, settings)})

    async def resolve(request: web.Request) -> web.Response:
        user_id = _user(request)
        body = await _json_body(request)
        parsed = parse_request(str(body.get("request", "")))
        if parsed is None:
            return _error(400, "Send a YouTube link or song - artist.")
        cache_key = (
            f"id:{parsed.video_id}" if isinstance(parsed, LinkRequest) else "q:" + parsed.query.lower()
        )
        cached = store.cached_search(cache_key, SEARCH_CACHE_S)
        if cached is not None:
            return web.json_response({**cached, "cached": True})
        if not isinstance(parsed, LinkRequest):
            bucket = (user_id, int(clock() // 86400))
            if search_counts[bucket] >= SEARCHES_PER_USER_PER_DAY:
                return _error(429, "Daily song search limit reached. Send links instead.")
            search_counts[bucket] += 1
        try:
            track = await asyncio.to_thread(app[YOUTUBE].resolve, parsed)
        except TrackLookupError as error:
            return _error(422, str(error))
        result = {"track": dataclasses.asdict(track)}
        store.cache_search(cache_key, result)
        return web.json_response({**result, "cached": False})

    app.router.add_get("/v1/health", health)
    app.router.add_get("/v1/stream-key", get_key)
    app.router.add_put("/v1/stream-key", put_key)
    app.router.add_delete("/v1/stream-key", delete_key)
    app.router.add_post("/v1/stream-key/used", key_used)
    app.router.add_get("/v1/settings", get_settings)
    app.router.add_put("/v1/settings", put_settings)
    app.router.add_post("/v1/music/resolve", resolve)

    async def purge_loop(_app: web.Application):
        async def loop():
            while True:
                store.purge_expired()
                await asyncio.sleep(600)

        task = asyncio.create_task(loop())
        yield
        task.cancel()

    app.cleanup_ctx.append(purge_loop)
    return app
