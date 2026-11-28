"""App appearance: dark, light, a season, a holiday, or "Seasonal" (Preferences → Appearance).

Both modes are Qt's Fusion style (through a thin proxy for dock titles and
spacing), a matching ``QPalette``, and one app-wide stylesheet, built from one
of the palettes below. Change a colour there and the whole app follows.

Rules the stylesheet follows: content (chat, stream stats) stands out, the UI
around it stays quiet. Colour only where it means something: orange for the
main action and selection, red for live and danger, green for healthy, yellow
for a warning. Panels are told apart by background or a 1px divider, never by
boxes inside boxes. Corners are 10px (cards 14px), spacing comes in steps of 8px.

Buttons take a style from Qt properties, set with ``set_props``:
``primary`` (orange), ``live`` (red, white text) and ``danger`` (red text).
``set_live`` switches a Start/Stop Streaming button between orange and red
with a pulsing dot. Code that paints its own colours reads ``colors()`` and,
if it keeps them, listens to ``theme_changed()``.

The stream overlays have their own themes (overlay/static/themes.css) and are
not affected.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QRectF, Qt, QTemporaryDir, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QProxyStyle,
    QStyle,
    QStyleFactory,
    QStyleOptionDockWidget,
    QWidget,
)

from ignite_live.core import seasons
from ignite_live.core.palettes import (  # noqa: F401 - re-exported
    AUTUMN,
    CHRISTMAS,
    DARK,
    HALLOWEEN,
    LIGHT,
    PALETTES,
    SPRING,
    SUMMER,
    THANKSGIVING,
    WINTER,
    Palette,
)
from ignite_live.core.events import ConnectionState


# Saved setting value -> name in Preferences. "Seasonal" picks a season or holiday by the date (core/seasons.py).
THEMES = {"dark": "Dark", "light": "Light", seasons.SEASONAL: seasons.SEASONAL_LABEL, "spring": "Spring",
          "summer": "Summer", "autumn": "Autumn", "winter": "Winter", "halloween": "Halloween",
          "thanksgiving": "Thanksgiving", "christmas": "Christmas"}

# The app icon keeps its own colours, so the logo stays the same in either mode.
LOGO_TILE = "#FF7A3D"
LOGO_EDGE = "#FF7A3D"
LOGO_TIP = "#1A0F08"
LOGO_BASE = "#1A0F08"

BODY_PX = 13  # Body text.
LABEL_PX = 11  # Captions: dock titles, table headers, pills.
RADIUS = 10

STATE_TEXT = {
    ConnectionState.CONNECTED: "Connected",
    ConnectionState.CONNECTING: "Connecting…",
    ConnectionState.DEGRADED: "Waiting",
    ConnectionState.ERROR: "Problem",
    ConnectionState.OFFLINE: "Off",
}

_base_font: QFont | None = None
_style: QStyle | None = None  # Kept referenced while the app uses it.
_active: Palette = DARK
_images: dict[str, QTemporaryDir] = {}  # Per mode: the stylesheet's small SVGs, drawn in its colours.


class _Notifier(QObject):
    changed = Signal(object)  # The new Palette.


_notifier: _Notifier | None = None


def theme_changed() -> Signal:
    """Emitted with the new ``Palette`` after the app switches between dark and light."""
    global _notifier
    if _notifier is None:
        _notifier = _Notifier()
    return _notifier.changed


def colors() -> Palette:
    """The palette in use."""
    return _active


def resolve_theme(name: str) -> str:
    """The palette to use: "seasonal" picks today's; an unknown name (e.g. an older "system") follows the desktop's
    colour scheme."""
    name = seasons.resolve(name)
    if name in PALETTES:
        return name
    hints = QGuiApplication.styleHints() if QGuiApplication.instance() is not None else None
    return "light" if hints is not None and hints.colorScheme() == Qt.ColorScheme.Light else "dark"


def tint(color: str, alpha: float) -> str:
    """``color`` at ``alpha`` opacity, as a stylesheet ``rgba()``."""
    c = QColor(color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {round(alpha * 255)})"


def tint_color(color: str | QColor, alpha: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(alpha)
    return c


def qpalette(p: Palette) -> QPalette:
    c = {name: QColor(getattr(p, name)) for name in (
        "window", "panel", "raised", "divider", "text", "text_muted", "text_disabled", "accent", "accent_text",
        "on_accent", "live", "panel_alt")}
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: c["panel"],
        QPalette.ColorRole.WindowText: c["text"],
        QPalette.ColorRole.Base: c["raised"],
        QPalette.ColorRole.AlternateBase: c["panel_alt"],
        QPalette.ColorRole.ToolTipBase: c["raised"],
        QPalette.ColorRole.ToolTipText: c["text"],
        QPalette.ColorRole.PlaceholderText: c["text_disabled"],
        QPalette.ColorRole.Text: c["text"],
        QPalette.ColorRole.Button: c["raised"],
        QPalette.ColorRole.ButtonText: c["text"],
        QPalette.ColorRole.BrightText: c["live"],
        QPalette.ColorRole.Highlight: c["accent"],
        QPalette.ColorRole.HighlightedText: c["on_accent"],
        QPalette.ColorRole.Link: c["accent_text"],
        QPalette.ColorRole.LinkVisited: c["accent_text"],
        QPalette.ColorRole.Accent: c["accent"],
        QPalette.ColorRole.Light: c["divider"],
        QPalette.ColorRole.Midlight: c["divider"],
        QPalette.ColorRole.Mid: c["divider"],
        QPalette.ColorRole.Dark: c["window"],
        QPalette.ColorRole.Shadow: QColor("#000000"),
    }
    for role, color in roles.items():
        palette.setColor(role, color)
    # Disabled labels are how the app draws notes and captions, so they stay readable in secondary text.
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, c["text_muted"])
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, c["text_disabled"])
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, c["divider"])
    return palette


def caption_font(base: QFont) -> QFont:
    """Small uppercase secondary-label font, for dock titles."""
    font = QFont(base)
    font.setPixelSize(LABEL_PX)
    font.setWeight(QFont.Weight.DemiBold)
    font.setCapitalization(QFont.Capitalization.AllUppercase)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
    return font


def tabular(font: QFont) -> QFont:
    """``font`` with fixed-width digits, so changing numbers don't jitter."""
    font = QFont(font)
    font.setFeature(QFont.Tag("tnum"), 1)
    return font


