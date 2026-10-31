"""OBS panel (a dock): stream health, scene switcher, audio mixer, recording and tools."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import Controller
from ignite_live.core.events import ConnectionState
from ignite_live.integrations.obs import AudioInput
from ignite_live.integrations.obs_studio import Health, Studio, clock
from ignite_live.ui.actions import AppActions, action_button
from ignite_live.ui.pages.stream import note
from ignite_live.ui.theme import state_color, warn_color

SILENT_DB = -60  # The slider's left end; below this OBS is effectively silent, so it maps to -100 dB.
WARN_PCT = 1.0  # Health values turn orange from here (the notice comes at the Preferences threshold).


def db_text(db: float) -> str:
    return "-∞ dB" if db <= SILENT_DB else f"{db:.1f} dB"


def health_rows(health: Health, studio: Studio) -> list[tuple[str, str, bool]]:
    """(label, value, warn) for the health section."""
    live = studio.streaming
    rows = [
        ("Stream", f"LIVE {clock(health.stream_duration_ms)}" if live else "Not streaming", health.reconnecting),
        ("Bitrate", f"{health.bitrate_kbps:,} kbps" if live else "–", live and health.bitrate_kbps == 0),
        ("Dropped (network)", f"{health.network_dropped_pct:.1f}%" if live else "–", health.network_dropped_pct >= WARN_PCT),
        ("Rendering", f"{health.fps:.0f} fps, {health.render_lag_pct:.1f}% skipped", health.render_lag_pct >= WARN_PCT),
        ("Encoding", f"{health.encode_lag_pct:.1f}% skipped", health.encode_lag_pct >= WARN_PCT),
        ("CPU", f"{health.cpu:.0f}%", health.cpu >= 85),
        ("Memory", f"{health.memory_mb:,.0f} MB", False),
        ("Disk free", f"{health.disk_mb / 1024:,.1f} GB" if health.disk_mb else "–", 0 < health.disk_mb < 5 * 1024),
    ]
    if studio.recording:
        rows.insert(1, ("Recording", ("Paused " if studio.record_paused else "REC ") + clock(studio.record_duration_ms), False))
    vertical = studio.vertical
    if vertical is not None:
        parts = ["LIVE" if vertical.streaming else "Not streaming"]
        parts += ["REC"] if vertical.recording else []
        parts += ["backtrack on"] if vertical.backtrack else []
        rows.insert(1, ("Vertical canvas", " · ".join(parts), False))
    return rows


def section(title: str) -> QLabel:
    label = QLabel(title)
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    label.setContentsMargins(0, 8, 0, 2)
    return label


class AudioRow(QWidget):
    """Name, level and mute on one line, the fader below: fits a narrow dock."""

    def __init__(self, controller: Controller, audio: AudioInput) -> None:
        super().__init__()
        self.controller, self.name = controller, audio.name
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setVerticalSpacing(0)
        self.mute = QToolButton()
        self.mute.setCheckable(True)
        self.mute.setToolTip(f"Mute {audio.name} in OBS")
        self.mute.toggled.connect(self._mute)
        self.label = QLabel(audio.name)
        self.label.setToolTip(audio.name)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(SILENT_DB, 0)
        self.value = QLabel("")
        self.value.setMinimumWidth(self.value.fontMetrics().horizontalAdvance("-60.0 dB"))
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # Send the volume shortly after the slider settles, not on every pixel.
        self.debounce = QTimer(self, singleShot=True, interval=120)
        self.debounce.timeout.connect(self._send_volume)
        self.slider.valueChanged.connect(lambda v: (self.value.setText(db_text(v)), self.debounce.start()))
        layout.addWidget(self.label, 0, 0)
        layout.addWidget(self.value, 0, 1)
        layout.addWidget(self.mute, 0, 2)
        layout.addWidget(self.slider, 1, 0, 1, 3)
        layout.setColumnStretch(0, 1)
        self.update_from(audio)

    def update_from(self, audio: AudioInput) -> None:
        self.mute.blockSignals(True)
        self.mute.setChecked(audio.muted)
        self.mute.blockSignals(False)
        self.mute.setText("Muted" if audio.muted else "Mute")
        self.label.setEnabled(not audio.muted)
        if not self.slider.isSliderDown() and not self.debounce.isActive():
            self.slider.blockSignals(True)
            self.slider.setValue(max(SILENT_DB, min(0, round(audio.volume_db))))
            self.slider.blockSignals(False)
            self.value.setText(db_text(audio.volume_db))

    def _mute(self, muted: bool) -> None:
        self.controller.set_input_mute(self.name, muted)

    def _send_volume(self) -> None:
        value = self.slider.value()
        self.controller.set_input_volume(self.name, -100.0 if value <= SILENT_DB else float(value))


class ObsPanel(QWidget):
    def __init__(self, controller: Controller, actions: AppActions) -> None:
        super().__init__()
        self.controller = controller
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(4)
        self.offline = note("")
        layout.addWidget(self.offline)

        # Health.
        layout.addWidget(section("Health"))
        self.health_grid = QGridLayout()
        self.health_grid.setHorizontalSpacing(12)
        self.health_grid.setVerticalSpacing(2)
        self.health_grid.setColumnStretch(1, 1)
        self.health_labels: dict[str, tuple[QLabel, QLabel]] = {}
        layout.addLayout(self.health_grid)

        # Scenes.
        layout.addWidget(section("Scenes"))
        lists = QHBoxLayout()
        self.scenes = QListWidget()
        self.vertical_scenes = QListWidget()  # Aitum Vertical Canvas, when installed.
        self.scene_titles: dict[bool, QLabel] = {}
        for vertical, widget, title in ((False, self.scenes, "Horizontal"), (True, self.vertical_scenes, "Vertical")):
            widget.setToolTip("Click a scene to show it on stream")
            widget.itemClicked.connect(lambda item, v=vertical: self._scene_clicked(item, v))
            widget.itemActivated.connect(lambda item, v=vertical: self._scene_clicked(item, v))
            column = QVBoxLayout()
            self.scene_titles[vertical] = note(title)
            column.addWidget(self.scene_titles[vertical])
            column.addWidget(widget, 1)
            lists.addLayout(column)
        layout.addLayout(lists, 2)

        # Audio.
        layout.addWidget(section("Audio"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        holder = QWidget()
        self.audio_layout = QVBoxLayout(holder)
        self.audio_layout.setContentsMargins(0, 0, 0, 0)
        self.audio_layout.addStretch(1)
        scroll.setWidget(holder)
        scroll.setToolTip("Changes go straight to OBS's mixer")
        layout.addWidget(scroll, 3)
        self.audio_rows: dict[str, AudioRow] = {}

        # Recording and tools.
        layout.addWidget(section("Tools"))
        tools = QGridLayout()
        for index, action in enumerate((actions.toggle_record, actions.pause_record, actions.toggle_replay,
                                        actions.save_replay, actions.toggle_virtualcam, actions.screenshot,
                                        actions.refresh_overlays, actions.add_all_overlays)):
            tools.addWidget(action_button(action), *divmod(index, 2))
        layout.addLayout(tools)
        self.vertical_tools = QWidget()  # Aitum Vertical Canvas: its own recording and backtrack (replay buffer).
        vertical_box = QVBoxLayout(self.vertical_tools)
        vertical_box.setContentsMargins(0, 0, 0, 0)
        vertical_box.addWidget(section("Vertical canvas"))
        for action in (actions.toggle_vertical_record, actions.toggle_vertical_backtrack, actions.save_vertical_clip):
            vertical_box.addWidget(action_button(action))
        layout.addWidget(self.vertical_tools)
        self.replay_note = note("")
        layout.addWidget(self.replay_note)

        controller.studio_changed.connect(self._studio)
        controller.status_changed.connect(lambda change: self._studio(controller.studio) if change.source == "obs" else None)
        self._studio(controller.studio)

    # Updates.

    def _studio(self, studio: Studio) -> None:
        obs = self.controller.statuses["obs"]
        self.offline.setVisible(not studio.connected)
        self.offline.setText(f"OBS isn't connected: {obs.detail or 'open OBS'}. Set it up in File → Preferences → OBS.")
        self._health(studio)
        self._scenes(studio)
        self._audio(studio)
        self.replay_note.setText(
            "Clips need the replay buffer: in OBS, Settings → Output → Replay Buffer → Enable, then restart OBS's "
            "outputs. Moderators can type !clip in chat to save one." if studio.connected and studio.replay is None
            else "Moderators can type !clip to save a clip, and !scene name to switch scenes (Preferences → OBS)."
        )

    def _health(self, studio: Studio) -> None:
        rows = health_rows(studio.health, studio)
        if [name for name, *_ in rows] != list(self.health_labels):
            while self.health_grid.count():
                self.health_grid.takeAt(0).widget().deleteLater()
            self.health_labels = {}
            bold = QFont(self.font())
            bold.setBold(True)
            for row, (name, _value, _warn) in enumerate(rows):
                title, value = QLabel(name), QLabel("")
                title.setEnabled(False)  # Secondary text colour, like note().
                value.setFont(bold)
                value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.health_grid.addWidget(title, row, 0)
                self.health_grid.addWidget(value, row, 1)
                self.health_labels[name] = (title, value)
        for name, text, warn in rows:
            title, value = self.health_labels[name]
            value.setText(text if studio.connected else "–")
            color = None
            if studio.connected and warn:
                color = warn_color(value)
            elif name == "Stream" and studio.streaming:
                color = state_color(ConnectionState.ERROR, value)  # Red "LIVE".
            value.setStyleSheet(f"color: {color.name()};" if color is not None else "")

    def _scenes(self, studio: Studio) -> None:
        vertical = studio.vertical
        self._fill(self.scenes, studio.scenes, studio.scene, studio.connected)
        self._fill(self.vertical_scenes, vertical.scenes if vertical else (), vertical.scene if vertical else "",
                   studio.connected)
        for widget in (self.vertical_scenes, self.scene_titles[True], self.scene_titles[False]):
            widget.setVisible(vertical is not None)  # One plain list unless there's a vertical canvas.
        self.vertical_tools.setVisible(studio.connected and vertical is not None)

    @staticmethod
    def _fill(widget: QListWidget, scenes: tuple[str, ...], current_scene: str, enabled: bool) -> None:
        names = [widget.item(i).data(Qt.ItemDataRole.UserRole) for i in range(widget.count())]
        if names != list(scenes):
            widget.clear()
            for name in scenes:
                item = QListWidgetItem(name)
                item.setData(Qt.ItemDataRole.UserRole, name)
                widget.addItem(item)
        for i in range(widget.count()):
            item = widget.item(i)
            current = item.data(Qt.ItemDataRole.UserRole) == current_scene
            font = item.font()
            font.setBold(current)
            item.setFont(font)
            item.setText(("● " if current else "    ") + item.data(Qt.ItemDataRole.UserRole))
        widget.setEnabled(enabled)

    def _audio(self, studio: Studio) -> None:
        names = [a.name for a in studio.audio]
        if names != list(self.audio_rows):
            for row in self.audio_rows.values():
                row.deleteLater()
            self.audio_rows = {}
            for audio in studio.audio:
                row = AudioRow(self.controller, audio)
                self.audio_layout.insertWidget(self.audio_layout.count() - 1, row)
                self.audio_rows[audio.name] = row
        for audio in studio.audio:
            self.audio_rows[audio.name].update_from(audio)

    def _scene_clicked(self, item: QListWidgetItem, vertical: bool = False) -> None:
        studio = self.controller.studio
        name = item.data(Qt.ItemDataRole.UserRole)
        current = studio.vertical.scene if vertical and studio.vertical else studio.scene
        if name != current:
            self.controller.switch_scene(name, vertical)
