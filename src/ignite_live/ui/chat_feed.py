"""TikTok LIVE-style chat feed: avatars, badges, gift cards, follow rows.

Used by the Chat panel and the pop-out chat window.
"""

from __future__ import annotations

import zlib

from PySide6.QtCore import QModelIndex, QObject, QPointF, QRect, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from ignite_live.core.images import allowed_image_url
from ignite_live.core.events import ChatComment, ChatEvent, ChatFollow, ChatGift, ChatUser

TIKTOK_PINK = "#fe2c55"
TIKTOK_CYAN = "#25f4ee"
# Platform pills shown before names from Twitch and YouTube (TikTok is the default, so it has none).
PLATFORM_BADGES = {"twitch": ("Twitch", "#9146ff", "#ffffff"), "youtube": ("YouTube", "#ff0033", "#ffffff")}
EVENT_ROLE = Qt.ItemDataRole.UserRole
KIND_ROLE = Qt.ItemDataRole.UserRole + 1

MAX_IMAGE_BYTES = 512 * 1024
MAX_CACHED_IMAGES = 800
AVATAR_COLORS = ("#fe2c55", "#25c9c3", "#7c6cff", "#ff8a5c", "#3fa7f5", "#f5a623", "#59c27a", "#e45dbf")


def kind_of(event: ChatEvent) -> str | None:
    if isinstance(event, ChatComment):
        return "comments"
    if isinstance(event, ChatGift):
        return "gifts"
    if isinstance(event, ChatFollow):
        return "follows"
    return None



class ImageCache(QObject):
    """Downloads avatars and gift icons once, keeps them in memory."""

    loaded = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._network = QNetworkAccessManager(self)
        self._images: dict[str, QImage] = {}
        self._pending: set[str] = set()
        self._failed: set[str] = set()

    def get(self, url: str) -> QImage | None:
        image = self._images.get(url)
        if image is None and url and url not in self._pending and url not in self._failed:
            self._fetch(url)
        return image

    def _fetch(self, url: str) -> None:
        if not allowed_image_url(url):
            self._failed.add(url)
            return
        request = QNetworkRequest(QUrl(url))
        request.setTransferTimeout(10_000)
        reply = self._network.get(request)
        self._pending.add(url)
        reply.downloadProgress.connect(lambda received, _total: received > MAX_IMAGE_BYTES and reply.abort())
        reply.finished.connect(lambda: self._done(url, reply))

    def _done(self, url: str, reply: QNetworkReply) -> None:
        self._pending.discard(url)
        data = reply.read(MAX_IMAGE_BYTES + 1)
        reply.deleteLater()
        image = QImage()
        if reply.error() != QNetworkReply.NetworkError.NoError or len(data) > MAX_IMAGE_BYTES or not image.loadFromData(data):
            self._failed.add(url)
            return
        if len(self._images) >= MAX_CACHED_IMAGES:
            self._images.pop(next(iter(self._images)))  # Oldest first.
        self._images[url] = image.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.loaded.emit(url)


_cache: ImageCache | None = None


def image_cache() -> ImageCache:
    global _cache
    if _cache is None:
        _cache = ImageCache()
    return _cache