class EmberStyle(QProxyStyle):
    """Fusion, with spacing in 8px steps and small uppercase dock titles (stylesheets can't uppercase text)."""

    METRICS = {
        QStyle.PixelMetric.PM_LayoutHorizontalSpacing: 8,
        QStyle.PixelMetric.PM_LayoutVerticalSpacing: 8,
        QStyle.PixelMetric.PM_DockWidgetTitleMargin: 8,
    }
    MARGINS = {QStyle.PixelMetric.PM_LayoutLeftMargin, QStyle.PixelMetric.PM_LayoutTopMargin,
               QStyle.PixelMetric.PM_LayoutRightMargin, QStyle.PixelMetric.PM_LayoutBottomMargin}

    def __init__(self) -> None:
        super().__init__(QStyleFactory.create("Fusion"))

    def pixelMetric(self, metric, option=None, widget=None) -> int:  # noqa: N802 - Qt override
        if metric in self.METRICS:
            return self.METRICS[metric]
        if metric in self.MARGINS:  # Windows and dialogs get room to breathe; nested layouts stay compact.
            return 16 if widget is not None and widget.isWindow() else 8
        return super().pixelMetric(metric, option, widget)

    def styleHint(self, hint, option=None, widget=None, data=None) -> int:  # noqa: N802 - Qt override
        if hint == QStyle.StyleHint.SH_DockWidget_ButtonsHaveFrame:
            return 0  # The stylesheet draws them; with a frame, Qt would also shrink their icons to 10px.
        return super().styleHint(hint, option, widget, data)

    def drawControl(self, element, option, painter, widget=None) -> None:  # noqa: N802 - Qt override
        if element == QStyle.ControlElement.CE_DockWidgetTitle and isinstance(option, QStyleOptionDockWidget):
            painter.save()
            painter.fillRect(option.rect, QColor(_active.panel))
            if option.title:
                rect = self.subElementRect(QStyle.SubElement.SE_DockWidgetTitleBarText, option, widget)
                painter.setFont(caption_font(painter.font()))
                painter.setPen(QColor(_active.text_muted))
                flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextHideMnemonic
                painter.drawText(rect, flags, painter.fontMetrics().elidedText(
                    option.title, Qt.TextElideMode.ElideRight, rect.width()))
            painter.restore()
            return
        super().drawControl(element, option, painter, widget)


