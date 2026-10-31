"""YouTube lookups for song requests. Metadata only: nothing is downloaded.

Links resolve with the keyless oEmbed endpoint, or with the Data API when a key
is set (then duration and embeddability are known up front).

"song - artist" requests use the Data API when a key is set (``search.list``
costs 100 of the default 10,000 daily quota units). Without a key they use the
search that youtube.com itself calls (no key, no quota). It's undocumented and
could change; each pick is checked with oEmbed, which refuses videos that
can't be embedded.
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
_WEB_CLIENT = {"clientName": "WEB", "clientVersion": "2.20250101.00.00", "hl": "en", "gl": "US"}
_VIDEOS_ONLY = "EgIQAQ%3D%3D"  # The "Type: Video" search filter.
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


def _video_renderers(node):
    """Every ``videoRenderer`` in a youtube.com search response, in page order."""
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            renderer = item.get("videoRenderer")
            if isinstance(renderer, dict):
                yield renderer
            stack.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            stack.extend(reversed(item))


def search_results(payload: dict) -> list[Track]:
    """Tracks from a keyless search response. Live streams (no length) are left out."""
    tracks = []
    for renderer in _video_renderers(payload):
        video_id = str(renderer.get("videoId", ""))
        duration = parse_clock(_text(renderer.get("lengthText")))
        if not _VIDEO_ID.match(video_id) or duration is None:
            continue
        tracks.append(Track(
            video_id=video_id,
            title=_text(renderer.get("title")),
            channel=_text(renderer.get("ownerText")) or _text(renderer.get("longBylineText")),
            duration_s=duration,
        ))
    return tracks


_EXPLICIT = re.compile(
    r"(?i)[(\[][^)\]]*\b(?:explicit|dirty)\b[^)\]]*[)\]]|\b(?:explicit|dirty) (?:version|edit|lyrics)\b"
)
_CLEAN = re.compile(r"(?i)\b(?:clean(?: version| edit)?|radio edit|censored)\b")
_LENGTH = re.compile(rb'"lengthSeconds"\s*:\s*"(\d+)"')


def is_explicit(track: Track) -> bool:
    """YouTube has no explicit flag: titles marked "(Explicit)", "[Dirty]", "Explicit Version" and the like."""
    return bool(_EXPLICIT.search(track.title))


def _score(track: Track, request: SearchRequest, prefer_clean: bool = False) -> int:
    """Prefer results whose title or channel mention both song and artist (and clean versions if asked)."""
    haystack = f"{track.title} {track.channel}".lower()
    if prefer_clean and _CLEAN.search(track.title):
        return 10 + _score(track, request)
    score = 2 if request.song.lower() in haystack else 0
    if request.artist and request.artist.lower() in haystack:
        score += 2
    if "official" in haystack or track.channel.lower().endswith("- topic"):
        score += 1
    return score


class YouTubeClient:
    api_base = "https://www.googleapis.com/youtube/v3"
    oembed_url = "https://www.youtube.com/oembed"
    keyless_search_url = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"

    def __init__(self, api_key: str = "", transport: Transport = urllib_transport, prefer_clean: bool = False) -> None:
        """``prefer_clean``: skip results marked explicit, and pick "clean" / "radio edit" versions first."""
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
        if not self._api_key:
            return self._search_keyless(request)
        payload = self._get(
            f"{self.api_base}/search",
            {
                "part": "snippet",
                "type": "video",
                "videoEmbeddable": "true",
                "videoCategoryId": "10",  # Music
                "maxResults": "5",
                "q": request.query,
            },
        )
        ids = [
            item["id"]["videoId"]
            for item in payload.get("items", [])
            if isinstance(item, dict) and isinstance(item.get("id"), dict) and item["id"].get("videoId")
        ]
        candidates = self._rank([t for t in self.videos(ids) if t.embeddable is not False and not t.age_restricted],
                                request)
        if not candidates:
            raise TrackLookupError(f'No playable result for "{request.query}".')
        return candidates[0]

    def _search_keyless(self, request: SearchRequest) -> Track:
        body = json.dumps({"context": {"client": _WEB_CLIENT}, "query": request.query, "params": _VIDEOS_ONLY})
        try:
            status, raw = self._transport(
                "POST", self.keyless_search_url, {"Content-Type": "application/json"}, body.encode()
            )
        except TransportError as error:
            raise TrackLookupError("Could not reach YouTube.") from error
        candidates = search_results(self._decode(status, raw))[:5]
        ranked = self._rank(candidates, request)
        for track in ranked[:_EMBED_CHECKS]:
            try:
                details = self.oembed(track.video_id)
            except TrackLookupError:
                continue  # Can't be embedded (or vanished): try the next one.
            return Track(track.video_id, track.title or details.title, track.channel or details.channel,
                         track.duration_s, embeddable=True)
        raise TrackLookupError(f'No playable result for "{request.query}".')

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
