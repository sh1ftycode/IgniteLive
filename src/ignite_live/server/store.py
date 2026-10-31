"""SQLite storage for the Ignite backend.

Stream keys are encrypted at rest with Fernet (AES-128-CBC + HMAC). Device
tokens are stored as SHA-256 digests; they are 256-bit random values, so a
slow password hash adds nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from cryptography.fernet import Fernet, InvalidToken

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stream_keys (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    secret BLOB NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    used_at REAL
);
CREATE TABLE IF NOT EXISTS settings (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS search_cache (
    query TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


@dataclass(frozen=True)
class StoredKey:
    server: str
    key: str
    session_id: str | None
    created_at: float
    expires_at: float


def load_or_create_secret(data_dir: Path, from_env: str = "") -> bytes:
    """Encryption key from ``from_env`` ($IGNITE_SECRET), else a 0600 file in data_dir."""
    if from_env:
        return from_env.encode()
    path = data_dir / "secret.key"
    if path.exists():
        return path.read_bytes().strip()
    data_dir.mkdir(parents=True, exist_ok=True)
    secret = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(secret)
    return secret


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, path: str | Path, secret: bytes, clock: Callable[[], float] = time.time) -> None:
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)
        self._fernet = Fernet(secret)
        self._clock = clock

    def close(self) -> None:
        self._db.close()

    # Users and device tokens.

    def add_user(self, name: str) -> str:
        """Create a user and return their device token (shown once)."""
        token = "st_" + secrets.token_urlsafe(32)
        with self._db:
            self._db.execute(
                "INSERT INTO users (name, token_hash, created_at) VALUES (?, ?, ?)",
                (name, _digest(token), self._clock()),
            )
        return token

    def rotate_token(self, name: str) -> str:
        token = "st_" + secrets.token_urlsafe(32)
        with self._db:
            changed = self._db.execute(
                "UPDATE users SET token_hash = ? WHERE name = ?", (_digest(token), name)
            ).rowcount
        if not changed:
            raise KeyError(name)
        return token

    def remove_user(self, name: str) -> bool:
        with self._db:
            return self._db.execute("DELETE FROM users WHERE name = ?", (name,)).rowcount > 0

    def users(self) -> list[str]:
        return [row[0] for row in self._db.execute("SELECT name FROM users ORDER BY name")]

    def user_for_token(self, token: str) -> int | None:
        row = self._db.execute(
            "SELECT id FROM users WHERE token_hash = ?", (_digest(token),)
        ).fetchone()
        return row[0] if row else None

    # Stream keys.

    def put_key(self, user_id: int, server: str, key: str, session_id: str | None, ttl_s: float) -> StoredKey:
        now = self._clock()
        secret = self._fernet.encrypt(
            json.dumps({"server": server, "key": key, "session_id": session_id}).encode()
        )
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO stream_keys (user_id, secret, created_at, expires_at, used_at)"
                " VALUES (?, ?, ?, ?, NULL)",
                (user_id, secret, now, now + ttl_s),
            )
        return StoredKey(server, key, session_id, now, now + ttl_s)

    def get_key(self, user_id: int) -> StoredKey | None:
        """The active key, or None once it has expired or been used."""
        row = self._db.execute(
            "SELECT secret, created_at, expires_at, used_at FROM stream_keys WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        secret, created_at, expires_at, used_at = row
        if used_at is not None or expires_at <= self._clock():
            self.delete_key(user_id)
            return None
        try:
            data = json.loads(self._fernet.decrypt(secret))
        except InvalidToken:
            # Encryption key changed: the stored key is unreadable, so drop it.
            self.delete_key(user_id)
            return None
        return StoredKey(data["server"], data["key"], data.get("session_id"), created_at, expires_at)

    def mark_key_used(self, user_id: int) -> bool:
        with self._db:
            return self._db.execute(
                "UPDATE stream_keys SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
                (self._clock(), user_id),
            ).rowcount > 0

    def delete_key(self, user_id: int) -> None:
        with self._db:
            self._db.execute("DELETE FROM stream_keys WHERE user_id = ?", (user_id,))

    def purge_expired(self) -> int:
        with self._db:
            return self._db.execute(
                "DELETE FROM stream_keys WHERE expires_at <= ? OR used_at IS NOT NULL",
                (self._clock(),),
            ).rowcount

    # Settings sync (non-secret preferences only).

    def get_settings(self, user_id: int) -> tuple[dict, float] | None:
        row = self._db.execute(
            "SELECT body, updated_at FROM settings WHERE user_id = ?", (user_id,)
        ).fetchone()
        return (json.loads(row[0]), row[1]) if row else None

    def put_settings(self, user_id: int, body: dict) -> float:
        now = self._clock()
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO settings (user_id, body, updated_at) VALUES (?, ?, ?)",
                (user_id, json.dumps(body), now),
            )
        return now

    # Song search cache, shared by all users to save YouTube quota.

    def cached_search(self, query: str, max_age_s: float) -> dict | None:
        row = self._db.execute(
            "SELECT body, created_at FROM search_cache WHERE query = ?", (query,)
        ).fetchone()
        if not row or row[1] <= self._clock() - max_age_s:
            return None
        return json.loads(row[0])

    def cache_search(self, query: str, body: dict) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO search_cache (query, body, created_at) VALUES (?, ?, ?)",
                (query, json.dumps(body), self._clock()),
            )