def _image_dir(p: Palette) -> str:
    if p.name not in _images:
        folder = _images[p.name] = QTemporaryDir()

        def line(color: str, width: float = 1.6) -> str:
            return f'fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"'

        muted = p.text_muted
        svgs = {
            "close": f'<path d="M4.5 4.5l7 7M11.5 4.5l-7 7" {line(muted)}/>',
            "dock_close": f'<path d="M3 3l10 10M13 3L3 13" {line(muted, 1.5)}/>',
            "dock_float": f'<rect x="2.5" y="2.5" width="11" height="11" rx="2" {line(muted, 1.5)}/>',
            "down": f'<path d="M4 6.5l4 4 4-4" {line(muted)}/>',
            "up": f'<path d="M4 9.5l4-4 4 4" {line(muted)}/>',
            "left": f'<path d="M9.5 4l-4 4 4 4" {line(muted)}/>',
            "right": f'<path d="M6.5 4l4 4-4 4" {line(muted)}/>',
            "check": f'<path d="M4 8.2l2.6 2.6L12 5.4" {line(p.on_accent, 2)}/>',
            "check_disabled": f'<path d="M4 8.2l2.6 2.6L12 5.4" {line(p.text_disabled, 2)}/>',
            "dash": f'<path d="M4.5 8h7" {line(p.on_accent, 2)}/>',
            "dot": f'<circle cx="8" cy="8" r="3" fill="{p.on_accent}"/>',
        }
        for name, body in svgs.items():
            svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">{body}</svg>'
            (Path(folder.path()) / f"{name}.svg").write_text(svg, "utf-8")
    return _images[p.name].path()


