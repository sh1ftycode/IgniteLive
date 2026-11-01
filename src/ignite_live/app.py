"""Ignite desktop app entry point."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QtMsgType, QTimer, qInstallMessageHandler
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication

from ignite_live import __version__
from ignite_live.controller import Controller
from ignite_live.core import keychain, settings
from ignite_live.core.bundled import import_bundled_keys
from ignite_live.core.runtime import Runtime
from ignite_live.ui.icons import logo
from ignite_live.ui.main_window import MainWindow
from ignite_live.ui.splash import Splash
from ignite_live.ui.theme import apply_theme

SPLASH_SECONDS = 1.2  # At least this long, so the loading screen doesn't just flicker.
LOG_BYTES = 2 * 1024 * 1024  # Per file; ignite.log rolls over to ignite.log.1 … .3.
LOG_BACKUPS = 3
QT_LEVELS = {
    QtMsgType.QtDebugMsg: logging.DEBUG,
    QtMsgType.QtInfoMsg: logging.INFO,
    QtMsgType.QtWarningMsg: logging.WARNING,
    QtMsgType.QtCriticalMsg: logging.ERROR,
    QtMsgType.QtFatalMsg: logging.CRITICAL,
}


class _LogWriter:
    """Stands in for stdout/stderr: anything printed goes to the log, a line at a time."""

    def __init__(self, logger: logging.Logger, level: int) -> None:
        self.logger, self.level, self.pending = logger, level, ""

    def write(self, text: str) -> int:
        lines = (self.pending + text).split("\n")
        self.pending = lines.pop()
        for line in lines:
            if line.strip():
                self.logger.log(self.level, line.rstrip())
        return len(text)

    def flush(self) -> None:
        if self.pending.strip():
            self.logger.log(self.level, self.pending.rstrip())
        self.pending = ""

    def isatty(self) -> bool:
        return False


def _setup_logging() -> None:
    """Everything goes to <Ignite folder>/logs/ignite.log and nowhere else: not the terminal Ignite was
    started from, and no console window on Windows. Set IGNITE_DEBUG=1 to also see it in the terminal."""
    folder = settings.log_dir(settings.default_path())
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("ignite.log", "shiftytool.log"):  # Before 1.2 the log sat next to settings.json.
        stray = folder.parent / name
        if stray.is_file():
            try:
                stray.replace(folder / name.replace(".log", "-old.log"))
            except OSError:
                pass
    handlers: list[logging.Handler] = [
        RotatingFileHandler(folder / "ignite.log", maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
    ]
    debug = bool(os.environ.get("IGNITE_DEBUG")) and sys.stderr is not None
    if debug:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    # TikTokLive logs every frame at INFO; keep only its warnings.
    logging.getLogger("TikTokLive").setLevel(logging.WARNING)
    logging.captureWarnings(True)
    qt = logging.getLogger("qt")
    qInstallMessageHandler(lambda mode, _context, message: qt.log(QT_LEVELS.get(mode, logging.WARNING), message))
    crash = logging.getLogger("ignite")
    sys.excepthook = lambda kind, error, trace: crash.critical("Unexpected error", exc_info=(kind, error, trace))
    threading.excepthook = lambda args: crash.critical(
        "Unexpected error in %s", args.thread.name if args.thread else "a thread",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    if not debug:
        sys.stdout = _LogWriter(logging.getLogger("stdout"), logging.INFO)
        sys.stderr = _LogWriter(logging.getLogger("stderr"), logging.WARNING)


def _icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(QPixmap.fromImage(logo(size)))
    return icon


def main() -> int:
    _setup_logging()
    if sys.platform == "win32":
        # Own taskbar identity, so Windows shows our icon instead of Python's.
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Ignite.Desktop")
    app = QApplication(sys.argv)
    app.setApplicationName("Ignite")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(_icon())
    started = time.monotonic()
    splash = Splash()
    splash.show()
    splash.step("Starting…", 0.05)
    log = logging.getLogger("ignite")
    log.info("Ignite %s starting (%s, keychain: %s)", __version__, sys.platform, keychain.backend_name())
    imported = import_bundled_keys()
    if imported:
        log.info("Copied bundled keys into the keychain: %s", ", ".join(imported))
    splash.step("Loading your settings…", 0.25)
    controller = Controller(Runtime())
    apply_theme(app, controller.settings.ui_theme)
    splash.step("Setting up the workspace…", 0.55)
    window = MainWindow(controller)
    splash.step("Getting ready to connect…", 0.85)

    def reveal() -> None:
        splash.step("Ready", 1.0)
        window.show()
        splash.finish(window)
        s = controller.settings
        if not s.setup_done and not s.tiktok_username:  # First run: the setup guide, once the window is up.
            QTimer.singleShot(400, window.open_setup_guide)
        controller.start()
        window.updates.start()  # Checks GitHub releases shortly, then every few hours.

    QTimer.singleShot(max(0, int((SPLASH_SECONDS - (time.monotonic() - started)) * 1000)), reveal)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
