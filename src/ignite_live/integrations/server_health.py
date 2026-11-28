"""Is the Ignite server answering? Checked every so often, so hosted overlay links can fall back to this PC.

The server's ``GET /v1/health`` is the probe. One missed answer isn't enough to call the server down (a request
can be lost on any network); ``FAILURES_TO_DROP`` in a row is. One answer brings it back.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from ignite_live.core.http import USER_AGENT, tls_context

CHECK_SECONDS = 15.0  # While the server is answering.
RETRY_SECONDS = 5.0  # While it isn't: notice quickly when it's back.
TIMEOUT_SECONDS = 4.0
FAILURES_TO_DROP = 2

Probe = Callable[[str, float], bool]


def http_probe(base_url: str, timeout: float = TIMEOUT_SECONDS) -> bool:
    """True when ``<base_url>/v1/health`` answers 200 within ``timeout`` seconds. Blocking: run it on a thread."""
    url = base_url.rstrip("/") + "/v1/health"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    context = tls_context() if url.lower().startswith("https:") else None
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def fetch_config(base_url: str, timeout: float = TIMEOUT_SECONDS) -> dict:
    """The server's public settings (``GET /v1/config``), or {} if it doesn't have them (an older server) or
    doesn't answer. Blocking: run it on a thread."""
    request = urllib.request.Request(base_url.rstrip("/") + "/v1/config", headers={"User-Agent": USER_AGENT})
    context = tls_context() if base_url.lower().startswith("https:") else None
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            data = json.loads(response.read(64 * 1024))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@dataclass
class ServerMonitor:
    """Turns probe results into up/down, reporting only the changes."""

    up: bool = True  # Assumed up until it has failed, so a fresh start doesn't warn.
    failures: int = 0

    def record(self, answered: bool) -> bool | None:
        """Feed one probe result. Returns the new state (True: up, False: down) if it changed, else None."""
        if answered:
            self.failures = 0
            if not self.up:
                self.up = True
                return True
            return None
        self.failures += 1
        if self.up and self.failures >= FAILURES_TO_DROP:
            self.up = False
            return False
        return None

    def reset(self) -> None:
        self.up, self.failures = True, 0

    @property
    def next_check_seconds(self) -> float:
        return CHECK_SECONDS if self.up and not self.failures else RETRY_SECONDS