def stylesheet(p: Palette) -> str:
    img, r = _image_dir(p), f"{RADIUS}px"
    WINDOW, PANEL, RAISED, DIVIDER, PANEL_ALT, SELECTED, PRESSED = (  # noqa: N806 - read like the CSS they fill
        p.window, p.panel, p.raised, p.divider, p.panel_alt, p.selected, p.pressed)
    TEXT, TEXT_MUTED, TEXT_DISABLED = p.text, p.text_muted, p.text_disabled  # noqa: N806
    ACCENT, ACCENT_HOVER, ACCENT_PRESSED, ON_ACCENT = p.accent, p.accent_hover, p.accent_pressed, p.on_accent  # noqa: N806
    LIVE, LIVE_HOVER, LIVE_PRESSED, ON_LIVE = p.live, p.live_hover, p.live_pressed, p.on_live  # noqa: N806
    fields = ("QLineEdit", "QPlainTextEdit", "QTextEdit", "QAbstractSpinBox", "QComboBox")
    views = "QListView, QTreeView, QTableView"

    def each(state: str) -> str:
        return ", ".join(f"{field}{state}" for field in fields)

    def filled(selector: str, bg: str, fg: str, hover: str, pressed: str) -> str:
        """An orange or red filled button, for both push and tool buttons."""
        both = f"QPushButton{selector}, QToolButton{selector}"
        on = lambda state: f"QPushButton{selector}:{state}, QToolButton{selector}:{state}"  # noqa: E731
        return (f"{both} {{ background: {bg}; color: {fg}; font-weight: 600; }}\n"
                f"{on('hover')} {{ background: {hover}; }}\n"
                f"{on('pressed')} {{ background: {pressed}; }}\n"
                f"{on('disabled')} {{ background: {RAISED}; color: {TEXT_DISABLED}; }}\n")

    PRIMARY, PRIMARY_HOVER, PRIMARY_PRESSED, ON_PRIMARY = p.primary, p.primary_hover, p.primary_pressed, p.on_primary  # noqa: N806
    SIDEBAR, HANDLE, ACCENT_TEXT = p.sidebar, p.handle, p.accent_text  # noqa: N806
    return f"""
QMainWindow, QWidget#appContent, QWidget#page {{ background: {WINDOW}; }}
QMainWindow::separator {{ background: {DIVIDER}; width: 1px; height: 1px; }}
QDialog {{ background: {PANEL}; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {DIVIDER}; border-radius: {r}; padding: 4px 8px; }}

QMenuBar {{ background: {WINDOW}; padding: 4px 8px 0 8px; }}
QMenuBar::item {{ padding: 4px 8px; border-radius: {r}; background: transparent; color: {TEXT_MUTED}; }}
QMenuBar::item:selected, QMenuBar::item:pressed {{ background: {RAISED}; color: {TEXT}; }}
QMenu {{ background: {PANEL}; border: 1px solid {DIVIDER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: {r}; color: {TEXT}; }}
QMenu::item:selected {{ background: {RAISED}; }}
QMenu::item:disabled {{ color: {TEXT_DISABLED}; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::separator {{ height: 1px; background: {DIVIDER}; margin: 4px 6px; }}

QToolBar {{ background: {WINDOW}; border: none; border-bottom: 1px solid {DIVIDER}; padding: 8px; spacing: 4px; }}
QToolButton {{ background: transparent; color: {TEXT}; border: none; border-radius: {r}; padding: 4px 8px; }}
QToolButton:hover {{ background: {RAISED}; }}
QToolButton:pressed, QToolButton:checked {{ background: {PRESSED}; }}
QToolButton:disabled {{ color: {TEXT_DISABLED}; }}
QToolButton[popupMode="2"] {{ padding-right: 20px; }}
QToolButton::menu-indicator {{ image: url({img}/down.svg); subcontrol-position: right center; subcontrol-origin: padding;
                               width: 10px; right: 4px; }}
QToolButton[iconOnly="true"] {{ padding: 6px; }}
QToolButton[iconOnly="true"]::menu-indicator {{ image: none; width: 0; }}

QPushButton {{ background: {RAISED}; color: {TEXT}; border: 1px solid {DIVIDER}; border-radius: {r};
               padding: 7px 14px; min-height: 18px; font-weight: 500; }}
QPushButton:hover {{ background: {PRESSED}; border-color: {HANDLE}; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton:pressed, QPushButton:checked {{ background: {PRESSED}; }}
QPushButton:disabled {{ background: {RAISED}; color: {TEXT_DISABLED}; border-color: {DIVIDER}; }}
QPushButton::menu-indicator {{ image: url({img}/down.svg); subcontrol-position: right center; subcontrol-origin: padding;
                               width: 10px; right: 8px; }}
QPushButton[primary="true"], QToolButton[primary="true"] {{ background: {PRIMARY}; color: {ON_PRIMARY};
    border: 1px solid {PRIMARY}; font-weight: 600; }}
QPushButton[primary="true"]:hover, QToolButton[primary="true"]:hover {{ background: {PRIMARY_HOVER}; border-color: {PRIMARY_HOVER}; }}
QPushButton[primary="true"]:pressed, QToolButton[primary="true"]:pressed {{ background: {PRIMARY_PRESSED}; }}
QPushButton[primary="true"]:disabled, QToolButton[primary="true"]:disabled {{ background: {RAISED}; color: {TEXT_DISABLED};
    border-color: {DIVIDER}; }}
QDialogButtonBox QPushButton:default {{ background: {PRIMARY}; color: {ON_PRIMARY}; border-color: {PRIMARY}; font-weight: 600; }}
QDialogButtonBox QPushButton:default:hover {{ background: {PRIMARY_HOVER}; }}
{filled('[live="true"]', LIVE, ON_LIVE, LIVE_HOVER, LIVE_PRESSED)}
QPushButton[live="true"], QToolButton[live="true"] {{ border: 1px solid {LIVE}; }}
QPushButton[outline="true"] {{ background: transparent; color: {ACCENT_TEXT}; border-color: {ACCENT}; font-weight: 600; }}
QPushButton[outline="true"]:hover {{ background: {tint(ACCENT, 0.12)}; }}
QPushButton[ghost="true"], QToolButton[ghost="true"] {{ background: transparent; border-color: transparent; color: {TEXT_MUTED}; }}
QPushButton[ghost="true"]:hover, QToolButton[ghost="true"]:hover {{ background: {RAISED}; color: {TEXT}; }}
QPushButton[danger="true"], QToolButton[danger="true"] {{ color: {LIVE}; }}
QPushButton[danger="true"]:hover, QToolButton[danger="true"]:hover {{ background: {tint(LIVE, 0.12)}; border-color: {LIVE}; }}
QPushButton[danger="true"]:disabled, QToolButton[danger="true"]:disabled {{ color: {TEXT_DISABLED}; }}
QPushButton[pulse="true"] {{ padding-left: 26px; padding-right: 14px; }}
QToolButton[pulse="true"] {{ padding-left: 26px; }}
QPushButton[big="true"] {{ min-height: 30px; font-size: 14px; }}

QTabWidget::pane {{ border: none; border-top: 1px solid {DIVIDER}; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {TEXT_MUTED}; padding: 8px 2px; border: none;
                border-bottom: 2px solid transparent; margin-right: 18px; font-weight: 500; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom-color: {ACCENT}; }}
QTabBar::scroller {{ width: 48px; }}
QTabBar QToolButton {{ background: {PANEL}; border: none; border-radius: 0; padding: 0; }}
QTabBar QToolButton::left-arrow {{ image: url({img}/left.svg); width: 14px; height: 14px; }}
QTabBar QToolButton::right-arrow {{ image: url({img}/right.svg); width: 14px; height: 14px; }}
QTabBar::close-button {{ image: none; subcontrol-position: right; border-radius: 4px; }}
QTabBar::close-button:selected {{ image: url({img}/close.svg); }}

QGroupBox {{ border: none; border-top: 1px solid {DIVIDER}; margin-top: 24px; padding-top: 8px;
             font-size: {LABEL_PX}px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 0; padding: 0 0 4px 0;
                    color: {TEXT_MUTED}; }}

{", ".join(fields)} {{ background: {WINDOW}; color: {TEXT}; border: 1px solid {DIVIDER}; border-radius: {r};
    padding: 5px 8px; min-height: 20px; selection-background-color: {tint(ACCENT, 0.35)}; selection-color: {TEXT}; }}
{each(":hover")} {{ border-color: {HANDLE}; }}
{each(":focus")} {{ border-color: {ACCENT}; }}
{each(":disabled")} {{ background: transparent; color: {TEXT_DISABLED}; border-color: {DIVIDER}; }}
QLineEdit[readOnly="true"] {{ background: transparent; border-color: {DIVIDER}; color: {TEXT_MUTED}; }}
QTextEdit[readOnly="true"], QPlainTextEdit[readOnly="true"] {{ background: transparent; border-color: transparent; }}
QComboBox QLineEdit, QAbstractSpinBox QLineEdit {{ background: transparent; border: none; padding: 0; min-height: 0; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url({img}/down.svg); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {DIVIDER}; border-radius: {r}; padding: 4px;
                               selection-background-color: {RAISED}; selection-color: {TEXT}; outline: 0; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
    subcontrol-origin: border; width: 16px; margin: 0; border: none; background: transparent; }}
QAbstractSpinBox::up-arrow {{ image: url({img}/up.svg); width: 10px; height: 10px; }}
QAbstractSpinBox::down-arrow {{ image: url({img}/down.svg); width: 10px; height: 10px; }}

QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {TEXT_DISABLED}; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; background: {WINDOW};
                                                 border: 1px solid {HANDLE}; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {TEXT_MUTED}; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked, QCheckBox::indicator:indeterminate {{
    background: {PRIMARY}; border-color: {PRIMARY}; }}
QCheckBox::indicator:checked {{ image: url({img}/check.svg); }}
QCheckBox::indicator:indeterminate {{ image: url({img}/dash.svg); }}
QRadioButton::indicator:checked {{ image: url({img}/dot.svg); }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ background: transparent; border-color: {DIVIDER}; }}
QCheckBox::indicator:checked:disabled {{ background: {DIVIDER}; image: url({img}/check_disabled.svg); }}

{views} {{ background: {PANEL}; border: none; alternate-background-color: {PANEL_ALT}; outline: 0;
           gridline-color: {PANEL}; selection-background-color: {SELECTED}; selection-color: {TEXT}; }}
QListView::item, QTreeView::item {{ padding: 4px 8px; border-radius: {r}; }}
QListView::item:hover, QTreeView::item:hover {{ background: {RAISED}; }}
QListView::item:selected, QTreeView::item:selected, QTableView::item:selected {{
    background: {SELECTED}; color: {TEXT}; }}
QTableView::item {{ padding: 0 8px; }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{ background: {PANEL}; color: {TEXT_MUTED}; border: none; border-bottom: 1px solid {DIVIDER};
                        padding: 6px 8px; font-size: {LABEL_PX}px; font-weight: 600; }}
QTableCornerButton::section {{ background: {PANEL}; border: none; }}
QScrollArea {{ background: transparent; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {HANDLE}; border-radius: 3px; }}
QScrollBar::handle:vertical {{ min-height: 32px; }}
QScrollBar::handle:horizontal {{ min-width: 32px; }}
QScrollBar::handle:hover {{ background: {TEXT_DISABLED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QSlider::groove:horizontal {{ height: 4px; background: {DIVIDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {PRIMARY}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: {ACCENT_HOVER}; }}
QSlider::sub-page:horizontal:disabled {{ background: {DIVIDER}; }}
QSlider::handle:horizontal:disabled {{ background: {TEXT_DISABLED}; }}

QProgressBar {{ background: {DIVIDER}; border: none; border-radius: 3px; text-align: center; color: {TEXT}; max-height: 6px; }}
QProgressBar::chunk {{ background: {PRIMARY}; border-radius: 3px; }}
QSplitter::handle {{ background: {DIVIDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

QStatusBar {{ background: {WINDOW}; border-top: 1px solid {DIVIDER}; color: {TEXT_MUTED}; }}
QStatusBar::item {{ border: none; }}

QLabel[role="pageTitle"] {{ font-size: 24px; font-weight: 600; color: {TEXT}; }}
QLabel[role="pageSubtitle"] {{ color: {TEXT_MUTED}; }}
QLabel[role="sectionTitle"] {{ font-size: 16px; font-weight: 600; color: {TEXT}; }}
QLabel[role="cardTitle"] {{ font-size: 14px; font-weight: 600; color: {TEXT}; }}
QLabel[role="caption"] {{ font-size: {LABEL_PX}px; font-weight: 600; color: {TEXT_MUTED}; }}
QLabel[role="muted"] {{ color: {TEXT_MUTED}; }}
QLabel[role="value"] {{ font-size: 24px; font-weight: 600; color: {TEXT}; }}
QLabel[role="emptyTitle"] {{ font-size: 15px; font-weight: 600; color: {TEXT}; }}
QLabel[tone="healthy"] {{ color: {p.healthy}; }}
QLabel[tone="warning"] {{ color: {p.warning}; }}
QLabel[tone="error"] {{ color: {LIVE}; }}
QLabel[tone="accent"] {{ color: {ACCENT_TEXT}; }}
QLabel[tone="muted"] {{ color: {TEXT_MUTED}; }}

QFrame#card {{ background: {PANEL}; border: none; border-radius: 14px; }}
QFrame#cardHeader {{ background: transparent; border: none; border-bottom: 1px solid {DIVIDER}; }}
QFrame#inset {{ background: {WINDOW}; border: 1px solid {DIVIDER}; border-radius: 10px; }}
QFrame#sidebar {{ background: {SIDEBAR}; border: none; border-right: 1px solid {DIVIDER}; }}
QFrame#appHeader {{ background: {WINDOW}; border: none; }}
QFrame#hline {{ background: {DIVIDER}; border: none; max-height: 1px; min-height: 1px; }}
QFrame#vline {{ background: {DIVIDER}; border: none; max-width: 1px; min-width: 1px; }}
QFrame#pill {{ background: {RAISED}; border: none; border-radius: 13px; }}
QFrame#popover {{ background: {PANEL}; border: 1px solid {DIVIDER}; border-radius: 14px; }}
QFrame#toast {{ background: {RAISED}; border: 1px solid {DIVIDER}; border-radius: 8px; }}
QFrame#toast[level="error"] {{ border-color: {LIVE}; }}
QFrame#banner {{ background: {tint(LIVE, 0.12)}; border: 1px solid {LIVE}; border-radius: 8px; }}
QFrame#bannerInfo {{ background: {tint(ACCENT, 0.10)}; border: 1px solid {tint(ACCENT, 0.45)}; border-radius: 8px; }}
QListWidget#settingsNav, QListWidget#subNav {{ background: {WINDOW}; border: none; border-right: 1px solid {DIVIDER};
    padding: 12px 10px; }}
QListWidget#settingsNav::item, QListWidget#subNav::item {{ border-radius: 8px; padding-left: 8px; color: {TEXT_MUTED}; }}
QListWidget#settingsNav::item:hover, QListWidget#subNav::item:hover {{ background: {RAISED}; color: {TEXT}; }}
QListWidget#settingsNav::item:selected, QListWidget#subNav::item:selected {{ background: {RAISED}; color: {TEXT}; }}
QWidget#saveBar {{ background: {PANEL}; border-top: 1px solid {DIVIDER}; }}
QListWidget#overlayList {{ background: transparent; border: none; }}
QListWidget#overlayList::item {{ border: 1px solid transparent; border-radius: 8px; padding: 8px; margin-bottom: 2px; }}
QListWidget#overlayList::item:hover {{ background: {RAISED}; }}
QListWidget#overlayList::item:selected {{ background: {SELECTED}; border-color: {tint(ACCENT, 0.5)}; color: {TEXT}; }}
"""


