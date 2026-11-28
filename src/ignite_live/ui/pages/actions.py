"""Alerts: event → action rules (a gift shows an alert, likes switch a scene, …), their history and sounds."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
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
    QStackedWidget,
    QTableWidgetItem,
    QTabWidget,
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
    MAX_RULES,
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
from ignite_live.core.events import PLATFORMS, ChatFollow, ChatGift, ChatShare, ConnectionState
from ignite_live.ui.flow_layout import FlowLayout
from ignite_live.ui.kit import (
    Card,
    EmptyState,
    EventRow,
    PageContainer,
    StatusIndicator,
    button,
    hline,
    icon_label,
    label,
    restyle_buttons,
)
from ignite_live.ui.pages.music import read_only_table
from ignite_live.ui.pages.stream import note
from ignite_live.ui.simulate import sample_events
from ignite_live.ui.theme import colors, state_color, theme_changed

MAX_HISTORY = 300

TEXT_HINTS = {
    "alert": "Alert text, e.g. {user} sent {gift}!",
    "tts": "What to say, e.g. Thanks {user} for the {gift}",
    "song": "Song to request: {args} (from the command), a link, or song - artist",
}
MINECRAFT_HINTS = {
    "command": "Console command, e.g. ttinteractive gift {each} {user}",
    "broadcast": "Message for everyone in the game, e.g. {user} sent {gift}!",
}


class ActionRow(QWidget):
    """One action in the rule editor; shows only the fields its kind uses."""

    def __init__(self, controller: Controller, action: Action, on_remove) -> None:
        super().__init__()
        self.controller = controller
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.kind = QComboBox()
        for key, name in ACTIONS.items():
            self.kind.addItem(name, key)
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
        for key, name in GAMES.items():
            self.game.addItem(f"Start {name.lower()}", key)
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
        # Minecraft (ServerTap): a console command or a broadcast; a command can run once per gift in a combo.
        self.mc_mode = QComboBox()
        self.mc_mode.addItem("Run command", "command")
        self.mc_mode.addItem("Broadcast message", "broadcast")
        self.mc_mode.setCurrentIndex(max(0, self.mc_mode.findData(action.mode if action.kind == "minecraft"
                                                                  else "command")))
        self.mc_mode.currentIndexChanged.connect(self._kind_changed)
        self.repeat = QCheckBox("Once per gift")
        self.repeat.setToolTip("A combo of 5 Roses runs it 5 times (at most 50). Use {each} for one gift's coins")
        self.repeat.setChecked(action.repeat == "per_gift")
        remove = QPushButton("Remove")
        remove.clicked.connect(lambda: on_remove(self))
        # Whichever text field the kind shows takes the spare width.
        for widget in (self.kind, self.mc_mode, self.text, self.target, self.game, self.scene, self.mode, self.seconds,
                       self.volume, self.amount, self.repeat):
            layout.addWidget(widget, 100 if widget in (self.text, self.target, self.scene) else 0)
        layout.addStretch(1)  # Takes the room when the kind has no text field (e.g. Save clip).
        layout.addWidget(remove)
        self._initial_target = action.target
        self._last_kind = None
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()

    def _kind_changed(self) -> None:
        kind = self.kind.currentData()
        minecraft = kind == "minecraft"
        self.text.setVisible(kind in ("alert", "tts", "song", "minecraft"))
        self.text.setPlaceholderText(MINECRAFT_HINTS[self.mc_mode.currentData()] if minecraft
                                     else TEXT_HINTS.get(kind, ""))
        self.mc_mode.setVisible(minecraft)
        self.repeat.setVisible(minecraft and self.mc_mode.currentData() == "command")
        self.target.setVisible(kind in ("sound", "scene", "source"))
        self.scene.setVisible(kind == "source")
        self.mode.setVisible(kind == "source")
        self.seconds.setVisible(kind in ("scene", "source", "clip", "wait", "alert"))
        clip, wait, alert = kind == "clip", kind == "wait", kind == "alert"
        self.seconds.setMaximum(MAX_CLIP_DELAY_S if clip else 60 if alert else 600)
        self.seconds.setMinimum(0.1 if wait else 0)
        self.seconds.setSpecialValueText("Now" if clip else "Overlay's time" if alert else "" if wait else "Keep")
        self.seconds.setPrefix("Wait " if clip or wait else "On screen " if alert else "")
        self.seconds.setToolTip("Wait this long before saving, so the clip includes the reaction" if clip
                                else "The actions after this one wait this long" if wait
                                else "How long this alert stays (the overlay's own setting: 5 s unless changed)"
                                if alert else "Undo the change after this long (Keep: leave it)")
        if wait and not self.seconds.value():
            self.seconds.setValue(2)
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
                "scene": self.scene.text().strip(),
                "mode": self.mc_mode.currentData() if kind == "minecraft" else self.mode.currentData(),
                "seconds": self.seconds.value(), "volume": self.volume.value(), "amount": self.amount.value(),
                "repeat": "per_gift" if self.repeat.isChecked() else "once"}


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
        for key, name in TRIGGERS.items():
            self.kind.addItem(name, key)
        self.kind.setCurrentIndex(max(0, self.kind.findData(trigger.kind)))
        form.addRow("Trigger", self.kind)
        self.gifts = QLineEdit(", ".join(trigger.gifts))
        self.gifts.setPlaceholderText("Any gift. Or names, comma-separated: Rose, Lion")
        self.min_coins = QSpinBox()
        self.min_coins.setRange(0, 10_000_000)
        self.min_coins.setValue(trigger.min_coins)
        self.min_coins.setToolTip("The gift's value × how many were sent at once")
        self.max_coins = QSpinBox()
        self.max_coins.setRange(0, 10_000_000)
        self.max_coins.setSpecialValueText("No limit")
        self.max_coins.setValue(trigger.max_coins)
        self.max_coins.setToolTip("For tiers that don't overlap: e.g. 1–98 coins spawns chickens, 99–498 zombies")
        self.every = QSpinBox()
        self.every.setRange(1, 100_000_000)
        self.every.setValue(trigger.every or 100)
        self.board = QComboBox()
        self.board.addItem("Any leaderboard", "")
        for key, (name, _unit, _counter) in BOARDS.items():
            self.board.addItem(name, key)
        self.board.setCurrentIndex(max(0, self.board.findData(trigger.board)))
        self.game = QComboBox()
        self.game.addItem("Any game", "")
        for key, name in GAMES.items():
            self.game.addItem(name, key)
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
            "max_coins": ("At most (coins)", self.max_coins),
            "every": ("Every", self.every),
            "command": (f"Command  {controller.settings.command_prefix or '!'}", self.command),
            "goal": ("Goal", self.goal),
            "board": ("Leaderboard", self.board),
            "game": ("Game", self.game),
            "spike_min": ("Chat messages", self.spike_min),
            "factor": ("And", self.factor),
            "role": ("Who", self.role),
        }
        for title, widget in self.rows.values():
            form.addRow(title, widget)
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
        shown = {"gift": {"gifts", "min_coins", "max_coins", "role"}, "like": {"every"}, "viewers": {"every"},
                 "command": {"command", "role"}, "first_chat": {"role"}, "follow": set(), "share": {"role"},
                 "goal": {"goal"},
                 "spike": {"spike_min", "factor"}, "leader": {"board"},
                 "game_end": {"game"}}[kind]
        for key, (_label, widget) in self.rows.items():
            self.form.setRowVisible(widget, key in shown)
        self.form.setRowVisible(self.platform_row, kind not in ("goal", "spike", "leader", "game_end"))
        self.every.setSuffix(" likes" if kind == "like" else " viewers")
        names = ", ".join(f"{{{name}}}" for name in (*PLACEHOLDERS[kind], "rule"))
        self.placeholders.setText(f"Texts can use {names}. Alerts and sounds play in the Alerts overlay. In "
                                  "Minecraft commands, viewers' names come through as one plain word.")

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
                        "min_coins": self.min_coins.value(), "max_coins": self.max_coins.value(),
                        "command": command, "factor": self.factor.value(),
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
                if action["kind"] == "minecraft":
                    return f"Action {number} ({label}) needs a command or a message."
                if action["kind"] == "wait":
                    return f"Action {number} ({label}) needs a time."
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


def tt_interactive_rules() -> list[Rule]:
    """Rules for TT Interactive Stream Games (a Minecraft plugin): its own command turns a gift's coins into an
    in-game effect (chickens, zombies, TNT rain, a boss wave), run here through ServerTap."""
    return [
        Rule(id=new_id(), name="Gift → Minecraft (TT Interactive)", trigger=Trigger("gift"),
             actions=(Action("minecraft", "ttinteractive gift {each} {user}", mode="command", repeat="per_gift"),)),
        Rule(id=new_id(), name="Follow → Minecraft message", trigger=Trigger("follow"),
             actions=(Action("minecraft", "{user} just followed!", mode="broadcast"),)),
        Rule(id=new_id(), name="Every 100 likes → Minecraft chicken", enabled=False, trigger=Trigger("like", every=100),
             actions=(Action("minecraft", "ttinteractive gift 1 {user}", mode="command"),)),
    ]


RULES_FILE_FILTER = "Ignite rules (*.ignite-rules.json *.json)"


class ActionsPage(QWidget):
    """Alerts: what happens on stream when viewers follow, gift, share or chat.

    Rules (each an alert type and what it does, switched on and off right in the list), a preview and test
    panel, this session's alert history, and the sounds alerts can play. ``open_page_requested``: a link to
    another screen (e.g. the notification settings).
    """

    open_page_requested = Signal(str)

    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setStyleSheet("QTabWidget::pane { border: none; } QTabWidget::tab-bar { left: 24px; } QTabBar { margin-top: 8px; }")
        outer.addWidget(self.tabs)
        self.tabs.addTab(self._rules_tab(), "Rules")
        self.tabs.addTab(self._history_tab(), "Activity")
        self.tabs.addTab(self._sounds_tab(), "Sounds")
        controller.settings_changed.connect(lambda _s: self._refresh())
        controller.chat_event.connect(self._chat_event)
        theme_changed().connect(self._restyle)
        self._refresh()
        self._fill_sound_list()
        self._restyle()

    def show_tab(self, name: str) -> None:
        self.tabs.setCurrentIndex({"rules": 0, "activity": 1, "sounds": 2}.get(name, 0))

    # Rules.

    def _rules_tab(self) -> QWidget:
        page = PageContainer()
        columns = QHBoxLayout()
        columns.setSpacing(16)
        self.rules_card = Card("Alert rules", icon="bell")
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.buttons = {}
        for key, text, icon, slot, kind in (("add", "Add Rule", "plus", self._add, "primary"),
                                            ("edit", "Edit", "pencil", self._edit, ""),
                                            ("copy", "Duplicate", "copy", self._duplicate, ""),
                                            ("test", "Test", "flask", self._test, ""),
                                            ("delete", "Delete", "trash", self._delete, "danger")):
            widget = button(text, icon, kind=kind)
            widget.clicked.connect(slot)
            bar.addWidget(widget)
            self.buttons[key] = widget
        self.buttons["test"].setToolTip("Run this rule's actions now with sample values (ignores cooldowns)")
        bar.addStretch(1)
        more = QPushButton("More")
        more.icon_name = "ellipsis-vertical"  # type: ignore[attr-defined]
        menu = QMenu(more)
        menu.addAction("Add Auto-Clip Rules", self._add_auto_clip).setToolTip(
            "Save a clip on big gifts (1,000+ coins) and chat spikes")
        menu.addAction("Add TT Interactive Rules (Minecraft)", self._add_tt_interactive)
        menu.addSeparator()
        menu.addAction("Export Rules…", self._export)
        menu.addAction("Import Rules…", self._import)
        more.setMenu(menu)
        self.more = more
        bar.addWidget(more)
        self.rules_card.body.addLayout(bar)
        self.table = read_only_table(["On", "Name", "When", "Do"])
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        for column in (0, 1, 2):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(320)
        self.table.itemDoubleClicked.connect(lambda _item: self._edit())
        self.table.itemChanged.connect(self._toggled)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.rules_empty = EmptyState("bell", "No alert rules yet",
                                      "Rules react to your LIVE: a gift shows an alert and plays a sound, 1,000 likes "
                                      "switch a scene, !hype reads a message aloud.")
        self.rules_stack = QStackedWidget()
        self.rules_stack.addWidget(self.rules_empty)
        self.rules_stack.addWidget(self.table)
        self.rules_card.body.addWidget(self.rules_stack, 1)
        columns.addWidget(self.rules_card, 3)

        side = QVBoxLayout()
        side.setSpacing(16)
        self.types_card = Card("Alert types", icon="layers")
        self.types_card.body.addWidget(label("What can start a rule. Pick one to add a rule for it.", "muted", wrap=True))
        self.type_rows: dict[str, QPushButton] = {}
        chips = QWidget()
        flow = FlowLayout(chips)
        flow.setSpacing(8)
        for key, text in TRIGGERS.items():
            widget = QPushButton(text)
            widget.setStyleSheet("padding: 5px 12px; border-radius: 14px; font-size: 12px; min-height: 16px;")
            widget.setCursor(Qt.CursorShape.PointingHandCursor)
            widget.setToolTip(f"Add a rule that starts when: {text.lower()}")
            widget.clicked.connect(lambda _=False, k=key: self._add(k))
            flow.addWidget(widget)
            self.type_rows[key] = widget
        self.types_card.body.addWidget(chips)
        side.addWidget(self.types_card)

        self.preview_card = Card("Preview", icon="eye")
        self.overlay_state = StatusIndicator("", "muted")
        self.preview_card.body.addWidget(self.overlay_state)
        self.preview_card.body.addWidget(label("Alerts and their sounds play in the Alerts overlay, so they're in "
                                               "OBS's audio mix.", "muted", wrap=True))
        self.send_test = button("Send Test Alert", "flask", tip="A sample gift and follow on the Alerts overlay "
                                                               "(they don't count anywhere)")
        self.send_test.clicked.connect(lambda: self.controller.simulate(*sample_events("alerts")))
        self.open_preview = button("Preview in Browser", "external-link",
                                   tip="The Alerts overlay with sample alerts, in your browser")
        self.open_preview.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl(self.controller.overlay.url("alerts") + "&demo=1")))
        self.notification_settings = button("Notification Settings", "sliders",
                                            tip="Messages, speech and Discord forwarding")
        self.notification_settings.clicked.connect(lambda: self.open_page_requested.emit("prefs-Messages"))
        for widget in (self.send_test, self.open_preview, self.notification_settings):
            self.preview_card.body.addWidget(widget)
        side.addWidget(self.preview_card)
        side.addStretch(1)
        columns.addLayout(side, 2)
        page.add_layout(columns)
        page.finish()
        self._overlay_timer = QTimer(self, interval=3000)
        self._overlay_timer.timeout.connect(self._overlay_status)
        self._overlay_status()
        return page

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._overlay_timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._overlay_timer.stop()
        super().hideEvent(event)

    def _overlay_status(self) -> None:
        open_now = "alerts" in self.controller.overlay.open_pages()
        self.overlay_state.set("Alerts overlay is open in OBS" if open_now else "Alerts overlay isn't open in OBS",
                               "healthy" if open_now else "warning")

    # Activity: this session's alerts.

    def _history_tab(self) -> QWidget:
        page = PageContainer()
        self.history_card = Card("Alert activity", icon="history")
        bar = QHBoxLayout()
        bar.addWidget(label("Follows, gifts and shares since Ignite started.", "muted"))
        bar.addStretch(1)
        self.history_filter = QComboBox()
        for key, text in (("", "All"), ("follow", "Follows"), ("gift", "Gifts"), ("share", "Shares")):
            self.history_filter.addItem(text, key)
        self.history_filter.currentIndexChanged.connect(lambda _i: self._render_history())
        bar.addWidget(self.history_filter)
        self.history_card.body.addLayout(bar)
        self.history_list = QVBoxLayout()
        self.history_list.setSpacing(0)
        self.history_card.body.addLayout(self.history_list)
        self.history_empty = EmptyState("history", "No alerts yet", "They're listed here as viewers follow, gift "
                                                                    "and share while you're live.")
        self.history_card.body.addWidget(self.history_empty)
        page.add(self.history_card)
        page.finish()
        self.history: list[tuple[float, str, object]] = []
        return page

    def _chat_event(self, event) -> None:
        kind = "follow" if isinstance(event, ChatFollow) else "gift" if isinstance(event, ChatGift) else \
            "share" if isinstance(event, ChatShare) else ""
        if not kind or getattr(event, "streaking", False):
            return
        self.history.insert(0, (time.time(), kind, event))
        del self.history[MAX_HISTORY:]
        if self.tabs.currentIndex() == 1:
            self._render_history()

    def _render_history(self) -> None:
        while self.history_list.count():
            item = self.history_list.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        wanted = self.history_filter.currentData()
        shown = [(when, event) for when, kind, event in self.history if not wanted or kind == wanted][:100]
        self.history_empty.setVisible(not shown)
        from ignite_live.ui.pages.overview import event_text

        for index, (when, event) in enumerate(shown):
            if index:
                self.history_list.addWidget(hline())
            icon, tone, title, detail = event_text(event)
            self.history_list.addWidget(EventRow(icon, tone, title, detail, when))

    # Sounds.

    def _sounds_tab(self) -> QWidget:
        page = PageContainer()
        self.sounds_card = Card("Sounds", icon="volume")
        bar = QHBoxLayout()
        bar.addWidget(label("mp3, wav or ogg files in the sounds folder. Rules, chat commands and the test buttons "
                            "play them through the Alerts overlay.", "muted", wrap=True), 1)
        self.open_folder = button("Open Sounds Folder", "folder-open")
        self.open_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.controller.open_sounds_folder()))))
        self.reload_sounds = button("Refresh", "refresh-cw")
        self.reload_sounds.clicked.connect(self._fill_sound_list)
        bar.addWidget(self.reload_sounds)
        bar.addWidget(self.open_folder)
        self.sounds_card.body.addLayout(bar)
        self.sound_rows = QVBoxLayout()
        self.sound_rows.setSpacing(4)
        self.sounds_card.body.addLayout(self.sound_rows)
        self.sounds_empty = EmptyState("volume", "No sounds yet", "Add files to the sounds folder, then Refresh.")
        self.sounds_card.body.addWidget(self.sounds_empty)
        page.add(self.sounds_card)
        page.finish()
        # "Play Sound" menu (kept for callers of the old Actions tab).
        self.sound_menu = QMenu(self)
        self.sound_menu.aboutToShow.connect(self._fill_sounds)
        return page

    def _sound_names(self) -> list[str]:
        folder = self.controller.sounds_dir
        return sorted(p.name for p in folder.iterdir() if SOUND_NAME.match(p.name)) if folder.is_dir() else []

    def _fill_sound_list(self) -> None:
        while self.sound_rows.count():
            item = self.sound_rows.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        names = self._sound_names()
        self.sounds_empty.setVisible(not names)
        for name in names:
            line = QWidget()
            row = QHBoxLayout(line)
            row.setContentsMargins(0, 2, 0, 2)
            row.addWidget(icon_label("music", colors().text_muted, 16))
            row.addWidget(label(name), 1)
            play = button("Play", "play", tip=f"Play {name} through the Alerts overlay")
            play.clicked.connect(lambda _=False, n=name: self.controller.play_sound(n))
            row.addWidget(play)
            self.sound_rows.addWidget(line)

    def _fill_sounds(self) -> None:
        self.sound_menu.clear()
        names = self._sound_names()
        if not names:
            self.sound_menu.addAction("No sounds yet: add files to the sounds folder").setEnabled(False)
        for name in names:
            self.sound_menu.addAction(name, lambda n=name: self.controller.play_sound(n))

    # Rules: data.

    def _rules(self) -> list[Rule]:
        return parse_rules(self.controller.settings.action_rules)

    def _refresh(self) -> None:
        selected = self._selected()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        prefix = self.controller.settings.command_prefix or "!"
        goal_names = {goal.id: goal.name for goal in parse_goals(self.controller.settings.goals)}
        rules = self._rules()
        for row, rule in enumerate(rules):
            self.table.insertRow(row)
            on = QTableWidgetItem()
            on.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            on.setCheckState(Qt.CheckState.Checked if rule.enabled else Qt.CheckState.Unchecked)
            on.setData(Qt.ItemDataRole.UserRole, rule.id)
            on.setToolTip("On: the rule runs. Off: it's kept but does nothing.")
            self.table.setItem(row, 0, on)
            for column, text in enumerate((rule.name, describe_trigger(rule.trigger, prefix, goal_names),
                                           describe_actions(rule.actions)), 1):
                self.table.setItem(row, column, QTableWidgetItem(text))
            if selected and rule.id == selected.id:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        self.rules_stack.setCurrentWidget(self.table if rules else self.rules_empty)
        counts: dict[str, int] = {}
        for rule in rules:
            if rule.enabled:
                counts[rule.trigger.kind] = counts.get(rule.trigger.kind, 0) + 1
        for key, widget in self.type_rows.items():
            count = counts.get(key, 0)
            widget.setText(f"{TRIGGERS[key]}" + (f"  ·  {count} on" if count else ""))
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

    def _add(self, kind: str = "gift") -> None:
        name = f"{TRIGGERS.get(kind, 'New')} alert" if kind else "New rule"
        self._open_editor(Rule(id=new_id(), name=name, trigger=Trigger(kind or "gift"),
                               actions=(Action("alert", "{user} sent {gift}!" if kind == "gift" else "{user}!"),)))

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
        answer = QMessageBox.question(self, "Delete rule", f"Delete “{rule.name}”? This can't be undone.")
        if answer == QMessageBox.StandardButton.Yes:
            self._save([r for r in self._rules() if r.id != rule.id])

    def _test(self) -> None:
        rule = self._selected()
        if rule is not None:
            self.controller.test_rule(rule)

    def _add_auto_clip(self) -> None:
        rules = self._rules()
        names = {rule.name for rule in rules}
        added = [rule for rule in auto_clip_rules() if rule.name not in names]
        if added:
            self._save(rules + added)
        self.controller.notice.emit("info", "Auto-clip rules added: clips are saved 10 s after a big gift or a chat "
                                            "spike. They need OBS's replay buffer running." if added
                                    else "The auto-clip rules are already there.")

    def _add_tt_interactive(self) -> None:
        rules = self._rules()
        names = {rule.name for rule in rules}
        added = [rule for rule in tt_interactive_rules() if rule.name not in names]
        if added:
            self._save(rules + added)
        self.controller.notice.emit("info", "Minecraft rules added: gifts run TT Interactive's /ttinteractive gift. "
                                            "Set ServerTap's address and key in Settings → Integrations."
                                    if added else "The TT Interactive rules are already there.")

    def _export(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(self, "Export rules", "my.ignite-rules.json", RULES_FILE_FILTER)
        if not path:
            return
        data = {"ignite_rules": 1, "rules": [to_dict(rule) for rule in self._rules()]}
        try:
            Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        except OSError as error:
            self.controller.notice.emit("error", f"Couldn't save the rules: {error.strerror or error}")
            return
        self.controller.notice.emit("info", f"Saved {len(data['rules'])} rules to {Path(path).name}.")

    def _import(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Import rules", "", RULES_FILE_FILTER)
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text("utf-8")[:2_000_000])
        except (OSError, ValueError, UnicodeDecodeError):
            self.controller.notice.emit("error", "That file isn't an Ignite rules file.")
            return
        found = parse_rules(raw.get("rules") if isinstance(raw, dict) else raw)
        rules = self._rules()
        room = max(0, MAX_RULES - len(rules))
        added = [replace(rule, id=new_id()) for rule in found[:room]]  # New ids: never overwrite one of yours.
        if added:
            self._save(rules + added)
        skipped = len(found) - len(added)
        self.controller.notice.emit("info" if added else "error",
                                    f"Imported {len(added)} rules." + (f" {skipped} didn't fit (at most {MAX_RULES})."
                                                                       if skipped else "")
                                    if found else "No rules Ignite can use were in that file.")

    def _toggled(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        rule_id = item.data(Qt.ItemDataRole.UserRole)
        on = item.checkState() == Qt.CheckState.Checked
        self._save([replace(r, enabled=on) if r.id == rule_id else r for r in self._rules()])

    def _restyle(self, _palette=None) -> None:
        for card in (self.rules_card, self.types_card, self.preview_card, self.history_card, self.sounds_card):
            card.restyle()
        for empty in (self.rules_empty, self.history_empty, self.sounds_empty):
            empty.restyle()
        restyle_buttons(*self.buttons.values(), self.more, self.send_test, self.open_preview,
                        self.notification_settings, self.open_folder, self.reload_sounds)
        self._fill_sound_list()
