"""Music tab → Connect YouTube Music: paste the cookie of a signed-in music.youtube.com tab.

YouTube Music offers other apps no sign-in, so this is the one way in (see
music/ytmusic_account.py). The dialog says plainly what the cookie is and where it goes.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import Controller
from ignite_live.music.ytmusic_account import AccountError, clean_cookie
from ignite_live.ui.pages.stream import note
from ignite_live.ui.theme import colors

STEPS = (
    "<ol style='margin-left: -20px;'>"
    "<li>Open <b>music.youtube.com</b> in your browser and sign in.</li>"
    "<li>Press <b>F12</b>, open the <b>Network</b> tab, and type <b>browse</b> in its filter box.</li>"
    "<li>Click <b>Library</b> on the page, then click one of the <b>browse</b> requests that appear.</li>"
    "<li>Under <b>Request Headers</b>, find <b>cookie</b>, right-click its value and copy it.</li>"
    "<li>Paste it below and press Connect.</li>"
    "</ol>"
)


class ConnectYouTubeMusic(QDialog):
    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._waiting = False  # Connect was pressed and the answer isn't in yet.
        self.setWindowTitle("Connect YouTube Music")
        layout = QVBoxLayout(self)
        intro = QLabel("Use your own YouTube Music playlists and Liked Music as background music. YouTube Music "
                       "has no sign-in for other apps, so Ignite uses your browser's session instead:")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        steps = QLabel(STEPS)
        steps.setWordWrap(True)
        layout.addWidget(steps)
        self.cookie = QPlainTextEdit()
        self.cookie.setPlaceholderText("Paste the cookie header's value here")
        self.cookie.setTabChangesFocus(True)
        self.cookie.setFixedHeight(96)
        layout.addWidget(self.cookie)
        self.problem = QLabel("")
        self.problem.setWordWrap(True)
        self.problem.hide()
        layout.addWidget(self.problem)
        layout.addWidget(note(
            "Treat this cookie like a password: it lets whoever has it use YouTube as you. Ignite keeps it in this "
            "PC's keychain (never in settings files, profiles, logs or on your Ignite server), sends it only to "
            "music.youtube.com, and only reads your playlists. Disconnect removes it. It stops working when you "
            "sign out in that browser. This isn't an official YouTube feature and may break when YouTube changes "
            "its site."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.connect_button = buttons.addButton("Connect", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self._connect)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        controller.busy_changed.connect(self._busy)
        controller.ytmusic_changed.connect(self._changed)
        self.resize(520, 0)

    def _connect(self) -> None:
        try:
            clean_cookie(self.cookie.toPlainText())  # The obvious mistakes, before asking YouTube.
        except AccountError as error:
            self._problem(str(error))
            return
        self.problem.hide()
        self._waiting = True
        self.controller.connect_ytmusic(self.cookie.toPlainText())

    def _problem(self, text: str) -> None:
        self.problem.setText(text)
        self.problem.setStyleSheet(f"color: {colors().live};")
        self.problem.show()

    def _busy(self, name: str, busy: bool) -> None:
        if name == "ytmusic":
            self.connect_button.setEnabled(not busy)
            self.connect_button.setText("Checking…" if busy else "Connect")
            if not busy:
                QTimer.singleShot(0, self._finished)  # After the controller has handled the result.

    def _finished(self) -> None:
        if self.isVisible() and self._waiting:
            # It failed: the reason is in the status bar and the Activity panel; keep the dialog for another try.
            self._waiting = False
            self._problem("That didn't work: see the message at the bottom of the main window. "
                          "You can paste a fresh cookie and try again.")

    def _changed(self, account: dict | None) -> None:
        if account is not None and not account["error"] and self._waiting:
            self._waiting = False
            self.cookie.clear()  # Not kept on screen longer than needed.
            self.accept()
