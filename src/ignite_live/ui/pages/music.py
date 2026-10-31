from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import Controller, command_config
from ignite_live.music.commands import CommandKind
from ignite_live.ui.actions import AppActions, action_button
from ignite_live.ui.pages.stream import buttons, note

COMMANDS = (
    (CommandKind.REQUEST, " song - artist", "Request a song, or paste a YouTube link"),
    (CommandKind.SKIP, "", "Skip your own song; moderators can skip any song"),
    (CommandKind.REVOKE, "", "Remove your latest request"),
    (CommandKind.CURRENT, "", "Show what's playing"),
    (CommandKind.QUEUE, "", "Show how many songs are queued"),
    (CommandKind.TTS_SKIP, "", "Moderators: stop the message being read aloud"),
    (CommandKind.CLIP, "", "Moderators: save the last moments as a clip (OBS replay buffer)"),
    (CommandKind.SCENE, " name", "Moderators: switch OBS scenes"),
)


def command_rows(settings) -> list[tuple[str, str]]:
    """The chat commands as viewers type them, with the streamer's own names."""
    prefix, aliases = settings.command_prefix or "!", command_config(settings).aliases
    rows = []
    for kind, args, meaning in COMMANDS:
        if kind not in aliases:  # Turned off (e.g. !scene in Preferences → OBS).
            continue
        names = aliases[kind]
        extra = f" (also {', '.join(prefix + n for n in names[1:])})" if len(names) > 1 else ""
        rows.append((f"{prefix}{names[0]}{args}", meaning + extra))
    return rows


def length(seconds: int | None) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds else ""


def read_only_table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(table.fontMetrics().height() + 8)
    return table


