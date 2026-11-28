"""Help → Setup Guide (F1): five steps from nothing to live, shown over the whole window.

1 Account (optional) · 2 Connect your channel · 3 Pick a look · 4 Add it to OBS (with tuning for this PC) ·
5 Test everything, then go live.

Opens by itself the first time (until finished); every step can be skipped and done later. Nothing in OBS changes
without the user pressing a button that says so.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import Controller
from ignite_live.core import keychain
from ignite_live.core.bundled import main_server
from ignite_live.core.events import ConnectionState, StatusChange
from ignite_live.integrations.streamlabs import AccountInfo
from ignite_live.live.checklist import FAIL, INFO, OK, WARN, Check
from ignite_live.overlay import server as overlay_server
from ignite_live.overlay.server import PAGES, THEMES
from ignite_live.setup import tuning
from ignite_live.setup.hardware import VENDOR_NAMES, Hardware
from ignite_live.ui import lucide
from ignite_live.ui.icons import glyph_pixmap
from ignite_live.ui.kit import hline
from ignite_live.ui.pages.stream import disclosure
from ignite_live.ui.simulate import sample_events
from ignite_live.ui.theme import colors, set_props, theme_changed
from ignite_live.viewers.points import clean_config as clean_points

STEPS = ("Account", "Channel", "Look", "OBS", "Go live")
NARROW, WIDE = 460, 936  # The design's column: a narrow one for most steps, a wide one for the test step.
LOOKS = ("synthwave", "minimal", "ocean")  # The themes offered here; every theme is in Overlays.
# What the switches on the Look step turn on: (overlay page, label).
LOOK_OVERLAYS = (("chat", "Chat"), ("alerts", "Gift alerts"), ("goals", "Goal bar"), ("nowplaying", "Now playing"))
DEFAULT_ON = ("chat", "alerts")
CHECK_MARKS = {OK: ("check", "healthy"), INFO: ("info", "text_muted"), WARN: ("warn", "warning"),
               FAIL: ("cross", "live")}

GUIDE_STYLE = """
QWidget#guidePage { background: transparent; }
QFrame#stepTile { background: %(panel)s; border: 1px solid %(divider)s; border-radius: 10px; }
QFrame#stepTile[ready="true"] { border-color: %(healthy)s; }
QFrame#stepTile QLabel { background: transparent; }
QFrame#guidePanel { background: %(panel)s; border: none; border-radius: 12px; }
QFrame#guidePanel QLabel { background: transparent; }
QFrame#linkBox { background: %(panel)s; border: 1px solid %(divider)s; border-radius: 10px; }
QLineEdit[flat="true"] { background: transparent; border: none; padding: 0; min-height: 20px; }
QPushButton[dashed="true"] { background: transparent; color: %(muted)s; border: 1px dashed %(handle)s; border-radius: 10px;
    padding: 12px; }
QPushButton[dashed="true"]:hover { color: %(text)s; border-color: %(accent)s; }
QPushButton[discord="true"] { background: #5865f2; color: #ffffff; border: 1px solid #5865f2; border-radius: 10px;
    padding: 12px; font-weight: 600; }
