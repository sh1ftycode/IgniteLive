"""Live Chat: the combined TikTok, Twitch and YouTube chat, with its platforms and settings beside it;
then who's watching (and their loyalty points) and the song requests, as tabs.

Moderation is on each message's right-click menu: block a viewer from Ignite, and, with TikTok signed in on
your Ignite account, make them a moderator, mute or kick them in your LIVE. Messages can be sent to TikTok chat
the same way; the box says so when that isn't set up rather than pretending to send.
"""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QSlider,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import Controller
from ignite_live.core import settings as settings_store
from ignite_live.core.events import ChatEvent, ConnectionState, LiveStatus, StatusChange, ViewerCount
from ignite_live.ui.actions import AppActions
from ignite_live.ui.chat_feed import EVENT_ROLE, TEXT_SIZE_MAX, TEXT_SIZE_MIN, TEXT_SIZE_STEP, ChatFeed, fades
from ignite_live.ui.kit import (
    Card,
    EmptyState,
    StatusDot,
    button,
    icon_button,
    label,
    recolor_icon_button,
    restyle_buttons,
)
from ignite_live.ui.live_widgets import StatsBar
from ignite_live.ui.pages.music import MusicPage
from ignite_live.ui.pages.viewers import ViewersPage
from ignite_live.ui.theme import STATE_TEXT, theme_changed

HISTORY = 200  # Chat events replayed into a newly opened pop-out.
NOT_CHAT_LINES = (ViewerCount, LiveStatus)
FILTERS = (("comments", "Comments"), ("gifts", "Gifts"), ("follows", "Follows"), ("joins", "Joins"))
PLATFORM_ROWS = (
    ("tiktok", "TikTok", "TikTok username", "tiktok_username"),
    ("twitch", "Twitch", "Twitch channel", "twitch_channel"),
    ("youtube", "YouTube", "@handle, channel or live link", "youtube_channel"),
)
ACTIVE = (ConnectionState.CONNECTING, ConnectionState.CONNECTED, ConnectionState.DEGRADED)
TONE = {ConnectionState.CONNECTED: "healthy", ConnectionState.CONNECTING: "warning",
        ConnectionState.DEGRADED: "warning", ConnectionState.ERROR: "error", ConnectionState.OFFLINE: "muted"}


class PlatformRow(QWidget):
    """A chat platform: its name field, a status dot and detail, and Connect / Disconnect."""

    def __init__(self, controller: Controller, platform: str, title: str, hint: str, field: str) -> None:
        super().__init__()
        self.controller, self.platform = controller, platform
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.dot = StatusDot("muted")
        head.addWidget(self.dot)
        head.addWidget(label(title))
        head.addStretch(1)
        self.state = label("Off", "muted")
        head.addWidget(self.state)
        layout.addLayout(head)
        line = QHBoxLayout()
        line.setSpacing(6)
        self.name = QLineEdit(getattr(controller.settings, field))
        self.name.setPlaceholderText(hint)
        self.name.setAccessibleName(f"{title} channel")
        self.name.returnPressed.connect(self._connect)
        self.toggle = button("Connect")
        self.toggle.clicked.connect(self._toggle)
        line.addWidget(self.name, 1)
        line.addWidget(self.toggle)
        layout.addLayout(line)
        self.detail = label("", "muted", wrap=True)
        self.detail.hide()
        layout.addWidget(self.detail)

    def _connect(self) -> None:
        self.controller.connect_platform(self.platform, self.name.text())

    def _toggle(self) -> None:
        if self.controller.statuses[self.platform].state in ACTIVE:
            self.controller.disconnect_chat(self.platform)
        else:
            self._connect()

    def show_status(self, change: StatusChange) -> None:
        self.dot.set_tone(TONE[change.state])
        self.state.setText(STATE_TEXT[change.state])
        self.toggle.setText("Disconnect" if change.state in ACTIVE else "Connect")
        problem = change.state in (ConnectionState.ERROR, ConnectionState.DEGRADED) and change.detail
        self.detail.setText(change.detail if problem else "")
        self.detail.setVisible(bool(problem))


