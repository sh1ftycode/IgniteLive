"""File → Preferences: every setting in one standard dialog (OK / Cancel / Apply).

Sections are listed on the left. Secrets go to the system keychain; a blank
secret field keeps the saved value.
"""

from __future__ import annotations

import html
import re
from dataclasses import replace

from PySide6.QtCore import QObject, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import REMOTE_CONTROL, Controller, command_aliases
from ignite_live.core import keychain
from ignite_live.core.events import PLATFORMS
from ignite_live.core.settings import COMMAND_DEFAULTS, ROLES, Settings
from ignite_live.core.templates import BY_KEY, GROUPS, PLACEHOLDERS, SAMPLE, TEMPLATES, render, unknown_placeholders
from ignite_live.integrations.discord_bridge import valid_webhook_url
from ignite_live.integrations.servertap import DEFAULT_URL as DEFAULT_SERVERTAP_URL, ServerTapError
from ignite_live.integrations.servertap import check_url as check_servertap_url
from ignite_live.live.presence import MODULES as PRESENCE_MODULES, PRESETS as PRESENCE_PRESETS
from ignite_live.live.presence import Snapshot, build as build_presence, clean_presence
from ignite_live.live.presence import parse_config as parse_presence

# The Rich Presence preview's sample values.
PRESENCE_SAMPLE = Snapshot(live={"tiktok": True}, usernames={"tiktok": "you"}, viewers=152, likes=12_400,
                           followers=8_100, song="Blinding Lights", artist="The Weeknd", requester="Ember",
                           game="Tug-of-war", started=1.0, icon_url="https://example.com/icon.png")
from ignite_live.music.commands import CommandKind
from ignite_live.music.youtube import video_id_from
from ignite_live.core.seasons import SEASONAL, SEASONAL_LABEL
from ignite_live.overlay.server import THEMES as OVERLAY_THEMES
from ignite_live.ui.pages.stream import disclosure, note
from ignite_live.speech.voices import PROVIDERS, tiktok_voices, valid_session
from ignite_live.ui.theme import THEMES as APP_THEMES
from ignite_live.ui.theme import resolve_theme

ROLE_NAMES = {"everyone": "Everyone", "followers": "Followers (Twitch subs, YouTube members)", "mods": "Moderators"}
TABS = ("OBS", "Chat platforms", "Server", "Song requests", "Commands", "Text to speech", "Moderation", "Messages",
        "Discord", "Minecraft", "Appearance", "Experimental", "Advanced")
MOD_PLACES = {"tts": "Text to speech", "overlay": "Chat and alerts on stream", "discord": "Discord",
              "songs": "Song titles and requests", "rules": "Viewer names and text in rule alerts"}
PANIC_STEPS = {"tts": "Mute text to speech", "alerts": "Clear and hold alerts", "music": "Pause the music",
               "rules": "Pause action rules and stop the running game"}
COMMAND_LABELS = {
    "request": "Request a song",
    "skip": "Skip",
    "revoke": "Remove my request",
    "current": "What's playing",
    "queue": "Queue size",
    "tts_skip": "Skip spoken message (mods)",
    "clip": "Save a clip (mods)",
    "scene": "Switch scene (mods)",
    "points": "Points balance",
    "bump": "Move my song up (points)",
    "say": "Paid text to speech (points)",
    "sound": "Play a sound (points)",
    "give": "Give points (mods)",
    "block": "Block a viewer (mods)",
    "unblock": "Unblock a viewer (mods)",
    "panic": "Panic button (mods, if allowed)",
}


def _secret(name: str) -> QLineEdit:
    widget = QLineEdit()
    widget.setEchoMode(QLineEdit.EchoMode.Password)
    widget.setPlaceholderText("Saved; type to replace" if keychain.load(name) else "Not set")
    return widget


def _spin(value: int, low: int, high: int, suffix: str = "") -> QSpinBox:
    widget = QSpinBox()
    widget.setRange(low, high)
    widget.setValue(value)
    widget.setSuffix(suffix)
    return widget


def _choice(options: dict[str, str], selected: str) -> QComboBox:
    widget = QComboBox()
    for key, name in options.items():
        widget.addItem(name, key)
    widget.setCurrentIndex(max(0, widget.findData(selected)))
    return widget


def _slider(value: int, low: int, high: int) -> tuple[QHBoxLayout, QSlider]:
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(low, high)
    slider.setValue(value)
    shown = QLabel(str(value))
    shown.setMinimumWidth(shown.fontMetrics().horizontalAdvance("-100"))
    slider.valueChanged.connect(lambda v: shown.setText(str(v)))
    row = QHBoxLayout()
    row.addWidget(slider, 1)
    row.addWidget(shown)
    return row, slider


# Settings are grouped the way people look for them; each group shows its sections as cards, one under another.
SETTING_GROUPS = (
    ("General", "sliders", "Song requests, chat commands and text to speech.",
     ("Song requests", "Commands", "Text to speech")),
    ("Appearance", "palette", "How Ignite and your overlays look.", ("Appearance",)),
    ("Streaming", "radio", "OBS: the connection, and what happens when you go live.", ("OBS",)),
    ("Integrations", "puzzle", "Chat platforms, your Ignite server, Discord and Minecraft.",
     ("Chat platforms", "Server", "Discord", "Minecraft")),
    ("Notifications", "bell", "Everything Ignite says to viewers: chat replies, overlays, speech and Discord.",
     ("Messages",)),
    ("Privacy", "shield", "Blocked words and viewers, links, and what Panic does.", ("Moderation",)),
    ("Advanced", "wrench", "Updates, experiments and the rest.", ("Experimental", "Advanced")),
    ("About", "info", "Version, files and help.", ("About",)),
)
GROUP_OF = {section: index for index, (_name, _icon, _text, sections) in enumerate(SETTING_GROUPS)
            for section in sections}


class _SectionSignals(QObject):
    """``currentRowChanged(section index)``: what callers of the old list-and-stack watched."""

    currentRowChanged = Signal(int)  # noqa: N815 - mirrors QListWidget


class Sections(QWidget):
    """Settings groups on the left (General, Appearance, …), the chosen group's sections as cards on the right.

    Keeps a tab-like API: ``addTab(page, section title)``, and indexes that follow the order sections were
    added (``TABS``)."""

    def __init__(self) -> None:
        super().__init__()
        from ignite_live.ui.kit import Card, label
        from ignite_live.ui import lucide
        from ignite_live.ui.theme import colors

        self._Card, self._label, self._lucide, self._colors = Card, label, lucide, colors
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("settingsNav")
        self.nav.setFixedWidth(208)
        self.nav.setIconSize(QSize(16, 16))
        self.stack = QStackedWidget()
        layout.addWidget(self.nav)
        layout.addWidget(self.stack, 1)
        self.list = _SectionSignals(self)  # The old name, for code that watches which section is showing.
        self.titles: list[str] = []
        self.cards: dict[str, QWidget] = {}
        self.columns: dict[int, QVBoxLayout] = {}
        self.scrolls: dict[int, QScrollArea] = {}
        self.group_rows: dict[int, int] = {}
        self.nav.currentRowChanged.connect(self._group_chosen)

    def _group_page(self, group: int) -> QVBoxLayout:
        if group not in self.columns:
            name, icon, text, _sections = SETTING_GROUPS[group]
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            holder = QWidget()
            holder.setObjectName("page")
            column = QVBoxLayout(holder)
            column.setContentsMargins(24, 4, 24, 24)
            column.setSpacing(16)
            column.addWidget(self._label(name, "sectionTitle"))
            column.addWidget(self._label(text, "muted", wrap=True))
            column.addStretch(1)
            scroll.setWidget(holder)
            self.columns[group], self.scrolls[group] = column, scroll
            # Groups go in their fixed order, whatever order their first section arrives in.
            position = sorted(self.columns).index(group)
            self.stack.insertWidget(position, scroll)
            item = QListWidgetItem(self._lucide.icon(icon, self._colors().text_muted, 16), name)
            item.setData(Qt.ItemDataRole.UserRole, group)
            item.setSizeHint(QSize(0, 36))
            self.nav.insertItem(position, item)
            if self.nav.currentRow() < 0:
                self.nav.setCurrentRow(0)
        return self.columns[group]

    def addTab(self, page: QWidget, title: str) -> None:  # noqa: N802 - mirrors QTabWidget
        group = GROUP_OF.get(title, len(SETTING_GROUPS) - 2)
        column = self._group_page(group)
        card = self._Card(title)
        card.body.addWidget(page)
        column.insertWidget(column.count() - 1, card)
        self.titles.append(title)
        self.cards[title] = card

    def _group_chosen(self, row: int) -> None:
        if row < 0:
            return
        group = self.nav.item(row).data(Qt.ItemDataRole.UserRole)
        self.stack.setCurrentWidget(self.scrolls[group])
        for title in SETTING_GROUPS[group][3]:
            if title in self.titles:
                self.list.currentRowChanged.emit(self.titles.index(title))

    def show_section(self, title: str) -> None:
        if title not in self.cards:
            return
        group = GROUP_OF.get(title, len(SETTING_GROUPS) - 2)
        for row in range(self.nav.count()):
            if self.nav.item(row).data(Qt.ItemDataRole.UserRole) == group:
                self.nav.setCurrentRow(row)
                break
        self.scrolls[group].ensureWidgetVisible(self.cards[title], 0, 24)

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if 0 <= index < len(self.titles):
            self.show_section(self.titles[index])

    def currentIndex(self) -> int:  # noqa: N802
        row = self.nav.currentRow()
        if row < 0:
            return 0
        group = self.nav.item(row).data(Qt.ItemDataRole.UserRole)
        first = next((t for t in SETTING_GROUPS[group][3] if t in self.titles), None)
        return self.titles.index(first) if first else 0

    def setTabVisible(self, index: int, visible: bool) -> None:  # noqa: N802
        if 0 <= index < len(self.titles):
            self.cards[self.titles[index]].setVisible(visible)