class MusicPage(QWidget):
    def __init__(self, controller: Controller, actions: AppActions) -> None:
        super().__init__()
        self.controller = controller
        grid = QGridLayout(self)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)

        # Now playing.
        playing = QGroupBox("Now playing")
        form = QFormLayout(playing)
        self.song_title = QLabel("Nothing playing")
        self.song_title.setWordWrap(True)
        font = self.song_title.font()
        font.setBold(True)
        self.song_title.setFont(font)
        form.addRow("Title:", self.song_title)
        self.song_meta = QLabel("")
        form.addRow("Requested by:", self.song_meta)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(controller.settings.volume)
        self.volume_value = QLabel(f"{controller.settings.volume}%")
        self.volume_value.setMinimumWidth(self.volume_value.fontMetrics().horizontalAdvance("100%"))
        self.volume.valueChanged.connect(lambda v: self.volume_value.setText(f"{v}%"))
        self.volume.sliderReleased.connect(lambda: controller.set_volume(self.volume.value()))
        volume_row = QHBoxLayout()
        volume_row.addWidget(self.volume, 1)
        volume_row.addWidget(self.volume_value)
        form.addRow("Volume:", volume_row)
        form.addRow(buttons(action_button(actions.skip, "Skip")))
        grid.addWidget(playing, 0, 0)

        # Queue.
        queue = QGroupBox("Queue")
        layout = QVBoxLayout(queue)
        self.queue = read_only_table(["#", "Title", "Channel", "Requested by", "Length"])
        header = self.queue.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (0, 2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.queue.itemSelectionChanged.connect(lambda: self.remove.setEnabled(bool(self.queue.selectedItems())))
        layout.addWidget(self.queue, 1)
        self.request = QLineEdit()
        self.request.setPlaceholderText("Song - Artist, or a link")
        self.request.returnPressed.connect(self._request)
        add = QPushButton("Add")
        add.clicked.connect(self._request)
        self.remove = QPushButton("Remove")
        self.remove.clicked.connect(self._remove)
        row = QHBoxLayout()
        row.addWidget(QLabel("Add song:"))
        row.addWidget(self.request, 1)
        row.addWidget(add)
        row.addSpacing(12)
        row.addWidget(self.remove)
        row.addWidget(action_button(actions.clear_queue, "Clear"))
        layout.addLayout(row)
        layout.addWidget(note("Songs you add skip cooldowns and per-viewer limits."))
        grid.addWidget(queue, 1, 0)

        # Background playlist.
        playlist = QGroupBox("Background playlist")
        form = QFormLayout(playlist)
        self.playlist_link = QLineEdit(controller.settings.music_playlist_url)
        self.playlist_link.setPlaceholderText("https://open.spotify.com/playlist/…")
        self.playlist_link.returnPressed.connect(self._load_playlist)
        form.addRow("&Spotify link:", self.playlist_link)
        self.shuffle = QCheckBox("Shuffle")
        self.shuffle.setChecked(controller.settings.music_playlist_shuffle)
        form.addRow("", self.shuffle)
        self.playlist_state = QLabel("No playlist loaded")
        self.playlist_state.setWordWrap(True)
        form.addRow("Status:", self.playlist_state)
        self.load_playlist = QPushButton("Load")
        self.load_playlist.clicked.connect(self._load_playlist)
        form.addRow(buttons(action_button(actions.stop_playlist, "Stop"), self.load_playlist))
        form.addRow(note("Plays when nobody has requested anything; viewer requests go first. Reads Spotify's "
                         "public playlist page (no Spotify account), so only public playlists and about the first "
                         "50 songs. Each song is looked up on YouTube right before it plays."))
        grid.addWidget(playlist, 0, 1)

        # Chat commands.
        commands = QGroupBox("Chat commands")
        layout = QVBoxLayout(commands)
        table = read_only_table(["Command", "What it does"])
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setWordWrap(True)
        self.commands = table
        self._commands(controller.settings)
        layout.addWidget(table)
        layout.addWidget(note("Rename commands in File → Preferences → Commands; who can request, cooldowns and "
                              "limits are in Song requests. They work on TikTok, Twitch and YouTube."))
        grid.addWidget(commands, 1, 1)
        grid.setRowStretch(1, 1)

        controller.settings_changed.connect(self._commands)
        controller.queue_changed.connect(self._queue)
        controller.playlist_changed.connect(self._playlist)
        controller.busy_changed.connect(self._busy)
        controller.settings_changed.connect(lambda s: self.volume.setValue(s.volume) if not self.volume.isSliderDown() else None)
        self._queue(controller.queue_snapshot)
        self._playlist(controller.playlist_info)

    def _commands(self, settings) -> None:
        rows = command_rows(settings)
        self.commands.setRowCount(len(rows))
        for row_index, (command, meaning) in enumerate(rows):
            self.commands.setItem(row_index, 0, QTableWidgetItem(command))
            self.commands.setItem(row_index, 1, QTableWidgetItem(meaning))
        self.commands.resizeRowsToContents()

    def _request(self) -> None:
        text = self.request.text().strip()
        if text:
            self.controller.request_song(text)
            self.request.clear()

    def _remove(self) -> None:
        for row in sorted({index.row() for index in self.queue.selectedIndexes()}):
            item_id = self.queue.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if item_id is not None:
                self.controller.remove_song(item_id)

    def _load_playlist(self) -> None:
        link = self.playlist_link.text().strip()
        if link:
            self.controller.load_playlist(link, self.shuffle.isChecked())

    def _queue(self, snapshot: dict) -> None:
        current = snapshot.get("current")
        self.song_title.setText((current["title"] or current["video_id"]) if current else "Nothing playing")
        self.song_meta.setText(current["requested_by"] if current else "")
        upcoming = snapshot.get("upcoming", [])
        self.queue.setRowCount(len(upcoming))
        for row, item in enumerate(upcoming):
            cells = [str(row + 1), item["title"] or item["video_id"], item.get("channel", ""), item["requested_by"],
                     length(item.get("duration_s"))]
            for column, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, item["id"])
                if column in (0, 4):
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.queue.setItem(row, column, cell)
        self.remove.setEnabled(False)

    def _playlist(self, info: dict | None) -> None:
        if info is None:
            self.playlist_state.setText("No playlist loaded")
        else:
            upcoming = f"; next: {info['next']}" if info["next"] else ""
            count = f" ({info['count']} songs)" if info.get("count") else ""
            self.playlist_state.setText(f"{info['name']}{count}{upcoming}")

    def _busy(self, name: str, busy: bool) -> None:
        if name == "playlist":
            self.load_playlist.setEnabled(not busy)
            self.load_playlist.setText("Loading…" if busy else "Load")
