"""Add Overlays to OBS: pick which overlays (or all of them) and which scene."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import Controller
from ignite_live.overlay.server import PAGES
from ignite_live.ui.pages.stream import note

VERTICAL_ROLE = Qt.ItemDataRole.UserRole + 1  # Marks scenes of the Aitum vertical canvas.


class AddOverlaysDialog(QDialog):
    def __init__(self, controller: Controller, parent: QWidget | None = None, selected: list[str] | None = None) -> None:
        """``selected``: the overlays ticked at first; None ticks all of them."""
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Add Overlays to OBS")
        layout = QVBoxLayout(self)

        box = QGroupBox("Overlays")
        rows = QVBoxLayout(box)
        self.everything = QCheckBox("All overlays")
        self.everything.setTristate(True)
        self.everything.clicked.connect(self._all_clicked)
        rows.addWidget(self.everything)
        self.checks: dict[str, QCheckBox] = {}
        self.present: set[str] = set()  # Overlays OBS already has.
        for page, (title, width, height) in PAGES.items():
            check = QCheckBox(f"{title}  ({width} × {height})")
            check.setChecked(selected is None or page in selected)
            check.setStyleSheet("QCheckBox { margin-left: 18px; }")  # Indented under "All overlays".
            check.toggled.connect(self._sync_all)
            rows.addWidget(check)
            self.checks[page] = check
        layout.addWidget(box)

        form = QFormLayout()
        self.scene = QComboBox()
        studio = controller.studio
        for name in studio.scenes:
            self.scene.addItem(name, name)
        vertical = studio.vertical
        if vertical is not None:  # Aitum Vertical Canvas scenes, listed after the main ones.
            self.scene.insertSeparator(self.scene.count())
            for name in vertical.scenes:
                self.scene.addItem(f"Vertical: {name}", name)
                index = self.scene.count() - 1
                self.scene.setItemData(index, True, VERTICAL_ROLE)
                if not vertical.canvas_uuid:
                    self.scene.model().item(index).setEnabled(False)
                    self.scene.setItemData(index, "Needs a newer OBS (obs-websocket 5.7+) to add sources here",
                                           Qt.ItemDataRole.ToolTipRole)
        self.scene.setCurrentIndex(max(0, self.scene.findData(studio.scene)))  # Main scenes come first.
        form.addRow("Add to &scene:", self.scene)
        self.again = QCheckBox("Also add overlays that are already in OBS")
        self.again.toggled.connect(self._sync_all)
        form.addRow("", self.again)
        layout.addLayout(form)
        self.status = note("Checking what's already in OBS…")
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.add = buttons.addButton("Add to OBS", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._sync_all()
        if not studio.connected:
            self.status.setText("OBS isn't connected. Open OBS (and check File → Preferences → OBS), then try again.")
            self.add.setEnabled(False)
        else:
            controller.overlays_in_obs(self._show_present)

    def chosen(self) -> list[str]:
        return [page for page, check in self.checks.items() if check.isChecked()]

    def _all_clicked(self) -> None:
        # Clicking a partly-checked "All" ticks everything (not the in-between state).
        everything = self.everything.checkState() != Qt.CheckState.Unchecked
        for check in self.checks.values():
            check.blockSignals(True)
            check.setChecked(everything)
            check.blockSignals(False)
        self._sync_all()

    def _sync_all(self) -> None:
        chosen = self.chosen()
        state = (Qt.CheckState.Checked if len(chosen) == len(self.checks)
                 else Qt.CheckState.Unchecked if not chosen else Qt.CheckState.PartiallyChecked)
        self.everything.setCheckState(state)
        # Only count what will really be added: ones already in OBS are skipped unless "again" is ticked.
        count = sum(1 for page in chosen if self.again.isChecked() or page not in self.present)
        self.add.setText(f"Add {count} to OBS")
        self.add.setEnabled(count > 0 and self.controller.studio.connected)

    def _show_present(self, pages: set[str]) -> None:
        try:
            self._mark_present(pages)
        except RuntimeError:  # The dialog closed before OBS answered.
            pass

    def _mark_present(self, pages: set[str]) -> None:
        self.present = set(pages)
        names = [PAGES[page][0] for page in PAGES if page in pages]
        for page, check in self.checks.items():
            title = PAGES[page][0]
            check.setText(check.text().split("  —")[0] + ("  — already in OBS" if page in pages else ""))
            check.setToolTip(f"{title} is already a source in OBS." if page in pages else "")
        self.status.setText(
            f"Already in OBS: {', '.join(names)}. Those are skipped unless you tick the box above."
            if names else "None of these overlays are in OBS yet."
        )
        self._sync_all()

    def _accept(self) -> None:
        index = self.scene.currentIndex()
        vertical = bool(self.scene.itemData(index, VERTICAL_ROLE)) if index >= 0 else False
        self.controller.add_overlays(self.chosen(), self.scene.currentData() or "", self.again.isChecked(), vertical)
        self.accept()
