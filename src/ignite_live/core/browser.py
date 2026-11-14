"""Open a link in the user's browser, including from the packaged Linux build.

The PyInstaller build (and the AppImage around it) points ``LD_LIBRARY_PATH`` at
its own bundled libraries. ``webbrowser.open`` hands that to ``xdg-open``, so the
system's ``gio``/``flatpak``/browser load Ignite's older copies, crash, and the
link silently never opens. Worse, ``webbrowser`` reports success as soon as
``xdg-open`` *starts*, so nobody notices. Here the opener gets the environment the
user's session had, and its exit code is checked.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import webbrowser


log = logging.getLogger(__name__)

# Set by PyInstaller/the AppImage for Ignite itself; meaningless or harmful to host programs.
_BUNDLE_VARS = ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "PYTHONHOME",
                "PYTHONPATH", "SSL_CERT_FILE", "SSL_CERT_DIR", "GIO_MODULE_DIR", "GDK_PIXBUF_MODULE_FILE",
                "GTK_PATH")
_PATH_VARS = ("LD_LIBRARY_PATH", "XDG_DATA_DIRS")


def host_environment(env: dict[str, str] | None = None) -> dict[str, str]:
    """The environment minus what the bundle added, for launching the system's programs."""
    env = dict(os.environ if env is None else env)
    frozen = getattr(sys, "frozen", False) or "_MEIPASS" in vars(sys) or "APPIMAGE" in env
    for name in _PATH_VARS:
        original = env.pop(f"{name}_ORIG", None)  # PyInstaller saves the pre-bundle value here.
        if original is not None:
            if original:
                env[name] = original
            else:
                env.pop(name, None)
        elif frozen and name == "LD_LIBRARY_PATH":
            env.pop(name, None)
    if frozen:
        for name in _BUNDLE_VARS:
            env.pop(name, None)
    return env


def open_url(url: str, timeout: float = 10) -> bool:
    """Open ``url``; True only if something actually took it."""
    if sys.platform.startswith("linux"):
        env = host_environment()
        for command in (["xdg-open", url], ["gio", "open", url]):
            if not shutil.which(command[0], path=env.get("PATH")):
                continue
            try:
                # xdg-open returns once the browser (or the portal) has the link.
                result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.PIPE, timeout=timeout, start_new_session=True)
            except subprocess.TimeoutExpired:
                return True  # Still running: some openers wait for the browser to exit.
            except OSError as error:
                log.warning("Opening the browser with %s failed: %s", command[0], error)
                continue
            if result.returncode == 0:
                return True
            log.warning("%s exited with %s: %s", command[0], result.returncode,
                        result.stderr.decode(errors="replace").strip()[:300])
    try:
        return webbrowser.open(url)
    except webbrowser.Error:
        return False


def restore_host_library_path() -> None:
    """Give every program Ignite starts (Qt's link opener, xdg-open, OBS…) the session's LD_LIBRARY_PATH.

    Safe for Ignite itself: the dynamic loader read the variable at process start and ignores later changes.
    """
    if not sys.platform.startswith("linux"):
        return
    os.environ.pop("LD_LIBRARY_PATH", None)
    os.environ.update({k: v for k, v in host_environment().items() if k == "LD_LIBRARY_PATH"})
    os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