def banned_text(s: Settings) -> str:
    """The banned list as the editor shows it: one id per line, with why it was auto-banned."""
    return "\n".join(f"{video_id}  # {s.banned_reasons[video_id]}" if video_id in s.banned_reasons else video_id
                     for video_id in s.banned_video_ids)


def _lines(edit: QPlainTextEdit) -> list[str]:
    found = []
    for line in edit.toPlainText().splitlines():
        if line.strip() and line.strip() not in found:
            found.append(line.strip())
    return found


def _tab(tabs: Sections, title: str) -> QFormLayout:
    page = QWidget()
    form = QFormLayout(page)
    tabs.addTab(page, title)
    return form


class ColorButton(QPushButton):
    """Pick a colour, or leave it empty for the theme's own."""

    def __init__(self, value: str) -> None:
        super().__init__()
        self.value = value
        self.clicked.connect(self._pick)
        self._show()

    def _pick(self) -> None:
        color = QColorDialog.getColor(QColor(self.value or "#7c6cff"), self, "Overlay colour")
        if color.isValid():
            self.value = color.name()
            self._show()

    def clear(self) -> None:
        self.value = ""
        self._show()

    def _show(self) -> None:
        self.setText(self.value or "Theme default")
        self.setStyleSheet(f"QPushButton {{ border-left: 14px solid {self.value}; }}" if self.value else "")


class MessagesEditor(QWidget):
    """Every viewer-facing message: pick one on the left, edit it with a live preview."""

    def __init__(self, overrides: dict[str, str]) -> None:
        super().__init__()
        self.texts = dict(overrides)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(note("Use {placeholders} to insert details. Changes apply to chat replies, overlays, "
                              "text to speech, and Discord as soon as you click Apply."))
        split = QSplitter()
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        groups = {}
        for group in GROUPS:
            groups[group] = QTreeWidgetItem(self.tree, [group])
            groups[group].setFlags(Qt.ItemFlag.ItemIsEnabled)
            groups[group].setExpanded(True)
        for template in TEMPLATES:
            item = QTreeWidgetItem(groups[template.group], [template.label])
            item.setData(0, Qt.ItemDataRole.UserRole, template.key)
        split.addWidget(self.tree)

        editor = QWidget()
        form = QVBoxLayout(editor)
        form.setContentsMargins(6, 0, 0, 0)
        self.title = QLabel("")
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.edit = QPlainTextEdit()
        self.edit.setFixedHeight(self.edit.fontMetrics().height() * 4)
        self.edit.textChanged.connect(self._changed)
        self.reset = QPushButton("Reset to Default")
        self.reset.clicked.connect(lambda: self.edit.setPlainText(BY_KEY[self.key].default))
        self.placeholders = QLabel("")
        self.placeholders.setWordWrap(True)
        self.placeholders.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        form.addWidget(self.title)
        form.addWidget(self.edit)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.reset)
        form.addLayout(row)
        form.addWidget(QLabel("Preview:"))
        form.addWidget(self.preview)
        form.addWidget(self.warning)
        form.addWidget(QLabel("Placeholders:"))
        form.addWidget(self.placeholders)
        form.addStretch(1)
        split.addWidget(editor)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)
        self.key = TEMPLATES[0].key
        self.tree.currentItemChanged.connect(self._select)
        self.tree.setCurrentItem(groups[TEMPLATES[0].group].child(0))

    def _select(self, item: QTreeWidgetItem | None) -> None:
        key = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if not key:
            return
        self.key = key
        template = BY_KEY[key]
        self.title.setText(f"{template.group}: {template.label}")
        self.placeholders.setText("\n".join(f"{{{name}}}  {PLACEHOLDERS.get(name, '')}" for name in template.placeholders)
                                  or "None for this message.")
        self.edit.blockSignals(True)
        self.edit.setPlainText(self.texts.get(key, template.default))
        self.edit.blockSignals(False)
        self._preview()

    def _changed(self) -> None:
        text = self.edit.toPlainText().replace("\n", " ")
        if text == BY_KEY[self.key].default:
            self.texts.pop(self.key, None)
        else:
            self.texts[self.key] = text
        self._preview()

    def _preview(self) -> None:
        text = self.texts.get(self.key, BY_KEY[self.key].default)
        self.preview.setText(render(text, SAMPLE) or "(empty: nothing is shown or said)")
        unknown = unknown_placeholders(self.key, text)
        self.warning.setText(f"Not a placeholder here: {', '.join('{' + u + '}' for u in unknown)}" if unknown else "")
        self.reset.setEnabled(self.key in self.texts)


