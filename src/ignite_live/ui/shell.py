"""The window's header: the screen's title and description, then connection status, streaming status, Panic,
notifications and the profile menu.

Everything it shows comes from the controller's real state: a platform counts as connected only when its
chat says so, and "Live" only shows while OBS reports the output streaming.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ignite_live.core.events import PLATFORMS, ConnectionState
from ignite_live.integrations.obs_studio import Studio, clock
from ignite_live.ui import lucide
from ignite_live.ui.kit import Pill, icon_label, label, vline
from ignite_live.ui.sidebar import Avatar
from ignite_live.ui.theme import colors, set_props, theme_changed

MAX_NOTIFICATIONS = 500


def connection_summary(statuses: dict) -> tuple[str, str, str]:
    """(text, tone, tooltip) for the chat platforms: which are connected, waiting, or failing."""
    connected = [PLATFORMS[p] for p in PLATFORMS if statuses[p].state is ConnectionState.CONNECTED]
    waiting = [PLATFORMS[p] for p in PLATFORMS
               if statuses[p].state in (ConnectionState.CONNECTING, ConnectionState.DEGRADED)]
    failing = [PLATFORMS[p] for p in PLATFORMS if statuses[p].state is ConnectionState.ERROR]
    lines = [f"{PLATFORMS[p]}: {statuses[p].state.value}" + (f" ({statuses[p].detail})" if statuses[p].detail else "")
             for p in PLATFORMS]
    tip = "\n".join(lines)
    if failing:
        return f"{failing[0]} problem", "error", tip
    if connected:
        return (f"Connected to {connected[0]}" if len(connected) == 1 else f"{len(connected)} platforms connected"), \
            "healthy", tip
    if waiting:
        return f"Connecting to {waiting[0]}…", "warning", tip
    return "Chat not connected", "muted", tip


def server_summary(configured: bool, up: bool, user: str = "") -> tuple[str, str, str]:
    """(text, tone, tooltip) for the Ignite account and server."""
    if not configured:
        return "Sign in", "accent", ("Sign in to your Ignite account: your stream key is kept for 48 hours, settings "
                                     "sync between PCs, you get song search, hosted overlay links, and you can run "
                                     "Ignite from a browser or phone. Click to sign in.")
    who = f"@{user}" if user else "your Ignite account"
    if up:
        return (f"@{user}" if user else "Server"), "healthy", f"Signed in as {who}. The server is answering."
    return "Server offline", "warning", (f"Ignite's servers aren't answering for {who}. Overlay links use this PC until they "
                                         "are back; Ignite keeps checking.")


def stream_summary(studio: Studio) -> tuple[str, str, str]:
    """(text, tone, tooltip) for OBS's main output."""
    if not studio.connected:
        return "OBS offline", "muted", "Ignite isn't connected to OBS. Check Settings → Streaming."
    health = studio.health
    if studio.streaming:
        bad = health.reconnecting
        tip = f"Live for {clock(health.stream_duration_ms)}"
        return f"Live · {clock(health.stream_duration_ms)}", "warning" if bad else "live", tip
    return "Offline", "muted", "OBS is connected and not streaming."


class BellButton(QToolButton):
    """The notifications button, with the unread count on it."""

    def __init__(self) -> None:
        super().__init__()
        self.unread = 0
        self.setProperty("iconOnly", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Notifications")
        self.setAccessibleName("Notifications")
        self.setIconSize(QSize(20, 20))
        self.setFixedSize(36, 36)
        self.restyle()

    def set_unread(self, count: int) -> None:
        self.unread = count
        self.setAccessibleDescription(f"{count} unread" if count else "")
        self.update()

    def restyle(self) -> None:
        self.setIcon(lucide.icon("bell", colors().text_muted, 20))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        if not self.unread:
            return
        p = colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        text = "9+" if self.unread > 9 else str(self.unread)
        badge = QRectF(self.width() - 17, 3, 15, 15)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(p.live))
        painter.drawEllipse(badge)
        font = QFont(self.font())
        font.setPixelSize(9)
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QColor(p.on_live))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)


