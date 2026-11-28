"""Overview: the stream at a glance.

Four stat tiles, the chat, the stream (preview and go-live controls), the OBS scenes, the latest follows, gifts
and shares, and the Ctrl+K bar. Every number is one Ignite already tracks; what isn't available says so (OBS
offline, nothing yet) instead of showing zeros that look real.
"""

from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QMenu,
)

from ignite_live.controller import Controller
from ignite_live.core.events import ChatComment, ChatFollow, ChatGift, ChatShare
from ignite_live.integrations.obs_studio import Studio, clock
from ignite_live.live.audience import compact
from ignite_live.ui.actions import AppActions
from ignite_live.ui.chat_feed import ChatFeed
from ignite_live.ui.command_bar import open_command_bar
from ignite_live.ui.flow_layout import FlowLayout
from ignite_live.ui.icons import logo
from ignite_live.ui.kit import (
    Card,
    EmptyState,
    EventRow,
    MetricCard,
    PageContainer,
    StatusIndicator,
    button,
    hline,
    icon_button,
    label,
    recolor_icon_button,
    restyle_buttons,
)
from ignite_live.ui.theme import colors, set_live, theme_changed

PREVIEW_MS = 1500
PREVIEW_MIN_WIDTH = 320
PREVIEW_MAX_WIDTH = 1280
RECENT_KINDS = (ChatFollow, ChatGift, ChatShare)
MAX_EVENTS = 5
SPARK_POINTS = 40  # Viewer readings kept for the small line on the Viewers tile.
RATE_WINDOW = 60.0  # Seconds of chat counted for "Chat per min".


def event_text(event) -> tuple[str, str, str, str]:
    """(icon, tone, title, detail) for a follow, gift or share."""
    name = event.user.nickname or event.user.unique_id
    if isinstance(event, ChatFollow):
        return "heart", "error", "New follower", f"{name} followed you"
    if isinstance(event, ChatGift):
        count = f" ×{event.count}" if event.count > 1 else ""
        return "gift", "warning", "New gift", f"{name} sent {event.gift_name}{count}"
    return "share", "accent", "New share", f"{name} shared your LIVE"


class CommandHint(QFrame):
    """A clickable bar that says what Ctrl+K does."""

    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("commandHint")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Jump to any screen or run an action (Ctrl+K)")
        self.setFixedHeight(48)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Search or run a command")
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 0, 16, 0)
        row.addWidget(label("Search or run a command…", "muted"), 1)
        row.addWidget(label("Ctrl K", "muted"))
        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(event)