class PreferencesDialog(QDialog):
    def __init__(self, controller: Controller, parent: QWidget | None = None, tab: str = "OBS") -> None:
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Preferences")
        self.setMinimumSize(820, 560)
        s = controller.settings
        layout = QVBoxLayout(self)
        self.tabs = Sections()
        layout.addWidget(self.tabs, 1)

        form = _tab(self.tabs, "OBS")
        self.obs_url = QLineEdit(s.obs_url)
        form.addRow("OBS WebSocket &URL:", self.obs_url)
        self.obs_password = _secret(keychain.OBS_PASSWORD)
        form.addRow("OBS WebSocket &password:", self.obs_password)
        form.addRow(note("In OBS: Tools → WebSocket Server Settings → Enable, then Show Connect Info."))
        self.obs_record = QCheckBox("Record while live (starts and stops with the stream)")
        self.obs_record.setChecked(s.obs_record_while_live)
        form.addRow("When you go live:", self.obs_record)
        self.obs_replay = QCheckBox("Run the replay buffer while live, so clips can be saved")
        self.obs_replay.setChecked(s.obs_replay_while_live)
        form.addRow("", self.obs_replay)
        self.obs_live_scene = QComboBox()
        self.obs_live_scene.setEditable(True)
        self.obs_live_scene.addItem("")
        self.obs_live_scene.addItems(controller.studio.scenes)
        if controller.studio.vertical is not None:  # A vertical scene works too (Aitum Vertical Canvas).
            self.obs_live_scene.addItems(controller.studio.vertical.scenes)
        self.obs_live_scene.setCurrentText(s.obs_live_scene)
        self.obs_live_scene.lineEdit().setPlaceholderText("Keep the current scene")
        form.addRow("Switch to scene:", self.obs_live_scene)
        self.obs_chat_clip = QCheckBox("Moderators can type !clip to save a clip")
        self.obs_chat_clip.setChecked(s.obs_chat_clip)
        form.addRow("Chat:", self.obs_chat_clip)
        self.obs_chat_scene = QCheckBox("Moderators can type !scene name to switch scenes")
        self.obs_chat_scene.setChecked(s.obs_chat_scene)
        form.addRow("", self.obs_chat_scene)
        self.obs_screenshots = QLineEdit(s.obs_screenshot_dir)
        self.obs_screenshots.setPlaceholderText("Pictures/Ignite")
        form.addRow("Screenshot &folder:", self.obs_screenshots)
        form.addRow(note("Warnings appear when OBS drops frames (your upload can't keep up), skips them while "
                         "rendering (GPU overloaded) or encoding (CPU or encoder overloaded), and when disk space for "
                         "recordings runs low. Only recordings and replay buffers that these automations started are "
                         "stopped when the stream ends."))

        form = _tab(self.tabs, "Chat platforms")
        self.tiktok = QLineEdit(s.tiktok_username)
        self.tiktok.setPlaceholderText("without the @")
        form.addRow("&TikTok username:", self.tiktok)
        # TikTok signed in on the Ignite server's website (Euler Stream): Ignite can act as the streamer.
        self.tiktok_account = QLabel("Checking…")
        self.tiktok_account.setWordWrap(True)
        tiktok_site = QPushButton("Sign In on the Website…")
        tiktok_site.setToolTip("Opens your account on the Ignite server's website, signed in: TikTok is at the top")
        tiktok_site.clicked.connect(lambda: controller.open_server_account(
            lambda url: QDesktopServices.openUrl(QUrl(url))))
        tiktok_row = QHBoxLayout()
        tiktok_row.addWidget(self.tiktok_account, 1)
        tiktok_row.addWidget(tiktok_site)
        form.addRow("TikTok account:", tiktok_row)
        self.tiktok_replies = QCheckBox("Answer song requests in TikTok chat, as you")
        self.tiktok_replies.setChecked(s.tiktok_chat_replies)
        self.tiktok_replies.setToolTip("“@name: added Song (#2)”, refusals and skips, said in your LIVE's chat. At most "
                                       "one message every 3 seconds")
        form.addRow("", self.tiktok_replies)
        form.addRow(note("Sign your TikTok account in on the Ignite server's website (by scanning a QR code with the "
                         "TikTok app) to let Ignite answer requests in your chat, and to make viewers moderators, "
                         "mute them or kick them out from the Viewers tab. Your TikTok password never reaches Ignite."))
        controller.tiktok_account_changed.connect(self._show_tiktok_account)
        controller.refresh_tiktok_account()
        self.twitch = QLineEdit(s.twitch_channel)
        self.twitch.setPlaceholderText("channel name or twitch.tv link")
        form.addRow("T&witch channel:", self.twitch)
        self.youtube = QLineEdit(s.youtube_channel)
        self.youtube.setPlaceholderText("@handle, channel link, or live video link")
        form.addRow("&YouTube channel:", self.youtube)
        form.addRow(note("Chat from every platform you fill in shows up together: in the Chat panel, the chat "
                         "overlay, song requests, text to speech, and Discord. No logins or keys are needed: "
                         "Twitch and YouTube chat are read the same way their own websites show them. "
                         "YouTube chat connects while you're live and checks again every minute."))
        self.twitch_stats = QCheckBox("Show Twitch viewer and follower counts (from DecAPI)")
        self.twitch_stats.setChecked(s.twitch_stats)
        self.twitch_stats.setToolTip("Twitch gives these numbers only to signed-in apps. DecAPI (decapi.me) is a free "
                                     "public service for chat bots; Ignite sends it your channel name once a minute")
        form.addRow("Twitch counts:", self.twitch_stats)
        form.addRow(note("Viewer, follower and like counts come from each platform's public pages. Who's watching is "
                         "only what the platforms reveal: TikTok announces viewers as they join, Twitch lists who "
                         "is in chat (up to about 1,000 names), and YouTube only shows who chats."))

        form = _tab(self.tabs, "Server")
        self.sign_in_state = QLabel()
        self.sign_in_state.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sign_in_state.setWordWrap(True)
        self.sign_in_button = QPushButton("Sign In…")
        self.sign_in_button.setToolTip("Opens the Ignite website: log in there (password or Discord) and approve "
                                       "this PC")
        self.sign_in_button.clicked.connect(self._sign_in)
        self.sign_in_cancel = QPushButton("Cancel")
        self.sign_in_cancel.clicked.connect(controller.cancel_server_sign_in)
        self.sign_out_button = QPushButton("Sign Out")
        self.sign_out_button.setToolTip("Signs this PC out on the server too. Hosted links stop until you sign in again")
        self.sign_out_button.clicked.connect(controller.sign_out_of_server)
        account_row = QHBoxLayout()
        account_row.addWidget(self.sign_in_state, 1)
        for button in (self.sign_in_button, self.sign_in_cancel, self.sign_out_button):
            account_row.addWidget(button)
        form.addRow("Account:", account_row)
        controller.server_sign_in.connect(self._show_sign_in)
        self._show_sign_in(controller.server_account())
        self.remote_control = QCheckBox("Allow remote control")
        self.remote_control.setChecked(s.remote_control)
        remote_note = note("Lets you run Ignite from a browser or phone when you're signed in to the server, and lets "
                           "hosted widgets that have your control key run commands: go live, Panic, scenes, "
                           "recording, clips, music and text to speech. Ignite connects out to the server, so "
                           "nothing on this PC is opened to the internet. Stream keys, tokens and passwords are "
                           "never sent. Hosted widget links (Overlays tab) work without this.")
        form.addRow("", self.remote_control)
        form.addRow(remote_note)
        # REMOTE_CONTROL (controller.py) can hide it; the setting itself is kept.
        self.remote_control.setVisible(REMOTE_CONTROL)
        remote_note.setVisible(REMOTE_CONTROL)
        self._remote_note = remote_note
        account = controller.server_account()
        self._remote_control_locked(bool(account and account.get("username")))
        form.addRow(note("Optional. Sign in to keep your stream key for 48 hours when the app is closed, sync "
                         "settings between computers, run song search, and use Ignite from a browser or your "
                         "phone. Every signed-in PC is listed on the website under Account, where you can sign it out."))

        form = _tab(self.tabs, "Song requests")
        self.request_role = _choice({r: ROLE_NAMES[r] for r in ROLES}, s.request_role)
        form.addRow("Who can &request:", self.request_role)
        self.skip_role = _choice({r: ROLE_NAMES[r] for r in ROLES}, s.skip_role)
        form.addRow("Who can &skip any song:", self.skip_role)
        self.cooldown = _spin(s.user_cooldown_s, 0, 3600, " s")
        form.addRow("&Cooldown per viewer:", self.cooldown)
        self.per_user = _spin(s.max_per_user, 1, 50)
        form.addRow("Songs per &viewer:", self.per_user)
        self.max_queue = _spin(s.max_queue, 1, 500)
        form.addRow("&Queue size:", self.max_queue)
        self.max_minutes = _spin(s.max_minutes, 1, 120, " min")
        form.addRow("&Longest song:", self.max_minutes)
        self.banned = QPlainTextEdit(banned_text(s))
        self.banned.setPlaceholderText("One YouTube link or video ID per line (text after # is a note)")
        self.banned.setFixedHeight(self.banned.fontMetrics().height() * 5)
        form.addRow("&Banned songs:", self.banned)
        self.refuse_unknown = QCheckBox("Refuse songs whose length can't be found")
        self.refuse_unknown.setChecked(s.refuse_unknown_length)
        form.addRow("", self.refuse_unknown)
        self.refuse_explicit = QCheckBox("Refuse explicit songs (titles marked Explicit); search picks clean "
                                         "versions")
        self.refuse_explicit.setChecked(s.refuse_explicit)
        form.addRow("", self.refuse_explicit)
        self.auto_ban = QCheckBox("Ban songs refused for being too long, explicit, or a blocked word")
        self.auto_ban.setChecked(s.auto_ban)
        form.addRow("", self.auto_ban)
        form.addRow(note("YouTube has no explicit flag, so only titles that say so are caught. Copyright still applies to anything played on stream."))
        self.youtube_key = _secret(keychain.YOUTUBE_API_KEY)
        form.addRow("YouTube &API key:", self.youtube_key)
        form.addRow(note("Optional. Song search works without it; with a key it uses Google's official "
                         "YouTube API (about 100 searches a day for free)."))

        form = _tab(self.tabs, "Commands")
        self.command_prefix = QLineEdit(s.command_prefix)
        self.command_prefix.setMaxLength(3)
        self.command_prefix.setPlaceholderText("!")
        form.addRow("Command &prefix:", self.command_prefix)
        self.command_names: dict[str, QLineEdit] = {}
        for kind, default in COMMAND_DEFAULTS.items():
            field = QLineEdit(s.command_names.get(kind, ""))
            field.setPlaceholderText(default)
            form.addRow(f"{COMMAND_LABELS[kind]}:", field)
            self.command_names[kind] = field
        form.addRow(note("Separate several names with commas, e.g. “sr, play, request”. Leave a field empty for "
                         "the default shown in grey. Works the same on TikTok, Twitch, and YouTube."))

        self._tts_page(s)
        self._moderation_page(s)

        self.messages = MessagesEditor(s.texts)
        self.tabs.addTab(self.messages, "Messages")

        form = _tab(self.tabs, "Discord")
        self.discord_enabled = QCheckBox("Forward chat to Discord")
        self.discord_enabled.setChecked(s.discord_enabled)
        form.addRow(self.discord_enabled)
        self.discord_url = _secret(keychain.DISCORD_WEBHOOK)
        form.addRow("&Webhook URL:", self.discord_url)
        forward = QHBoxLayout()
        self.discord_comments = QCheckBox("Comments")
        self.discord_comments.setChecked(s.discord_comments)
        self.discord_gifts = QCheckBox("Gifts")
        self.discord_gifts.setChecked(s.discord_gifts)
        self.discord_follows = QCheckBox("Follows")
        self.discord_follows.setChecked(s.discord_follows)
        for check in (self.discord_comments, self.discord_gifts, self.discord_follows):
            forward.addWidget(check)
        forward.addStretch(1)
        form.addRow("Forward:", forward)
        test = QPushButton("Send Test Message")
        test.clicked.connect(self._test_discord)
        form.addRow("", test)
        form.addRow(note("Discord: Channel settings → Integrations → Webhooks → New Webhook → Copy Webhook URL. "
                         "Pings and link previews are disabled; messages are batched every few seconds. "
                         "Change the line format in Messages → Discord."))

        self._presence_section(form, s)

        form = _tab(self.tabs, "Minecraft")
        self.minecraft_url = QLineEdit(s.minecraft_url)
        self.minecraft_url.setPlaceholderText(DEFAULT_SERVERTAP_URL)
        form.addRow("ServerTap &address:", self.minecraft_url)
        self.minecraft_key = _secret(keychain.MINECRAFT_KEY)
        form.addRow("ServerTap &key:", self.minecraft_key)
        self.minecraft_state = QLabel()
        self.minecraft_state.setWordWrap(True)
        self.minecraft_state.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        test = QPushButton("Test Connection")
        test.clicked.connect(self._test_minecraft)
        minecraft_row = QHBoxLayout()
        minecraft_row.addWidget(test)
        minecraft_row.addWidget(self.minecraft_state, 1)
        form.addRow("", minecraft_row)
        form.addRow(note("Gifts, follows and likes can run commands on your Minecraft server (Actions tab → "
                         "Minecraft). The server needs the ServerTap plugin: its key is “key” in "
                         "plugins/ServerTap/config.yml. Change it from “change_me” to a long random one there "
                         "first, and keep port 4567 closed to the internet. For TT Interactive Stream Games, use "
                         "Actions → Add TT Interactive Rules."))

        form = _tab(self.tabs, "Appearance")
        # "Seasonal" stays selected as itself; an old or unknown value shows what it looks like now.
        self.ui_theme = _choice(APP_THEMES, s.ui_theme if s.ui_theme in APP_THEMES else resolve_theme(s.ui_theme))
        form.addRow("&Mode:", self.ui_theme)
        self.close_to_tray = QCheckBox("Keep running in the system tray when the window is closed")
        self.close_to_tray.setChecked(s.close_to_tray)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.close_to_tray.setEnabled(False)
            self.close_to_tray.setToolTip("Your desktop has no system tray")
        form.addRow("Window:", self.close_to_tray)
        self.overlay_theme = _choice({SEASONAL: SEASONAL_LABEL, **OVERLAY_THEMES}, s.overlay_theme)
        form.addRow("&Overlay theme:", self.overlay_theme)
        self.overlay_accent = ColorButton(s.overlay_accent)
        self.overlay_accent_2 = ColorButton(s.overlay_accent_2)
        for label, button in (("Overlay accent:", self.overlay_accent), ("Second accent:", self.overlay_accent_2)):
            clear = QPushButton("Reset")
            clear.clicked.connect(button.clear)
            row = QHBoxLayout()
            row.addWidget(button, 1)
            row.addWidget(clear)
            form.addRow(label, row)
        self.overlay_font = QComboBox()
        self.overlay_font.setEditable(True)
        self.overlay_font.addItem("")
        self.overlay_font.addItems(QFontDatabase.families())
        self.overlay_font.setCurrentText(s.overlay_font)
        self.overlay_font.lineEdit().setPlaceholderText("Theme default")
        form.addRow("Overlay &font:", self.overlay_font)
        self.overlay_css = QPlainTextEdit(s.overlay_css)
        self.overlay_css.setPlaceholderText(".card { border-radius: 0; }\n.line .text { font-size: 1.2em; }")
        self.overlay_css.setFixedHeight(self.overlay_css.fontMetrics().height() * 7)
        form.addRow("Custom &CSS:", self.overlay_css)
        form.addRow(note("Overlay changes reach every OBS overlay live. Colours, font and CSS go on top of the "
                         "theme. Each overlay's URL also takes options (see the Overlays tab). Fonts must be "
                         "installed on this computer."))

        form = _tab(self.tabs, "Experimental")
        form.addRow(note("These features work, but they can change or stop working without notice. They are off "
                         "unless you turn them on."))
        form.addRow(QLabel("<b>TikTok chat</b>"))
        self.tiktok_via_server = QCheckBox("Read TikTok chat through the Ignite server")
        self.tiktok_via_server.setChecked(s.tiktok_via_server)
        self.tiktok_via_server.setToolTip("Off by default: this PC already reads TikTok chat itself for free. "
                                          "Turning this on routes it through the server's Euler Stream key instead, "
                                          "which every signed-in account shares.")
        form.addRow("", self.tiktok_via_server)
        form.addRow(note("Uses the Ignite server and may be unstable. Leave this off unless your own connection is unreliable: the server's TikTok key is "
                         "shared by every signed-in account, so it's best saved for dashboards reading chat while "
                         "this PC is off, which always works regardless of this setting."))
        form.addRow(QLabel("<b>Going live</b>"))
        self.experimental_relaunch = QCheckBox("Change the TikTok title and category while live")
        self.experimental_relaunch.setChecked(s.experimental_relaunch)
        form.addRow("", self.experimental_relaunch)
        form.addRow(note("Adds Update While Live to the Stream tab. TikTok can't change a LIVE that's running, so "
                         "Ignite gets a new Streamlabs key with the new title and category, puts it on your OBS "
                         "output and restarts the LIVE. Viewers see it end and a new one start, and some may "
                         "not follow. Uses Streamlabs' private API: it may stop working without notice."))

        form = _tab(self.tabs, "Advanced")
        self.port = _spin(s.overlay_port, 1024, 65535)
        form.addRow("Overlay &port:", self.port)
        form.addRow(note("Changing the port changes every overlay URL. Takes effect after a restart."))
        self.sign_key = _secret(keychain.SIGN_API_KEY)
        form.addRow("&Euler Stream API key:", self.sign_key)
        form.addRow(note("Optional. Raises TikTok chat rate limits."))
        self.auto_update = QCheckBox("Keep Ignite up to date automatically")
        self.auto_update.setChecked(s.auto_update)
        form.addRow("&Updates:", self.auto_update)
        form.addRow(note("Checks Ignite's GitHub releases every few hours and downloads a new version in the "
                         "background. It installs when you quit Ignite, or right away from the banner, never in "
                         "the middle of a stream. Help → Check for Updates checks now."))

        self.message = QLabel("")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply
        )
        box.accepted.connect(self._ok)
        box.rejected.connect(self.reject)
        box.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        layout.addWidget(box)
        self.button_box = box  # The Settings screen hides it and gives the page its own Save bar.
        if tab in TABS:
            self.tabs.setCurrentIndex(TABS.index(tab))
            if tab == "Text to speech":
                self._provider_changed()

    def _moderation_page(self, s: Settings) -> None:
        form = _tab(self.tabs, "Moderation")
        form.addRow(note("Ignite can't delete messages on TikTok, Twitch or YouTube: chat there is read-only for "
                         "apps. This decides what Ignite reads aloud, shows on stream, sends to Discord, and plays."))
        self.blocked_words = QPlainTextEdit("\n".join(s.blocked_words))
        self.blocked_words.setPlaceholderText("One word or phrase per line. bad* also blocks badly, baddest…")
        self.blocked_words.setFixedHeight(self.blocked_words.fontMetrics().height() * 6)
        form.addRow("&Blocked words:", self.blocked_words)
        form.addRow(note("Whole words, any case, and common dodges like b.a.d, b a d, b4d or baaad."))
        places = QGridLayout()
        self.mod_places: dict[str, QCheckBox] = {}
        for index, (key, label) in enumerate(MOD_PLACES.items()):
            check = QCheckBox(label)
            check.setChecked(getattr(s, f"mod_filter_{key}"))
            places.addWidget(check, index // 2, index % 2)
            self.mod_places[key] = check
        form.addRow("Apply to:", places)
        self.mod_chat_mode = _choice({"mask": "Show it with the words as ***", "hide": "Leave the message out"},
                                     s.mod_chat_mode)
        form.addRow("Chat on stream:", self.mod_chat_mode)
        self.mod_hide_links = QCheckBox("Hide links on stream and in Discord (shown as [link])")
        self.mod_hide_links.setChecked(s.mod_hide_links)
        form.addRow("", self.mod_hide_links)
        self.blocked_users = QPlainTextEdit("\n".join(s.blocked_users))
        self.blocked_users.setPlaceholderText("One name per line (any platform), or block from the Viewers tab")
        self.blocked_users.setFixedHeight(self.blocked_users.fontMetrics().height() * 4)
        form.addRow("Blocked &viewers:", self.blocked_users)
        form.addRow(note("Blocked viewers' messages only show in your own chat panel: no text to speech, commands, "
                         "song requests, or points. Moderators can type !block @name and !unblock @name."))

        heading = QLabel("<b>Panic button</b> (toolbar, or Ctrl+Shift+P while Ignite is focused)")
        form.addRow(heading)
        self.panic_checks: dict[str, QCheckBox] = {}
        for key, label in PANIC_STEPS.items():
            check = QCheckBox(label)
            check.setChecked(getattr(s, f"panic_{key}"))
            form.addRow("", check)
            self.panic_checks[key] = check
        self.panic_scene = QComboBox()
        self.panic_scene.setEditable(True)
        self.panic_scene.addItem("")
        studio = self.controller.studio
        self.panic_scene.addItems(list(studio.scenes) + (list(studio.vertical.scenes) if studio.vertical else []))
        self.panic_scene.setCurrentText(s.panic_scene)
        self.panic_scene.lineEdit().setPlaceholderText("Stay on the current scene")
        form.addRow("Switch to scene:", self.panic_scene)
        self.panic_inputs = QListWidget()
        names = [a.name for a in studio.audio] + [n for n in s.panic_mute_inputs if n not in {a.name for a in studio.audio}]
        for name in names:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name in s.panic_mute_inputs else Qt.CheckState.Unchecked)
            self.panic_inputs.addItem(item)
        self.panic_inputs.setFixedHeight(self.panic_inputs.fontMetrics().height() * 5 + 8)
        form.addRow("Mute in OBS:", self.panic_inputs)
        if not names:
            form.addRow("", note("Connect OBS to choose audio sources to mute."))
        self.panic_chat = QCheckBox("Moderators can type !panic")
        self.panic_chat.setChecked(s.panic_chat_command)
        form.addRow("", self.panic_chat)
        form.addRow(note("Resume puts back only what panic changed. It doesn't switch scenes back if you've changed "
                         "scene yourself in the meantime."))

    def _tts_page(self, s: Settings) -> None:
        form = _tab(self.tabs, "Text to speech")
        self.tts_enabled = QCheckBox("Read chat aloud")
        self.tts_enabled.setChecked(s.tts_enabled)
        form.addRow(self.tts_enabled)
        platforms = QHBoxLayout()
        self.tts_platforms: dict[str, QCheckBox] = {}
        for key, name in PLATFORMS.items():
            check = QCheckBox(name)
            check.setChecked(key in s.tts_platforms)
            platforms.addWidget(check)
            self.tts_platforms[key] = check
        platforms.addStretch(1)
        form.addRow("From:", platforms)
        read = QHBoxLayout()
        self.tts_comments = QCheckBox("Comments")
        self.tts_comments.setChecked(s.tts_comments)
        self.tts_gifts = QCheckBox("Gifts, bits, Super Chats, subs")
        self.tts_gifts.setChecked(s.tts_gifts)
        self.tts_follows = QCheckBox("Follows")
        self.tts_follows.setChecked(s.tts_follows)
        for check in (self.tts_comments, self.tts_gifts, self.tts_follows):
            read.addWidget(check)
        read.addStretch(1)
        form.addRow("Read:", read)
        self.tts_role = _choice({r: ROLE_NAMES[r] for r in ROLES}, s.tts_role)
        form.addRow("&Whose comments:", self.tts_role)
        self.tts_command = QLineEdit(s.tts_command)
        self.tts_command.setPlaceholderText("empty: read every comment; e.g. tts: only “!tts …”")
        form.addRow("Only with co&mmand:", self.tts_command)
        self.tts_skip_commands = QCheckBox("Don't read other commands (like !sr)")
        self.tts_skip_commands.setChecked(s.tts_skip_commands)
        form.addRow("", self.tts_skip_commands)
        self.tts_skip_links = QCheckBox("Say “link” instead of reading links")
        self.tts_skip_links.setChecked(s.tts_skip_links)
        form.addRow("", self.tts_skip_links)
        self.tts_max_chars = _spin(s.tts_max_chars, 10, 500, " characters")
        form.addRow("&Longest message:", self.tts_max_chars)
        self.tts_cooldown = _spin(s.tts_user_cooldown_s, 0, 3600, " s")
        form.addRow("Cooldown per &viewer:", self.tts_cooldown)
        self.tts_max_queue = _spin(s.tts_max_queue, 1, 100)
        form.addRow("Messages &waiting:", self.tts_max_queue)
        form.addRow("Blocked words:", note("In the Moderation section; they apply to everything."))

        self.tts_provider = _choice(PROVIDERS, s.tts_provider if s.tts_provider in PROVIDERS else "edge")
        form.addRow("&Voices:", self.tts_provider)
        self.voice_pages = QStackedWidget()
        form.addRow(self.voice_pages)

        # Edge: a language, then a voice in it (the list comes from Microsoft, loaded on demand).
        page = QWidget()
        edge = QFormLayout(page)
        edge.setContentsMargins(0, 0, 0, 0)
        self.edge_language = QComboBox()
        self.edge_voice = QComboBox()
        self.edge_voice.addItem(s.tts_edge_voice, s.tts_edge_voice)  # Until the list arrives.
        self._edge_voices: list = []
        self.edge_language.currentIndexChanged.connect(self._fill_edge_voices)
        edge.addRow("Language:", self.edge_language)
        edge.addRow("Voice:", self.edge_voice)
        edge.addRow(note("Natural voices from Microsoft (the ones Edge reads pages aloud with). Free, no account; "
                         "needs internet."))
        self.voice_pages.addWidget(page)

        # TikTok: its voice IDs, and the streamer's session cookie.
        page = QWidget()
        tiktok = QFormLayout(page)
        tiktok.setContentsMargins(0, 0, 0, 0)
        self.tiktok_voice = QComboBox()
        self.tiktok_voice.setEditable(True)  # Any voice ID TikTok has can be typed.
        for voice in tiktok_voices():
            self.tiktok_voice.addItem(f"{voice.name} ({voice.id})", voice.id)
        found = self.tiktok_voice.findData(s.tts_tiktok_voice)
        if found < 0:
            self.tiktok_voice.addItem(s.tts_tiktok_voice, s.tts_tiktok_voice)
            found = self.tiktok_voice.count() - 1
        self.tiktok_voice.setCurrentIndex(found)
        tiktok.addRow("Voice:", self.tiktok_voice)
        self.tiktok_session = _secret(keychain.TIKTOK_SESSION)
        tiktok.addRow("Session ID:", self.tiktok_session)
        tiktok.addRow(note(
            "TikTok only makes its voices for logged-in accounts, so this uses yours. To get the session ID: sign in at "
            "tiktok.com in Chrome or Edge, press F12 → Application → Cookies → https://www.tiktok.com, and copy the "
            "value of “sessionid”. It's kept in your system keychain and only sent to TikTok.\n"
            "Treat it like a password: anyone with it is logged in as you. Signing out of TikTok in that browser "
            "makes it stop working (paste a new one then). This uses TikTok's app endpoint, not an official API."
        ))
        self.voice_pages.addWidget(page)

        # This computer's voices.
        page = QWidget()
        system = QFormLayout(page)
        system.setContentsMargins(0, 0, 0, 0)
        self.tts_engine = QComboBox()
        self.tts_engine.addItem("System default", "")
        self.tts_voice = QComboBox()
        self.tts_voice.addItem("Default voice", "")
        self._voice_wanted = s.tts_voice
        self.tts_engine.currentIndexChanged.connect(self._load_voices)
        self._engines_loaded = False
        load = QPushButton("Load Voices")
        load.clicked.connect(self._load_engines)
        engine_row = QHBoxLayout()
        engine_row.addWidget(self.tts_engine, 1)
        engine_row.addWidget(load)
        system.addRow("Engine:", engine_row)
        system.addRow("Voice:", self.tts_voice)
        if s.tts_voice:
            self.tts_voice.addItem(s.tts_voice, s.tts_voice)
            self.tts_voice.setCurrentIndex(1)
        system.addRow(note("Works offline, but most of these sound robotic. On Linux, install speech-dispatcher and "
                           "espeak-ng first."))
        self.voice_pages.addWidget(page)
        self.tts_provider.currentIndexChanged.connect(self._provider_changed)

        row, self.tts_rate = _slider(s.tts_rate, -100, 100)
        form.addRow("Speed:", row)
        row, self.tts_pitch = _slider(s.tts_pitch, -100, 100)
        form.addRow("Pitch:", row)
        row, self.tts_volume = _slider(s.tts_volume, 0, 100)
        form.addRow("Volume:", row)
        self.tts_sample = QLineEdit("this song is fire")
        test = QPushButton("Test Voice")
        test.clicked.connect(lambda: self.controller.test_tts(self._settings_from_form(), self.tts_sample.text(),
                                                              self.tiktok_session.text().strip()))
        sample = QHBoxLayout()
        sample.addWidget(self.tts_sample, 1)
        sample.addWidget(test)
        form.addRow("Try it:", sample)
        form.addRow(note("What's said is set in Messages → Text to speech (default: “From {platform}, {user} says "
                         "{message}”). OBS picks it up through Desktop Audio, or an Application Audio Capture of "
                         "Ignite. Moderators can type !skiptts to stop a message; Chat → Mute Text to Speech "
                         "pauses it. Pitch doesn't apply to TikTok voices."))
        self.voice_pages.setCurrentIndex(list(PROVIDERS).index(self.tts_provider.currentData()))
        # Voice lists load when this page is first shown, not whenever Preferences opens.
        self.tabs.list.currentRowChanged.connect(
            lambda row: self._provider_changed() if TABS[row] == "Text to speech" else None)

    def _provider_changed(self) -> None:
        provider = self.tts_provider.currentData()
        self.voice_pages.setCurrentIndex(list(PROVIDERS).index(provider))
        if provider == "edge" and not self._edge_voices:
            self.controller.edge_voices(self._edge_loaded, lambda error: self.message.setText(str(error)))
        elif provider == "system" and self.controller.settings.tts_enabled:
            self._load_engines()

    def _edge_loaded(self, voices: list) -> None:
        self._edge_voices = voices
        wanted = self.edge_voice.currentData() or self.controller.settings.tts_edge_voice
        language = next((v.language for v in voices if v.id == wanted), "")
        self.edge_language.blockSignals(True)
        self.edge_language.clear()
        for name in sorted({v.language for v in voices}):
            self.edge_language.addItem(name, name)
        self.edge_language.setCurrentIndex(max(0, self.edge_language.findData(language)))
        self.edge_language.blockSignals(False)
        self._fill_edge_voices(wanted=wanted)

    def _fill_edge_voices(self, _index: int = 0, wanted: str = "") -> None:
        wanted = wanted or self.edge_voice.currentData() or ""
        language = self.edge_language.currentData()
        self.edge_voice.clear()
        for voice in self._edge_voices:
            if voice.language == language:
                self.edge_voice.addItem(voice.name, voice.id)
        self.edge_voice.setCurrentIndex(max(0, self.edge_voice.findData(wanted)))

    def _load_engines(self) -> None:
        """Ask the OS for engines and voices (only on request: it loads the speech plugin)."""
        if not self._engines_loaded:
            from ignite_live.speech.speaker import available_engines

            self._engines_loaded = True
            self.tts_engine.blockSignals(True)
            for name in available_engines():
                self.tts_engine.addItem(name, name)
            self.tts_engine.setCurrentIndex(max(0, self.tts_engine.findData(self.controller.settings.tts_engine)))
            self.tts_engine.blockSignals(False)
        self._load_voices()

    def _load_voices(self) -> None:
        wanted = self.tts_voice.currentData() or self._voice_wanted
        self.tts_voice.clear()
        self.tts_voice.addItem("Default voice", "")
        for name in self.controller.tts_voices(self.tts_engine.currentData() or ""):
            self.tts_voice.addItem(name, name)
        self.tts_voice.setCurrentIndex(max(0, self.tts_voice.findData(wanted)))

    def _tiktok_voice_id(self) -> str:
        """The chosen voice's ID, or an ID typed in by hand."""
        text = self.tiktok_voice.currentText().strip()
        index = self.tiktok_voice.findText(text)
        if index >= 0:
            return self.tiktok_voice.itemData(index)
        match = re.search(r"\(([\w-]+)\)$", text)
        return match.group(1) if match else (text or self.controller.settings.tts_tiktok_voice)

    def _ok(self) -> None:
        if self.apply():
            self.accept()

    # Discord Rich Presence (live/presence.py): modules, templates and presets; a preview with sample values.

    def _presence_section(self, form: QFormLayout, s: Settings) -> None:
        config = parse_presence(s.presence)
        form.addRow(QLabel("<b>Rich Presence</b>"))
        self.presence_on = QCheckBox("Show what I'm streaming on my Discord profile")
        self.presence_on.setChecked(config.enabled)
        form.addRow(self.presence_on)
        self.presence_app = QLineEdit(config.app_id)
        self.presence_app.setPlaceholderText("Empty: the one your Ignite server gives out (“Playing Ignite”)")
        advanced = QWidget()  # Few people need their own Discord application: it stays out of the way.
        QFormLayout(advanced).addRow("&Application ID:", self.presence_app)
        form.addRow(disclosure("Advanced", advanced))
        self.presence_preset = QComboBox()
        self.presence_preset.addItem("Custom", "")
        for key, (label, _details, _state) in PRESENCE_PRESETS.items():
            self.presence_preset.addItem(label, key)
        self.presence_preset.activated.connect(self._presence_preset_chosen)
        form.addRow("&Preset:", self.presence_preset)
        self.presence_details = QLineEdit(config.details)
        form.addRow("First line:", self.presence_details)
        self.presence_state = QLineEdit(config.state)
        form.addRow("Second line:", self.presence_state)
        modules = QGridLayout()  # Two columns: four checkboxes in a row are wider than the window.
        self.presence_modules: dict[str, QCheckBox] = {}
        for index, (key, (label, names)) in enumerate(PRESENCE_MODULES.items()):
            box = QCheckBox(label)
            box.setToolTip("Fills " + ", ".join(f"{{{name}}}" for name in names))
            box.setChecked(key in config.modules)
            modules.addWidget(box, index // 2, index % 2)
            self.presence_modules[key] = box
        modules.setColumnStretch(2, 1)
        form.addRow("Show:", modules)
        self.presence_time = QCheckBox("Time since the stream started")
        self.presence_time.setChecked(config.show_time)
        self.presence_platform = QCheckBox("The platform's icon (your app's art assets: tiktok, twitch, youtube)")
        self.presence_platform.setChecked(config.small_image == "platform")
        form.addRow("", self.presence_time)
        form.addRow("", self.presence_platform)
        self.presence_image = QLineEdit(config.large_image)
        self.presence_image.setPlaceholderText("ignite (the Ignite logo), none, or an https:// image link")
        form.addRow("Big &image:", self.presence_image)
        self.presence_image_text = QLineEdit(config.large_text)
        form.addRow("Image hover text:", self.presence_image_text)
        self.presence_buttons: list[tuple[QLineEdit, QLineEdit]] = []
        for number in range(2):
            label, url = config.buttons[number] if number < len(config.buttons) else ("", "")
            label_edit, url_edit = QLineEdit(label), QLineEdit(url)
            label_edit.setMaxLength(32)
            label_edit.setPlaceholderText("Watch on TikTok" if not number else "Label")
            url_edit.setPlaceholderText("https://www.tiktok.com/@{tiktok}/live" if not number else "https://…")
            row = QHBoxLayout()
            row.addWidget(label_edit, 1)
            row.addWidget(url_edit, 2)
            form.addRow(f"Button {number + 1}:", row)
            self.presence_buttons.append((label_edit, url_edit))
        self.presence_idle = QComboBox()
        self.presence_idle.addItem("Show nothing", "hide")
        self.presence_idle.addItem("Show these lines", "show")
        self.presence_idle.setCurrentIndex(max(0, self.presence_idle.findData(config.idle)))
        self.presence_idle_details = QLineEdit(config.idle_details)
        self.presence_idle_state = QLineEdit(config.idle_state)
        idle = QHBoxLayout()
        idle.addWidget(self.presence_idle)
        idle.addWidget(self.presence_idle_details, 2)
        idle.addWidget(self.presence_idle_state, 1)
        form.addRow("When not live:", idle)
        self.presence_preview = QLabel()
        self.presence_preview.setWordWrap(True)
        self.presence_preview.setTextFormat(Qt.TextFormat.PlainText)
        form.addRow("Preview:", self.presence_preview)
        self.presence_state_label = QLabel(self.controller.presence_status)
        self.presence_state_label.setWordWrap(True)
        form.addRow("Status:", self.presence_state_label)
        self.controller.presence_changed.connect(self.presence_state_label.setText)
        form.addRow(note("Placeholders: {platforms}, {platform}, {viewers}, {likes}, {followers}, {song}, {artist}, "
                         "{requester}, {game}, and your usernames {tiktok}, {twitch}, {youtube} (for button links). "
                         "Separate parts with “ · ”: a part whose information isn't there (or whose box above is "
                         "off) is left out. Your profile shows “Playing Ignite”; for another name, make an application at "
                         "discord.com/developers and enter its ID. Needs the Discord app on this PC, with Activity "
                         "Privacy → “Share your detected activities” on."))
        for widget in (self.presence_details, self.presence_state, self.presence_image, self.presence_image_text,
                       self.presence_idle_details, self.presence_idle_state, self.presence_app,
                       *(edit for pair in self.presence_buttons for edit in pair)):
            widget.textChanged.connect(self._presence_preview)
        for box in (self.presence_on, self.presence_time, self.presence_platform, *self.presence_modules.values()):
            box.toggled.connect(self._presence_preview)
        self.presence_idle.currentIndexChanged.connect(self._presence_preview)
        self._presence_preview()

    def _presence_preset_chosen(self) -> None:
        key = self.presence_preset.currentData()
        if key in PRESENCE_PRESETS:
            _label, details, state = PRESENCE_PRESETS[key]
            self.presence_details.setText(details)
            self.presence_state.setText(state)

    def _presence_form(self) -> dict:
        return {
            "enabled": self.presence_on.isChecked(), "app_id": self.presence_app.text().strip(),
            "details": self.presence_details.text(), "state": self.presence_state.text(),
            "modules": [key for key, box in self.presence_modules.items() if box.isChecked()],
            "show_time": self.presence_time.isChecked(),
            "large_image": self.presence_image.text().strip() or "ignite",
            "large_text": self.presence_image_text.text(),
            "small_image": "platform" if self.presence_platform.isChecked() else "none",
            "buttons": [[label.text(), url.text().strip()] for label, url in self.presence_buttons
                        if label.text().strip() and url.text().strip()],
            "idle": self.presence_idle.currentData(), "idle_details": self.presence_idle_details.text(),
            "idle_state": self.presence_idle_state.text(),
        }

    def _presence_preview(self) -> None:
        raw = self._presence_form()
        config = parse_presence({**raw, "enabled": True})  # Preview even while off.
        preset = next((key for key, (_l, d, st) in PRESENCE_PRESETS.items()
                       if (d, st) == (raw["details"], raw["state"])), "")
        self.presence_preset.setCurrentIndex(max(0, self.presence_preset.findData(preset)))
        activity = build_presence(config, PRESENCE_SAMPLE)
        if activity is None:
            self.presence_preview.setText("(nothing: you're not live)")
            return
        lines = [activity.get("details", ""), activity.get("state", "")]
        if "timestamps" in activity:
            lines.append("1:23:45 elapsed")
        lines += [f"[{button['label']}]" for button in activity.get("buttons", [])]
        bad = [label.text() for label, url in self.presence_buttons
               if label.text().strip() and url.text().strip() and not url.text().strip().startswith("https://")]
        if bad:
            lines.append("(Button links must start with https://)")
        if raw["app_id"] and not parse_presence(raw).app_id:
            lines.append("(The application ID is a long number, from the Developer Portal)")
        self.presence_preview.setText("\n".join(line for line in lines if line))

    def _minecraft_url(self) -> str:
        """The typed address, cleaned; the saved one while it's mistyped (apply() says so before saving)."""
        try:
            return check_servertap_url(self.minecraft_url.text())
        except ServerTapError:
            return self.controller.settings.minecraft_url

    def _test_minecraft(self) -> None:
        try:
            url = check_servertap_url(self.minecraft_url.text())
        except ServerTapError as error:
            self.minecraft_state.setText(str(error))
            return
        self.minecraft_state.setText("Checking…")
        self.controller.test_minecraft(lambda text, _ok: self.minecraft_state.setText(text), url,
                                       self.minecraft_key.text().strip())

    def _test_discord(self) -> None:
        url = self.discord_url.text().strip() or keychain.load(keychain.DISCORD_WEBHOOK) or ""
        if not valid_webhook_url(url):
            self.message.setText("Paste a Discord webhook URL first.")
            return
        self.controller.test_discord(url)

    def _settings_from_form(self) -> Settings:
        banned, reasons = [], {}
        for line in self.banned.toPlainText().splitlines():
            link, _, reason = line.partition(" #")
            video_id = video_id_from(link)
            if video_id and video_id not in banned:
                banned.append(video_id)
                if reason.strip():
                    reasons[video_id] = reason.strip()[:120]
        prefix = self.command_prefix.text().strip() or "!"
        names = {kind: field.text().strip() for kind, field in self.command_names.items() if field.text().strip()}
        return replace(
            self.controller.settings,
            tiktok_username=self.tiktok.text().strip().lstrip("@"),
            twitch_channel=self.twitch.text().strip(),
            twitch_stats=self.twitch_stats.isChecked(),
            minecraft_url=self._minecraft_url(),
            presence=clean_presence(self._presence_form()),
            tiktok_chat_replies=self.tiktok_replies.isChecked(),
            youtube_channel=self.youtube.text().strip(),
            obs_url=self.obs_url.text().strip() or "ws://127.0.0.1:4455",
            obs_record_while_live=self.obs_record.isChecked(),
            obs_replay_while_live=self.obs_replay.isChecked(),
            obs_live_scene=self.obs_live_scene.currentText().strip(),
            obs_chat_clip=self.obs_chat_clip.isChecked(),
            obs_chat_scene=self.obs_chat_scene.isChecked(),
            obs_screenshot_dir=self.obs_screenshots.text().strip(),
            remote_control=self.remote_control.isChecked(),
            tiktok_via_server=self.tiktok_via_server.isChecked(),
            overlay_port=self.port.value(),
            request_role=self.request_role.currentData(),
            skip_role=self.skip_role.currentData(),
            user_cooldown_s=self.cooldown.value(),
            max_per_user=self.per_user.value(),
            max_queue=self.max_queue.value(),
            max_minutes=self.max_minutes.value(),
            banned_video_ids=banned,
            banned_reasons=reasons,
            refuse_unknown_length=self.refuse_unknown.isChecked(),
            refuse_explicit=self.refuse_explicit.isChecked(),
            auto_ban=self.auto_ban.isChecked(),
            blocked_words=_lines(self.blocked_words),
            blocked_users=_lines(self.blocked_users),
            mod_hide_links=self.mod_hide_links.isChecked(),
            mod_chat_mode=self.mod_chat_mode.currentData(),
            **{f"mod_filter_{key}": check.isChecked() for key, check in self.mod_places.items()},
            **{f"panic_{key}": check.isChecked() for key, check in self.panic_checks.items()},
            panic_scene=self.panic_scene.currentText().strip(),
            panic_mute_inputs=[self.panic_inputs.item(i).text() for i in range(self.panic_inputs.count())
                               if self.panic_inputs.item(i).checkState() == Qt.CheckState.Checked],
            panic_chat_command=self.panic_chat.isChecked(),
            command_prefix=prefix,
            command_names=names,
            discord_enabled=self.discord_enabled.isChecked(),
            discord_comments=self.discord_comments.isChecked(),
            discord_gifts=self.discord_gifts.isChecked(),
            discord_follows=self.discord_follows.isChecked(),
            tts_enabled=self.tts_enabled.isChecked(),
            tts_platforms=[key for key, check in self.tts_platforms.items() if check.isChecked()],
            tts_comments=self.tts_comments.isChecked(),
            tts_gifts=self.tts_gifts.isChecked(),
            tts_follows=self.tts_follows.isChecked(),
            tts_role=self.tts_role.currentData(),
            tts_command=self.tts_command.text().strip().lstrip(prefix).lower(),
            tts_skip_commands=self.tts_skip_commands.isChecked(),
            tts_skip_links=self.tts_skip_links.isChecked(),
            tts_max_chars=self.tts_max_chars.value(),
            tts_user_cooldown_s=self.tts_cooldown.value(),
            tts_max_queue=self.tts_max_queue.value(),
            tts_provider=self.tts_provider.currentData(),
            tts_edge_voice=self.edge_voice.currentData() or self.controller.settings.tts_edge_voice,
            tts_tiktok_voice=self._tiktok_voice_id(),
            tts_engine=self.tts_engine.currentData() or "",
            tts_voice=self.tts_voice.currentData() or "",
            tts_rate=self.tts_rate.value(),
            tts_pitch=self.tts_pitch.value(),
            tts_volume=self.tts_volume.value(),
            texts=dict(self.messages.texts),
            ui_theme=self.ui_theme.currentData(),
            close_to_tray=self.close_to_tray.isChecked(),
            auto_update=self.auto_update.isChecked(),
            experimental_relaunch=self.experimental_relaunch.isChecked(),
            overlay_theme=self.overlay_theme.currentData(),
            overlay_accent=self.overlay_accent.value,
            overlay_accent_2=self.overlay_accent_2.value,
            overlay_font=self.overlay_font.currentText().strip(),
            overlay_css=self.overlay_css.toPlainText(),
        )

    def apply(self) -> bool:
        """Save everything; False (and a message) if something is invalid."""
        discord_url = self.discord_url.text().strip()
        if discord_url and not valid_webhook_url(discord_url):
            self.tabs.setCurrentIndex(TABS.index("Discord"))
            self.message.setText("That Discord webhook URL doesn't look right. Nothing was saved.")
            return False
        try:
            check_servertap_url(self.minecraft_url.text())
        except ServerTapError as error:
            self.tabs.setCurrentIndex(TABS.index("Minecraft"))
            self.message.setText(f"{error} Nothing was saved.")
            return False
        session = self.tiktok_session.text().strip()
        if session and not valid_session(session):
            self.tabs.setCurrentIndex(TABS.index("Text to speech"))
            self.message.setText("That TikTok session ID doesn't look right: copy just the value of the “sessionid” "
                                 "cookie. Nothing was saved.")
            return False
        new = self._settings_from_form()
        clash = self._command_clash(new)
        if clash:
            self.tabs.setCurrentIndex(TABS.index("Commands"))
            self.message.setText(f"“{clash}” is used for two commands. Nothing was saved.")
            return False
        secrets = {
            keychain.OBS_PASSWORD: self.obs_password,
            keychain.YOUTUBE_API_KEY: self.youtube_key,
            keychain.SIGN_API_KEY: self.sign_key,
            keychain.DISCORD_WEBHOOK: self.discord_url,
            keychain.TIKTOK_SESSION: self.tiktok_session,
            keychain.MINECRAFT_KEY: self.minecraft_key,
        }
        obs_password_entered = bool(self.obs_password.text().strip())
        for name, widget in secrets.items():
            if widget.text().strip():
                if not keychain.save(name, widget.text().strip()):
                    self.message.setText("No system keychain was found, so secrets were not saved.")
                    return False
                widget.clear()
                widget.setPlaceholderText("Saved; type to replace")
        self.banned.setPlainText(banned_text(new))

        old = self.controller.settings
        url_changed = new.obs_url != old.obs_url
        self.controller.save_settings(new)
        if obs_password_entered and not url_changed:  # A URL change already reconnects.
            self.controller.reconnect_obs()
        self.controller.check_backend()
        if discord_url:  # A new webhook in the keychain: settings alone wouldn't restart the bridge.
            self.controller.restart_discord()
        if session:  # A new TikTok session ID in the keychain.
            self.controller.apply_tts()
        self.message.setText("Saved.")
        return True

    # Signing in to the server (controller.sign_in_to_server).

    def _show_tiktok_account(self, status: dict) -> None:
        if status.get("connected"):
            name = status.get("username")
            self.tiktok_account.setText(f"Signed in as @{name}" if name else "Signed in")
        elif status.get("error"):
            self.tiktok_account.setText(f"Couldn't check: {status['error']}")
        elif status.get("server") is False:
            self.tiktok_account.setText("Sign in to your Ignite server first (Server tab)")
        elif status.get("available"):
            self.tiktok_account.setText("Not signed in")
        else:
            self.tiktok_account.setText("Your Ignite server doesn't offer TikTok sign-in")

    def _sign_in(self) -> None:
        self.controller.sign_in_to_server()

    def _show_sign_in(self, state: dict | None) -> None:
        waiting = bool(state and state.get("waiting"))
        if waiting:
            self.sign_in_state.setText(f"In your browser, approve the code <b>{html.escape(state['code'])}</b>. Waiting…")
        elif state:
            who = state.get("username")
            self.sign_in_state.setText(f"Signed in as <b>{html.escape(who)}</b>" if who else "Connected")
        else:
            self.sign_in_state.setText("Not signed in")
        self._remote_control_locked(bool(state and state.get("username")) and not waiting)
        self.sign_in_button.setVisible(not waiting and not state)
        self.sign_in_cancel.setVisible(waiting)
        self.sign_out_button.setVisible(bool(state) and not waiting)

    def _remote_control_locked(self, signed_in: bool) -> None:
        """Signed in to an account, remote control is on and stays on until you sign out."""
        if not hasattr(self, "_remote_note"):
            return
        if signed_in:
            self.remote_control.setChecked(True)
        self.remote_control.setEnabled(not signed_in)
        self.remote_control.setToolTip("Remote control is on for your Ignite account. Sign out to change it."
                                       if signed_in else "")

    @staticmethod
    def _command_clash(s: Settings) -> str:
        seen: dict[str, CommandKind] = {}
        for kind, names in command_aliases(s).items():
            for name in names:
                if seen.setdefault(name, kind) is not kind:
                    return f"{s.command_prefix}{name}"
        return ""
