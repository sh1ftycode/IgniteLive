"""Dockable panels: Chat, Connections, Activity; plus the picture-in-picture chat window."""

from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import QByteArray, QEvent, QObject, QRectF, Qt
from PySide6.QtGui import QBrush, QCloseEvent, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDockWidget,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QSizeGrip,
    QSlider,
    QStackedWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from ignite_live.controller import SOURCES, Controller
from ignite_live.core import settings as settings_store
from ignite_live.core.events import ChatEvent, ConnectionState, LiveStatus, StatusChange, ViewerCount
from ignite_live.ui.actions import AppActions
from ignite_live.ui.chat_feed import ChatFeed
from ignite_live.ui.live_widgets import AudienceList, MusicBar, StatsBar
from ignite_live.ui.pages.music import read_only_table
from ignite_live.ui.icons import glyph
from ignite_live.ui.theme import RADIUS, STATE_TEXT, colors, state_color, theme_changed, tint, tint_color

HISTORY = 200  # Chat events replayed into a newly opened pop-out.
MAX_ACTIVITY = 500
FILTERS = (("comments", "Comments"), ("gifts", "Gifts"), ("follows", "Follows"))
NOT_CHAT_LINES = (ViewerCount, LiveStatus)  # Shown in the stats row, not in the feed.


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


class ChatPanel(QWidget):
    """A row per platform (name, status light, connect), the stats row, and tabs: the combined feed with
    its filters, and who's here."""

    def __init__(self, controller: Controller, actions: AppActions) -> None:
        super().__init__()
        self.controller = controller
        self.history: deque[ChatEvent] = deque(maxlen=HISTORY)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(8)
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
        top.addStretch(1)
        for action in (actions.connect_chat, actions.disconnect_chat, actions.popout_chat):
            button = QToolButton()
            button.setDefaultAction(action)
            top.addWidget(button)
        layout.addLayout(top)
        self.stats = StatsBar(controller)  # Live or not, and viewers, followers and likes per platform.
        layout.addWidget(self.stats)
        self.state = QLabel("")
        self.state.setWordWrap(True)
        self.state.hide()
        layout.addWidget(self.state)
        self.feed = ChatFeed()
        chat = QWidget()
        chat_layout = QVBoxLayout(chat)
        chat_layout.setContentsMargins(0, 4, 0, 0)
        chat_layout.addLayout(filter_checks(self.feed))
        chat_layout.addWidget(self.feed, 1)
        self.audience = AudienceList(controller)
        self.views = QTabWidget()
        self.views.setDocumentMode(True)
        self.views.addTab(chat, "Chat")
        self.views.addTab(self.audience, "Viewers")
        self.views.setTabToolTip(1, "Who's here right now, and the platform they're watching from")
        layout.addWidget(self.views, 1)

        actions.connect_chat.triggered.connect(self._connect_all)
        controller.chat_event.connect(self._event)
        controller.status_changed.connect(self._status)
        controller.settings_changed.connect(self._settings)
        controller.audience_changed.connect(self._audience)
        self._audience(controller.audience_snapshot)
        for platform, *_ in PLATFORM_ROWS:
            self._status(controller.statuses[platform])

    def _audience(self, people: list) -> None:
        self.views.setTabText(1, f"Viewers ({len(people):,})" if people else "Viewers")

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
        if isinstance(event, NOT_CHAT_LINES):
            return
        self.history.append(event)
        self.feed.add_event(event)

    def _status(self, change: StatusChange) -> None:
        if change.source not in self.names:
            return
        light = self.lights[change.source]
        light.setStyleSheet(f"color: {state_color(change.state, light).name()};")
        light.setToolTip(f"{STATE_TEXT[change.state]}: {change.detail}" if change.detail else STATE_TEXT[change.state])
        self.toggles[change.source].setText("Disconnect" if change.state in ACTIVE else "Connect")
        problems = [self.controller.statuses[p].detail for p, *_ in PLATFORM_ROWS
                    if self.controller.statuses[p].state in (ConnectionState.ERROR, ConnectionState.DEGRADED)]
        self.state.setText("\n".join(problems))
        self.state.setVisible(bool(problems))


