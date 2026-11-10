"""Pieces shared by the Chat panel, the pop-out chat and the Music tab: the stats row (viewers, followers
and likes per platform), the list of who's here, and the music buttons.

Each can be made ``see_through`` for the pop-out, which floats over a game: nothing then relies on a panel
behind it. Numbers sit on small backed pills and list text gets an edge.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QSizePolicy,
    QSlider,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import Controller
from ignite_live.core.events import PLATFORMS, ChatUser, ConnectionState, LiveStatus
from ignite_live.live.audience import IDLE_SECONDS, compact
from ignite_live.ui.chat_feed import ChatDelegate, image_cache, make_see_through
from ignite_live.ui.flow_layout import FlowLayout
from ignite_live.ui.icons import glyph, glyph_pixmap
from ignite_live.ui.theme import RADIUS, colors, tabular, theme_changed, tint, tint_color

USER_ROLE = Qt.ItemDataRole.UserRole
STATE_ROLE = Qt.ItemDataRole.UserRole + 1
MAX_ROWS = 500  # Rows drawn in the viewer list; the header still counts everyone.
# What each platform calls its numbers.
FOLLOWER_WORD = {"tiktok": "followers", "twitch": "followers", "youtube": "subscribers"}
SOURCES = {
    "tiktok": "TikTok: viewers and likes come with the chat; followers from the LIVE's details (not available "
              "for age-restricted streams).",
    "twitch": "Twitch: viewers and followers from DecAPI (decapi.me), checked once a minute. Turn it off in "
              "Preferences → Chat platforms.",
    "youtube": "YouTube: watching now, likes and subscribers from the stream's page, checked every 30 seconds. "
               "YouTube rounds subscribers.",
}


def clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def pill_style(see_through: bool) -> str:
    """Stylesheet for a small rounded backing: solid enough to read numbers on over a game."""
    p = colors()
    background = tint(p.panel, 0.82) if see_through else p.raised
    return f"background: {background}; border-radius: {RADIUS}px; padding: 2px 8px;"


class Chip(QWidget):
    """A small rounded pill of short texts and icons, e.g. "TikTok (eye) 1,234 (people) 48.2K".

    Icons rather than words keep it narrow enough for two to share a row of the pop-out; the tooltip
    spells the numbers out.
    """

    ICON = 14
    PAD = 8

    def __init__(self, see_through: bool = False) -> None:
        super().__init__()
        self.see_through = see_through
        self.parts: list[tuple[str, str, str, bool]] = []  # ("text" or "icon", text or glyph name, colour, bold)
        self.setFont(tabular(self.font()))  # The numbers change; their digits stay put.
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_parts(self, parts: list[tuple[str, str, str, bool]]) -> None:
        if parts != self.parts:
            self.parts = parts
            self.updateGeometry()
            self.update()

    def _font(self, bold: bool) -> QFont:
        font = QFont(self.font())
        font.setBold(bold)
        return font

    def _advance(self, part: tuple[str, str, str, bool]) -> tuple[int, int]:
        """(width, gap after it): an icon sits close to the number it belongs to."""
        kind, value, _color, bold = part
        if kind == "icon":
            return self.ICON, 3
        return QFontMetrics(self._font(bold)).horizontalAdvance(value), 8

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        sizes = [self._advance(part) for part in self.parts]
        width = sum(width + gap for width, gap in sizes) - (sizes[-1][1] if sizes else 0)
        return QSize(width + 2 * self.PAD, max(self.fontMetrics().height(), self.ICON) + 6)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return self.sizeHint()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        p = colors()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tint_color(p.panel, 0.82) if self.see_through else QColor(p.raised))
        painter.drawRoundedRect(QRectF(self.rect()), RADIUS, RADIUS)
        x, height = float(self.PAD), self.height()
        for part in self.parts:
            kind, value, color, bold = part
            width, gap = self._advance(part)
            if kind == "icon":
                painter.drawPixmap(QPointF(x, (height - self.ICON) / 2), glyph_pixmap(value, color, self.ICON))
            else:
                painter.setFont(self._font(bold))
                painter.setPen(QColor(color))
                painter.drawText(QRectF(x, 0, width + 2, height), Qt.AlignmentFlag.AlignVCenter, value)
            x += width + gap


STAT_ICONS = (("viewers", "eye", "watching"), ("followers", "people", "followers"), ("likes", "heart", "likes"))


class StatsBar(QWidget):
    """Live or not with the totals, then a chip per connected platform: viewers, followers (YouTube:
    subscribers) and likes. Wraps when narrow."""

    def __init__(self, controller: Controller, see_through: bool = False) -> None:
        super().__init__()
        self.controller = controller
        self.see_through = see_through
        layout = FlowLayout(self)
        layout.setSpacing(4)
        self.total = Chip(see_through)
        layout.addWidget(self.total)
        self.pills: dict[str, Chip] = {}
        for platform in PLATFORMS:
            pill = Chip(see_through)
            layout.addWidget(pill)
            self.pills[platform] = pill
        # Bound methods, not lambdas: Qt disconnects them by itself when this widget is deleted (the pop-out
        # is, each time it closes).
        controller.stats_changed.connect(self.show_stats)
        controller.status_changed.connect(self._refresh)
        controller.chat_event.connect(self._chat_event)
        theme_changed().connect(self._restyle)
        self._restyle()

    def _refresh(self, _change=None) -> None:
        self.show_stats(self.controller.stats_snapshot)

    def _chat_event(self, event) -> None:
        if isinstance(event, LiveStatus):  # Twitch went live or offline: the "LIVE" label follows.
            self._refresh()

    def _restyle(self, _palette=None) -> None:
        for chip in (self.total, *self.pills.values()):
            chip.parts = []  # Their colours are the old mode's: build them again.
        self._refresh()

    @staticmethod
    def _numbers(row: dict, likes: bool = True) -> list[tuple[str, str, str, bool]]:
        p, parts = colors(), []
        for key, icon, _word in STAT_ICONS:
            if row.get(key) is not None and (likes or key != "likes"):
                parts += [("icon", icon, p.text_muted, False), ("text", compact(row[key]), p.text, False)]
        return parts

    @staticmethod
    def _spelled_out(row: dict, follower_word: str = "followers") -> str:
        """The exact numbers, for the tooltip: "1,234 watching · 48,200 followers"."""
        words = {"followers": follower_word}
        return " · ".join(f"{row[key]:,} {words.get(key, word)}" for key, _icon, word in STAT_ICONS
                          if row.get(key) is not None)

    def show_stats(self, stats: dict) -> None:
        p, controller = colors(), self.controller
        live = [name for platform, name in PLATFORMS.items() if controller.live.get(platform)]
        head = ("text", "● LIVE", p.live, True) if live else ("text", "Not live", p.text_muted, False)
        self.total.set_parts([head, *self._numbers(stats["total"])])
        exact = self._spelled_out(stats["total"])
        if live:
            self.total.setToolTip(f"Live on {', '.join(live)}." + (f"\nAll platforms together: {exact}" if exact else ""))
        else:
            self.total.setToolTip("Not live. Connect a platform in the Chat panel; counts appear while you're live."
                                  + (f"\n{exact}" if exact else ""))
        for platform, pill in self.pills.items():
            row = stats["platforms"][platform]
            connected = controller.statuses[platform].state is not ConnectionState.OFFLINE
            # Over a game there's room for two numbers a platform; its likes are in the total and the tooltip.
            numbers = self._numbers(row, likes=not self.see_through)
            pill.setVisible(connected or bool(numbers))
            pill.set_parts([("text", PLATFORMS[platform], getattr(p, platform), True),
                            *(numbers or [("text", "no numbers yet", p.text_muted, False)])])
            exact = self._spelled_out(row, FOLLOWER_WORD[platform])
            pill.setToolTip((f"{PLATFORMS[platform]}: {exact}\n" if exact else "") + SOURCES[platform])
        self.layout().invalidate()


class AudienceDelegate(ChatDelegate):
    """One viewer per row: picture, platform pill, name, badges, and what they were last seen doing."""

    def __init__(self, view: QListWidget) -> None:
        super().__init__(view, compact=True)
        self.row = round(34 * self.scale)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802 - Qt override
        return QSize(self.view.viewport().width(), self.row)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        user = index.data(USER_ROLE)
        if not isinstance(user, ChatUser):
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect: QRect = option.rect
        self._plate(painter, rect)
        size = round(22 * self.scale)
        x = rect.left() + self.pad
        self._paint_avatar(painter, x, rect.center().y() - size / 2, size, user)
        tx = x + size + self.pad
        state = str(index.data(STATE_ROLE) or "")
        small = QFontMetrics(self.small_font)
        state_w = small.horizontalAdvance(state) + self.pad if state else 0
        name_metrics = QFontMetrics(self.name_font)
        top = rect.center().y() - name_metrics.height() / 2
        painter.save()  # A long name with badges stops short of the text on the right.
        painter.setClipRect(QRectF(rect.left(), rect.top(), rect.width() - state_w - self.pad, rect.height()))
        self._name_line(painter, tx, top, max(60, rect.right() - tx - state_w), user, self._text(),
                        always_platform=True)
        painter.restore()
        if state:
            painter.setFont(self.small_font)
            painter.setPen(self._muted())
            baseline = rect.center().y() - small.height() / 2 + small.ascent()
            self._draw_text(painter, QPointF(rect.right() - state_w, baseline), state)
        painter.restore()


def seen_text(entry: dict) -> str:
    if entry["in_chat"] and not entry["active"]:
        return "in chat"
    if not entry["active"]:
        return "joined"
    minutes = int(entry["idle_s"] // 60)
    return "active now" if minutes < 1 else f"active {minutes} min ago"


class AudienceList(QWidget):
    """Who's here, as far as each platform says, with the platform they're watching from."""

    def __init__(self, controller: Controller, see_through: bool = False) -> None:
        super().__init__()
        self.controller = controller
        self.see_through = see_through
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.summary.setToolTip(
            "No platform publishes its full viewer list, so this shows who they do reveal:\n"
            "• TikTok: viewers as they join, and everyone who chats, likes, gifts, follows or shares\n"
            "• Twitch: who is in chat (up to about 1,000 names; logged-out viewers are never listed)\n"
            "• YouTube: only people who chat\n"
            f"Viewers leave the list {int(IDLE_SECONDS // 60)} minutes after they were last seen "
            "(Twitch: when they leave chat). The viewer counts above are the platforms' own totals.")
        layout.addWidget(self.summary)
        self.list = QListWidget()
        self.list.setObjectName("audienceList")
        self.list.setItemDelegate(AudienceDelegate(self.list))
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setUniformItemSizes(True)
        self.list.platforms = set(PLATFORMS)  # Read by the delegate: always show the platform pill.
        if see_through:
            make_see_through(self.list)
        layout.addWidget(self.list, 1)
        self._latest: list[dict] = controller.audience_snapshot
        controller.audience_changed.connect(self._changed)
        image_cache().loaded.connect(self._image_loaded)
        theme_changed().connect(self._show)
        self._show()

    def count(self) -> int:
        return len(self._latest)

    def _image_loaded(self, _url: str) -> None:
        self.list.viewport().update()

    def _changed(self, people: list) -> None:
        self._latest = people
        if self.isVisible():  # Hidden behind the chat tab: redraw when it's looked at.
            self._show()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._show()

    def _show(self, _palette=None) -> None:
        people, p = self._latest, colors()
        counts = dict.fromkeys(PLATFORMS, 0)
        for entry in people:
            counts[entry["user"].platform] += 1
        parts = [f'<b style="color: {getattr(p, platform)};">{name}</b> {counts[platform]:,}'
                 for platform, name in PLATFORMS.items() if counts[platform]]
        if people:
            text = f"<b>{len(people):,}</b> seen here&nbsp;&nbsp;" + "&nbsp;&nbsp;".join(parts)
            if len(people) > MAX_ROWS:
                text += f'<br><span style="color: {p.text_muted};">Showing the latest {MAX_ROWS:,}.</span>'
        else:
            text = (f'<span style="color: {p.text_muted};">Nobody seen yet. Viewers show up here as they join '
                    "(TikTok), enter chat (Twitch) or chat (YouTube). Hover for details.</span>")
        self.summary.setText(text)
        self.summary.setStyleSheet(pill_style(True) + f" color: {p.text};" if self.see_through else "")
        bar = self.list.verticalScrollBar()
        position = bar.value()
        self.list.setUpdatesEnabled(False)
        self.list.clear()
        for entry in people[:MAX_ROWS]:
            item = QListWidgetItem()
            item.setData(USER_ROLE, entry["user"])
            item.setData(STATE_ROLE, seen_text(entry))
            user: ChatUser = entry["user"]
            item.setToolTip(f"{user.nickname or user.unique_id} (@{user.unique_id}) on {user.platform_name}")
            self.list.addItem(item)
        self.list.setUpdatesEnabled(True)
        bar.setValue(position)


class Elided(QLabel):
    """A one-line label that shortens its text with "…" rather than forcing the window wider."""

    def __init__(self, text: str = "") -> None:
        super().__init__()
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._fit()

    def _fit(self) -> None:
        self.setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(10, self.width() - 2)))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._fit()


class MusicBar(QWidget):
    """Previous, play/pause and next for the song requests, with the title, the time, and the volume."""

    def __init__(self, controller: Controller, see_through: bool = False, volume: bool = True) -> None:
        super().__init__()
        self.controller = controller
        self.see_through = see_through
        self._state: dict = controller.playback_state()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.box = QWidget()
        self.box.setObjectName("musicBar")
        outer.addWidget(self.box)
        column = QVBoxLayout(self.box)
        column.setContentsMargins(6, 4, 6, 4)
        column.setSpacing(3)
        row = QHBoxLayout()
        row.setSpacing(2)
        self.previous = self._button("Start the song over; press again right away to go back a song",
                                     controller.previous_song)
        self.toggle = self._button("", controller.toggle_music_pause)
        self.next = self._button("Skip to the next song", controller.skip_song)
        for button in (self.previous, self.toggle, self.next):
            row.addWidget(button)
        self.title = Elided()
        row.addSpacing(6)
        row.addWidget(self.title, 1)
        self.time = QLabel("")
        self.time.setFont(tabular(self.time.font()))
        self.time.setEnabled(False)  # Secondary text.
        row.addWidget(self.time)
        self.volume: QSlider | None = None
        if volume:
            self.volume = QSlider(Qt.Orientation.Horizontal)
            self.volume.setRange(0, 100)
            self.volume.setFixedWidth(64)
            self.volume.setValue(controller.settings.volume)
            self.volume.setToolTip(f"Music volume: {controller.settings.volume}%")
            self.volume.valueChanged.connect(self._volume_moved)
            self.volume.sliderReleased.connect(lambda: controller.set_volume(self.volume.value()))
            controller.settings_changed.connect(self._settings)
            row.addSpacing(4)
            row.addWidget(self.volume)
        column.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.setRange(0, 1000)
        column.addWidget(self.progress)
        self._drawn: tuple | None = None  # What the buttons show now: they're redrawn only when it changes.
        controller.playback_changed.connect(self.show_state)
        theme_changed().connect(self._restyle)
        self._restyle()

    def _restyle(self, _palette=None) -> None:
        background = tint(colors().panel, 0.82) if self.see_through else "transparent"
        self.box.setStyleSheet(f"QWidget#musicBar {{ background: {background}; border-radius: {RADIUS}px; }}")
        self._drawn = None
        self.show_state(self._state)

    @staticmethod
    def _button(tip: str, slot) -> QToolButton:
        button = QToolButton()
        button.setAutoRaise(True)
        button.setToolTip(tip)
        button.setCursor(Qt.CursorShape.ArrowCursor)
        button.clicked.connect(lambda _checked=False: slot())
        return button

    def _volume_moved(self, value: int) -> None:
        self.volume.setToolTip(f"Music volume: {value}%")
        if not self.volume.isSliderDown():  # Arrow keys or the wheel: there's no release to wait for.
            self.controller.set_volume(value)

    def _settings(self, settings) -> None:
        if self.volume is not None and not self.volume.isSliderDown() and self.volume.value() != settings.volume:
            self.volume.blockSignals(True)
            self.volume.setValue(settings.volume)
            self.volume.blockSignals(False)
            self.volume.setToolTip(f"Music volume: {settings.volume}%")

    def show_state(self, state: dict) -> None:
        self._state = state
        p, current = colors(), state.get("current")
        paused, panic = bool(state.get("paused")), bool(state.get("panic"))
        has_previous = bool(current) or bool(state.get("has_previous"))
        drawn = (current.get("id") if current else None, paused, panic, has_previous)
        if drawn != self._drawn:  # Progress arrives twice a second; the buttons and title rarely change.
            self._drawn = drawn
            self.previous.setIcon(glyph("previous", p.text if has_previous else p.text_disabled))
            self.next.setIcon(glyph("next", p.text if current else p.text_disabled))
            self.toggle.setIcon(glyph("play" if paused else "pause", p.accent_text if paused else p.text))
            if panic:
                self.toggle.setToolTip("Paused by the panic button: press Resume to play again")
            else:
                self.toggle.setToolTip("Play the music" if paused else "Pause the music")
            self.toggle.setEnabled(not panic)
            self.previous.setEnabled(has_previous)
            self.next.setEnabled(bool(current))
            if current:
                title = current.get("title") or current.get("video_id") or ""
                artist = f" · {current['channel']}" if current.get("channel") else ""
                self.title.set_full_text(f"{title}{artist}")
                self.title.setToolTip(f"{title}{artist}\nRequested by {current.get('requested_by', '')}")
            else:
                self.title.set_full_text("Nothing playing")
            self.title.setEnabled(bool(current))
        duration, position = state.get("duration") or 0.0, state.get("position") or 0.0
        if current and not state.get("player"):
            self.time.setText("no player")
            self.time.setToolTip("Music plays in the Now Playing overlay: add it to OBS from the Overlays tab "
                                 "(or open it in a browser) to hear it.")
        elif current and duration:
            self.time.setText(f"{clock(position)} / {clock(duration)}" + ("  paused" if paused else ""))
            self.time.setToolTip("")
        else:
            self.time.setText("paused" if paused and current else "")
            self.time.setToolTip("")
        self.progress.setValue(round(1000 * min(1.0, position / duration)) if current and duration else 0)
