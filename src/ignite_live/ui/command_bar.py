"""The command bar (Ctrl+K): type to find a screen or an action, Enter to run it.

Every entry is something the app already does (open a screen, panic, skip a song, switch an OBS scene, change the
theme), so nothing here acts on its own: it only finds things faster. ``build_commands`` is plain data so it can be
tested without a window.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFrame, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from ignite_live.ui import lucide
from ignite_live.ui.theme import THEMES, colors

MAX_SHOWN = 8


@dataclass(frozen=True)
class Command:
    title: str
    group: str  # "Go to", "Actions", "Scenes" or "Theme".
    run: Callable[[], None]
    icon: str = "chevron-right"
    hint: str = ""


def matches(command: Command, query: str) -> bool:
    """Every word of the query appears in the title or group, in any order."""
    haystack = f"{command.title} {command.group}".lower()
    return all(word in haystack for word in query.lower().split())


def filter_commands(commands: list[Command], query: str) -> list[Command]:
    found = [c for c in commands if matches(c, query)]
    # Titles that start with the query come first, then the rest in the original order.
    q = query.strip().lower()
    return sorted(found, key=lambda c: 0 if q and c.title.lower().startswith(q) else 1)[:MAX_SHOWN]


def build_commands(window) -> list[Command]:
    """The entries for the current state of ``window`` (a MainWindow)."""
    from ignite_live.ui.main_window import SCREENS

    controller = window.controller
    out: list[Command] = []
    for number, (key, title, icon, _text) in enumerate(SCREENS, 1):
        out.append(Command(title, "Go to", lambda k=key: window.open_page(k), icon, f"Ctrl {number}"))
    if controller.panicking:
        out.append(Command("Resume after panic", "Actions", controller.resume, "shield", "Ctrl Shift P"))
    else:
        out.append(Command("Panic: mute, hold alerts, pause music", "Actions", controller.panic, "shield",
                           "Ctrl Shift P"))
    out.append(Command("Skip the current song", "Actions", controller.skip_song, "skip-forward"))
    out.append(Command("Check before going live", "Actions", window.actions.check_stream.trigger, "circle-check"))
    out.append(Command("Open the setup guide", "Actions", window.open_setup_guide, "sparkles", "F1"))
    out.append(Command("Open preferences", "Actions", lambda: window.open_preferences(), "settings", "Ctrl ,"))
    for scene in list(getattr(controller.studio, "scenes", []) or []):
        out.append(Command(f"Switch scene to {scene}", "Scenes", lambda s=scene: controller.switch_scene(s),
                           "layers"))
    for key, name in THEMES.items():
        out.append(Command(f"Theme: {name}", "Theme", lambda k=key: _set_theme(controller, k), "palette"))
    return out


def _set_theme(controller, key: str) -> None:
    controller.save_settings(replace(controller.settings, ui_theme=key))


class CommandBar(QDialog):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.window_ = window
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)
        self.setFixedWidth(560)
        self.commands = build_commands(window)
        p = colors()
        frame = QFrame(self)
        frame.setObjectName("commandBarFrame")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        column = QVBoxLayout(frame)
        column.setContentsMargins(10, 10, 10, 10)
        column.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Go to a screen, or do something…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refresh)
        self.search.returnPressed.connect(self._run_current)
        self.list = QListWidget()
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.itemActivated.connect(lambda _item: self._run_current())
        self.list.itemClicked.connect(lambda _item: self._run_current())
        self.note = QLabel("Ignite never sends or changes anything until you press Enter.")
        self.note.setProperty("role", "muted")
        column.addWidget(self.search)
        column.addWidget(self.list)
        column.addWidget(self.note)
        frame.setStyleSheet(
            f"QFrame#commandBarFrame {{ background: {p.panel}; border: 1px solid {p.divider}; border-radius: 12px; }}"
            f"QListWidget {{ background: transparent; outline: none; }}"
            f"QListWidget::item {{ padding: 8px 10px; border-radius: 8px; }}"
            f"QListWidget::item:selected {{ background: {p.raised}; color: {p.text}; }}")
        self._refresh("")

    def _refresh(self, query: str) -> None:
        p = colors()
        self.shown = filter_commands(self.commands, query)
        self.list.clear()
        for command in self.shown:
            suffix = f"    {command.hint}" if command.hint else ""
            item = QListWidgetItem(lucide.icon(command.icon, p.accent, 16), f"{command.title}{suffix}")
            item.setToolTip(command.group)
            self.list.addItem(item)
        if self.shown:
            self.list.setCurrentRow(0)
        self.list.setFixedHeight(max(1, len(self.shown)) * 42 + 8)
        self.adjustSize()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        key = event.key()
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self.shown:
            step = 1 if key == Qt.Key.Key_Down else -1
            self.list.setCurrentRow((self.list.currentRow() + step) % len(self.shown))
            return
        super().keyPressEvent(event)

    def _run_current(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.shown):
            command = self.shown[row]
            self.accept()
            command.run()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        host = self.window_.geometry()
        self.move(host.center().x() - self.width() // 2, host.top() + 110)
        self.search.setFocus()


def open_command_bar(window) -> None:
    CommandBar(window).exec()
