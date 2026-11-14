"""Stream tab: two steps to go live. 1) get a stream key, 2) send it to OBS and start.

Everything rarely needed (the key itself, a Streamlabs token, a key from elsewhere) sits
under "More options", closed by default.
"""

from __future__ import annotations

import html
import re
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import Controller
from ignite_live.integrations.backend import CachedKey
from ignite_live.integrations.obs_studio import Studio
from ignite_live.integrations.obs_targets import StreamTarget
from ignite_live.integrations.streamlabs import AccountInfo
from ignite_live.ui.actions import AppActions, action_button
from ignite_live.ui.theme import LABEL_PX, RADIUS, colors, set_live, set_props, theme_changed, tint

CONTENT_WIDTH = 760  # The page's column; wider screens get margins instead of stretched fields.


def hours_left(expires_at: float) -> str:
    remaining = max(0.0, expires_at - time.time())
    return f"{remaining / 3600:.0f} h" if remaining >= 3600 else f"{max(1, int(remaining // 60))} min"


def buttons(*widgets: QWidget) -> QHBoxLayout:
    """A right-aligned button row, as in standard dialogs."""
    layout = QHBoxLayout()
    layout.addStretch(1)
    for widget in widgets:
        layout.addWidget(widget)
    return layout


HELP_AFTER = 70  # Longer explanations fold away behind a "?" button.


def note(text: str, inline: bool = False) -> QWidget:
    """A short note in the secondary text colour; a longer explanation becomes a "?" at the end of the
    section that shows it on click or hover (``inline`` keeps it as text, e.g. in the setup guide)."""
    if len(text) >= HELP_AFTER and not inline:
        return HelpNote(text)
    label = QLabel(text)
    label.setWordWrap(True)
    label.setEnabled(False)  # Drawn in the style's secondary text colour.
    return label


def _rich(text: str) -> str:
    """As rich text, so Qt wraps it (one long line otherwise). Notes may already use <b>…</b>."""
    paragraphs = text.split("\n\n")
    if not RICH.search(text):
        paragraphs = [html.escape(part) for part in paragraphs]
    return "".join(f"<p>{part}</p>" for part in paragraphs)


RICH = re.compile(r"<(b|i|a|br)\b[^>]*>", re.IGNORECASE)


def help_button(text: str) -> QToolButton:
    """A small "?" that shows ``text``: on hover, and on click (for touch screens and the keyboard)."""
    button = QToolButton()
    button.setText("?")
    button.setAutoRaise(True)
    button.setCursor(Qt.CursorShape.WhatsThisCursor)
    button.setAccessibleName("Help")
    set_help(button, text)
    button.clicked.connect(lambda: QToolTip.showText(button.mapToGlobal(button.rect().bottomLeft()),
                                                     button.toolTip(), button))
    return button


def set_help(button: QToolButton, text: str) -> None:
    button.setToolTip(_rich(text))
    button.setAccessibleDescription(RICH.sub("", text))


class HelpNote(QWidget):
    """A "?" in place of a paragraph of explanation. In a form it moves to the end of the row before it (the
    setting it explains) and its own row goes; two in a row share one "?". Otherwise it's a row of its own."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text
        self.moved = False
        self._checked = False
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.addStretch(1)
        self.button = help_button(text)
        self._row.addWidget(self.button)

    def add(self, text: str) -> None:
        self.text += "\n\n" + text
        set_help(self.button, self.text)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if not self._checked:
            self._checked = True
            QTimer.singleShot(0, self, self._place)  # After showing: changing the form while Qt shows it crashes.

    def _place(self) -> None:
        parent = self.parentWidget()
        layout = parent.layout() if parent is not None else None
        previous = _previous_widget(self)
        if isinstance(previous, HelpNote) and (previous.moved or previous.isVisible()):
            previous.add(self.text)
            self._hide_row(layout)
            return
        if not isinstance(layout, QFormLayout):
            return
        row, _role = layout.getWidgetPosition(self)
        if row <= 0:
            return
        field = layout.itemAt(row - 1, QFormLayout.ItemRole.FieldRole)
        spanning = layout.itemAt(row - 1, QFormLayout.ItemRole.SpanningRole)
        item = field or spanning
        if item is None:
            return
        if item.layout() is not None and isinstance(item.layout(), QBoxLayout):
            item.layout().addWidget(self.button)  # A row of controls: the "?" goes last.
        elif item.widget() is not None:
            # A single control: put it and the "?" side by side in that row.
            label_item = layout.itemAt(row - 1, QFormLayout.ItemRole.LabelRole)
            label = label_item.widget() if label_item is not None else None
            taken = layout.takeRow(row - 1)
            control = item.widget()
            holder = QWidget()
            line = QHBoxLayout(holder)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(control, 1)
            line.addWidget(self.button)
            if label is not None:
                layout.insertRow(row - 1, label, holder)
            elif taken.fieldItem is None:  # It was a spanning row.
                layout.insertRow(row - 1, holder)
            else:
                layout.insertRow(row - 1, "", holder)
        else:
            return
        self.moved = True
        self._hide_row(layout)

    def _hide_row(self, layout) -> None:
        if isinstance(layout, QFormLayout):
            row, _role = layout.getWidgetPosition(self)
            if row >= 0:
                layout.setRowVisible(row, False)
                return
        self.hide()


def _previous_widget(widget: QWidget) -> QWidget | None:
    """The widget just before ``widget`` in its parent's layout (a spanning form row, or a box layout item)."""
    parent = widget.parentWidget()
    layout = parent.layout() if parent is not None else None
    if isinstance(layout, QFormLayout):
        row, _role = layout.getWidgetPosition(widget)
        for earlier in range(row - 1, -1, -1):
            item = layout.itemAt(earlier, QFormLayout.ItemRole.SpanningRole)
            if item is not None and item.widget() is not None:
                return item.widget()
            if layout.itemAt(earlier, QFormLayout.ItemRole.FieldRole) is not None:
                return None  # A normal row in between: not neighbours.
        return None
    if layout is not None and layout.indexOf(widget) > 0:
        item = layout.itemAt(layout.indexOf(widget) - 1)
        return item.widget() if item is not None else None
    return None


