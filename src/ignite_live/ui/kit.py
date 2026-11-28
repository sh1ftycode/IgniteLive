"""Ignite's component library: the pieces every screen is built from.

Colours come from the app stylesheet (ui/theme.py) through object names and ``role``/``tone`` properties, so
most of these need no restyling of their own. The hand-painted ones (``MetricCard``, ``StatusDot``,
``LineChart``) read ``colors()`` when they paint, so a theme switch only needs a repaint.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ignite_live.ui import lucide
from ignite_live.ui.theme import card_fill, colors, set_props, tabular, theme_changed, tint_color

TRANSITION_MS = 170


# Small helpers.

def label(text: str = "", role: str | None = None, tone: str | None = None, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    if tone:
        widget.setProperty("tone", tone)
    widget.setWordWrap(wrap)
    return widget


def set_tone(widget: QWidget, tone: str | None) -> None:
    set_props(widget, tone=tone or "")


def hline() -> QFrame:
    line = QFrame()
    line.setObjectName("hline")
    return line


def vline() -> QFrame:
    line = QFrame()
    line.setObjectName("vline")
    return line


def icon_label(name: str, color: str | None = None, size: int = 16) -> QLabel:
    widget = QLabel()
    widget.setFixedSize(size, size)
    widget.icon_name, widget.icon_color = name, color  # type: ignore[attr-defined]
    widget.setPixmap(lucide.pixmap(name, color or colors().text_muted, size))
    return widget


def recolor(widget: QLabel, color: str | None = None) -> None:
    """Redraw an ``icon_label`` (after a theme switch, or to show a new state)."""
    if color is not None:
        widget.icon_color = color  # type: ignore[attr-defined]
    widget.setPixmap(lucide.pixmap(widget.icon_name, widget.icon_color or colors().text_muted,  # type: ignore[attr-defined]
                                   widget.width()))


def button(text: str = "", icon: str | None = None, *, kind: str = "", tip: str = "") -> QPushButton:
    """A push button: ``kind`` is "", "primary", "outline", "ghost", "danger" or "live"."""
    widget = QPushButton(text)
    widget.icon_name = icon  # type: ignore[attr-defined]
    if kind:
        widget.setProperty(kind, True)
    if tip:
        widget.setToolTip(tip)
    _paint_button_icon(widget)
    return widget


def _paint_button_icon(widget: QPushButton) -> None:
    name = getattr(widget, "icon_name", None)
    if not name:
        return
    p = colors()
    if widget.property("primary") or widget.property("live"):
        color = p.on_primary
    elif widget.property("outline"):
        color = p.accent_text
    elif widget.property("danger"):
        color = p.live
    else:
        color = p.text_muted
    glyph = lucide.pixmap(name, color, 16)
    wide = QPixmap(glyph.width() + 6 * glyph.devicePixelRatio(), glyph.height())  # 6px of room before the label
    wide.setDevicePixelRatio(glyph.devicePixelRatio())
    wide.fill(Qt.GlobalColor.transparent)
    painter = QPainter(wide)
    painter.drawPixmap(0, 0, glyph)
    painter.end()
    widget.setIcon(QIcon(wide))
    widget.setIconSize(QSize(22, 16))


def restyle_buttons(*buttons: QPushButton) -> None:
    for widget in buttons:
        _paint_button_icon(widget)


def icon_button(name: str, tip: str, size: int = 16) -> QToolButton:
    """A square, icon-only button (card header actions, the header's bell)."""
    widget = QToolButton()
    widget.icon_name = name  # type: ignore[attr-defined]
    widget.setProperty("iconOnly", True)
    widget.setToolTip(tip)
    widget.setAccessibleName(tip)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.setIcon(lucide.icon(name, colors().text_muted, size))
    widget.setIconSize(QSize(size, size))
    return widget


def recolor_icon_button(widget: QToolButton, color: str | None = None) -> None:
    widget.setIcon(lucide.icon(widget.icon_name, color or colors().text_muted, widget.iconSize().width()))  # type: ignore[attr-defined]


# Layout.

class PageContainer(QScrollArea):
    """A screen's scrolling content: 24px margins, 16px between sections, an optional maximum width."""

    def __init__(self, max_width: int | None = None, spacing: int = 16) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        holder.setObjectName("page")
        self.setWidget(holder)
        row = QHBoxLayout(holder)
        row.setContentsMargins(24, 20, 24, 24)
        column = QWidget()
        if max_width:
            column.setMaximumWidth(max_width)
        row.addWidget(column)
        self.layout_ = QVBoxLayout(column)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(spacing)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.layout_.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout) -> None:
        self.layout_.addLayout(layout)

    def finish(self) -> None:
        self.layout_.addStretch(1)


