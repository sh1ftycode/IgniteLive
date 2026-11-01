"""YouTube lookups for song requests. Metadata only: nothing is downloaded.

"song - artist" requests search YouTube Music's song catalog: official audio
("art tracks", whose picture is just the album cover), not music videos,
covers or lyric uploads. It's the search music.youtube.com itself calls (no
key, no quota); it's undocumented and could change. Each pick is checked with
oEmbed, which refuses songs that can't be embedded.

Links (youtube.com or music.youtube.com) resolve with the keyless oEmbed
endpoint, or with the Data API when a key is set (then duration and
embeddability are known up front).

``radio`` gives songs like one song (YouTube Music's radio for it, the same
genre and vibe), and ``playlist`` reads a public YouTube or YouTube Music
playlist; both come from the same undocumented "up next" list music.youtube.com
shows, so they share its limits (about the first 50 to 200 songs).
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, replace

from ignite_live.core.http import Transport, TransportError, urllib_transport

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")
_CLOCK = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")
_MUSIC_CLIENT = {"clientName": "WEB_REMIX", "clientVersion": "1.20250101.01.00", "hl": "en", "gl": "US"}
_SONGS_ONLY = "EgWKAQIIAWoMEA4QChADEAQQCRAF"  # YouTube Music's "Songs" search filter.
_ART_TRACK = "MUSIC_VIDEO_TYPE_ATV"  # Official audio with the album cover as its picture.
_PODCAST = "MUSIC_VIDEO_TYPE_PODCAST_EPISODE"
_PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{10,64}$")
_ART_SIZE = re.compile(r"=w\d+-h\d+")
_SEARCH_RESULTS = 8
_EMBED_CHECKS = 3


class TrackLookupError(Exception):
    """A safe-to-display lookup error (never includes the API key)."""


@dataclass(frozen=True)
class Track:
    video_id: str
    title: str
    channel: str
    duration_s: int | None = None
    embeddable: bool | None = None
    age_restricted: bool = False
    image_url: str = ""  # Cover art; empty means the video's YouTube thumbnail.
    explicit: bool = False  # Marked explicit by YouTube Music.

    @property
    def cover(self) -> str:
        return self.image_url or f"https://i.ytimg.com/vi/{self.video_id}/mqdefault.jpg"


@dataclass(frozen=True)
class LinkRequest:
    video_id: str


@dataclass(frozen=True)
class SearchRequest:
    song: str
    artist: str = ""

    @property
    def query(self) -> str:
        return f"{self.song} {self.artist}".strip()


def video_id_from(text: str) -> str | None:
    """Extract a video ID from a YouTube URL or a bare 11-character ID."""
    text = text.strip()
    if _VIDEO_ID.match(text):
        return text
    if "://" not in text:
        text = "https://" + text
    try:
        url = urllib.parse.urlsplit(text)
    except ValueError:
        return None
    host = (url.hostname or "").lower()
    if host not in _HOSTS:
        return None
    if host == "youtu.be":
        candidate = url.path.strip("/").split("/")[0]
    elif url.path == "/watch":
        candidate = urllib.parse.parse_qs(url.query).get("v", [""])[0]
    else:
        parts = url.path.strip("/").split("/")
        candidate = parts[1] if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live", "v"} else ""
    return candidate if _VIDEO_ID.match(candidate) else None


def parse_request(text: str) -> LinkRequest | SearchRequest | None:
    """Turn the argument of ``!sr`` into a link or a "song - artist" search."""
    text = " ".join(text.split())[:200]
    if not text:
        return None
    video_id = video_id_from(text)
    if video_id:
        return LinkRequest(video_id)
    # Leading space so " - artist" (no song) still splits and is rejected.
    song, sep, artist = (" " + text).partition(" - ")
    song, artist = song.strip(), artist.strip()
    if not song:
        return None
    return SearchRequest(song, artist if sep else "")


def parse_duration(value: str) -> int | None:
    match = _DURATION.match(value or "")
    if not match:
        return None
    days, hours, minutes, seconds = (int(group or 0) for group in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def parse_clock(value: str) -> int | None:
    """"4:23" or "1:02:03" (search result lengths) in seconds."""
    match = _CLOCK.match((value or "").strip())
    if not match:
        return None
    hours, minutes, seconds = (int(group or 0) for group in match.groups())
    return (hours * 60 + minutes) * 60 + seconds


def _text(value) -> str:
    if not isinstance(value, dict):
        return ""
    if "simpleText" in value:
        return str(value["simpleText"])
    runs = value.get("runs")
    return "".join(str(run.get("text", "")) for run in runs if isinstance(run, dict)) if isinstance(runs, list) else ""


def _renderers(node, key: str):
    """Every ``key`` object (e.g. a search result row) anywhere in a YouTube Music response, in page order."""
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            renderer = item.get(key)
            if isinstance(renderer, dict):
                yield renderer
            stack.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            stack.extend(reversed(item))


def _music_type(endpoint: dict) -> str | None:
    return ((endpoint.get("watchEndpointMusicSupportedConfigs") or {}).get("watchEndpointMusicConfig") or {}).get(
        "musicVideoType")


def _cover(thumbnails) -> str:
    """The album art, at a size that looks sharp on the Now Playing card."""
    last = thumbnails[-1] if isinstance(thumbnails, list) and thumbnails and isinstance(thumbnails[-1], dict) else {}
    url = str(last.get("url", ""))
    return _ART_SIZE.sub("=w400-h400", url) if url.startswith("https://") else ""


def _explicit(renderer: dict) -> bool:
    badges = renderer.get("badges") if isinstance(renderer.get("badges"), list) else []
    return any(((badge.get("musicInlineBadgeRenderer") or {}).get("icon") or {}).get("iconType")
               == "MUSIC_EXPLICIT_BADGE" for badge in badges if isinstance(badge, dict))


def _column(renderer: dict, index: int) -> dict:
    columns = renderer.get("flexColumns")
    if not isinstance(columns, list) or len(columns) <= index or not isinstance(columns[index], dict):
        return {}
    return (columns[index].get("musicResponsiveListItemFlexColumnRenderer") or {}).get("text") or {}


def _watch_endpoint(renderer: dict) -> dict:
    runs = _column(renderer, 0).get("runs")
    run = runs[0] if isinstance(runs, list) and runs and isinstance(runs[0], dict) else {}
    return (run.get("navigationEndpoint") or {}).get("watchEndpoint") or {}


def music_search_results(payload: dict) -> list[Track]:
    """Songs from a YouTube Music search: art tracks with a known length (artist, album art, explicit badge)."""
    tracks = []
    for renderer in _renderers(payload, "musicResponsiveListItemRenderer"):
        endpoint = _watch_endpoint(renderer)
        video_id = str((renderer.get("playlistItemData") or {}).get("videoId") or endpoint.get("videoId") or "")
        details = [part.strip() for part in _text(_column(renderer, 1)).split(" • ")]  # Artist • album • 3:44
        duration = parse_clock(details[-1]) if details else None
        if not _VIDEO_ID.match(video_id) or _music_type(endpoint) != _ART_TRACK or duration is None:
            continue
        thumbs = (((renderer.get("thumbnail") or {}).get("musicThumbnailRenderer") or {}).get("thumbnail") or {}).get(
            "thumbnails")
        tracks.append(Track(video_id=video_id, title=_text(_column(renderer, 0)),
                            channel=details[0] if len(details) > 1 else "", duration_s=duration,
                            image_url=_cover(thumbs), explicit=_explicit(renderer)))
    return tracks


def up_next_results(payload: dict, official_audio_only: bool) -> list[Track]:
    """Songs from YouTube Music's "up next" list (a song's radio, or a playlist). Podcasts and live streams
    are left out; ``official_audio_only`` also leaves out music videos and uploads."""
    tracks = []
    for renderer in _renderers(payload, "playlistPanelVideoRenderer"):
        video_id = str(renderer.get("videoId") or "")
        kind = _music_type((renderer.get("navigationEndpoint") or {}).get("watchEndpoint") or {})
        duration = parse_clock(_text(renderer.get("lengthText")))
        if not _VIDEO_ID.match(video_id) or duration is None or kind == _PODCAST:
            continue
        if official_audio_only and kind != _ART_TRACK:
            continue
        byline = [part.strip() for part in _text(renderer.get("longBylineText")).split(" • ")]  # Artist • album • year
        tracks.append(Track(video_id=video_id, title=_text(renderer.get("title")), channel=byline[0],
                            duration_s=duration, image_url=_cover((renderer.get("thumbnail") or {}).get("thumbnails")),
                            explicit=_explicit(renderer)))
    return tracks


def playlist_id_from(text: str) -> str | None:
    """The playlist in a youtube.com or music.youtube.com link (``list=``), or None."""
    text = text.strip()
    if "://" not in text:
        text = "https://" + text
    try:
        url = urllib.parse.urlsplit(text)
    except ValueError:
        return None
    if (url.hostname or "").lower() not in _HOSTS:
        return None
    candidate = urllib.parse.parse_qs(url.query).get("list", [""])[0]
    return candidate if _PLAYLIST_ID.match(candidate) else None


_EXPLICIT = re.compile(
    r"(?i)[(\[][^)\]]*\b(?:explicit|dirty)\b[^)\]]*[)\]]|\b(?:explicit|dirty) (?:version|edit|lyrics)\b"
)
_CLEAN = re.compile(r"(?i)\b(?:clean(?: version| edit)?|radio edit|censored)\b")
_LENGTH = re.compile(rb'"lengthSeconds"\s*:\s*"(\d+)"')


def is_explicit(track: Track) -> bool:
    """YouTube Music's explicit badge, or (for links) titles marked "(Explicit)", "[Dirty]", "Explicit Version"…"""
    return track.explicit or bool(_EXPLICIT.search(track.title))


