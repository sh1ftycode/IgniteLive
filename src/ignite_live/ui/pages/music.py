from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
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
from ignite_live.ui.live_widgets import MusicBar
from ignite_live.ui.pages.stream import buttons, note, scrolling

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


def read_only_table(headers: list[str], min_rows: int = 3) -> QTableWidget:
    """A select-only table. It keeps room for ``min_rows`` rows, so a small window scrolls the page
    rather than squashing the table to nothing."""
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    row = table.fontMetrics().height() + 8
    table.verticalHeader().setDefaultSectionSize(row)
    table.setMinimumHeight(table.horizontalHeader().sizeHint().height() + min_rows * row + 2 * table.frameWidth())
    return table


class MusicPage(QWidget):
    def __init__(self, controller: Controller, actions: AppActions) -> None:
        super().__init__()
        self.controller = controller
        grid = scrolling(self, QGridLayout)
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
        # Previous, play/pause and next, with the song's time. (The same buttons are on the pop-out chat.)
        self.playback = MusicBar(controller, volume=False)
        form.addRow("Playback:", self.playback)
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

        # Background music: a playlist, or autoplay.
        playlist = QGroupBox("Background music")
        form = self.background_form = QFormLayout(playlist)
        self.playlist_link = QLineEdit(controller.settings.music_playlist_url)
        self.playlist_link.setPlaceholderText("Spotify, YouTube or YouTube Music playlist link")
        self.playlist_link.returnPressed.connect(self._load_playlist)
        form.addRow("&Playlist:", self.playlist_link)
        self.shuffle = QCheckBox("Shuffle")
        self.shuffle.setChecked(controller.settings.music_playlist_shuffle)
        form.addRow("", self.shuffle)
        self.playlist_state = QLabel("No playlist loaded")
        self.playlist_state.setWordWrap(True)
        form.addRow("Status:", self.playlist_state)
        self.load_playlist = QPushButton("Load")
        self.load_playlist.clicked.connect(self._load_playlist)
        form.addRow(buttons(action_button(actions.stop_playlist, "Stop"), self.load_playlist))
        form.addRow(note("Plays when nobody has requested anything; viewer requests go first. Public playlists "
                         "(or your own, with YouTube Music connected below). Spotify: about the first 50 songs, each "
                         "looked up on YouTube Music right before it plays. YouTube: its songs play as they are."))
        # Your own YouTube Music account: its playlists and Liked Music (music/ytmusic_account.py).
        self.ytmusic_state = QLabel("Not connected")
        self.ytmusic_state.setWordWrap(True)
        form.addRow("YouTube Music:", self.ytmusic_state)
        self.ytmusic_playlists = QComboBox()
        self.ytmusic_playlists.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.ytmusic_playlists.setMinimumContentsLength(16)
        self.ytmusic_use = QPushButton("Use")
        self.ytmusic_use.setToolTip("Load this playlist as the background music")
        self.ytmusic_use.clicked.connect(self._use_ytmusic_playlist)
        library = QHBoxLayout()
        library.addWidget(self.ytmusic_playlists, 1)
        library.addWidget(self.ytmusic_use)
        self.ytmusic_library = QWidget()
        self.ytmusic_library.setLayout(library)
        library.setContentsMargins(0, 0, 0, 0)
        form.addRow("My playlists:", self.ytmusic_library)
        self.ytmusic_connect = QPushButton("Connect…")
        self.ytmusic_connect.clicked.connect(self._connect_ytmusic)
        self.ytmusic_refresh = QPushButton("Refresh")
        self.ytmusic_refresh.setToolTip("Read your playlists again")
        self.ytmusic_refresh.clicked.connect(lambda: controller.refresh_ytmusic())
        self.ytmusic_disconnect = QPushButton("Disconnect")
        self.ytmusic_disconnect.clicked.connect(controller.disconnect_ytmusic)
        form.addRow(buttons(self.ytmusic_disconnect, self.ytmusic_refresh, self.ytmusic_connect))
        self.radio = QCheckBox("Autoplay songs like chat's requests")
        self.radio.setChecked(controller.settings.music_radio)
        self.radio.setToolTip("With nothing queued and no playlist on, plays similar songs (YouTube Music radio) "
                              "to what chat requested most recently, so it stays in the same genre")
        self.radio.toggled.connect(lambda _on: self._save_radio())
        form.addRow("Autoplay:", self.radio)
        self.radio_start = QLineEdit(controller.settings.music_radio_start)
        self.radio_start.setPlaceholderText("A song or artist, until chat requests something")
        self.radio_start.editingFinished.connect(self._save_radio)
        form.addRow("&Start from:", self.radio_start)
        self.radio_state = note("")
        form.addRow(self.radio_state)
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
        controller.radio_changed.connect(self.radio_state.setText)
        controller.settings_changed.connect(self._radio_settings)
        controller.busy_changed.connect(self._busy)
        controller.settings_changed.connect(lambda s: self.volume.setValue(s.volume) if not self.volume.isSliderDown() else None)
        controller.ytmusic_changed.connect(self._ytmusic)
        self._queue(controller.queue_snapshot)
        self._playlist(controller.playlist_info)
        self.radio_state.setText(controller.radio_status)
        self._ytmusic(controller.ytmusic)

    def _connect_ytmusic(self) -> None:
        from ignite_live.ui.ytmusic import ConnectYouTubeMusic

        dialog = ConnectYouTubeMusic(self.controller, self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)  # Nothing of it (or the pasted text) is kept.
        dialog.exec()

    def _use_ytmusic_playlist(self) -> None:
        link = self.ytmusic_playlists.currentData()
        if link:
            self.playlist_link.setText(link)
            self.controller.load_playlist(link, self.shuffle.isChecked())

    def _ytmusic(self, account: dict | None) -> None:
        connected = account is not None
        playlists = account["playlists"] if connected else []
        if not connected:
            self.ytmusic_state.setText("Not connected. Connect it to use your own playlists and Liked Music.")
        elif account["error"]:
            self.ytmusic_state.setText(f"Connected, but: {account['error']}")
        else:
            who = account["name"] or "your account"
            self.ytmusic_state.setText(f"Connected as {who} ({len(playlists)} playlists)")
        self.ytmusic_playlists.clear()
        for playlist in playlists:
            self.ytmusic_playlists.addItem(playlist.name, playlist.link)
            self.ytmusic_playlists.setItemData(self.ytmusic_playlists.count() - 1, playlist.detail or playlist.name,
                                               Qt.ItemDataRole.ToolTipRole)
        self.background_form.setRowVisible(self.ytmusic_library, bool(playlists))  # The row and its label.
        self.ytmusic_connect.setText("Reconnect…" if connected else "Connect…")
        self.ytmusic_refresh.setVisible(connected)
        self.ytmusic_disconnect.setVisible(connected)

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

    def _save_radio(self) -> None:
        self.controller.set_radio(self.radio.isChecked(), self.radio_start.text())

    def _radio_settings(self, settings) -> None:
        self.radio.blockSignals(True)
        self.radio.setChecked(settings.music_radio)
        self.radio.blockSignals(False)
        if not self.radio_start.hasFocus():
            self.radio_start.setText(settings.music_radio_start)

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
            self.ytmusic_use.setEnabled(not busy)
        elif name == "ytmusic":
            self.ytmusic_refresh.setEnabled(not busy)
            self.ytmusic_connect.setEnabled(not busy)