def page_layout(page: QWidget) -> QVBoxLayout:
    """A fixed (non-scrolling) screen: same margins as ``PageContainer``."""
    page.setObjectName("page")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(24, 20, 24, 24)
    layout.setSpacing(16)
    return layout


def row(*widgets: QWidget, spacing: int = 8, stretch_last: bool = False) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(spacing)
    for widget in widgets:
        layout.addWidget(widget)
    if not stretch_last:
        layout.addStretch(1)
    return layout


# Card.

class Card(QFrame):
    """A panel with an icon + title header strip (and room for header actions on the right), then its body.

    Put content in ``self.body`` (a QVBoxLayout with 16px margins). ``primary`` is accepted for older callers
    and has no look of its own: purple is for the main *action*, not for whole cards.
    """

    def __init__(self, title: str = "", icon: str | None = None, primary: bool = False,
                 parent: QWidget | None = None, padded: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._primary = primary
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.header = None
        self._icon: QLabel | None = None
        if title:
            self.header = QFrame()
            self.header.setObjectName("cardHeader")
            self.header_layout = QHBoxLayout(self.header)
            self.header_layout.setContentsMargins(18, 16, 14, 0)
            self.header_layout.setSpacing(8)
            # ``icon`` is accepted for older callers: the design's card titles are plain text.
            self.title = label(title, "cardTitle")
            self.header_layout.addWidget(self.title)
            self.header_layout.addStretch(1)
            outer.addWidget(self.header)
        content = QWidget()
        self.body = QVBoxLayout(content)
        if padded:
            self.body.setContentsMargins(18, 12, 18, 18)
        else:
            self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(10)
        outer.addWidget(content, 1)

    def add_header_widget(self, widget: QWidget) -> QWidget:
        if self.header is not None:
            self.header_layout.addWidget(widget)
        return widget

    def restyle(self) -> None:
        if self._icon is not None:
            recolor(self._icon, colors().accent)


class Segmented(QFrame):
    """A row of mutually exclusive choices in one pill (the design's range and scene switchers).

    Reads like a combo box (``addItem``, ``currentData``, ``currentText``, ``currentIndex``,
    ``currentIndexChanged``) so it can stand in for one.
    """

    currentIndexChanged = Signal(int)  # noqa: N815 - the Qt name this stands in for

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("segmented")
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(2)
        self._row = row
        self._buttons: list[QPushButton] = []
        self._data: list[object] = []
        self._current = -1
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def addItem(self, text: str, data: object = None) -> None:  # noqa: N802 - Qt-style name
        index = len(self._buttons)
        item = QPushButton(text)
        item.setProperty("segment", True)
        item.setCheckable(True)
        item.setCursor(Qt.CursorShape.PointingHandCursor)
        item.clicked.connect(lambda _=False, i=index: self.setCurrentIndex(i))
        self._row.addWidget(item)
        self._buttons.append(item)
        self._data.append(data)
        if self._current < 0:
            self.setCurrentIndex(0)

    def count(self) -> int:
        return len(self._buttons)

    def currentIndex(self) -> int:  # noqa: N802
        return self._current

    def currentData(self) -> object:  # noqa: N802
        return self._data[self._current] if self._current >= 0 else None

    def currentText(self) -> str:  # noqa: N802
        return self._buttons[self._current].text() if self._current >= 0 else ""

    def findData(self, data: object) -> int:  # noqa: N802
        return self._data.index(data) if data in self._data else -1

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if not 0 <= index < len(self._buttons):
            return
        for i, item in enumerate(self._buttons):
            item.setChecked(i == index)
        if index != self._current:
            self._current = index
            self.currentIndexChanged.emit(index)


# Metrics and status.

class MetricCard(QWidget):
    """A metric: a large icon in its own colour, the value, its caption, and a quiet trend on the right.

    Hand-painted (no child widgets): a row of these stays light, and test runs that build hundreds of pages
    don't pile up widgets and connections (a QFrame-with-labels version crashed the suite once).
    """

    PAD = 16
    ICON = 26

    def __init__(self, icon: str, label_text: str, tone: str = "accent", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon = icon
        self._label = label_text
        self._tone = tone
        self._value = "–"
        self._trend = ""
        self._live = False
        self._series: list[float] = []
        self.setFont(tabular(self.font()))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(label_text)

    def set_value(self, text: str) -> None:
        if text != self._value:
            self._value = text
            self.setToolTip(f"{self._label}: {text}")
            self.setAccessibleDescription(text)
            self.update()

    def set_trend(self, text: str) -> None:
        if text != self._trend:
            self._trend = text
            self.update()

    def set_series(self, values: list[float]) -> None:
        """A small line under the value (the last few readings); needs at least two points."""
        values = list(values)
        if values != self._series:
            self._series = values
            self.update()

    def set_live(self, live: bool) -> None:
        if live != self._live:
            self._live = live
            self.update()

    def restyle(self) -> None:
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(220, 100)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(150, 100)

    def _tone_color(self) -> str:
        p = colors()
        return {"accent": p.accent, "live": p.live, "warning": p.warning, "healthy": p.healthy,
                "muted": p.text_muted}.get("live" if self._live else self._tone, p.accent)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        """The design's stat tile: a quiet caption on top, a large value, the trend under it, a small icon at the
        right. No outline: the panel colour against the window does the separating (red outline only while live)."""
        p = colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(p.live), 1) if self._live else Qt.PenStyle.NoPen)
        painter.setBrush(card_fill())
        painter.drawRoundedRect(rect, 14, 14)
        text_x = self.PAD + 2
        available = max(0.0, self.width() - text_x - self.PAD)
        caption = self.font()
        caption.setPixelSize(12)
        painter.setFont(caption)
        painter.setPen(QColor(p.text_muted))
        painter.drawText(QRectF(text_x, 12, available, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         QFontMetrics(caption).elidedText(self._label, Qt.TextElideMode.ElideRight, int(available)))
        value_font = self.font()
        value_font.setWeight(value_font.Weight.DemiBold)
        full = max(0.0, self.width() - text_x - self.PAD)
        for size in (26, 24, 20, 18, 16):
            value_font.setPixelSize(size)
            if QFontMetrics(value_font).horizontalAdvance(self._value) <= full:
                break
        metrics = QFontMetrics(value_font)
        painter.setFont(value_font)
        painter.setPen(QColor(p.live if self._live else p.text))
        painter.drawText(QRectF(text_x, 30, full, metrics.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(self._value, Qt.TextElideMode.ElideRight, int(full)))
        if len(self._series) > 1:
            top = 30 + metrics.height() + 10 if not self._trend else 30 + metrics.height() + 22
            low, high = min(self._series), max(self._series)
            span = (high - low) or 1.0
            height = max(10.0, self.height() - top - 14)
            step = full / (len(self._series) - 1)
            path = QPainterPath()
            for index, value in enumerate(self._series):
                point = QPointF(text_x + index * step, top + height - (value - low) / span * height)
                path.moveTo(point) if index == 0 else path.lineTo(point)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(p.accent), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(path)
        if self._trend:
            painter.setFont(caption)
            painter.setPen(QColor(p.healthy if self._trend.startswith("+") else p.text_muted))
            painter.drawText(QRectF(text_x, 30 + metrics.height() + 1, full, 16),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             QFontMetrics(caption).elidedText(self._trend, Qt.TextElideMode.ElideRight, int(full)))


StatTile = MetricCard  # The name the first redesigned pages used.

TONES = ("healthy", "warning", "error", "muted", "accent", "live")


def tone_color(tone: str) -> str:
    p = colors()
    return {"healthy": p.healthy, "warning": p.warning, "error": p.live, "live": p.live, "accent": p.accent,
            "muted": p.text_disabled}.get(tone, p.text_disabled)


class StatusDot(QWidget):
    """A small filled dot: green connected, yellow waiting, red problem, grey off."""

    def __init__(self, tone: str = "muted", size: int = 8) -> None:
        super().__init__()
        self._tone = tone
        self.setFixedSize(size, size)

    def set_tone(self, tone: str) -> None:
        if tone != self._tone:
            self._tone = tone
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tone_color(self._tone)))
        painter.drawEllipse(QRectF(self.rect()))


