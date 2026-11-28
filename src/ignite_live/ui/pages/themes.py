"""Themes: one look for the dashboard and the overlays.

Pick one of the 21 built-in themes, or make your own: five colours, a corner radius and a font, with a preview of
the dashboard and of the overlays before anything changes, and a check that the text can still be read. Your themes
are saved in the settings and can be exported to a file and imported again.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import Controller
from ignite_live.core import seasons, themes
from ignite_live.core.themes import Theme
from ignite_live.ui.kit import Card, PageContainer, Segmented, button, hline, label
from ignite_live.ui import background, lucide
from ignite_live.ui.theme import colors, set_props, theme_changed

COLUMNS = 5
COLOUR_ROWS = (("bg", "Background"), ("panel", "Panels"), ("accent", "Accent"), ("accent2", "Second accent"),
               ("text", "Text"))


def ago(when: float) -> str:
    if not when:
        return "Saved"
    seconds = max(0, time.time() - when)
    if seconds < 90:
        return "Edited just now"
    if seconds < 3600:
        return f"Edited {int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"Edited {int(seconds // 3600)} h ago"
    return f"Edited {int(seconds // 86400)} days ago"


def paint_swatch(painter: QPainter, rect: QRectF, theme: Theme, radius: float = 6) -> None:
    """Three blocks: the theme's background, then its two accents."""
    painter.save()
    clip = QPainterPath()
    clip.addRoundedRect(rect, radius, radius)
    painter.setClipPath(clip)
    painter.setPen(Qt.PenStyle.NoPen)
    fifth = rect.width() / 5
    for index, colour in enumerate((theme.bg, theme.accent, theme.accent2)):
        left = rect.left() if index == 0 else rect.left() + fifth * (index + 2)
        width = fifth * 3 if index == 0 else fifth
        painter.setBrush(QColor(colour))
        painter.drawRect(QRectF(left, rect.top(), width + 1, rect.height()))
    painter.restore()


class ThemeTile(QFrame):
    """A theme to pick: its colours as three blocks, its name, and whether it is seasonal."""

    clicked = Signal()

    def __init__(self, theme: Theme, subtitle: str = "") -> None:
        super().__init__()
        self.theme = theme
        self.subtitle = subtitle
        self.selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(112)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(theme.name + (f", {subtitle}" if subtitle else ""))
        self.setToolTip(f"Use {theme.name} for the dashboard and the overlays")

    def set_selected(self, on: bool) -> None:
        if on != self.selected:
            self.selected = on
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        p = colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor(p.accent if self.selected else p.divider), 2 if self.selected else 1))
        painter.setBrush(QColor(p.panel))
        painter.drawRoundedRect(rect, 10, 10)
        swatch = QRectF(rect.left() + 10, rect.top() + 10, rect.width() - 20, 54)
        paint_swatch(painter, swatch, self.theme)
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(p.text))
        painter.drawText(QRectF(rect.left() + 12, swatch.bottom() + 8, rect.width() - 24, 18),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self.theme.name)
        if self.subtitle:
            small = QFont(self.font())
            small.setPixelSize(11)
            painter.setFont(small)
            painter.setPen(QColor(p.text_muted))
            painter.drawText(QRectF(rect.left() + 12, swatch.bottom() + 26, rect.width() - 24, 16),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self.subtitle)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(event)


