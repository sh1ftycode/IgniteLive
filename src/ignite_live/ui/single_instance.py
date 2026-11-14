"""One Ignite at a time.

Clicking the taskbar or Start menu entry again, or a desktop shortcut, used to start a second copy: two
sets of chat connections, two overlay servers fighting for the same port, and two windows. Now the first
copy listens on a local socket (a named pipe on Windows, a socket file on Linux and macOS) only this user
can open. A second copy finds it, asks it to bring its window forward, and quits before showing anything.
"""

from __future__ import annotations

import getpass
import hashlib
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SHOW = b"show\n"
CONNECT_MS = 500


def server_name(settings_path: Path) -> str:
    """Per user and per settings file: a portable copy with its own settings can still run beside the
    installed one."""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - no user name (unusual): the settings path still tells copies apart.
        user = ""
    digest = hashlib.sha256(f"{user}\0{settings_path.resolve()}".encode()).hexdigest()[:16]
    return f"ignite-{digest}"


class SingleInstance(QObject):
    """``already_running()`` first; if not, ``listen()``, and ``activated`` fires on each later launch."""

    activated = Signal()

    def __init__(self, name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.name = name
        self._server: QLocalServer | None = None

    def already_running(self) -> bool:
        """Whether another copy answers; if so it has been asked to show its window."""
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if not socket.waitForConnected(CONNECT_MS):
            return False
        socket.write(SHOW)
        socket.flush()
        socket.waitForBytesWritten(CONNECT_MS)
        socket.disconnectFromServer()
        return True

    def listen(self) -> bool:
        """Start answering later launches. False if that's impossible (Ignite then runs as before)."""
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self.name):
            # A socket file left by a copy that crashed: nobody answered on it, so it's safe to remove.
            QLocalServer.removeServer(self.name)
            if not server.listen(self.name):
                return False
        server.newConnection.connect(self._connection)
        self._server = server
        return True

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None

    def _connection(self) -> None:
        while self._server is not None and self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                self._read(socket)

    def _read(self, socket: QLocalSocket) -> None:
        if SHOW.strip() in bytes(socket.readAll()):
            self.activated.emit()