QPushButton[discord="true"]:hover { background: #4752c4; border-color: #4752c4; }
QFrame#guidePanel QLabel[num="true"] { background: %(raised)s; color: %(text)s; border-radius: 15px; font-weight: 600; }
QFrame#okBanner { background: %(ok_bg)s; border-radius: 8px; }
QFrame#okBanner QLabel { color: %(healthy)s; background: transparent; font-weight: 500; }
QFrame#waitBanner { background: %(panel)s; border-radius: 8px; }
"""


def guide_note(text: str) -> QLabel:
    """Small muted text: the guide is a walkthrough, so its explanations stay on the page."""
    widget = QLabel(text)
    widget.setWordWrap(True)
    widget.setProperty("role", "muted")
    return widget


def body(text: str) -> QLabel:
    widget = QLabel(text)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.TextFormat.RichText)
    return widget


def heading(text: str) -> QLabel:
    widget = QLabel(text)
    widget.setStyleSheet("font-size: 28px; font-weight: 600;")
    widget.setWordWrap(True)
    return widget


def status_tile(content: QLabel, icon_name: str) -> QFrame:
    """A rounded tile around a status line, with an icon at the left and a green outline once it's ready
    (``set_ready``). The design's connected-channel tile."""
    frame = QFrame()
    frame.setObjectName("stepTile")
    row = QHBoxLayout(frame)
    row.setContentsMargins(14, 12, 14, 12)
    row.setSpacing(12)
    icon = QLabel()
    icon.setPixmap(lucide.pixmap(icon_name, colors().text_muted, 20))
    row.addWidget(icon)
    row.addWidget(content, 1)
    frame.icon_label = icon  # type: ignore[attr-defined]
    return frame


def set_ready(frame: QFrame, ready: bool) -> None:
    """Green outline and a check when ``ready``; the icon goes back to muted otherwise."""
    p = colors()
    frame.icon_label.setPixmap(lucide.pixmap("circle-check" if ready else "circle", p.healthy if ready else p.text_muted, 20))
    set_props(frame, ready=ready)


def dashed_button(text: str) -> QPushButton:
    widget = QPushButton(text)
    set_props(widget, dashed=True)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return widget


def panel() -> QFrame:
    frame = QFrame()
    frame.setObjectName("guidePanel")
    return frame


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_swatches: dict[str, tuple[str, str, str]] = {}


def theme_swatch(key: str) -> tuple[str, str, str]:
    """(surface, accent, second accent) of an overlay theme, read from the same stylesheet the overlays use."""
    if key not in _swatches:
        found = ("#16192b", "#ff7a3a", "#e8ebf7")
        try:
            css = (Path(overlay_server.__file__).parent / "static" / "themes.css").read_text("utf-8")
            pattern = r':root\s*\{([^}]*)\}' if key == "minimal" else r':root\[data-theme="%s"\]\s*\{([^}]*)\}' % key
            block = re.search(pattern, css)
            if block:
                text = block.group(1)
                rgba = re.search(r"--surface:\s*rgba\((\d+),\s*(\d+),\s*(\d+)", text)
                a = re.search(r"--accent:\s*(#[0-9a-fA-F]{6})", text)
                b = re.search(r"--accent-2:\s*(#[0-9a-fA-F]{6})", text)
                if rgba and a and b:
                    found = ("#%02x%02x%02x" % tuple(int(v) for v in rgba.groups()), a.group(1), b.group(1))
        except OSError:
            pass
        _swatches[key] = found
    return _swatches[key]


class StepBar(QWidget):
    """One rounded segment per step: ember up to and including the current one, a quiet divider colour after."""

    GAP = 4

    def __init__(self, count: int) -> None:
        super().__init__()
        self.count = count
        self.value = 0
        self.setFixedHeight(3)

    def set_value(self, value: int) -> None:
        self.value = value
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        p = colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        width = (self.width() - self.GAP * (self.count - 1)) / self.count
        for i in range(self.count):
            painter.setBrush(QColor(p.accent if i <= self.value else p.divider))
            painter.drawRoundedRect(QRectF(i * (width + self.GAP), 0, width, self.height()), 1.5, 1.5)


class PageStack(QWidget):
    """Pages with one showing. Unlike a QStackedWidget it is only as tall as the page showing, so a short step stays
    centred instead of stretching to the height of the tallest one."""

    def __init__(self) -> None:
        super().__init__()
        self._pages: list[QWidget] = []
        self._current = -1
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(0, 0, 0, 0)

    def addWidget(self, page: QWidget) -> None:  # noqa: N802 - the QStackedWidget names this stands in for
        page.hide()
        self._column.addWidget(page)
        self._pages.append(page)

    def count(self) -> int:
        return len(self._pages)

    def widget(self, index: int) -> QWidget:
        return self._pages[index]

    def currentIndex(self) -> int:  # noqa: N802
        return self._current

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        for i, page in enumerate(self._pages):
            page.setVisible(i == index)
        self._current = index


class ThemeCard(QFrame):
    """A look to pick: its colours as three blocks, and its name."""

    clicked = Signal()

    def __init__(self, key: str) -> None:
        super().__init__()
        self.key = key
        self.selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(122)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(THEMES.get(key, key))

    def set_selected(self, on: bool) -> None:
        self.selected = on
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        p = colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor(p.accent if self.selected else p.divider), 2 if self.selected else 1))
        painter.setBrush(QColor(p.panel))
        painter.drawRoundedRect(rect, 10, 10)
        surface, accent, second = theme_swatch(self.key)
        block = QRectF(rect.left() + 10, rect.top() + 10, rect.width() - 20, 56)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.save()
        from PySide6.QtGui import QPainterPath

        clip = QPainterPath()
        clip.addRoundedRect(block, 6, 6)
        painter.setClipPath(clip)
        third = block.width() / 5
        for index, color in enumerate((surface, accent, second)):
            width = third * 3 if index == 0 else third
            left = block.left() if index == 0 else block.left() + third * (index + 2)
            painter.setBrush(QColor(color))
            painter.drawRect(QRectF(left, block.top(), width + 1, block.height()))
        painter.restore()
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(p.text))
        text = QRectF(rect.left() + 12, block.bottom() + 8, rect.width() - 24, rect.bottom() - block.bottom() - 12)
        painter.drawText(text, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop) | Qt.TextFlag.TextWordWrap.value,
                         THEMES.get(self.key, self.key))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(event)


