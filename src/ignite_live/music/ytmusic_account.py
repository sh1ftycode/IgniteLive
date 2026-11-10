"""Your own YouTube Music account: its playlists and Liked Music as background music.

YouTube Music has no public API and no sign-in for other apps. What does work
is what its website does: requests carrying your browser's session cookie plus
an ``Authorization: SAPISIDHASH …`` header derived from one of those cookies
(the same approach as the ``ytmusicapi`` project). So "connecting" means pasting
the cookie from a signed-in music.youtube.com tab once.

That cookie is as good as being signed in to your Google account on YouTube, so
it is treated like a password: it lives in the OS keychain only, is sent to
music.youtube.com only, and never appears in logs, errors or settings files.

Only reading is done here: the account's name, its list of playlists, and (in
``YouTubeClient.playlist``) a playlist's songs. Songs still play through the
normal YouTube player in the Now Playing overlay, so tracks you uploaded
yourself and Premium-only audio don't play; everything in YouTube's catalogue
does. Undocumented and unsupported by Google: it can stop working when YouTube
changes its site, and the cookie expires when you sign out in that browser.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Callable

from ignite_live.core.http import Transport, TransportError, urllib_transport
from ignite_live.music.youtube import _MUSIC_CLIENT, ACCOUNT_PLAYLISTS, _renderers, _text

ORIGIN = "https://music.youtube.com"
BROWSE_URL = f"{ORIGIN}/youtubei/v1/browse?prettyPrint=false"
ACCOUNT_URL = f"{ORIGIN}/youtubei/v1/account/account_menu?prettyPrint=false"
LIBRARY_PLAYLISTS = "FEmusic_liked_playlists"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36")
# The cookies the SAPISIDHASH header can be made from, in the order YouTube Music's own site prefers.
_SAPISID_COOKIES = ("__Secure-3PAPISID", "SAPISID", "__Secure-1PAPISID")
MAX_COOKIE_CHARS = 16_000
MAX_PLAYLISTS = 200


class AccountError(Exception):
    """Safe to show. Never contains the cookie."""


@dataclass(frozen=True)
class AccountPlaylist:
    id: str
    name: str
    detail: str = ""  # "Playlist • 42 songs", as YouTube Music words it.

    @property
    def link(self) -> str:
        return f"{ORIGIN}/playlist?list={self.id}"


def parse_cookie(text: str) -> dict[str, str]:
    """The cookies in a pasted ``Cookie`` header (with or without the "Cookie:" in front)."""
    text = " ".join(text.split())
    if text.lower().startswith("cookie:"):
        text = text[7:]
    cookies = {}
    for part in text.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name and all(32 < ord(char) < 127 for char in name + value):
            cookies[name] = value
    return cookies


def clean_cookie(text: str) -> str:
    """A pasted cookie as one tidy header value. Raises AccountError when it can't be a signed-in session."""
    if len(text) > MAX_COOKIE_CHARS:
        raise AccountError("That's too long to be a cookie. Copy only the value of the request's “cookie” header.")
    cookies = parse_cookie(text)
    if not any(cookies.get(name) for name in _SAPISID_COOKIES):
        raise AccountError("That doesn't look like a signed-in YouTube Music cookie (it has no SAPISID). Copy the "
                           "whole “cookie” header from a request to music.youtube.com while signed in.")
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


def sapisid_hash(sapisid: str, now: int, origin: str = ORIGIN) -> str:
    """The ``Authorization`` value YouTube's own pages send: a timestamp and a SHA-1 over it, the cookie, the origin."""
    digest = hashlib.sha1(f"{now} {sapisid} {origin}".encode()).hexdigest()
    return f"SAPISIDHASH {now}_{digest}"