def scrolling(page: QWidget, layout_type: type[QBoxLayout] | type[QGridLayout] = QVBoxLayout):
    """A ``layout_type`` for ``page``'s content, inside a scroll area: in a small window the page
    scrolls rather than squashing its tables or cutting off its buttons."""
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    body = QWidget()
    scroll.setWidget(body)
    outer.addWidget(scroll)
    return layout_type(body)


def disclosure(title: str, content: QWidget) -> QWidget:
    """A "▸ title" toggle that shows or hides ``content`` (closed to start with)."""
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    toggle = QToolButton()
    toggle.setText(title)
    toggle.setCheckable(True)
    toggle.setAutoRaise(True)
    toggle.setArrowType(Qt.ArrowType.RightArrow)
    toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    content.setVisible(False)

    def flip(shown: bool) -> None:
        toggle.setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)
        content.setVisible(shown)

    toggle.toggled.connect(flip)
    layout.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
    layout.addWidget(content)
    box.toggle = toggle  # type: ignore[attr-defined]
    return box


def chip(text: str, color: str) -> str:
    """Stylesheet for a small status pill: a faint tint of ``color`` with ``color`` text."""
    return (f"QLabel {{ color: {color}; background: {tint(color, 0.14)}; border-radius: {RADIUS}px; padding: 2px 8px; "
            f"font-size: {LABEL_PX}px; font-weight: 600; }}")


