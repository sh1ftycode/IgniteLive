"""The main window: the sidebar, the header, and one screen at a time.

Overview, Stream Control, Live Chat, Alerts, Analytics, Overlays, Integrations and Settings. Messages appear as
toasts and stay in the notification centre (the header's bell); connection and streaming status live in the
header; Panic is always one click away there. Every command keeps its keyboard shortcut (they're on the
window, not a menu bar); the profile menu has the app-wide ones. Closing hides to the system tray.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ignite_live import __version__
from ignite_live.controller import Controller
from ignite_live.core import settings as settings_store
from ignite_live.core.bundled import main_server
from ignite_live.core.seasons import SEASONAL
from ignite_live.ui.actions import AppActions
from ignite_live.ui.checklist import ChecklistDialog, go_live
from ignite_live.ui.docks import ChatPopout
from ignite_live.ui.kit import ToastHost
from ignite_live.ui.pages.actions import ActionsPage
from ignite_live.ui.pages.api import ApiPage
from ignite_live.ui.pages.live_chat import LiveChatPage
from ignite_live.ui.pages.overlays import OverlaysPage
from ignite_live.ui.pages.overview import OverviewPage
from ignite_live.ui.pages.settings import SettingsPage
from ignite_live.ui.pages.stream import StreamPage
from ignite_live.ui.pages.streams import StreamsPage
from ignite_live.ui.profiles import ProfilesMenu
from ignite_live.ui.shell import AppHeader, NotificationCenter
from ignite_live.ui.sidebar import EXPANDED_WIDTH as SIDEBAR_WIDTH
from ignite_live.ui.sidebar import NavItem, Sidebar
from ignite_live.ui.simulate import SimulateDialog
from ignite_live.ui.theme import apply_theme, colors, resolve_theme, theme_changed
from ignite_live.ui.updates import UpdateBanner, UpdateButton, UpdateManager, show_notes

# The screens, in sidebar order (Ctrl+1… follows it): key, title, Lucide icon, the header's description.
SCREENS = (
    ("overview", "Overview", "house", "Your stream at a glance"),
    ("stream", "Stream Control", "radio", "Go live, choose your output and keep an eye on OBS"),
    ("chat", "Live Chat", "message-square-more", "Chat from TikTok, Twitch and YouTube, your viewers and song requests"),
    ("actions", "Alerts", "bell", "What happens on stream when viewers follow, gift, share or chat"),
    ("streams", "Analytics", "chart-column", "How your streams went, from the reports Ignite saves"),
    ("overlays", "Overlays", "layers", "Browser Sources for OBS, goal bars and chat games"),
    ("api", "Integrations", "puzzle", "Platforms and services Ignite works with, and its links and API"),
    ("settings", "Settings", "settings", "Preferences for Ignite on this PC"),
)
PAGE_TITLES = {key: title for key, title, _icon, _text in SCREENS}
SUBTITLES = {key: text for key, _title, _icon, text in SCREENS}
NAV_ITEMS = [NavItem(key, title, icon) for key, title, icon, _text in SCREENS]
# Older links (the setup guide, the checklist, other callers): where those screens went.
MOVED = {"music": ("chat", "music"), "viewers": ("chat", "viewers"), "games": ("overlays", "games"),
         "goals": ("overlays", "goals"), "obs": ("stream", "")}
MIN_WIDTH = 760 + SIDEBAR_WIDTH  # The screens' own minimum, plus the sidebar.
MIN_HEIGHT = 560


class MainWindow(QMainWindow):
    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        self.setWindowTitle("Ignite")
        screen = QApplication.primaryScreen()
        room = screen.availableGeometry() if screen is not None else None
        self.resize(min(1600, room.width()) if room else 1600, min(960, room.height()) if room else 960)
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        self.chat_popout: ChatPopout | None = None
        self.simulator: SimulateDialog | None = None
        self.tray: QSystemTrayIcon | None = None
        self.quitting = False  # Closing the window only hides it to the tray, unless this is set.
        self.shut_down = False
        self.actions = AppActions(controller, self)
        self.updates = UpdateManager(controller, self)

        # Screens.
        self.guide = None  # The in-window setup guide while it is open.
        self._guide_return = "overview"
        self.pages: dict[str, QWidget] = {}
        self.pages["chat"] = LiveChatPage(controller, self.actions)
        self.chat_panel = self.pages["chat"]  # Its history seeds a newly opened pop-out.
        self.pages["overview"] = OverviewPage(controller, self.actions, self.chat_panel.history)
        self.pages["stream"] = StreamPage(controller, self.actions)
        self.pages["actions"] = ActionsPage(controller)
        self.pages["streams"] = StreamsPage(controller)
        self.pages["overlays"] = OverlaysPage(controller)
        self.pages["api"] = ApiPage(controller)
        self.pages["settings"] = SettingsPage(controller, {
            "check_updates": lambda: self.updates.check(manual=True), "whats_new": lambda: show_notes(self.updates, self),
            "setup_guide": self.open_setup_guide, "feedback": self.open_feedback, "open_logs": self.open_logs})
        for key in ("overview", "actions", "api"):
            self.pages[key].open_page_requested.connect(self.open_page)
        self._current_page_key = "overview"
        self.empty_page = QWidget()  # Shown when every sidebar section is hidden.
        empty = QVBoxLayout(self.empty_page)
        empty.addStretch(1)
        hint = QLabel("Every section is hidden. Show one from the sidebar, or:")
        hint.setProperty("role", "muted")
        empty.addWidget(hint, 0, Qt.AlignmentFlag.AlignHCenter)
        reopen = QPushButton("Show All Sections")
        reopen.clicked.connect(self.show_all_sections)
        empty.addWidget(reopen, 0, Qt.AlignmentFlag.AlignHCenter)
        empty.addStretch(1)
        self.content_stack = QStackedWidget()
        for key in PAGE_TITLES:
            self.content_stack.addWidget(self.pages[key])
        self.content_stack.addWidget(self.empty_page)

        # Shell.
        self.sidebar = Sidebar(NAV_ITEMS)
        self.sidebar.page_requested.connect(self.open_page)
        self.sidebar.hide_requested.connect(self.hide_page)
        self.sidebar.show_all_requested.connect(self.show_all_sections)
        self.sidebar.collapsed_changed.connect(self._sidebar_collapsed_changed)
        self.update_button = UpdateButton(self.updates, self)
        self.sidebar.add_footer_widget(self.update_button)
        self.sidebar.account_clicked.connect(self._show_profile_menu)
        self.sidebar.set_collapsed(controller.settings.sidebar_collapsed)
        self.header = AppHeader(self.actions.panic)
        self.header.status_clicked.connect(lambda: self.open_page("stream"))
        self.header.chat_clicked.connect(lambda: self.open_page("api"))
        self.header.account_clicked.connect(lambda: self.open_page("prefs-Server"))
        self.notifications = NotificationCenter(self)
        self.notifications.unread_changed.connect(self.header.bell.set_unread)
        self.header.bell.clicked.connect(lambda: self.notifications.open_below(self.header.bell))
        self.profile_menu = self._build_profile_menu()
        self.header.profile.setMenu(self.profile_menu)

        # Panic mode banner, under the header.
        self.panic_banner = QWidget()
        self.panic_banner.setObjectName("panicBanner")
        banner = QHBoxLayout(self.panic_banner)
        banner.setContentsMargins(24, 8, 24, 8)
        self.panic_label = QLabel()
        resume = QPushButton("Resume")
        resume.clicked.connect(controller.resume)
        banner.addWidget(self.panic_label, 1)
        banner.addWidget(resume)
        self.panic_banner.hide()
        controller.panic_changed.connect(self._panic_changed)
        self.update_banner = UpdateBanner(self.updates, self)

        central = QWidget()
        central.setObjectName("appContent")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.sidebar)
        column_widget = QWidget()
        column = QVBoxLayout(column_widget)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.header)
        column.addWidget(self.panic_banner)
        column.addWidget(self.update_banner)
        column.addWidget(self.content_stack, 1)
        root.addWidget(column_widget, 1)
        self.setCentralWidget(central)
        self.toasts = ToastHost(central)
        self._restore_sidebar()

        self._shortcuts()
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray()

        controller.status_changed.connect(lambda _change: self.header.set_connections(controller.statuses))
        controller.server_changed.connect(lambda _up: self._show_server())
        controller.settings_changed.connect(lambda _s: self._show_server())
        controller.studio_changed.connect(self._studio)
        controller.notice.connect(self._notice)
        controller.browser_link.connect(self._browser_link)
        controller.settings_changed.connect(self._settings_changed)
        controller.account_changed.connect(lambda _info: self._account())
        controller.tiktok_account_changed.connect(lambda _status: self._account())
        self._theme = controller.settings.ui_theme  # Applied by app.py at startup.
        # "Seasonal": move on to the next season or holiday when the date gets there (checked twice an hour).
        self._season_timer = QTimer(self, interval=30 * 60 * 1000)
        self._season_timer.timeout.connect(self._season_tick)
        self._season_timer.start()
        theme_changed().connect(self._restyle)
        self.header.set_connections(controller.statuses)
        self._show_server()
        self._studio(controller.studio)
        self._account()
        self._restyle()
        self._restore_layout()

    # Construction.

    def _shortcuts(self) -> None:
        """Every command stays on its keyboard shortcut, with no menu bar to carry it."""
        a = self.actions
        a.preferences.triggered.connect(lambda: self.open_preferences())
        a.open_logs.triggered.connect(self.open_logs)
        a.quit.triggered.connect(self.quit)
        a.about.triggered.connect(self._about)
        a.popout_chat.triggered.connect(self.open_chat_popout)
        a.lock_popout.toggled.connect(self.lock_chat_popout)
        a.add_all_overlays.triggered.connect(lambda: self.open_add_overlays())
        a.go_live = lambda: go_live(self, self.controller)
        a.check_stream.triggered.connect(lambda: ChecklistDialog(self.controller, self).exec())
        a.simulate.triggered.connect(self.open_simulator)
        for value in vars(a).values():
            if isinstance(value, QAction):
                self.addAction(value)
        for number, key in enumerate(PAGE_TITLES, 1):
            action = QAction(PAGE_TITLES[key], self)
            action.setShortcut(QKeySequence(f"Ctrl+{number}"))
            action.triggered.connect(lambda _=False, k=key: self.open_page(k))
            self.addAction(action)
        guide = QAction("Setup Guide", self)
        guide.setShortcut(QKeySequence("F1"))
        guide.triggered.connect(self.open_setup_guide)
        self.addAction(guide)

    def _build_profile_menu(self) -> QMenu:
        a = self.actions
        menu = QMenu(self)
        self.account_section = menu.addSection("Ignite")
        menu.addAction("Settings", lambda: self.open_page("settings"), "Ctrl+,")
        self.profiles_menu = ProfilesMenu(self.controller, self)
        menu.addMenu(self.profiles_menu)
        menu.addAction(a.simulate)
        menu.addAction("Setup Guide", self.open_setup_guide, "F1")
        menu.addSeparator()
        menu.addAction("Check for Updates", lambda: self.updates.check(manual=True))
        whats_new = menu.addAction("What's New", lambda: show_notes(self.updates, self))
        whats_new.setEnabled(False)
        self.updates.changed.connect(lambda: whats_new.setEnabled(self.updates.release is not None))
        if (self.controller.settings.backend_url or main_server()).strip():
            menu.addAction("Feedback & Requests", self.open_feedback)
        menu.addAction(a.open_logs)
        menu.addAction(a.about)
        menu.addSeparator()
        menu.addAction(a.quit)
        return menu

    def _show_profile_menu(self) -> None:
        self.profile_menu.exec(self.sidebar.account.mapToGlobal(self.sidebar.account.rect().topRight()))

    def _tray(self) -> None:
        """Tray icon: click to show or hide the window; the menu has the go-live essentials."""
        a = self.actions
        self.tray = QSystemTrayIcon(QApplication.windowIcon(), self)
        self.tray.setToolTip("Ignite")
        menu = QMenu(self)
        self.tray_toggle = menu.addAction("Hide Ignite", self.toggle_window)
        menu.addSeparator()
        for item in (a.toggle_stream, a.toggle_record, a.save_replay, None, a.popout_chat, a.lock_popout,
                     a.mute_tts, None, a.panic, None, a.quit):
            menu.addSeparator() if item is None else menu.addAction(item)
        menu.aboutToShow.connect(lambda: self.tray_toggle.setText("Hide Ignite" if self.isVisible() else "Show Ignite"))
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_clicked)
        self.tray.show()
        # Hidden in the tray, closing the pop-out chat (then the last window) mustn't quit the app.
        QApplication.instance().setQuitOnLastWindowClosed(False)
        QApplication.instance().aboutToQuit.connect(self._shut_down)  # E.g. logging out while in the tray.

    def _tray_clicked(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.toggle_window()

    def toggle_window(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.bring_forward()

    def bring_forward(self) -> None:
        """Show the window (from the tray or minimised) on top. Also when Ignite is launched again."""
        if self.isMinimized() or not self.isVisible():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit(self) -> None:
        """The profile menu's Quit and the tray's: really close, rather than hiding to the tray."""
        self.quitting = True
        self.close()

    # Navigation.

    def open_page(self, key: str) -> None:
        """Screens and the places buttons and links refer to: "stream", "prefs-Text to speech" (a Settings
        section), "actions:activity" (a screen's tab), "checklist", "simulate", "guide"."""
        key, _, tab = key.partition(":")
        if key.startswith("prefs-"):
            self.open_preferences(key.removeprefix("prefs-"))
            return
        if key == "simulate":
            self.open_simulator()
            return
        if key == "guide":
            self.open_setup_guide()
            return
        if key == "checklist":
            ChecklistDialog(self.controller, self).exec()
            return
        if key in MOVED:
            key, tab = MOVED[key][0], tab or MOVED[key][1]
        key = key if key in self.pages else "overview"
        s = self.controller.settings
        if key in s.closed_tabs:  # Shown again from a Ctrl+N shortcut or a link.
            s.closed_tabs = [k for k in s.closed_tabs if k != key]
            self.sidebar.set_visible_items([*self.sidebar.visible_keys(), key])
        self._close_guide(None)  # Going somewhere else leaves the guide.
        page = self.pages[key]
        if tab and hasattr(page, "show_tab"):
            page.show_tab(tab)
        self.content_stack.setCurrentWidget(page)
        self.sidebar.set_current(key)
        self._current_page_key = key
        self.header.set_page(PAGE_TITLES[key], SUBTITLES[key])
        self.setWindowTitle(f"Ignite — {PAGE_TITLES[key]}")
        if key == "chat" and not tab:
            self.chat_panel.username.setFocus()

    # Sidebar sections: each can be hidden; both that and their order are remembered.

    def _restore_sidebar(self) -> None:
        s = self.controller.settings
        order = [key for key in s.tab_order if key in PAGE_TITLES]
        order += [key for key in PAGE_TITLES if key not in order]
        visible = [key for key in order if key not in s.closed_tabs]
        self.sidebar.set_visible_items(visible)
        self.open_page(visible[0]) if visible else self._show_empty()

    def hide_page(self, key: str) -> None:
        s = self.controller.settings
        if key not in s.closed_tabs:
            s.closed_tabs = [*s.closed_tabs, key]
        remaining = [k for k in self.sidebar.visible_keys() if k != key]
        self.sidebar.set_visible_items(remaining)
        if self.content_stack.currentWidget() is self.pages[key]:
            self.open_page(remaining[0]) if remaining else self._show_empty()

    def show_all_sections(self) -> None:
        s = self.controller.settings
        s.closed_tabs = []
        order = [key for key in s.tab_order if key in PAGE_TITLES] or list(PAGE_TITLES)
        order += [key for key in PAGE_TITLES if key not in order]
        self.sidebar.set_visible_items(order)
        if self.content_stack.currentWidget() is self.empty_page:
            self.open_page(order[0])

    def _show_empty(self) -> None:
        self.content_stack.setCurrentWidget(self.empty_page)
        self.header.set_page("Ignite", "")
        self.setWindowTitle("Ignite")

    def _sidebar_collapsed_changed(self, collapsed: bool) -> None:
        self.controller.settings.sidebar_collapsed = collapsed
        settings_store.save(self.controller.settings, self.controller.settings_path)

    # Other windows.

    def _show_server(self) -> None:
        c = self.controller
        self.header.set_server(bool(c.settings.backend_url), c.server_up, c.settings.server_username)

    def open_setup_guide(self) -> None:
        """Shows the setup guide in the main window's content area; closing it returns to the page you were on."""
        from ignite_live.ui.setup_guide import SetupGuide

        if self.guide is not None:  # Already open: start over from the first step.
            self._close_guide(None)
        else:
            self._guide_return = getattr(self, "_current_page_key", "overview")
        guide = self.guide = SetupGuide(self.controller)
        self.content_stack.addWidget(guide)
        self.content_stack.setCurrentWidget(guide)
        self.header.set_page("Setup Guide", "Get Ignite ready for your first stream")
        self.setWindowTitle("Ignite — Setup Guide")
        guide.closed.connect(self._close_guide)

    def _close_guide(self, _finished: bool | None) -> None:
        guide, self.guide = self.guide, None
        if guide is None:
            return
        self.content_stack.removeWidget(guide)
        guide.deleteLater()
        if _finished is not None:
            self.open_page(self._guide_return)

    def open_logs(self) -> None:
        folder = settings_store.log_dir(self.controller.settings_path)
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def open_feedback(self) -> None:
        """The Ignite server's form (web: /feedback), with this version filled in."""
        server = (self.controller.settings.backend_url or main_server()).rstrip("/")
        if server:
            QDesktopServices.openUrl(QUrl(f"{server}/feedback?version={__version__}"))

    def open_simulator(self) -> None:
        if self.simulator is None:
            self.simulator = SimulateDialog(self.controller, self)
        self.simulator.show()
        self.simulator.raise_()

    def open_preferences(self, section: str = "") -> None:
        """Settings, open on a section (e.g. "Text to speech") when one's given."""
        self.open_page("settings")
        if section:
            self.pages["settings"].show_section(section)

    def open_add_overlays(self, selected: list[str] | None = None) -> None:
        """Pick overlays to add to OBS; ``selected`` pre-ticks some (default: all)."""
        from ignite_live.ui.add_overlays import AddOverlaysDialog

        AddOverlaysDialog(self.controller, self, selected).exec()

    def open_chat_popout(self) -> None:
        if self.chat_popout is None:
            self.chat_popout = ChatPopout(self.controller, list(self.chat_panel.history))
            self.chat_popout.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.chat_popout.destroyed.connect(lambda: setattr(self, "chat_popout", None))
            self.chat_popout.locked_changed.connect(self._popout_locked)
        self.chat_popout.show()
        self.chat_popout.raise_()
        self.chat_popout.activateWindow()

    def lock_chat_popout(self, locked: bool) -> None:
        """The Lock Pop-out Chat action (Live Chat's menu, or the tray): the only way back once it's click-through."""
        if self.chat_popout is not None:
            self.chat_popout.set_locked(locked)
        else:
            self.controller.settings.chat_popout_locked = locked  # Opens that way next time.
            settings_store.save(self.controller.settings, self.controller.settings_path)

    def _popout_locked(self, locked: bool) -> None:
        self.actions.lock_popout.setChecked(locked)
        if locked:
            self.controller.notice.emit("info", "Pop-out chat locked: clicks go through it to your game. Unlock it "
                                                "from Live Chat's menu or the tray icon's menu.")

    # Updates.

    def _studio(self, studio) -> None:
        self.header.set_stream(studio)
        if self.tray is not None:
            text = self.header.stream.text.text()
            self.tray.setToolTip(f"Ignite · {text}")

    def _account(self) -> None:
        """Who's signed in: Streamlabs (the stream key) or TikTok on the server, else none."""
        info = self.controller.account
        tiktok = self.controller.tiktok_account
        if info is not None:
            name, detail = f"@{info.username}", "Streamlabs" + (" · TikTok" if tiktok.get("connected") else "")
        elif tiktok.get("connected") and tiktok.get("username"):
            name, detail = f"@{tiktok['username']}", "TikTok via your server"
        else:
            name, detail = "Not signed in", "Local only" if not self.controller.settings.backend_url else "Server set up"
        self.sidebar.account.set_account(name, detail)
        self.header.profile.set_name(name if name != "Not signed in" else "Ignite")
        self.account_section.setText(name if name != "Not signed in" else "Ignite")

    def _panic_changed(self, on: bool) -> None:
        s = self.controller.settings
        done = [label for key, label in (("tts", "TTS muted"), ("alerts", "alerts held"), ("music", "music paused"),
                                         ("rules", "rules paused")) if getattr(s, f"panic_{key}")]
        if s.panic_scene:
            done.append(f"switched to {s.panic_scene}")
        self.panic_label.setText("Panic mode: " + (", ".join(done) or "on") + ".")
        self.panic_banner.setVisible(on)

    def _browser_link(self, title: str, url: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("Ignite couldn't open your browser. Copy this link into a browser on this computer, "
                    "sign in there, and Ignite will pick it up.")
        box.setInformativeText(url)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        copy = box.addButton("Copy Link", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(url))
        box.open()

    def _notice(self, level: str, text: str) -> None:
        self.notifications.add(level, text)
        self.toasts.show_message(level, text)

    def _settings_changed(self, settings) -> None:
        app = QApplication.instance()
        if app is not None and settings.ui_theme != self._theme:
            self._theme = settings.ui_theme
            apply_theme(app, settings.ui_theme)
        self._account()

    def _season_tick(self) -> None:
        app = QApplication.instance()
        if app is not None and self.controller.settings.ui_theme == SEASONAL and resolve_theme(SEASONAL) != colors().name:
            apply_theme(app, SEASONAL)

    def _restyle(self, _palette=None) -> None:
        """Colours set in code (rather than by the stylesheet) follow a switch between dark and light."""
        p = colors()
        self.panic_banner.setStyleSheet(f"QWidget#panicBanner {{ background: {p.live}; }} "
                                        f"QLabel {{ color: {p.on_live}; font-weight: 600; }}")

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About Ignite",
            f"<h3>Ignite {__version__}</h3>"
            "<p>Stream keys, OBS control, TikTok, Twitch and YouTube chat, song requests, and text to speech "
            "in one app.</p>"
            f"<p>Settings: {self.controller.settings_path.parent}<br>"
            f"Log: {settings_store.log_dir(self.controller.settings_path) / 'ignite.log'}</p>",
        )

    # Window layout is remembered between runs.

    def _restore_layout(self) -> None:
        geometry, _, _state = self.controller.settings.window_state.partition("|")
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode()))

    def _reset_layout(self) -> None:
        self.show_all_sections()

    def _save_layout(self) -> None:
        s = self.controller.settings
        s.window_state = f"{bytes(self.saveGeometry().toBase64()).decode()}|"
        visible = self.sidebar.visible_keys()
        s.closed_tabs = [key for key in PAGE_TITLES if key not in visible]
        s.tab_order = visible + s.closed_tabs
        settings_store.save(s, self.controller.settings_path)

    def closeEvent(self, event: QCloseEvent) -> None:
        s = self.controller.settings
        if self.tray is not None and s.close_to_tray and not self.quitting:
            self._save_layout()
            self.hide()
            event.ignore()
            if not s.tray_hint_shown:
                self.tray.showMessage("Ignite is still running", "It's in the system tray: click it to come back, "
                                      "or right-click it to quit.", QApplication.windowIcon(), 6000)
                s.tray_hint_shown = True
                settings_store.save(s, self.controller.settings_path)
            return
        if self.chat_popout is not None:
            self.chat_popout.close()  # Otherwise the app keeps running for the pop-out.
        self._shut_down()
        super().closeEvent(event)
        if self.tray is not None:
            self.tray.hide()
            QApplication.quit()  # The tray turned off quitting when the last window closes.

    def _shut_down(self) -> None:
        if self.shut_down:
            return
        self.shut_down = True
        self._save_layout()
        self.controller.shutdown()
        self.updates.install_on_quit()  # A downloaded update goes in place for next time.
