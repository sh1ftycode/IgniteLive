"""Reconnect delays shared by the chat clients."""

from __future__ import annotations

import random
from typing import Callable

MAX_BACKOFF_SECONDS = 120.0


def backoff_delay(attempt: int, jitter: Callable[[], float] = random.random) -> float:
    """Exponential backoff (2s, 4s, 8s, ...) capped, with up to 25% jitter."""
    base = min(MAX_BACKOFF_SECONDS, 2.0 ** max(1, attempt))
    return base * (1 + 0.25 * jitter())
