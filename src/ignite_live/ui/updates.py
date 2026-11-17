"""Automatic updates (see core/updates.py): the banner that announces them and the status bar's Update button.

Ignite checks its GitHub releases shortly after it starts and every few hours. A newer version downloads (or,
for the Flatpak, installs through Flatpak) in the background; it goes in when Ignite quits, or right away from
"Restart and Update". Nothing restarts on its own, so an update never interrupts a stream. Everything happens
in Ignite: What's New shows the release notes here, not on GitHub.
"""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ignite_live import __version__
from ignite_live.core import updates
from ignite_live.ui.theme import colors, set_props, theme_changed

log = logging.getLogger(__name__)

FIRST_CHECK_MS = 30_000  # After the window is up and chat has connected.
CHECK_EVERY_MS = 6 * 3600 * 1000


class UpdateManager(QObject):
    """States: "idle", "checking", "downloading" (or installing through Flatpak), "ready" (downloaded and checked,
    or installed by Flatpak: a restart away), "available" (newer, not downloaded: updates are off, it failed, or
    this copy runs from source), "current", "error"."""

    changed = Signal()
    _progress = Signal(int)  # From the download thread: percent.

    def __init__(self, controller, parent: QObject | None = None, install: updates.Install | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.install = install or updates.how_installed()
        self.state = "idle"
        self.message = ""
        self.percent = -1
        self.release: updates.Release | None = None
        self.ready: Path | None = None
        self._manual = False
        self._timer = QTimer(self, interval=CHECK_EVERY_MS)
        self._timer.timeout.connect(self._scheduled)
        self._progress.connect(self._on_progress)

    def start(self) -> None:
        folder = updates.download_folder(self.install, self.controller.settings_path.parent)
        updates.clean_downloads(folder)  # What a finished update left behind (the replaced folder, …).
        QTimer.singleShot(FIRST_CHECK_MS, self._scheduled)
        self._timer.start()

    @property
    def busy(self) -> bool:
        return self.state in ("checking", "downloading")

    @property
    def can_install(self) -> bool:
        """This copy can put an update in place itself (everything but running from source)."""
        return self.install.kind != updates.MANUAL

    def _scheduled(self) -> None:
        if self.controller.settings.auto_update:
            self.check()

    def check(self, manual: bool = False) -> None:
        """Ask GitHub for the latest release; ``manual`` (Help menu, the Update button) reports the outcome."""
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
        if not self.can_install:
            self._set("available", f"Ignite {release.version} is out. This copy runs from source: update it with "
                                   f"git pull.")
            self._tell("info")
            return
        if self.controller.settings.auto_update:
            self.download()  # Turned off: only say it's out; the Update button downloads it when clicked.
        else:
            self._set("available", f"Ignite {release.version} is out.")
            self._tell("info")

    def download(self) -> None:
        """Get the update ready: download, check and unpack it (or have Flatpak install it)."""
        release = self.release
        if release is None or self.state == "downloading":  # (Straight after a check, the state is "checking".)
            return
        if self.install.kind == updates.FLATPAK:
            self._set("downloading", f"Installing Ignite {release.version} through Flatpak…")
            self.controller.runtime.blocking(updates.flatpak_update, on_success=lambda _: self._downloaded(None),
                                             on_error=self._download_failed)
            return
        name = updates.asset_name(release, self.install)
        if name is None or name not in release.assets or updates.CHECKSUMS not in release.assets:
            self._set("available", f"Ignite {release.version} is out, but its release has no file for this copy "
                                   f"of Ignite yet. Ignite tries again later.")
            self._tell("error")
            return
        save_as = updates.UPDATE_PREFIX + name if self.install.kind != updates.INSTALLER else name
        if self.ready is not None and self.ready.exists() and release.version in self.ready.as_posix():
            self._set("ready", self._ready_text())
            return
        folder = updates.download_folder(self.install, self.controller.settings_path.parent)
        updates.clean_downloads(folder)
        self.percent = 0
        self._set("downloading", f"Downloading Ignite {release.version}…")
        install = self.install

        def work() -> Path:
            def progress(done: int, total: int) -> None:
                if total:
                    self._progress.emit(int(done * 100 / total))

            path = updates.download(release, name, folder, save_as=save_as, progress=progress)
            if install.kind in (updates.ZIP, updates.TARBALL):
                path = updates.unpack(path, install, release.version)
            return path

        self.controller.runtime.blocking(work, on_success=self._downloaded, on_error=self._download_failed)

    def _on_progress(self, percent: int) -> None:
        if self.state == "downloading" and percent != self.percent:
            self.percent = percent
            self.changed.emit()

    def _downloaded(self, path: Path | None) -> None:
        self.ready = path
        self.percent = -1
        self._set("ready", self._ready_text())
        self._tell("info")

    def _ready_text(self) -> str:
        if self.install.kind == updates.FLATPAK:
            return f"Ignite {self.release.version} is installed. Restart Ignite to use it."
        return f"Ignite {self.release.version} is ready. It installs when you quit Ignite."

    def _download_failed(self, error: BaseException) -> None:
        log.warning("Update failed: %s", error)
        self.percent = -1
        reason = str(error) if isinstance(error, updates.UpdateError) else "the download failed."
        self._set("available", f"Ignite {self.release.version} is out, but {reason[:1].lower() + reason[1:]}")
        self._tell("error")

    def _failed(self, error: BaseException) -> None:
        log.info("Update check failed: %s", error)
        self._set("error", str(error) if isinstance(error, updates.UpdateError) else "Couldn't check for updates.")
        self._tell("error")

    def _tell(self, level: str) -> None:
        """A check asked for says what happened; background checks only speak up about a new version."""
        if self._manual or self.state in ("ready", "available"):
            self.controller.notice.emit(level, self.message)
        self._manual = False

    def _set(self, state: str, message: str) -> None:
        self.state, self.message = state, message
        self.changed.emit()

    # Installing.

    def install_now(self) -> bool:
        """Hand over to the update and start Ignite again afterwards. The caller quits Ignite."""
        return self._install(relaunch=True)

    def install_on_quit(self) -> None:
        """Ignite is quitting: put the downloaded update in place for next time."""
        if self.state == "ready" and self.controller.settings.auto_update and self.install.kind != updates.FLATPAK:
            self._install(relaunch=False)

    def _install(self, relaunch: bool) -> bool:
        if self.install.kind != updates.FLATPAK and (self.ready is None or not self.ready.exists()):
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

    def notes(self) -> str:
        """The release's notes (its CHANGELOG section), as Markdown."""
        if self.release is None:
            return ""
        return self.release.notes.strip() or f"Ignite {self.release.version}."


def show_notes(manager: UpdateManager, parent: QWidget) -> None:
    """What's New, inside Ignite."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(f"What's New in Ignite {manager.release.version}" if manager.release else "What's New")
    dialog.resize(560, 520)
    layout = QVBoxLayout(dialog)
    text = QTextBrowser()
    text.setOpenExternalLinks(True)
    text.setMarkdown(manager.notes())
    layout.addWidget(text)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()


def restart_and_update(manager: UpdateManager, window) -> None:
    """The one place an update is installed from (banner and status bar): asks first while live."""
    c = manager.controller
    live = c.obs_streaming or any(c.live.values())
    if live and QMessageBox.question(
        window, "Update Ignite",
        "You're live. Restarting closes Ignite for a few seconds: chat, music and overlays pause until it's back "
        "(OBS keeps streaming).\n\nRestart and update now?",
    ) != QMessageBox.StandardButton.Yes:
        return
    if manager.install_now():
        window.quit()


def act(manager: UpdateManager, window) -> None:
    """What the Update button does in each state."""
    if manager.state == "ready":
        restart_and_update(manager, window)
    elif manager.state == "available" and manager.can_install:
        manager.download()
    elif manager.state in ("idle", "current", "error"):
        manager.check(manual=True)


class UpdateBanner(QWidget):
    """A slim bar above the tabs while an update is ready (or out, when it couldn't be got ready)."""

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
        self.action.clicked.connect(lambda: act(manager, window))
        notes = QPushButton("What's New")
        notes.clicked.connect(lambda: show_notes(manager, window))
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
        if manager.state == "ready":
            self.action.setText("Restart and Update")
        else:
            self.action.setText("Download and Install")
        self.action.setVisible(manager.state == "ready" or manager.can_install)

    def _later(self) -> None:
        if self.manager.release:
            self.dismissed = self.manager.release.version
        self.setVisible(False)


class UpdateButton(QToolButton):
    """Always in the status bar: what the update is doing, and one click to the next step."""

    def __init__(self, manager: UpdateManager, window) -> None:
        super().__init__()
        self.manager, self.window_ = manager, window
        self.setObjectName("updateButton")
        self.setAutoRaise(True)
        self.clicked.connect(lambda: act(manager, window))
        manager.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        m = self.manager
        version = m.release.version if m.release else ""
        if m.state == "ready":
            text, tip = f"⬆ Restart to update to {version}", m.message
        elif m.state == "downloading":
            text = f"⬆ Updating… {m.percent}%" if m.percent >= 0 else "⬆ Updating…"
            tip = m.message
        elif m.state == "available":
            text = f"⬆ Update to {version}" if m.can_install else f"Ignite {version} is out"
            tip = m.message
        elif m.state == "checking":
            text, tip = "Checking for updates…", ""
        else:
            text, tip = "Check for Updates", m.message or f"Ignite {__version__}"
        self.setText(text)
        self.setToolTip(tip)
        self.setEnabled(m.state not in ("checking", "downloading") and not (m.state == "available" and not m.can_install))
        set_props(self, primary=m.state in ("ready", "available"))