def library_playlists(payload: dict) -> list[AccountPlaylist]:
    """Playlists in a library page: every tile that opens a playlist ("New playlist" and the like are skipped)."""
    found: dict[str, AccountPlaylist] = {}
    for kind in ("musicTwoRowItemRenderer", "musicResponsiveListItemRenderer"):  # Grid view, or list view.
        for tile in _renderers(payload, kind):
            browse = ((tile.get("navigationEndpoint") or {}).get("browseEndpoint") or {}).get("browseId")
            if not isinstance(browse, str) or not browse.startswith("VL") or len(browse) > 80:
                continue
            title = tile.get("title")
            if title is None:  # List view keeps its texts in columns.
                columns = tile.get("flexColumns") or [{}]
                first = columns[0] if isinstance(columns[0], dict) else {}
                title = (first.get("musicResponsiveListItemFlexColumnRenderer") or {}).get("text")
            name = _text(title).strip()[:120]
            if name and browse[2:] not in found:
                found[browse[2:]] = AccountPlaylist(browse[2:], name, _text(tile.get("subtitle")).strip()[:120])
    return list(found.values())[:MAX_PLAYLISTS]


class YouTubeMusicAccount:
    def __init__(self, cookie: str, transport: Transport = urllib_transport,
                 clock: Callable[[], float] = time.time) -> None:
        self._cookie = clean_cookie(cookie)
        cookies = parse_cookie(self._cookie)
        self._sapisid = next(cookies[name] for name in _SAPISID_COOKIES if cookies.get(name))
        self._transport = transport
        self._clock = clock

    @property
    def cookie(self) -> str:
        """The cleaned-up cookie, to keep in the keychain."""
        return self._cookie

    def __repr__(self) -> str:  # Never the cookie: reprs end up in logs and tracebacks.
        return "YouTubeMusicAccount(…)"

    def headers(self) -> dict[str, str]:
        """What a signed-in request to music.youtube.com carries."""
        return {
            "Cookie": self._cookie,
            "Authorization": sapisid_hash(self._sapisid, int(self._clock())),
            "X-Origin": ORIGIN,
            "X-Goog-AuthUser": "0",
            "User-Agent": USER_AGENT,
        }

    def _post(self, url: str, body: dict) -> dict:
        payload = json.dumps({"context": {"client": _MUSIC_CLIENT}, **body}).encode()
        try:
            status, raw = self._transport("POST", url, {
                "Content-Type": "application/json", "Origin": ORIGIN, **self.headers()}, payload)
        except TransportError as error:
            raise AccountError(f"Could not reach YouTube Music: {error}.") from None
        if status in (401, 403):
            raise AccountError("YouTube Music didn't accept that cookie. It may have expired (signing out in the "
                               "browser ends it): copy a fresh one.")
        if status != 200:
            raise AccountError(f"YouTube Music returned HTTP {status}.")
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise AccountError("YouTube Music returned an unreadable response.") from None
        if not isinstance(data, dict):
            raise AccountError("YouTube Music returned an unexpected response.")
        return data

    def name(self) -> str:
        """The account's display name, or "" when YouTube doesn't say (not worth failing over)."""
        try:
            menu = self._post(ACCOUNT_URL, {})
        except AccountError:
            return ""
        header = next(_renderers(menu, "activeAccountHeaderRenderer"), {})
        return _text(header.get("accountName")).strip()[:80]

    def playlists(self) -> list[AccountPlaylist]:
        """The library's playlists, Liked Music first. Raises AccountError when the cookie isn't signed in."""
        payload = self._post(BROWSE_URL, {"browseId": LIBRARY_PLAYLISTS})
        found = library_playlists(payload)
        if not found:
            # Signed out, the library page is a "Sign in" prompt with no playlists (a real library always has
            # at least Liked Music).
            raise AccountError("YouTube Music answered as if signed out: the cookie is incomplete or has expired. "
                               "Copy the whole “cookie” header again while signed in.")
        fixed = [AccountPlaylist(key, label) for key, label in ACCOUNT_PLAYLISTS.items()]
        return fixed + [playlist for playlist in found if playlist.id not in ACCOUNT_PLAYLISTS]
