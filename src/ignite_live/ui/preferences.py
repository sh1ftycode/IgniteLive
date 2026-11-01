"""File → Preferences: every setting in one standard dialog (OK / Cancel / Apply).

Sections are listed on the left. Secrets go to the system keychain; a blank
secret field keeps the saved value.
"""

from __future__ import annotations

import re
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontDatabase
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

from ignite_live.controller import Controller, command_aliases
from ignite_live.core import keychain
from ignite_live.core.events import PLATFORMS
from ignite_live.core.settings import COMMAND_DEFAULTS, ROLES, Settings
from ignite_live.core.templates import BY_KEY, GROUPS, PLACEHOLDERS, SAMPLE, TEMPLATES, render, unknown_placeholders
from ignite_live.integrations.discord_bridge import valid_webhook_url
from ignite_live.music.commands import CommandKind
from ignite_live.music.youtube import video_id_from
from ignite_live.overlay.server import THEMES as OVERLAY_THEMES
from ignite_live.ui.pages.stream import note
from ignite_live.speech.voices import PROVIDERS, tiktok_voices, valid_session
from ignite_live.ui.theme import THEMES as APP_THEMES
from ignite_live.ui.theme import resolve_theme

ROLE_NAMES = {"everyone": "Everyone", "followers": "Followers (Twitch subs, YouTube members)", "mods": "Moderators"}
TABS = ("OBS", "Chat platforms", "Server", "Song requests", "Commands", "Text to speech", "Moderation", "Messages",
        "Discord", "Appearance", "Advanced")
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


class Sections(QWidget):
    """A section list on the left and the selected page on the right (tab-like API)."""

    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setMaximumWidth(170)
        self.stack = QStackedWidget()
        layout.addWidget(self.list)
        layout.addWidget(self.stack, 1)
        self.list.currentRowChanged.connect(self.stack.setCurrentIndex)

    def addTab(self, page: QWidget, title: str) -> None:  # noqa: N802 - mirrors QTabWidget
        scroll = QScrollArea()
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.stack.addWidget(scroll)
        self.list.addItem(title)
        if self.list.currentRow() < 0:
            self.list.setCurrentRow(0)

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        self.list.setCurrentRow(index)

    def currentIndex(self) -> int:  # noqa: N802
        return self.list.currentRow()

    def setTabVisible(self, index: int, visible: bool) -> None:  # noqa: N802
        self.list.item(index).setHidden(not visible)


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
        self.obs_warn = _spin(s.obs_warn_dropped_pct, 0, 50, " %")
        self.obs_warn.setSpecialValueText("Never")
        form.addRow("&Warn when dropping:", self.obs_warn)
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

        form = _tab(self.tabs, "Server")
        self.backend_url = QLineEdit(s.backend_url)
        self.backend_url.setPlaceholderText("https://ignite.example.com")
        form.addRow("Server &URL:", self.backend_url)
        self.backend_token = _secret(keychain.BACKEND_TOKEN)
        form.addRow("Device &token:", self.backend_token)
        form.addRow(note("Optional. Keeps your stream key for 48 hours when the app is closed, syncs settings "
                         "between computers, and runs song search. Get a token with: ignite-server add-user NAME"))

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

        form = _tab(self.tabs, "Appearance")
        self.ui_theme = _choice(APP_THEMES, resolve_theme(s.ui_theme))
        form.addRow("&Mode:", self.ui_theme)
        self.close_to_tray = QCheckBox("Keep running in the system tray when the window is closed")
        self.close_to_tray.setChecked(s.close_to_tray)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.close_to_tray.setEnabled(False)
            self.close_to_tray.setToolTip("Your desktop has no system tray")
        form.addRow("Window:", self.close_to_tray)
        self.overlay_theme = _choice(OVERLAY_THEMES, s.overlay_theme)
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

        form = _tab(self.tabs, "Advanced")
        self.port = _spin(s.overlay_port, 1024, 65535)
        form.addRow("Overlay &port:", self.port)
        form.addRow(note("Changing the port changes every overlay URL. Takes effect after a restart."))
        self.sign_key = _secret(keychain.SIGN_API_KEY)
        form.addRow("&Euler Stream API key:", self.sign_key)
        form.addRow(note("Optional. Raises TikTok chat rate limits."))

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
            youtube_channel=self.youtube.text().strip(),
            obs_url=self.obs_url.text().strip() or "ws://127.0.0.1:4455",
            obs_record_while_live=self.obs_record.isChecked(),
            obs_replay_while_live=self.obs_replay.isChecked(),
            obs_live_scene=self.obs_live_scene.currentText().strip(),
            obs_warn_dropped_pct=self.obs_warn.value(),
            obs_chat_clip=self.obs_chat_clip.isChecked(),
            obs_chat_scene=self.obs_chat_scene.isChecked(),
            obs_screenshot_dir=self.obs_screenshots.text().strip(),
            backend_url=self.backend_url.text().strip().rstrip("/"),
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
            keychain.BACKEND_TOKEN: self.backend_token,
            keychain.YOUTUBE_API_KEY: self.youtube_key,
            keychain.SIGN_API_KEY: self.sign_key,
            keychain.DISCORD_WEBHOOK: self.discord_url,
            keychain.TIKTOK_SESSION: self.tiktok_session,
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

    @staticmethod
    def _command_clash(s: Settings) -> str:
        seen: dict[str, CommandKind] = {}
        for kind, names in command_aliases(s).items():
            for name in names:
                if seen.setdefault(name, kind) is not kind:
                    return f"{s.command_prefix}{name}"
        return ""
