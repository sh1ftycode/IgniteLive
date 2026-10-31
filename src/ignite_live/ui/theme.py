"""App appearance: Qt's Fusion style with the Ignite palette and stylesheet, or the system theme.

Colours go through ``QPalette`` so every standard widget is drawn by the style
itself, and an app-wide stylesheet built from the same tokens shapes them:
cards for group boxes, underlined tabs, rounded fields and buttons, slim scroll
bars. The overlays use the same Ignite colours in their "Minimal" theme
(overlay/static/themes.css). The system theme drops both.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTemporaryDir
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QStyle, QStyleFactory, QWidget

from ignite_live.core.events import ConnectionState

COLORS = {
    "bg": "#0c0e18",
    "surface": "#12152a",
    "card": "#171b33",
    "raised": "#1e2340",
    "line": "#262c4a",
    "line_strong": "#343b5e",
    "text": "#e8ebf7",
    "muted": "#9aa1c2",
    "faint": "#5e6589",
    "accent": "#7c6cff",
    "accent_hover": "#9488ff",
    "accent_soft": "#2b2a5c",
    "accent_2": "#22c7e8",
    "accent_text": "#ffffff",
    "ok": "#5fd3a0",
    "warn": "#f0c060",
    "error": "#ff7a8c",
}

THEMES = {"ignite": "Ignite (midnight)", "system": "System theme"}

STATE_TEXT = {
    ConnectionState.CONNECTED: "Connected",
    ConnectionState.CONNECTING: "Connecting…",
    ConnectionState.DEGRADED: "Waiting",
    ConnectionState.ERROR: "Problem",
    ConnectionState.OFFLINE: "Off",
}

_system_style: str | None = None
_images: QTemporaryDir | None = None  # The stylesheet's small SVGs, drawn in the theme's colours.


def ignite_palette() -> QPalette:
    c = {name: QColor(value) for name, value in COLORS.items()}
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: c["surface"],
        QPalette.ColorRole.WindowText: c["text"],
        QPalette.ColorRole.Base: c["bg"],
        QPalette.ColorRole.AlternateBase: c["card"],
        QPalette.ColorRole.ToolTipBase: c["raised"],
        QPalette.ColorRole.ToolTipText: c["text"],
        QPalette.ColorRole.PlaceholderText: c["faint"],
        QPalette.ColorRole.Text: c["text"],
        QPalette.ColorRole.Button: c["raised"],
        QPalette.ColorRole.ButtonText: c["text"],
        QPalette.ColorRole.BrightText: c["error"],
        QPalette.ColorRole.Highlight: c["accent"],
        QPalette.ColorRole.HighlightedText: c["accent_text"],
        QPalette.ColorRole.Link: c["accent_2"],
        QPalette.ColorRole.LinkVisited: c["accent_2"],
        QPalette.ColorRole.Light: c["line_strong"],
        QPalette.ColorRole.Midlight: c["line_strong"],
        QPalette.ColorRole.Mid: c["line"],
        QPalette.ColorRole.Dark: c["bg"],
        QPalette.ColorRole.Shadow: QColor("#000000"),
    }
    for role, color in roles.items():
        palette.setColor(role, color)
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, c["faint"])
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, c["line_strong"])
    return palette


def _image_dir() -> str:
    global _images
    if _images is None:
        _images = QTemporaryDir()
        stroke = f'fill="none" stroke="{COLORS["muted"]}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"'
        svgs = {
            "close": f'<path d="M4.5 4.5l7 7M11.5 4.5l-7 7" {stroke}/>',
            "down": f'<path d="M4 6.5l4 4 4-4" {stroke}/>',
            "up": f'<path d="M4 9.5l4-4 4 4" {stroke}/>',
        }
        for name, body in svgs.items():
            svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">{body}</svg>'
            (Path(_images.path()) / f"{name}.svg").write_text(svg, "utf-8")
    return _images.path()


def stylesheet() -> str:
    c, img = COLORS, _image_dir()
    fields = "QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox"
    views = "QListView, QListWidget, QTreeView, QTreeWidget, QTableView, QTableWidget"
    return f"""
