"""Overlays: Browser Sources for OBS (music, chat, alerts, goals, games), the goal bars and chat games."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import Controller
from ignite_live.core.seasons import SEASONAL, SEASONAL_LABEL
from ignite_live.overlay.server import PAGES, THEMES
from ignite_live.ui.goals import GoalsBox
from ignite_live.ui.kit import Card, PageContainer, StatusIndicator, button, label, restyle_buttons
from ignite_live.ui.pages.music import read_only_table
from ignite_live.ui.simulate import sample_events
from ignite_live.ui.theme import colors, theme_changed

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
    "chat": "Chat from TikTok, Twitch and YouTube on stream: comments, gifts, follows and joins. Lines fade out after a while.",
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
    "commands": "Every chat command viewers can use, as you've set them up: song requests, points (with their "
                "price), text to speech and your action rules, plus how to play while a game runs. A long list "
                "scrolls slowly.",
    "requests": "What happened to each song request: added (and where in the queue), refused (and why) or "
                "skipped, as cards that stay up long enough to read. Your viewers' answer, on stream. The words "
                "are your Messages (File → Preferences → Messages).",
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
        ("eventfade=10", "seconds for gift and joined lines, which flood in and fade sooner (default 6)"),
        ("gifts=0", "hide gifts"),
        ("follows=0", "hide follows"),
        ("joins=0", "hide “joined” lines"),
        ("platforms=all", "platform badge on every line (default: Twitch and YouTube only); 0 hides them"),
        ("size=1.5", "text bigger (or smaller, e.g. 0.8): it follows the Browser Source's width, so a small "
                     "source still has sharp text"),
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
        ("size=1.5", "text bigger (or smaller, e.g. 0.8): it follows the Browser Source's width, so a small "
                     "source still has sharp text"),
    ],
    "game": [],
    "commands": [
        ("mods=1", "also list the moderator-only commands"),
        ("group=Music", "show one group only: Music, Points, Text to speech, Moderators or More"),
        ("aliases=0", "hide other names for the same command"),
        ("speed=40", "how fast a long list scrolls, in pixels a second (5–200, default 30)"),
    ],
    "requests": [
        ("seconds=20", "how long each card stays (4–60 s, default 12)"),
        ("max=3", "how many cards at once (1–8, default 4)"),
        ("from=bottom", "new cards at the bottom, older ones above"),
        ("platform=0", "hide the platform (TikTok, Twitch, YouTube)"),
    ],
}


# Overlays with a Send Test button; the others have Preview with Sample Data.
TESTABLE = ("alerts", "chat", "goals", "leaderboard", "game")


def web_view_class():
    """Qt's embedded browser, or None where it isn't installed (the preview then opens your browser instead)."""
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except ImportError:
        return None
    return QWebEngineView


class LivePreview(QFrame):
    """The overlay running inside the app on a 16:9 canvas, filled with sample data (``&demo=1``).

    The browser is created the first time a preview is asked for: it's heavy, and most visits never need it."""

    CANVAS = (1920, 1080)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("inset")
        self.view = None
        self._url = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.hint = label("Press Preview with Sample Data to see this overlay here.", "muted", wrap=True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hint)
        self.hide()

    @staticmethod
    def available() -> bool:
        return web_view_class() is not None

    def show_url(self, url: str) -> bool:
        """Load ``url`` in the preview; False when there's no embedded browser."""
        cls = web_view_class()
        if cls is None:
            return False
        if self.view is None:
            self.view = cls()
            self.view.page().setBackgroundColor(QColor(24, 24, 28))
            self.layout().addWidget(self.view)
            self.hint.hide()
        self._url = url
        self.view.setUrl(QUrl(url))
        self.show()
        self._fit()
        return True

    def stop(self) -> None:
        """Unload the page so it stops running (and holding the overlay's socket) when it isn't wanted."""
        if self.view is not None:
            self.view.setUrl(QUrl("about:blank"))
        self.hide()

    def _fit(self) -> None:
        """16:9 at the width available, with the page zoomed so a full 1920-wide canvas fits."""
        width = max(1, self.width())
        self.setFixedHeight(max(120, round(width * self.CANVAS[1] / self.CANVAS[0])))
        if self.view is not None:
            self.view.setZoomFactor(width / self.CANVAS[0])

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._fit()


