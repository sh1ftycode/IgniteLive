"""Post-stream analytics: what happened each minute of a stream, and the report built from it. Pure logic, no Qt.

``StreamRecorder`` counts on the loop thread while OBS streams. It keeps per-minute
totals, not raw chat, so a 3-hour stream is about 15 KB. Files live in
``<config>/streams`` and never leave the computer.
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Callable

from ignite_live.core.events import ChatComment, ChatEvent, ChatFollow, ChatGift, ChatLike, ChatShare, ViewerCount

VERSION = 1
BIG_GIFT_COINS = 500  # Gifts worth this much become moments on the timeline.
TOP = 20
COUNTED = ("comments", "likes", "gifts", "coins", "follows", "shares")
# A drop worth reporting: at least this many viewers and this share of them, within DROP_WINDOW minutes.
DROP_MIN_VIEWERS = 3
DROP_MIN_SHARE = 0.15
DROP_WINDOW = 3


def _name(event) -> str:
    return (event.user.nickname or event.user.unique_id)[:40]


class StreamRecorder:
    def __init__(self, started: float, title: str = "", clock: Callable[[], float] = time.monotonic) -> None:
        """``started`` is wall-clock time (for the file name and report); ``clock`` measures the stream."""
        self.started = started
        self.title = title
        self.ended: float | None = None
        self._clock = clock
        self._start = clock()
        self.minutes: list[dict] = []
        self.songs: list[dict] = []
        self.moments: list[dict] = []
        self.gifters: Counter = Counter()
        self.chatters: Counter = Counter()
        self.peak = (0, 0.0)  # (viewers, minute)
        self._viewers: dict[str, int] = {}  # Latest count per platform.
        self._song: dict | None = None
        self.games: list[dict] = []
        self._game: dict | None = None

    def _t(self) -> float:
        return round((self._clock() - self._start) / 60, 2)

    def _minute(self) -> dict:
        index = int(self._t())
        while len(self.minutes) <= index:
            previous = self.minutes[-1]["viewers"] if self.minutes else 0
            self.minutes.append({"t": len(self.minutes), "viewers": previous, **dict.fromkeys(COUNTED, 0),
                                 "song": self._song["title"] if self._song else ""})
        return self.minutes[index]

    def feed(self, event: ChatEvent) -> None:
        if isinstance(event, ViewerCount):
            self._viewers[event.platform] = event.count
            total = sum(self._viewers.values())
            self._minute()["viewers"] = total
            if total > self.peak[0]:
                self.peak = (total, self._t())
            return
        if isinstance(event, ChatComment):
            key, amount = "comments", 1
            self.chatters[_name(event)] += 1
        elif isinstance(event, ChatGift):
            coins = event.diamonds * event.count
            self._add("gifts", event.count)
            key, amount = "coins", coins
            if coins:
                self.gifters[_name(event)] += coins
            if coins >= BIG_GIFT_COINS:
                self.moment("gift", f"{_name(event)} sent {event.count}× {event.gift_name} ({coins:,} coins)")
        elif isinstance(event, ChatLike):
            key, amount = "likes", event.count
        elif isinstance(event, ChatFollow):
            key, amount = "follows", 1
        elif isinstance(event, ChatShare):
            key, amount = "shares", 1
        else:
            return
        self._add(key, amount)

    def _add(self, key: str, amount: int) -> None:
        self._minute()[key] += amount
        for window in (self._song, self._game):
            if window is not None and key in ("comments", "likes", "coins"):
                window[key] += amount

    def song(self, title: str, requested_by: str = "") -> None:
        """The song now playing (empty: nothing)."""
        if self._song is not None and title == self._song["title"] and self._song["end"] is None:
            return
        t = self._t()
        if self._song is not None:
            self._song["end"] = t
        self._song = None
        if title:
            self._song = {"title": title[:120], "requested_by": requested_by[:40], "start": t, "end": None,
                          "comments": 0, "likes": 0, "coins": 0}
            self.songs.append(self._song)
        self._minute()["song"] = title[:120]

    def game(self, label: str) -> None:
        """The game now running (empty: none), for how much chat it got."""
        t = self._t()
        if self._game is not None:
            self._game["end"] = t
        self._game = None
        if label:
            self._game = {"title": label[:60], "start": t, "end": None, "comments": 0, "likes": 0, "coins": 0}
            self.games.append(self._game)

    def moment(self, kind: str, text: str, path: str = "") -> None:
        self.moments.append({"t": self._t(), "kind": kind, "text": text[:200], "path": path})

    def clip_saved(self, path: str) -> None:
        """OBS reported a saved clip: attach it to the clip moment waiting for one, or add one."""
        for moment in reversed(self.moments):
            if moment["kind"] == "clip" and not moment["path"]:
                moment["path"] = path
                return
        self.moment("clip", "Clip saved", path)

    def finish(self, ended: float) -> None:
        self.song("")
        self.game("")
        self.ended = ended

    def to_dict(self) -> dict:
        return {
            "version": VERSION,
            "started": self.started,
            "ended": self.ended,
            "title": self.title,
            "minutes": self.minutes,
            "songs": self.songs,
            "games": self.games,
            "moments": self.moments,
            "peak": {"viewers": self.peak[0], "t": self.peak[1]},
            "top_gifters": [[name, amount] for name, amount in self.gifters.most_common(TOP)],
            "top_chatters": [[name, amount] for name, amount in self.chatters.most_common(TOP)],
        }


# Storage.

def file_name(started: float) -> str:
    return time.strftime("%Y-%m-%d %H-%M-%S", time.localtime(started)) + ".json"


def save(data: dict, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / file_name(data["started"])
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, separators=(",", ":")), "utf-8")
    os.replace(temp, path)
    return path


def load(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("minutes"), list):
        return None
    if not isinstance(data.get("started"), (int, float)):
        return None
    return data


def saved_streams(folder: Path) -> list[Path]:
    """Newest first."""
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.json"), reverse=True)


# The report.

def _minutes(data: dict) -> list[dict]:
    return [m for m in data.get("minutes", []) if isinstance(m, dict)]


def number(entry: dict, key: str) -> float:
    value = entry.get(key, 0)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def duration_minutes(data: dict) -> float:
    ended, started = data.get("ended"), data.get("started", 0)
    if isinstance(ended, (int, float)) and ended > started:
        return (ended - started) / 60
    return float(len(_minutes(data)))


def summary(data: dict) -> dict:
    minutes = _minutes(data)
    totals = {key: int(sum(number(m, key) for m in minutes)) for key in COUNTED}
    watched = [number(m, "viewers") for m in minutes if number(m, "viewers") > 0]
    peak = data.get("peak") if isinstance(data.get("peak"), dict) else {}
    return {
        "duration_min": duration_minutes(data),
        "peak_viewers": int(number(peak, "viewers")),
        "peak_at": number(peak, "t"),
        "average_viewers": round(sum(watched) / len(watched)) if watched else 0,
        **totals,
    }


def drops(data: dict, limit: int = 3) -> list[dict]:
    """The biggest moments viewers left, with what was going on."""
    minutes = _minutes(data)
    viewers = [number(m, "viewers") for m in minutes]
    found = []
    for i, before in enumerate(viewers):
        best = None
        for j in range(i + 1, min(len(viewers), i + DROP_WINDOW + 1)):
            lost = before - viewers[j]
            if lost >= DROP_MIN_VIEWERS and lost >= before * DROP_MIN_SHARE and (best is None or lost > best[1]):
                best = (j, lost)
        if best:
            found.append({"from": i, "to": best[0], "before": int(before), "lost": int(best[1])})
    chosen: list[dict] = []
    for drop in sorted(found, key=lambda d: (-d["lost"], d["to"] - d["from"])):  # Ties: the tighter window.
        if all(drop["to"] < c["from"] or drop["from"] > c["to"] for c in chosen):
            chosen.append(drop)
        if len(chosen) == limit:
            break
    moments = [m for m in data.get("moments", []) if isinstance(m, dict)]
    for drop in chosen:
        drop["song"] = str(minutes[drop["to"]].get("song", "") or minutes[drop["from"]].get("song", ""))
        drop["moments"] = [str(m.get("text", "")) for m in moments
                           if drop["from"] <= number(m, "t") < drop["to"] + 1][:3]
    return sorted(chosen, key=lambda d: d["from"])


def song_stats(data: dict) -> list[dict]:
    """Each song's chat, likes and coins per minute, and how that compares with the stream (1.0 = usual)."""
    return _windows(data, "songs")


