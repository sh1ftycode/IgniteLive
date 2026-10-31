"""One background asyncio loop for all network work, bridged to the Qt thread.

Long-lived connections (chat, OBS, the overlay server) live on the loop.
Blocking calls (Streamlabs, backend, keychain) run via ``asyncio.to_thread``.
Results come back to the GUI thread through a queued Qt signal.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any, Awaitable, Callable

from PySide6.QtCore import QObject, Qt, Signal


class Runtime(QObject):
    _invoke = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._invoke.connect(lambda fn: fn(), Qt.ConnectionType.QueuedConnection)
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._serve, name="ignite-async", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def to_ui(self, fn: Callable, *args: Any) -> None:
        """Run ``fn(*args)`` on the GUI thread. Safe to call from any thread."""
        self._invoke.emit(lambda: fn(*args))

    def submit(
        self,
        awaitable: Awaitable,
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> concurrent.futures.Future:
        future = asyncio.run_coroutine_threadsafe(_as_coroutine(awaitable), self.loop)

        def done(result: concurrent.futures.Future) -> None:
            if result.cancelled():
                return
            error = result.exception()
            if error is not None:
                if on_error:
                    self.to_ui(on_error, error)
            elif on_success:
                self.to_ui(on_success, result.result())

        future.add_done_callback(done)
        return future

    def blocking(
        self,
        fn: Callable,
        *args: Any,
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> concurrent.futures.Future:
        return self.submit(asyncio.to_thread(fn, *args), on_success, on_error)

    def shutdown(self, cleanup: Awaitable | None = None, timeout: float = 5.0) -> None:
        if self.loop.is_closed():  # Already shut down (e.g. the window closed first).
            if cleanup is not None and asyncio.iscoroutine(cleanup):
                cleanup.close()
            return
        if cleanup is not None:
            try:
                asyncio.run_coroutine_threadsafe(_as_coroutine(cleanup), self.loop).result(timeout)
            except Exception:
                pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout)
        if not self.loop.is_running():
            self.loop.close()


async def _as_coroutine(awaitable: Awaitable) -> Any:
    return await awaitable