class SetupGuide(QWidget):
    """The setup tutorial, shown inside the main window (not a separate window).

    ``closed`` fires when the user finishes or chooses Finish Later; the host then shows the page they came from.
    """

    closed = Signal(bool)  # True: finished; False: left for later.

    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.hardware: Hardware | None = None
        self.current_obs: tuning.ObsTuning | None = None
        self.checks: list[Check] = []
        self._signing_in = False
        self._waiting_text = ""
        self._restyle()
        theme_changed().connect(lambda _p=None: self._restyle())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()  # Long steps scroll instead of squashing on small screens.
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        surface = QWidget()
        surface.setObjectName("guidePage")
        scroll.setWidget(surface)
        rows = QVBoxLayout(surface)
        rows.setContentsMargins(24, 32, 24, 32)
        rows.addStretch(1)
        centre = QHBoxLayout()
        centre.addStretch(1)
        self.holder = QWidget()
        self.holder.setObjectName("guidePage")
        centre.addWidget(self.holder)
        centre.addStretch(1)
        rows.addLayout(centre)
        rows.addStretch(1)
        column = QVBoxLayout(self.holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(14)
        self.progress = StepBar(len(STEPS))
        column.addWidget(self.progress)
        self.counter = guide_note("")
        column.addWidget(self.counter)
        self.stack = PageStack()
        column.addWidget(self.stack)

        self.timer = QTimer(self, interval=2500)
        self.timer.timeout.connect(self._poll_obs)
        for build in (self._account_page, self._channel_page, self._look_page, self._obs_page, self._live_page):
            page = QWidget()
            page.setObjectName("guidePage")
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(18)
            build(layout)
            self.stack.addWidget(page)

        controller.status_changed.connect(self._status)
        controller.account_changed.connect(self._account)
        controller.server_sign_in.connect(self._server_sign_in)
        controller.notice.connect(self._notice)
        controller.key_changed.connect(lambda _key: self._key_changed(controller.active_key))
        controller.hosted_changed.connect(self._fill_links)
        controller.server_changed.connect(lambda _up: self._fill_links())
        self.go(0)

    def _restyle(self) -> None:
        p = colors()
        self.setStyleSheet(GUIDE_STYLE % {"panel": p.panel, "divider": p.divider, "healthy": p.healthy,
                                          "muted": p.text_muted, "handle": p.handle, "text": p.text,
                                          "accent": p.accent, "raised": p.raised,
                                          "ok_bg": QColor(p.healthy).darker(450).name()})

    def accept(self) -> None:
        self.closed.emit(True)

    def reject(self) -> None:
        self.closed.emit(False)

    # Building blocks.

    def _title(self, layout: QVBoxLayout, title: str, subtitle: str = "") -> None:
        layout.addWidget(heading(title))
        if subtitle:
            note = body(subtitle)
            note.setProperty("role", "muted")
            note.setStyleSheet("font-size: 14px;")
            layout.addWidget(note)

    def _primary(self, text: str, slot) -> QPushButton:
        widget = QPushButton(text)
        set_props(widget, primary=True, big=True)
        widget.setDefault(False)
        widget.clicked.connect(slot)
        return widget

    def _under(self, layout: QVBoxLayout, skip: str | None = "Skip for now", back: bool = True) -> None:
        """Back, Skip for now and Finish later, under the main button."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 8, 0, 0)
        back_button = QPushButton("Back")
        set_props(back_button, ghost=True)
        back_button.clicked.connect(lambda: self.go(self.stack.currentIndex() - 1))
        back_button.setVisible(back)
        later = QPushButton("Finish later")
        set_props(later, ghost=True)
        later.setToolTip("Close for now. Help → Setup Guide brings it back.")
        later.clicked.connect(self.reject)
        row.addWidget(back_button)
        row.addStretch(1)
        if skip:
            skip_button = QPushButton(skip)
            set_props(skip_button, ghost=True)
            skip_button.clicked.connect(self._skip)
            row.addWidget(skip_button)
        row.addStretch(1)
        row.addWidget(later)
        layout.addLayout(row)

    # Navigation.

    def _skip(self) -> None:
        """Skip for now: on to the next step; on the last one, finish without going live."""
        if self.stack.currentIndex() == len(STEPS) - 1:
            self._finish()
        else:
            self.go(self.stack.currentIndex() + 1, save=False)

    def go(self, index: int, save: bool = True) -> None:
        if not 0 <= index < len(STEPS):
            return
        if save:
            self._save_page(self.stack.currentIndex())
        self._left(self.stack.currentIndex())
        self.stack.setCurrentIndex(index)
        self.counter.setText(f"Step {index + 1} of {len(STEPS)}")
        self.progress.set_value(index)
        self._fit()
        self._entered(STEPS[index])

    def _fit(self) -> None:
        """The column is the design's width (wider on the test step, as far as the window allows), and the stack is
        only as tall as the step showing, so short steps stay centred instead of stretching to the tallest."""
        index = self.stack.currentIndex()
        want = WIDE if STEPS[index] == "Go live" else NARROW
        self.holder.setFixedWidth(max(320, min(want, self.width() - 48)))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._fit()

    def _entered(self, step: str) -> None:
        if step == "Account":
            self._refresh_account()
        elif step == "OBS":
            self._fill_links()
            self._poll_obs()
            self.timer.start()
        elif step == "Go live":
            self._run_checks()
            self._show_preview()

    def _left(self, index: int) -> None:
        step = STEPS[index]
        if step == "OBS":
            self.timer.stop()
        elif step == "Go live":
            self.preview.stop()

    def _save_page(self, index: int) -> None:
        step = STEPS[index]
        s = self.controller.settings
        if step == "Channel":
            new = replace(s, tiktok_username=self.username.text().strip().lstrip("@"),
                          twitch_channel=self.twitch.text().strip().lstrip("#"),
                          youtube_channel=self.youtube.text().strip())
            if new != s:
                self.controller.save_settings(new)
        elif step == "Look":
            points = clean_points({**s.points, "enabled": self.points_on.isChecked()})
            new = replace(s, overlay_theme=self.look or s.overlay_theme, tts_enabled=self.tts_on.isChecked(),
                          points=points, panic_scene=self.brb.currentText().strip())
            if self.tts_on.isChecked() and self.system_voice.isChecked():
                new = replace(new, tts_provider="system")
            if new != s:
                self.controller.save_settings(new)
        elif step == "Go live":
            title = self.stream_title.text().strip()
            if title != s.stream_title:
                self.controller.save_settings(replace(s, stream_title=title))

    def _finish(self) -> None:
        self._save_page(self.stack.currentIndex())
        self.controller.save_settings(replace(self.controller.settings, setup_done=True))
        self.accept()

    # 1. Account.

    def _account_page(self, layout: QVBoxLayout) -> None:
        self._title(layout, "Let's get you live",
                    "Chat, alerts and overlays for TikTok, Twitch and YouTube. Free to start, no card.")
        self.discord_button = QPushButton("Continue with Discord")
        set_props(self.discord_button, discord=True)
        self.discord_button.setIcon(lucide.icon("gamepad", "#ffffff", 18))
        self.discord_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.discord_button.setToolTip("Opens your browser to sign in; choose Discord there")
        self.discord_button.clicked.connect(self._sign_in)
        layout.addWidget(self.discord_button)
        self.or_row = QWidget()
        divider = QHBoxLayout(self.or_row)
        divider.setContentsMargins(0, 0, 0, 0)
        divider.addWidget(hline(), 1)
        divider.addWidget(guide_note("or"))
        divider.addWidget(hline(), 1)
        layout.addWidget(self.or_row)
        self.email_button = self._primary("Create account with email", self._sign_in)
        self.email_button.setToolTip("Opens your browser to sign in or make an account")
        layout.addWidget(self.email_button)
        self.account_hint = guide_note("You finish in your browser, then come back here. No card needed.")
        self.account_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.account_hint)
        self.account_status = QLabel()
        self.account_status.setWordWrap(True)
        self.server_tile = status_tile(self.account_status, "user")
        layout.addWidget(self.server_tile)
        self.cancel_sign_in = QPushButton("Cancel")
        set_props(self.cancel_sign_in, ghost=True)
        self.cancel_sign_in.clicked.connect(self.controller.cancel_server_sign_in)
        layout.addWidget(self.cancel_sign_in, 0, Qt.AlignmentFlag.AlignHCenter)
        self.account_next = self._primary("Continue", lambda: self.go(1))
        layout.addWidget(self.account_next)
        self._under(layout, back=False)

    def _refresh_account(self) -> None:
        c = self.controller
        signed = c.server_account()
        has_server = bool(main_server() or c.settings.backend_url)
        waiting = self._signing_in
        show_buttons = has_server and not signed and not waiting
        for widget in (self.discord_button, self.or_row, self.email_button, self.account_hint):
            widget.setVisible(show_buttons)
        self.server_tile.setVisible(not show_buttons)
        self.cancel_sign_in.setVisible(waiting)
        self.account_next.setVisible(bool(signed) or not has_server)
        if signed:
            self.account_status.setText(f"<b>Signed in as {_escape(str(signed.get('username') or 'you'))}</b><br>"
                                        "Your settings, stream key and overlay links follow you.")
        elif waiting:
            self.account_status.setText(self._waiting_text)
        elif not has_server:
            self.account_status.setText("<b>This copy of Ignite works on this PC.</b><br>Accounts aren't part of this "
                                        "version, so everything stays local.")
        set_ready(self.server_tile, bool(signed))

    def _sign_in(self) -> None:
        self._signing_in = True
        self._waiting_text = "<b>Finish signing in in your browser</b><br>Ignite continues by itself when you're done."
        self._refresh_account()
        self.controller.sign_in_to_server()

    def _server_sign_in(self, state) -> None:
        if isinstance(state, dict) and state.get("waiting"):
            self._signing_in = True
            self._waiting_text = (f"<b>Finish signing in in your browser</b><br>Code: <b>{_escape(str(state.get('code', '')))}"
                                  "</b>. Ignite continues by itself when you're done.")
        else:
            self._signing_in = False
        self._refresh_account()
        if isinstance(state, dict) and not state.get("waiting") and self.stack.currentIndex() == 0:
            self.go(1, save=False)

    # 2. Channel.

    def _channel_page(self, layout: QVBoxLayout) -> None:
        c = self.controller
        s = c.settings
        self._title(layout, "Connect your channel", "We only read chat and gifts. We never post as you.")
        self.channel_name = QLabel()  # What the tile says about the channel: where it is and what it's doing.
        tile = QFrame()
        tile.setObjectName("stepTile")
        row = QHBoxLayout(tile)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(12)
        self.channel_icon = QLabel()
        row.addWidget(self.channel_icon)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.username = QLineEdit(s.tiktok_username)
        self.username.setPlaceholderText("Your TikTok username")
        self.username.setAccessibleName("TikTok username")
        self.username.setProperty("flat", True)
        self.username.setStyleSheet("font-weight: 600;")
        self.channel_note = guide_note("TikTok · reads your LIVE chat, gifts and follows")
        text.addWidget(self.username)
        text.addWidget(self.channel_note)
        row.addLayout(text, 1)
        self.channel_tile = tile
        tile.icon_label = self.channel_icon  # type: ignore[attr-defined]
        layout.addWidget(tile)
        self.username.textChanged.connect(lambda _t: self._channel_changed())
        more = QHBoxLayout()
        more.setSpacing(10)
        self.twitch = QLineEdit(s.twitch_channel)
        self.youtube = QLineEdit(s.youtube_channel)
        self.add_twitch = dashed_button("+ Twitch")
        self.add_youtube = dashed_button("+ YouTube")
        for name, edit, add in (("Twitch channel", self.twitch, self.add_twitch),
                                ("YouTube channel, @handle or link", self.youtube, self.add_youtube)):
            cell = QVBoxLayout()
            cell.setSpacing(0)
            edit.setPlaceholderText(name)
            edit.setAccessibleName(name)
            edit.setVisible(bool(edit.text()))
            add.setVisible(not edit.text())
            add.clicked.connect(lambda _=False, e=edit, a=add: (a.hide(), e.show(), e.setFocus()))
            cell.addWidget(add)
            cell.addWidget(edit)
            more.addLayout(cell, 1)
        layout.addLayout(more)
        layout.addWidget(guide_note("You can add more channels any time in Settings."))
        layout.addWidget(self._primary("Continue", lambda: self.go(2)))
        self._under(layout)
        self._channel_changed()

    def _channel_changed(self) -> None:
        name = self.username.text().strip().lstrip("@")
        set_ready(self.channel_tile, bool(name))
        self._status(self.controller.statuses["tiktok"])

    # 3. Look.

    def _look_page(self, layout: QVBoxLayout) -> None:
        s = self.controller.settings
        self._title(layout, "Pick a look", "Your dashboard and overlays share it. Change anything later.")
        self.look = s.overlay_theme if s.overlay_theme in LOOKS else "minimal"
        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.look_cards: dict[str, ThemeCard] = {}
        for key in LOOKS:
            card = ThemeCard(key)
            card.clicked.connect(lambda k=key: self._choose_look(k))
            cards.addWidget(card, 1)
            self.look_cards[key] = card
        layout.addLayout(cards)
        self._choose_look(self.look)
        more = QPushButton("More themes in Overlays")
        set_props(more, ghost=True)
        more.clicked.connect(lambda: self._open("overlays"))
        layout.addWidget(more, 0, Qt.AlignmentFlag.AlignLeft)

        shown = panel()
        rows = QVBoxLayout(shown)
        rows.setContentsMargins(18, 12, 18, 12)
        rows.setSpacing(8)
        self.overlay_checks: dict[str, QCheckBox] = {}
        for page, text in LOOK_OVERLAYS:
            row = QHBoxLayout()
            row.addWidget(QLabel(text), 1)
            check = QCheckBox()
            check.setAccessibleName(text)
            check.setChecked(page in DEFAULT_ON)
            row.addWidget(check)
            rows.addLayout(row)
            self.overlay_checks[page] = check
        layout.addWidget(shown)

        extras = QWidget()
        form = QVBoxLayout(extras)
        form.setContentsMargins(0, 4, 0, 0)
        self.tts_on = QCheckBox("Read chat aloud (text to speech)")
        self.tts_on.setChecked(s.tts_enabled)
        self.system_voice = QCheckBox("Use this computer's own voice (lighter than online voices)")
        self.system_voice.setChecked(s.tts_provider == "system")
        self.points_on = QCheckBox("Loyalty points: viewers earn points and can spend them")
        self.points_on.setChecked(bool(s.points.get("enabled")))
        for widget in (self.tts_on, self.system_voice, self.points_on):
            form.addWidget(widget)
        row = QFormLayout()
        self.brb = QComboBox()
        self.brb.setEditable(True)
        self.brb.addItem("")
        studio = self.controller.studio
        self.brb.addItems(list(studio.scenes) + (list(studio.vertical.scenes) if studio.vertical else []))
        self.brb.setCurrentText(s.panic_scene)
        self.brb.lineEdit().setPlaceholderText("Stay on the current scene")
        row.addRow("Panic button scene:", self.brb)
        form.addLayout(row)
        form.addWidget(guide_note("The panic button (top right, Ctrl+Shift+P) mutes text to speech, holds alerts, "
                                  "pauses music and switches to this scene; Resume undoes it."))
        layout.addWidget(disclosure("Optional extras", extras))
        layout.addWidget(self._primary("Continue", lambda: self.go(3)))
        self._under(layout)

    def _choose_look(self, key: str) -> None:
        self.look = key
        for k, card in self.look_cards.items():
            card.set_selected(k == key)

    def chosen_pages(self) -> list[str]:
        return [page for page, check in self.overlay_checks.items() if check.isChecked()]

    def _open(self, key: str) -> None:
        host = self.window()
        if hasattr(host, "open_page"):
            host.open_page(key)

    # 4. OBS.

    def _obs_page(self, layout: QVBoxLayout) -> None:
        c = self.controller
        self._title(layout, "Add it to OBS")
        self.links = QVBoxLayout()
        self.links.setSpacing(8)
        layout.addLayout(self.links)
        self.link_note = guide_note("")
        layout.addWidget(self.link_note)
        steps = panel()
        rows = QVBoxLayout(steps)
        rows.setContentsMargins(18, 16, 18, 16)
        rows.setSpacing(12)
        for number, text in enumerate(("In OBS, add a Browser source",
                                       "Paste the link, with the size shown above it",
                                       "Done. It updates by itself."), 1):
            row = QHBoxLayout()
            row.setSpacing(12)
            badge = QLabel(str(number))
            badge.setProperty("num", True)
            badge.setFixedSize(30, 30)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(badge)
            row.addWidget(QLabel(text), 1)
            rows.addLayout(row)
        layout.addWidget(steps)
        self.obs_banner = QFrame()
        banner = QHBoxLayout(self.obs_banner)
        banner.setContentsMargins(12, 9, 12, 9)
        self.obs_banner_icon = QLabel()
        self.obs_banner_text = QLabel()
        banner.addWidget(self.obs_banner_icon)
        banner.addWidget(self.obs_banner_text, 1)
        layout.addWidget(self.obs_banner)
        self._banner(False, "")

        # Or let Ignite do it: connect to OBS, then add the overlays.
        auto = QWidget()
        form = QVBoxLayout(auto)
        form.setContentsMargins(0, 4, 0, 0)
        form.addWidget(body(
            "Ignite controls OBS through its WebSocket server (OBS 28 or newer has it built in): in OBS, "
            "<b>Tools → WebSocket Server Settings</b>, tick <b>Enable WebSocket server</b>, then "
            "<b>Show Connect Info</b> for the password."))
        fields = QFormLayout()
        self.obs_url = QLineEdit(c.settings.obs_url)
        self.obs_password = QLineEdit()
        self.obs_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.obs_password.setPlaceholderText("Saved in your system keychain" if keychain.load(keychain.OBS_PASSWORD)
                                             else "Paste the password")
        fields.addRow("Address:", self.obs_url)
        fields.addRow("Password:", self.obs_password)
        form.addLayout(fields)
        connect = QPushButton("Connect")
        connect.clicked.connect(self._connect_obs)
        form.addWidget(connect, 0, Qt.AlignmentFlag.AlignLeft)
        self.obs_status = body("")
        self.obs_tile = status_tile(self.obs_status, "radio")
        form.addWidget(self.obs_tile)
        self.add_overlays = QPushButton("Add my overlays to OBS")
        self.add_overlays.clicked.connect(self._add_overlays)
        form.addWidget(self.add_overlays, 0, Qt.AlignmentFlag.AlignLeft)
        self.overlay_result = body("")
        form.addWidget(self.overlay_result)
        form.addWidget(guide_note("For Now Playing (it plays the music), open its properties in OBS and tick "
                                  "<b>Control audio via OBS</b>, so the music reaches your stream."))
        self.auto_box = disclosure("Or let Ignite add it for you", auto)
        layout.addWidget(self.auto_box)
        self._status(c.statuses["obs"])

        # Tune OBS for this PC.
        tune = QWidget()
        inner = QVBoxLayout(tune)
        inner.setContentsMargins(0, 4, 0, 0)
        self.pc_info = body("Checking this PC…")
        inner.addWidget(self.pc_info)
        self.tier = QLabel()
        self.tier.setStyleSheet("font-size: 15px; font-weight: 600;")
        inner.addWidget(self.tier)
        upload = QFormLayout()
        self.upload = QComboBox()
        for key, (text, _mbps) in tuning.UPLOADS.items():
            self.upload.addItem(text, key)
        self.upload.currentIndexChanged.connect(lambda _i: self._show_tier())
        upload.addRow("Your upload speed:", self.upload)
        inner.addLayout(upload)
        inner.addWidget(guide_note("Upload speed decides how much video you can send. A speed test website shows it; "
                                   "Ignite doesn't run one, so it never contacts other services for this."))
        self.tune_table = QTableWidget(0, 4)
        self.tune_table.setHorizontalHeaderLabels(["Change", "Setting", "Now", "Recommended"])
        self.tune_table.verticalHeader().setVisible(False)
        self.tune_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tune_table.horizontalHeader().setStretchLastSection(True)
        self.tune_table.setMinimumHeight(190)
        inner.addWidget(self.tune_table)
        self.tune_notes = body("")
        inner.addWidget(self.tune_notes)
        buttons = QHBoxLayout()
        self.apply_tune = QPushButton("Apply to OBS")
        self.apply_tune.clicked.connect(self._apply)
        self.undo_tune = QPushButton("Undo Tuning")
        self.undo_tune.clicked.connect(lambda: self.controller.undo_tuning(self._read_obs))
        buttons.addWidget(self.apply_tune)
        buttons.addWidget(self.undo_tune)
        buttons.addStretch(1)
        inner.addLayout(buttons)
        self.tune_result = body("")
        inner.addWidget(self.tune_result)
        inner.addWidget(guide_note("Only OBS's output size and frame rate change, never your canvas or scenes, and "
                                   "Undo Tuning puts everything back."))
        self.tune_box = disclosure("Tune OBS for this PC (optional)", tune)
        self.tune_box.toggle.toggled.connect(self._tune_opened)
        layout.addWidget(self.tune_box)
        layout.addWidget(self._primary("Continue", lambda: self.go(4)))
        self._under(layout)

    def _banner(self, showing: bool, text: str) -> None:
        p = colors()
        self.obs_banner.setObjectName("okBanner" if showing else "waitBanner")
        self.obs_banner.setStyleSheet("")  # Re-read the object name's style.
        self.obs_banner_icon.setPixmap(lucide.pixmap("circle-check" if showing else "circle", p.healthy if showing
                                                     else p.text_muted, 16))
        self.obs_banner_text.setText(text)
        self.obs_banner_text.setStyleSheet(f"color: {p.healthy if showing else p.text_muted}; font-size: 12px;")
        self.obs_banner.style().unpolish(self.obs_banner)
        self.obs_banner.style().polish(self.obs_banner)

    def _fill_links(self) -> None:
        """One link for each overlay chosen on the Look step: the Ignite server's, and this PC's only as backup."""
        if not hasattr(self, "links"):
            return
        while self.links.count():
            item = self.links.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        pages = self.chosen_pages()
        for page in pages:
            title, width, height = PAGES[page]
            holder = QWidget()
            box = QVBoxLayout(holder)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(4)
            box.addWidget(guide_note(f"{title} · {width} × {height}"))
            frame = QFrame()
            frame.setObjectName("linkBox")
            row = QHBoxLayout(frame)
            row.setContentsMargins(14, 8, 8, 8)
            url = QLineEdit(self.controller.hosted_link(page))
            url.setReadOnly(True)
            url.setProperty("flat", True)
            url.setAccessibleName(f"{title} Browser Source URL")
            mono = QFont("Consolas")
            mono.setStyleHint(QFont.StyleHint.Monospace)
            mono.setPixelSize(12)
            url.setFont(mono)
            url.setCursorPosition(0)
            copy = QPushButton("Copy")
            set_props(copy, primary=True)
            copy.clicked.connect(lambda _=False, u=url, t=title: self._copy(u.text(), t))
            row.addWidget(url, 1)
            row.addWidget(copy)
            box.addWidget(frame)
            self.links.addWidget(holder)
        if not pages:
            self.links.addWidget(guide_note("You didn't switch any overlay on, so there's nothing to add yet. Go back "
                                            "to pick some, or add them later in Overlays."))
        hosted = self.controller.links_are_hosted
        self.link_note.setText("" if hosted else
                               "These links work on this PC. Sign in to Ignite (step 1) for links that work from any "
                               "PC; this PC's links stay as the backup.")
        self.link_note.setVisible(bool(pages) and not hosted)

    def _copy(self, text: str, title: str) -> None:
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.clipboard().setText(text)
        self.controller.notice.emit("info", f"Copied the {title} link.")

    def _poll_obs(self) -> None:
        """Is OBS showing what was picked? Asks OBS which of Ignite's overlays it has."""
        c = self.controller
        wanted = set(self.chosen_pages())
        if c.statuses["obs"].state is not ConnectionState.CONNECTED:
            self._banner(False, "Waiting for OBS. Connect it below, or just paste the link.")
            return

        def found(pages: set[str]) -> None:
            if wanted and wanted <= pages:
                self._banner(True, "OBS is showing your overlay" + ("s" if len(wanted) > 1 else ""))
            elif pages & wanted:
                self._banner(False, f"{len(pages & wanted)} of {len(wanted)} overlays are in OBS so far.")
            else:
                self._banner(False, "OBS doesn't have your overlay yet.")

        c.overlays_in_obs(found)

    def _connect_obs(self) -> None:
        c = self.controller
        password = self.obs_password.text().strip()
        if password and not keychain.save(keychain.OBS_PASSWORD, password):
            self.obs_status.setText("No system keychain was found, so the password couldn't be saved.")
            return
        self.obs_password.clear()
        url = self.obs_url.text().strip() or "ws://127.0.0.1:4455"
        if url != c.settings.obs_url:
            c.save_settings(replace(c.settings, obs_url=url))  # Reconnects by itself.
        else:
            c.reconnect_obs()
        self.obs_status.setText("Connecting…")

    def _status(self, change: StatusChange) -> None:
        if change.source == "obs" and hasattr(self, "obs_status"):
            marks = {ConnectionState.CONNECTED: "Connected", ConnectionState.ERROR: "Not connected"}
            self.obs_status.setText(f"<b>{marks.get(change.state, 'Not connected yet')}</b>: {_escape(change.detail)}")
            set_ready(self.obs_tile, change.state is ConnectionState.CONNECTED)
            self.add_overlays.setEnabled(change.state is ConnectionState.CONNECTED)
        elif change.source == "tiktok" and hasattr(self, "channel_note"):
            words = {ConnectionState.CONNECTED: "connected, reading chat", ConnectionState.CONNECTING: "connecting…",
                     ConnectionState.DEGRADED: "connects by itself once you're live"}
            self.channel_note.setText("TikTok · " + words.get(change.state, "reads your LIVE chat, gifts and follows"))

    def _add_overlays(self) -> None:
        chosen = self.chosen_pages()
        if chosen:
            self.overlay_result.setText("Adding to OBS…")
            self.controller.add_overlays(chosen)
        else:
            self.overlay_result.setText("Switch at least one overlay on in the Look step.")

    def _notice(self, level: str, text: str) -> None:
        """While on the OBS step, OBS's answer shows there instead of only in a toast."""
        if STEPS[self.stack.currentIndex()] == "OBS" and self.overlay_result.text() == "Adding to OBS…":
            color = colors().live if level == "error" else colors().healthy
            self.overlay_result.setText(f"<span style='color: {color}'>{_escape(text)}</span>")
            self._poll_obs()

    def _tune_opened(self, shown: bool) -> None:
        if shown:
            if self.hardware is None:
                self.controller.detect_hardware(self._detected)
            self._read_obs()

    def _detected(self, hw: Hardware) -> None:
        self.hardware = hw
        gpu = ", ".join(hw.gpu_names) or "none found"
        ram = f"{hw.ram_gb:g} GB" if hw.ram_gb else "unknown"
        self.pc_info.setText(f"<b>Processor:</b> {hw.threads or 'unknown'} threads<br><b>Memory:</b> {ram}<br>"
                             f"<b>Graphics:</b> {_escape(gpu)}")
        self._show_tier()

    def recommendation(self) -> tuning.Recommendation:
        base = self.current_obs.base if self.current_obs and all(self.current_obs.base) else (1080, 1920)
        return tuning.recommend(self.hardware or Hardware(), self.upload.currentData(), base)

    def _show_tier(self) -> None:
        if self.hardware is None:
            return
        rec = self.recommendation()
        encoder = tuning.ENCODER_LABELS[rec.encoder]
        gpu = f" with {VENDOR_NAMES[self.hardware.gpus[0]]} graphics" if self.hardware.gpus else ""
        self.tier.setText(f"{tuning.TIERS[rec.tier]} PC{gpu}: {min(rec.output)}p at {rec.fps} FPS, {encoder}, "
                          f"{rec.bitrate:,} kbps")

    def _read_obs(self) -> None:
        self.undo_tune.setEnabled(bool(self.controller.settings.tuning_backup))
        if self.controller.statuses["obs"].state is not ConnectionState.CONNECTED:
            self.tune_table.setRowCount(0)
            self.apply_tune.setEnabled(False)
            self.tune_notes.setText("Connect OBS first (\"Or let Ignite add it for you\", above).")
            return
        self.tune_notes.setText("Reading OBS…")
        self.controller.read_tuning(self._show_tuning, lambda error: self.tune_notes.setText(_escape(str(error))))

    def _show_tuning(self, current: tuning.ObsTuning) -> None:
        self.current_obs = current
        if self.hardware is None:
            self.hardware = Hardware()
        rec = self.recommendation()
        target = self.controller.stream_target
        vertical = target is not None and target.kind == "vertical"
        rows = tuning.rows(current, rec, vertical)
        self.tune_rows = rows
        self.tune_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            check = QTableWidgetItem()
            if row.settable:
                check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                same = row.current == row.recommended
                check.setCheckState(Qt.CheckState.Unchecked if same else Qt.CheckState.Checked)
            else:
                check.setFlags(Qt.ItemFlag.ItemIsEnabled)
                check.setText("by hand")
            self.tune_table.setItem(index, 0, check)
            for column, text in enumerate((row.label, row.current, row.recommended), 1):
                self.tune_table.setItem(index, column, QTableWidgetItem(text))
        notes = list(rec.notes)
        manual = tuning.why_by_hand(current, vertical)
        if manual:
            notes.append(manual)
        self.tune_notes.setText("<br>".join(_escape(n) for n in notes))
        self.apply_tune.setEnabled(any(r.settable for r in rows))
        self._show_tier()

    def _chosen(self) -> set[str]:
        return {row.key for index, row in enumerate(getattr(self, "tune_rows", []))
                if row.settable and self.tune_table.item(index, 0).checkState() == Qt.CheckState.Checked}

    def _apply(self) -> None:
        keys = self._chosen()
        if not keys:
            self.tune_result.setText("Nothing ticked to change.")
            return
        self.tune_result.setText("Applying…")
        self.controller.apply_tuning(self.recommendation(), keys, self._applied)

    def _applied(self, problems: list[str]) -> None:
        if problems:
            self.tune_result.setText("<b>Not applied:</b><br>" + "<br>".join(_escape(p) for p in problems))
        else:
            self.tune_result.setText("Done: OBS has the new settings.")
        self._read_obs()

    # 5. Go live.

    def _live_page(self, layout: QVBoxLayout) -> None:
        c = self.controller
        self._title(layout, "Test everything before you go live",
                    "We send fake events through your whole setup. Nothing is posted anywhere, and every test is "
                    "marked TEST.")
        columns = QHBoxLayout()
        columns.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(10)
        self.check_panel = panel()
        self.check_rows = QVBoxLayout(self.check_panel)
        self.check_rows.setContentsMargins(18, 8, 18, 8)
        self.check_rows.setSpacing(0)
        left.addWidget(self.check_panel)
        key = QWidget()
        key_form = QVBoxLayout(key)
        key_form.setContentsMargins(0, 4, 0, 0)
        key_form.addWidget(body("Sign in with Streamlabs, then get a key: Ignite reuses it for 48 hours, or until you "
                                "end the LIVE."))
        self.account = body("")
        self.account_tile = status_tile(self.account, "user")
        key_form.addWidget(self.account_tile)
        row = QHBoxLayout()
        self.sign_in = QPushButton("Sign In with Streamlabs…")
        self.sign_in.clicked.connect(c.login_streamlabs)
        self.get_key = QPushButton("Get Stream Key")
        self.get_key.clicked.connect(lambda: c.get_stream_key(c.settings.stream_title, "", "0"))
        send = QPushButton("Send Key to OBS")
        send.clicked.connect(c.send_key_to_obs)
        for widget in (self.sign_in, self.get_key, send):
            row.addWidget(widget)
        row.addStretch(1)
        key_form.addLayout(row)
        self.key_status = body("")
        key_form.addWidget(self.key_status)
        manual = QWidget()
        manual_form = QFormLayout(manual)
        manual_form.setContentsMargins(0, 0, 0, 0)
        self.key_server = QLineEdit()
        self.key_server.setPlaceholderText("rtmp:// or rtmps://")
        self.key_value = QLineEdit()
        self.key_value.setEchoMode(QLineEdit.EchoMode.Password)
        save = QPushButton("Use This Key")
        save.clicked.connect(self._save_key)
        manual_form.addRow("Server:", self.key_server)
        manual_form.addRow("Key:", self.key_value)
        manual_form.addRow("", save)
        key_form.addWidget(disclosure("I have a key already (e.g. from TikTok LIVE Studio)", manual))
        left.addWidget(disclosure("Stream key", key))
        left.addStretch(1)
        columns.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(12)
        seen = panel()
        seen_box = QVBoxLayout(seen)
        seen_box.setContentsMargins(18, 14, 18, 16)
        seen_box.addWidget(guide_note("What viewers will see"))
        from ignite_live.ui.pages.overlays import LivePreview

        self.preview = LivePreview()
        self.preview.setMinimumHeight(220)
        self.preview_hint = guide_note("The in-app preview isn't available here. Your alert still plays in OBS when you "
                                       "press Send test.")
        seen_box.addWidget(self.preview)
        seen_box.addWidget(self.preview_hint)
        self.preview_hint.hide()
        right.addWidget(seen)
        stream = panel()
        stream_box = QVBoxLayout(stream)
        stream_box.setContentsMargins(18, 14, 18, 16)
        stream_box.addWidget(guide_note("Your stream"))
        self.stream_title = QLineEdit(c.settings.stream_title)
        self.stream_title.setPlaceholderText("What are you streaming?")
        self.stream_title.setAccessibleName("Stream title")
        stream_box.addWidget(self.stream_title)
        right.addWidget(stream)
        right.addStretch(1)
        columns.addLayout(right, 1)
        layout.addLayout(columns)

        footer = QHBoxLayout()
        again = QPushButton("Run tests again")
        set_props(again, ghost=True)
        again.setIcon(lucide.icon("refresh-cw", colors().text_muted, 16))
        again.clicked.connect(self._run_checks)
        footer.addWidget(again)
        footer.addStretch(1)
        self.summary = QLabel()
        footer.addWidget(self.summary)
        self.go_live_button = QPushButton("Go live")
        set_props(self.go_live_button, primary=True, big=True)
        self.go_live_button.setIcon(lucide.icon("radio", "#1a0f08", 18))
        self.go_live_button.clicked.connect(self._go_live)
        footer.addWidget(self.go_live_button)
        layout.addLayout(footer)
        self._under(layout, skip="Finish without going live")
        # "Skip" here finishes the guide instead of moving on.
        self._key_changed(c.active_key)
        self._account(c.account)

    def _run_checks(self) -> None:
        self.summary.setText("Checking…")
        self.controller.run_checklist(self._show_checks)

    def _show_checks(self, checks: list[Check]) -> None:
        self.checks = checks
        while self.check_rows.count():
            item = self.check_rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        p = colors()
        for index, check in enumerate(checks):
            self.check_rows.addWidget(self._check_row(check, index == 0))
        self.check_rows.addWidget(self._gift_row(bool(checks)))
        passed = sum(1 for c in checks if c.status == OK)
        optional = sum(1 for c in checks if c.status == INFO)
        fails = sum(1 for c in checks if c.status == FAIL)
        text = f"{passed} of {len(checks)} checks passed" + (f", {optional} optional" if optional else "")
        self.summary.setText(text)
        self.summary.setStyleSheet(f"color: {p.live if fails else p.healthy}; font-size: 14px;")

    def _check_row(self, check: Check, first: bool) -> QWidget:
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        if not first:
            column.addWidget(hline())
        row = QHBoxLayout()
        row.setContentsMargins(0, 12, 0, 12)
        row.setSpacing(12)
        mark, role = CHECK_MARKS[check.status]
        icon = QLabel()
        icon.setPixmap(glyph_pixmap(mark, getattr(colors(), role), 20))
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(icon)
        text = QVBoxLayout()
        text.setSpacing(1)
        title = QLabel(check.title)
        title.setWordWrap(True)
        title.setStyleSheet("font-weight: 600;")
        text.addWidget(title)
        if check.detail:
            text.addWidget(guide_note(check.detail))
        row.addLayout(text, 1)
        if check.fix:
            fix = QPushButton(check.fix_label or "Fix")
            fix.clicked.connect(lambda _=False, f=check.fix: self._fix(f))
            row.addWidget(fix, 0, Qt.AlignmentFlag.AlignTop)
        column.addLayout(row)
        return holder

    def _gift_row(self, divider: bool) -> QWidget:
        """The one test you have to judge yourself: did the alert show, and did you hear it?"""
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        if divider:
            column.addWidget(hline())
        row = QHBoxLayout()
        row.setContentsMargins(0, 12, 0, 12)
        row.setSpacing(12)
        icon = QLabel()
        icon.setPixmap(lucide.pixmap("volume", "#4f7be8", 20))
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(icon)
        text = QVBoxLayout()
        text.setSpacing(1)
        title = QLabel("Gift alert and sound")
        title.setStyleSheet("font-weight: 600;")
        text.addWidget(title)
        self.gift_detail = guide_note("Press Send test. Did the alert play, with sound?")
        text.addWidget(self.gift_detail)
        row.addLayout(text, 1)
        send = QPushButton("Send test")
        send.clicked.connect(lambda: self.controller.simulate(*sample_events("alerts")))
        yes = QPushButton("Yes")
        no = QPushButton("No")
        yes.clicked.connect(lambda: self.gift_detail.setText("Great: alerts and sound work."))
        no.clicked.connect(lambda: self.gift_detail.setText(
            "Check that the Alerts overlay is in OBS (step 4) and that its audio isn't muted in OBS's mixer."))
        for widget in (send, yes, no):
            row.addWidget(widget, 0, Qt.AlignmentFlag.AlignTop)
        column.addLayout(row)
        return holder

    def _fix(self, fix: str) -> None:
        from ignite_live.ui.checklist import run_fix

        run_fix(self.window(), self.controller, fix)
        if not fix.startswith(("open:", "add_overlay:")):
            QTimer.singleShot(1500, self._run_checks)  # Give OBS a moment, then show the result.

    def _show_preview(self) -> None:
        url = self.controller.backup_link("alerts") + "&demo=1"
        if not self.preview.show_url(url):
            self.preview.hide()
            self.preview_hint.show()

    def _go_live(self) -> None:
        from ignite_live.ui.checklist import go_live

        window = self.window()
        self._finish()
        go_live(window, self.controller)

    def _account(self, info: AccountInfo | None) -> None:
        if not hasattr(self, "account"):
            return
        if info is None:
            text = "Not signed in to Streamlabs."
        elif info.can_be_live:
            text = f"Signed in as <b>@{_escape(info.username)}</b>, who can go LIVE."
        else:
            text = (f"Signed in as <b>@{_escape(info.username)}</b>, but TikTok hasn't approved LIVE for this "
                    f"account yet ({_escape(info.application_status.lower())}).")
        self.account.setText(text)
        set_ready(self.account_tile, info is not None and info.can_be_live)
        self.sign_in.setVisible(info is None)
        self.get_key.setEnabled(info is not None)

    def _key_changed(self, key) -> None:
        if not hasattr(self, "key_status"):
            return
        self.key_status.setText(
            f"<span style='color: {colors().healthy}'><b>A stream key is ready.</b></span> Send it to OBS, and OBS "
            "can go live." if key is not None else "No stream key yet."
        )

    def _save_key(self) -> None:
        if self.key_server.text().strip() and self.key_value.text().strip():
            self.controller.use_manual_key(self.key_server.text().strip(), self.key_value.text().strip())
            self.key_value.clear()