def game_stats(data: dict) -> list[dict]:
    """The same for each game that ran."""
    return _windows(data, "games")


def _windows(data: dict, key: str) -> list[dict]:
    total = summary(data)
    length = max(duration_minutes(data), 1.0)
    usual = {key: total[key] / length for key in ("comments", "likes", "coins")}
    end_of_stream = duration_minutes(data)
    stats = []
    for song in data.get(key, []):
        if not isinstance(song, dict):
            continue
        start = number(song, "start")
        end = song.get("end") if isinstance(song.get("end"), (int, float)) else end_of_stream
        played = max(end - start, 0.0)
        if played < 0.5:  # Skipped straight away: too short to say anything.
            continue
        rates = {key: number(song, key) / played for key in ("comments", "likes", "coins")}
        ratios = [rates[key] / usual[key] for key in ("comments", "likes") if usual[key] > 0]
        stats.append({"title": str(song.get("title", "")), "requested_by": str(song.get("requested_by", "") or ""),
                      "start": start, "minutes": round(played, 1), **{f"{k}_per_min": round(v, 1) for k, v in rates.items()},
                      "engagement": round(sum(ratios) / len(ratios), 2) if ratios else None})
    return stats


def clips(data: dict) -> list[dict]:
    return [m for m in data.get("moments", []) if isinstance(m, dict) and m.get("kind") == "clip"]


def _cell(text: str) -> str:
    """Song titles come from YouTube: never let one run as a spreadsheet formula."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def to_csv(data: dict) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["minute", "viewers", *COUNTED, "song"])
    for m in _minutes(data):
        writer.writerow([int(number(m, "t")), int(number(m, "viewers")),
                         *(int(number(m, key)) for key in COUNTED), _cell(str(m.get("song", "")))])
    return out.getvalue()


def clock(minutes: float) -> str:
    """Minutes into the stream as h:mm."""
    whole = int(minutes)
    return f"{whole // 60}:{whole % 60:02d}"
