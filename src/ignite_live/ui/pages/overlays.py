from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QSplitter,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import Controller
from ignite_live.core.seasons import SEASONAL, SEASONAL_LABEL
from ignite_live.overlay.server import PAGES, THEMES
from ignite_live.ui.goals import GoalsBox
from ignite_live.ui.simulate import sample_events
from ignite_live.ui.pages.music import read_only_table
from ignite_live.ui.pages.stream import note, scrolling
from ignite_live.ui.flow_layout import FlowLayout

DESCRIPTIONS = {
    "nowplaying": (
        "Plays the song requests, with the cover, title, requester, progress, what's next, and replies to "
        "viewers. Required for music. Songs come from YouTube Music and play in the cover square: YouTube's "
        "rules say it must stay visible, at least 200×200. In OBS, tick <b>Control audio via OBS</b> on this "
        "source. Added in more than one scene, only one copy plays the sound."
    ),
    "queue": "The next songs and who requested them.",
    "visualizer": (
        "Bars that move while a song plays and settle when it stops. YouTube doesn't let pages read its audio, "
        "so they're animated rather than audio-reactive. For bars that follow the real audio, add an OBS "
        "audio-visualizer plugin to the Now Playing overlay's audio."
    ),
    "chat": "Chat from TikTok, Twitch and YouTube on stream: comments, gifts, and follows. Lines fade out after a while.",
    "alerts": (
        "A pop-up for each gift and new follower, one at a time, plus the alerts and sounds of your action rules "
        "(Actions tab). In OBS, tick <b>Control audio via OBS</b> so the sounds reach your stream."
    ),
    "goals": "Progress bars for the goals below (likes, coins, follows, shares, or one gift).",
    "leaderboard": (
        "Top gifters, likers or chatters this stream or all time, or the most loyalty points. When first place "
        "changes it shows a callout, and “New #1” rules (Actions tab) run."
    ),
    "game": "Whichever viewer game is running (Games tab): tug-of-war, boss fight, or poll, then the winner. "
            "Hides itself when no game runs.",
}
OPTIONS = {
    "nowplaying": [],
    "queue": [("rows=8", "how many songs to list (1–15, default 5)")],
    "visualizer": [
        ("bars=64", "number of bars (8–128, default 48)"),
        ("style=mirror", "bars grow from the middle"),
        ("speed=1.5", "animation speed (0.5–2)"),
    ],
    "chat": [
        ("max=12", "lines on screen (1–30, default 8)"),
        ("fade=0", "seconds before a line disappears; 0 keeps them (default 45)"),
        ("gifts=0", "hide gifts"),
        ("follows=0", "hide follows"),
        ("platforms=all", "platform badge on every line (default: Twitch and YouTube only); 0 hides them"),
    ],
    "alerts": [
        ("seconds=8", "how long each alert shows (2–15, default 5)"),
        ("min=100", "only gifts worth at least this many diamonds"),
        ("follows=0", "no follow alerts"),
        ("sound=0", "don't play sounds (for a second copy, e.g. on the vertical canvas)"),
    ],
    "goals": [("goal=<id>", "show only one goal (Copy URL in the Goals box adds this)")],
    "leaderboard": [
        ("board=gifters", "gifters (default), likers, chatters, or points"),
        ("period=alltime", "all time instead of this stream (points are always all time)"),
        ("rows=3", "how many places (1–10, default 5)"),
        ("callout=0", "no “new #1” callout"),
    ],
    "game": [],
}


# Overlays with a Send Test button; the others have Preview with Sample Data.
TESTABLE = ("alerts", "chat", "goals", "leaderboard", "game")