def _score(track: Track, request: SearchRequest, prefer_clean: bool = False) -> int:
    """Prefer results whose title or channel mention both song and artist (and clean versions if asked)."""
    haystack = f"{track.title} {track.channel}".lower()
    if prefer_clean and _CLEAN.search(track.title):
        return 10 + _score(track, request)
    score = 2 if request.song.lower() in haystack else 0
    if request.artist and request.artist.lower() in haystack:
        score += 2
    return score


class YouTubeClient:
    api_base = "https://www.googleapis.com/youtube/v3"
    oembed_url = "https://www.youtube.com/oembed"
    music_search_url = "https://music.youtube.com/youtubei/v1/search?prettyPrint=false"
    music_next_url = "https://music.youtube.com/youtubei/v1/next?prettyPrint=false"

    def __init__(self, api_key: str = "", transport: Transport = urllib_transport, prefer_clean: bool = False) -> None:
        """``api_key``: optional, for links. ``prefer_clean``: skip explicit songs, and pick clean versions first."""
        self._api_key = api_key.strip()
        self._transport = transport
        self.prefer_clean = prefer_clean

    @property
    def can_search(self) -> bool:
        return True  # With or without a key.

    def resolve(self, request: LinkRequest | SearchRequest) -> Track:
        if isinstance(request, LinkRequest):
            if self._api_key:
                tracks = self.videos([request.video_id])
                if not tracks:
                    raise TrackLookupError("That video doesn't exist or is private.")
                return tracks[0]
            track = self.oembed(request.video_id)
            return replace(track, duration_s=self.watch_length(request.video_id))
        return self.search(request)

    def watch_length(self, video_id: str) -> int | None:
        """A video's length from its watch page (oEmbed doesn't say). Undocumented: None when it can't be read."""
        try:
            status, body = self._transport("GET", f"https://www.youtube.com/watch?v={video_id}",
                                           {"Accept-Language": "en"}, None)
        except TransportError:
            return None
        found = _LENGTH.search(body) if status == 200 and isinstance(body, bytes) else None
        return int(found.group(1)) if found else None

    def _rank(self, candidates: list[Track], request: SearchRequest) -> list[Track]:
        if self.prefer_clean:
            candidates = [t for t in candidates if not is_explicit(t)]
        # Stable sort keeps YouTube's relevance order among equal scores.
        return sorted(candidates, key=lambda t: -_score(t, request, self.prefer_clean))

    def search(self, request: SearchRequest) -> Track:
        """The best-matching song on YouTube Music that can be embedded."""
        found = self._music_search(request.query)
        if self.prefer_clean and found and found[0].explicit:
            # The clean version is its own song, often further down: ask for it, and look there first.
            seen = {track.video_id for track in found}
            found = [t for t in self._music_search(f"{request.query} clean") if t.video_id not in seen] + found
        for track in self._rank(found, request)[:_EMBED_CHECKS]:
            try:
                self.oembed(track.video_id)
            except TrackLookupError:
                continue  # Can't be embedded (or vanished): try the next one.
            return replace(track, embeddable=True)
        raise TrackLookupError(f'No song on YouTube Music for "{request.query}".')

    def radio(self, video_id: str) -> list[Track]:
        """Songs like this one, from YouTube Music's radio for it: official audio, without the song itself."""
        payload = self._up_next({"videoId": video_id, "playlistId": f"RDAMVM{video_id}"})
        tracks = [t for t in up_next_results(payload, official_audio_only=True) if t.video_id != video_id]
        return [t for t in tracks if not is_explicit(t)] if self.prefer_clean else tracks

    def playlist(self, playlist_id: str) -> tuple[str, list[Track]]:
        """A public YouTube or YouTube Music playlist: its name and songs (as they are: uploads included)."""
        tracks = up_next_results(self._up_next({"playlistId": playlist_id}), official_audio_only=False)
        if not tracks:
            raise TrackLookupError("That playlist is private, empty, or has no songs Ignite can play.")
        try:
            name = self._oembed_title(f"https://www.youtube.com/playlist?list={playlist_id}")
        except TrackLookupError:
            name = ""
        return name or "YouTube playlist", tracks

    def _up_next(self, body: dict) -> dict:
        payload = json.dumps({"context": {"client": _MUSIC_CLIENT}, "isAudioOnly": True, **body})
        try:
            status, raw = self._transport("POST", self.music_next_url, {
                "Content-Type": "application/json", "Origin": "https://music.youtube.com"}, payload.encode())
        except TransportError as error:
            raise TrackLookupError("Could not reach YouTube Music.") from error
        return self._decode(status, raw)

    def _oembed_title(self, url: str) -> str:
        status, body = self._send(f"{self.oembed_url}?{urllib.parse.urlencode({'url': url, 'format': 'json'})}")
        return str(self._decode(status, body).get("title", ""))

    def _music_search(self, query: str) -> list[Track]:
        body = json.dumps({"context": {"client": _MUSIC_CLIENT}, "query": query, "params": _SONGS_ONLY})
        try:
            status, raw = self._transport("POST", self.music_search_url, {
                "Content-Type": "application/json", "Origin": "https://music.youtube.com"}, body.encode())
        except TransportError as error:
            raise TrackLookupError("Could not reach YouTube Music.") from error
        return music_search_results(self._decode(status, raw))[:_SEARCH_RESULTS]

    def videos(self, video_ids: list[str]) -> list[Track]:
        if not video_ids:
            return []
        payload = self._get(
            f"{self.api_base}/videos",
            {"part": "snippet,contentDetails,status", "id": ",".join(video_ids[:50])},
        )
        by_id = {}
        for item in payload.get("items", []):
            snippet = item.get("snippet") or {}
            details = item.get("contentDetails") or {}
            status = item.get("status") or {}
            rating = details.get("contentRating") or {}
            by_id[item.get("id")] = Track(
                video_id=str(item.get("id")),
                title=str(snippet.get("title", "")),
                channel=str(snippet.get("channelTitle", "")),
                duration_s=parse_duration(details.get("duration", "")),
                embeddable=status.get("embeddable"),
                age_restricted=rating.get("ytRating") == "ytAgeRestricted",
            )
        return [by_id[i] for i in video_ids if i in by_id]

    def oembed(self, video_id: str) -> Track:
        query = urllib.parse.urlencode(
            {"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"}
        )
        status, body = self._send(f"{self.oembed_url}?{query}")
        if status in (401, 403):
            raise TrackLookupError("That video can't be embedded.")
        if status == 404 or status == 400:
            raise TrackLookupError("That video doesn't exist or is private.")
        payload = self._decode(status, body)
        return Track(
            video_id=video_id,
            title=str(payload.get("title", "")),
            channel=str(payload.get("author_name", "")),
        )

    def _get(self, url: str, params: dict[str, str]) -> dict:
        status, body = self._send(f"{url}?{urllib.parse.urlencode({**params, 'key': self._api_key})}")
        if status == 403:
            raise TrackLookupError("YouTube refused the request (quota used up or key not allowed).")
        if status == 400:
            raise TrackLookupError("YouTube rejected the request. Check the API key.")
        return self._decode(status, body)

    def _send(self, url: str) -> tuple[int, bytes]:
        try:
            return self._transport("GET", url, {"Accept": "application/json"}, None)
        except TransportError as error:
            raise TrackLookupError("Could not reach YouTube.") from error

    @staticmethod
    def _decode(status: int, body: bytes) -> dict:
        if status != 200:
            raise TrackLookupError(f"YouTube returned HTTP {status}.")
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise TrackLookupError("YouTube returned an unreadable response.") from error
        if not isinstance(payload, dict):
            raise TrackLookupError("YouTube returned an unexpected response.")
        return payload
