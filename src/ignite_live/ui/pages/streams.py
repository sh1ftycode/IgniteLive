"""Streams tab: a report for each past stream (viewers, engagement, songs, clips)."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import Controller
from ignite_live.live import analytics
from ignite_live.ui.pages.music import read_only_table
from ignite_live.ui.pages.stream import note

MOMENT_COLORS = {"clip": "#7c6cff", "goal": "#59c27a", "gift": "#f5a623", "scene": "#8b9a93"}


def _duration(minutes: float) -> str:
    whole = int(round(minutes))
    return f"{whole // 60} h {whole % 60:02d} min" if whole >= 60 else f"{whole} min"


def stream_label(data: dict) -> str:
    started = time.strftime("%a %d %b %Y, %H:%M", time.localtime(data["started"]))
    total = analytics.summary(data)
    live = " (recording)" if data.get("ended") is None else ""
    return f"{started}{live}\n{_duration(total['duration_min'])} · peak {total['peak_viewers']:,} viewers"


class Chart(QWidget):
    """Viewers as a line, chat activity per minute as bars, and the stream's moments as ticks."""

    def __init__(self) -> None:
        super().__init__()
        self.data: dict = {}
        self.setMinimumHeight(220)

    def set_data(self, data: dict) -> None:
        self.data = data
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        minutes = [m for m in self.data.get("minutes", []) if isinstance(m, dict)]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        text, dim = palette.color(QPalette.ColorRole.Text), palette.color(QPalette.ColorRole.PlaceholderText)
        accent = palette.color(QPalette.ColorRole.Highlight)
        area = QRectF(self.rect()).adjusted(44, 12, -12, -28)
        if not minutes or area.width() < 50:
            painter.setPen(dim)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No minutes recorded yet.")
            return
        count = len(minutes)
        step = area.width() / max(count, 1)
        viewers = [analytics.number(m, "viewers") for m in minutes]
        activity = [analytics.number(m, "comments") + analytics.number(m, "gifts") for m in minutes]
        top_viewers, top_activity = max(max(viewers), 1), max(max(activity), 1)

        # Grid and labels.
        painter.setPen(QPen(dim, 1, Qt.PenStyle.DotLine))
        for fraction in (0.0, 0.5, 1.0):
            y = area.bottom() - fraction * area.height()
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            painter.drawText(QRectF(0, y - 8, 40, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"{round(top_viewers * fraction):,}")
        every = max(1, count // 6)
        for i in range(0, count, every):
            x = area.left() + i * step
            painter.drawText(QRectF(x - 30, area.bottom() + 6, 60, 16), Qt.AlignmentFlag.AlignCenter,
                             analytics.clock(i))

        # Chat activity bars.
        bars = QColor(dim)
        bars.setAlpha(110)
        for i, value in enumerate(activity):
            height = value / top_activity * area.height() * 0.6
            painter.fillRect(QRectF(area.left() + i * step + step * 0.15, area.bottom() - height,
                                    max(step * 0.7, 1), height), bars)

        # Viewers line.
        path = QPainterPath()
        for i, value in enumerate(viewers):
            point = QPointF(area.left() + (i + 0.5) * step, area.bottom() - value / top_viewers * area.height())
            path.moveTo(point) if i == 0 else path.lineTo(point)
        painter.setPen(QPen(accent, 2.2))
        painter.drawPath(path)

        # Moments.
        for moment in self.data.get("moments", []):
            if not isinstance(moment, dict) or moment.get("kind") not in MOMENT_COLORS:
                continue
            x = area.left() + analytics.number(moment, "t") * step
            painter.setPen(QPen(QColor(MOMENT_COLORS[moment["kind"]]), 2))
            painter.drawLine(QPointF(x, area.top()), QPointF(x, area.top() + 8))
        painter.setPen(text)


def stat(value: str, label: str) -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 12, 0)
    number = QLabel(value)
    number.setStyleSheet("font-size: 20px; font-weight: 700")
    layout.addWidget(number)
    layout.addWidget(note(label))
    return box


class StreamsPage(QWidget):
    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        self.data: dict | None = None
        self.path: Path | None = None
        layout = QHBoxLayout(self)
        split = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(lambda item, _old: self._open(item))
        left_layout.addWidget(self.list, 1)
        folder = QPushButton("Open Streams Folder")
        folder.clicked.connect(self._open_folder)
        left_layout.addWidget(folder)
        split.addWidget(left)

        self.report = QWidget()
        self.report_layout = QVBoxLayout(self.report)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.report)
        split.addWidget(scroll)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setSizes([260, 780])
        layout.addWidget(split)
        self._build_report()
        controller.stream_report_ready.connect(lambda path: self.refresh(select=path))
        self.refresh()

    def _build_report(self) -> None:
        r = self.report_layout
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 16px; font-weight: 600")
        r.addWidget(self.title)
        self.stats = QGridLayout()
        r.addLayout(self.stats)
        self.chart = Chart()
        r.addWidget(self.chart)
        r.addWidget(note("Line: viewers. Bars: comments and gifts per minute. Ticks: clips (orange), goals (green), "
                         "big gifts (amber), scene changes (grey)."))

        drops_box = QGroupBox("When viewers left")
        self.drops = QLabel()
        self.drops.setWordWrap(True)
        self.drops.setTextFormat(Qt.TextFormat.RichText)
        QVBoxLayout(drops_box).addWidget(self.drops)
        r.addWidget(drops_box)

        songs_box = QGroupBox("Songs")
        songs_layout = QVBoxLayout(songs_box)
        songs_layout.addWidget(note("Chat, likes and coins per minute while each song played. "
                                    "Engagement compares chat and likes with the rest of the stream: 1.0× is usual."))
        self.songs = read_only_table(["Started", "Song", "Requested by", "Chat/min", "Likes/min", "Coins", "Engagement"])
        self.songs.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.songs.setMinimumHeight(180)
        songs_layout.addWidget(self.songs)
        r.addWidget(songs_box)

        self.games_box = QGroupBox("Games")
        games_layout = QVBoxLayout(self.games_box)
        games_layout.addWidget(note("The same for each game that ran (Games tab)."))
        self.games = read_only_table(["Started", "Game", "Chat/min", "Likes/min", "Coins", "Engagement"])
        self.games.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.games.setMinimumHeight(120)
        games_layout.addWidget(self.games)
        r.addWidget(self.games_box)

        people = QHBoxLayout()
        self.gifters = read_only_table(["Top gifters", "Coins"])
        self.chatters = read_only_table(["Top chatters", "Messages"])
        for table in (self.gifters, self.chatters):
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            table.setMinimumHeight(160)
            people.addWidget(table)
        r.addLayout(people)

        clips_box = QGroupBox("Clips")
        clips_layout = QVBoxLayout(clips_box)
        self.clips = read_only_table(["At", "Why", "File"])
        self.clips.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.clips.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.clips.itemDoubleClicked.connect(self._open_clip)
        self.clips.setMinimumHeight(120)
        clips_layout.addWidget(self.clips)
        clips_layout.addWidget(note("Double-click a clip to open it."))
        r.addWidget(clips_box)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.export = QPushButton("Export CSV…")
        self.export.clicked.connect(self._export)
        self.delete = QPushButton("Delete Report…")
        self.delete.clicked.connect(self._delete)
        actions.addWidget(self.export)
        actions.addWidget(self.delete)
        r.addLayout(actions)
        r.addStretch(1)

    def refresh(self, select: Path | None = None) -> None:
        current = select or self.path
        self.list.blockSignals(True)
        self.list.clear()
        chosen = None
        for path in self.controller.saved_streams():
            data = analytics.load(path)
            if data is None:
                continue
            item = QListWidgetItem(stream_label(data))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.list.addItem(item)
            if current and Path(current) == path:
                chosen = item
        self.list.blockSignals(False)
        if chosen is None and self.list.count():
            chosen = self.list.item(0)
        if chosen is not None:
            self.list.blockSignals(True)
            self.list.setCurrentItem(chosen)
            self.list.blockSignals(False)
            self._open(chosen)
        else:
            self._show(None, None)

    def _open(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        self._show(path, analytics.load(path))

    def _show(self, path: Path | None, data: dict | None) -> None:
        self.path, self.data = path, data
        self.export.setEnabled(data is not None)
        self.delete.setEnabled(data is not None)
        while self.stats.count():
            old = self.stats.takeAt(0).widget()
            old.hide()  # Out of the layout but still drawn until Qt deletes it.
            old.deleteLater()
        if data is None:
            self.title.setText("No streams yet")
            self.chart.set_data({})
            self.drops.setText("A report is recorded every time OBS streams. It shows up here when you stop.")
            for table in (self.songs, self.games, self.gifters, self.chatters, self.clips):
                table.setRowCount(0)
            return
        self.title.setText(stream_label(data).replace("\n", " · ") + (f" · {data['title']}" if data.get("title") else ""))
        total = analytics.summary(data)
        tiles = [
            (f"{total['peak_viewers']:,}", f"peak viewers at {analytics.clock(total['peak_at'])}"),
            (f"{total['average_viewers']:,}", "average viewers"),
            (f"{total['follows']:,}", "new follows"),
            (f"{total['likes']:,}", "likes"),
            (f"{total['coins']:,}", f"coins from {total['gifts']:,} gifts"),
            (f"{total['comments']:,}", "comments"),
        ]
        for column, (value, label) in enumerate(tiles):
            self.stats.addWidget(stat(value, label), 0, column)
        self.chart.set_data(data)

        drops = analytics.drops(data)
        if drops:
            lines = []
            for drop in drops:
                context = []
                if drop["song"]:
                    context.append(f"during “{_escape(drop['song'])}”")
                context += [_escape(text) for text in drop["moments"]]
                lines.append(f"<b>−{drop['lost']:,} viewers</b> ({drop['before']:,} → {drop['before'] - drop['lost']:,}) "
                             f"at {analytics.clock(drop['from'])}–{analytics.clock(drop['to'] + 1)}"
                             + (": " + "; ".join(context) if context else ""))
            self.drops.setText("<br>".join(lines))
        else:
            self.drops.setText("No big drops: viewers stayed steady.")

        _fill_windows(self.songs, analytics.song_stats(data), requester=True)
        games = analytics.game_stats(data)
        _fill_windows(self.games, games, requester=False)
        self.games_box.setVisible(bool(games))

        for table, key in ((self.gifters, "top_gifters"), (self.chatters, "top_chatters")):
            people = [p for p in data.get(key, []) if isinstance(p, list) and len(p) == 2]
            table.setRowCount(len(people))
            for row, (name, amount) in enumerate(people):
                table.setItem(row, 0, QTableWidgetItem(str(name)))
                table.setItem(row, 1, QTableWidgetItem(f"{amount:,}" if isinstance(amount, int) else str(amount)))

        clips = analytics.clips(data)
        self.clips.setRowCount(len(clips))
        for row, clip in enumerate(clips):
            file = QTableWidgetItem(Path(clip.get("path") or "").name or "(vertical backtrack: see Aitum's folder)")
            file.setData(Qt.ItemDataRole.UserRole, clip.get("path") or "")
            self.clips.setItem(row, 0, QTableWidgetItem(analytics.clock(analytics.number(clip, "t"))))
            self.clips.setItem(row, 1, QTableWidgetItem(str(clip.get("text", ""))))
            self.clips.setItem(row, 2, file)

    def _open_clip(self, item: QTableWidgetItem) -> None:
        path = self.clips.item(item.row(), 2).data(Qt.ItemDataRole.UserRole)
        if path and Path(path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        elif path:
            self.controller.notice.emit("error", f"The clip isn't there any more: {path}")

    def _export(self) -> None:
        if self.data is None or self.path is None:
            return
        target, _ = QFileDialog.getSaveFileName(self, "Export stream as CSV",
                                                str(Path.home() / (self.path.stem + ".csv")), "CSV (*.csv)")
        if target:
            try:
                Path(target).write_text(analytics.to_csv(self.data), "utf-8")
            except OSError as error:
                self.controller.notice.emit("error", f"Couldn't export: {error}")
                return
            self.controller.notice.emit("info", f"Exported to {target}.")

    def _delete(self) -> None:
        if self.path is None:
            return
        if QMessageBox.question(self, "Delete report", "Delete this stream's report? Clips stay where they are.") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            self.path.unlink()
        except OSError as error:
            self.controller.notice.emit("error", f"Couldn't delete: {error}")
        self.path = None
        self.refresh()

    def _open_folder(self) -> None:
        self.controller.streams_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.controller.streams_dir)))


def _fill_windows(table, stats: list[dict], requester: bool) -> None:
    """Songs or games: rates per minute and engagement, with the best and lowest marked."""
    rated = [s for s in stats if s["engagement"] is not None]
    best = max(rated, key=lambda s: s["engagement"]) if len(rated) > 1 else None
    worst = min(rated, key=lambda s: s["engagement"]) if len(rated) > 1 else None
    table.setRowCount(len(stats))
    for row, item in enumerate(stats):
        engagement = f"{item['engagement']:.1f}×" if item["engagement"] is not None else "–"
        if item is best:
            engagement += "  best"
        elif item is worst:
            engagement += "  lowest"
        values = [analytics.clock(item["start"]), item["title"]]
        if requester:
            values.append(item["requested_by"])
        values += [f"{item['comments_per_min']:g}", f"{item['likes_per_min']:g}",
                   f"{round(item['coins_per_min'] * item['minutes']):,}", engagement]
        for column, value in enumerate(values):
            table.setItem(row, column, QTableWidgetItem(value))


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