class OverlaysPage(QWidget):
    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        layout = scrolling(self)

        theme_box = QGroupBox("Theme")
        form = QFormLayout(theme_box)
        self.theme = QComboBox()
        for key, name in {SEASONAL: SEASONAL_LABEL, **THEMES}.items():
            self.theme.addItem(name, key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(controller.settings.overlay_theme)))
        self.theme.activated.connect(self._theme_chosen)
        gallery = QPushButton("Browse Themes…")
        gallery.setToolTip("Every theme side by side with sample songs and chat; pick one there")
        gallery.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(controller.overlay.url("gallery"))))
        theme_row = QHBoxLayout()
        theme_row.addWidget(self.theme, 1)
        theme_row.addWidget(gallery)
        form.addRow("Overlay theme:", theme_row)
        form.addRow(note("Applies to every overlay at once, live. To give one overlay its own theme, choose it "
                         "under “Theme for this URL” before copying the URL."))
        layout.addWidget(theme_box)

        split = QSplitter(Qt.Orientation.Vertical)
        list_box = QGroupBox("Overlays")
        list_layout = QVBoxLayout(list_box)
        self.table = read_only_table(["Overlay", "Suggested size", "URL"])
        self.table.setSelectionMode(self.table.SelectionMode.SingleSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setRowCount(len(PAGES))
        for row, (page, (title, width, height)) in enumerate(PAGES.items()):
            name = QTableWidgetItem(title)
            name.setData(Qt.ItemDataRole.UserRole, page)
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(f"{width} × {height}"))
            self.table.setItem(row, 2, QTableWidgetItem(""))
        self.table.itemSelectionChanged.connect(self._selected)
        list_layout.addWidget(self.table, 1)

        self.url_theme = QComboBox()
        self.url_theme.addItem("Follow app setting", "")
        for key, name in THEMES.items():
            self.url_theme.addItem(name, key)
        self.url_theme.currentIndexChanged.connect(lambda _: self._fill_urls())
        copy = QPushButton("Copy URL")
        copy.clicked.connect(self._copy)
        preview = QPushButton("Preview with Sample Data")
        preview.setToolTip("Opens this overlay in your browser with fake songs and chat, so you can see it without going live")
        preview.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self._url(self._page()) + "&demo=1")))
        add = QPushButton("Add to OBS…")
        add.setToolTip("Add this overlay (or tick more, or all) to an OBS scene")
        add.clicked.connect(lambda: self.window().open_add_overlays([self._page()]))
        add_all = QPushButton("Add All to OBS…")
        add_all.clicked.connect(lambda: self.window().open_add_overlays())
        self.test = QPushButton("Send Test")
        self.test.clicked.connect(self._send_test)
        self.row_buttons = [copy, preview, self.test, add]
        self.add_all = add_all
        row = FlowLayout()  # Wraps in a narrow window rather than cutting off the buttons.
        theme_label = note("Theme for this URL:")
        theme_label.setWordWrap(False)
        row.addWidget(theme_label)
        row.addWidget(self.url_theme)
        for widget in self.row_buttons:
            row.addWidget(widget)
        row.addWidget(add_all)
        list_layout.addLayout(row)
        split.addWidget(list_box)
        split.addWidget(GoalsBox(controller))

        details = QGroupBox("Details")
        details_layout = QVBoxLayout(details)
        self.details = QTextBrowser()
        self.details.setOpenLinks(False)
        details_layout.addWidget(self.details)
        split.addWidget(details)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setStretchFactor(2, 2)
        layout.addWidget(split, 1)

        controller.settings_changed.connect(self._settings)
        self._fill_urls()
        self.table.selectRow(0)

    def _page(self) -> str:
        items = self.table.selectedItems()
        return items[0].tableWidget().item(items[0].row(), 0).data(Qt.ItemDataRole.UserRole) if items else "nowplaying"

    def _url(self, page: str) -> str:
        return self.controller.overlay.url(page, self.url_theme.currentData() or "")

    def _fill_urls(self) -> None:
        for row in range(self.table.rowCount()):
            page = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            self.table.item(row, 2).setText(self._url(page))
        self._selected()

    def _selected(self) -> None:
        has = bool(self.table.selectedItems())
        for widget in self.row_buttons:
            widget.setEnabled(has)
        page = self._page()
        testable = page in TESTABLE
        self.test.setEnabled(has and testable)
        self.test.setToolTip("Send a sample event to this overlay in OBS (it doesn't count anywhere)" if testable
                             else "Use Preview with Sample Data for this overlay")
        title, width, height = PAGES[page]
        html = [f"<h3>{title}</h3><p>{DESCRIPTIONS[page]}</p>",
                f"<p>Suggested Browser Source size: {width} × {height}. Overlays are transparent, so place and "
                "crop them freely in OBS.</p>"]
        if OPTIONS[page]:
            html.append("<p><b>URL options</b> (add to the end of the URL):</p><table cellpadding='3'>")
            html += [f"<tr><td><code>&amp;{opt}</code></td><td>{text}</td></tr>" for opt, text in OPTIONS[page]]
            html.append("</table>")
        html.append("<p>Any overlay: <code>&amp;theme=neon</code> pins its theme (" + ", ".join(THEMES) + "). "
                    "<code>&amp;demo=1</code> fills it with sample data for previews; don't use that one in OBS.</p>")
        self.details.setHtml("".join(html))

    def _send_test(self) -> None:
        page = self._page()
        if page == "goals":
            self.controller.test_goal()
        elif page == "leaderboard":
            self.controller.test_leaderboard()
        elif page == "game":
            self.controller.test_game("tug")
        else:
            self.controller.simulate(*sample_events(page))

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self._url(self._page()))
        self.controller.notice.emit("info", f"Copied the {PAGES[self._page()][0]} overlay URL.")

    def _theme_chosen(self) -> None:
        theme = self.theme.currentData()
        if theme != self.controller.settings.overlay_theme:
            self.controller.save_settings(replace(self.controller.settings, overlay_theme=theme))

    def _settings(self, settings) -> None:
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings.overlay_theme)))