class ThemePreview(QWidget):
    """The dashboard or the overlays, drawn in a theme's colours: what you get if you press Apply."""

    def __init__(self) -> None:
        super().__init__()
        self.theme: Theme | None = None
        self.mode = "overlays"
        self.setMinimumSize(420, 300)

    def show_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.update()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.update()

    # Drawing helpers.

    def _text(self, painter: QPainter, rect: QRectF, text: str, colour: str, size: int = 12, bold: bool = False,
              align=Qt.AlignmentFlag.AlignLeft) -> None:
        font = QFont(self.font())
        font.setPixelSize(size)
        font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(colour))
        painter.drawText(rect, int(align | Qt.AlignmentFlag.AlignVCenter),
                         QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, int(rect.width())))

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        if self.theme is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 10, 10)
        painter.setClipPath(clip)
        if self.mode == "dashboard":
            self._dashboard(painter, self.theme)
        else:
            self._overlays(painter, self.theme)

    def _dashboard(self, painter: QPainter, theme: Theme) -> None:
        pal = themes.palette_for(theme)
        w, h = self.width(), self.height()
        painter.fillRect(self.rect(), QColor(pal.window))
        side = min(150.0, w * 0.26)
        painter.fillRect(QRectF(0, 0, side, h), QColor(pal.sidebar))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(pal.accent))
        painter.drawRoundedRect(QRectF(14, 14, 20, 20), 5, 5)
        self._text(painter, QRectF(42, 12, side - 50, 24), "Ignite", pal.text, 13, True)
        for index, name in enumerate(("Overview", "Stream Control", "Live Chat", "Alerts", "Analytics")):
            row = QRectF(8, 52 + index * 30, side - 16, 26)
            if index == 0:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(pal.raised))
                painter.drawRoundedRect(row, min(8, theme.radius), min(8, theme.radius))
            self._text(painter, QRectF(row.left() + 12, row.top(), row.width() - 14, row.height()), name,
                       pal.text if index == 0 else pal.text_muted, 12, index == 0)
        x = side + 18
        self._text(painter, QRectF(x, 14, w - x - 16, 26), "Overview", pal.text, 17, True)
        gap = 10.0
        card_w = (w - x - 16 - gap * 2) / 3
        radius = min(14, theme.radius)
        for index, (name, value) in enumerate((("Viewers", "1,284"), ("Chat per min", "96"), ("Gifts", "312"))):
            card = QRectF(x + index * (card_w + gap), 50, card_w, 70)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(pal.panel))
            painter.drawRoundedRect(card, radius, radius)
            self._text(painter, QRectF(card.left() + 12, card.top() + 8, card.width() - 20, 18), name, pal.text_muted, 11)
            self._text(painter, QRectF(card.left() + 12, card.top() + 28, card.width() - 20, 32), value,
                       pal.accent_text, 22, True)
        chat = QRectF(x, 132, w - x - 16, h - 132 - 56)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(pal.panel))
        painter.drawRoundedRect(chat, radius, radius)
        self._text(painter, QRectF(chat.left() + 14, chat.top() + 8, 100, 22), "Chat", pal.text, 13, True)
        for index, (who, what, colour) in enumerate((("ember", "!sr blinding lights", pal.text),
                                                     ("nightowl", "that clutch was insane", pal.text))):
            top = chat.top() + 38 + index * 38
            if top + 34 > chat.bottom():
                break
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(pal.raised))
            painter.drawEllipse(QPointF(chat.left() + 24, top + 14), 11, 11)
            self._text(painter, QRectF(chat.left() + 44, top, chat.width() - 60, 16), who, colour, 12, True)
            self._text(painter, QRectF(chat.left() + 44, top + 16, chat.width() - 60, 16), what, pal.text_muted, 12)
        go = QRectF(w - 128, h - 44, 112, 32)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(pal.primary))
        painter.drawRoundedRect(go, min(10, theme.radius), min(10, theme.radius))
        self._text(painter, go, "Go live", pal.on_primary, 13, True, Qt.AlignmentFlag.AlignHCenter)

    def _overlays(self, painter: QPainter, theme: Theme) -> None:
        w, h = self.width(), self.height()
        gradient = QLinearGradient(0, 0, w, h)
        gradient.setColorAt(0, QColor(theme.bg))
        gradient.setColorAt(1, QColor(themes.mix(theme.bg, theme.accent, 0.55)))
        painter.fillRect(self.rect(), gradient)
        radius = float(theme.radius)
        panel = QColor(theme.panel)
        panel.setAlphaF(0.92)

        def card(rect: QRectF, border: str | None = None) -> None:
            painter.setBrush(panel)
            painter.setPen(QPen(QColor(border), 2) if border else Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, radius, radius)

        alert = QRectF(w * 0.12, 22, w * 0.52, 36)
        card(alert, theme.accent)
        self._text(painter, alert, "nightowl sent a Galaxy", theme.text, 13, True, Qt.AlignmentFlag.AlignHCenter)
        goal = QRectF(w - 150 - 20, 18, 150, 62)
        card(goal)
        self._text(painter, QRectF(goal.left() + 12, goal.top() + 6, 126, 16), "Likes goal", theme.muted, 11)
        track = QRectF(goal.left() + 12, goal.top() + 26, 126, 6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(themes.mix(theme.panel, theme.text, 0.18)))
        painter.drawRoundedRect(track, 3, 3)
        painter.setBrush(QColor(theme.accent2))
        painter.drawRoundedRect(QRectF(track.left(), track.top(), track.width() * 0.82, 6), 3, 3)
        self._text(painter, QRectF(goal.left() + 12, goal.top() + 36, 126, 18), "412 / 500", theme.text, 12, True)
        chat = QRectF(20, h - 78, w * 0.5, 58)
        card(chat)
        self._text(painter, QRectF(chat.left() + 12, chat.top() + 8, 70, 18), "ember", theme.accent, 12, True)
        self._text(painter, QRectF(chat.left() + 66, chat.top() + 8, chat.width() - 78, 18), "!sr blinding lights",
                   theme.text, 12)
        self._text(painter, QRectF(chat.left() + 12, chat.top() + 30, 70, 18), "maya_k", theme.accent2, 12, True)
        self._text(painter, QRectF(chat.left() + 76, chat.top() + 30, chat.width() - 88, 18), "love the overlay",
                   theme.text, 12)
        song = QRectF(w - 190 - 20, h - 66, 190, 46)
        card(song)
        self._text(painter, QRectF(song.left() + 12, song.top() + 6, 166, 16), "Now playing", theme.muted, 11)
        self._text(painter, QRectF(song.left() + 12, song.top() + 22, 166, 18), "Blinding Lights", theme.text, 13, True)


