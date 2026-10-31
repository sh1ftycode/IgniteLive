"""Everyone who's been in chat: points and all-time totals, in ``<config>/viewers.sqlite3``.

Local only: never synced, never in profiles. Changes collect in memory and are
written together (``flush``), because likes and comments arrive several times a
second; reads always include what hasn't been written yet.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ignite_live.core.events import ChatUser

COUNTERS = ("points", "minutes", "messages", "coins", "likes")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS viewers (
    key TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    unique_id TEXT NOT NULL,
    name TEXT NOT NULL,
    points INTEGER NOT NULL DEFAULT 0,
    minutes INTEGER NOT NULL DEFAULT 0,
    messages INTEGER NOT NULL DEFAULT 0,
    coins INTEGER NOT NULL DEFAULT 0,
    likes INTEGER NOT NULL DEFAULT 0,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS viewers_name ON viewers (platform, name COLLATE NOCASE);
"""


@dataclass(frozen=True)
class Viewer:
    key: str
    platform: str
    unique_id: str
    name: str
    points: int = 0
    minutes: int = 0
    messages: int = 0
    coins: int = 0
    likes: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0


class ViewerStore:
    def __init__(self, path: Path | str, clock=time.time) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        # Used from the loop thread and (for the Viewers tab) the GUI thread, one at a time.
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()
        self._clock = clock
        self._pending: dict[str, dict] = {}  # key -> {"user": ChatUser, counter deltas…}

    def close(self) -> None:
        self.flush()
        with self._lock:
            self._db.close()

    def add(self, user: ChatUser, **deltas: int) -> None:
        """Count something for a viewer (points can be negative; a balance never goes below 0)."""
        with self._lock:
            entry = self._pending.setdefault(user.key, {"user": user, **dict.fromkeys(COUNTERS, 0)})
            entry["user"] = user  # Latest name.
            for name, value in deltas.items():
                entry[name] += int(value)

    def flush(self) -> None:
        with self._lock:
            if not self._pending:
                return
            now = self._clock()
            with self._db:
                for key, entry in self._pending.items():
                    user = entry["user"]
                    self._db.execute(
                        "INSERT INTO viewers (key, platform, unique_id, name, first_seen, last_seen) "
                        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET name = excluded.name, "
                        "last_seen = excluded.last_seen",
                        (key, user.platform, user.unique_id, (user.nickname or user.unique_id)[:60], now, now),
                    )
                    self._db.execute(
                        "UPDATE viewers SET points = MAX(0, points + ?), minutes = minutes + ?, messages = messages + ?, "
                        "coins = coins + ?, likes = likes + ? WHERE key = ?",
                        (*(entry[name] for name in COUNTERS), key),
                    )
            self._pending.clear()

    def balance(self, key: str) -> int:
        with self._lock:
            row = self._db.execute("SELECT points FROM viewers WHERE key = ?", (key,)).fetchone()
            pending = self._pending.get(key, {}).get("points", 0)
        return max(0, (row[0] if row else 0) + pending)

    def get(self, key: str) -> Viewer | None:
        self.flush()
        with self._lock:
            row = self._db.execute("SELECT * FROM viewers WHERE key = ?", (key,)).fetchone()
        return Viewer(*row) if row else None

    def find(self, platform: str, name: str) -> Viewer | None:
        """By @handle or display name on one platform (for !give @someone)."""
        self.flush()
        name = name.strip().lstrip("@")
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM viewers WHERE platform = ? AND (unique_id = ? COLLATE NOCASE OR name = ? COLLATE NOCASE) "
                "ORDER BY last_seen DESC LIMIT 1", (platform, name, name)).fetchone()
        return Viewer(*row) if row else None

    def top(self, counter: str, limit: int = 10) -> list[Viewer]:
        if counter not in COUNTERS:
            raise ValueError(counter)
        self.flush()
        with self._lock:
            rows = self._db.execute(f"SELECT * FROM viewers WHERE {counter} > 0 ORDER BY {counter} DESC, last_seen DESC "
                                    "LIMIT ?", (limit,)).fetchall()
        return [Viewer(*row) for row in rows]

    def search(self, text: str = "", limit: int = 200) -> list[Viewer]:
        self.flush()
        pattern = f"%{text.strip()}%"
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM viewers WHERE name LIKE ? OR unique_id LIKE ? ORDER BY last_seen DESC LIMIT ?",
                (pattern, pattern, limit)).fetchall()
        return [Viewer(*row) for row in rows]

    def set_points(self, key: str, points: int) -> None:
        self.flush()
        with self._lock, self._db:
            self._db.execute("UPDATE viewers SET points = ? WHERE key = ?", (max(0, int(points)), key))

    def reset_points(self) -> None:
        self.flush()
        with self._lock, self._db:
            self._db.execute("UPDATE viewers SET points = 0")

    def count(self) -> int:
        self.flush()
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM viewers").fetchone()[0]
