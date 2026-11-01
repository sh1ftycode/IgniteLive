"""Automatic updates (see core/updates.py) and the banner that announces them.

Ignite checks its GitHub releases shortly after it starts and every few hours. A
newer version downloads in the background; it installs when Ignite quits, or
right away from the banner's "Restart and Update". Nothing restarts on its own,
so an update never interrupts a stream.
"""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QWidget

from ignite_live import __version__
from ignite_live.core import updates
from ignite_live.ui.theme import colors, set_props, theme_changed

log = logging.getLogger(__name__)

FIRST_CHECK_MS = 30_000  # After the window is up and chat has connected.
CHECK_EVERY_MS = 6 * 3600 * 1000


class UpdateManager(QObject):
    """States: "idle", "checking", "downloading", "ready" (downloaded, verified), "available" (newer, but this copy
    can't install it itself), "current", "error"."""

    changed = Signal()

    def __init__(self, controller, parent: QObject | None = None, install: updates.Install | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.install = install or updates.how_installed()
        self.state = "idle"
        self.message = ""
        self.release: updates.Release | None = None
        self.ready: Path | None = None
        self._manual = False
        self._timer = QTimer(self, interval=CHECK_EVERY_MS)
        self._timer.timeout.connect(self._scheduled)

    def start(self) -> None:
        QTimer.singleShot(FIRST_CHECK_MS, self._scheduled)
        self._timer.start()

    @property
    def busy(self) -> bool:
        return self.state in ("checking", "downloading")

    def _scheduled(self) -> None:
        if self.controller.settings.auto_update:
            self.check()

    def check(self, manual: bool = False) -> None:
        """Ask GitHub for the latest release; ``manual`` (Help menu) reports the outcome either way."""
        if self.busy:
            return
        self._manual = manual
        self._set("checking", "Checking for updates…")
        self.controller.runtime.blocking(partial(updates.latest_release, user_agent=f"Ignite/{__version__}"),
                                         on_success=self._found, on_error=self._failed)

    def _found(self, release: updates.Release) -> None:
        if not updates.is_newer(release.version, __version__):
            self._set("current", f"Ignite {__version__} is the latest version.")
            self._tell("info")
            return
        self.release = release
        name = updates.asset_name(release, self.install)
        can_install = name is not None and name in release.assets and updates.CHECKSUMS in release.assets
        if not can_install or not self.controller.settings.auto_update:
            self._set("available", f"Ignite {release.version} is out.")
            self._tell("info")
            return
        save_as = updates.UPDATE_PREFIX + name if self.install.kind == updates.APPIMAGE else name
        if self.ready is not None and self.ready.name == save_as and self.ready.is_file():
            self._set("ready", self._ready_text())
            return
        folder = updates.download_folder(self.install, self.controller.settings_path.parent)
        updates.clean_downloads(folder)
        self._set("downloading", f"Downloading Ignite {release.version}…")
        self.controller.runtime.blocking(partial(updates.download, release, name, folder, save_as=save_as),
                                         on_success=self._downloaded, on_error=self._download_failed)

    def _downloaded(self, path: Path) -> None:
        self.ready = path
        self._set("ready", self._ready_text())
        self._tell("info")

    def _ready_text(self) -> str:
        return f"Ignite {self.release.version} is ready. It installs when you quit Ignite."

    def _download_failed(self, error: BaseException) -> None:
        log.warning("Update download failed: %s", error)
        reason = str(error) if isinstance(error, updates.UpdateError) else "the download failed."
        self._set("available", f"Ignite {self.release.version} is out, but {reason[:1].lower() + reason[1:]}")
        self._tell("error")

    def _failed(self, error: BaseException) -> None:
        log.info("Update check failed: %s", error)
        self._set("error", str(error) if isinstance(error, updates.UpdateError) else "Couldn't check for updates.")
        self._tell("error")

    def _tell(self, level: str) -> None:
        """Help → Check for Updates says what happened; background checks only speak up about a new version."""
        if self._manual or self.state in ("ready", "available"):
            self.controller.notice.emit(level, self.message)
        self._manual = False

    def _set(self, state: str, message: str) -> None:
        self.state, self.message = state, message
        self.changed.emit()

    # Installing.

    def install_now(self) -> bool:
        """Hand over to the downloaded update and start it again afterwards. The caller quits Ignite."""
        return self._install(relaunch=True)

    def install_on_quit(self) -> None:
        """Ignite is quitting: put the downloaded update in place for next time."""
        if self.state == "ready" and self.controller.settings.auto_update:
            self._install(relaunch=False)

    def _install(self, relaunch: bool) -> bool:
        if self.ready is None or not self.ready.is_file():
            return False
        try:
            updates.install(self.ready, self.install, relaunch)
        except (OSError, updates.UpdateError) as error:
            log.warning("Installing the update failed: %s", error)
            self._set("available", f"Ignite {self.release.version} couldn't be installed: {error}")
            return False
        log.info("Installing Ignite %s (%s)", self.release.version, self.install.kind)
        self.state = "installing"
        return True

    def open_release_page(self) -> None:
        QDesktopServices.openUrl(QUrl(self.release.page if self.release else
                                      f"https://github.com/{updates.REPO}/releases/latest"))


class UpdateBanner(QWidget):
    """A slim bar above the tabs while an update is ready (or out, for copies that can't install it)."""

    def __init__(self, manager: UpdateManager, window) -> None:
        super().__init__()
        self.manager, self.window_ = manager, window
        self.dismissed = ""  # "Later" hides it for that version, this run.
        self.setObjectName("updateBanner")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 8, 16, 8)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.action = QPushButton()
        set_props(self.action, primary=True)
        self.action.clicked.connect(self._act)
        notes = QPushButton("What's New")
        notes.clicked.connect(manager.open_release_page)
        later = QPushButton("Later")
        later.clicked.connect(self._later)
        row.addWidget(self.text, 1)
        for button in (self.action, notes, later):
            row.addWidget(button)
        manager.changed.connect(self.refresh)
        theme_changed().connect(self._restyle)
        self._restyle()
        self.refresh()

    def _restyle(self, _palette=None) -> None:
        p = colors()
        self.setStyleSheet(f"QWidget#updateBanner {{ background: {p.raised}; border-bottom: 1px solid {p.divider}; }}")

    def refresh(self) -> None:
        manager = self.manager
        version = manager.release.version if manager.release else ""
        shown = manager.state in ("ready", "available") and version != self.dismissed
        self.setVisible(shown)
        if not shown:
            return
        self.text.setText(manager.message)
        self.action.setText("Restart and Update" if manager.state == "ready" else "Download")

    def _act(self) -> None:
        manager = self.manager
        if manager.state != "ready":
            manager.open_release_page()
            return
        c = manager.controller
        live = c.obs_streaming or any(c.live.values())
        if live and QMessageBox.question(
            self, "Update Ignite",
            "You're live. Restarting closes Ignite for a few seconds: chat, music and overlays pause until it's back "
            "(OBS keeps streaming).\n\nRestart and update now?",
        ) != QMessageBox.StandardButton.Yes:
            return
        if manager.install_now():
            self.window_.quit()

    def _later(self) -> None:
        if self.manager.release:
            self.dismissed = self.manager.release.version
        self.setVisible(False)