class ColourRow(QWidget):
    """A colour: a swatch that opens the colour picker, its name, and its hex code to type."""

    changed = Signal(str)

    def __init__(self, text: str, value: str) -> None:
        super().__init__()
        self.value = value
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.swatch = QPushButton()
        self.swatch.setFixedSize(26, 26)
        self.swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.swatch.setToolTip(f"Choose the {text.lower()} colour")
        self.swatch.setAccessibleName(f"{text} colour")
        self.swatch.clicked.connect(self._pick)
        self.hex = QLineEdit()
        self.hex.setMaxLength(7)
        self.hex.setFixedWidth(84)
        self.hex.setAccessibleName(f"{text} hex code")
        self.hex.editingFinished.connect(self._typed)
        row.addWidget(self.swatch)
        row.addWidget(label(text), 1)
        row.addWidget(self.hex)
        self._show()

    def set_value(self, value: str) -> None:
        self.value = value
        self._show()

    def _show(self) -> None:
        self.swatch.setStyleSheet(f"QPushButton {{ background: {self.value}; border: 1px solid {colors().divider}; "
                                  "border-radius: 7px; padding: 0; min-height: 0; }")
        self.hex.setText(self.value.upper())

    def _pick(self) -> None:
        chosen = QColorDialog.getColor(QColor(self.value), self, "Choose a colour")
        if chosen.isValid():
            self._set(chosen.name())

    def _typed(self) -> None:
        text = self.hex.text().strip()
        text = text if text.startswith("#") else "#" + text
        if themes.HEX.match(text):
            self._set(text.lower())
        else:
            self._show()  # Not a colour: back to what it was.

    def _set(self, value: str) -> None:
        if value != self.value:
            self.value = value
            self.changed.emit(value)
        self._show()


