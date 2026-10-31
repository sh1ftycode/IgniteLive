"""Minimal blocking HTTP transport shared by the provider adapters.

Adapters take a ``Transport`` so tests can replace the network entirely.
Run blocking calls on a worker thread (Qt) or ``asyncio.to_thread`` (asyncio).
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Callable

Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


class TransportError(Exception):
    """The request never produced an HTTP response (DNS, TLS, timeout, ...)."""


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise TransportError(str(getattr(error, "reason", error))) from error