class NotificationCenter(QFrame):
    """Everything Ignite has told you this session (what the old Activity panel listed), newest first, in a
    popover under the bell."""

    unread_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("popover")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedSize(380, 440)
        self.entries: list[tuple[float, str, str]] = []
        self.unread = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(16, 12, 10, 10)
        head.addWidget(label("Notifications", "cardTitle"))
        head.addStretch(1)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setProperty("ghost", True)
        self.clear_button.clicked.connect(self.clear)
        head.addWidget(self.clear_button)
        layout.addLayout(head)
        line = QFrame()
        line.setObjectName("hline")
        layout.addWidget(line)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_holder = QWidget()
        self.list_layout = QVBoxLayout(self.list_holder)
        self.list_layout.setContentsMargins(8, 8, 8, 8)
        self.list_layout.setSpacing(2)
        self.empty = label("You're all caught up.", "muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.list_layout.addWidget(self.empty)
        self.list_layout.addStretch(1)
        scroll.setWidget(self.list_holder)
        layout.addWidget(scroll, 1)

    def add(self, level: str, text: str) -> None:
        self.entries.append((time.time(), level, text))
        del self.entries[:-MAX_NOTIFICATIONS]
        if not self.isVisible():
            self.unread += 1
            self.unread_changed.emit(self.unread)
        else:
            self._render()

    def clear(self) -> None:
        self.entries.clear()
        self._render()

    def open_below(self, anchor: QWidget) -> None:
        self.unread = 0
        self.unread_changed.emit(0)
        self._render()
        point = anchor.mapToGlobal(QPoint(anchor.width() - self.width(), anchor.height() + 6))
        self.move(point)
        self.show()

    def _render(self) -> None:
        while self.list_layout.count() > 2:
            item = self.list_layout.takeAt(1)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.empty.setVisible(not self.entries)
        self.clear_button.setEnabled(bool(self.entries))
        p = colors()
        for when, level, text in reversed(self.entries[-150:]):
            entry = QWidget()
            row = QHBoxLayout(entry)
            row.setContentsMargins(8, 8, 8, 8)
            row.setSpacing(10)
            row.addWidget(icon_label("circle-x" if level == "error" else "info",
                                     p.live if level == "error" else p.accent, 16), 0, Qt.AlignmentFlag.AlignTop)
            message = label(text, wrap=True)
            message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(message, 1)
            row.addWidget(label(time.strftime("%H:%M", time.localtime(when)), "muted"), 0, Qt.AlignmentFlag.AlignTop)
            self.list_layout.insertWidget(self.list_layout.count() - 1, entry)


class ProfileButton(QToolButton):
    """The round initial in the header's corner; its menu holds the app-wide commands."""

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("iconOnly", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setFixedSize(40, 40)
        self.setToolTip("Account and app")
        self.setAccessibleName("Account and app menu")
        self.avatar = Avatar(32)
        self.avatar.setParent(self)
        self.avatar.move(4, 4)
        self.avatar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_name(self, name: str) -> None:
        self.avatar.set_name(name)


class AppHeader(QFrame):
    """``status_clicked``: the streaming pill (MainWindow opens Stream Control). ``chat_clicked``: the
    connection pill (opens Integrations)."""

    status_clicked = Signal()
    chat_clicked = Signal()
    account_clicked = Signal()  # The account pill: sign in, or see which server this is.

    def __init__(self, panic_action) -> None:
        super().__init__()
        self.setObjectName("appHeader")
        self.setFixedHeight(72)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 10, 16, 10)
        layout.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title = label("", "pageTitle")
        self.subtitle = label("", "pageSubtitle")
        titles.addStretch(1)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        titles.addStretch(1)
        layout.addLayout(titles, 1)

        self.connection = Pill("Chat not connected")
        self.connection.clicked.connect(self.chat_clicked.emit)
        layout.addWidget(self.connection, 0, Qt.AlignmentFlag.AlignVCenter)
        self.stream = Pill("OBS offline")
        self.stream.clicked.connect(self.status_clicked.emit)
        layout.addWidget(self.stream, 0, Qt.AlignmentFlag.AlignVCenter)
        self.server = Pill("Sign in")  # Your Ignite account and server: always here, so it is easy to find.
        self.server.clicked.connect(self.account_clicked.emit)
        layout.addWidget(self.server, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(vline())

        # Panic is never tucked into a menu: one click, always in the same place.
        self.panic_action = panic_action
        self.panic = QPushButton("Panic")
        self.panic.setProperty("danger", True)
        self.panic.setCursor(Qt.CursorShape.PointingHandCursor)
        self.panic.setToolTip(panic_action.toolTip())
        self.panic.clicked.connect(panic_action.trigger)
        panic_action.changed.connect(self._panic_changed)
        layout.addWidget(self.panic, 0, Qt.AlignmentFlag.AlignVCenter)

        self.bell = BellButton()
        layout.addWidget(self.bell, 0, Qt.AlignmentFlag.AlignVCenter)
        self.profile = ProfileButton()
        layout.addWidget(self.profile, 0, Qt.AlignmentFlag.AlignVCenter)
        theme_changed().connect(self._restyle)
        self._panic_changed()

    def set_page(self, title: str, subtitle: str) -> None:
        self.title.setText(title)
        self.subtitle.setText(subtitle)

    def set_connections(self, statuses: dict) -> None:
        text, tone, tip = connection_summary(statuses)
        self.connection.set(text, tone, tip + "\n\nClick for Integrations.")

    def set_server(self, configured: bool, up: bool, user: str = "") -> None:
        self.server.set(*server_summary(configured, up, user))

    def set_stream(self, studio: Studio) -> None:
        text, tone, tip = stream_summary(studio)
        self.stream.set(text, tone, tip + "\n\nClick for Stream Control.")

    def _panic_changed(self) -> None:
        on = self.panic_action.text().replace("&", "") == "Resume"
        self.panic.setText("Resume" if on else "Panic")
        self.panic.setToolTip(self.panic_action.toolTip() + "  (Ctrl+Shift+P)")
        set_props(self.panic, danger=not on, live=on)
        self.panic.setIcon(lucide.icon("octagon-alert", colors().on_live if on else colors().live, 16))

    def _restyle(self, _palette=None) -> None:
        self.bell.restyle()
        self._panic_changed()
