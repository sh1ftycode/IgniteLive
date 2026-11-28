"""Stream Control, and the small form helpers every screen shares (notes, "?" buttons, disclosures).

Going live is two steps: get a stream key, then send it to OBS and start. Everything rarely needed (the key
itself, a Streamlabs token, a key from elsewhere) sits under "More options", closed by default.
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
from ignite_live.core.events import PLATFORMS, ConnectionState
from ignite_live.integrations.backend import CachedKey
from ignite_live.integrations.obs_studio import Studio, clock
from ignite_live.integrations.obs_targets import StreamTarget
from ignite_live.integrations.streamlabs import AccountInfo
from ignite_live.ui import lucide
from ignite_live.ui.actions import AppActions, action_button
from ignite_live.ui.kit import (
    Banner,
    Card,
    MetricCard,
    PageContainer,
    button,
    hline,
    icon_button,
    label,
    recolor_icon_button,
    restyle_buttons,
)
from ignite_live.ui.theme import LABEL_PX, RADIUS, colors, set_live, set_props, tabular, theme_changed, tint

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
    """Stream Control: everything for going live, in the order you need it.

    Status across the top; then the go-live controls and the output they drive; the stream key (Streamlabs or
    your own); and OBS itself (scenes, recording and clips, health) beside them. Errors from OBS or Streamlabs
    show in a banner rather than a status bar you might not see.
    """

    def __init__(self, controller: Controller, actions: AppActions) -> None:
        super().__init__()
        self.controller = controller
        self.actions = actions
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page = PageContainer()
        outer.addWidget(page)

        self.banner = Banner("", error=True)
        self.banner.hide()
        page.add(self.banner)

        # Status.
        metrics = QHBoxLayout()
        metrics.setSpacing(16)
        self.status_tile = MetricCard("radio", "Status", "accent")
        self.uptime_tile = MetricCard("clock", "Uptime", "muted")
        self.connections_tile = MetricCard("plug", "Chats connected", "accent")
        for tile in (self.status_tile, self.uptime_tile, self.connections_tile):
            metrics.addWidget(tile, 1)
        page.add_layout(metrics)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(16)
        right = QVBoxLayout()
        right.setSpacing(16)
        columns.addLayout(left, 3)
        columns.addLayout(right, 2)
        page.add_layout(columns)

        # Go live.
        self.obs_box = Card("Go live", icon="play")
        self.state = QLabel()
        self.obs_box.add_header_widget(self.state)
        self.obs_box.add_header_widget(help_button(
            "Get a stream key, then send it to OBS and start streaming. Video goes straight from OBS to TikTok."))
        main_row = QHBoxLayout()
        main_row.setSpacing(8)
        self.start = button("Go Live", "play", kind="primary",
                            tip="Check OBS, the key, audio and overlays, then start the chosen output")
        self.start.setProperty("big", True)
        self.start.clicked.connect(lambda: self.actions.go_live())
        self.stop_button = button("Stop Stream", "square", tip="Stop OBS's output (asks first)")
        self.stop_button.setProperty("big", True)
        self.stop_button.clicked.connect(self._stop)
        main_row.addWidget(self.start, 1)
        main_row.addWidget(self.stop_button, 1)
        self.obs_box.body.addLayout(main_row)
        more_row = QHBoxLayout()
        more_row.setSpacing(8)
        self.check_first = button("Check First", "circle-check", tip=actions.check_stream.toolTip())
        self.check_first.clicked.connect(actions.check_stream.trigger)
        self.send_key_button = button("Send Key to OBS", "key", tip=actions.send_key.toolTip())
        self.send_key_button.clicked.connect(actions.send_key.trigger)
        self.end = button("End LIVE…", "circle-x", kind="danger", tip=actions.end_live.toolTip())
        self.end.clicked.connect(actions.end_live.trigger)
        # Experimental (Settings → Advanced): a new title or category mid-stream, by restarting the LIVE.
        self.relaunch = button("Update While Live…", "refresh-cw",
                               tip="Experimental: restart the LIVE with this title and category (a new key)")
        self.relaunch.clicked.connect(self._relaunch)
        for widget in (self.check_first, self.send_key_button, self.relaunch):
            more_row.addWidget(widget)
        more_row.addStretch(1)
        more_row.addWidget(self.end)
        self.obs_box.body.addLayout(more_row)
        self.obs_box.body.addWidget(hline())
        output = QGridLayout()
        output.setHorizontalSpacing(12)
        output.setVerticalSpacing(8)
        output.addWidget(label("Output", "muted"), 0, 0)
        self.target = QComboBox()
        self.target.setAccessibleName("OBS output")
        self.target.activated.connect(lambda _: controller.set_stream_target(self.target.currentData()))
        refresh = icon_button("refresh-cw", "Reload OBS's outputs")
        refresh.clicked.connect(actions.refresh_outputs.trigger)
        self.refresh_outputs = refresh
        target_row = QHBoxLayout()
        target_row.addWidget(self.target, 1)
        target_row.addWidget(refresh)
        output.addLayout(target_row, 0, 1)
        self.target_hint = label("", "muted", wrap=True)
        output.addWidget(self.target_hint, 1, 1)
        output.setColumnStretch(1, 1)
        self.obs_box.body.addLayout(output)
        left.addWidget(self.obs_box)

        # Stream key.
        self.key_box = Card("Stream key", icon="key")
        key_form = QWidget()
        self.key_box.body.addWidget(key_form)
        form = QFormLayout(key_form)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        account_row = QHBoxLayout()
        self.account_text = QLabel("Not signed in")
        self.account_text.setWordWrap(True)
        account_row.addWidget(self.account_text, 1)
        self.sign_in = action_button(actions.sign_in, "Sign In…")
        self.sign_out = action_button(actions.sign_out, "Sign Out")
        account_row.addWidget(self.sign_in)
        account_row.addWidget(self.sign_out)
        form.addRow("Streamlabs", account_row)
        self.title = QLineEdit(controller.settings.stream_title)
        self.title.setPlaceholderText("What are you streaming?")
        form.addRow("&Title", self.title)
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.category.lineEdit().setPlaceholderText("Type to search")
        self.category.addItem("Other", "")
        self._search = QTimer(self, singleShot=True, interval=400)
        self._search.timeout.connect(lambda: controller.search_categories(self.category.currentText()))
        self.category.lineEdit().textEdited.connect(lambda _: self._search.start())
        form.addRow("&Category", self.category)
        self.key_state = QLabel("No key yet")
        self.key_state.setWordWrap(True)
        form.addRow("Key", self.key_state)
        get_key = action_button(actions.get_key)
        set_props(get_key, primary=True)
        self.get_key_button = get_key
        form.addRow(buttons(action_button(actions.new_key, "Generate New Key"), get_key))

        more = QWidget()
        more_form = QFormLayout(more)
        more_form.setContentsMargins(0, 4, 0, 0)
        self.server = QLineEdit()
        self.server.setReadOnly(True)
        more_form.addRow("Server", self.server)
        self.key = QLineEdit()
        self.key.setReadOnly(True)
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.reveal = QCheckBox("Show")
        self.reveal.toggled.connect(
            lambda shown: self.key.setEchoMode(QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password))
        key_row = QHBoxLayout()
        key_row.addWidget(self.key, 1)
        key_row.addWidget(self.reveal)
        more_form.addRow("Key", key_row)
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("If browser sign-in doesn't work")
        use_token = QPushButton("Use Token")
        use_token.clicked.connect(self._use_token)
        token_row = QHBoxLayout()
        token_row.addWidget(self.token, 1)
        token_row.addWidget(use_token)
        more_form.addRow("Streamlabs token", token_row)
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
        more_form.addRow("Your own key", manual_row)
        more_form.addRow(note("A key from TikTok LIVE Studio or anywhere else; no Streamlabs needed."))
        self.more = disclosure("More options: the key itself, a token, or your own key", more)
        form.addRow(self.more)
        left.addWidget(self.key_box)
        left.addStretch(1)

        # OBS: scenes, recording and clips, health.
        self.scenes_box = Card("Scenes", icon="layers")
        self.scene_list = QVBoxLayout()
        self.scene_list.setSpacing(4)
        self.scenes_box.body.addLayout(self.scene_list)
        self.scenes_empty = label("Scenes show up once OBS is connected.", "muted", wrap=True)
        self.scenes_box.body.addWidget(self.scenes_empty)
        right.addWidget(self.scenes_box)

        self.record_box = Card("Recording and clips", icon="video")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self.record_buttons = []
        for index, (action, icon) in enumerate(((actions.toggle_record, "circle-dot"), (actions.pause_record, "pause"),
                                                (actions.toggle_replay, "history"), (actions.save_replay, "scissors"),
                                                (actions.toggle_virtualcam, "camera"), (actions.screenshot, "monitor"),
                                                (actions.toggle_vertical_record, "circle-dot"),
                                                (actions.toggle_vertical_backtrack, "history"),
                                                (actions.save_vertical_clip, "scissors"))):
            widget = action_button(action)
            widget.icon_name = icon  # type: ignore[attr-defined]
            action.changed.connect(lambda w=widget, a=action: w.setVisible(a.isVisible()))
            widget.setVisible(action.isVisible())
            grid.addWidget(widget, index // 2, index % 2)
            self.record_buttons.append(widget)
        self.record_box.body.addLayout(grid)
        right.addWidget(self.record_box)

        self.health_box = Card("Status", icon="activity")
        self.health_rows: dict[str, QLabel] = {}
        health = QGridLayout()
        health.setHorizontalSpacing(16)
        health.setVerticalSpacing(6)
        for row, (key, name) in enumerate((("stream", "Stream"), ("recording", "Recording"))):
            health.addWidget(label(name, "muted"), row, 0)
            value = label("–")
            value.setFont(tabular(value.font()))
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            health.addWidget(value, row, 1)
            self.health_rows[key] = value
        health.setColumnStretch(1, 1)
        self.health_box.body.addLayout(health)
        right.addWidget(self.health_box)
        right.addStretch(1)

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
        controller.status_changed.connect(self._connections)
        theme_changed().connect(self._restyle)
        self._targets(controller.targets)
        self._account(controller.account)
        self._key(controller.active_key)
        self._settings(controller.settings)
        self._connections()
        self._studio(controller.studio)
        self._restyle()

    def _category_id(self) -> str:
        index = self.category.findText(self.category.currentText())
        return str((self.category.itemData(index) if index >= 0 else "") or "")

    def get_key(self, force_new: bool) -> None:
        self.controller.get_stream_key(self.title.text(), self._category_id(), "0", force_new)

    def _settings(self, settings) -> None:
        self.relaunch.setVisible(settings.experimental_relaunch)
        self.relaunch.setEnabled(self.controller.obs_streaming)

    def _stop(self) -> None:
        answer = QMessageBox.question(self, "Stop streaming",
                                      "Stop OBS's output? Your viewers will see the stream end. The key stays "
                                      "valid, so you can go live again.")
        if answer == QMessageBox.StandardButton.Yes:
            self.controller.set_obs_streaming(False)

    def _relaunch(self) -> None:
        answer = QMessageBox.question(
            self, "Update While Live (experimental)",
            f"Restart the LIVE as “{self.title.text().strip()}” in {self.category.currentText() or 'Other'}?\n\n"
            "Ignite gets a new key, stops your OBS output, ends this LIVE, and starts a new one with the new key. "
            "Viewers see the LIVE end and a new one begin, and some may not follow. TikTok chat reconnects by "
            "itself once the new LIVE is up.",
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
        studio, key = self.controller.studio, self.controller.active_key
        if studio.streaming:
            text, color, tile = "LIVE", colors().live, "Live"
        elif key is not None:
            text, color, tile = "Key ready", colors().healthy, "Ready"
        else:
            text, color, tile = "No key yet", colors().text_muted, "No key"
        self.state.setText(text)
        self.state.setStyleSheet(chip(text, color))
        self.status_tile.set_value(tile if studio.connected else "OBS offline")
        self.status_tile.set_live(studio.streaming)

    def _connections(self, _change=None) -> None:
        connected = sum(1 for platform in PLATFORMS
                        if self.controller.statuses[platform].state is ConnectionState.CONNECTED)
        self.connections_tile.set_value(f"{connected} of {len(PLATFORMS)}")
        problems = [f"{name}: {self.controller.statuses[source].detail}"
                    for source, name in (("obs", "OBS"), ("streamlabs", "Streamlabs"))
                    if self.controller.statuses[source].state is ConnectionState.ERROR
                    and self.controller.statuses[source].detail]
        self.banner.text.setText("\n".join(problems))
        self.banner.setVisible(bool(problems))

    def _studio(self, studio: Studio) -> None:
        self._show_state()
        live, health = studio.streaming, studio.health
        self.start.setEnabled(studio.connected and not live)
        self.stop_button.setEnabled(studio.connected and live)
        set_live(self.stop_button, live)
        self.uptime_tile.set_value(clock(health.stream_duration_ms) if live else "–")
        rows = self.health_rows
        if not studio.connected:
            for value in rows.values():
                value.setText("–")
            rows["stream"].setText("OBS isn't connected")
        else:
            rows["stream"].setText(f"Live {clock(health.stream_duration_ms)}" if live else "Not streaming")
            rows["recording"].setText(("Paused " if studio.record_paused else "Recording ")
                                      + clock(studio.record_duration_ms) if studio.recording else "Off")
        self._scenes(studio)
        restyle_buttons(self.start, self.stop_button, *self.record_buttons)

    def _scenes(self, studio: Studio) -> None:
        names = [(name, False) for name in studio.scenes]
        if studio.vertical is not None:
            names += [(name, True) for name in studio.vertical.scenes]
        shown = [(getattr(b, "scene", None), getattr(b, "vertical", None)) for b in self._scene_buttons()]
        current = {(studio.scene, False)} | ({(studio.vertical.scene, True)} if studio.vertical else set())
        if shown != names:
            for widget in self._scene_buttons():
                self.scene_list.removeWidget(widget)
                widget.hide()
                widget.deleteLater()
            for name, vertical in names:
                widget = QPushButton(("Vertical · " if vertical else "") + name)
                widget.scene, widget.vertical = name, vertical  # type: ignore[attr-defined]
                widget.setProperty("ghost", True)
                widget.setCursor(Qt.CursorShape.PointingHandCursor)
                widget.setStyleSheet("text-align: left;")
                widget.clicked.connect(lambda _=False, n=name, v=vertical: self.controller.switch_scene(n, v))
                self.scene_list.addWidget(widget)
        for widget in self._scene_buttons():
            on = (widget.scene, widget.vertical) in current
            widget.setIcon(lucide.icon("circle-dot" if on else "circle", colors().accent if on else colors().text_disabled, 14))
            set_props(widget, ghost=not on, outline=on)
        self.scenes_empty.setVisible(not names)
        self.scenes_empty.setText("Scenes show up once OBS is connected." if not studio.connected else
                                  "OBS has no scenes.")

    def _scene_buttons(self) -> list[QPushButton]:
        return [self.scene_list.itemAt(i).widget() for i in range(self.scene_list.count())
                if self.scene_list.itemAt(i).widget() is not None]

    def _restyle(self, _palette=None) -> None:
        self._show_state()  # The chip's colours follow the mode.
        for card in (self.key_box, self.obs_box, self.scenes_box, self.record_box, self.health_box):
            card.restyle()
        for tile in (self.status_tile, self.uptime_tile, self.connections_tile):
            tile.restyle()
        self.banner.restyle()
        recolor_icon_button(self.refresh_outputs)
        restyle_buttons(self.start, self.stop_button, self.check_first, self.send_key_button, self.end, self.relaunch,
                        *self.record_buttons)

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
            self.target_hint.setText("Only this output gets the key and is started by Go Live.")
