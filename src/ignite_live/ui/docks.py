"""Dockable panels: Chat, Connections, Activity; plus the picture-in-picture chat window."""

from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import QByteArray, QEvent, QObject, Qt
from PySide6.QtGui import QBrush, QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDockWidget,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QSizeGrip,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import SOURCES, Controller
from ignite_live.core import settings as settings_store
from ignite_live.core.events import ChatEvent, ConnectionState, LiveStatus, StatusChange, ViewerCount
from ignite_live.ui.actions import AppActions
from ignite_live.ui.chat_feed import ChatFeed
from ignite_live.ui.pages.music import read_only_table
from ignite_live.ui.icons import glyph
from ignite_live.ui.theme import COLORS, STATE_TEXT, state_color

HISTORY = 200  # Chat events replayed into a newly opened pop-out.
MAX_ACTIVITY = 500
FILTERS = (("comments", "Comments"), ("gifts", "Gifts"), ("follows", "Follows"))


def filter_checks(feed: ChatFeed) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(QLabel("Show:"))
    for kind, name in FILTERS:
        check = QCheckBox(name)
        check.setChecked(feed.filters[kind])
        check.toggled.connect(lambda shown, k=kind: feed.set_filter(k, shown))
        row.addWidget(check)
    row.addStretch(1)
    return row


PLATFORM_ROWS = (
    ("tiktok", "TikTok", "TikTok username", "tiktok_username"),
    ("twitch", "Twitch", "Twitch channel", "twitch_channel"),
    ("youtube", "YouTube", "@handle, channel or live link", "youtube_channel"),
)
ACTIVE = (ConnectionState.CONNECTING, ConnectionState.CONNECTED, ConnectionState.DEGRADED)


def live_summary(controller: Controller, viewers: int | None) -> str:
    """"LIVE on TikTok (1,234 watching), Twitch" or "Not live"."""
    live = []
    for platform, name, _hint, _field in PLATFORM_ROWS:
        if controller.live.get(platform):
            watching = f" ({viewers:,} watching)" if platform == "tiktok" and viewers is not None else ""
            live.append(name + watching)
    return "LIVE on " + ", ".join(live) if live else "Not live"


