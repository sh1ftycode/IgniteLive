"""Minimal blocking HTTP transport shared by the provider adapters.

Adapters take a ``Transport`` so tests can replace the network entirely.
Run blocking calls on a worker thread (Qt) or ``asyncio.to_thread`` (asyncio).
"""

from __future__ import annotations

import socket
import ssl
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    import aiohttp

Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]

_tls: ssl.SSLContext | None = None


class TransportError(Exception):
    """The request never produced an HTTP response (DNS, TLS, timeout, ...). Its text is a short, safe reason."""


def tls_context() -> ssl.SSLContext:
    """Trust the system's certificates plus Mozilla's (certifi).

    Some setups have an incomplete system store: the Windows build under Wine, a fresh Windows that
    only fetches root certificates when a browser asks, or a packaged Linux build whose OpenSSL looks
    in another distribution's folder. There, HTTPS fails before reaching the site. The system's own
    certificates stay trusted too, so a company proxy or antivirus that inspects HTTPS keeps working.
    """
    global _tls
    if _tls is None:
        context = ssl.create_default_context()
        try:
            import certifi

            context.load_verify_locations(certifi.where())
        except (ImportError, OSError, ssl.SSLError):
            pass  # The system store alone, as before.
        _tls = context
    return _tls


def tls_session(**kwargs: Any) -> aiohttp.ClientSession:
    """An aiohttp session that trusts what ``tls_context()`` trusts. Create it inside a running event loop.

    aiohttp's own default trusts only the system store, so under Wine every chat connection failed
    with CERTIFICATE_VERIFY_FAILED while TikTokLive's httpx requests (which bundle certifi) went through.
    """
    import aiohttp

    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=tls_context()), **kwargs)


def reason(error: BaseException) -> str:
    """Why a request failed, in a few plain words."""
    cause = getattr(error, "reason", error)
    if isinstance(cause, ssl.SSLCertVerificationError):
        return "its security certificate couldn't be checked"
    if isinstance(cause, ssl.SSLError):
        return "the secure connection failed"
    if isinstance(cause, socket.gaierror):
        return "its address couldn't be looked up; check your internet connection"
    if isinstance(cause, (TimeoutError, socket.timeout)):
        return "it took too long to answer"
    if isinstance(cause, ConnectionRefusedError):
        return "the connection was refused"
    if isinstance(cause, ConnectionError):
        return "the connection was dropped"
    return str(cause) or type(cause).__name__


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    context = tls_context() if url.lower().startswith("https:") else None
    try:
        with urllib.request.urlopen(request, timeout=20, context=context) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise TransportError(reason(error)) from error