class StatusIndicator(QWidget):
    """A dot and a short state ("Connected", "Live 0:42:10"), optionally with a detail underneath."""

    def __init__(self, text: str = "", tone: str = "muted") -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.dot = StatusDot(tone)
        self.text = label(text)
        layout.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.text)
        layout.addStretch(1)
        self.set(text, tone)

    def set(self, text: str, tone: str) -> None:
        self.dot.set_tone(tone)
        self.text.setText(text)
        set_props(self.text, tone={"healthy": "healthy", "warning": "warning", "error": "error"}.get(tone, ""))


class Pill(QFrame):
    """A rounded status pill for the header: dot, text, and an optional trailing icon or menu."""

    clicked = Signal()

    def __init__(self, text: str = "", tone: str = "muted") -> None:
        super().__init__()
        self.setObjectName("pill")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 12, 4)
        layout.setSpacing(8)
        self.dot = StatusDot(tone)
        self.text = label(text)
        layout.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.text)
        self.setFixedHeight(28)

    def set(self, text: str, tone: str, tip: str = "") -> None:
        self.dot.set_tone(tone)
        self.text.setText(text)
        self.setToolTip(tip)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# Empty, loading and error states.

class EmptyState(QWidget):
    """An icon, a short title and one line of help, centred: for lists and panels with nothing in them yet."""

    def __init__(self, icon: str, title: str, text: str = "", action: QPushButton | None = None) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 24, 16, 24)
        layout.setSpacing(6)
        layout.addStretch(1)
        self.icon = icon_label(icon, None, 36)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(6)
        self.title = label(title, "emptyTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.title)
        self.text = label(text, "muted", wrap=True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.text.setVisible(bool(text))
        layout.addWidget(self.text)
        if action is not None:
            layout.addSpacing(6)
            layout.addWidget(action, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

    def set_text(self, title: str, text: str = "") -> None:
        self.title.setText(title)
        self.text.setText(text)
        self.text.setVisible(bool(text))

    def restyle(self) -> None:
        recolor(self.icon, colors().text_disabled)


class Banner(QFrame):
    """A message across the top of a screen: an error (red) or information, with an optional action."""

    def __init__(self, text: str = "", error: bool = True, action: QPushButton | None = None) -> None:
        super().__init__()
        self.setObjectName("banner" if error else "bannerInfo")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        self.icon = icon_label("triangle-alert" if error else "info", None, 16)
        self.error = error
        self.text = label(text, wrap=True)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.text, 1)
        if action is not None:
            layout.addWidget(action, 0, Qt.AlignmentFlag.AlignVCenter)
        self.restyle()

    def restyle(self) -> None:
        recolor(self.icon, colors().live if self.error else colors().accent)


# Charts.

def nice_ceiling(value: float) -> float:
    """A round top for a chart's scale: 7 → 10, 2,340 → 2,500, 0 → 1."""
    if value <= 0:
        return 1
    magnitude = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * magnitude:
            return step * magnitude
    return 10 * magnitude


def short_number(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if value >= 1000:
        return f"{value / 1000:.1f}".rstrip("0").rstrip(".") + "K"
    return f"{value:g}"


class LineChart(QWidget):
    """Lines over labelled x positions, with a light grid, y-axis values, a soft fill under the first series,
    and the values under the mouse in a tooltip. ``series`` is ``[(name, colour, values)]``."""

    def __init__(self, empty_text: str = "No data yet.") -> None:
        super().__init__()
        self.setMinimumHeight(180)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.labels: list[str] = []
        self.series: list[tuple[str, str, list[float]]] = []
        self.empty_text = empty_text
        self._hover: int | None = None

    def set_data(self, labels: list[str], series: list[tuple[str, str, list[float]]]) -> None:
        self.labels, self.series = labels, series
        self.update()

    def _plot(self) -> QRectF:
        return QRectF(self.rect()).adjusted(44, 10, -12, -26)

    def _x(self, plot: QRectF, index: int) -> float:
        count = len(self.labels)
        return plot.left() + (plot.width() * index / (count - 1) if count > 1 else plot.width() / 2)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        p = colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        small = self.font()
        small.setPixelSize(11)
        painter.setFont(small)
        plot = self._plot()
        values = [v for _name, _color, data in self.series for v in data]
        if not self.labels or plot.width() < 40 or plot.height() < 40:
            painter.setPen(QColor(p.text_muted))
            painter.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, self.empty_text)
            return
        top = nice_ceiling(max(values, default=0))
        for step in range(5):
            y = plot.bottom() - plot.height() * step / 4
            painter.setPen(QPen(QColor(p.divider), 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(p.text_muted))
            painter.drawText(QRectF(0, y - 8, plot.left() - 10, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, short_number(top * step / 4))
        every = max(1, math.ceil(len(self.labels) / 8))
        for index, text in enumerate(self.labels):
            if index % every == 0 or index == len(self.labels) - 1:
                x = self._x(plot, index)
                painter.drawText(QRectF(x - 36, plot.bottom() + 6, 72, 16), Qt.AlignmentFlag.AlignCenter, text)
        if self._hover is not None:
            painter.setPen(QPen(QColor(p.handle), 1, Qt.PenStyle.DashLine))
            x = self._x(plot, self._hover)
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        for order, (_name, color, data) in enumerate(self.series):
            points = [QPointF(self._x(plot, i), plot.bottom() - plot.height() * (v / top)) for i, v in enumerate(data)]
            if not points:
                continue
            line = QPainterPath(points[0])
            for point in points[1:]:
                line.lineTo(point)
            if order == 0 and len(points) > 1:
                area = QPainterPath(line)
                area.lineTo(points[-1].x(), plot.bottom())
                area.lineTo(points[0].x(), plot.bottom())
                area.closeSubpath()
                fill = QLinearGradient(0, plot.top(), 0, plot.bottom())
                fill.setColorAt(0, tint_color(color, 0.28))
                fill.setColorAt(1, tint_color(color, 0.0))
                painter.fillPath(area, fill)
            pen = QPen(QColor(color), 2)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(line)
            painter.setBrush(QColor(color))
            painter.setPen(QPen(QColor(p.panel), 1.5))
            for index, point in enumerate(points):
                radius = 4.5 if index == self._hover else 3.2
                painter.drawEllipse(point, radius, radius)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        plot = self._plot()
        if not self.labels or plot.width() <= 0:
            return
        count = len(self.labels)
        fraction = (event.position().x() - plot.left()) / plot.width() if count > 1 else 0
        index = max(0, min(count - 1, round(fraction * (count - 1))))
        if index != self._hover:
            self._hover = index
            self.update()
            lines = [self.labels[index]] + [f"{name}: {data[index]:,.0f}" for name, _c, data in self.series
                                            if index < len(data)]
            QToolTip.showText(event.globalPosition().toPoint(), "\n".join(lines), self)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._hover = None
        self.update()
        super().leaveEvent(event)


class Legend(QWidget):
    """Coloured dots and names, for a chart's card header."""

    def __init__(self, items: list[tuple[str, str]]) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.dots = []
        for name, tone in items:
            dot = _ColorDot(tone)
            self.dots.append(dot)
            layout.addWidget(dot)
            layout.addWidget(label(name, "muted"))


class _ColorDot(QWidget):
    def __init__(self, color_name: str) -> None:
        super().__init__()
        self.color_name = color_name
        self.setFixedSize(8, 8)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(getattr(colors(), self.color_name, self.color_name)))
        painter.drawEllipse(QRectF(self.rect()))


# Event lists.

def ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


class EventRow(QWidget):
    """One event: an icon in its colour, a title and a detail, and how long ago on the right."""

    def __init__(self, icon: str, tone: str, title: str, detail: str, when: float) -> None:
        super().__init__()
        self.when = when
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(12)
        self.icon = icon_label(icon, tone_color(tone), 20)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(1)
        title_label = label(title)
        title_label.setStyleSheet("font-weight: 600;")
        text.addWidget(title_label)
        self.detail = label(detail, "muted")
        self.detail.setWordWrap(True)
        text.addWidget(self.detail)
        layout.addLayout(text, 1)
        self.time = label(ago(time.time() - when), "muted")
        layout.addWidget(self.time, 0, Qt.AlignmentFlag.AlignTop)

    def refresh_time(self) -> None:
        self.time.setText(ago(time.time() - self.when))


# Toasts.

class ToastHost(QWidget):
    """Short-lived messages stacked in the window's bottom-right corner. Errors stay longer and can be
    dismissed; every message is also kept in the notification centre."""

    MAX = 3

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(8)
        self.layout_.addStretch(1)
        parent.installEventFilter(self)
        self.hide()

    def show_message(self, level: str, text: str) -> None:
        toast = QFrame(self)
        toast.setObjectName("toast")
        toast.setProperty("level", level)
        layout = QHBoxLayout(toast)
        layout.setContentsMargins(12, 10, 8, 10)
        layout.setSpacing(10)
        p = colors()
        layout.addWidget(icon_label("circle-x" if level == "error" else "circle-check" if level == "success"
                                    else "info", p.live if level == "error" else p.healthy if level == "success"
                                    else p.accent, 16), 0, Qt.AlignmentFlag.AlignTop)
        message = label(text, wrap=True)
        message.setMaximumWidth(320)
        layout.addWidget(message, 1)
        close = icon_button("x", "Dismiss", 14)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        close.clicked.connect(lambda: self._dismiss(toast))
        while self.layout_.count() - 1 >= self.MAX:
            old = self.layout_.itemAt(1).widget()
            self._dismiss(old, animate=False)
        self.layout_.addWidget(toast)
        effect = QGraphicsOpacityEffect(toast)
        toast.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", toast)
        fade.setDuration(TRANSITION_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.start()
        QTimer.singleShot(12000 if level == "error" else 6000, toast, lambda: self._dismiss(toast))
        self.show()
        self.raise_()
        self._place()

    def _dismiss(self, toast: QWidget, animate: bool = True) -> None:
        if toast is None or toast.parent() is None:
            return
        self.layout_.removeWidget(toast)
        toast.hide()
        toast.deleteLater()
        if self.layout_.count() <= 1:
            self.hide()
        else:
            self._place()

    def _place(self) -> None:
        parent = self.parentWidget()
        width = 380
        self.adjustSize()
        height = min(self.sizeHint().height(), parent.height() - 32)
        self.setGeometry(parent.width() - width - 20, parent.height() - height - 20, width, height)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        if watched is self.parentWidget() and event.type() == event.Type.Resize and self.isVisible():
            self._place()
        return False


def watch_theme(callback) -> None:
    """Call ``callback`` after every theme switch (for code that keeps colours of its own)."""
    theme_changed().connect(lambda _palette: callback())
