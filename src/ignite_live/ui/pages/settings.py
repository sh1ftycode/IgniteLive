"""Settings: every preference, grouped (General, Appearance, Streaming, Integrations, Notifications, Privacy,
Advanced, About), with one Save bar at the bottom.

The forms are Preferences' own (ui/preferences.py), shown in the window rather than a dialog, so the two never
drift apart. Unsaved edits survive moving to another screen and back; when settings change elsewhere while this
screen is hidden, it reloads them the next time it's opened.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ignite_live import __version__
from ignite_live.controller import Controller
from ignite_live.core import settings as settings_store
from ignite_live.ui.kit import button, label, restyle_buttons
from ignite_live.ui.preferences import PreferencesDialog
from ignite_live.ui.theme import theme_changed


class EmbeddedPreferences(PreferencesDialog):
    """Preferences as part of the window: Esc and Enter mustn't close it."""

    def reject(self) -> None:  # noqa: D102 - Qt override
        pass

    def accept(self) -> None:  # noqa: D102 - Qt override
        pass


class SettingsPage(QWidget):
    def __init__(self, controller: Controller, hooks: dict[str, Callable[[], None]]) -> None:
        super().__init__()
        self.controller = controller
        self.hooks = hooks
        self.setObjectName("page")
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(0)
        self.prefs: EmbeddedPreferences | None = None
        self._stale = False
        self._wanted = ""

        bar = QWidget()
        bar.setObjectName("saveBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(24, 10, 24, 10)
        row.setSpacing(8)
        self.message = label("", "muted", wrap=True)
        row.addWidget(self.message, 1)
        self.discard = button("Discard Changes", "x", tip="Reload the saved settings into the form")
        self.discard.clicked.connect(self.reload)
        self.save = button("Save Changes", "check", kind="primary", tip="Save every section (Ctrl+S)")
        self.save.setShortcut("Ctrl+S")
        self.save.clicked.connect(self.apply)
        row.addWidget(self.discard)
        row.addWidget(self.save)
        self.bar = bar
        self._build()
        self.layout_.addWidget(bar)
        controller.settings_changed.connect(self._settings_changed)
        theme_changed().connect(lambda _p: restyle_buttons(self.discard, self.save))

    def _build(self, section: str = "") -> None:
        if self.prefs is not None:
            self.layout_.removeWidget(self.prefs)
            self.prefs.hide()
            self.prefs.deleteLater()
        prefs = EmbeddedPreferences(self.controller, self, section or "OBS")
        prefs.setWindowFlags(Qt.WindowType.Widget)
        prefs.button_box.hide()
        prefs.message.hide()  # The Save bar shows it instead.
        prefs.layout().setContentsMargins(0, 0, 0, 0)
        prefs.tabs.addTab(self._about(), "About")
        self.prefs = prefs
        self.layout_.insertWidget(0, prefs, 1)
        if section:
            prefs.tabs.show_section(section)
        self._stale = False

    def _about(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(label(f"Ignite {__version__}", "sectionTitle"))
        layout.addWidget(label("Stream keys, OBS control, TikTok, Twitch and YouTube chat, song requests, alerts, "
                               "overlays and text to speech in one app.", "muted", wrap=True))
        files = QLabel(f"Settings: {self.controller.settings_path.parent}<br>"
                       f"Log: {settings_store.log_dir(self.controller.settings_path) / 'ignite.log'}")
        files.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        files.setProperty("role", "muted")
        layout.addWidget(files)
        row = QHBoxLayout()
        row.setSpacing(8)
        for text, icon, hook in (("Check for Updates", "refresh-cw", "check_updates"),
                                 ("What's New", "sparkles", "whats_new"),
                                 ("Setup Guide", "circle-help", "setup_guide"),
                                 ("Feedback & Requests", "message-circle", "feedback"),
                                 ("Open Log Folder", "folder-open", "open_logs")):
            if hook in self.hooks:
                widget = button(text, icon)
                widget.clicked.connect(self.hooks[hook])
                row.addWidget(widget)
        row.addStretch(1)
        layout.addLayout(row)
        return page

    def show_section(self, section: str) -> None:
        """Open on a section (e.g. "Text to speech"), as "prefs-…" links ask."""
        if self.prefs is None or self._stale:
            self._build(section)
            return
        self.prefs.tabs.show_section(section)
        if section == "Text to speech":
            self.prefs._provider_changed()

    def apply(self) -> bool:
        saved = self.prefs.apply()
        self.message.setText(self.prefs.message.text())
        self.message.setProperty("tone", "" if saved else "error")
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        return saved

    def reload(self) -> None:
        self._build(self.prefs.tabs.titles[self.prefs.tabs.currentIndex()] if self.prefs else "")
        self.message.setText("Showing your saved settings.")

    def _settings_changed(self, _settings) -> None:
        if not self.isVisible():
            self._stale = True

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._stale:
            self._build()
        super().showEvent(event)