class CanvasPreview(QFrame):
    """The overlay's Browser Source, drawn to scale: how much of a 1080p canvas it takes. The real thing runs in
    the LivePreview beside it."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("inset")
        self.setMinimumHeight(170)
        self.size_ = (0, 0)
        self.name = ""

    def set_overlay(self, name: str, width: int, height: int) -> None:
        self.name, self.size_ = name, (width, height)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        p = colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect()).adjusted(16, 16, -16, -30)
        canvas_w, canvas_h = 1920, 1080
        scale = min(area.width() / canvas_w, area.height() / canvas_h)
        canvas = QRectF(0, 0, canvas_w * scale, canvas_h * scale)
        canvas.moveCenter(area.center())
        painter.setPen(QPen(QColor(p.divider), 1, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(canvas)
        width, height = self.size_
        if width and height:
            box = QRectF(0, 0, min(width, canvas_w) * scale, min(height, canvas_h) * scale)
            box.moveCenter(canvas.center())
            painter.setPen(QPen(QColor(p.accent), 1.5))
            painter.setBrush(QColor(p.selected))
            painter.drawRoundedRect(box, 4, 4)
            painter.setPen(QColor(p.text))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self.name)
        painter.setPen(QColor(p.text_muted))
        small = self.font()
        small.setPixelSize(11)
        painter.setFont(small)
        painter.drawText(QRectF(self.rect()).adjusted(16, 0, -16, -8), Qt.AlignmentFlag.AlignBottom
                         | Qt.AlignmentFlag.AlignHCenter, f"{width} × {height} on a 1920 × 1080 canvas")


class OverlaysPage(QWidget):
    """Overlays: every Browser Source Ignite serves, whether it's open in OBS, and its link and options; then the
    goal bars and chat games those overlays show."""

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
        self.tabs.addTab(self._overlays_tab(), "Overlays")
        goals = PageContainer()
        self.goals_card = Card("Goals", icon="target")
        self.goals = GoalsBox(controller)
        self.goals_card.body.addWidget(self.goals)
        goals.add(self.goals_card)
        goals.finish()
        self.tabs.addTab(goals, "Goals")
        from ignite_live.ui.pages.games import GamesPage

        self.games = GamesPage(controller)
        self.tabs.addTab(self.games, "Chat Games")
        controller.settings_changed.connect(self._settings)
        theme_changed().connect(self._restyle)
        self._status_timer = QTimer(self, interval=3000)
        self._status_timer.timeout.connect(self._statuses)
        self._fill_urls()
        self.table.selectRow(0)
        self._restyle()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._status_timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._status_timer.stop()
        self.live_preview.stop()  # Leaving the tab ends the preview.
        super().hideEvent(event)

    def show_tab(self, name: str) -> None:
        self.tabs.setCurrentIndex({"overlays": 0, "goals": 1, "games": 2}.get(name, 0))

    def _overlays_tab(self) -> QWidget:
        page = PageContainer()
        # Look: applies to every overlay.
        self.theme_box = Card("Look", icon="palette")
        look = QHBoxLayout()
        look.setSpacing(8)
        look.addWidget(label("Overlay theme", "muted"))
        self.theme = QComboBox()
        for key, name in {SEASONAL: SEASONAL_LABEL, **THEMES}.items():
            self.theme.addItem(name, key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(self.controller.settings.overlay_theme)))
        self.theme.activated.connect(self._theme_chosen)
        look.addWidget(self.theme, 1)
        self.gallery = button("Browse Themes", "palette", tip="Every theme side by side with sample songs and chat")
        self.gallery.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.controller.overlay.url("gallery"))))
        self.reload = button("Reload in OBS", "refresh-cw", tip="Reload every Ignite overlay in OBS (F5)")
        self.reload.clicked.connect(self.controller.refresh_overlays)
        self.add_all = button("Add All to OBS", "plus", kind="primary", tip="Choose a scene, and add every overlay to it")
        self.add_all.clicked.connect(lambda: self.window().open_add_overlays())
        for widget in (self.gallery, self.reload, self.add_all):
            look.addWidget(widget)
        self.theme_box.body.addLayout(look)
        self.theme_box.body.addWidget(label("Applies to every overlay at once, live. To give one overlay its own "
                                            "theme, pick it for that overlay's link below.", "muted", wrap=True))
        page.add(self.theme_box)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        self.list_box = Card("Overlays", icon="layers", padded=False)
        self.table = read_only_table(["Overlay", "Status", "Size"])
        self.table.setSelectionMode(self.table.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        self.table.setStyleSheet("QTableView { border: none; }")
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setRowCount(len(PAGES))
        for row, (page_key, (title, width, height)) in enumerate(PAGES.items()):
            name = QTableWidgetItem(title)
            name.setData(Qt.ItemDataRole.UserRole, page_key)
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(""))
            self.table.setItem(row, 2, QTableWidgetItem(f"{width} × {height}"))
        self.table.itemSelectionChanged.connect(self._selected)
        self.table.setMinimumHeight(420)
        self.list_box.body.addWidget(self.table, 1)
        columns.addWidget(self.list_box, 2)

        detail = QVBoxLayout()
        detail.setSpacing(16)
        self.details_box = Card("Overlay", icon="video")
        self.status = StatusIndicator("", "muted")
        self.details_box.add_header_widget(self.status)
        url_row = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setReadOnly(True)
        self.url.setAccessibleName("Browser Source URL")
        copy = button("Copy URL", "copy", tip="Paste it into a Browser Source in OBS")
        copy.clicked.connect(self._copy)
        url_row.addWidget(self.url, 1)
        url_row.addWidget(copy)
        self.details_box.body.addWidget(label("Browser Source URL", "caption"))
        self.details_box.body.addLayout(url_row)
        theme_row = QHBoxLayout()
        theme_row.addWidget(label("Theme for this URL", "muted"))
        self.url_theme = QComboBox()
        self.url_theme.addItem("Follow app setting", "")
        for key, name in THEMES.items():
            self.url_theme.addItem(name, key)
        self.url_theme.currentIndexChanged.connect(lambda _: self._fill_urls())
        theme_row.addWidget(self.url_theme, 1)
        self.details_box.body.addLayout(theme_row)
        self.live_preview = LivePreview()
        self.details_box.body.addWidget(self.live_preview)
        self.preview_area = CanvasPreview()
        self.details_box.body.addWidget(self.preview_area)
        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(8)
        add = button("Add to OBS", "plus", kind="primary", tip="Add this overlay (or tick more) to an OBS scene")
        add.clicked.connect(lambda: self.window().open_add_overlays([self._page()]))
        preview = button("Preview with Sample Data", "eye",
                         tip="Shows this overlay right here with sample songs and chat")
        preview.clicked.connect(self._preview)
        in_browser = button("Open in Browser", "external-link", tip="Opens the sample preview in your web browser")
        in_browser.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self._demo_url())))
        self.test = button("Send Test", "flask")
        self.test.clicked.connect(self._send_test)
        for widget in (add, preview, in_browser, self.test):
            buttons_row.addWidget(widget)
        buttons_row.addStretch(1)
        self.details_box.body.addLayout(buttons_row)
        self.row_buttons = [copy, preview, in_browser, self.test, add]
        self.description = QLabel()
        self.description.setWordWrap(True)
        self.description.setTextFormat(Qt.TextFormat.RichText)
        self.details_box.body.addWidget(self.description)
        detail.addWidget(self.details_box)

        self.options_box = Card("URL options", icon="sliders")
        self.details = QTextBrowser()
        self.details.setOpenLinks(False)
        self.details.setMinimumHeight(160)
        self.details.setFrameShape(QFrame.Shape.NoFrame)
        self.options_box.body.addWidget(self.details)
        detail.addWidget(self.options_box)
        detail.addStretch(1)
        columns.addLayout(detail, 3)
        page.add_layout(columns)
        page.finish()
        return page

    def _page(self) -> str:
        items = self.table.selectedItems()
        return items[0].tableWidget().item(items[0].row(), 0).data(Qt.ItemDataRole.UserRole) if items else "nowplaying"

    def _demo_url(self) -> str:
        return self._url(self._page()) + "&demo=1"

    def _preview(self) -> None:
        """Run the selected overlay in the app; with no embedded browser, in the user's browser instead."""
        if not self.live_preview.show_url(self._demo_url()):
            QDesktopServices.openUrl(QUrl(self._demo_url()))

    def _url(self, page: str) -> str:
        return self.controller.overlay.url(page, self.url_theme.currentData() or "")

    def _fill_urls(self) -> None:
        self._selected()

    def _statuses(self) -> None:
        open_now = self.controller.overlay.open_pages()
        p = colors()
        for row in range(self.table.rowCount()):
            page = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            item = self.table.item(row, 1)
            on = page in open_now
            item.setText("Open in OBS" if on else "Not open")
            item.setForeground(QColor(p.healthy if on else p.text_muted))
        page = self._page()
        on = page in open_now
        self.status.set("Open in OBS" if on else "Not open in OBS", "healthy" if on else "muted")

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
        self.details_box.title.setText(title)
        self.url.setText(self._url(page))
        self.preview_area.set_overlay(title, width, height)
        if not self.live_preview.isHidden():  # Follow the selection while a preview is showing.
            self.live_preview.show_url(self._demo_url())
        self.description.setText(DESCRIPTIONS[page])
        html = []
        if OPTIONS[page]:
            html.append("<p>Add to the end of the URL:</p><table cellpadding='4'>")
            html += [f"<tr><td><code>&amp;{opt}</code></td><td>{text}</td></tr>" for opt, text in OPTIONS[page]]
            html.append("</table>")
        html.append("<p>Any overlay: <code>&amp;theme=neon</code> pins its theme (" + ", ".join(THEMES) + "). "
                    "<code>&amp;demo=1</code> fills it with sample data for previews; don't use that one in OBS.</p>")
        self.details.setHtml("".join(html))
        self._statuses()

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

    def _restyle(self, _palette=None) -> None:
        for card in (self.theme_box, self.list_box, self.details_box, self.options_box, self.goals_card):
            card.restyle()
        restyle_buttons(self.gallery, self.reload, self.add_all, *self.row_buttons)
        self._statuses()
