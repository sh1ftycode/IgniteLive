"""Stream → Check Before Going Live: the pre-stream checklist and its Fix buttons."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import Controller
from ignite_live.live.checklist import FAIL, INFO, OK, WARN, Check, counts
from ignite_live.ui.icons import glyph_pixmap
from ignite_live.ui.theme import COLORS

MARKS = {OK: ("check", COLORS["ok"]), INFO: ("info", COLORS["muted"]), WARN: ("warn", COLORS["warn"]),
         FAIL: ("cross", COLORS["error"])}


def run_fix(window, controller: Controller, fix: str) -> None:
    """Carry out a check's fix (see live/checklist.py for the ids)."""
    kind, _, arg = fix.partition(":")
    if kind == "open":
        window.open_page(arg)
    elif kind == "get_key":
        window.actions.get_key.trigger()
    elif kind == "send_key":
        controller.send_key_to_obs()
    elif kind == "unmute":
        controller.set_input_mute(arg, False)
    elif kind == "add_overlay":
        window.open_add_overlays([arg])
    elif kind == "start_replay":
        controller.start_clip_buffer()
    elif kind == "replay_45":
        controller.set_replay_seconds(45)


class ChecklistDialog(QDialog):
    def __init__(self, controller: Controller, window, going_live: bool = False, checks: list[Check] | None = None):
        super().__init__(window)
        self.controller, self.window_, self.going_live = controller, window, going_live
        self.setWindowTitle("Ready to go live?")
        self.resize(620, 520)
        layout = QVBoxLayout(self)
        self.heading = QLabel("Checking…")
        self.heading.setStyleSheet("font-size: 15px; font-weight: 600")
        layout.addWidget(self.heading)
        holder = QWidget()
        self.grid = QGridLayout(holder)
        self.grid.setColumnStretch(1, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        layout.addWidget(scroll, 1)
        self.buttons = QDialogButtonBox()
        self.again = self.buttons.addButton("Check Again", QDialogButtonBox.ButtonRole.ResetRole)
        self.again.clicked.connect(self.refresh)
        if going_live:
            self.go = self.buttons.addButton("Go Live", QDialogButtonBox.ButtonRole.AcceptRole)
            self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        else:
            self.go = None
            self.buttons.addButton(QDialogButtonBox.StandardButton.Close)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        if checks is None:
            self.refresh()
        else:
            self.show_checks(checks)

    def refresh(self) -> None:
        self.heading.setText("Checking…")
        self.again.setEnabled(False)
        self.controller.run_checklist(self.show_checks)

    def show_checks(self, checks: list[Check]) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        order = {FAIL: 0, WARN: 1, INFO: 2, OK: 3}
        for row, check in enumerate(sorted(checks, key=lambda c: order[c.status])):
            mark, color = MARKS[check.status]
            icon = QLabel()
            icon.setPixmap(glyph_pixmap(mark, color, 18))
            self.grid.addWidget(icon, row, 0)
            text = QLabel(f"<b>{_escape(check.title)}</b>" + (f"<br>{_escape(check.detail)}" if check.detail else ""))
            text.setWordWrap(True)
            self.grid.addWidget(text, row, 1)
            if check.fix:
                fix = QPushButton(check.fix_label or "Fix")
                fix.clicked.connect(lambda _=False, f=check.fix: self._fix(f))
                self.grid.addWidget(fix, row, 2)
        self.grid.setRowStretch(len(checks), 1)
        fails, warns = counts(checks)
        if fails:
            self.heading.setText(f"{fails} thing{'s' if fails != 1 else ''} will stop the stream from working")
        elif warns:
            self.heading.setText(f"Ready, with {warns} thing{'s' if warns != 1 else ''} to look at")
        else:
            self.heading.setText("Everything's ready")
        if self.go is not None:
            self.go.setText("Go Live Anyway" if fails else "Go Live")
        self.again.setEnabled(True)
        self.checks = checks

    def _fix(self, fix: str) -> None:
        run_fix(self.window_, self.controller, fix)
        if not fix.startswith(("open:", "add_overlay:")):
            QTimer.singleShot(1500, self.refresh)  # Give OBS a moment, then show the result.


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def go_live(window, controller: Controller) -> None:
    """Start streaming after the checklist: straight away if nothing fails, otherwise ask."""

    def checked(checks: list[Check]) -> None:
        fails, warns = counts(checks)
        if fails:
            dialog = ChecklistDialog(controller, window, going_live=True, checks=checks)
            if not dialog.exec():
                return
        controller.set_obs_streaming(True)
        if warns and not fails:
            controller.notice.emit("info", f"Going live with {warns} warning{'s' if warns != 1 else ''}: "
                                           "Stream → Check Before Going Live shows them.")

    controller.run_checklist(checked)