class ChatPanel(QWidget):
    """A row per platform (name, status light, connect), filters, and the combined feed."""

    def __init__(self, controller: Controller, actions: AppActions) -> None:
        super().__init__()
        self.controller = controller
        self.history: deque[ChatEvent] = deque(maxlen=HISTORY)
        self.viewers: int | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(6)
        self.names: dict[str, QLineEdit] = {}
        self.lights: dict[str, QLabel] = {}
        self.toggles: dict[str, QToolButton] = {}
        for row, (platform, title, hint, field_name) in enumerate(PLATFORM_ROWS):
            name = QLineEdit(getattr(controller.settings, field_name))
            name.setPlaceholderText(hint)
            name.returnPressed.connect(lambda p=platform: self._connect(p))
            light = QLabel("●")
            toggle = QToolButton()
            toggle.setText("Connect")
            toggle.clicked.connect(lambda _=False, p=platform: self._toggle(p))
            label = QLabel(f"{title}:")
            label.setBuddy(name)
            grid.addWidget(label, row, 0)
            grid.addWidget(name, row, 1)
            grid.addWidget(light, row, 2)
            grid.addWidget(toggle, row, 3)
            self.names[platform], self.lights[platform], self.toggles[platform] = name, light, toggle
        self.username = self.names["tiktok"]  # Focused by open_page("chat").
        layout.addLayout(grid)

        top = QHBoxLayout()
        self.live = QLabel("Not live")
        self.live.setWordWrap(True)
        top.addWidget(self.live, 1)
        for action in (actions.connect_chat, actions.disconnect_chat, actions.popout_chat):
            button = QToolButton()
            button.setDefaultAction(action)
            top.addWidget(button)
        layout.addLayout(top)
        self.state = QLabel("")
        self.state.setWordWrap(True)
        self.state.hide()
        layout.addWidget(self.state)
        self.feed = ChatFeed()
        layout.addLayout(filter_checks(self.feed))
        layout.addWidget(self.feed, 1)

        actions.connect_chat.triggered.connect(self._connect_all)
        controller.chat_event.connect(self._event)
        controller.status_changed.connect(self._status)
        controller.settings_changed.connect(self._settings)
        for platform, *_ in PLATFORM_ROWS:
            self._status(controller.statuses[platform])

    def _connect(self, platform: str) -> None:
        self.controller.connect_platform(platform, self.names[platform].text())

    def _toggle(self, platform: str) -> None:
        if self.controller.statuses[platform].state in ACTIVE:
            self.controller.disconnect_chat(platform)
        else:
            self._connect(platform)

    def _connect_all(self) -> None:
        typed = {p: self.names[p].text().strip() for p, *_ in PLATFORM_ROWS}
        if not any(typed.values()):
            self.controller.connect_all_chats()  # Explains what to fill in.
        for platform, name in typed.items():
            if name:
                self.controller.connect_platform(platform, name)

    def _settings(self, settings) -> None:
        for platform, _title, _hint, field_name in PLATFORM_ROWS:
            if not self.names[platform].hasFocus():
                self.names[platform].setText(getattr(settings, field_name))

    def _event(self, event: ChatEvent) -> None:
        if isinstance(event, (ViewerCount, LiveStatus)):
            if isinstance(event, ViewerCount):
                self.viewers = event.count
            self._show_live()
            return
        self.history.append(event)
        self.feed.add_event(event)

    def _status(self, change: StatusChange) -> None:
        if change.source not in self.names:
            return
        if change.source == "tiktok" and change.state is not ConnectionState.CONNECTED:
            self.viewers = None
        light = self.lights[change.source]
        light.setStyleSheet(f"color: {state_color(change.state, light).name()};")
        light.setToolTip(f"{STATE_TEXT[change.state]}: {change.detail}" if change.detail else STATE_TEXT[change.state])
        self.toggles[change.source].setText("Disconnect" if change.state in ACTIVE else "Connect")
        problems = [self.controller.statuses[p].detail for p, *_ in PLATFORM_ROWS
                    if self.controller.statuses[p].state in (ConnectionState.ERROR, ConnectionState.DEGRADED)]
        self.state.setText("\n".join(problems))
        self.state.setVisible(bool(problems))
        self._show_live()

    def _show_live(self) -> None:
        summary = live_summary(self.controller, self.viewers)
        self.live.setText(summary)
        error = state_color(ConnectionState.ERROR, self.live).name()  # Red "LIVE", readable in any theme.
        self.live.setStyleSheet(f"color: {error}; font-weight: bold;" if summary != "Not live" else "")