def _font(base: QFont, px: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(base)
    font.setPixelSize(round(px))
    font.setWeight(weight)
    return font


class ChatDelegate(QStyledItemDelegate):
    def __init__(self, view: QListWidget, compact: bool = False) -> None:
        super().__init__(view)
        self.view = view
        self.scale = 0.88 if compact else 1.0
        base = view.font()
        self.name_font = _font(base, 12 * self.scale, QFont.Weight.DemiBold)
        self.text_font = _font(base, 14 * self.scale)
        self.badge_font = _font(base, 9.5 * self.scale, QFont.Weight.Bold)
        self.big_font = _font(base, 18 * self.scale, QFont.Weight.Black)
        self.small_font = _font(base, 11 * self.scale)
        self.pad = round(10 * self.scale)
        self.avatar = round(32 * self.scale)

    # Colours follow the app palette, so the feed works with light and dark themes.

    def _dark(self) -> bool:
        return self.view.palette().color(QPalette.ColorRole.Base).lightness() < 128

    def _text(self) -> QColor:
        return self.view.palette().color(QPalette.ColorRole.Text)

    def _muted(self) -> QColor:
        color = self._text()
        color.setAlpha(150)
        return color

    # Geometry.

    def _text_width(self) -> int:
        return max(80, self.view.viewport().width() - self.avatar - 3 * self.pad)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        event = index.data(EVENT_ROLE)
        width = self.view.viewport().width()
        if isinstance(event, ChatComment):
            text = QFontMetrics(self.text_font).boundingRect(
                QRect(0, 0, self._text_width(), 100_000), Qt.TextFlag.TextWordWrap, event.text
            )
            name_h = QFontMetrics(self.name_font).height()
            return QSize(width, max(self.avatar, name_h + 2 + text.height()) + 2 * round(7 * self.scale))
        if isinstance(event, ChatGift):
            return QSize(width, round(58 * self.scale))
        return QSize(width, round(34 * self.scale))

    # Painting.

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        event = index.data(EVENT_ROLE)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = option.rect
        if isinstance(event, ChatComment):
            self._paint_comment(painter, rect, event)
        elif isinstance(event, ChatGift):
            self._paint_gift(painter, rect, event)
        elif isinstance(event, ChatFollow):
            self._paint_follow(painter, rect, event)
        painter.restore()

    def _paint_avatar(self, painter: QPainter, x: float, y: float, size: float, user: ChatUser) -> None:
        circle = QRectF(x, y, size, size)
        path = QPainterPath()
        path.addEllipse(circle)
        image = image_cache().get(user.avatar_url) if user.avatar_url else None
        if image is not None:
            painter.save()
            painter.setClipPath(path)
            painter.drawImage(circle, image)
            painter.restore()
            return
        color = AVATAR_COLORS[zlib.crc32(user.unique_id.encode()) % len(AVATAR_COLORS)]
        painter.fillPath(path, QColor(color))
        painter.setPen(QColor("#ffffff"))
        painter.setFont(_font(self.name_font, size * 0.42, QFont.Weight.Bold))
        painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, (user.nickname or user.unique_id or "?")[:1].upper())

    def _badge(self, painter: QPainter, x: float, center_y: float, text: str, bg: str, fg: str) -> float:
        metrics = QFontMetrics(self.badge_font)
        w, h = metrics.horizontalAdvance(text) + round(10 * self.scale), metrics.height() + 2
        box = QRectF(x, center_y - h / 2, w, h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(box, h / 2, h / 2)
        painter.setPen(QColor(fg))
        painter.setFont(self.badge_font)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        return x + w + 5

    def _name_line(self, painter: QPainter, x: float, y: float, max_w: float, user: ChatUser, color: QColor) -> float:
        """Platform pill, nickname, and Host/Mod/Sub/follower badges; returns the line's height."""
        metrics = QFontMetrics(self.name_font)
        mid = y + metrics.height() / 2
        platform = PLATFORM_BADGES.get(user.platform)
        if platform:
            x = self._badge(painter, x, mid, *platform)
        name = metrics.elidedText(user.nickname or user.unique_id, Qt.TextElideMode.ElideRight, int(max_w * 0.6))
        painter.setFont(self.name_font)
        painter.setPen(QColor(color))
        painter.drawText(QPointF(x, y + metrics.ascent()), name)
        bx = x + metrics.horizontalAdvance(name) + 6
        if user.is_host:
            bx = self._badge(painter, bx, mid, "HOST", TIKTOK_PINK, "#ffffff")
        if user.is_moderator:
            bx = self._badge(painter, bx, mid, "MOD", TIKTOK_CYAN, "#0b1f1f")
        if user.is_subscriber:
            bx = self._badge(painter, bx, mid, "MEMBER" if user.platform == "youtube" else "SUB", "#f5a623", "#1f1400")
        if user.is_follower:
            bx = self._badge(painter, bx, mid, "FOLLOWER", TIKTOK_PINK, "#ffffff")
        return metrics.height()

    def _paint_comment(self, painter: QPainter, rect: QRect, event: ChatComment) -> None:
        top = rect.top() + round(7 * self.scale)
        x = rect.left() + self.pad
        self._paint_avatar(painter, x, top, self.avatar, event.user)
        tx = x + self.avatar + self.pad
        name_h = self._name_line(painter, tx, top, self._text_width(), event.user, self._muted())
        painter.setFont(self.text_font)
        painter.setPen(self._text())
        painter.drawText(
            QRect(int(tx), int(top + name_h + 2), self._text_width(), 100_000),
            Qt.TextFlag.TextWordWrap,
            event.text,
        )

    def _paint_gift(self, painter: QPainter, rect: QRect, event: ChatGift) -> None:
        card = QRectF(rect).adjusted(4, 3, -4, -3)
        gradient = QLinearGradient(card.topLeft(), card.topRight())
        gradient.setColorAt(0, QColor(254, 44, 85, 70))
        gradient.setColorAt(1, QColor(254, 44, 85, 8))
        painter.setPen(QPen(QColor(254, 44, 85, 90), 1))
        painter.setBrush(gradient)
        painter.drawRoundedRect(card, card.height() / 2, card.height() / 2)

        size = self.avatar
        x = card.left() + 8
        top = card.center().y() - size / 2
        self._paint_avatar(painter, x, top, size, event.user)
        tx = x + size + self.pad

        # Right side: gift icon, "×5", and the diamond total.
        right = card.right() - 14
        painter.setFont(self.big_font)
        # A Super Chat is one gift: show its amount rather than "×1".
        count = event.amount if event.amount and event.count == 1 else f"×{event.count}"
        count_w = QFontMetrics(self.big_font).horizontalAdvance(count)
        painter.setPen(QColor("#ffd34d" if self._dark() else "#a86a00"))
        painter.drawText(QRectF(right - count_w, card.top(), count_w, card.height()), Qt.AlignmentFlag.AlignVCenter, count)
        right -= count_w + 8
        icon = image_cache().get(event.image_url) if event.image_url else None
        if icon is not None:
            icon_size = card.height() - 10
            painter.drawImage(QRectF(right - icon_size, card.center().y() - icon_size / 2, icon_size, icon_size), icon)
            right -= icon_size + 8

        name_h = self._name_line(painter, tx, top - 1, right - tx, event.user, self._text())
        detail = f"sent {event.gift_name}"
        if event.amount and event.count != 1:
            detail += f"  ·  {event.amount}"
        elif event.diamonds:
            detail += f"  ·  {event.diamonds * event.count:,} diamonds"
        if event.message:
            detail += f"  ·  “{event.message}”"
        painter.setFont(self.small_font)
        painter.setPen(QColor("#ffc2cf" if self._dark() else TIKTOK_PINK))
        metrics = QFontMetrics(self.small_font)
        painter.drawText(
            QPointF(tx, top - 1 + name_h + 2 + metrics.ascent()),
            metrics.elidedText(detail, Qt.TextElideMode.ElideRight, int(right - tx)),
        )

    def _paint_follow(self, painter: QPainter, rect: QRect, event: ChatFollow) -> None:
        size = round(22 * self.scale)
        x = rect.left() + self.pad + (self.avatar - size) / 2
        top = rect.center().y() - size / 2
        self._paint_avatar(painter, x, top, size, event.user)
        tx = rect.left() + self.pad + self.avatar + self.pad
        metrics = QFontMetrics(self.name_font)
        baseline = rect.center().y() - metrics.height() / 2 + metrics.ascent()
        name = metrics.elidedText(event.user.nickname or event.user.unique_id, Qt.TextElideMode.ElideRight, 200)
        painter.setFont(self.name_font)
        painter.setPen(self._text())
        painter.drawText(QPointF(tx, baseline), name)
        painter.setFont(self.small_font)
        painter.setPen(QColor(TIKTOK_PINK))
        painter.drawText(QPointF(tx + metrics.horizontalAdvance(name) + 6, baseline), "followed the host")


class ChatFeed(QListWidget):
    """Scrolling chat with filters, a row cap, and a "new messages" pill."""

    def __init__(self, compact: bool = False, max_rows: int = 1000) -> None:
        super().__init__()
        self.max_rows = max_rows
        self.filters = {"comments": True, "gifts": True, "follows": True}
        self.setObjectName("chatFeed")
        self.setItemDelegate(ChatDelegate(self, compact))
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setUniformItemSizes(False)
        self._unseen = 0
        self.new_pill = QPushButton("", self)
        self.new_pill.setObjectName("newMessages")
        self.new_pill.setStyleSheet(
            "QPushButton { background: #fe2c55; color: white; border: 0; border-radius: 12px; padding: 4px 12px;"
            " font-weight: bold; }"
        )
        self.new_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_pill.clicked.connect(self.jump_to_latest)
        self.new_pill.hide()
        self.verticalScrollBar().valueChanged.connect(self._scrolled)
        image_cache().loaded.connect(lambda _url: self.viewport().update())

    def add_event(self, event: ChatEvent) -> None:
        kind = kind_of(event)
        if kind is None:
            return
        following = self._at_bottom()
        item = QListWidgetItem()
        item.setData(EVENT_ROLE, event)
        item.setData(KIND_ROLE, kind)
        self.addItem(item)
        item.setHidden(not self.filters[kind])
        while self.count() > self.max_rows:
            self.takeItem(0)
        if following:
            self.scrollToBottom()
        elif not item.isHidden():
            self._unseen += 1
            self._show_pill()

    def set_filter(self, kind: str, shown: bool) -> None:
        self.filters[kind] = shown
        for row in range(self.count()):
            item = self.item(row)
            item.setHidden(not self.filters[item.data(KIND_ROLE)])

    def jump_to_latest(self) -> None:
        self.scrollToBottom()
        self._unseen = 0
        self.new_pill.hide()

    def clear(self) -> None:
        """Remove every row and hide the new-messages pill."""
        super().clear()
        self.jump_to_latest()

    def _at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - 8

    def _scrolled(self) -> None:
        if self._at_bottom():
            self._unseen = 0
            self.new_pill.hide()

    def _show_pill(self) -> None:
        self.new_pill.setText(f"{self._unseen} new message{'s' if self._unseen != 1 else ''}  ↓")
        self.new_pill.adjustSize()
        self._place_pill()
        self.new_pill.show()
        self.new_pill.raise_()

    def _place_pill(self) -> None:
        area = self.viewport().geometry()
        self.new_pill.move(area.center().x() - self.new_pill.width() // 2, area.bottom() - self.new_pill.height() - 10)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._place_pill()
        self.doItemsLayout()  # Re-wrap rows to the new width.