class ChatPopout(QWidget):
    """Picture-in-picture chat: a small frameless, see-through window that stays on top of OBS or a game.

    The window's background is as solid as the streamer sets it (0 to 100 %, in its menu), so the game shows
    through; chat, the stats row, the viewer list and the music buttons stay fully drawn on top, each
    readable without a panel behind it (text with an edge, numbers on small backed pills).

    Drag it by its top bar and resize it from the corner. Locked, it stays put and can't be moved, resized,
    scrolled or closed; only the lock button and the music buttons still take clicks.
    """

    def __init__(self, controller: Controller, history: list[ChatEvent]) -> None:
        flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)  # paintEvent draws what background there is.
        self.controller = controller
        self.locked = False
        s = controller.settings
        self.opacity = max(0, min(100, s.chat_popout_opacity))
        self.setWindowTitle("Ignite Chat")
        self.setMinimumSize(260, 240)
        self.resize(380, 600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 4)
        layout.setSpacing(4)

        self.header = QWidget()  # The drag handle: a solid bar, so its buttons read over anything.
        self.header.setObjectName("popoutHeader")
        self.header.setCursor(Qt.CursorShape.SizeAllCursor)
        top = QHBoxLayout(self.header)
        top.setContentsMargins(4, 2, 4, 2)
        top.setSpacing(2)
        self.show_chat = self._view_button("Chat", "Chat from every connected platform")
        self.show_viewers = self._view_button("Viewers", "Who's here right now, and the platform they're watching from")
        self.show_chat.setChecked(True)
        self.options = QToolButton()
        self.options.setToolTip("What this window shows, and how see-through it is")
        self.options.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.lock = QToolButton()
        self.lock.setCheckable(True)
        self.lock.toggled.connect(self.set_locked)
        self.close_button = QToolButton()
        self.close_button.setToolTip("Close the pop-out chat")
        self.close_button.clicked.connect(self.close)
        top.addWidget(self.show_chat)
        top.addWidget(self.show_viewers)
        top.addStretch(1)
        for button in (self.options, self.lock, self.close_button):
            button.setAutoRaise(True)
            button.setCursor(Qt.CursorShape.ArrowCursor)
            top.addWidget(button)
        layout.addWidget(self.header)
        self.header.installEventFilter(self)

        self.stats = StatsBar(controller, see_through=True)
        self.stats.setVisible(s.chat_popout_stats)
        self.stats.installEventFilter(self)  # Dragging the numbers moves the window too.
        layout.addWidget(self.stats)

        self.feed = ChatFeed(see_through=True)
        self.audience = AudienceList(controller, see_through=True)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.feed)
        self.stack.addWidget(self.audience)
        layout.addWidget(self.stack, 1)
        self.show_chat.clicked.connect(lambda: self.stack.setCurrentWidget(self.feed))
        self.show_viewers.clicked.connect(lambda: self.stack.setCurrentWidget(self.audience))

        self.music = MusicBar(controller, see_through=True)
        self.music.setVisible(s.chat_popout_music)
        bottom = QHBoxLayout()
        bottom.setSpacing(2)
        bottom.addWidget(self.music, 1)
        self.grip = QSizeGrip(self)
        bottom.addWidget(self.grip, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(bottom)

        self.options.setMenu(self._menu())
        for event in history:
            self.feed.add_event(event)
        self.feed.jump_to_latest()
        controller.chat_event.connect(self._event)
        controller.audience_changed.connect(self._audience)
        self._audience(controller.audience_snapshot)
        if s.chat_popout_geometry:
            self.restoreGeometry(QByteArray.fromBase64(s.chat_popout_geometry.encode()))
        self.lock.setChecked(s.chat_popout_locked)
        theme_changed().connect(self._restyle)
        self._restyle()

    def _view_button(self, text: str, tip: str) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tip)
        button.setCheckable(True)
        button.setAutoExclusive(True)
        button.setAutoRaise(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setCursor(Qt.CursorShape.ArrowCursor)
        return button

    def _menu(self) -> QMenu:
        """What the chat shows, which extra rows are on, and the background slider."""
        menu = QMenu(self.options)
        for kind, name in FILTERS:
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(self.feed.filters[kind])
            action.toggled.connect(lambda shown, k=kind: self.feed.set_filter(k, shown))
        menu.addSeparator()
        for text, widget in (("Viewer and follower counts", self.stats), ("Music buttons", self.music)):
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(not widget.isHidden())
            action.toggled.connect(widget.setVisible)
        menu.addSeparator()
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(12, 4, 12, 4)
        row_layout.addWidget(QLabel("Background:"))
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setPageStep(10)
        self.opacity_slider.setMinimumWidth(120)
        self.opacity_slider.setValue(self.opacity)
        self.opacity_slider.setToolTip("0 %: only the chat itself floats over your game. 100 %: a solid panel.")
        self.opacity_value = QLabel()
        self.opacity_value.setMinimumWidth(self.opacity_value.fontMetrics().horizontalAdvance("100 %"))
        self.opacity_slider.valueChanged.connect(self.set_opacity)
        row_layout.addWidget(self.opacity_slider, 1)
        row_layout.addWidget(self.opacity_value)
        slider = QWidgetAction(menu)
        slider.setDefaultWidget(row)
        menu.addAction(slider)
        self.set_opacity(self.opacity)
        return menu

    def set_opacity(self, percent: int) -> None:
        """How solid the window's background is; everything drawn on it stays as it is."""
        self.opacity = max(0, min(100, int(percent)))
        self.opacity_value.setText(f"{self.opacity} %")
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        p = colors()
        # All but invisible, yet not nothing: where a window is fully transparent, Windows hands clicks to
        # whatever is behind it, and the chat couldn't be dragged, resized or scrolled at 0 %.
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
        solid = self.opacity / 100
        painter.setBrush(tint_color(p.panel, solid))
        # The edge shows where to grab the window; locked in place and fully clear, it goes too.
        painter.setPen(QPen(tint_color(p.divider, solid if self.locked else max(solid, 0.6)), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS + 2, RADIUS + 2)

    def set_locked(self, locked: bool) -> None:
        self.locked = locked
        self.lock.setIcon(glyph("lock" if locked else "unlock", colors().accent_text if locked else None))
        self.lock.setToolTip("Unlock to move, resize, scroll or close the chat" if locked
                             else "Lock the chat in place: it can't be moved, resized, scrolled or closed "
                                  "(the music buttons still work)")
        # The lists and everything on them (the "new messages" pill, the scroll bars) stop taking the mouse.
        for widget in (self.feed, self.audience):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, locked)
        for widget in (self.options, self.close_button, self.grip):
            widget.setVisible(not locked)
        self.header.setCursor(Qt.CursorShape.ArrowCursor if locked else Qt.CursorShape.SizeAllCursor)
        if locked:
            self.feed.jump_to_latest()  # Locked, it can't be scrolled back down: follow new messages.
        self.update()  # The edge is drawn differently.

    def _restyle(self, _palette=None) -> None:
        """Icons and the bar are drawn in the mode's colours: redraw them when it changes."""
        p = colors()
        self.header.setStyleSheet(f"QWidget#popoutHeader {{ background: {tint(p.panel, 0.86)};"
                                  f" border-radius: {RADIUS}px; }}")
        self.close_button.setIcon(glyph("close"))
        self.options.setIcon(glyph("menu"))
        self.show_chat.setIcon(glyph("chat"))
        self.show_viewers.setIcon(glyph("people"))
        self.set_locked(self.locked)

    def _audience(self, people: list) -> None:
        self.show_viewers.setText(f"Viewers {len(people):,}" if people else "Viewers")

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if (event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton
                and not self.locked and self.windowHandle() is not None):
            self.windowHandle().startSystemMove()  # Works on Wayland too, unlike moving the window by hand.
            return True
        return super().eventFilter(watched, event)

    def _event(self, event: ChatEvent) -> None:
        if not isinstance(event, NOT_CHAT_LINES):
            self.feed.add_event(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        s = self.controller.settings
        s.chat_popout_geometry = bytes(self.saveGeometry().toBase64()).decode()
        s.chat_popout_locked = self.locked
        s.chat_popout_opacity = self.opacity
        s.chat_popout_stats = not self.stats.isHidden()
        s.chat_popout_music = not self.music.isHidden()
        settings_store.save(s, self.controller.settings_path)
        super().closeEvent(event)


class ConnectionsPanel(QWidget):
    def __init__(self, controller: Controller) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.table = read_only_table(["Service", "State", "Details"], min_rows=2)
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
        layout.setContentsMargins(8, 8, 8, 8)
        self.table = read_only_table(["Time", "Message"], min_rows=2)
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
    widget.setAutoFillBackground(True)  # A panel on the window background; the gap between docks divides them.
    panel.setWidget(widget)
    return panel
