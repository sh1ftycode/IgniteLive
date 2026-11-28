"""The app sidebar: the IgniteLive logo, the eight screens, and the version and account at the bottom.

One destination per row (Lucide icon and label), the current one filled. Collapsing shows icons alone, with
the label as a tooltip. MainWindow owns which screens exist and their order (``set_visible_items``).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ignite_live import __version__
from ignite_live.ui import lucide
from ignite_live.ui.icons import logo
from ignite_live.ui.theme import colors, theme_changed, tint

ICON_SIZE = 18
ICON_GAP = 10  # Transparent room to the right of each icon: the space between it and the label.
LOGO_SIZE = 26
EXPANDED_WIDTH = 232
COLLAPSED_WIDTH = 64


@dataclass(frozen=True)
class NavItem:
    key: str
    title: str
    icon: str


class Avatar(QWidget):
    """A round initial, for the signed-in account (the sidebar's footer and the header's profile button)."""

    def __init__(self, size: int = 32) -> None:
        super().__init__()
        self.setFixedSize(size, size)
        self.text = "?"

    def set_name(self, name: str) -> None:
        self.text = (name.strip().lstrip("@")[:1] or "?").upper()
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        p = colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(p.primary))
        painter.drawEllipse(QRectF(self.rect()))
        font = QFont(self.font())
        font.setPixelSize(int(self.height() * 0.44))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(p.on_primary))
        painter.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, self.text)


class AccountRow(QFrame):
    """The sidebar's footer: who's signed in (and where), clickable for the profile menu."""

    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)
        self.avatar = Avatar(32)
        layout.addWidget(self.avatar)
        text = QVBoxLayout()
        text.setSpacing(0)
        self.name = QLabel("Not signed in")
        self.name.setStyleSheet("font-weight: 600;")
        self.detail = QLabel("")
        self.detail.setProperty("role", "muted")
        text.addWidget(self.name)
        text.addWidget(self.detail)
        self.text_box = QWidget()
        self.text_box.setLayout(text)
        layout.addWidget(self.text_box, 1)
        self.setObjectName("accountRow")

    def set_account(self, name: str, detail: str) -> None:
        self.name.setText(name)
        self.detail.setText(detail)
        self.avatar.set_name(name if name != "Not signed in" else "I")
        self.setToolTip(f"{name}\n{detail}")

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class Sidebar(QFrame):
    """``page_requested``: a row was clicked. ``hide_requested``/``show_all_requested``: its context menu.
    ``collapsed_changed``: the collapse toggle (so MainWindow can remember it). ``account_clicked``: the footer."""

    page_requested = Signal(str)
    hide_requested = Signal(str)
    show_all_requested = Signal()
    collapsed_changed = Signal(bool)
    account_clicked = Signal()

    def __init__(self, items: list[NavItem], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.items = {item.key: item for item in items}
        self._collapsed = False
        self._buttons: dict[str, QToolButton] = {}
        self.setObjectName("sidebar")
        self.setFixedWidth(EXPANDED_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 16, 12, 12)
        outer.setSpacing(4)

        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 18)
        brand.setSpacing(10)
        self.brand_icon = QLabel()
        self.brand_icon.setFixedSize(LOGO_SIZE, LOGO_SIZE)
        self.brand_icon.setPixmap(QPixmap.fromImage(logo(LOGO_SIZE)))
        brand.addWidget(self.brand_icon)
        self.brand_label = QLabel()
        self.brand_label.setTextFormat(Qt.TextFormat.RichText)
        brand.addWidget(self.brand_label)
        brand.addStretch(1)
        outer.addLayout(brand)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.nav_layout = QVBoxLayout()
        self.nav_layout.setContentsMargins(0, 0, 0, 0)
        self.nav_layout.setSpacing(4)
        for item in items:
            self._add_button(item)
        outer.addLayout(self.nav_layout)
        outer.addStretch(1)

        self.update_button: QWidget | None = None
        self.footer = QVBoxLayout()
        self.footer.setSpacing(8)
        self.account = AccountRow()
        self.account.clicked.connect(self.account_clicked.emit)
        self.footer.addWidget(self.account)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(6, 0, 0, 0)
        self.version = QLabel(f"v{__version__}")
        self.version.setProperty("role", "muted")
        bottom.addWidget(self.version)
        bottom.addStretch(1)
        self.collapse_button = QToolButton()
        self.collapse_button.setProperty("iconOnly", True)
        self.collapse_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapse_button.setIconSize(QSize(16, 16))
        self.collapse_button.clicked.connect(lambda: self.set_collapsed(not self._collapsed))
        bottom.addWidget(self.collapse_button)
        self.footer.addLayout(bottom)
        outer.addLayout(self.footer)

        theme_changed().connect(self._restyle)
        self._restyle()
        self._update_collapse_tip()

    def add_footer_widget(self, widget: QWidget) -> None:
        """Above the account row: the update status."""
        self.update_button = widget
        self.footer.insertWidget(0, widget)

    # Building: rows are rebuilt (not reordered in place) when the visible set or its order changes.

    def _add_button(self, item: NavItem) -> None:
        button = QToolButton()
        button.key = item.key  # noqa: B010 - a plain tag read back in _menu/_restyle_button
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setIconSize(QSize(ICON_SIZE + ICON_GAP, ICON_SIZE))
        button.setText(item.title)
        button.setAccessibleName(item.title)
        button.setFixedHeight(40)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        button.customContextMenuRequested.connect(lambda pos, k=item.key: self._menu(k, pos))
        button.clicked.connect(lambda: self.page_requested.emit(item.key))
        button.toggled.connect(lambda _on, b=button: self._restyle_button(b))
        self.group.addButton(button)
        self._buttons[item.key] = button
        self.nav_layout.addWidget(button)
        self._apply_collapsed(button, item)
        self._restyle_button(button)

    def _menu(self, key: str, pos) -> None:
        menu = QMenu(self)
        menu.addAction("Hide", lambda: self.hide_requested.emit(key))
        menu.addAction("Show All Sections", self.show_all_requested.emit).setEnabled(
            len(self._buttons) < len(self.items))
        menu.exec(self._buttons[key].mapToGlobal(pos))

    def set_visible_items(self, keys: list[str]) -> None:
        """Rebuilds the list in this order, showing only these keys (hidden ones aren't in it at all)."""
        current = next((k for k, b in self._buttons.items() if b.isChecked()), None)
        for button in self._buttons.values():
            self.nav_layout.removeWidget(button)
            button.hide()  # deleteLater() alone leaves it visibly on screen until the event loop gets to it.
            button.deleteLater()
        self._buttons = {}
        self.group.deleteLater()
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for key in keys:
            if key in self.items:
                self._add_button(self.items[key])
        if current is not None:
            self.set_current(current)

    def visible_keys(self) -> list[str]:
        return list(self._buttons.keys())

    def set_current(self, key: str) -> None:
        for other_key, button in self._buttons.items():
            button.setChecked(other_key == key)
            self._restyle_button(button)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self.setFixedWidth(COLLAPSED_WIDTH if collapsed else EXPANDED_WIDTH)
        self.brand_label.setVisible(not collapsed)
        self.version.setVisible(not collapsed)
        self.account.text_box.setVisible(not collapsed)
        if self.update_button is not None:
            self.update_button.setVisible(not collapsed)
        for key, button in self._buttons.items():
            self._apply_collapsed(button, self.items[key])
        self._update_collapse_tip()
        self.collapsed_changed.emit(collapsed)

    def _update_collapse_tip(self) -> None:
        self.collapse_button.setToolTip("Expand Sidebar" if self._collapsed else "Collapse Sidebar")
        self.collapse_button.setIcon(lucide.icon("chevron-right" if self._collapsed else "panel-left",
                                                 colors().text_muted, 16))

    def _apply_collapsed(self, button: QToolButton, item: NavItem) -> None:
        button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly if self._collapsed else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setToolTip(item.title if self._collapsed else "")

    # Styling.

    def _restyle_button(self, button: QToolButton) -> None:
        p = colors()
        active = button.isChecked()
        glyph = lucide.pixmap(self.items[button.key].icon, p.accent if active else p.text_muted, ICON_SIZE)
        wide = QPixmap(glyph.width() + ICON_GAP * glyph.devicePixelRatio(), glyph.height())
        wide.setDevicePixelRatio(glyph.devicePixelRatio())
        wide.fill(Qt.GlobalColor.transparent)
        painter = QPainter(wide)
        painter.drawPixmap(0, 0, glyph)
        painter.end()
        button.setIcon(QIcon(wide))

    def _restyle(self, _palette=None) -> None:
        p = colors()
        self.brand_label.setText(f'<span style="font-size:18px; font-weight:700; color:{p.text};">Ignite</span>')
        self.setStyleSheet(f"""
            QFrame#sidebar QToolButton {{ background: transparent; color: {p.text_muted}; border: none;
                border-radius: 8px; padding: 0 12px; text-align: left; font-size: 14px; font-weight: 500; }}
            QFrame#sidebar QToolButton:hover {{ background: {p.raised}; color: {p.text}; }}
            QFrame#sidebar QToolButton:checked {{ background: {p.raised}; color: {p.text};
                font-weight: 600; }}
            QFrame#sidebar QToolButton:focus {{ outline: none; }}
            QFrame#accountRow {{ background: transparent; border: 1px solid {p.divider}; border-radius: 10px; }}
            QFrame#accountRow:hover {{ background: {p.raised}; }}
        """)
        self._update_collapse_tip()
        for button in self._buttons.values():
            self._restyle_button(button)