class ChatTab(QWidget):
    """The feed and its message box on the left; platforms, chat settings and the counts on the right."""

    open_page_requested = Signal(str)

    def __init__(self, controller: Controller, actions: AppActions, history: deque) -> None:
        super().__init__()
        self.controller, self.actions, self.history = controller, actions, history
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 24)
        layout.setSpacing(16)

        # The chat.
        self.chat_card = Card("Chat", icon="message-square-more", padded=False)
        self.popout = icon_button("external-link", "Pop out chat: a small window on top of OBS or your game")
        self.popout.clicked.connect(actions.popout_chat.trigger)
        self.more = icon_button("ellipsis-vertical", "Chat actions")
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.more)
        for action in (actions.connect_chat, actions.disconnect_chat, None, actions.clear_chats, None,
                       actions.lock_popout):
            menu.addSeparator() if action is None else menu.addAction(action)
        self.more.setMenu(menu)
        self.chat_card.add_header_widget(self.popout)
        self.chat_card.add_header_widget(self.more)
        self.feed = ChatFeed(size=controller.settings.chat_text_size)
        self.feed.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.feed.customContextMenuRequested.connect(self._moderate)
        self.feed.text_size_changed.connect(self._text_size)
        self.empty = EmptyState("message-square-more", "No messages yet",
                                "Chat from TikTok, Twitch and YouTube appears here once a platform is connected "
                                "and your stream is live.")
        self.stack = QStackedWidget()
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.feed)
        self.chat_card.body.addWidget(self.stack, 1)

        composer = QWidget()
        bar = QHBoxLayout(composer)
        bar.setContentsMargins(12, 10, 12, 12)
        bar.setSpacing(8)
        self.message = QLineEdit()
        self.message.setAccessibleName("Message to TikTok chat")
        self.message.setMaxLength(150)
        self.message.returnPressed.connect(self._send)
        self.send = button("", "send", kind="primary", tip="Send to your TikTok LIVE chat (Enter)")
        self.send.setFixedWidth(44)
        self.send.clicked.connect(self._send)
        bar.addWidget(self.message, 1)
        bar.addWidget(self.send)
        self.chat_card.body.addWidget(composer)
        layout.addWidget(self.chat_card, 1)

        # The side panel.
        side = QWidget()
        side.setFixedWidth(330)
        column = QVBoxLayout(side)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(16)
        self.platforms_card = Card("Platforms", icon="plug")
        self.rows = {platform: PlatformRow(controller, platform, title, hint, field)
                     for platform, title, hint, field in PLATFORM_ROWS}
        for row in self.rows.values():
            self.platforms_card.body.addWidget(row)
        all_row = QHBoxLayout()
        self.connect_all = button("Connect All", "plug")
        self.connect_all.clicked.connect(self._connect_all)
        self.disconnect_all = button("Disconnect All", "unplug")
        self.disconnect_all.clicked.connect(actions.disconnect_chat.trigger)
        all_row.addWidget(self.connect_all)
        all_row.addWidget(self.disconnect_all)
        self.platforms_card.body.addLayout(all_row)
        column.addWidget(self.platforms_card)

        self.settings_card = Card("Chat settings", icon="sliders")
        self.settings_card.body.addWidget(label("Show", "caption"))
        shows = QHBoxLayout()
        for kind, name in FILTERS:
            check = QCheckBox(name)
            check.setChecked(self.feed.filters[kind])
            check.toggled.connect(lambda shown, k=kind: self.feed.set_filter(k, shown))
            shows.addWidget(check)
        shows.addStretch(1)
        self.settings_card.body.addLayout(shows)
        self.settings_card.body.addWidget(label("Text size", "caption"))
        size_row = QHBoxLayout()
        self.text_size = QSlider(Qt.Orientation.Horizontal)
        self.text_size.setRange(TEXT_SIZE_MIN, TEXT_SIZE_MAX)
        self.text_size.setSingleStep(TEXT_SIZE_STEP)
        self.text_size.setPageStep(TEXT_SIZE_STEP)
        self.text_size.setValue(self.feed.text_size)
        self.text_size.setAccessibleName("Chat text size")
        self.text_size.valueChanged.connect(lambda v: self.feed.set_text_size(round(v / TEXT_SIZE_STEP) * TEXT_SIZE_STEP))
        self.size_value = label(f"{self.feed.text_size} %", "muted")
        self.feed.text_size_changed.connect(lambda percent: (self.size_value.setText(f"{percent} %"),
                                                             self.text_size.setValue(percent)))
        size_row.addWidget(self.text_size, 1)
        size_row.addWidget(self.size_value)
        self.settings_card.body.addLayout(size_row)
        self.settings_card.body.addWidget(label("Text to speech", "caption"))
        speech = QHBoxLayout()
        self.mute = button("Mute Speech", "volume-x")
        self.mute.setCheckable(True)
        self.mute.toggled.connect(lambda on: actions.mute_tts.setChecked(on))
        actions.mute_tts.toggled.connect(self._muted)
        self.skip = button("Skip Message", "skip-forward")
        self.skip.clicked.connect(actions.skip_tts.trigger)
        speech.addWidget(self.mute)
        speech.addWidget(self.skip)
        self.settings_card.body.addLayout(speech)
        column.addWidget(self.settings_card)

        self.stats_card = Card("Audience", icon="users")
        self.stats = StatsBar(controller)
        self.stats_card.body.addWidget(self.stats)
        column.addWidget(self.stats_card)
        column.addStretch(1)
        layout.addWidget(side)

        controller.chat_event.connect(self._event)
        controller.chats_cleared.connect(self._cleared)
        controller.status_changed.connect(self._status)
        controller.settings_changed.connect(self._settings)
        controller.tiktok_account_changed.connect(lambda _s: self._composer())
        controller.settings_changed.connect(lambda _s: self._composer())
        actions.mute_tts.changed.connect(self._actions_changed)
        actions.skip_tts.changed.connect(self._actions_changed)
        theme_changed().connect(self._restyle)
        for platform in self.rows:
            self._status(controller.statuses[platform])
        self._muted(actions.mute_tts.isChecked())
        self._actions_changed()
        self._composer()
        self._restyle()

    # Feed.

    def _event(self, event: ChatEvent) -> None:
        if isinstance(event, NOT_CHAT_LINES):
            return
        if not fades(event):  # A pop-out opened later shouldn't bring back rows that have faded away.
            self.history.append(event)
        self.feed.add_event(event)
        if self.feed.count():
            self.stack.setCurrentWidget(self.feed)

    def _cleared(self) -> None:
        self.history.clear()
        self.feed.clear()
        self.stack.setCurrentWidget(self.empty)

    def _text_size(self, percent: int) -> None:
        self.controller.settings.chat_text_size = percent
        settings_store.save(self.controller.settings, self.controller.settings_path)

    # Moderation: right-click a message.

    def _moderate(self, pos) -> None:
        item = self.feed.itemAt(pos)
        event = item.data(EVENT_ROLE) if item is not None else None
        user = getattr(event, "user", None)
        if user is None:
            return
        menu = QMenu(self)
        menu.addSection(f"{user.nickname or user.unique_id} · {user.platform_name}")
        blocked = user.key.lower() in {entry.lower() for entry in self.controller.settings.blocked_users}
        menu.addAction("Unblock in Ignite" if blocked else "Block in Ignite",
                       lambda: self.controller.block_viewer(user.key, not blocked)).setToolTip(
            "Ignite ignores their messages: no speech, commands, requests or points")
        if user.platform == "tiktok" and self.controller.tiktok_account.get("moderation"):
            menu.addSeparator()
            for text, action, seconds in (("Make Moderator", "mod", 0), ("Remove Moderator", "unmod", 0),
                                          ("Mute for 5 Minutes", "mute", 300), ("Unmute", "unmute", 0),
                                          ("Kick from LIVE", "kick", 0)):
                menu.addAction(text, lambda a=action, s=seconds: self.controller.tiktok_moderate(user.key, a, s or 300))
        menu.addSeparator()
        menu.addAction("Copy Name", lambda: self._copy(user.nickname or user.unique_id))
        menu.exec(self.feed.viewport().mapToGlobal(pos))

    def _copy(self, text: str) -> None:
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.clipboard().setText(text)

    # Sending.

    def _composer(self) -> None:
        tiktok = self.controller.tiktok_account
        ready = bool(tiktok.get("connected"))
        self.message.setEnabled(ready)
        self.send.setEnabled(ready)
        name = tiktok.get("username")
        self.message.setPlaceholderText(f"Send a message as @{name}…" if ready and name else
                                        "Send a message to your TikTok chat…" if ready else
                                        "To chat from here, sign in to TikTok in your Ignite account (Integrations)")

    def _send(self) -> None:
        if self.controller.send_tiktok_chat(self.message.text()):
            self.message.clear()

    # State.

    def _connect_all(self) -> None:
        typed = {p: row.name.text().strip() for p, row in self.rows.items()}
        if not any(typed.values()):
            self.controller.connect_all_chats()  # Explains what to fill in.
        for platform, name in typed.items():
            if name:
                self.controller.connect_platform(platform, name)

    def _status(self, change: StatusChange) -> None:
        row = self.rows.get(change.source)
        if row is not None:
            row.show_status(change)

    def _settings(self, settings) -> None:
        for platform, _title, _hint, field in PLATFORM_ROWS:
            if not self.rows[platform].name.hasFocus():
                self.rows[platform].name.setText(getattr(settings, field))

    def _muted(self, muted: bool) -> None:
        self.mute.blockSignals(True)
        self.mute.setChecked(muted)
        self.mute.blockSignals(False)
        self.mute.setText("Unmute Speech" if muted else "Mute Speech")

    def _actions_changed(self) -> None:
        self.mute.setEnabled(self.actions.mute_tts.isEnabled())
        self.skip.setEnabled(self.actions.skip_tts.isEnabled())

    def _restyle(self, _palette=None) -> None:
        for card in (self.chat_card, self.platforms_card, self.settings_card, self.stats_card):
            card.restyle()
        self.empty.restyle()
        recolor_icon_button(self.popout)
        recolor_icon_button(self.more)
        restyle_buttons(self.send, self.connect_all, self.disconnect_all, self.mute, self.skip)


class LiveChatPage(QWidget):
    """Chat, Viewers & Points, Song Requests. ``history``: recent chat, for a newly opened pop-out."""

    def __init__(self, controller: Controller, actions: AppActions) -> None:
        super().__init__()
        self.setObjectName("page")
        self.history: deque[ChatEvent] = deque(maxlen=HISTORY)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setExpanding(False)
        self.chat = ChatTab(controller, actions, self.history)
        self.viewers = ViewersPage(controller)
        self.music = MusicPage(controller, actions)
        self.tabs.addTab(self.chat, "Chat")
        self.tabs.addTab(self.viewers, "Viewers && Points")
        self.tabs.addTab(self.music, "Song Requests")
        self.tabs.setStyleSheet("QTabWidget::pane { border: none; } QTabWidget::tab-bar { left: 24px; } QTabBar { margin-top: 8px; }")
        layout.addWidget(self.tabs)
        self.feed = self.chat.feed

    @property
    def username(self) -> QLineEdit:
        """The TikTok username field (focused by open_page("chat"))."""
        return self.chat.rows["tiktok"].name

    def show_tab(self, name: str) -> None:
        self.tabs.setCurrentWidget({"chat": self.chat, "viewers": self.viewers, "music": self.music}[name])