class ThemesPage(QWidget):
    """The picker, the editor, and your own themes."""

    open_page_requested = Signal(str)

    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        self.draft: Theme | None = None  # The theme being edited, with the edits so far.
        self.saved: Theme | None = None  # What it was when the editor opened or last saved.
        self.is_new = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)
        self.stack.addWidget(self._picker())
        self.stack.addWidget(self._editor())
        controller.settings_changed.connect(self._settings_changed)
        theme_changed().connect(self._restyle)
        self._fill()

    # The picker.

    def _picker(self) -> QWidget:
        page = PageContainer()
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addStretch(1)
        top.addWidget(label("Match my overlays", "muted"))
        self.match = QCheckBox()
        self.match.setAccessibleName("Match my overlays")
        self.match.setToolTip("On: the dashboard wears the same theme as your overlays. Off: it keeps the dark or "
                              "light look from Settings → Appearance.")
        self.match.toggled.connect(self._match_toggled)
        top.addWidget(self.match)
        self.import_button = button("Import", "upload", tip="Add a theme from a file")
        self.import_button.clicked.connect(self._import)
        top.addWidget(self.import_button)
        self.create = button("Create a theme", "plus", kind="primary", tip="Start your own, from the theme you have now")
        self.create.clicked.connect(self._create)
        top.addWidget(self.create)
        page.add_layout(top)
        page.add_layout(self._background_section())

        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        for column in range(COLUMNS):
            self.grid.setColumnStretch(column, 1)
        self.tiles: dict[str, ThemeTile] = {}
        for index, (key, theme) in enumerate(themes.builtin_themes().items()):
            tile = ThemeTile(theme, "Seasonal" if theme.seasonal else "")
            tile.clicked.connect(lambda k=key: self._apply(k))
            self.grid.addWidget(tile, index // COLUMNS, index % COLUMNS)
            self.tiles[key] = tile
        page.add_layout(self.grid)

        self.season_panel = QFrame()
        self.season_panel.setObjectName("seasonPanel")
        self.season_panel.setStyleSheet(
            f"QFrame#seasonPanel {{ border: 1px dashed {colors().handle}; border-radius: 12px; }}")
        season = QHBoxLayout(self.season_panel)
        season.setContentsMargins(18, 16, 18, 16)
        season.addWidget(label("Switch with the season automatically. Ignite changes to the right seasonal theme by "
                               "date, and back again.", wrap=True), 1)
        self.seasonal = QCheckBox()
        self.seasonal.setAccessibleName("Switch with the season")
        self.seasonal.toggled.connect(self._season_toggled)
        season.addWidget(self.seasonal)
        page.add(self.season_panel)

        self.mine_card = Card("My themes")
        self.mine_rows = QVBoxLayout()
        self.mine_rows.setSpacing(0)
        self.mine_card.body.addLayout(self.mine_rows)
        self.mine_empty = label("Themes you make or import show up here. Create one from any theme above.", "muted",
                                wrap=True)
        self.mine_card.body.addWidget(self.mine_empty)
        page.add(self.mine_card)
        page.finish()
        return page

    # The dashboard background.

    def _background_section(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(16)
        card = Card("Background")
        self.bg_kind = label("", "muted")
        card.add_header_widget(self.bg_kind)
        self.bg_preview = BackgroundPreview()
        card.body.addWidget(self.bg_preview)
        picks = QHBoxLayout()
        picks.setSpacing(8)
        self.bg_picks: dict[str, PickButton] = {}
        none = PickButton("", "No background")
        none.clicked.connect(lambda: self._bg_choose(""))
        picks.addWidget(none)
        self.bg_picks[""] = none
        for key, name in background.PRESETS.items():
            pick = PickButton(f"preset:{key}", name)
            pick.clicked.connect(lambda _=False, k=f"preset:{key}": self._bg_choose(k))
            picks.addWidget(pick)
            self.bg_picks[f"preset:{key}"] = pick
        self.bg_file = PickButton("file", "Use your own picture")
        self.bg_file.clicked.connect(self._bg_upload)
        picks.addWidget(self.bg_file)
        picks.addStretch(1)
        card.body.addLayout(picks)
        self.bg_hue, self.bg_hue_text = self._bg_slider(card, "Hue", 0, 359, "°", "hue")
        self.bg_blur, self.bg_blur_text = self._bg_slider(card, "Blur", 0, 24, " px")
        self.bg_dim, self.bg_dim_text = self._bg_slider(card, "Dim", 0, 90, " %")
        card.body.addWidget(label("Image on the dashboard"))
        self.bg_mode = Segmented()
        for text, key in (("Full", "full"), ("Subtle", "subtle"), ("Off", "off")):
            self.bg_mode.addItem(text, key)
        self.bg_mode.currentIndexChanged.connect(lambda _i: self._bg_save(now=True))
        card.body.addWidget(self.bg_mode, 0, Qt.AlignmentFlag.AlignLeft)
        card.body.addWidget(label("Subtle keeps text easy to read during long streams. Full shows the whole picture "
                                  "behind the panels.", "muted", wrap=True))
        self.bg_card = card
        row.addWidget(card, 2)

        side = Card("Use the background on")
        use = QHBoxLayout()
        use.addWidget(label("Dashboard"), 1)
        self.bg_dashboard = QCheckBox()
        self.bg_dashboard.setAccessibleName("Use the background on the dashboard")
        self.bg_dashboard.toggled.connect(lambda _on: self._bg_save(now=True))
        use.addWidget(self.bg_dashboard)
        side.body.addLayout(use)
        side.body.addWidget(label("Overlays stay see-through: they sit on top of your scene in OBS, so a picture "
                                  "there would cover your stream.", "muted", wrap=True))
        self.bg_tip = QFrame()
        self.bg_tip.setObjectName("bgTip")
        tip = QHBoxLayout(self.bg_tip)
        tip.setContentsMargins(12, 10, 12, 10)
        tip_icon = QLabel()
        tip_icon.setPixmap(lucide.pixmap("lightbulb", colors().accent, 16))
        tip_icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        tip.addWidget(tip_icon)
        tip.addWidget(label("Blur and Dim keep text readable on busy pictures. If you pick a picture with lots of "
                            "detail, try Subtle.", wrap=True), 1)
        side.body.addWidget(self.bg_tip)
        side.body.addStretch(1)
        self.bg_side = side
        row.addWidget(side, 1)
        self._bg_timer = QTimer(self, interval=150, singleShot=True)  # Sliders save once they settle.
        self._bg_timer.timeout.connect(lambda: self._bg_save(now=True))
        return row

    def _bg_slider(self, card: Card, text: str, low: int, high: int, unit: str, name: str = "") -> tuple[QSlider, QLabel]:
        head = QHBoxLayout()
        head.addWidget(label(text, "muted"), 1)
        value = label("", "muted")
        head.addWidget(value)
        card.body.addLayout(head)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(low, high)
        slider.setAccessibleName(text)
        if name == "hue":
            slider.setObjectName("hueSlider")
            slider.setStyleSheet(
                "QSlider#hueSlider::groove:horizontal { height: 6px; border-radius: 3px; background: qlineargradient("
                "x1:0, y1:0, x2:1, y2:0, stop:0 #ff3b3b, stop:0.17 #ffd23b, stop:0.33 #3bff6a, stop:0.5 #3bf0ff, "
                "stop:0.67 #3b5bff, stop:0.83 #d03bff, stop:1 #ff3b3b); } "
                "QSlider#hueSlider::sub-page:horizontal { background: transparent; }")
        slider.valueChanged.connect(lambda v, t=value, u=unit: (t.setText(f"{v}{u}"), self._bg_timer.start()))
        card.body.addWidget(slider)
        return slider, value

    def _bg_choose(self, source: str) -> None:
        s = self.controller.settings
        if source == s.bg_source:
            return
        background.forget_file(s.bg_source, background.folder_of(self.controller.settings_path))
        self.controller.save_settings(replace(s, bg_source=source))

    def _bg_upload(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Choose a picture", "",
                                              "Pictures (*.png *.jpg *.jpeg *.webp *.bmp);;All files (*)")
        if name:
            self.use_picture(Path(name))

    def use_picture(self, path: Path) -> bool:
        folder = background.folder_of(self.controller.settings_path)
        try:
            source = background.add_file(path, folder)
        except ValueError as error:
            self.controller.notice.emit("error", str(error))
            return False
        s = self.controller.settings
        background.forget_file(s.bg_source, folder)  # The one it replaces, if that was an upload.
        mode = s.bg_mode if s.bg_mode != "off" else "subtle"
        self.controller.save_settings(replace(s, bg_source=source, bg_mode=mode))
        return True

    def _bg_save(self, now: bool = False) -> None:
        if getattr(self, "_bg_loading", False):
            return
        s = self.controller.settings
        new = replace(s, bg_hue=self.bg_hue.value(), bg_blur=self.bg_blur.value(), bg_dim=self.bg_dim.value(),
                      bg_mode=self.bg_mode.currentData() or "subtle", bg_dashboard=self.bg_dashboard.isChecked())
        if new != s:
            self.controller.save_settings(new)

    def _fill_background(self) -> None:
        s = self.controller.settings
        self._bg_loading = True
        try:
            for slider, text, value, unit in ((self.bg_hue, self.bg_hue_text, s.bg_hue, "°"),
                                              (self.bg_blur, self.bg_blur_text, s.bg_blur, " px"),
                                              (self.bg_dim, self.bg_dim_text, s.bg_dim, " %")):
                slider.setValue(value)
                text.setText(f"{value}{unit}")
            self.bg_mode.setCurrentIndex(max(0, self.bg_mode.findData(s.bg_mode)))
            self.bg_dashboard.setChecked(s.bg_dashboard)
        finally:
            self._bg_loading = False
        kind = s.bg_source.partition(":")[0]
        self.bg_kind.setText({"preset": "Built in", "file": "Your picture"}.get(kind, "None"))
        for key, pick in self.bg_picks.items():
            pick.set_selected(key == s.bg_source)
        self.bg_file.set_selected(kind == "file")
        has = bool(s.bg_source)
        for widget in (self.bg_hue, self.bg_blur, self.bg_dim, self.bg_mode):
            widget.setEnabled(has)
        image = None
        if has:
            source = background.load_source(s.bg_source, background.folder_of(self.controller.settings_path))
            if source is not None:
                image = background.render(source, s.bg_source, s.bg_hue, s.bg_blur, s.bg_dim)
        self.bg_preview.set_picture(image)

    def _fill(self) -> None:
        s = self.controller.settings
        self.match.blockSignals(True)
        self.match.setChecked(s.theme_match_overlays)
        self.match.blockSignals(False)
        self.seasonal.blockSignals(True)
        self.seasonal.setChecked(s.overlay_theme == seasons.SEASONAL)
        self.seasonal.blockSignals(False)
        current = seasons.resolve(s.overlay_theme)
        for key, tile in self.tiles.items():
            tile.set_selected(not s.theme_active and key == current)
        self._fill_mine()
        self._fill_background()

    def _fill_mine(self) -> None:
        while self.mine_rows.count():
            item = self.mine_rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        s = self.controller.settings
        self.mine_empty.setVisible(not s.custom_themes)
        for index, item in enumerate(s.custom_themes):
            theme = themes.from_dict(item)
            self.mine_rows.addWidget(self._mine_row(theme, theme.key == s.theme_active, index > 0))

    def _mine_row(self, theme: Theme, active: bool, divider: bool) -> QWidget:
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        if divider:
            column.addWidget(hline())
        row = QHBoxLayout()
        row.setContentsMargins(0, 10, 0, 10)
        row.setSpacing(14)
        swatch = ThemeSwatch(theme)
        row.addWidget(swatch)
        text = QVBoxLayout()
        text.setSpacing(1)
        name = QLabel(theme.name)
        name.setStyleSheet("font-weight: 600;")
        text.addWidget(name)
        text.addWidget(label(ago(theme.edited), "muted"))
        row.addLayout(text, 1)
        if active:
            badge = QLabel("In use")
            badge.setStyleSheet(f"color: {colors().healthy}; background: {themes.mix(colors().panel, colors().healthy, 0.15)};"
                                "border-radius: 10px; padding: 2px 10px; font-size: 11px; font-weight: 600;")
            row.addWidget(badge)
        else:
            use = button("Use", tip=f"Use {theme.name} for the dashboard and the overlays")
            use.clicked.connect(lambda _=False, k=theme.key: self._apply(k))
            row.addWidget(use)
        more = QToolButton()
        more.setText("···")
        more.setToolTip("More")
        more.setAccessibleName(f"More for {theme.name}")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        menu.addAction("Edit", lambda k=theme.key: self._edit(k))
        menu.addAction("Duplicate", lambda k=theme.key: self._duplicate(k))
        menu.addAction("Export…", lambda k=theme.key: self._export(k))
        menu.addSeparator()
        menu.addAction("Delete", lambda k=theme.key: self._delete(k))
        more.setMenu(menu)
        row.addWidget(more)
        column.addLayout(row)
        return holder

    # Applying and saving.

    def _save(self, **changes) -> None:
        self.controller.save_settings(replace(self.controller.settings, **changes))

    def _apply(self, key: str) -> None:
        s = themes.applied(self.controller.settings, key)
        if s is not self.controller.settings:
            self.controller.save_settings(s)

    def _match_toggled(self, on: bool) -> None:
        self._save(theme_match_overlays=on)

    def _season_toggled(self, on: bool) -> None:
        s = self.controller.settings
        if on:
            self.controller.save_settings(replace(themes.applied(s, "minimal") if s.theme_active else s,
                                                  overlay_theme=seasons.SEASONAL, theme_active=""))
        else:
            self._apply(seasons.resolve(seasons.SEASONAL))

    def _settings_changed(self, _settings) -> None:
        self._fill()

    def _custom(self, key: str) -> dict | None:
        return next((t for t in self.controller.settings.custom_themes if t["id"] == key), None)

    def _duplicate(self, key: str) -> None:
        item = self._custom(key)
        if item is None:
            return
        copy = themes.copy_of(themes.from_dict(item), f"{item['name']} copy", self.controller.settings.custom_themes)
        copy["base"] = item["base"]
        self._save(custom_themes=[*self.controller.settings.custom_themes, copy])

    def _delete(self, key: str) -> None:
        item = self._custom(key)
        if item is None:
            return
        if QMessageBox.question(self, "Delete theme", f"Delete “{item['name']}”? This can't be undone, but you can "
                                                      "export it first.") != QMessageBox.StandardButton.Yes:
            return
        s = self.controller.settings
        if s.theme_active == key:  # Don't leave the overlays on a theme that is gone.
            s = themes.applied(s, item["base"] if item["base"] in themes.builtin_themes() else "minimal")
        self.controller.save_settings(replace(s, custom_themes=[t for t in s.custom_themes if t["id"] != key]))

    def _export(self, key: str) -> None:
        item = self._custom(key)
        if item is None:
            return
        name, _ = QFileDialog.getSaveFileName(self, "Export theme", f"{item['name']}.ignite-theme.json",
                                              "Ignite theme (*.json)")
        if name:
            try:
                Path(name).write_text(themes.export_json(themes.from_dict(item)), "utf-8")
            except OSError as error:
                self.controller.notice.emit("error", f"Couldn't save the theme file: {error.strerror or error}")
                return
            self.controller.notice.emit("info", f"Exported {item['name']}.")

    def _import(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Import theme", "", "Ignite theme (*.json);;All files (*)")
        if name:
            self.import_file(Path(name))

    def import_file(self, path: Path) -> bool:
        try:
            text = path.read_text("utf-8")
            item = themes.import_json(text, self.controller.settings.custom_themes)
        except (OSError, ValueError) as error:
            self.controller.notice.emit("error", str(error) if isinstance(error, ValueError)
                                        else "Couldn't read that file.")
            return False
        self._save(custom_themes=[*self.controller.settings.custom_themes, item])
        self.controller.notice.emit("info", f"Imported {item['name']}.")
        return True

    # The editor.

    def _editor(self) -> QWidget:
        page = PageContainer()
        bar = QHBoxLayout()
        bar.setSpacing(10)
        back = button("Themes", "chevron-left", kind="ghost", tip="Back to all themes")
        back.clicked.connect(self._back)
        bar.addWidget(back)
        self.name = QLineEdit()
        self.name.setMaxLength(40)
        self.name.setAccessibleName("Theme name")
        self.name.setStyleSheet("font-size: 20px; font-weight: 600; background: transparent; border: none;")
        self.name.textEdited.connect(self._name_edited)
        bar.addWidget(self.name, 1)
        self.state = label("", "muted")
        bar.addWidget(self.state)
        self.reset_button = button("Reset", "refresh-cw", kind="ghost", tip="Back to how it was when you opened it")
        self.reset_button.clicked.connect(self._reset)
        self.save_button = button("Save", tip="Keep the theme without using it yet")
        self.save_button.clicked.connect(self._save_draft)
        self.apply_button = button("Apply", kind="primary", tip="Save it and use it for the dashboard and the overlays")
        self.apply_button.clicked.connect(self._apply_draft)
        for widget in (self.reset_button, self.save_button, self.apply_button):
            bar.addWidget(widget)
        page.add_layout(bar)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        left = Card("Theme")
        left.setFixedWidth(330)
        left.body.addWidget(label("Start from", "muted"))
        self.start = QComboBox()
        self.start.setAccessibleName("Start from")
        self.start.activated.connect(self._start_from)
        left.body.addWidget(self.start)
        left.body.addWidget(label("Colours", "cardTitle"))
        self.colour_rows: dict[str, ColourRow] = {}
        for field, text in COLOUR_ROWS:
            row = ColourRow(text, "#000000")
            row.changed.connect(lambda value, f=field: self._colour(f, value))
            left.body.addWidget(row)
            self.colour_rows[field] = row
        left.body.addWidget(label("Shape", "cardTitle"))
        shape = QHBoxLayout()
        shape.addWidget(label("Corners", "muted"), 1)
        self.radius_text = label("", "muted")
        shape.addWidget(self.radius_text)
        left.body.addLayout(shape)
        self.radius = QSlider(Qt.Orientation.Horizontal)
        self.radius.setRange(0, themes.MAX_RADIUS)
        self.radius.setAccessibleName("Corner radius")
        self.radius.valueChanged.connect(self._radius)
        left.body.addWidget(self.radius)
        left.body.addWidget(label("Type", "cardTitle"))
        self.font = QComboBox()
        self.font.setAccessibleName("Overlay font")
        for family in themes.FONTS:
            self.font.addItem(family or "The theme's own font", family)
        self.font.activated.connect(self._font)
        left.body.addWidget(self.font)
        left.body.addWidget(label("The dashboard always uses Inter. The font is for your overlays.", "muted", wrap=True))
        left.body.addStretch(1)
        columns.addWidget(left, 0, Qt.AlignmentFlag.AlignTop)

        right = QVBoxLayout()
        right.setSpacing(12)
        modes = QHBoxLayout()
        self.mode = Segmented()
        self.mode.addItem("Overlays", "overlays")
        self.mode.addItem("Dashboard", "dashboard")
        self.mode.currentIndexChanged.connect(lambda _i: self.preview.set_mode(self.mode.currentData()))
        modes.addWidget(self.mode)
        modes.addStretch(1)
        right.addLayout(modes)
        self.preview = ThemePreview()
        right.addWidget(self.preview, 1)
        checks = QHBoxLayout()
        checks.setSpacing(12)
        self.text_check = CheckCard("Text on panels")
        self.accent_check = CheckCard("Accent on background")
        self.locked_check = CheckCard("Live, success, warning")
        self.locked_check.show_note("Locked colours", "lock")
        for card in (self.text_check, self.accent_check, self.locked_check):
            checks.addWidget(card, 1)
        right.addLayout(checks)
        self.tip = label("", "muted", wrap=True)
        right.addWidget(self.tip)
        right.addStretch(1)
        columns.addLayout(right, 1)
        page.add_layout(columns)
        page.finish()
        return page

    def _create(self) -> None:
        s = self.controller.settings
        source = themes.theme_of(s.theme_active, s.custom_themes) if s.theme_active else None
        source = source or themes.builtin_themes().get(seasons.resolve(s.overlay_theme)) or themes.builtin_themes()["minimal"]
        name = f"My {source.name}" if source.builtin else f"{source.name} copy"
        item = themes.copy_of(source, name, s.custom_themes)
        self._open(themes.from_dict(item), new=True)

    def _edit(self, key: str) -> None:
        item = self._custom(key)
        if item is not None:
            self._open(themes.from_dict(item), new=False)

    def _open(self, theme: Theme, new: bool) -> None:
        self.is_new = new
        self.saved = theme
        self.draft = theme
        self.start.blockSignals(True)
        self.start.clear()
        self.start.addItem("Keep these colours", "")
        for key, built in themes.builtin_themes().items():
            self.start.addItem(built.name, key)
        for item in self.controller.settings.custom_themes:
            if item["id"] != theme.key:
                self.start.addItem(item["name"] + " (mine)", item["id"])
        self.start.setCurrentIndex(0)
        self.start.blockSignals(False)
        self.name.setText(theme.name)
        self._load_controls()
        self.stack.setCurrentIndex(1)

    def _load_controls(self) -> None:
        d = self.draft
        for field, row in self.colour_rows.items():
            row.set_value(getattr(d, field))
        self.radius.blockSignals(True)
        self.radius.setValue(d.radius)
        self.radius.blockSignals(False)
        self.font.setCurrentIndex(max(0, self.font.findData(d.font)))
        self._refresh()

    def _refresh(self) -> None:
        d = self.draft
        self.radius_text.setText(f"{d.radius} px")
        self.preview.show_theme(d)
        checks = d.checks()
        for card, (ratio, ok), need in ((self.text_check, checks["text"], themes.CONTRAST_TEXT),
                                        (self.accent_check, checks["accent"], themes.CONTRAST_ACCENT)):
            card.show_ratio(ratio, ok, need)
        if not checks["text"][1]:
            self.tip.setText("The text is hard to read on the panels. Pick a lighter or darker text colour, or change "
                             "the panel colour: viewers (and you) should be able to read chat at a glance.")
        elif not d.dark:
            self.tip.setText("Light themes look great in daylight; on a dark OBS scene they can glare, so check them "
                             "in the overlays preview.")
        else:
            self.tip.setText("Tip: glow and busy colours look great on stream but can tire the eye in the dashboard.")
        changed = self.draft != self.saved
        self.state.setText("Unsaved changes" if changed else ("New theme" if self.is_new else
                                                               ago(self.draft.edited).replace("Edited", "Saved")))
        self.reset_button.setEnabled(changed)

    def _colour(self, field: str, value: str) -> None:
        self.draft = replace(self.draft, **{field: value})
        self._refresh()

    def _radius(self, value: int) -> None:
        self.draft = replace(self.draft, radius=value)
        self._refresh()

    def _font(self) -> None:
        self.draft = replace(self.draft, font=self.font.currentData() or "")
        self._refresh()

    def _name_edited(self, text: str) -> None:
        self.draft = replace(self.draft, name=text)
        self._refresh()

    def _start_from(self) -> None:
        key = self.start.currentData()
        if not key:
            return
        source = themes.theme_of(key, self.controller.settings.custom_themes)
        if source is None:
            return
        self.draft = replace(self.draft, bg=source.bg, panel=source.panel, accent=source.accent, accent2=source.accent2,
                             text=source.text, radius=source.radius, font=source.font,
                             base=source.key if source.builtin else source.base)
        self._load_controls()

    def _reset(self) -> None:
        if self.saved is not None:
            self.draft = self.saved
            self.name.setText(self.saved.name)
            self._load_controls()

    def _store(self) -> Theme | None:
        """Save the draft into the settings (a new one is added, an old one replaced); returns what was saved."""
        d = self.draft
        name = d.name.strip()
        if not name:
            self.controller.notice.emit("error", "Give the theme a name first.")
            self.name.setFocus()
            return None
        d = replace(d, name=name, edited=time.time())
        s = self.controller.settings
        item = themes.to_dict(d)
        item["base"] = d.base
        customs = [item if t["id"] == d.key else t for t in s.custom_themes]
        if all(t["id"] != d.key for t in s.custom_themes):
            customs.append(item)
        cleaned = themes.clean_custom(customs)
        if not any(t["id"] == d.key for t in cleaned):
            self.controller.notice.emit("error", "That theme can't be saved: check its colours.")
            return None
        self.controller.save_settings(replace(s, custom_themes=cleaned))
        saved = next(themes.from_dict(t) for t in cleaned if t["id"] == d.key)
        self.saved = self.draft = saved
        self.is_new = False
        self._refresh()
        return saved

    def _save_draft(self) -> None:
        saved = self._store()
        if saved is not None:
            self.controller.notice.emit("info", f"Saved {saved.name}.")
            # An applied theme that was edited shows its changes straight away.
            if self.controller.settings.theme_active == saved.key:
                self._apply(saved.key)

    def _apply_draft(self) -> None:
        saved = self._store()
        if saved is not None:
            self._apply(saved.key)
            self.controller.notice.emit("info", f"Using {saved.name} for the dashboard and your overlays.")

    def _back(self) -> None:
        if self.draft is not None and self.draft != self.saved:
            answer = QMessageBox.question(self, "Unsaved changes", "Leave without saving this theme?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.stack.setCurrentIndex(0)

    def _restyle(self, _palette=None) -> None:
        self.season_panel.setStyleSheet(
            f"QFrame#seasonPanel {{ border: 1px dashed {colors().handle}; border-radius: 12px; }}")
        self._fill()
        for row in self.colour_rows.values():
            row._show()
        self.mine_card.restyle()
        self.bg_card.restyle()
        self.bg_side.restyle()
        self.bg_tip.setStyleSheet(f"QFrame#bgTip {{ background: {themes.mix(colors().panel, colors().accent, 0.10)}; "
                                  "border-radius: 8px; }}")


class BackgroundPreview(QWidget):
    """The background as it will look, small; a quiet note when there isn't one."""

    def __init__(self) -> None:
        super().__init__()
        self._image = None
        self.setFixedHeight(96)

    def set_picture(self, image) -> None:
        self._image = image
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(rect, 8, 8)
        painter.setClipPath(clip)
        if self._image is None:
            painter.fillRect(rect, QColor(colors().raised))
            painter.setPen(QColor(colors().text_muted))
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), "No background")
            return
        ratio = self._image.width() / self._image.height()
        width = max(rect.width(), rect.height() * ratio)
        height = width / ratio
        painter.drawImage(QRectF(rect.center().x() - width / 2, rect.center().y() - height / 2, width, height), self._image)


class PickButton(QPushButton):
    """A background to choose: none (a crossed circle), a built-in picture, or your own (an upload mark)."""

    def __init__(self, source: str, tip: str) -> None:
        super().__init__()
        self.source = source
        self.selected = False
        self.setFixedSize(56, 38)
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._thumb = None
        if source.startswith("preset:"):
            self._thumb = background.preset_image(source.partition(":")[2], QSize(112, 76))

    def set_selected(self, on: bool) -> None:
        self.selected = on
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        p = colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        clip = QPainterPath()
        clip.addRoundedRect(rect, 8, 8)
        painter.save()
        painter.setClipPath(clip)
        if self._thumb is not None:
            painter.drawImage(rect, self._thumb)
        else:
            painter.fillRect(rect, QColor(p.raised))
        painter.restore()
        if self.source in ("", "file"):
            icon = lucide.pixmap("ban" if not self.source else "upload", p.text_muted, 18)
            painter.drawPixmap(int(rect.center().x() - 9), int(rect.center().y() - 9), icon)
        if self.source == "file" and not self.selected:
            painter.setPen(QPen(QColor(p.handle), 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 8, 8)
        elif self.selected or self.hasFocus():
            painter.setPen(QPen(QColor(p.accent), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 8, 8)


class ThemeSwatch(QWidget):
    """A theme's three colour blocks, small, for lists."""

    def __init__(self, theme: Theme) -> None:
        super().__init__()
        self.theme = theme
        self.setFixedSize(72, 40)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_swatch(painter, QRectF(self.rect()), self.theme, 7)


class CheckCard(QFrame):
    """One readout under the preview: a caption, then a ratio with Pass or Check, or a short note."""

    def __init__(self, caption: str) -> None:
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)
        layout.addWidget(label(caption, "muted"))
        row = QHBoxLayout()
        self.value = QLabel()
        self.value.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.chip = QLabel()
        row.addWidget(self.value)
        row.addWidget(self.chip)
        row.addStretch(1)
        layout.addLayout(row)

    def show_ratio(self, ratio: float, ok: bool, need: float) -> None:
        p = colors()
        self.value.setText(f"{ratio:.1f} : 1")
        colour = p.healthy if ok else p.warning
        self.chip.setText("Pass" if ok else f"Needs {need:g}")
        self.chip.setStyleSheet(f"color: {colour}; background: {themes.mix(p.panel, colour, 0.15)}; border-radius: 9px; "
                                "padding: 1px 9px; font-size: 11px; font-weight: 600;")

    def show_note(self, text: str, icon: str) -> None:
        self.value.setText(text)
        self.value.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.chip.setPixmap(lucide.pixmap(icon, colors().text_muted, 16))
