"""Goals box on the Overlays tab: goal bars for likes, coins, follows, shares, or one gift."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ignite_live.actions.goals import (
    DEFAULT_LABELS,
    GOAL_KINDS,
    LABEL_PLACEHOLDERS,
    MAX_GOALS,
    MAX_TARGET,
    WHEN_DONE,
    Goal,
    describe_goal,
    goal_to_dict,
    parse_goal,
    parse_goals,
)
from ignite_live.actions.rules import new_id
from ignite_live.controller import Controller
from ignite_live.ui.pages.music import read_only_table
from ignite_live.ui.pages.stream import note


class GoalDialog(QDialog):
    def __init__(self, goal: Goal, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.goal_id = goal.id
        self.setWindowTitle("Edit goal")
        self.resize(480, 0)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(goal.name)
        self.kind = QComboBox()
        for key, label in GOAL_KINDS.items():
            self.kind.addItem(label, key)
        self.kind.setCurrentIndex(max(0, self.kind.findData(goal.kind)))
        self.gift = QLineEdit(goal.gift)
        self.gift.setPlaceholderText("e.g. Rose")
        self.target = QSpinBox()
        self.target.setRange(1, MAX_TARGET)
        self.target.setGroupSeparatorShown(True)
        self.target.setValue(goal.target)
        self.label = QLineEdit(goal.label)
        self.when_done = QComboBox()
        for key, label in WHEN_DONE.items():
            self.when_done.addItem(label, key)
        self.when_done.setCurrentIndex(max(0, self.when_done.findData(goal.when_done)))
        self.step = QSpinBox()
        self.step.setRange(0, MAX_TARGET)
        self.step.setGroupSeparatorShown(True)
        self.step.setSpecialValueText("By the original target")
        self.step.setValue(goal.step)
        self.keep = QCheckBox("Keep progress across streams")
        self.keep.setChecked(goal.keep)
        for label, widget in (("Name", self.name), ("Counts", self.kind), ("Gift", self.gift), ("Target", self.target),
                              ("Bar text", self.label), ("When reached", self.when_done), ("Raise by", self.step),
                              ("", self.keep)):
            form.addRow(label, widget)
        self.form = form
        layout.addLayout(form)
        layout.addWidget(note("Bar text can use " + ", ".join(f"{{{p}}}" for p in LABEL_PLACEHOLDERS) +
                              ". To celebrate, add a rule with the “Goal reached” trigger on the Actions tab."))
        self.error = QLabel()
        self.error.setStyleSheet("color: #e5484d")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.kind.currentIndexChanged.connect(self._changed)
        self.when_done.currentIndexChanged.connect(self._changed)
        self._changed()
        self.result_goal: Goal | None = None

    def _changed(self) -> None:
        kind = self.kind.currentData()
        self.form.setRowVisible(self.gift, kind == "gift")
        self.form.setRowVisible(self.step, self.when_done.currentData() == "raise")
        self.label.setPlaceholderText(DEFAULT_LABELS[kind])

    def _accept(self) -> None:
        goal = parse_goal({"id": self.goal_id, "name": self.name.text(), "kind": self.kind.currentData(),
                           "gift": self.gift.text(), "target": self.target.value(), "label": self.label.text(),
                           "when_done": self.when_done.currentData(), "step": self.step.value(),
                           "keep": self.keep.isChecked()})
        if goal is None:
            self.error.setText("Enter which gift counts.")
            return
        self.result_goal = goal
        self.accept()


class GoalsBox(QGroupBox):
    def __init__(self, controller: Controller) -> None:
        super().__init__("Goals")
        self.controller = controller
        layout = QVBoxLayout(self)
        layout.addWidget(note("Goal bars for the <b>Goals</b> overlay. Each goal can also have its own URL."))
        self.table = read_only_table(["Name", "Goal", "Progress"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.table.itemDoubleClicked.connect(lambda _item: self._edit())
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.buttons = {}
        for key, text, slot in (("add", "Add Goal…", self._add), ("edit", "Edit…", self._edit),
                                ("delete", "Delete", self._delete), ("test", "Test", self._test),
                                ("reset", "Reset Progress", self._reset),
                                ("copy", "Copy URL", self._copy)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
            self.buttons[key] = button
        self.buttons["copy"].setToolTip("The Goals overlay showing only this goal")
        self.buttons["test"].setToolTip("Show it reached on the overlay and run its “Goal reached” rules; "
                                        "the real progress comes back after a few seconds")
        row.addStretch(1)
        layout.addLayout(row)
        controller.settings_changed.connect(lambda _s: self._refresh())
        controller.goals_changed.connect(self._progress)
        self._refresh()

    def _goals(self) -> list[Goal]:
        return parse_goals(self.controller.settings.goals)

    def _refresh(self) -> None:
        selected = self._selected()
        self.table.setRowCount(0)
        for row, goal in enumerate(self._goals()):
            self.table.insertRow(row)
            name = QTableWidgetItem(goal.name)
            name.setData(Qt.ItemDataRole.UserRole, goal.id)
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(describe_goal(goal)))
            self.table.setItem(row, 2, QTableWidgetItem(""))
            if selected and goal.id == selected.id:
                self.table.selectRow(row)
        self.buttons["add"].setEnabled(self.table.rowCount() < MAX_GOALS)
        self._progress(self.controller.goal_snapshot)
        self._selection_changed()

    def _progress(self, bars: list[dict]) -> None:
        by_id = {bar["id"]: bar for bar in bars}
        for row in range(self.table.rowCount()):
            bar = by_id.get(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole))
            if bar:
                self.table.item(row, 2).setText(f"{bar['current']:,} / {bar['target']:,} ({bar['percent']:g}%)")

    def _selected(self) -> Goal | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        goal_id = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return next((goal for goal in self._goals() if goal.id == goal_id), None)

    def _selection_changed(self) -> None:
        has = self._selected() is not None
        for key in ("edit", "delete", "test", "reset", "copy"):
            self.buttons[key].setEnabled(has)

    def _save(self, goals: list[Goal]) -> None:
        self.controller.save_settings(replace(self.controller.settings, goals=[goal_to_dict(g) for g in goals]))

    def _open_editor(self, goal: Goal) -> None:
        dialog = GoalDialog(goal, self)
        if dialog.exec() and dialog.result_goal is not None:
            self.put(dialog.result_goal)

    def put(self, goal: Goal) -> None:
        goals = self._goals()
        index = next((i for i, g in enumerate(goals) if g.id == goal.id), None)
        if index is None:
            goals.append(goal)
        else:
            goals[index] = goal
        self._save(goals)

    def _add(self) -> None:
        self._open_editor(Goal(id=new_id(), name="Like goal", kind="likes", target=10_000))

    def _edit(self) -> None:
        goal = self._selected()
        if goal is not None:
            self._open_editor(goal)

    def _delete(self) -> None:
        goal = self._selected()
        if goal is None:
            return
        if QMessageBox.question(self, "Delete goal", f"Delete “{goal.name}”?") == QMessageBox.StandardButton.Yes:
            self._save([g for g in self._goals() if g.id != goal.id])

    def _test(self) -> None:
        goal = self._selected()
        if goal is not None:
            self.controller.test_goal(goal.id)

    def _reset(self) -> None:
        goal = self._selected()
        if goal is not None:
            self.controller.reset_goal(goal.id)

    def _copy(self) -> None:
        goal = self._selected()
        if goal is not None:
            QGuiApplication.clipboard().setText(self.controller.overlay.url("goals") + f"&goal={goal.id}")
            self.controller.notice.emit("info", f"Copied the URL for “{goal.name}”.")
