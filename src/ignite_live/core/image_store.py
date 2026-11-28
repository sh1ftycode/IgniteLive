"""Profile pictures and gift icons kept on disk, so they show at once on a slow connection and survive a restart.

Files are named by a hash of their address and live in the Ignite folder (``images``). A picture is used without
asking again for a week; after that it is fetched again, and the old copy is only used if that fails. The folder
is kept to a size by deleting the oldest files.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

MAX_AGE_S = 7 * 24 * 3600
MAX_TOTAL_BYTES = 50 * 1024 * 1024
PRUNE_EVERY = 200  # Writes between size checks.


class ImageStore:
    def __init__(self, directory: Path, max_age_s: float = MAX_AGE_S, max_bytes: int = MAX_TOTAL_BYTES,
                 clock=time.time) -> None:
        self.directory = directory
        self.max_age_s = max_age_s
        self.max_bytes = max_bytes
        self._clock = clock
        self._writes = 0

    def path_for(self, url: str) -> Path:
        return self.directory / (hashlib.sha256(url.encode()).hexdigest()[:40] + ".img")

    def fresh(self, url: str) -> bytes | None:
        """The saved picture if it is less than a week old."""
        return self._read(url, require_fresh=True)

    def stale(self, url: str) -> bytes | None:
        """The saved picture however old it is: for when the network can't give a new one."""
        return self._read(url, require_fresh=False)

    def _read(self, url: str, require_fresh: bool) -> bytes | None:
        path = self.path_for(url)
        try:
            if require_fresh and self._clock() - path.stat().st_mtime > self.max_age_s:
                return None
            return path.read_bytes()
        except OSError:
            return None

    def save(self, url: str, data: bytes) -> None:
        """Best effort: a full disk or a locked file just means the picture isn't kept."""
        path = self.path_for(url)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_bytes(data)
            os.replace(temp, path)
        except OSError:
            return
        self._writes += 1
        if self._writes % PRUNE_EVERY == 0:
            self.prune()

    def prune(self) -> None:
        """Delete pictures past twice their life, then the oldest until the folder is under its size."""
        try:
            files = [(f.stat().st_mtime, f.stat().st_size, f) for f in self.directory.glob("*.img")]
        except OSError:
            return
        now = self._clock()
        keep = []
        for mtime, size, file in sorted(files, key=lambda item: item[0]):
            if now - mtime > self.max_age_s * 2:
                self._delete(file)
            else:
                keep.append((mtime, size, file))
        total = sum(size for _m, size, _f in keep)
        for _mtime, size, file in keep:
            if total <= self.max_bytes:
                break
            self._delete(file)
            total -= size

    @staticmethod
    def _delete(file: Path) -> None:
        try:
            file.unlink()
        except OSError:
            pass
