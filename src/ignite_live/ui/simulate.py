"""View → Simulate Events: fake gifts, follows, likes, shares, comments and viewer counts, to test a setup offline.

Simulated events run through everything a real one does (rules, goals, games,
overlays, text to speech, song requests) but never reach stream reports, points,
or leaderboards.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import Controller
from ignite_live.core.events import (
    PLATFORMS,
    ChatComment,
    ChatFollow,
    ChatGift,
    ChatLike,
    ChatShare,
    ChatUser,
    ViewerCount,
)
from ignite_live.ui.pages.stream import note

ROLES = {"viewer": "Viewer", "follower": "Follower", "mod": "Moderator"}


def test_user(name: str = "Test viewer", role: str = "follower", platform: str = "tiktok") -> ChatUser:
    name = name.strip() or "Test viewer"
    return ChatUser("test-" + name.lower().replace(" ", "-")[:30], name[:40], is_moderator=role == "mod",
                    is_follower=role in ("follower", "mod"), platform=platform)


def sample_events(page: str) -> list:
    """What an overlay's Send Test button sends."""
    fan, friend = test_user("Test viewer"), test_user("Another viewer", "viewer")
    if page == "alerts":
        return [ChatGift(fan, "Rose", 5, diamonds=1), ChatFollow(friend)]
    if page == "chat":
        return [ChatComment(fan, "This is a test message", 0), ChatGift(friend, "Rose", 1, diamonds=1),
                ChatFollow(friend)]
    return []


class SimulateDialog(QDialog):
    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Simulate events")
        layout = QVBoxLayout(self)
        layout.addWidget(note("Test your rules, goals, games and overlays without going live. Simulated events "
                              "never count in stream reports, points or leaderboards."))
        who = QHBoxLayout()
        self.name = QLineEdit("Test viewer")
        self.role = QComboBox()
        for key, label in ROLES.items():
            self.role.addItem(label, key)
        self.role.setCurrentIndex(1)
        self.platform = QComboBox()
        for key, label in PLATFORMS.items():
            self.platform.addItem(label, key)
        for widget in (QLabel("As"), self.name, self.role, self.platform):
            who.addWidget(widget)
        layout.addLayout(who)

        grid = QGridLayout()
        self.gift = QLineEdit("Rose")
        self.gift_count = self._spin(1, 1, 999, "× ")
        self.coins = self._spin(1, 0, 100_000, "Worth ", " each")
        self.coins.setToolTip("Coins (diamonds) per gift")
        self.likes = self._spin(50, 1, 10_000, "", " likes")
        self.comment = QLineEdit("!sr Never Gonna Give You Up - Rick Astley")
        self.viewers = self._spin(25, 0, 1_000_000, "", " viewers")
        rows = [
            ("Gift", [self.gift, self.gift_count, self.coins], self._gift),
            ("Follow", [], lambda: self._send(ChatFollow(self._user()))),
            ("Likes", [self.likes], lambda: self._send(ChatLike(self._user(), self.likes.value()))),
            ("Share", [], lambda: self._send(ChatShare(self._user()))),
            ("Comment", [self.comment], self._comment),
            ("Viewer count", [self.viewers],
             lambda: self._send(ViewerCount(self.viewers.value(), self.platform.currentData()))),
        ]
        for row, (label, widgets, send) in enumerate(rows):
            grid.addWidget(QLabel(label), row, 0)
            fields = QHBoxLayout()
            for widget in widgets:
                fields.addWidget(widget, 1 if isinstance(widget, QLineEdit) else 0)
            fields.addStretch(0 if widgets else 1)
            grid.addLayout(fields, row, 1)
            button = QPushButton("Send")
            button.clicked.connect(send)
            grid.addWidget(button, row, 2)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        self.resize(560, 0)

    @staticmethod
    def _spin(value: int, low: int, high: int, prefix: str = "", suffix: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setValue(value)
        spin.setPrefix(prefix)
        spin.setSuffix(suffix)
        spin.setGroupSeparatorShown(True)
        return spin

    def _user(self) -> ChatUser:
        return test_user(self.name.text(), self.role.currentData(), self.platform.currentData())

    def _send(self, event) -> None:
        self.controller.simulate(event)

    def _gift(self) -> None:
        self._send(ChatGift(self._user(), self.gift.text().strip()[:60] or "Rose", self.gift_count.value(),
                            diamonds=self.coins.value()))

    def _comment(self) -> None:
        if self.comment.text().strip():
            self._send(ChatComment(self._user(), self.comment.text().strip()[:300], 0))
