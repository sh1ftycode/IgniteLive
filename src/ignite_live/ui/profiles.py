"""File → Profiles: save, switch, import, and export whole setups (.igniteprofile files)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import Controller
from ignite_live.core import profiles
from ignite_live.core.profiles import Profile, ProfileError
from ignite_live.ui.pages.stream import note
from ignite_live.ui.theme import warn_color

FILTER = f"Ignite profiles (*{profiles.EXTENSION})"
OPEN_FILTER = f"Ignite profiles (*{profiles.EXTENSION} *{profiles.LEGACY_EXTENSION})"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class ImportDialog(QDialog):
    """Shows what a profile would change before anything is touched."""

    def __init__(self, controller: Controller, profile: Profile, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import profile")
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)  # Grows to fit the wrapped notes.
        heading = QLabel(f"<h3>{_escape(profile.name)}</h3>")
        layout.addWidget(heading)
        if profile.created:
            layout.addWidget(note(f"Made {_escape(profile.created)}"))
        lines = profiles.summary(profile, controller.sounds_dir)
        layout.addWidget(QLabel("<b>This changes:</b>" if lines else "This profile has no settings in it."))
        for line in lines:  # One label each: Qt sizes a wrapped rich-text list too short.
            layout.addWidget(QLabel(f"  •  {line}"))
        layout.addWidget(note("Your usernames, OBS and server connections, stream key, and passwords are never "
                              "part of a profile."))
        for warning in profiles.warnings(profile):
            label = QLabel("<b>Warning:</b> " + _escape(warning))
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {warn_color(label).name()};")
            layout.addWidget(label)
        self.replace_rules = QRadioButton("Replace my action rules and goals")
        self.add_rules = QRadioButton("Add them to my action rules and goals")
        self.replace_rules.setChecked(True)
        layout.addWidget(self.replace_rules)
        layout.addWidget(self.add_rules)
        self.overwrite = QCheckBox("Replace my sound files that have the same name")
        self.overwrite.setVisible(any((controller.sounds_dir / name).exists() for name in profile.sounds))
        layout.addWidget(self.overwrite)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ProfilesMenu(QMenu):
    def __init__(self, controller: Controller, parent: QWidget) -> None:
        super().__init__("&Profiles", parent)
        self.controller = controller
        self.addAction("&Save Current Setup…", self.save_current)
        self.switch = self.addMenu("S&witch To")
        self.switch.aboutToShow.connect(self._fill_switch)
        self.addSeparator()
        self.addAction("&Import…", self.import_file)
        self.addAction("&Export…", self.export_file)
        self.addSeparator()
        self.addAction("Open Profiles &Folder", self._open_folder)

    def _parent(self) -> QWidget:
        return self.parentWidget()

    def _fill_switch(self) -> None:
        self.switch.clear()
        saved = self.controller.saved_profiles()
        if not saved:
            self.switch.addAction("No saved profiles yet").setEnabled(False)
        for path in saved:
            self.switch.addAction(path.stem, lambda p=path: self.open_profile(p))

    def save_current(self) -> None:
        name, ok = QInputDialog.getText(self._parent(), "Save current setup", "Profile name:",
                                        QLineEdit.EchoMode.Normal, "My setup")
        if not ok or not name.strip():
            return
        path = self.controller.profiles_dir / profiles.file_name(name)
        if path.exists():
            answer = QMessageBox.question(self._parent(), "Save current setup", f"Replace the saved “{path.stem}”?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            self.controller.save_profile(name)
        except (ProfileError, OSError) as error:
            self.controller.notice.emit("error", str(error))
            return
        self.controller.notice.emit("info", f"Saved “{path.stem}”. Switch back to it from File → Profiles.")

    def export_file(self) -> None:
        start = str(Path.home() / profiles.file_name("My Ignite setup"))
        target, _ = QFileDialog.getSaveFileName(self._parent(), "Export profile", start, FILTER)
        if not target:
            return
        path = Path(target)
        if path.suffix != profiles.EXTENSION:
            path = path.with_name(path.name + profiles.EXTENSION)
        try:
            self.controller.export_profile(path, path.stem)
        except (ProfileError, OSError) as error:
            self.controller.notice.emit("error", f"Couldn't export: {error}")
            return
        self.controller.notice.emit("info", f"Exported your setup to {path}.")

    def import_file(self) -> None:
        target, _ = QFileDialog.getOpenFileName(self._parent(), "Import profile", str(Path.home()), OPEN_FILTER)
        if target:
            self.open_profile(Path(target))

    def open_profile(self, path: Path) -> None:
        try:
            profile = self.controller.read_profile(path)
        except ProfileError as error:
            self.controller.notice.emit("error", str(error))
            return
        dialog = ImportDialog(self.controller, profile, self._parent())
        if dialog.exec():
            try:
                self.controller.import_profile(profile, dialog.add_rules.isChecked(), dialog.overwrite.isChecked())
            except OSError as error:
                self.controller.notice.emit("error", f"Couldn't import: {error}")

    def _open_folder(self) -> None:
        self.controller.profiles_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.controller.profiles_dir)))