class ChatPopout(QWidget):
    """Picture-in-picture chat: a small frameless window that stays on top of OBS or a game.

    Drag it by its top bar and resize it from the corner. Locked, it stays put and ignores
    the mouse (no moving, resizing, scrolling or closing) except for the lock button itself.
    """

    def __init__(self, controller: Controller, history: list[ChatEvent]) -> None:
        flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.controller = controller
        self.viewers: int | None = None
        self.locked = False
        self.setWindowTitle("Ignite Chat")
        self.setMinimumSize(240, 220)
        self.resize(360, 560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)  # A visible edge, since there's no title bar.
        outer.addWidget(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(6, 4, 6, 2)
        layout.setSpacing(4)

        self.header = QWidget()  # The drag handle.
        self.header.setCursor(Qt.CursorShape.SizeAllCursor)
        top = QHBoxLayout(self.header)
        top.setContentsMargins(0, 0, 0, 0)
        self.live = QLabel("Not live")
        self.filters = QToolButton()
        self.filters.setText("Show")
        self.filters.setToolTip("Choose what the chat shows")
        self.filters.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.lock = QToolButton()
        self.lock.setCheckable(True)
        self.lock.setCursor(Qt.CursorShape.ArrowCursor)
        self.lock.toggled.connect(self.set_locked)
        self.close_button = QToolButton()
        self.close_button.setIcon(glyph("close"))
        self.close_button.setToolTip("Close the pop-out chat")
        self.close_button.clicked.connect(self.close)
        top.addWidget(self.live, 1)
        for button in (self.filters, self.lock, self.close_button):
            button.setAutoRaise(True)
            top.addWidget(button)
        layout.addWidget(self.header)
        self.header.installEventFilter(self)
        self.live.installEventFilter(self)

        self.feed = ChatFeed()
        menu = QMenu(self.filters)
        for kind, name in FILTERS:
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(self.feed.filters[kind])
            action.toggled.connect(lambda shown, k=kind: self.feed.set_filter(k, shown))
        self.filters.setMenu(menu)
        layout.addWidget(self.feed, 1)
        self.grip = QSizeGrip(frame)
        layout.addWidget(self.grip, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)

        for event in history:
            self.feed.add_event(event)
        self.feed.jump_to_latest()
        controller.chat_event.connect(self._event)
        controller.status_changed.connect(self._status)
        self._status(controller.statuses["tiktok"])
        geometry = controller.settings.chat_popout_geometry
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode()))
        self.lock.setChecked(controller.settings.chat_popout_locked)
        self.set_locked(self.lock.isChecked())

    def set_locked(self, locked: bool) -> None:
        self.locked = locked
        self.lock.setIcon(glyph("lock" if locked else "unlock", COLORS["accent_2"] if locked else COLORS["muted"]))
        self.lock.setToolTip("Unlock to move, resize, scroll or close the chat" if locked
                             else "Lock the chat in place: it ignores every click except this button")
        # The feed and everything on it (links, the "new messages" pill, the scroll bar) stop taking the mouse.
        self.feed.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, locked)
        for widget in (self.filters, self.close_button, self.grip):
            widget.setVisible(not locked)
        self.header.setCursor(Qt.CursorShape.ArrowCursor if locked else Qt.CursorShape.SizeAllCursor)
        if locked:
            self.feed.jump_to_latest()  # Locked, it can't be scrolled back down: follow new messages.

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if (event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton
                and not self.locked and self.windowHandle() is not None):
            self.windowHandle().startSystemMove()  # Works on Wayland too, unlike moving the window by hand.
            return True
        return super().eventFilter(watched, event)

    def _event(self, event: ChatEvent) -> None:
        if isinstance(event, (ViewerCount, LiveStatus)):
            if isinstance(event, ViewerCount):
                self.viewers = event.count
            self._status(self.controller.statuses["tiktok"])
        else:
            self.feed.add_event(event)

    def _status(self, change: StatusChange) -> None:
        if change.source in ("tiktok", "twitch", "youtube"):
            summary = live_summary(self.controller, self.viewers)
            self.live.setText(summary)
            error = state_color(ConnectionState.ERROR, self.live).name()
            self.live.setStyleSheet(f"color: {error}; font-weight: bold;" if summary != "Not live" else "")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        s = self.controller.settings
        s.chat_popout_geometry = bytes(self.saveGeometry().toBase64()).decode()
        s.chat_popout_locked = self.locked
        settings_store.save(s, self.controller.settings_path)
        super().closeEvent(event)


class ConnectionsPanel(QWidget):
    def __init__(self, controller: Controller) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.table = read_only_table(["Service", "State", "Details"])
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.rows = {source: row for row, source in enumerate(SOURCES)}
        self.table.setRowCount(len(SOURCES))
        for source, row in self.rows.items():
            self.table.setItem(row, 0, QTableWidgetItem(SOURCES[source]))
            self.table.setItem(row, 1, QTableWidgetItem(""))
            self.table.setItem(row, 2, QTableWidgetItem(""))
        layout.addWidget(self.table)
        controller.status_changed.connect(self.update_status)
        for change in controller.statuses.values():
            self.update_status(change)

    def update_status(self, change: StatusChange) -> None:
        row = self.rows[change.source]
        state = self.table.item(row, 1)
        state.setText(STATE_TEXT[change.state])
        state.setForeground(QBrush(state_color(change.state, self.table)))
        detail = self.table.item(row, 2)
        detail.setText(change.detail)
        detail.setToolTip(change.detail)


class ActivityPanel(QWidget):
    def __init__(self, controller: Controller) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.table = read_only_table(["Time", "Message"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        controller.notice.connect(self.add)

    def add(self, level: str, text: str) -> None:
        bar = self.table.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 2
        row = self.table.rowCount()
        self.table.insertRow(row)
        when = QTableWidgetItem(time.strftime("%H:%M:%S"))
        message = QTableWidgetItem(text)
        message.setToolTip(text)
        if level == "error":
            message.setForeground(QBrush(state_color(ConnectionState.ERROR, self.table)))
        self.table.setItem(row, 0, when)
        self.table.setItem(row, 1, message)
        while self.table.rowCount() > MAX_ACTIVITY:
            self.table.removeRow(0)
        if at_bottom:
            self.table.scrollToBottom()


def dock(title: str, name: str, widget: QWidget, parent: QWidget) -> QDockWidget:
    panel = QDockWidget(title, parent)
    panel.setObjectName(name)  # Needed for saveState()/restoreState().
    panel.setWidget(widget)
    return panel
