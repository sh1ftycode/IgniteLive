"""Analytics: your streams over a time range (totals, compared with the period before, and a trend chart),
then a full report for each stream (viewers, engagement, songs, games, clips). From the reports Ignite saves."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import Controller
from ignite_live.live import analytics
from ignite_live.ui.kit import Card, Legend, LineChart, MetricCard, PageContainer, StatTile, button, label, restyle_buttons
from ignite_live.ui.pages.music import read_only_table
from ignite_live.ui.pages.stream import note
from ignite_live.ui.theme import colors, theme_changed

MOMENT_COLORS = {"clip": "#7c6cff", "goal": "#59c27a", "gift": "#f5a623", "scene": "#8b9a93"}
RANGES = ((0, "All time"), (7, "Last 7 days"), (30, "Last 30 days"), (90, "Last 90 days"))
SUMMARY = (("streams", "radio", "Streams", "accent"), ("hours", "clock", "Hours streamed", "muted"),
           ("average", "users", "Avg. viewers", "accent"), ("peak", "eye", "Peak viewers", "accent"),
           ("follows", "heart", "New follows", "live"), ("coins", "coins", "Coins", "warning"))
TREND_METRICS = {
    "viewers": ("Viewers", (("Avg. viewers", "average_viewers"), ("Peak viewers", "peak_viewers"))),
    "growth": ("Follows and shares", (("Follows", "follows"), ("Shares", "shares"))),
    "chat": ("Comments and likes", (("Comments", "comments"), ("Likes", "likes"))),
    "gifts": ("Coins and gifts", (("Coins", "coins"), ("Gifts", "gifts"))),
}


class Section(QWidget):
    """A titled part of a report: a heading and its content, without another box around it."""

    def __init__(self, title: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(label(title, "cardTitle"))
        self.body = layout

    def restyle(self) -> None:
        pass


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


STAT_TILES = (("eye", "peak viewers"), ("chart", "average viewers"), ("heart", "new follows"),
             ("star", "likes"), ("zap", "coins"), ("chat", "comments"))


class StreamsPage(QWidget):
    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        self.data: dict | None = None
        self.path: Path | None = None
        self.reports: list[tuple[Path, dict, dict]] = []  # (path, data, summary), newest first.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page = PageContainer()
        outer.addWidget(page)

        # Range and summary.
        top = QHBoxLayout()
        top.setSpacing(8)
        self.range = QComboBox()
        self.range.setAccessibleName("Time range")
        for days, text in RANGES:
            self.range.addItem(text, days)
        self.range.currentIndexChanged.connect(lambda _i: self.refresh())
        top.addWidget(label("Time range", "muted"))
        top.addWidget(self.range)
        top.addStretch(1)
        self.export = button("Export CSV", "download", tip="Export the selected stream as a spreadsheet")
        self.export.clicked.connect(self._export)
        self.folder = button("Open Streams Folder", "folder-open")
        self.folder.clicked.connect(self._open_folder)
        top.addWidget(self.export)
        top.addWidget(self.folder)
        page.add_layout(top)
        summary = QHBoxLayout()
        summary.setSpacing(16)
        self.summary_tiles = {key: MetricCard(icon, text, tone) for key, icon, text, tone in SUMMARY}
        for tile in self.summary_tiles.values():
            summary.addWidget(tile, 1)
        page.add_layout(summary)

        # Over time.
        self.trend_card = Card("Performance over time", icon="trending-up")
        self.trend_metric = QComboBox()
        self.trend_metric.setAccessibleName("Metric")
        for key, (text, _series) in TREND_METRICS.items():
            self.trend_metric.addItem(text, key)
        self.trend_metric.currentIndexChanged.connect(lambda _i: self._fill_trend())
        self.trend_card.add_header_widget(self.trend_metric)
        self.trend_legend_row = QHBoxLayout()
        self.trend_legend_row.addStretch(1)
        self.trend_card.body.addLayout(self.trend_legend_row)
        self.trend_chart = LineChart("No streams in this range yet. A report is saved each time you stream.")
        self.trend_chart.setMinimumHeight(220)
        self.trend_card.body.addWidget(self.trend_chart)
        page.add(self.trend_card)

        # Each stream.
        split = QHBoxLayout()
        split.setSpacing(16)
        self.list_card = Card("Streams", icon="radio", padded=False)
        self.list = QListWidget()
        self.list.setObjectName("overlayList")
        self.list.setMinimumWidth(260)
        self.list.currentItemChanged.connect(lambda item, _old: self._open(item))
        self.list_card.body.addWidget(self.list, 1)
        self.list_card.setFixedWidth(300)
        split.addWidget(self.list_card)
        self.report_card = Card("Report", icon="file-text")
        self.report = QWidget()
        self.report_layout = QVBoxLayout(self.report)
        self.report_layout.setContentsMargins(0, 0, 0, 0)
        self.report_card.body.addWidget(self.report)
        split.addWidget(self.report_card, 1)
        page.add_layout(split)
        page.finish()
        self._build_report()
        controller.stream_report_ready.connect(lambda path: self.refresh(select=path))
        theme_changed().connect(self._restyle)
        self.refresh()
        self._restyle()

    def _restyle(self, _palette=None) -> None:
        for card in (self.drops_card, self.songs_card, self.games_box, self.clips_card, self.trend_card,
                     self.list_card, self.report_card):
            card.restyle()
        for tile in [*self.stat_tiles, *self.summary_tiles.values()]:
            tile.restyle()
        restyle_buttons(self.export, self.folder, self.delete)
        self._fill_trend()

    def _build_report(self) -> None:
        r = self.report_layout
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 16px; font-weight: 600")
        r.addWidget(self.title)
        self.stats = QGridLayout()
        self.stats.setHorizontalSpacing(8)
        self.stats.setVerticalSpacing(8)
        STATS_PER_ROW = 3
        self.stat_tiles = [StatTile(icon, label) for icon, label in STAT_TILES]
        for index, tile in enumerate(self.stat_tiles):
            self.stats.addWidget(tile, index // STATS_PER_ROW, index % STATS_PER_ROW)
        r.addLayout(self.stats)
        self.chart = Chart()
        r.addWidget(self.chart)
        r.addWidget(note("Line: viewers. Bars: comments and gifts per minute. Ticks: clips (orange), goals (green), "
                         "big gifts (amber), scene changes (grey)."))

        self.drops_card = Section("When viewers left")
        self.drops = QLabel()
        self.drops.setWordWrap(True)
        self.drops.setTextFormat(Qt.TextFormat.RichText)
        self.drops_card.body.addWidget(self.drops)
        r.addWidget(self.drops_card)

        self.songs_card = Section("Songs")
        self.songs_card.body.addWidget(note("Chat, likes and coins per minute while each song played. "
                                            "Engagement compares chat and likes with the rest of the stream: "
                                            "1.0× is usual."))
        self.songs = read_only_table(["Started", "Song", "Requested by", "Chat/min", "Likes/min", "Coins", "Engagement"])
        self.songs.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.songs.setMinimumHeight(180)
        self.songs_card.body.addWidget(self.songs)
        r.addWidget(self.songs_card)

        self.games_box = Section("Games")
        self.games_box.body.addWidget(note("The same for each game that ran (Games tab)."))
        self.games = read_only_table(["Started", "Game", "Chat/min", "Likes/min", "Coins", "Engagement"])
        self.games.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.games.setMinimumHeight(120)
        self.games_box.body.addWidget(self.games)
        r.addWidget(self.games_box)

        people = QHBoxLayout()
        self.gifters = read_only_table(["Top gifters", "Coins"])
        self.chatters = read_only_table(["Top chatters", "Messages"])
        for table in (self.gifters, self.chatters):
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            table.setMinimumHeight(160)
            people.addWidget(table)
        r.addLayout(people)

        self.clips_card = Section("Clips")
        self.clips = read_only_table(["At", "Why", "File"])
        self.clips.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.clips.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.clips.itemDoubleClicked.connect(self._open_clip)
        self.clips.setMinimumHeight(120)
        self.clips_card.body.addWidget(self.clips)
        self.clips_card.body.addWidget(note("Double-click a clip to open it."))
        r.addWidget(self.clips_card)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.delete = button("Delete Report", "trash", kind="danger", tip="Delete this stream's report (clips stay)")
        self.delete.clicked.connect(self._delete)
        actions.addWidget(self.delete)
        r.addLayout(actions)
        r.addStretch(1)

    def refresh(self, select: Path | None = None) -> None:
        current = select or self.path
        days = self.range.currentData()
        now = time.time()
        everything = []
        for path in self.controller.saved_streams():
            data = analytics.load(path)
            if data is not None:
                everything.append((path, data, analytics.summary(data)))
        start = now - days * 86400 if days else 0
        self.reports = [r for r in everything if r[1]["started"] >= start]
        before = [r for r in everything if days and start - days * 86400 <= r[1]["started"] < start]
        self._fill_summary(self.reports, before if days else None)
        self._fill_trend()
        self.list.blockSignals(True)
        self.list.clear()
        chosen = None
        for path, data, _summary in self.reports:
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

    def _fill_summary(self, reports: list, before: list | None) -> None:
        """Totals for the range, each compared with the period before it (when there is one)."""

        def totals(rows: list) -> dict:
            hours = sum(s["duration_min"] for _p, _d, s in rows) / 60
            average = sum(s["average_viewers"] for _p, _d, s in rows) / len(rows) if rows else 0
            return {"streams": len(rows), "hours": hours, "average": average,
                    "peak": max((s["peak_viewers"] for _p, _d, s in rows), default=0),
                    "follows": sum(s["follows"] for _p, _d, s in rows), "coins": sum(s["coins"] for _p, _d, s in rows)}

        now, then = totals(reports), totals(before) if before is not None else None
        text = {"streams": f"{now['streams']:,}", "hours": f"{now['hours']:.1f} h", "average": f"{now['average']:,.0f}",
                "peak": f"{now['peak']:,}", "follows": f"{now['follows']:,}", "coins": f"{now['coins']:,}"}
        for key, tile in self.summary_tiles.items():
            tile.set_value(text[key] if reports else "–")
            if then is None or not then["streams"] or not reports:
                tile.set_trend("")
                continue
            old, new = then[key], now[key]
            if not old:
                tile.set_trend("new" if new else "")
            else:
                change = (new - old) / old * 100
                tile.set_trend(f"{'+' if change >= 0 else '−'}{abs(change):.0f}%")
            tile.setToolTip(f"{tile.accessibleName()}: compared with the {self.range.currentText().lower()} before")

    def _fill_trend(self) -> None:
        key = self.trend_metric.currentData() or "viewers"
        _text, series = TREND_METRICS[key]
        rows = list(reversed(self.reports))  # Oldest first, left to right.
        labels = [time.strftime("%b %d", time.localtime(d["started"])).replace(" 0", " ") for _p, d, _s in rows]
        p = colors()
        palette = (p.accent, p.healthy)
        data = [(name, palette[i], [float(s[field]) for _p, _d, s in rows]) for i, (name, field) in enumerate(series)]
        self.trend_chart.set_data(labels, data)
        while self.trend_legend_row.count() > 1:
            item = self.trend_legend_row.takeAt(1)
            if item.widget():
                item.widget().hide()  # Off screen now, not when the deletion gets round to it.
                item.widget().deleteLater()
        self.trend_legend_row.addWidget(Legend([(name, ("accent", "healthy")[i]) for i, (name, _f) in enumerate(series)]))

    def _open(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        self._show(path, analytics.load(path))

    def _show(self, path: Path | None, data: dict | None) -> None:
        self.path, self.data = path, data
        self.export.setEnabled(data is not None)
        self.delete.setEnabled(data is not None)
        if data is None:
            self.title.setText("No streams yet")
            for tile in self.stat_tiles:
                tile.set_value("–")
                tile.setToolTip("")
            self.chart.set_data({})
            self.drops.setText("A report is recorded every time OBS streams. It shows up here when you stop.")
            for table in (self.songs, self.games, self.gifters, self.chatters, self.clips):
                table.setRowCount(0)
            return
        self.title.setText(stream_label(data).replace("\n", " · ") + (f" · {data['title']}" if data.get("title") else ""))
        total = analytics.summary(data)
        values = (f"{total['peak_viewers']:,}", f"{total['average_viewers']:,}", f"{total['follows']:,}",
                 f"{total['likes']:,}", f"{total['coins']:,}", f"{total['comments']:,}")
        tips = (f"Peak viewers, at {analytics.clock(total['peak_at'])}", "", "", "",
               f"Coins, from {total['gifts']:,} gifts", "")
        for tile, value, tip in zip(self.stat_tiles, values, tips):
            tile.set_value(value)
            if tip:
                tile.setToolTip(tip)
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
