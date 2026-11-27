"""Viewers & Points (on the Live Chat screen): loyalty points (how they're earned and spent) and everyone who's been in chat."""

from __future__ import annotations

import time
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ignite_live.actions.rules import SOUND_NAME
from ignite_live.controller import Controller, command_config
from ignite_live.core.events import PLATFORMS
from ignite_live.music.commands import CommandKind
from ignite_live.ui.kit import Card
from ignite_live.ui.pages.music import read_only_table
from ignite_live.ui.pages.stream import note
from ignite_live.ui.theme import theme_changed
from ignite_live.viewers.points import ACTIVE_EVERY_MIN, ACTIVE_WINDOW_MIN, CHAT_COOLDOWN_S, config_to_dict, parse_config


def _spin(value: int, high: int, suffix: str = "", special: str = "") -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(0, high)
    spin.setGroupSeparatorShown(True)
    spin.setValue(value)
    spin.setSuffix(suffix)
    if special:
        spin.setSpecialValueText(special)
    return spin


class ViewersPage(QWidget):
    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 16, 24, 24)
        self.split = split = QSplitter()  # Side by side, or stacked when the window is too narrow for both.
        split.setHandleWidth(16)
        split.setStyleSheet("QSplitter::handle { background: transparent; }")
        box.addWidget(split, 1)

        # Points settings.
        settings = QWidget()
        side = QVBoxLayout(settings)
        side.setContentsMargins(0, 0, 0, 0)
        self.points_box = box = Card("Loyalty points", icon="star", primary=True)
        form = QFormLayout()
        box.body.addLayout(form)
        c = parse_config(controller.settings.points)
        self.enabled = QCheckBox("Viewers earn and spend points")
        self.enabled.setChecked(c.enabled)
        form.addRow(self.enabled)
        self.currency = QLineEdit(c.currency)
        self.currency.setPlaceholderText("points")
        form.addRow("Name", self.currency)
        form.addRow(note("<b>Earning</b>"))
        self.active = _spin(c.active, 100_000, f" every {ACTIVE_EVERY_MIN} min")
        self.active.setToolTip(f"For viewers who commented, liked or gifted in the last {ACTIVE_WINDOW_MIN} minutes. "
                               "TikTok doesn't say who watches silently.")
        self.chat = _spin(c.chat, 100_000, " per message")
        self.chat.setToolTip(f"At most once every {CHAT_COOLDOWN_S} s per viewer")
        self.per_coin = _spin(c.per_coin, 100_000, " per coin gifted")
        self.follow = _spin(c.follow, 1_000_000, " for following")
        self.share = _spin(c.share, 1_000_000, " for sharing")
        for label, widget in (("Being around", self.active), ("Chatting", self.chat), ("Gifts", self.per_coin),
                              ("Follow", self.follow), ("Share", self.share)):
            form.addRow(label, widget)
        form.addRow(note("Follow and share bonuses are once per stream."))
        form.addRow(note("<b>Spending</b> (free when 0)"))
        prefix = command_config(controller.settings).prefix
        names = {kind: (controller.settings.command_names.get(kind.value) or kind.value).split(",")[0].strip()
                 for kind in (CommandKind.BUMP, CommandKind.SAY, CommandKind.SOUND)}
        self.cost_request = _spin(c.cost_request, 10_000_000, special="Free")
        self.cost_bump = _spin(c.cost_bump, 10_000_000, special="Free")
        self.cost_say = _spin(c.cost_say, 10_000_000, special="Free")
        self.cost_sound = _spin(c.cost_sound, 10_000_000, special="Free")
        for label, widget in (("Song request", self.cost_request),
                              (f"{prefix}{names[CommandKind.BUMP]} (play my song next)", self.cost_bump),
                              (f"{prefix}{names[CommandKind.SAY]} (read my message)", self.cost_say),
                              (f"{prefix}{names[CommandKind.SOUND]} (any sound)", self.cost_sound)):
            form.addRow(label, widget)
        self.sounds = QTableWidget(0, 2)
        self.sounds.setHorizontalHeaderLabels(["Sound", "Own price"])
        self.sounds.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.sounds.verticalHeader().setVisible(False)
        self.sounds.setMinimumHeight(110)
        form.addRow(self.sounds)
        form.addRow(note(f"Viewers also get {prefix}points to see their balance. Moderators can "
                         f"{prefix}give @name 100 (or −100)."))
        side.addWidget(box)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        test = QPushButton("Test")
        test.setToolTip("Shows a balance and a “not enough points” message on the overlays")
        test.clicked.connect(controller.test_points)
        apply = QPushButton("Apply")
        apply.clicked.connect(self._apply)
        buttons.addWidget(test)
        buttons.addWidget(apply)
        side.addLayout(buttons)
        side.addStretch(1)
        split.addWidget(settings)

        # Viewers.
        self.people_box = people = Card("Viewers", icon="people")
        right = people.body
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search viewers")
        self.search.textChanged.connect(lambda _text: self.refresh())
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        search_row.addWidget(self.search, 1)
        search_row.addWidget(refresh)
        right.addLayout(search_row)
        self.table = read_only_table(["Name", "Platform", "Points", "Messages", "Coins", "Likes", "Minutes", "Last seen"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(2, Qt.SortOrder.DescendingOrder)  # Most points first.
        self.table.itemSelectionChanged.connect(self._selection_changed)
        right.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.set_points = QPushButton("Set Points…")
        self.set_points.clicked.connect(self._set_points)
        reset = QPushButton("Reset Everyone's Points…")
        reset.clicked.connect(self._reset)
        self.block = QPushButton("Block")
        self.block.setToolTip("Ignite ignores their messages: no text to speech, commands, requests or points")
        self.block.clicked.connect(self._toggle_block)
        # As the streamer, in their TikTok LIVE (TikTok signed in on the Ignite server's website).
        self.tiktok = QPushButton("TikTok")
        self.tiktok.setToolTip("Make them a moderator, mute them, or kick them out of your LIVE. Needs TikTok signed in "
                               "on your Ignite server's website (File → Preferences → Chat platforms)")
        menu = QMenu(self.tiktok)
        for label, action, duration in (("Make Moderator", "mod", 0), ("Remove Moderator", "unmod", 0), (None, "", 0),
                                        ("Mute for 30 Seconds", "mute", 30), ("Mute for 5 Minutes", "mute", 300),
                                        ("Mute for This LIVE", "mute", -1), ("Unmute", "unmute", 0), (None, "", 0),
                                        ("Kick from LIVE…", "kick", 0), ("Let Back In", "unkick", 0)):
            if label is None:
                menu.addSeparator()
                continue
            menu.addAction(label).triggered.connect(
                lambda _checked=False, action=action, duration=duration: self._tiktok(action, duration))
        self.tiktok.setMenu(menu)
        # Only when it works: TikTok signed in on the server, and the server's Euler plan includes moderation.
        self.tiktok.setVisible(bool(controller.tiktok_account.get("moderation")))
        controller.tiktok_account_changed.connect(lambda status: self.tiktok.setVisible(bool(status.get("moderation"))))
        row.addWidget(self.set_points)
        row.addWidget(self.block)
        row.addWidget(self.tiktok)
        row.addStretch(1)
        row.addWidget(reset)
        right.addLayout(row)
        right.addWidget(note("Everyone who's commented, liked, or gifted, with all-time totals. Kept on this computer "
                             "only. Simulated events never count."))
        split.addWidget(people)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        self._fill_sounds(c)
        self.refresh()
        theme_changed().connect(self._restyle)
        self._restyle()

    def _restyle(self, _palette=None) -> None:
        self.points_box.restyle()
        self.people_box.restyle()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        side_by_side = sum(self.split.widget(i).minimumSizeHint().width() for i in range(self.split.count())) + 32
        wanted = Qt.Orientation.Horizontal if self.width() >= side_by_side else Qt.Orientation.Vertical
        if self.split.orientation() != wanted:
            self.split.setOrientation(wanted)

    def _fill_sounds(self, c) -> None:
        folder = self.controller.sounds_dir
        names = sorted(p.name for p in folder.iterdir() if SOUND_NAME.match(p.name)) if folder.is_dir() else []
        self.sounds.setRowCount(len(names))
        for row, name in enumerate(names):
            item = QTableWidgetItem(name)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.sounds.setItem(row, 0, item)
            self.sounds.setCellWidget(row, 1, _spin(c.sound_prices.get(name, 0), 10_000_000, special="Default"))

    def _apply(self) -> None:
        prices = {}
        for row in range(self.sounds.rowCount()):
            price = self.sounds.cellWidget(row, 1).value()
            if price:
                prices[self.sounds.item(row, 0).text()] = price
        config = config_to_dict(parse_config({
            "enabled": self.enabled.isChecked(), "currency": self.currency.text(), "active": self.active.value(),
            "chat": self.chat.value(), "per_coin": self.per_coin.value(), "follow": self.follow.value(),
            "share": self.share.value(), "cost_request": self.cost_request.value(),
            "cost_bump": self.cost_bump.value(), "cost_say": self.cost_say.value(),
            "cost_sound": self.cost_sound.value(), "sound_prices": prices,
        }))
        self.controller.save_settings(replace(self.controller.settings, points=config))
        self.controller.notice.emit("info", "Points settings saved." if config["enabled"] else
                                    "Points settings saved. Points are off: viewers don't earn or spend any.")

    def refresh(self) -> None:
        viewers = self.controller.viewer_list(self.search.text())
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(viewers))
        for row, v in enumerate(viewers):
            name = QTableWidgetItem(v.name)
            name.setData(Qt.ItemDataRole.UserRole, v.key)
            values = [name, QTableWidgetItem(PLATFORMS.get(v.platform, v.platform))]
            for number in (v.points, v.messages, v.coins, v.likes, v.minutes):
                item = QTableWidgetItem()
                item.setData(Qt.ItemDataRole.DisplayRole, number)  # Sorts as a number.
                values.append(item)
            values.append(QTableWidgetItem(time.strftime("%Y-%m-%d %H:%M", time.localtime(v.last_seen))))
            for column, item in enumerate(values):
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self._selection_changed()

    def _selected(self) -> tuple[str, str, int] | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        row = rows[0].row()
        name = self.table.item(row, 0)
        return name.data(Qt.ItemDataRole.UserRole), name.text(), int(self.table.item(row, 2).data(Qt.ItemDataRole.DisplayRole))

    def _selection_changed(self) -> None:
        selected = self._selected()
        self.set_points.setEnabled(selected is not None)
        self.block.setEnabled(selected is not None)
        self.block.setText("Unblock" if selected and self._is_blocked(selected[0]) else "Block")
        self.tiktok.setEnabled(selected is not None and selected[0].lower().startswith("tiktok:"))

    def _is_blocked(self, key: str) -> bool:
        return key.lower() in {entry.lower() for entry in self.controller.settings.blocked_users}

    def _toggle_block(self) -> None:
        selected = self._selected()
        if selected is not None:
            self.controller.block_viewer(selected[0], not self._is_blocked(selected[0]))
            self._selection_changed()

    def _tiktok(self, action: str, duration: int) -> None:
        selected = self._selected()
        if selected is None:
            return
        key, name, _points = selected
        if action == "kick":
            answer = QMessageBox.question(self, "Kick from LIVE", f"Kick {name} out of your TikTok LIVE? They can't "
                                          "come back in until you let them (TikTok button → Let Back In).")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.controller.tiktok_moderate(key, action, duration if duration else 300)

    def _set_points(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        key, name, points = selected
        value, ok = QInputDialog.getInt(self, "Set points", f"Points for {name}:", points, 0, 1_000_000_000)
        if ok:
            self.controller.set_viewer_points(key, value)
            self.refresh()

    def _reset(self) -> None:
        if QMessageBox.question(self, "Reset points", "Set everyone's points to 0? This can't be undone.") \
                == QMessageBox.StandardButton.Yes:
            self.controller.reset_all_points()
            self.refresh()