class StreamPage(QWidget):
    def __init__(self, controller: Controller, actions: AppActions) -> None:
        super().__init__()
        self.controller = controller
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        holder = QWidget()
        scroll.setWidget(holder)
        centre = QHBoxLayout(holder)
        column = QWidget()
        column.setMaximumWidth(CONTENT_WIDTH)
        centre.addWidget(column)
        page = QVBoxLayout(column)
        page.setContentsMargins(16, 16, 16, 16)
        page.setSpacing(8)

        # Header: what to do, and where things stand.
        top = QHBoxLayout()
        title = QLabel("Go live")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        top.addWidget(title)
        top.addStretch(1)
        self.state = QLabel()
        top.addWidget(self.state)
        top.addWidget(help_button("Get a stream key, then send it to OBS and start streaming. Video goes straight "
                                  "from OBS to TikTok."))
        page.addLayout(top)

        # 1. Stream key.
        key_box = QGroupBox("1   Stream key")
        form = QFormLayout(key_box)
        account_row = QHBoxLayout()
        self.account_text = QLabel("Not signed in")
        self.account_text.setWordWrap(True)
        account_row.addWidget(self.account_text, 1)
        self.sign_in = action_button(actions.sign_in, "Sign In…")
        self.sign_out = action_button(actions.sign_out, "Sign Out")
        account_row.addWidget(self.sign_in)
        account_row.addWidget(self.sign_out)
        form.addRow("Streamlabs:", account_row)
        self.title = QLineEdit(controller.settings.stream_title)
        self.title.setPlaceholderText("What are you streaming?")
        form.addRow("&Title:", self.title)
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.category.lineEdit().setPlaceholderText("Type to search")
        self.category.addItem("Other", "")
        self._search = QTimer(self, singleShot=True, interval=400)
        self._search.timeout.connect(lambda: controller.search_categories(self.category.currentText()))
        self.category.lineEdit().textEdited.connect(lambda _: self._search.start())
        form.addRow("&Category:", self.category)
        self.key_state = QLabel("No key yet")
        form.addRow("Key:", self.key_state)
        get_key = action_button(actions.get_key)
        set_props(get_key, primary=True)
        # Experimental (Preferences → Advanced): a new title or category mid-stream, by restarting the LIVE.
        self.relaunch = QPushButton("Update While Live…")
        self.relaunch.setToolTip("Experimental: restart the LIVE with this title and category (a new key)")
        self.relaunch.clicked.connect(self._relaunch)
        form.addRow(buttons(self.relaunch, action_button(actions.new_key, "Generate New Key"), get_key))

        more = QWidget()
        more_form = QFormLayout(more)
        more_form.setContentsMargins(0, 4, 0, 0)
        self.server = QLineEdit()
        self.server.setReadOnly(True)
        more_form.addRow("Server:", self.server)
        self.key = QLineEdit()
        self.key.setReadOnly(True)
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.reveal = QCheckBox("Show")
        self.reveal.toggled.connect(
            lambda shown: self.key.setEchoMode(QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password)
        )
        key_row = QHBoxLayout()
        key_row.addWidget(self.key, 1)
        key_row.addWidget(self.reveal)
        more_form.addRow("Key:", key_row)
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("If browser sign-in doesn't work")
        use_token = QPushButton("Use Token")
        use_token.clicked.connect(self._use_token)
        token_row = QHBoxLayout()
        token_row.addWidget(self.token, 1)
        token_row.addWidget(use_token)
        more_form.addRow("Streamlabs token:", token_row)
        more_form.addRow(note("Streamlabs keys use its private API, which can change without notice. "
                              "Terminal alternative: ignite-streamlabs-login"))
        self.manual_server = QLineEdit()
        self.manual_server.setPlaceholderText("rtmp:// or rtmps://")
        self.manual_key = QLineEdit()
        self.manual_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.manual_key.setPlaceholderText("Stream key")
        save = QPushButton("Use This Key")
        save.clicked.connect(self._manual)
        manual_row = QHBoxLayout()
        manual_row.addWidget(self.manual_server, 1)
        manual_row.addWidget(self.manual_key, 1)
        manual_row.addWidget(save)
        more_form.addRow("Your own key:", manual_row)
        more_form.addRow(note("A key from TikTok LIVE Studio or anywhere else; no Streamlabs needed."))
        self.more = disclosure("More options: the key itself, a token, or your own key", more)
        form.addRow(self.more)
        page.addWidget(key_box)

        # 2. OBS.
        obs_box = QGroupBox("2   Send to OBS and go live")
        form = QFormLayout(obs_box)
        self.target = QComboBox()
        self.target.activated.connect(lambda _: controller.set_stream_target(self.target.currentData()))
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(actions.refresh_outputs.trigger)
        target_row = QHBoxLayout()
        target_row.addWidget(self.target, 1)
        target_row.addWidget(refresh)
        form.addRow("OBS output:", target_row)
        self.target_hint = note("")
        form.addRow(self.target_hint)
        start = action_button(actions.toggle_stream)
        set_live(start, controller.obs_streaming)  # Orange when ready, red with a pulsing dot while live.
        controller.obs_streaming_changed.connect(lambda live: set_live(start, live))
        end = action_button(actions.end_live, "End LIVE…")
        set_props(end, danger=True)
        row = QHBoxLayout()
        row.addWidget(end)
        row.addStretch(1)
        row.addWidget(action_button(actions.check_stream, "Check First…"))
        row.addWidget(action_button(actions.send_key, "Send to OBS"))
        row.addWidget(start)
        form.addRow(row)
        page.addWidget(obs_box)
        page.addStretch(1)

        actions.get_key.triggered.connect(lambda: self.get_key(False))
        actions.new_key.triggered.connect(lambda: self.get_key(True))
        actions.end_live.triggered.connect(self._end_live)
        controller.account_changed.connect(self._account)
        controller.categories_loaded.connect(self._categories)
        controller.key_changed.connect(self._key)
        controller.targets_changed.connect(self._targets)
        controller.settings_changed.connect(lambda _: self._targets(controller.targets))
        controller.settings_changed.connect(self._settings)
        controller.obs_streaming_changed.connect(lambda _live: self._settings(controller.settings))
        controller.studio_changed.connect(self._studio)
        theme_changed().connect(self._restyle)
        self._targets(controller.targets)
        self._account(controller.account)
        self._key(controller.active_key)
        self._settings(controller.settings)

    def _category_id(self) -> str:
        index = self.category.findText(self.category.currentText())
        return str((self.category.itemData(index) if index >= 0 else "") or "")

    def get_key(self, force_new: bool) -> None:
        self.controller.get_stream_key(self.title.text(), self._category_id(), "0", force_new)

    def _settings(self, settings) -> None:
        self.relaunch.setVisible(settings.experimental_relaunch)
        self.relaunch.setEnabled(self.controller.obs_streaming)

    def _relaunch(self) -> None:
        answer = QMessageBox.question(
            self, "Update While Live (experimental)",
            f"Restart the LIVE as “{self.title.text().strip()}” in {self.category.currentText() or 'Other'}?\n\n"
            "Ignite gets a new key, stops your OBS output, ends this LIVE, and starts a new one with the new key. "
            "Viewers see the LIVE end and a new one begin, and some may not follow. TikTok chat reconnects by itself once the new LIVE is up.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.controller.relaunch_live(self.title.text(), self._category_id())

    def _use_token(self) -> None:
        if self.token.text().strip():
            self.controller.use_streamlabs_token(self.token.text(), remember=True)
            self.token.clear()

    def _manual(self) -> None:
        self.controller.use_manual_key(self.manual_server.text(), self.manual_key.text())
        self.manual_key.clear()

    def _end_live(self) -> None:
        answer = QMessageBox.question(
            self, "End LIVE", "End the TikTok LIVE and retire this key? You'll need a new key to go live again."
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.controller.end_live()

    # Updates.

    def _show_state(self) -> None:
        key = self.controller.active_key
        if self.controller.studio.streaming:
            text, color = "LIVE", colors().live
        elif key is not None:
            text, color = "Key ready", colors().healthy
        else:
            text, color = "No key yet", colors().text_muted
        self.state.setText(text)
        self.state.setStyleSheet(chip(text, color))

    def _restyle(self, _palette=None) -> None:
        self._show_state()  # The chip's colours follow the mode.

    def _studio(self, _studio: Studio) -> None:
        self._show_state()

    def _account(self, info: AccountInfo | None) -> None:
        if info is None:
            self.account_text.setText("Not signed in")
        elif info.can_be_live:
            self.account_text.setText(f"@{info.username} (can go LIVE)")
        else:
            self.account_text.setText(f"@{info.username} (not approved for LIVE: {info.application_status.lower()})")
        self.sign_in.setVisible(info is None)
        self.sign_out.setVisible(info is not None)

    def _categories(self, categories: list) -> None:
        typed = self.category.currentText()
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem("Other", "")
        for item in categories:
            self.category.addItem(item["name"], item["id"])
        self.category.setEditText(typed)
        self.category.blockSignals(False)

    def _key(self, key: CachedKey | None) -> None:
        self.server.setText(key.credentials.server if key else "")
        self.key.setText(key.credentials.key if key else "")
        self.key_state.setText(
            f"Ready ({key.credentials.source}, expires in {hours_left(key.expires_at)})" if key
            else "None yet. Get Stream Key reuses your saved key while it's valid (48 hours, or until you end "
                 "the LIVE)."
        )
        self.key_state.setWordWrap(True)
        self._show_state()

    def _targets(self, targets: list[StreamTarget]) -> None:
        chosen = self.controller.settings.stream_target
        self.target.clear()
        model: QStandardItemModel = self.target.model()
        for target in targets:
            label_text = target.label if target.can_set_key else f"{target.label} (key can't be set by apps)"
            self.target.addItem(label_text, target.id)
            if not target.can_set_key:
                item = model.item(self.target.count() - 1)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        index = self.target.findData(chosen)
        if index < 0:
            # Saved output not in OBS right now (OBS closed, or renamed): keep showing it.
            self.target.addItem(f"{chosen} (not found in OBS)", chosen)
            index = self.target.count() - 1
        self.target.setCurrentIndex(index)
        found = self.controller.stream_target
        if found is None:
            self.target_hint.setText("That output isn't in OBS right now. Open OBS, or pick another one.")
        elif found.kind == "main" and any(t.kind == "vertical" for t in targets):
            self.target_hint.setText("If Vertical Canvas is set to start with the main stream, it goes live too.")
        else:
            self.target_hint.setText("Only this output gets the key and is started by Start Streaming.")