class StreamPreview(QFrame):
    """OBS's program scene as it looks right now, refreshed while visible; an honest empty state otherwise."""

    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        self.setObjectName("inset")
        self.setMinimumSize(200, 120)  # Shrinks with its card; frames are fetched at whatever size it has.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.vertical = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)
        empty = QWidget()
        column = QVBoxLayout(empty)
        column.addStretch(1)
        mark = QLabel()
        mark.setPixmap(QPixmap.fromImage(logo(36)))
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(mark)
        self.brand = QLabel()
        self.brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(self.brand)
        self.hint = label("", "muted", wrap=True)
        self.hint.setContentsMargins(12, 0, 12, 0)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(self.hint)
        column.addStretch(1)
        self.stack.addWidget(empty)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.stack.addWidget(self.image)
        self._frame: QImage | None = None
        self.timer = QTimer(self, interval=PREVIEW_MS)
        self.timer.timeout.connect(self._grab)
        self._resized = QTimer(self, interval=200, singleShot=True)  # One fresh frame once resizing settles.
        self._resized.timeout.connect(self._grab)
        self.restyle()
        self.update_state(controller.studio)

    def restyle(self) -> None:
        p = colors()
        self.brand.setText(f'<span style="font-size:18px; font-weight:700; color:{p.text};">Ignite</span>'
                           f'<span style="font-size:18px; font-weight:700; color:{p.accent};">Live</span>')

    def update_state(self, studio: Studio) -> None:
        if not studio.connected:
            self._frame = None
            self.hint.setText("Connect OBS to preview your program scene here.")
            self.stack.setCurrentIndex(0)
        elif self._frame is None:
            self.hint.setText("Your stream will appear here.")

    def _grab(self) -> None:
        studio = self.controller.studio
        if not studio.connected:
            return
        vertical = studio.vertical.scene if self.vertical and studio.vertical is not None else ""
        self.controller.grab_preview(self._show, width=self._frame_width(), source=vertical or "")

    def _frame_width(self) -> int:
        """Pixels to ask OBS for: as wide as the preview is on screen, within what's worth sending."""
        wanted = round(self.image.width() * self.devicePixelRatioF())
        return max(PREVIEW_MIN_WIDTH, min(PREVIEW_MAX_WIDTH, wanted))

    def _show(self, data: bytes) -> None:
        frame = QImage.fromData(data, "JPG") if data else QImage()
        if frame.isNull():
            return
        self._frame = frame
        self._fit()
        self.stack.setCurrentIndex(1)

    def _fit(self) -> None:
        if self._frame is not None:
            self.image.setPixmap(QPixmap.fromImage(self._frame).scaled(
                self.image.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._fit()
        if self.isVisible() and self._frame is not None:
            self._resized.start()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self.timer.start()
        self._grab()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.timer.stop()
        super().hideEvent(event)


class OverviewPage(QWidget):
    """``open_page_requested``: a shortcut was clicked (a page key, as ``MainWindow.open_page`` takes)."""

    open_page_requested = Signal(str)

    def __init__(self, controller: Controller, actions: AppActions, history) -> None:
        super().__init__()
        self.controller, self.actions = controller, actions
        self._events: list[tuple[float, object]] = []
        self._comments: deque[float] = deque()  # When each chat message arrived, for "Chat per min".
        self._viewer_samples: deque[float] = deque(maxlen=SPARK_POINTS)
        self._gifts = 0
        self._gifters: set[str] = set()
        self._new_follows = 0
        self._scenes_shown: tuple = ()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page = PageContainer()
        outer.addWidget(page)

        # Stat tiles.
        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.viewers = MetricCard("users", "Viewers", "accent")
        self.chat_rate = MetricCard("message-square-more", "Chat per min", "accent")
        self.gifts = MetricCard("gift", "Gifts", "accent")
        self.followers = MetricCard("heart", "New followers", "accent")
        self.chat_rate.set_value("0")
        self.gifts.set_value("0")
        self.followers.set_value("0")
        self.chat_rate.set_trend("Quiet")
        self.gifts.set_trend("Since Ignite opened")
        self.followers.set_trend("Since Ignite opened")
        for tile in (self.viewers, self.chat_rate, self.gifts, self.followers):
            metrics.addWidget(tile, 1)
        page.add_layout(metrics)

        # Chat on the left; the stream, its scenes and the latest events on the right.
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        grid.addWidget(self._chat_card(history), 0, 0, 3, 1)
        grid.addWidget(self._stream_card(), 0, 1)
        grid.addWidget(self._scenes_card(), 1, 1)
        grid.addWidget(self._events_card(), 2, 1)
        grid.setColumnStretch(0, 5)
        grid.setColumnStretch(1, 3)
        grid.setRowStretch(0, 3)
        grid.setRowStretch(2, 2)
        holder = QWidget()
        holder.setLayout(grid)
        page.add(holder, 1)
        page.add(self._command_hint())

        controller.studio_changed.connect(self._studio)
        controller.stats_changed.connect(self._stats)
        controller.chat_event.connect(self._chat_event)
        controller.chats_cleared.connect(self._chats_cleared)
        controller.targets_changed.connect(self._targets)
        controller.settings_changed.connect(lambda _s: self._targets(controller.targets))
        controller.obs_streaming_changed.connect(lambda _live: self._studio(controller.studio))
        controller.tiktok_account_changed.connect(lambda _s: self._composer())
        theme_changed().connect(self._restyle)
        self._clock = QTimer(self, interval=30_000)
        self._clock.timeout.connect(self._render_events)
        self._rate_clock = QTimer(self, interval=5_000)
        self._rate_clock.timeout.connect(self._rate)
        self._studio(controller.studio)
        self._stats(controller.stats_snapshot)
        self._targets(controller.targets)
        self._render_events()
        self._composer()
        self._restyle()

    # Cards.

    def _stream_card(self) -> Card:
        card = self.stream_card = Card("Stream")
        self.preview_state = StatusIndicator("Offline", "muted")
        card.add_header_widget(self.preview_state)
        self.preview = StreamPreview(self.controller)
        self.preview.setMinimumHeight(90)
        card.body.addWidget(self.preview, 1)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.go_live = button("Go Live", "play", kind="primary", tip="Run the pre-stream check, then start OBS's output")
        self.go_live.clicked.connect(lambda: self.actions.go_live())
        self.stop = button("End stream", "square", tip="Stop OBS's output")
        self.stop.clicked.connect(self._stop)
        buttons.addWidget(self.go_live, 1)
        buttons.addWidget(self.stop, 1)
        card.body.addLayout(buttons)
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.addWidget(label("Output", "muted"), 0, 0)
        self.target = QComboBox()
        self.target.setToolTip("The OBS output Ignite starts and puts your stream key on")
        self.target.activated.connect(lambda _i: self.controller.set_stream_target(self.target.currentData()))
        form.addWidget(self.target, 0, 1)
        form.addWidget(label("Status", "muted"), 1, 0)
        self.health = StatusIndicator("–", "muted")
        form.addWidget(self.health, 1, 1)
        form.setColumnStretch(1, 1)
        card.body.addLayout(form)
        return card

    def _scenes_card(self) -> Card:
        card = self.scenes_card = Card("Scenes")
        self.scene_flow = QWidget()
        self.scene_layout = FlowLayout(self.scene_flow)
        self.scene_layout.setSpacing(8)
        card.body.addWidget(self.scene_flow)
        self.scene_empty = label("Connect OBS to switch scenes from here.", "muted", wrap=True)
        card.body.addWidget(self.scene_empty)
        self.scene_buttons: list[QPushButton] = []
        return card

    def _command_hint(self) -> QFrame:
        """The design's wide bar at the bottom: opens the Ctrl+K command bar."""
        bar = self.command_hint = CommandHint()
        bar.clicked.connect(lambda: open_command_bar(self.window()))
        return bar

    def _chat_card(self, history) -> Card:
        card = self.chat_card = Card("Chat", padded=False)
        self.popout = icon_button("external-link", "Pop out chat")
        self.popout.clicked.connect(self.actions.popout_chat.trigger)
        self.chat_more = icon_button("ellipsis-vertical", "Chat actions")
        self.chat_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.chat_more)
        menu.addAction("Open Live Chat", lambda: self.open_page_requested.emit("chat"))
        menu.addSeparator()
        for action in (self.actions.connect_chat, self.actions.disconnect_chat, self.actions.clear_chats):
            menu.addAction(action)
        self.chat_more.setMenu(menu)
        card.add_header_widget(self.popout)
        card.add_header_widget(self.chat_more)
        self.feed = ChatFeed(compact=True)
        for event in history:
            self.feed.add_event(event)
        self.chat_empty = EmptyState("message-square-more", "No messages yet",
                                     "Chat will appear here when your stream goes live.")
        self.chat_stack = QStackedWidget()
        self.chat_stack.addWidget(self.chat_empty)
        self.chat_stack.addWidget(self.feed)
        self.chat_stack.setCurrentWidget(self.feed if self.feed.count() else self.chat_empty)
        card.body.addWidget(self.chat_stack, 1)
        composer = QWidget()
        bar = QHBoxLayout(composer)
        bar.setContentsMargins(12, 8, 12, 12)
        bar.setSpacing(8)
        self.message = QLineEdit()
        self.message.setMaxLength(150)
        self.message.setAccessibleName("Message to TikTok chat")
        self.message.returnPressed.connect(self._send)
        self.send = button("", "send", kind="primary", tip="Send to your TikTok LIVE chat")
        self.send.setFixedWidth(40)
        self.send.clicked.connect(self._send)
        bar.addWidget(self.message, 1)
        bar.addWidget(self.send)
        card.body.addWidget(composer)
        return card

    def _events_card(self) -> Card:
        card = self.events_card = Card("Recent events")
        view_all = QPushButton("View all")
        view_all.setProperty("ghost", True)
        view_all.setCursor(Qt.CursorShape.PointingHandCursor)
        view_all.clicked.connect(lambda: self.open_page_requested.emit("actions:activity"))
        card.add_header_widget(view_all)
        self.events_list = QVBoxLayout()
        self.events_list.setSpacing(0)
        card.body.addLayout(self.events_list)
        self.events_empty = EmptyState("star", "Nothing yet", "Follows, gifts and shares appear here.")
        self.events_empty.layout().setContentsMargins(16, 6, 16, 6)
        card.body.addWidget(self.events_empty, 1)
        card.body.addStretch(1)
        return card

    # Updates.

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._clock.start()
        self._rate_clock.start()
        self._rate()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._clock.stop()
        self._rate_clock.stop()
        super().hideEvent(event)

    def _stop(self) -> None:
        answer = QMessageBox.question(self, "Stop streaming", "Stop OBS's output? Your viewers will see the stream end.")
        if answer == QMessageBox.StandardButton.Yes:
            self.controller.set_obs_streaming(False)

    def _studio(self, studio: Studio) -> None:
        live = studio.streaming
        self.go_live.setEnabled(studio.connected and not live)
        self.stop.setEnabled(studio.connected and live)
        set_live(self.stop, live)
        restyle_buttons(self.go_live, self.stop)
        if not studio.connected:
            self.preview_state.set("OBS offline", "muted")
            self.health.set("OBS isn't connected", "muted")
        else:
            self.preview_state.set("Live" if live else "Preview", "live" if live else "healthy")
            health = studio.health
            if not live:
                self.health.set("Not streaming", "muted")
            elif health.reconnecting:
                self.health.set("Reconnecting…", "warning")
            else:
                self.health.set(f"Live {clock(health.stream_duration_ms)}", "healthy")
        self.preview.update_state(studio)
        self._scenes(studio)

    def _scenes(self, studio: Studio) -> None:
        shown = (studio.scenes, studio.scene) if studio.connected else ()
        if shown == self._scenes_shown:
            return
        self._scenes_shown = shown
        for widget in self.scene_buttons:
            self.scene_layout.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        self.scene_buttons = []
        for name in studio.scenes if studio.connected else ():
            current = name == studio.scene
            scene = button(name, kind="primary" if current else "", tip=f"Switch OBS to {name}")
            scene.setCheckable(False)
            scene.clicked.connect(lambda _=False, n=name: self.controller.switch_scene(n))
            self.scene_layout.addWidget(scene)
            self.scene_buttons.append(scene)
        self.scene_empty.setVisible(not self.scene_buttons)
        self.scene_empty.setText("Connect OBS to switch scenes from here." if not studio.connected
                                 else "OBS has no scenes yet.")
        restyle_buttons(*self.scene_buttons)

    def _stats(self, stats: dict) -> None:
        total = stats["total"]
        count = total.get("viewers")
        self.viewers.set_value(compact(count))
        if isinstance(count, (int, float)):
            self._viewer_samples.append(float(count))
        self.viewers.set_series(list(self._viewer_samples))

    def _rate(self) -> None:
        """Chat messages in the last minute, and a word for how busy that is."""
        cutoff = time.time() - RATE_WINDOW
        while self._comments and self._comments[0] < cutoff:
            self._comments.popleft()
        count = len(self._comments)
        self.chat_rate.set_value(str(count))
        self.chat_rate.set_trend("Busy" if count >= 60 else "Steady" if count >= 15 else "Quiet")

    def _targets(self, targets) -> None:
        chosen = self.controller.settings.stream_target
        self.target.blockSignals(True)
        self.target.clear()
        for target in targets:
            self.target.addItem(target.label, target.id)
        index = self.target.findData(chosen)
        if index < 0 and chosen:
            self.target.addItem(f"{chosen} (not in OBS)", chosen)
            index = self.target.count() - 1
        if not self.target.count():
            self.target.addItem("Open OBS to choose", "")
        self.target.setCurrentIndex(max(0, index))
        self.target.blockSignals(False)

    def _chat_event(self, event) -> None:
        self._chat_line(event)
        if isinstance(event, ChatComment):
            self._comments.append(time.time())
            self._rate()
        if not isinstance(event, RECENT_KINDS) or getattr(event, "streaking", False):
            return
        who = event.user.unique_id or event.user.nickname
        if isinstance(event, ChatGift):
            self._gifts += max(1, event.count)
            self._gifters.add(who)
            self.gifts.set_value(compact(self._gifts))
            self.gifts.set_trend(f"{len(self._gifters)} {'person' if len(self._gifters) == 1 else 'people'}")
        elif isinstance(event, ChatFollow):
            self._new_follows += 1
            self.followers.set_value(f"+{compact(self._new_follows)}")
        self._events.insert(0, (time.time(), event))
        del self._events[MAX_EVENTS:]
        self._render_events()

    def _chat_line(self, event) -> None:
        self.feed.add_event(event)
        if self.feed.count():
            self.chat_stack.setCurrentWidget(self.feed)

    def _chats_cleared(self) -> None:
        self.feed.clear()
        self.chat_stack.setCurrentWidget(self.chat_empty)
        self._comments.clear()
        self._rate()

    def _render_events(self) -> None:
        while self.events_list.count():
            item = self.events_list.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.events_empty.setVisible(not self._events)
        for index, (when, event) in enumerate(self._events):
            if index:
                self.events_list.addWidget(hline())
            icon, tone, title, detail = event_text(event)
            self.events_list.addWidget(EventRow(icon, tone, title, detail, when))

    def _composer(self) -> None:
        tiktok = self.controller.tiktok_account
        ready = bool(tiktok.get("connected"))
        self.message.setEnabled(ready)
        self.send.setEnabled(ready)
        self.message.setPlaceholderText("Send a message…" if ready else "Sign in to TikTok in your Ignite account to chat")

    def _send(self) -> None:
        if self.controller.send_tiktok_chat(self.message.text()):
            self.message.clear()

    def _restyle(self, _palette=None) -> None:
        for card in (self.stream_card, self.scenes_card, self.chat_card, self.events_card):
            card.restyle()
        for tile in (self.viewers, self.chat_rate, self.gifts, self.followers):
            tile.restyle()
        self.preview.restyle()
        self.chat_empty.restyle()
        self.events_empty.restyle()
        recolor_icon_button(self.popout)
        recolor_icon_button(self.chat_more)
        restyle_buttons(self.go_live, self.stop, self.send, *self.scene_buttons)