QMainWindow, QDialog {{ background: {c['surface']}; }}
QMainWindow::separator {{ background: {c['bg']}; width: 3px; height: 3px; }}
QToolTip {{ background: {c['raised']}; color: {c['text']}; border: 1px solid {c['line_strong']};
            border-radius: 6px; padding: 5px 8px; }}

QMenuBar {{ background: {c['bg']}; padding: 2px 6px; border-bottom: 1px solid {c['line']}; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 6px; background: transparent; }}
QMenuBar::item:selected {{ background: {c['raised']}; }}
QMenu {{ background: {c['raised']}; border: 1px solid {c['line_strong']}; border-radius: 8px; padding: 5px; }}
QMenu::item {{ padding: 6px 28px 6px 28px; border-radius: 5px; }}
QMenu::item:selected {{ background: {c['accent']}; color: {c['accent_text']}; }}
QMenu::item:disabled {{ color: {c['faint']}; }}
QMenu::separator {{ height: 1px; background: {c['line']}; margin: 5px 8px; }}

QToolBar {{ background: {c['bg']}; border: none; border-bottom: 1px solid {c['line']}; padding: 5px 8px; spacing: 3px; }}
QToolBar::separator {{ background: {c['line']}; width: 1px; margin: 4px 6px; }}
QToolButton {{ border: 1px solid transparent; border-radius: 6px; padding: 4px 7px; }}
QToolButton:hover {{ background: {c['raised']}; border-color: {c['line']}; }}
QToolButton:pressed, QToolButton:checked {{ background: {c['accent_soft']}; border-color: {c['accent']}; }}
QToolButton[popupMode="2"] {{ padding-right: 18px; }}
QToolButton::menu-indicator {{ image: url({img}/down.svg); subcontrol-position: right center; subcontrol-origin: padding;
                               width: 10px; right: 4px; }}
QToolButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; font-weight: 600; padding: 4px 12px; }}
QToolButton#primary:hover {{ background: {c['accent_hover']}; }}
QToolButton#primary:disabled {{ background: {c['raised']}; color: {c['faint']}; }}
QToolButton#danger {{ color: {c['error']}; border-color: {c['line_strong']}; font-weight: 600; }}
QToolButton#danger:hover {{ background: #3a1d2c; border-color: {c['error']}; }}

QTabWidget::pane {{ border: none; border-top: 1px solid {c['line']}; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 9px 12px 7px 14px;
                border: none; border-bottom: 2px solid transparent; margin-right: 2px; }}
QTabBar::tab:hover {{ color: {c['text']}; background: {c['card']}; }}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom-color: {c['accent']}; font-weight: 600; }}
QTabBar::close-button {{ image: url({img}/close.svg); subcontrol-position: right; border-radius: 4px; margin-left: 2px; }}
QTabBar::close-button:hover {{ background: {c['line_strong']}; }}

QDockWidget {{ color: {c['muted']}; }}
QDockWidget::title {{ background: {c['bg']}; padding: 7px 10px; text-align: left; border-bottom: 1px solid {c['line']}; }}

QGroupBox {{ background: {c['card']}; border: 1px solid {c['line']}; border-radius: 10px;
             margin-top: 24px; padding: 12px 10px 10px 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 6px; padding: 0 4px 2px 4px;
                    color: {c['muted']}; font-weight: 600; }}

QPushButton {{ background: {c['raised']}; border: 1px solid {c['line_strong']}; border-radius: 7px;
               padding: 5px 14px; min-height: 18px; }}
QPushButton:hover {{ border-color: {c['accent']}; }}
QPushButton:pressed, QPushButton:checked {{ background: {c['accent_soft']}; }}
QPushButton:default {{ background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']}; }}
QPushButton:default:hover {{ background: {c['accent_hover']}; }}
QPushButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']};
                       font-weight: 600; }}
QPushButton#primary:hover {{ background: {c['accent_hover']}; }}
QPushButton#danger {{ color: {c['error']}; }}
QPushButton#danger:hover {{ border-color: {c['error']}; }}
QPushButton:disabled {{ background: {c['surface']}; color: {c['faint']}; border-color: {c['line']}; }}

