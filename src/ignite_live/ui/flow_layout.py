"""A layout that places widgets in a row and wraps onto more rows when there isn't room.

Used for button rows, so a narrow window wraps them instead of cutting off their labels.
"""

from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QStyle, QWidget


class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(QMargins(0, 0, 0, 0))

    # QLayout's item interface.

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - Qt override
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt override
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt override
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    # Sizing: as wide as one row would like, as narrow as the widest item.

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 - Qt override
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt override
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        return self._arrange(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt override
        super().setGeometry(rect)
        self._arrange(rect, move=True)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        visible = [item for item in self._items if not item.isEmpty()]
        width = sum(item.sizeHint().width() for item in visible) + self._gap() * max(0, len(visible) - 1)
        height = max((item.sizeHint().height() for item in visible), default=0)
        margins = self.contentsMargins()
        return QSize(width + margins.left() + margins.right(), height + margins.top() + margins.bottom())

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt override
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _gap(self) -> int:
        if self.spacing() >= 0:
            return self.spacing()
        parent = self.parentWidget()
        style = parent.style() if parent is not None else None
        return style.pixelMetric(QStyle.PixelMetric.PM_LayoutHorizontalSpacing) if style is not None else 6

    def _arrange(self, rect: QRect, move: bool) -> int:
        """Place the items row by row within ``rect`` (when ``move``); returns the height they need."""
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        gap = self._gap()
        x, y, row_height = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint().boundedTo(QSize(area.width(), item.sizeHint().height()))
            if x > area.x() and x + hint.width() > area.right() + 1:  # No room left on this row: start another.
                x, y, row_height = area.x(), y + row_height + gap, 0
            if move:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + gap
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y() + margins.bottom()
