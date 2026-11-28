"""Ignite's own music player: the Now Playing page running in a browser inside Ignite, so the song plays on this PC
instead of inside an OBS Browser Source. The overlay in OBS then only shows the cover, title and progress.

The player stays visible (YouTube requires that of its embedded player, at least 200 by 200): it sits on the
Song Requests tab. Songs are YouTube Music's art tracks, so what it shows is the album cover.
"""

from __future__ import annotations

from PySide6.QtCore import QUrl, Qt
from PySide6.QtWidgets import QFrame, QVBoxLayout

from ignite_live.controller import Controller
from ignite_live.core.events import StatusChange
from ignite_live.ui.kit import label

def web_view_class():
    """Qt's embedded browser, or None where it isn't installed."""
    from ignite_live.ui.pages.overlays import web_view_class as find  # Not at the top: that page imports this one's.

    return find()


PLAYER_QUERY = "&host=app"  # Tells the overlay server this copy is Ignite's own player: it plays, OBS copies show.
MIN_SIZE = (640, 260)  # Comfortably above YouTube's 200 x 200 minimum.


class AppPlayer(QFrame):
    """The in-app player. ``set_enabled(True)`` loads it; ``False`` unloads it, so the Now Playing overlay in OBS plays
    again. Without Qt's browser it can't load, and says so."""

    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        self.view = None
        self.enabled = False
        self._loaded = ""  # The address showing, so a repeat status doesn't reload (and cut off) the song.
        self.setObjectName("inset")
        self.setMinimumSize(*MIN_SIZE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.hint = label("", "muted", wrap=True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hint)
        controller.status_changed.connect(self._status)

    def url(self) -> str:
        return self.controller.backup_link("nowplaying") + PLAYER_QUERY  # This PC's own overlay server, always.

    @staticmethod
    def available() -> bool:
        return web_view_class() is not None

    def set_enabled(self, on: bool) -> bool:
        """Start or stop playing here. Returns whether the player is running."""
        self.enabled = on
        if not on:
            self._unload()
            self.hint.setText("Off. The Now Playing overlay in OBS plays the music.")
            return False
        cls = web_view_class()
        if cls is None:
            self.hint.setText("This copy of Ignite has no built-in browser, so it can't play music itself. The Now "
                              "Playing overlay in OBS plays it instead.")
            return False
        if self.view is None:
            self.view = cls()
            try:  # Songs start by themselves, as they do in OBS.
                from PySide6.QtWebEngineCore import QWebEngineSettings

                self.view.page().settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture,
                                                         False)
            except ImportError:
                pass
            self.layout().addWidget(self.view)
        self.hint.hide()
        self.view.show()
        self._load()
        return True

    def _load(self) -> None:
        url = self.url()
        if url != self._loaded:
            self._loaded = url
            self.view.setUrl(QUrl(url))

    def _unload(self) -> None:
        if self.view is not None:
            self._loaded = ""
            self.view.setUrl(QUrl("about:blank"))  # Stops the song and lets go of the overlay's connection.
            self.view.hide()
        self.hint.show()

    def _status(self, change: StatusChange) -> None:
        if change.source == "overlay" and self.enabled and self.view is not None:
            self._load()  # The overlay server (re)started on another address: connect again.