{fields} {{ background: {c['bg']}; border: 1px solid {c['line_strong']}; border-radius: 7px; padding: 4px 8px;
            selection-background-color: {c['accent']}; selection-color: {c['accent_text']}; min-height: 18px; }}
{fields.replace(",", ":focus,")}:focus {{ border-color: {c['accent']}; }}
{fields.replace(",", ":disabled,")}:disabled {{ background: {c['surface']}; color: {c['faint']}; border-color: {c['line']}; }}
QLineEdit[readOnly="true"] {{ background: {c['surface']}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url({img}/down.svg); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {c['raised']}; border: 1px solid {c['line_strong']}; border-radius: 6px;
                               selection-background-color: {c['accent']}; selection-color: {c['accent_text']};
                               outline: 0; padding: 3px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    border: none; width: 18px; background: transparent; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({img}/up.svg); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({img}/down.svg); width: 10px; height: 10px; }}

{views} {{ background: {c['bg']}; border: 1px solid {c['line']}; border-radius: 8px;
           alternate-background-color: {c['card']}; outline: 0; }}
QListView::item, QTreeView::item {{ padding: 3px 4px; border-radius: 5px; }}
QListView::item:hover, QTreeView::item:hover {{ background: {c['card']}; }}
QListView::item:selected, QTreeView::item:selected, QTableView::item:selected {{
    background: {c['accent_soft']}; color: {c['text']}; }}
QHeaderView::section {{ background: {c['surface']}; color: {c['muted']}; border: none;
                        border-bottom: 1px solid {c['line']}; padding: 6px 8px; font-weight: 600; }}
QTableCornerButton::section {{ background: {c['surface']}; border: none; }}
QScrollArea {{ background: transparent; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {c['line_strong']}; border-radius: 3px; }}
QScrollBar::handle:vertical {{ min-height: 28px; }}
QScrollBar::handle:horizontal {{ min-width: 28px; }}
QScrollBar::handle:hover {{ background: {c['faint']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QSlider::groove:horizontal {{ height: 4px; background: {c['line_strong']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {c['text']}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: {c['accent_hover']}; }}
QSlider::groove:horizontal:disabled, QSlider::sub-page:horizontal:disabled {{ background: {c['line']}; }}

QProgressBar {{ background: {c['bg']}; border: 1px solid {c['line']}; border-radius: 6px; text-align: center; }}
QProgressBar::chunk {{ border-radius: 5px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                       stop:0 {c['accent']}, stop:1 {c['accent_2']}); }}
QSplitter::handle {{ background: {c['line']}; }}
QStatusBar {{ background: {c['bg']}; border-top: 1px solid {c['line']}; color: {c['muted']}; }}
QStatusBar::item {{ border: none; }}
"""


def apply_theme(app: QApplication, name: str) -> None:
    """Switch between the Ignite look and the system theme at runtime."""
    global _system_style
    if _system_style is None:
        _system_style = app.style().name()
    if name == "system":
        app.setStyleSheet("")
        app.setStyle(QStyleFactory.create(_system_style))
        app.setPalette(app.style().standardPalette())
    else:
        app.setStyle(QStyleFactory.create("Fusion"))
        app.setPalette(ignite_palette())
        app.setStyleSheet(stylesheet())


def state_color(state: ConnectionState, widget: QWidget) -> QColor:
    """Text colour for a connection state; neutral states use the normal text colour."""
    dark = widget.palette().color(QPalette.ColorRole.Base).lightness() < 128
    if state is ConnectionState.ERROR:
        return QColor(COLORS["error"] if dark else "#c62828")
    if state is ConnectionState.CONNECTED:
        return QColor(COLORS["ok"] if dark else "#2e7d32")
    return widget.palette().color(QPalette.ColorRole.Text)


def warn_color(widget: QWidget) -> QColor:
    """Amber for "needs attention" values (e.g. OBS dropping frames), readable in light and dark themes."""
    dark = widget.palette().color(QPalette.ColorRole.Base).lightness() < 128
    return QColor(COLORS["warn"] if dark else "#9a5b00")


def icon(theme_name: str, fallback: QStyle.StandardPixmap, widget: QWidget | None = None) -> QIcon:
    """Icon from the desktop's icon theme (Linux), else the style's standard icon (Windows/macOS)."""
    found = QIcon.fromTheme(theme_name)
    if not found.isNull():
        return found
    style = widget.style() if widget is not None else QApplication.style()
    return style.standardIcon(fallback)
