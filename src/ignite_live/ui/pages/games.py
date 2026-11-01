"""Games tab: tug-of-war, boss fight and chat poll. One runs at a time, shown on the Game overlay."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import TEST_GAME_SECONDS, Controller
from ignite_live.games.base import GAMES, IMAGE_NAME
from ignite_live.games.manager import parse_games
from ignite_live.ui.pages.stream import note, scrolling


def _spin(value: int, low: int, high: int, suffix: str = "", special: str = "") -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(low, high)
    spin.setGroupSeparatorShown(True)
    spin.setValue(value)
    spin.setSuffix(suffix)
    if special:
        spin.setSpecialValueText(special)
    return spin


def _list(values: list[str]) -> str:
    return ", ".join(values)


def _split(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


class GamesPage(QWidget):
    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        layout = scrolling(self)
        status = QHBoxLayout()
        self.status = QLabel()
        self.status.setStyleSheet("font-weight: 600")
        self.stop = QPushButton("Stop Game")
        self.stop.clicked.connect(controller.stop_game)
        status.addWidget(self.status, 1)
        status.addWidget(self.stop)
        layout.addLayout(status)
        layout.addWidget(note("One game at a time. Show it with the <b>Game</b> overlay (Overlays tab). Rules can start "
                              "a game (“Start or stop a game”) and celebrate the winner (“Game ended”). Test runs a game "
                              f"for {TEST_GAME_SECONDS} s with pretend players: nothing counts for points or reports."))
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        config = parse_games(controller.settings.games)
        self.tabs.addTab(self._tug(config["tug"]), GAMES["tug"])
        self.tabs.addTab(self._boss(config["boss"]), GAMES["boss"])
        self.tabs.addTab(self._poll(config["poll"]), GAMES["poll"])
        controller.game_changed.connect(self._game_changed)
        self._game_changed(controller.games.snapshot())

    # Forms.

    def _buttons(self, kind: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch(1)
        for text, slot in (("Save", self.save), ("Test", lambda: self._run(kind, test=True)),
                           ("Start", lambda: self._run(kind))):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        return row

    def _page(self, form: QFormLayout, kind: str) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.addLayout(form)
        outer.addStretch(1)
        outer.addLayout(self._buttons(kind))
        return page

    def _tug(self, c: dict) -> QWidget:
        form = QFormLayout()
        self.left_name, self.left_color = QLineEdit(c["left_name"]), QLineEdit(c["left_color"])
        self.right_name, self.right_color = QLineEdit(c["right_name"]), QLineEdit(c["right_color"])
        for edit in (self.left_color, self.right_color):
            edit.setPlaceholderText("#rrggbb")
            edit.setMaximumWidth(110)
        for label, name, color in (("Left team", self.left_name, self.left_color),
                                   ("Right team", self.right_name, self.right_color)):
            row = QHBoxLayout()
            row.addWidget(name, 1)
            row.addWidget(color)
            form.addRow(label, row)
        self.tug_mode = QComboBox()
        self.tug_mode.addItem("Each team has its own gifts", "gifts")
        self.tug_mode.addItem("Viewers join a team; any gift pulls for it", "join")
        self.tug_mode.setCurrentIndex(max(0, self.tug_mode.findData(c["mode"])))
        form.addRow("Teams", self.tug_mode)
        self.left_gifts, self.right_gifts = QLineEdit(_list(c["left_gifts"])), QLineEdit(_list(c["right_gifts"]))
        for edit in (self.left_gifts, self.right_gifts):
            edit.setPlaceholderText("Gift names, comma-separated")
        form.addRow("Left team's gifts", self.left_gifts)
        form.addRow("Right team's gifts", self.right_gifts)
        self.like_value = _spin(c["like_value"], 0, 1000, " likes = 1 pull", "Likes don't pull")
        form.addRow("Likes", self.like_value)
        self.tug_target = _spin(c["target"], 10, 100_000_000, " ahead to win")
        form.addRow("Win by", self.tug_target)
        self.tug_seconds = _spin(c["seconds"], 0, 3600, " s", "No time limit")
        form.addRow("Time limit", self.tug_seconds)
        form.addRow(note("Gifts pull by their coin value. With a time limit, whoever's ahead when it runs out wins. "
                         "In join mode, viewers type !join and a team's name (or 1 or 2)."))
        self.tug_form = form

        def mode_changed() -> None:
            gifts = self.tug_mode.currentData() == "gifts"
            form.setRowVisible(self.left_gifts, gifts)
            form.setRowVisible(self.right_gifts, gifts)
            form.setRowVisible(self.like_value, not gifts)

        self.tug_mode.currentIndexChanged.connect(mode_changed)
        mode_changed()
        return self._page(form, "tug")

    def _boss(self, c: dict) -> QWidget:
        form = QFormLayout()
        self.boss_name = QLineEdit(c["name"])
        form.addRow("Name", self.boss_name)
        self.boss_image = QComboBox()
        self.boss_image.setEditable(True)
        self.boss_image.lineEdit().setPlaceholderText("A picture from the images folder, or a TikTok image link")
        folder = self.controller.images_dir
        self.boss_image.addItem("")
        if folder.is_dir():
            self.boss_image.addItems(sorted(p.name for p in folder.iterdir() if IMAGE_NAME.match(p.name)))
        self.boss_image.setEditText(c["image"])
        images = QPushButton("Images Folder…")
        images.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.controller.open_images_folder()))))
        row = QHBoxLayout()
        row.addWidget(self.boss_image, 1)
        row.addWidget(images)
        form.addRow("Picture", row)
        self.boss_hp = _spin(c["hp"], 10, 100_000_000, " HP")
        form.addRow("Health", self.boss_hp)
        self.boss_growth = _spin(c["growth"], 0, 1000, " % more", "Same")
        form.addRow("Each next boss", self.boss_growth)
        self.like_damage = _spin(c["like_damage"], 0, 1000, " damage per like", "Likes do nothing")
        self.coin_damage = _spin(c["coin_damage"], 0, 100_000, " damage per coin", "Gifts do nothing")
        self.comment_damage = _spin(c["comment_damage"], 0, 1000, " damage per comment", "Comments do nothing")
        form.addRow("Likes", self.like_damage)
        form.addRow("Gifts", self.coin_damage)
        form.addRow("Comments", self.comment_damage)
        self.boss_seconds = _spin(c["seconds"], 0, 3600, " s", "Until I stop it")
        form.addRow("Time limit", self.boss_seconds)
        form.addRow(note("When a boss is defeated, chat wins the round and a stronger one appears straight away. "
                         "Pictures: png, jpg, gif or webp."))
        return self._page(form, "boss")

    def _poll(self, c: dict) -> QWidget:
        form = QFormLayout()
        self.question = QLineEdit(c["question"])
        form.addRow("Question", self.question)
        self.options = QPlainTextEdit("\n".join(c["options"]))
        self.options.setPlaceholderText("One option per line (2 to 6)")
        self.options.setMaximumHeight(130)
        form.addRow("Options", self.options)
        self.poll_seconds = _spin(c["seconds"], 0, 3600, " s", "Until I stop it")
        form.addRow("Time limit", self.poll_seconds)
        form.addRow(note("Viewers vote by typing the option's number, !vote and the number, or the option itself. "
                         "One vote each; voting again changes it."))
        return self._page(form, "poll")

    # Saving and running.

    def values(self) -> dict:
        return parse_games({
            "tug": {"left_name": self.left_name.text(), "left_color": self.left_color.text().strip(),
                    "right_name": self.right_name.text(), "right_color": self.right_color.text().strip(),
                    "mode": self.tug_mode.currentData(), "left_gifts": _split(self.left_gifts.text()),
                    "right_gifts": _split(self.right_gifts.text()), "like_value": self.like_value.value(),
                    "target": self.tug_target.value(), "seconds": self.tug_seconds.value()},
            "boss": {"name": self.boss_name.text(), "image": self.boss_image.currentText().strip(),
                     "hp": self.boss_hp.value(), "growth": self.boss_growth.value(),
                     "like_damage": self.like_damage.value(), "coin_damage": self.coin_damage.value(),
                     "comment_damage": self.comment_damage.value(), "seconds": self.boss_seconds.value()},
            "poll": {"question": self.question.text(),
                     "options": [line.strip() for line in self.options.toPlainText().splitlines() if line.strip()],
                     "seconds": self.poll_seconds.value()},
        })

    def save(self) -> None:
        games = self.values()
        if games != self.controller.settings.games:
            self.controller.save_settings(replace(self.controller.settings, games=games))

    def _run(self, kind: str, test: bool = False) -> None:
        self.save()
        if test:
            self.controller.test_game(kind)
        else:
            self.controller.start_game(kind)

    def _game_changed(self, snapshot: dict | None) -> None:
        if snapshot is None:
            self.status.setText("No game running")
            self.stop.setEnabled(False)
            return
        self.stop.setEnabled(True)
        kind = snapshot["kind"]
        detail = ""
        if kind == "tug":
            detail = f"{snapshot['left']['name']} {snapshot['left']['score']:,} – {snapshot['right']['score']:,} " \
                     f"{snapshot['right']['name']}"
        elif kind == "boss":
            detail = f"Level {snapshot['level']}: {snapshot['hp']:,} / {snapshot['max_hp']:,} HP"
        elif kind == "poll":
            detail = f"{snapshot['total']} vote{'s' if snapshot['total'] != 1 else ''}"
        remaining = snapshot.get("remaining")
        clock = f" · {remaining // 60}:{remaining % 60:02d} left" if remaining is not None else ""
        self.status.setText(f"Running: {GAMES[kind]} · {detail}{clock}")
