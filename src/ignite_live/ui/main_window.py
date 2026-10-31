"""Main window: menu bar, toolbar, closable tabs (Stream, Music, Overlays, …, Games),
dockable Chat / OBS / Connections / Activity panels, a status bar, and the system tray icon."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
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
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ignite_live import __version__
from ignite_live.controller import Controller
from ignite_live.core import settings as settings_store
from ignite_live.core.events import ConnectionState, StatusChange
from ignite_live.ui.actions import AppActions
from ignite_live.ui.pages.actions import ActionsPage
from ignite_live.ui.pages.streams import StreamsPage
from ignite_live.ui.pages.games import GamesPage
from ignite_live.ui.pages.viewers import ViewersPage
from ignite_live.ui.docks import ActivityPanel, ChatPanel, ChatPopout, ConnectionsPanel, dock
from ignite_live.integrations.obs_studio import Studio, clock
from ignite_live.ui.obs_panel import ObsPanel
from ignite_live.ui.pages.music import MusicPage
from ignite_live.ui.pages.overlays import OverlaysPage
from ignite_live.ui.pages.stream import StreamPage
from ignite_live.ui.checklist import ChecklistDialog, go_live
from ignite_live.ui.preferences import PreferencesDialog
from ignite_live.ui.profiles import ProfilesMenu
from ignite_live.ui.simulate import SimulateDialog
from ignite_live.ui.theme import COLORS, STATE_TEXT, apply_theme, state_color, warn_color

# Tab titles, in the default order (View → Ctrl+1… follows it too).
PAGE_TITLES = {
    "stream": "&Stream",
    "music": "&Music",
    "overlays": "&Overlays",
    "actions": "Ac&tions",
    "streams": "Str&eams",
    "viewers": "Vie&wers",
    "games": "&Games",
}

STATUS_NAMES = {
    "tiktok": "TikTok",
    "twitch": "Twitch",
    "youtube": "YouTube",
    "obs": "OBS",
    "overlay": "Overlays",
    "streamlabs": "Streamlabs",
    "backend": "Server",
    "discord": "Discord",
    "tts": "TTS",
}
# Shown in the status bar only while in use, so unused extras don't crowd it.
OPTIONAL_STATUS = {"twitch", "youtube", "tts"}
INFO_SECONDS = 8
ERROR_SECONDS = 20


class MainWindow(QMainWindow):
    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        self.setWindowTitle("Ignite")
        screen = QApplication.primaryScreen()
        room = screen.availableGeometry() if screen is not None else None
        self.resize(min(1440, room.width()) if room else 1440, min(900, room.height()) if room else 900)
        self.setMinimumSize(900, 600)
        self.setDockOptions(QMainWindow.DockOption.AnimatedDocks | QMainWindow.DockOption.AllowTabbedDocks)
        self.chat_popout: ChatPopout | None = None
        self.simulator: SimulateDialog | None = None
        self.tray: QSystemTrayIcon | None = None
        self.quitting = False  # Closing the window only hides it to the tray, unless this is set.
        self.shut_down = False
        self.actions = AppActions(controller, self)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)  # Closed tabs come back from the View menu.
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        bar = self.tabs.tabBar()
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        bar.customContextMenuRequested.connect(self._tab_menu)
        # Panic mode banner, above the tabs.
        self.panic_banner = QWidget()
        self.panic_banner.setStyleSheet("background: #b4232a; color: white;")
        banner = QHBoxLayout(self.panic_banner)
        banner.setContentsMargins(12, 6, 12, 6)
        self.panic_label = QLabel()
        self.panic_label.setStyleSheet("font-weight: 600;")
        resume = QPushButton("Resume")
        resume.clicked.connect(controller.resume)
        banner.addWidget(self.panic_label, 1)
        banner.addWidget(resume)
        self.panic_banner.hide()
        controller.panic_changed.connect(self._panic_changed)
        self.pages = {
            "stream": StreamPage(controller, self.actions),
            "music": MusicPage(controller, self.actions),
            "overlays": OverlaysPage(controller),
            "actions": ActionsPage(controller),
            "streams": StreamsPage(controller),
            "viewers": ViewersPage(controller),
            "games": GamesPage(controller),
        }
        self.no_tabs = QWidget()  # Shown when every tab is closed.
        empty = QVBoxLayout(self.no_tabs)
        empty.addStretch(1)
        hint = QLabel("Every tab is closed. Open one from the View menu, or:")
        hint.setEnabled(False)
        empty.addWidget(hint, 0, Qt.AlignmentFlag.AlignHCenter)
        reopen = QPushButton("Reopen All Tabs")
        reopen.clicked.connect(self.reopen_tabs)
        empty.addWidget(reopen, 0, Qt.AlignmentFlag.AlignHCenter)
        empty.addStretch(1)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.tabs)
        self.stack.addWidget(self.no_tabs)
        self._restore_tabs()
        central = QWidget()
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.panic_banner)
        column.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        # Side docks run the full height; Connections and Activity share the space under the tabs.
        self.setCorner(Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.setCorner(Qt.Corner.BottomRightCorner, Qt.DockWidgetArea.RightDockWidgetArea)
        self.chat_panel = ChatPanel(controller, self.actions)
        self.chat_dock = dock("Chat", "chatDock", self.chat_panel, self)
        self.obs_panel = ObsPanel(controller, self.actions)
        self.obs_dock = dock("OBS", "obsDock", self.obs_panel, self)
        self.connections_dock = dock("Connections", "connectionsDock", ConnectionsPanel(controller), self)
        self.activity_panel = ActivityPanel(controller)
        self.activity_dock = dock("Activity", "activityDock", self.activity_panel, self)
        self.docks = (self.chat_dock, self.obs_dock, self.connections_dock, self.activity_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.chat_dock)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.obs_dock)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.connections_dock)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.activity_dock)
        self.tabifyDockWidget(self.connections_dock, self.activity_dock)
        self.activity_dock.raise_()
        self.resizeDocks([self.chat_dock, self.obs_dock], [360, 300], Qt.Orientation.Horizontal)
        self.resizeDocks([self.activity_dock], [180], Qt.Orientation.Vertical)

        self._menus()
        self._toolbar()
        self._status_bar()
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray()

        controller.status_changed.connect(self._status)
        controller.notice.connect(self._notice)
        controller.settings_changed.connect(self._settings_changed)
        for change in controller.statuses.values():
            self._status(change)
        self._restore_layout()

    # Construction.

    def _menus(self) -> None:
        a = self.actions
        a.preferences.triggered.connect(lambda: self.open_preferences())
        a.open_logs.triggered.connect(self.open_logs)
        a.quit.triggered.connect(self.quit)
        a.about.triggered.connect(self._about)
        a.popout_chat.triggered.connect(self.open_chat_popout)
        a.add_all_overlays.triggered.connect(lambda: self.open_add_overlays())
        a.go_live = lambda: go_live(self, self.controller)
        a.check_stream.triggered.connect(lambda: ChecklistDialog(self.controller, self).exec())
        a.simulate.triggered.connect(self.open_simulator)

        bar = self.menuBar()
        file = bar.addMenu("&File")
        file.addAction(a.preferences)
        self.profiles_menu = ProfilesMenu(self.controller, self)
        file.addMenu(self.profiles_menu)
        file.addAction(a.open_logs)
        file.addSeparator()
        file.addAction(a.quit)

        stream = bar.addMenu("&Stream")
        for item in (a.sign_in, a.sign_out, None, a.get_key, a.new_key, a.send_key, None, a.check_stream,
                     a.toggle_stream, a.end_live, None, a.panic, None, a.refresh_outputs):
            stream.addSeparator() if item is None else stream.addAction(item)

        chat = bar.addMenu("&Chat")
        for item in (a.connect_chat, a.disconnect_chat, None, a.popout_chat, None, a.skip_tts, a.mute_tts):
            chat.addSeparator() if item is None else chat.addAction(item)

        obs = bar.addMenu("&OBS")
        for item in (a.toggle_record, a.pause_record, None, a.toggle_replay, a.save_replay, None,
                     a.toggle_vertical_record, a.toggle_vertical_backtrack, a.save_vertical_clip, None,
                     a.toggle_virtualcam, a.screenshot, None, a.refresh_overlays, a.add_all_overlays):
            obs.addSeparator() if item is None else obs.addAction(item)

        music = bar.addMenu("&Music")
        for item in (a.skip, a.clear_queue, None, a.stop_playlist):
            music.addSeparator() if item is None else music.addAction(item)

        view = bar.addMenu("&View")
        for number, (key, title) in enumerate(PAGE_TITLES.items(), 1):
            action = view.addAction(title.replace("&", ""), lambda k=key: self.open_page(k))  # Reopens a closed tab.
            action.setShortcut(f"Ctrl+{number}")
        view.addAction("Reopen All Tabs", self.reopen_tabs)
        view.addSeparator()
        view.addAction(a.simulate)
        view.addSeparator()
        for panel in self.docks:
            view.addAction(panel.toggleViewAction())
        view.addSeparator()
        view.addAction("Reset Layout", self._reset_layout)

        help_menu = bar.addMenu("&Help")
        help_menu.addAction("&Setup Guide…", self.open_setup_guide).setShortcut("F1")
        help_menu.addSeparator()
        help_menu.addAction(a.about)

    def _toolbar(self) -> None:
        a = self.actions
        bar = QToolBar("Main toolbar")
        bar.setObjectName("mainToolbar")
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)  # Icons stay in the menus.
        bar.setMovable(False)
        # Only what's needed mid-stream; the rest is a tab or a menu away.
        for item in (a.toggle_stream, a.toggle_record, a.save_replay, None, a.popout_chat, a.preferences, None,
                     a.panic):
            bar.addSeparator() if item is None else bar.addAction(item)
        for action, role in ((a.toggle_stream, "primary"), (a.panic, "danger")):  # Styled in ui/theme.py.
            button = bar.widgetForAction(action)
            if button is not None:
                button.setObjectName(role)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        self.toolbar = bar

    def _tray(self) -> None:
        """Tray icon: click to show or hide the window; the menu has the go-live essentials."""
        a = self.actions
        self.tray = QSystemTrayIcon(QApplication.windowIcon(), self)
        self.tray.setToolTip("Ignite")
        menu = QMenu(self)
        self.tray_toggle = menu.addAction("Hide Ignite", self.toggle_window)
        menu.addSeparator()
        for item in (a.toggle_stream, a.toggle_record, a.save_replay, None, a.popout_chat, a.mute_tts, None,
                     a.panic, None, a.quit):
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
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def quit(self) -> None:
        """File → Quit and the tray's Quit: really close, rather than hiding to the tray."""
        self.quitting = True
        self.close()

    def _status_bar(self) -> None:
        # Live/recording time and stream health at a glance; empty while idle.
        self.health_label = QLabel("")
        self.health_label.setContentsMargins(6, 0, 12, 0)
        self.statusBar().addPermanentWidget(self.health_label)
        self.controller.studio_changed.connect(self._health)
        self.status_labels: dict[str, QLabel] = {}
        for source, name in STATUS_NAMES.items():
            label = QLabel(name)
            label.setContentsMargins(4, 0, 4, 0)
            self.statusBar().addPermanentWidget(label)
            self.status_labels[source] = label

    # Navigation.

    def open_page(self, key: str) -> None:
        """Pages that buttons and links refer to: tabs, the chat and OBS panels, and preferences."""
        if key == "settings":
            self.open_preferences()
        elif key.startswith("prefs-"):  # e.g. "prefs-Text to speech" opens that Preferences section.
            self.open_preferences(key.removeprefix("prefs-"))
        elif key == "simulate":
            self.open_simulator()
        elif key == "guide":
            self.open_setup_guide()
        elif key == "checklist":
            ChecklistDialog(self.controller, self).exec()
        elif key == "chat":
            self.chat_dock.show()
            self.chat_dock.raise_()
            self.chat_panel.username.setFocus()
        elif key == "obs":  # The OBS tab before 1.2.
            self.obs_dock.show()
            self.obs_dock.raise_()
        else:
            key = key if key in self.pages else "stream"
            if self.tabs.indexOf(self.pages[key]) < 0:
                self.tabs.addTab(self.pages[key], PAGE_TITLES[key])
                self._tabs_changed()
            self.tabs.setCurrentWidget(self.pages[key])

    # Tabs: each can be closed and dragged into a new order; both are remembered.

    def _key_of(self, page: QWidget) -> str:
        return next(key for key, widget in self.pages.items() if widget is page)

    def _restore_tabs(self) -> None:
        s = self.controller.settings
        order = [key for key in s.tab_order if key in PAGE_TITLES]
        order += [key for key in PAGE_TITLES if key not in order]
        for key in order:
            if key not in s.closed_tabs:
                self.tabs.addTab(self.pages[key], PAGE_TITLES[key])
        self._tabs_changed()

    def close_tab(self, index: int) -> None:
        self.tabs.removeTab(index)  # The page itself stays alive, so it keeps its state for when it comes back.
        self._tabs_changed()

    def close_other_tabs(self, page: QWidget) -> None:
        for index in reversed(range(self.tabs.count())):
            if self.tabs.widget(index) is not page:
                self.tabs.removeTab(index)
        self._tabs_changed()

    def reopen_tabs(self) -> None:
        for key in PAGE_TITLES:
            if self.tabs.indexOf(self.pages[key]) < 0:
                self.tabs.addTab(self.pages[key], PAGE_TITLES[key])
        self._tabs_changed()

    def _tabs_changed(self) -> None:
        self.stack.setCurrentWidget(self.tabs if self.tabs.count() else self.no_tabs)

    def _tab_menu(self, pos) -> None:
        bar = self.tabs.tabBar()
        index = bar.tabAt(pos)
        menu = QMenu(self)
        if index >= 0:
            page = self.tabs.widget(index)
            menu.addAction("Close Tab", lambda: self.close_tab(self.tabs.indexOf(page)))
            menu.addAction("Close Other Tabs", lambda: self.close_other_tabs(page))
            menu.addSeparator()
        menu.addAction("Reopen All Tabs", self.reopen_tabs).setEnabled(self.tabs.count() < len(PAGE_TITLES))
        menu.exec(bar.mapToGlobal(pos))

    def open_setup_guide(self) -> None:
        from ignite_live.ui.setup_guide import SetupGuide

        SetupGuide(self.controller, self).exec()

    def open_logs(self) -> None:
        folder = settings_store.log_dir(self.controller.settings_path)
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def open_simulator(self) -> None:
        if self.simulator is None:
            self.simulator = SimulateDialog(self.controller, self)
        self.simulator.show()
        self.simulator.raise_()

    def open_preferences(self, tab: str = "OBS") -> None:
        PreferencesDialog(self.controller, self, tab).exec()

    def open_add_overlays(self, selected: list[str] | None = None) -> None:
        """Pick overlays to add to OBS; ``selected`` pre-ticks some (default: all)."""
        from ignite_live.ui.add_overlays import AddOverlaysDialog

        AddOverlaysDialog(self.controller, self, selected).exec()

    def open_chat_popout(self) -> None:
        if self.chat_popout is None:
            self.chat_popout = ChatPopout(self.controller, list(self.chat_panel.history))
            self.chat_popout.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.chat_popout.destroyed.connect(lambda: setattr(self, "chat_popout", None))
        self.chat_popout.show()
        self.chat_popout.raise_()
        self.chat_popout.activateWindow()

    # Updates.

    def _status(self, change: StatusChange) -> None:
        # A coloured dot and the name; the state and details are in the tooltip (and the Connections panel).
        label = self.status_labels[change.source]
        name = STATUS_NAMES[change.source]
        lit = change.state.value in ("connected", "error")
        dot = state_color(change.state, label).name() if lit else COLORS["faint"]
        label.setText(f'<span style="color: {dot};">●</span> {name}')
        label.setToolTip(f"{name}: {STATE_TEXT[change.state]}" + (f"\n{change.detail}" if change.detail else ""))
        label.setEnabled(change.state is not ConnectionState.OFFLINE)
        label.setVisible(change.source not in OPTIONAL_STATUS or change.state is not ConnectionState.OFFLINE)

    def _health(self, studio: Studio) -> None:
        parts = []
        health = studio.health
        if studio.streaming:
            parts.append(f"● LIVE {clock(health.stream_duration_ms)}")
            if health.bitrate_kbps:
                parts.append(f"{health.bitrate_kbps:,} kbps")
            parts.append(f"{health.network_dropped_pct:.1f}% dropped")
        if studio.recording:
            parts.append(("REC paused " if studio.record_paused else "● REC ") + clock(studio.record_duration_ms))
        self.health_label.setText("  ·  ".join(parts))
        if self.tray is not None:
            self.tray.setToolTip("  ·  ".join(["Ignite", *(part.removeprefix("● ") for part in parts)]))
        bad = studio.streaming and (health.reconnecting or health.network_dropped_pct >= 1 or health.render_lag_pct >= 1)
        color = (warn_color(self.health_label) if bad else state_color(ConnectionState.ERROR, self.health_label)).name()
        self.health_label.setStyleSheet(f"color: {color}; font-weight: bold;" if parts else "")

    def _panic_changed(self, on: bool) -> None:
        s = self.controller.settings
        done = [label for key, label in (("tts", "TTS muted"), ("alerts", "alerts held"), ("music", "music paused"),
                                         ("rules", "rules paused")) if getattr(s, f"panic_{key}")]
        if s.panic_scene:
            done.append(f"switched to {s.panic_scene}")
        self.panic_label.setText("Panic mode: " + (", ".join(done) or "on") + ".")
        self.panic_banner.setVisible(on)

    def _notice(self, level: str, text: str) -> None:
        self.statusBar().showMessage(text, (ERROR_SECONDS if level == "error" else INFO_SECONDS) * 1000)

    def _settings_changed(self, settings) -> None:
        app = QApplication.instance()
        if app is not None and settings.ui_theme != getattr(self, "_theme", None):
            self._theme = settings.ui_theme
            apply_theme(app, settings.ui_theme)

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
        self._default_state = self.saveState()
        geometry, _, state = self.controller.settings.window_state.partition("|")
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode()))
        if state:
            self.restoreState(QByteArray.fromBase64(state.encode()))

    def _reset_layout(self) -> None:
        self.restoreState(self._default_state)
        for panel in self.docks:
            panel.show()
        self.tabs.clear()  # Removes the tabs, not the pages.
        self.reopen_tabs()
        self.tabs.setCurrentIndex(0)

    def _save_layout(self) -> None:
        s = self.controller.settings
        s.window_state = (
            f"{bytes(self.saveGeometry().toBase64()).decode()}|{bytes(self.saveState().toBase64()).decode()}"
        )
        open_tabs = [self._key_of(self.tabs.widget(i)) for i in range(self.tabs.count())]
        s.closed_tabs = [key for key in PAGE_TITLES if key not in open_tabs]
        s.tab_order = open_tabs + s.closed_tabs
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
