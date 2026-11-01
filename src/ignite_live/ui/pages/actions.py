"""Actions tab: event → action rules (a gift shows an alert, likes switch a scene, …)."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ignite_live.actions.goals import parse_goals
from ignite_live.games.base import GAMES
from ignite_live.viewers.leaderboard import BOARDS
from ignite_live.actions.rules import (
    ACTIONS,
    CLIP_DELAY_S,
    MAX_CLIP_DELAY_S,
    SPIKE_WINDOW_S,
    MAX_ACTIONS,
    PLACEHOLDERS,
    PLATFORM_KEYS,
    ROLE_NAMES,
    SOUND_NAME,
    TRIGGERS,
    Action,
    Rule,
    Trigger,
    describe_actions,
    describe_trigger,
    new_id,
    parse_action,
    parse_rule,
    parse_rules,
    to_dict,
)
from ignite_live.controller import Controller, action_engine_config
from ignite_live.core.events import PLATFORMS, ConnectionState
from ignite_live.ui.pages.music import read_only_table
from ignite_live.ui.pages.stream import note, scrolling
from ignite_live.ui.theme import state_color
from ignite_live.ui.flow_layout import FlowLayout

TEXT_HINTS = {
    "alert": "Alert text, e.g. {user} sent {gift}!",
    "tts": "What to say, e.g. Thanks {user} for the {gift}",
    "song": "Song to request: {args} (from the command), a link, or song - artist",
}


class ActionRow(QWidget):
    """One action in the rule editor; shows only the fields its kind uses."""

    def __init__(self, controller: Controller, action: Action, on_remove) -> None:
        super().__init__()
        self.controller = controller
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.kind = QComboBox()
        for key, label in ACTIONS.items():
            self.kind.addItem(label, key)
        self.kind.setCurrentIndex(max(0, self.kind.findData(action.kind)))
        self.text = QLineEdit(action.text)
        self.target = QComboBox()
        self.target.setEditable(True)
        self.target.setMinimumWidth(160)
        self.scene = QLineEdit(action.scene)
        self.scene.setPlaceholderText("In scene (empty: current)")
        self.mode = QComboBox()
        for mode in ("show", "hide", "toggle"):
            self.mode.addItem(mode.capitalize(), mode)
        self.mode.setCurrentIndex(max(0, self.mode.findData(action.mode)))
        self.seconds = QDoubleSpinBox()
        self.seconds.setRange(0, 600)
        self.seconds.setDecimals(1)
        self.seconds.setSuffix(" s")
        self.seconds.setSpecialValueText("Keep")
        self.seconds.setToolTip("Undo the change after this long (Keep: leave it)")
        self.seconds.setValue(action.seconds)
        self.game = QComboBox()
        for key, label in GAMES.items():
            self.game.addItem(f"Start {label.lower()}", key)
        self.game.addItem("Stop the running game", "stop")
        self.game.setCurrentIndex(max(0, self.game.findData(action.target)))
        self.amount = QSpinBox()
        self.amount.setRange(-1_000_000, 1_000_000)
        self.amount.setGroupSeparatorShown(True)
        self.amount.setSuffix(" points")
        self.amount.setToolTip("Given to the viewer who triggered the rule (needs points turned on in the Viewers tab)")
        self.amount.setValue(action.amount or 100)
        self.volume = QSpinBox()
        self.volume.setRange(0, 100)
        self.volume.setSuffix(" %")
        self.volume.setValue(action.volume)
        remove = QPushButton("Remove")
        remove.clicked.connect(lambda: on_remove(self))
        # Whichever text field the kind shows takes the spare width.
        for widget in (self.kind, self.text, self.target, self.game, self.scene, self.mode, self.seconds, self.volume,
                       self.amount):
            layout.addWidget(widget, 100 if widget in (self.text, self.target, self.scene) else 0)
        layout.addStretch(1)  # Takes the room when the kind has no text field (e.g. Save clip).
        layout.addWidget(remove)
        self._initial_target = action.target
        self._last_kind = None
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()

    def _kind_changed(self) -> None:
        kind = self.kind.currentData()
        self.text.setVisible(kind in ("alert", "tts", "song"))
        self.text.setPlaceholderText(TEXT_HINTS.get(kind, ""))
        self.target.setVisible(kind in ("sound", "scene", "source"))
        self.scene.setVisible(kind == "source")
        self.mode.setVisible(kind == "source")
        self.seconds.setVisible(kind in ("scene", "source", "clip"))
        clip = kind == "clip"
        self.seconds.setMaximum(MAX_CLIP_DELAY_S if clip else 600)
        self.seconds.setSpecialValueText("Now" if clip else "Keep")
        self.seconds.setPrefix("Wait " if clip else "")
        self.seconds.setToolTip("Wait this long before saving, so the clip includes the reaction" if clip
                                else "Undo the change after this long (Keep: leave it)")
        if clip and self._last_kind not in (None, "clip") and not self.seconds.value():
            self.seconds.setValue(CLIP_DELAY_S)
        self._last_kind = kind
        self.volume.setVisible(kind == "sound")
        self.amount.setVisible(kind == "points")
        self.game.setVisible(kind == "game")
        current = self.target.currentText() or self._initial_target
        self.target.clear()
        if kind == "sound":
            folder = self.controller.sounds_dir
            names = sorted(p.name for p in folder.iterdir() if SOUND_NAME.match(p.name)) if folder.is_dir() else []
            self.target.addItems(names)
            self.target.lineEdit().setPlaceholderText("File in the sounds folder")
        elif kind == "scene":
            studio = self.controller.studio
            self.target.addItems(list(studio.scenes) + (list(studio.vertical.scenes) if studio.vertical else []))
            self.target.lineEdit().setPlaceholderText("Scene name")
        elif kind == "source":
            self.target.lineEdit().setPlaceholderText("Source name")
        self.target.setEditText(current)

    def value(self) -> dict:
        kind = self.kind.currentData()
        target = self.game.currentData() if kind == "game" else self.target.currentText().strip()
        return {"kind": kind, "text": self.text.text(), "target": target,
                "scene": self.scene.text().strip(), "mode": self.mode.currentData(),
                "seconds": self.seconds.value(), "volume": self.volume.value(), "amount": self.amount.value()}


class RuleDialog(QDialog):
    def __init__(self, controller: Controller, rule: Rule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.rule_id = rule.id
        self.setWindowTitle("Edit rule")
        self.resize(860, 560)
        layout = QVBoxLayout(self)

        when = QGroupBox("When")
        form = QFormLayout(when)
        self.name = QLineEdit(rule.name)
        self.enabled = QCheckBox("On")
        self.enabled.setChecked(rule.enabled)
        name_row = QHBoxLayout()
        name_row.addWidget(self.name, 1)
        name_row.addWidget(self.enabled)
        form.addRow("Name", name_row)
        trigger = rule.trigger
        self.kind = QComboBox()
        for key, label in TRIGGERS.items():
            self.kind.addItem(label, key)
        self.kind.setCurrentIndex(max(0, self.kind.findData(trigger.kind)))
        form.addRow("Trigger", self.kind)
        self.gifts = QLineEdit(", ".join(trigger.gifts))
        self.gifts.setPlaceholderText("Any gift. Or names, comma-separated: Rose, Lion")
        self.min_coins = QSpinBox()
        self.min_coins.setRange(0, 10_000_000)
        self.min_coins.setValue(trigger.min_coins)
        self.min_coins.setToolTip("The gift's value × how many were sent at once")
        self.every = QSpinBox()
        self.every.setRange(1, 100_000_000)
        self.every.setValue(trigger.every or 100)
        self.board = QComboBox()
        self.board.addItem("Any leaderboard", "")
        for key, (label, _unit, _counter) in BOARDS.items():
            self.board.addItem(label, key)
        self.board.setCurrentIndex(max(0, self.board.findData(trigger.board)))
        self.game = QComboBox()
        self.game.addItem("Any game", "")
        for key, label in GAMES.items():
            self.game.addItem(label, key)
        self.game.setCurrentIndex(max(0, self.game.findData(trigger.game)))
        self.spike_min = QSpinBox()
        self.spike_min.setRange(3, 10_000)
        self.spike_min.setSuffix(f" in {SPIKE_WINDOW_S} s")
        self.spike_min.setPrefix("At least ")
        self.spike_min.setValue(trigger.every if trigger.kind == "spike" else 15)
        self.spike_min.setToolTip("Comments, gifts and likes together")
        self.factor = QSpinBox()
        self.factor.setRange(2, 20)
        self.factor.setSuffix("× the usual chat")
        self.factor.setValue(trigger.factor)
        self.command = QLineEdit(trigger.command)
        self.command.setPlaceholderText("e.g. hype")
        self.goal = QComboBox()
        self.goal.addItem("Any goal", "")
        for goal in parse_goals(controller.settings.goals):
            self.goal.addItem(goal.name, goal.id)
        if trigger.goal and self.goal.findData(trigger.goal) < 0:
            self.goal.addItem("(deleted goal)", trigger.goal)
        self.goal.setCurrentIndex(max(0, self.goal.findData(trigger.goal)))
        self.role = QComboBox()
        for role in ROLE_NAMES:
            self.role.addItem(role.capitalize(), role)
        self.role.setCurrentIndex(max(0, self.role.findData(trigger.role)))
        self.platforms = {key: QCheckBox(PLATFORMS[key]) for key in PLATFORM_KEYS}
        platform_row = QHBoxLayout()
        for key, box in self.platforms.items():
            box.setChecked(key in trigger.platforms)
            platform_row.addWidget(box)
        platform_row.addStretch(1)
        self.rows = {
            "gifts": ("Gifts", self.gifts),
            "min_coins": ("At least (coins)", self.min_coins),
            "every": ("Every", self.every),
            "command": (f"Command  {controller.settings.command_prefix or '!'}", self.command),
            "goal": ("Goal", self.goal),
            "board": ("Leaderboard", self.board),
            "game": ("Game", self.game),
            "spike_min": ("Chat messages", self.spike_min),
            "factor": ("And", self.factor),
            "role": ("Who", self.role),
        }
        for label, widget in self.rows.values():
            form.addRow(label, widget)
        form.addRow("Platforms", platform_row)
        self.platform_row = platform_row
        self.form = form
        self.cooldown = QSpinBox()
        self.cooldown.setRange(0, 86_400)
        self.cooldown.setSuffix(" s")
        self.cooldown.setValue(rule.cooldown_s)
        self.user_cooldown = QSpinBox()
        self.user_cooldown.setRange(0, 86_400)
        self.user_cooldown.setSuffix(" s")
        self.user_cooldown.setValue(rule.user_cooldown_s)
        cooldowns = QHBoxLayout()
        cooldowns.addWidget(QLabel("Rule"))
        cooldowns.addWidget(self.cooldown)
        cooldowns.addWidget(QLabel("Per viewer"))
        cooldowns.addWidget(self.user_cooldown)
        cooldowns.addStretch(1)
        form.addRow("Cooldown", cooldowns)
        layout.addWidget(when)

        do = QGroupBox("Do")
        do_layout = QVBoxLayout(do)
        self.placeholders = note("")
        do_layout.addWidget(self.placeholders)
        holder = QWidget()
        self.action_list = QVBoxLayout(holder)
        self.action_list.setContentsMargins(0, 0, 0, 0)
        self.action_list.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        do_layout.addWidget(scroll, 1)
        self.add_button = QPushButton("Add Action")
        self.add_button.clicked.connect(lambda: self._add_row(Action()))
        do_layout.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(do, 1)
        self.action_rows: list[ActionRow] = []
        for action in rule.actions:
            self._add_row(action)

        self.error = QLabel()
        self.error.setStyleSheet(f"color: {state_color(ConnectionState.ERROR, self.error).name()};")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()
        self.result_rule: Rule | None = None

    def _kind_changed(self) -> None:
        kind = self.kind.currentData()
        shown = {"gift": {"gifts", "min_coins", "role"}, "like": {"every"}, "viewers": {"every"},
                 "command": {"command", "role"}, "follow": set(), "share": {"role"}, "goal": {"goal"},
                 "spike": {"spike_min", "factor"}, "leader": {"board"},
                 "game_end": {"game"}}[kind]
        for key, (_label, widget) in self.rows.items():
            self.form.setRowVisible(widget, key in shown)
        self.form.setRowVisible(self.platform_row, kind not in ("goal", "spike", "leader", "game_end"))
        self.every.setSuffix(" likes" if kind == "like" else " viewers")
        names = ", ".join(f"{{{name}}}" for name in (*PLACEHOLDERS[kind], "rule"))
        self.placeholders.setText(f"Texts can use {names}. Alerts and sounds play in the Alerts overlay.")

    def _add_row(self, action: Action) -> None:
        row = ActionRow(self.controller, action, self._remove_row)
        self.action_rows.append(row)
        self.action_list.insertWidget(len(self.action_rows) - 1, row)
        self.add_button.setEnabled(len(self.action_rows) < MAX_ACTIONS)

    def _remove_row(self, row: ActionRow) -> None:
        self.action_rows.remove(row)
        row.deleteLater()
        self.add_button.setEnabled(True)

    def _accept(self) -> None:
        kind = self.kind.currentData()
        command = self.command.text().strip().lstrip(self.controller.settings.command_prefix or "!").lower()
        raw = {
            "id": self.rule_id, "name": self.name.text(), "enabled": self.enabled.isChecked(),
            "trigger": {"kind": kind, "gifts": [g.strip() for g in self.gifts.text().split(",") if g.strip()],
                        "min_coins": self.min_coins.value(), "command": command, "factor": self.factor.value(),
                        "every": self.spike_min.value() if kind == "spike" else self.every.value(),
                        "goal": self.goal.currentData(), "board": self.board.currentData(),
                        "game": self.game.currentData(),
                        "role": self.role.currentData(),
                        "platforms": [key for key, box in self.platforms.items() if box.isChecked()]},
            "actions": [row.value() for row in self.action_rows],
            "cooldown_s": self.cooldown.value(), "user_cooldown_s": self.user_cooldown.value(),
        }
        problem = self._problem(raw)
        if problem:
            self.error.setText(problem)
            return
        self.result_rule = parse_rule(raw)
        self.accept()

    def _problem(self, raw: dict) -> str:
        trigger = raw["trigger"]
        if not trigger["platforms"] and trigger["kind"] not in ("goal", "spike", "leader", "game_end"):
            return "Tick at least one platform."
        if trigger["kind"] == "command":
            if not trigger["command"]:
                return "Enter the command's name."
            if trigger["command"] in action_engine_config(self.controller.settings)[2]:
                return f"!{trigger['command']} is a built-in command. Pick another name."
        for number, action in enumerate(raw["actions"], 1):
            if parse_action(action) is None:
                label = ACTIONS[action["kind"]].lower()
                if action["kind"] == "sound":
                    return f"Action {number} ({label}): pick a sound file from the sounds folder."
                if action["kind"] == "points":
                    return f"Action {number} ({label}) needs an amount other than 0."
                return f"Action {number} ({label}) needs a {'name' if action['kind'] != 'tts' else 'text'}."
        if parse_rule(raw) is None:
            return "Check the trigger's settings."
        return ""


def auto_clip_rules() -> list[Rule]:
    clip = Action("clip", seconds=CLIP_DELAY_S)
    return [
        Rule(id=new_id(), name="Big gift → clip", trigger=Trigger("gift", min_coins=1000), actions=(clip,),
             cooldown_s=60),
        Rule(id=new_id(), name="Chat spike → clip", trigger=Trigger("spike", every=15, factor=3), actions=(clip,),
             cooldown_s=60),
    ]


class ActionsPage(QWidget):
    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        layout = scrolling(self)
        layout.addWidget(note(
            "Rules react to your LIVE: a gift shows an alert and plays a sound, every 1,000 likes switches a scene, "
            "!hype from a follower reads a message aloud. Alerts and sounds play in the <b>Alerts</b> overlay "
            "(Overlays tab), so they're in OBS's audio mix. Put sound files (mp3, wav, ogg) in the sounds folder."
        ))
        self.table = read_only_table(["On", "Name", "When", "Do"])
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        for column in (0, 1, 2):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemDoubleClicked.connect(lambda _item: self._edit())
        self.table.itemChanged.connect(self._toggled)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)
        buttons = FlowLayout()  # Wraps in a narrow window rather than cutting off the buttons.
        self.buttons = {}
        for key, text, slot in (("add", "Add Rule…", self._add), ("edit", "Edit…", self._edit),
                                ("copy", "Duplicate", self._duplicate), ("delete", "Delete", self._delete),
                                ("test", "Test", self._test)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
            self.buttons[key] = button
        self.buttons["test"].setToolTip("Run this rule's actions now with sample values (ignores cooldowns)")
        auto_clip = QPushButton("Add Auto-Clip Rules")
        auto_clip.setToolTip("Save a clip on big gifts (1,000+ coins) and chat spikes. Edit or delete them like any rule.")
        auto_clip.clicked.connect(self._add_auto_clip)
        buttons.addWidget(auto_clip)
        play = QPushButton("Play Sound")
        play.setToolTip("Play a file from the sounds folder through the Alerts overlay")
        self.sound_menu = QMenu(play)
        self.sound_menu.aboutToShow.connect(self._fill_sounds)
        play.setMenu(self.sound_menu)
        buttons.addWidget(play)
        folder = QPushButton("Sounds Folder…")
        folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(controller.open_sounds_folder()))))
        buttons.addWidget(folder)
        layout.addLayout(buttons)
        controller.settings_changed.connect(lambda _s: self._refresh())
        self._refresh()

    def _rules(self) -> list[Rule]:
        return parse_rules(self.controller.settings.action_rules)

    def _refresh(self) -> None:
        selected = self._selected()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        prefix = self.controller.settings.command_prefix or "!"
        goal_names = {goal.id: goal.name for goal in parse_goals(self.controller.settings.goals)}
        for row, rule in enumerate(self._rules()):
            self.table.insertRow(row)
            on = QTableWidgetItem()
            on.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            on.setCheckState(Qt.CheckState.Checked if rule.enabled else Qt.CheckState.Unchecked)
            on.setData(Qt.ItemDataRole.UserRole, rule.id)
            self.table.setItem(row, 0, on)
            for column, text in enumerate((rule.name, describe_trigger(rule.trigger, prefix, goal_names),
                                           describe_actions(rule.actions)), 1):
                self.table.setItem(row, column, QTableWidgetItem(text))
            if selected and rule.id == selected.id:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        self._selection_changed()

    def _selected(self) -> Rule | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        rule_id = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return next((rule for rule in self._rules() if rule.id == rule_id), None)

    def _selection_changed(self) -> None:
        has = self._selected() is not None
        for key in ("edit", "copy", "delete", "test"):
            self.buttons[key].setEnabled(has)

    def _save(self, rules: list[Rule]) -> None:
        self.controller.save_settings(replace(self.controller.settings, action_rules=[to_dict(r) for r in rules]))

    def _put(self, rule: Rule) -> None:
        rules = self._rules()
        index = next((i for i, r in enumerate(rules) if r.id == rule.id), None)
        if index is None:
            rules.append(rule)
        else:
            rules[index] = rule
        self._save(rules)

    def _open_editor(self, rule: Rule) -> None:
        dialog = RuleDialog(self.controller, rule, self)
        if dialog.exec() and dialog.result_rule is not None:
            self._put(dialog.result_rule)

    def _add(self) -> None:
        self._open_editor(Rule(id=new_id(), name="New rule", actions=(Action("alert", "{user} sent {gift}!"),)))

    def _edit(self) -> None:
        rule = self._selected()
        if rule is not None:
            self._open_editor(rule)

    def _duplicate(self) -> None:
        rule = self._selected()
        if rule is not None:
            self._put(replace(rule, id=new_id(), name=f"{rule.name} (copy)"[:80]))

    def _delete(self) -> None:
        rule = self._selected()
        if rule is None:
            return
        answer = QMessageBox.question(self, "Delete rule", f"Delete “{rule.name}”?")
        if answer == QMessageBox.StandardButton.Yes:
            self._save([r for r in self._rules() if r.id != rule.id])

    def _test(self) -> None:
        rule = self._selected()
        if rule is not None:
            self.controller.test_rule(rule)

    def _fill_sounds(self) -> None:
        self.sound_menu.clear()
        folder = self.controller.sounds_dir
        names = sorted(p.name for p in folder.iterdir() if SOUND_NAME.match(p.name)) if folder.is_dir() else []
        if not names:
            self.sound_menu.addAction("No sounds yet: add files to the sounds folder").setEnabled(False)
        for name in names:
            self.sound_menu.addAction(name, lambda n=name: self.controller.play_sound(n))

    def _add_auto_clip(self) -> None:
        rules = self._rules()
        names = {rule.name for rule in rules}
        added = [rule for rule in auto_clip_rules() if rule.name not in names]
        if added:
            self._save(rules + added)
        self.controller.notice.emit("info", "Auto-clip rules added: clips are saved 10 s after a big gift or a chat "
                                            "spike. They need OBS's replay buffer running." if added
                                    else "The auto-clip rules are already there.")

    def _toggled(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        rule_id = item.data(Qt.ItemDataRole.UserRole)
        on = item.checkState() == Qt.CheckState.Checked
        self._save([replace(r, enabled=on) if r.id == rule_id else r for r in self._rules()])
