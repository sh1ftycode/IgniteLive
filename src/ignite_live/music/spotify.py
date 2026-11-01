"""Read song titles and artists from a public Spotify playlist link.

This reads Spotify's public embed page (open.spotify.com/embed/playlist/<id>),
the same page any website shows when it embeds a playlist. It needs no
account, but it is not an official API: it's against Spotify's terms, it can
break whenever Spotify changes that page, and it returns only the first ~50
songs. Since February 2026 the official Web API only returns songs of
playlists the logged-in user owns or collaborates on, and needs Premium.

Only titles and artists are used; playback happens through YouTube.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from dataclasses import dataclass

from ignite_live.core.http import Transport, TransportError, urllib_transport
from ignite_live.music.youtube import Track

log = logging.getLogger(__name__)

EMBED_URL = "https://open.spotify.com/embed/playlist/{id}"
_ID = re.compile(r"^[A-Za-z0-9]{22}$")
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0",
    "Accept": "text/html",
    "Accept-Language": "en-US,en;q=0.8",
}


class SpotifyError(Exception):
    """A safe-to-display error."""


@dataclass(frozen=True)
class PlaylistSong:
    title: str
    artist: str
    duration_ms: int = 0
    track: Track | None = None  # Already on YouTube (a YouTube playlist): played as is, no search.

    @property
    def request(self) -> str:
        """The same "song - artist" text a viewer would type after !sr."""
        return f"{self.title} - {self.artist}" if self.artist else self.title


@dataclass(frozen=True)
class Playlist:
    id: str
    name: str
    songs: tuple[PlaylistSong, ...]


def playlist_id_from(text: str) -> str | None:
    """Accepts open.spotify.com links (with or without /intl-xx/ and ?si=), spotify:playlist: URIs, or a bare ID."""
    text = text.strip()
    if _ID.match(text):
        return text
    if text.startswith("spotify:playlist:"):
        candidate = text.rsplit(":", 1)[-1]
        return candidate if _ID.match(candidate) else None
    if "://" not in text:
        text = "https://" + text
    url = urllib.parse.urlsplit(text)
    if (url.hostname or "").lower() not in {"open.spotify.com", "play.spotify.com"}:
        return None
    parts = [part for part in url.path.split("/") if part]
    if "playlist" in parts:
        index = parts.index("playlist")
        if index + 1 < len(parts) and _ID.match(parts[index + 1]):
            return parts[index + 1]
    return None


def _find_entity(node: object) -> dict | None:
    """Locate the object holding ``trackList`` anywhere in the page data."""
    if isinstance(node, dict):
        if isinstance(node.get("trackList"), list):
            return node
        children = node.values()
    elif isinstance(node, list):
        children = node
    else:
        return None
    for child in children:
        found = _find_entity(child)
        if found is not None:
            return found
    return None


def parse_embed_page(html: str, playlist_id: str) -> Playlist:
    match = _NEXT_DATA.search(html)
    if not match:
        raise SpotifyError("Spotify's playlist page changed and can't be read right now.")
    try:
        entity = _find_entity(json.loads(match.group(1)))
    except json.JSONDecodeError as error:
        raise SpotifyError("Spotify's playlist page changed and can't be read right now.") from error
    if entity is None:
        raise SpotifyError("That playlist is private, empty, or doesn't exist.")
    songs = []
    for item in entity["trackList"]:
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            continue
        if item.get("uri") and not str(item["uri"]).startswith("spotify:track:"):
            continue  # Podcast episodes and the like.
        songs.append(
            PlaylistSong(
                title=" ".join(str(item["title"]).split()),
                artist=" ".join(str(item.get("subtitle") or "").split()),  # Spotify uses non-breaking spaces.
                duration_ms=int(item.get("duration") or 0),
            )
        )
    if not songs:
        raise SpotifyError("That playlist has no songs Ignite can use.")
    name = str(entity.get("title") or entity.get("name") or "Spotify playlist")
    return Playlist(playlist_id, name, tuple(songs))


def fetch_public_playlist(link: str, transport: Transport = urllib_transport) -> Playlist:
    """Blocking: run it off the GUI thread."""
    playlist_id = playlist_id_from(link)
    if playlist_id is None:
        raise SpotifyError("Paste a public Spotify, YouTube or YouTube Music playlist link.")
    try:
        status, body = transport("GET", EMBED_URL.format(id=playlist_id), dict(_HEADERS), None)
    except TransportError as error:
        log.warning("Spotify playlist %s: %s", playlist_id, error.__cause__ or error)  # The detail, for the log.
        raise SpotifyError(f"Could not reach Spotify: {error}.") from error
    if status == 404:
        raise SpotifyError("That playlist is private, empty, or doesn't exist.")
    if status != 200:
        log.warning("Spotify playlist %s: HTTP %s", playlist_id, status)
        raise SpotifyError(f"Spotify returned HTTP {status}.")
    return parse_embed_page(body.decode("utf-8", errors="replace"), playlist_id)
