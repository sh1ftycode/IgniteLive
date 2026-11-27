"""Overview: the stream at a glance.

Metrics, a live preview of OBS's program scene, the go-live controls, the chat, a chart of your last streams,
the latest follows, gifts and shares, and shortcuts. Every number is one Ignite already tracks; what isn't
available says so (OBS offline, no streams yet) instead of showing zeros that look real.
"""

from __future__ import annotations

import time

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
from ignite_live.core.events import ChatFollow, ChatGift, ChatShare
from ignite_live.integrations.obs_studio import Studio, clock
from ignite_live.live import analytics
from ignite_live.live.audience import compact
from ignite_live.ui.actions import AppActions
from ignite_live.ui.chat_feed import ChatFeed
from ignite_live.ui.icons import logo
from ignite_live.ui.kit import (
    Card,
    EmptyState,
    EventRow,
    LineChart,
    Legend,
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
RECENT_KINDS = (ChatFollow, ChatGift, ChatShare)
MAX_EVENTS = 5
RANGES = ((7, "Last 7 days"), (14, "Last 14 days"), (30, "Last 30 days"))


def event_text(event) -> tuple[str, str, str, str]:
    """(icon, tone, title, detail) for a follow, gift or share."""
    name = event.user.nickname or event.user.unique_id
    if isinstance(event, ChatFollow):
        return "heart", "error", "New follower", f"{name} followed you"
    if isinstance(event, ChatGift):
        count = f" ×{event.count}" if event.count > 1 else ""
        return "gift", "warning", "New gift", f"{name} sent {event.gift_name}{count}"
    return "share", "accent", "New share", f"{name} shared your LIVE"


def daily_report(controller: Controller, days: int) -> tuple[list[str], list[float], list[float]]:
    """Average viewers and new follows for each of the last ``days`` days, from the saved stream reports."""
    now = time.time()
    keys = [time.strftime("%Y-%m-%d", time.localtime(now - offset * 86400)) for offset in range(days - 1, -1, -1)]
    views = dict.fromkeys(keys, 0.0)
    follows = dict.fromkeys(keys, 0.0)
    for path in controller.saved_streams():
        data = analytics.load(path)
        if data is None or data.get("started", 0) < now - days * 86400:
            continue
        key = time.strftime("%Y-%m-%d", time.localtime(data["started"]))
        if key in views:
            summary = analytics.summary(data)
            views[key] += summary["average_viewers"]
            follows[key] += summary["follows"]
    labels = []
    for key in keys:
        day = time.strptime(key, "%Y-%m-%d")
        labels.append(f"{time.strftime('%b', day)} {day.tm_mday}")
    return labels, list(views.values()), list(follows.values())


class StreamPreview(QFrame):
    """OBS's program scene as it looks right now, refreshed while visible; an honest empty state otherwise."""

    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        self.setObjectName("inset")
        self.setMinimumHeight(240)
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
        mark.setPixmap(QPixmap.fromImage(logo(56)))
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(mark)
        self.brand = QLabel()
        self.brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(self.brand)
        self.hint = label("", "muted", wrap=True)
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
        self.restyle()
        self.update_state(controller.studio)

    def restyle(self) -> None:
        p = colors()
        self.brand.setText(f'<span style="font-size:28px; font-weight:700; color:{p.text};">Ignite</span>'
                           f'<span style="font-size:28px; font-weight:700; color:{p.accent};">Live</span>')

    def update_state(self, studio: Studio) -> None:
        if not studio.connected:
            self._frame = None
            self.hint.setText("Connect OBS to see your program scene here (Settings → Streaming).")
            self.stack.setCurrentIndex(0)
        elif self._frame is None:
            self.hint.setText("Your stream will appear here.")

    def _grab(self) -> None:
        studio = self.controller.studio
        if not studio.connected:
            return
        vertical = studio.vertical.scene if self.vertical and studio.vertical is not None else ""
        self.controller.grab_preview(self._show, source=vertical or "")

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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page = PageContainer()
        outer.addWidget(page)

        # Metrics.
        metrics = QHBoxLayout()
        metrics.setSpacing(16)
        self.viewers = MetricCard("users", "Viewers", "accent")
        self.uptime = MetricCard("clock", "Stream Uptime", "muted")
        self.followers = MetricCard("heart", "Followers", "live")
        self.subscribers = MetricCard("star", "Subscribers", "warning")
        self.subscribers.setToolTip("Subscribers and members in your audience right now (Twitch, YouTube)")
        for tile in (self.viewers, self.uptime, self.followers, self.subscribers):
            metrics.addWidget(tile, 1)
        page.add_layout(metrics)

        # Preview, controls, chat.
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)
        grid.addWidget(self._preview_card(), 0, 0)
        grid.addWidget(self._control_card(), 0, 1)
        grid.addWidget(self._chat_card(history), 0, 2)
        grid.addWidget(self._analytics_card(), 1, 0)
        grid.addWidget(self._events_card(), 1, 1)
        grid.addWidget(self._actions_card(), 1, 2)
        grid.setColumnStretch(0, 5)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(2, 3)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)
        holder = QWidget()
        holder.setLayout(grid)
        page.add(holder, 1)

        controller.studio_changed.connect(self._studio)
        controller.stats_changed.connect(self._stats)
        controller.audience_changed.connect(self._audience)
        controller.chat_event.connect(self._chat_event)
        controller.chats_cleared.connect(self._chats_cleared)
        controller.stream_report_ready.connect(lambda _path: self._fill_chart())
        controller.targets_changed.connect(self._targets)
        controller.settings_changed.connect(lambda _s: self._targets(controller.targets))
        controller.obs_streaming_changed.connect(lambda _live: self._studio(controller.studio))
        controller.tiktok_account_changed.connect(lambda _s: self._composer())
        theme_changed().connect(self._restyle)
        self._clock = QTimer(self, interval=30_000)
        self._clock.timeout.connect(self._render_events)
        self._studio(controller.studio)
        self._stats(controller.stats_snapshot)
        self._audience(controller.audience_snapshot)
        self._targets(controller.targets)
        self._fill_chart()
        self._render_events()
        self._composer()
        self._restyle()

    # Cards.

    def _preview_card(self) -> Card:
        card = self.preview_card = Card("Stream Preview", icon="circle-dot")
        self.preview_state = StatusIndicator("Offline", "muted")
        card.add_header_widget(self.preview_state)
        self.canvas = QComboBox()
        self.canvas.addItem("Main canvas", False)
        self.canvas.setToolTip("Which canvas to preview")
        self.canvas.currentIndexChanged.connect(lambda _i: setattr(self.preview, "vertical",
                                                                   bool(self.canvas.currentData())))
        card.add_header_widget(self.canvas)
        self.preview = StreamPreview(self.controller)
        card.body.addWidget(self.preview, 1)
        return card

    def _control_card(self) -> Card:
        card = self.control_card = Card("Stream Control", icon="radio")
        self.go_live = button("Go Live", "play", kind="primary", tip="Run the pre-stream check, then start OBS's output")
        self.go_live.setProperty("big", True)
        self.go_live.clicked.connect(lambda: self.actions.go_live())
        self.stop = button("Stop Stream", "square", tip="Stop OBS's output")
        self.stop.setProperty("big", True)
        self.stop.clicked.connect(self._stop)
        card.body.addWidget(self.go_live)
        card.body.addWidget(self.stop)
        card.body.addSpacing(4)
        card.body.addWidget(hline())
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(12)
        form.addWidget(label("Output", "muted"), 0, 0)
        self.target = QComboBox()
        self.target.setToolTip("The OBS output Ignite starts and puts your stream key on")
        self.target.activated.connect(lambda _i: self.controller.set_stream_target(self.target.currentData()))
        form.addWidget(self.target, 0, 1)
        form.addWidget(label("Stream Health", "muted"), 1, 0)
        self.health = StatusIndicator("–", "muted")
        form.addWidget(self.health, 1, 1)
        form.setColumnStretch(1, 1)
        card.body.addLayout(form)
        card.body.addStretch(1)
        return card

    def _chat_card(self, history) -> Card:
        card = self.chat_card = Card("Live Chat", icon="message-square-more", padded=False)
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

    def _analytics_card(self) -> Card:
        card = self.analytics_card = Card("Analytics", icon="chart-column")
        self.range = QComboBox()
        for days, text in RANGES:
            self.range.addItem(text, days)
        self.range.currentIndexChanged.connect(lambda _i: self._fill_chart())
        card.add_header_widget(self.range)
        legend_row = QHBoxLayout()
        legend_row.addStretch(1)
        self.legend = Legend([("Avg. viewers", "accent"), ("New followers", "healthy")])
        legend_row.addWidget(self.legend)
        card.body.addLayout(legend_row)
        self.chart = LineChart("No streams in this period yet. A report is saved each time you stream.")
        card.body.addWidget(self.chart, 1)
        return card

    def _events_card(self) -> Card:
        card = self.events_card = Card("Recent Events", icon="star")
        view_all = QPushButton("View all")
        view_all.setProperty("ghost", True)
        view_all.setCursor(Qt.CursorShape.PointingHandCursor)
        view_all.clicked.connect(lambda: self.open_page_requested.emit("actions:activity"))
        card.add_header_widget(view_all)
        self.events_list = QVBoxLayout()
        self.events_list.setSpacing(0)
        card.body.addLayout(self.events_list)
        self.events_empty = EmptyState("star", "Nothing yet", "Follows, gifts and shares show up here while you're live.")
        card.body.addWidget(self.events_empty, 1)
        card.body.addStretch(1)
        return card

    def _actions_card(self) -> Card:
        card = self.quick_card = Card("Quick Actions", icon="zap")
        self.quick = []
        for key, icon, text, kind in (("overlays", "video", "Manage Overlays", "outline"),
                                      ("streams", "chart-column", "View Analytics", ""),
                                      ("actions", "bell", "Configure Alerts", ""),
                                      ("checklist", "circle-check", "Check Before Going Live", "")):
            widget = button(text, icon, kind=kind)
            widget.setProperty("big", True)
            widget.clicked.connect(lambda _=False, k=key: self.open_page_requested.emit(k))
            card.body.addWidget(widget)
            self.quick.append(widget)
        card.body.addStretch(1)
        return card

    # Updates.

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._clock.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._clock.stop()
        super().hideEvent(event)

    def _stop(self) -> None:
        answer = QMessageBox.question(self, "Stop streaming", "Stop OBS's output? Your viewers will see the stream end.")
        if answer == QMessageBox.StandardButton.Yes:
            self.controller.set_obs_streaming(False)

    def _studio(self, studio: Studio) -> None:
        live = studio.streaming
        self.uptime.set_value(clock(studio.health.stream_duration_ms) if live else "00:00")
        self.uptime.set_live(live)
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
            bad = live and (health.reconnecting or health.network_dropped_pct >= 1 or health.render_lag_pct >= 1)
            if not live:
                self.health.set("Not streaming", "muted")
            elif bad:
                self.health.set(f"Trouble · {health.network_dropped_pct:.1f}% dropped", "warning")
            else:
                self.health.set(f"Healthy · {health.bitrate_kbps:,} kbps", "healthy")
        has_vertical = studio.vertical is not None
        if has_vertical and self.canvas.count() == 1:
            self.canvas.addItem("Vertical canvas", True)
        elif not has_vertical and self.canvas.count() > 1:
            self.canvas.removeItem(1)
        self.canvas.setVisible(has_vertical)
        self.preview.update_state(studio)

    def _stats(self, stats: dict) -> None:
        total = stats["total"]
        self.viewers.set_value(compact(total.get("viewers")))
        self.followers.set_value(compact(total.get("followers")))

    def _audience(self, people: list) -> None:
        self.subscribers.set_value(compact(sum(1 for row in people if row["user"].is_subscriber)))

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

    def _fill_chart(self) -> None:
        labels, views, follows = daily_report(self.controller, self.range.currentData() or 7)
        has_data = any(views) or any(follows)
        p = colors()
        self.chart.set_data(labels if has_data else [], [("Avg. viewers", p.accent, views),
                                                         ("New followers", p.healthy, follows)])

    def _chat_event(self, event) -> None:
        if not isinstance(event, RECENT_KINDS) or getattr(event, "streaking", False):
            self._chat_line(event)
            return
        self._chat_line(event)
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
        self.message.setPlaceholderText("Send a message…" if ready else "Sign in to TikTok on your server to chat")

    def _send(self) -> None:
        if self.controller.send_tiktok_chat(self.message.text()):
            self.message.clear()

    def _restyle(self, _palette=None) -> None:
        for card in (self.preview_card, self.control_card, self.chat_card, self.analytics_card, self.events_card,
                     self.quick_card):
            card.restyle()
        for tile in (self.viewers, self.uptime, self.followers, self.subscribers):
            tile.restyle()
        self.preview.restyle()
        self.chat_empty.restyle()
        self.events_empty.restyle()
        recolor_icon_button(self.popout)
        recolor_icon_button(self.chat_more)
        restyle_buttons(self.go_live, self.stop, self.send, *self.quick)
        self._fill_chart()