def apply_theme(app: QApplication, name: str) -> None:
    """Switch the app to dark or light mode (at startup, or live from Preferences)."""
    global _base_font, _style, _active
    if _base_font is None:
        _base_font = QFont(app.font())  # The system font, kept; only its size changes.
        if "Inter" in QFontDatabase.families():  # Ignite's chosen font, when it's installed; the system font otherwise.
            _base_font.setFamily("Inter")
    _active = PALETTES[resolve_theme(name)]
    if _style is None:
        _style = EmberStyle()
        app.setStyle(_style)
    app.setPalette(qpalette(_active))
    body = QFont(_base_font)
    body.setPixelSize(BODY_PX)
    app.setFont(body)
    app.setStyleSheet(stylesheet(_active))
    theme_changed().emit(_active)


def set_props(widget: QWidget, **props: object) -> None:
    """Set style properties (e.g. ``primary=True``) and restyle the widget to match."""
    for key, value in props.items():
        widget.setProperty(key, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


class PulseDot(QWidget):
    """A softly pulsing dot at the left of a button while live (the stylesheet leaves room for it)."""

    SIZE = 8

    def __init__(self, button: QWidget) -> None:
        super().__init__(button)
        self.button = button
        self.live = False
        self.opacity = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.resize(self.SIZE, self.SIZE)
        self.pulse = QVariantAnimation(self, startValue=1.0, endValue=1.0, duration=1600, loopCount=-1)
        self.pulse.setKeyValueAt(0.5, 0.3)
        self.pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.pulse.valueChanged.connect(self._fade)
        button.installEventFilter(self)
        self.hide()

    def refresh(self) -> None:
        self.setVisible(self.live)
        self._place()

    def _fade(self, value: float) -> None:
        self.opacity = value
        self.update()

    def _place(self) -> None:
        self.move(11 if self.button.inherits("QPushButton") else 10, (self.button.height() - self.SIZE) // 2)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if watched is self.button and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._place()
        return False

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.pulse.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.pulse.stop()
        super().hideEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tint_color(_active.on_live, self.opacity))
        painter.drawEllipse(QRectF(0, 0, self.SIZE, self.SIZE))


def set_live(button: QWidget, live: bool) -> None:
    """The Start/Stop Streaming button: orange while ready, red with a pulsing dot while live."""
    dot = next((child for child in button.children() if isinstance(child, PulseDot)), None)
    if dot is None:
        dot = PulseDot(button)
    dot.live = live
    set_props(button, primary=not live, live=live, pulse=live)
    dot.refresh()


def state_color(state: ConnectionState, widget: QWidget) -> QColor:
    """Text colour for a connection state; neutral states use the normal text colour."""
    if state is ConnectionState.ERROR:
        return QColor(_active.live)
    if state is ConnectionState.CONNECTED:
        return QColor(_active.healthy)
    return widget.palette().color(QPalette.ColorRole.Text)


def warn_color(_widget: QWidget | None = None) -> QColor:
    """Yellow for "needs attention" values (e.g. OBS dropping frames), readable in either mode."""
    return QColor(_active.warning)
