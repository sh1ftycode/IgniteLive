"""API tab: hosted links on the Ignite server, and the API on this PC, for widgets and other streaming apps."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QWidget,
)

from ignite_live.controller import Controller
from ignite_live.core import keychain
from ignite_live.core.bundled import main_server
from ignite_live.overlay.server import PAGES
from ignite_live.ui.pages.stream import disclosure, help_button, scrolling

API_DOCS = "https://github.com/sh1ftycode/IgniteLive/blob/main/docs/API.md"

HOSTED_HELP = (
    "Each overlay gets a link on your Ignite server, like a TikFinity widget link. Paste it into a Browser Source "
    "in OBS, Streamlabs Desktop, TikTok LIVE Studio, Meld or vMix, on this PC or another. Ignite sends your chat, "
    "alerts and music to the server for them while it runs. If the server stops answering, widgets on this PC "
    "switch to Ignite here by themselves, and go back when it's working again. Anyone with a link can see what "
    "that widget shows: New Links replaces them all. Turning this on makes you an account on the server; there's "
    "no sign-up."
)
CUSTOM_HELP = (
    "For your own widgets: add the script line to the widget's page and use Ignite.connect() (see the API "
    "Reference). It uses the server, and Ignite on this PC when the server doesn't answer. The control key also "
    "lets widgets run commands, if remote control is on in Preferences, Server."
)
LOCAL_HELP = (
    "Apps and widgets on this PC can use Ignite directly: a Browser Source in any streaming app, a Stream Deck "
    "button, a bot. The events address gets chat, gifts, follows, likes, joins and Ignite's state. Commands (go "
    "live, scenes, clips, music, alerts, text to speech) also need the control key: only give it to things you "
    "trust."
)


def read_only(placeholder: str = "") -> QLineEdit:
    field = QLineEdit()
    field.setReadOnly(True)
    field.setPlaceholderText(placeholder)
    return field


def copy_row(field: QLineEdit, *extra: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(field, 1)
    copy = QPushButton("Copy")
    copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(field.text()))
    row.addWidget(copy)
    for widget in extra:
        row.addWidget(widget)
    return row


def secret_row(field: QLineEdit, *extra: QWidget) -> QHBoxLayout:
    """A key: hidden until Show, with Copy (which copies it either way)."""
    field.setEchoMode(QLineEdit.EchoMode.Password)
    show = QPushButton("Show")
    show.setCheckable(True)
    show.toggled.connect(lambda shown: (
        field.setEchoMode(QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password),
        show.setText("Hide" if shown else "Show")))
    return copy_row(field, show, *extra)


def titled(title: str, help_text: str) -> tuple[QGroupBox, QFormLayout]:
    """A section with its "?" at the end of the first row."""
    box = QGroupBox(title)
    form = QFormLayout(box)
    form.help = help_button(help_text)  # type: ignore[attr-defined] - placed by the caller
    return box, form


class ApiPage(QWidget):
    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        layout = scrolling(self)
        layout.addWidget(self._hosted())
        layout.addWidget(self._local())
        docs = QPushButton("API Reference…")
        docs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(API_DOCS)))
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(docs)
        layout.addLayout(footer)
        layout.addStretch(1)
        controller.hosted_changed.connect(self._fill)
        controller.settings_changed.connect(lambda _settings: self._fill())
        self._fill()

    # Hosted links.

    def _hosted(self) -> QGroupBox:
        box, form = titled("Hosted links", HOSTED_HELP)
        self.hosted_state = QLabel()
        self.hosted_toggle = QPushButton("Turn On")
        self.hosted_toggle.clicked.connect(
            lambda: self.controller.set_hosted_widgets(not self.controller.settings.hosted_widgets))
        top = QHBoxLayout()
        top.addWidget(self.hosted_state, 1)
        top.addWidget(self.hosted_toggle)
        top.addWidget(form.help)
        form.addRow(top)

        self.hosted_page = QComboBox()
        for page, (title, width, height) in PAGES.items():
            self.hosted_page.addItem(f"{title}  ({width} × {height})", page)
        self.hosted_page.currentIndexChanged.connect(lambda _: self._fill())
        self.hosted_url = read_only()
        widget_row = copy_row(self.hosted_url)
        widget_row.insertWidget(0, self.hosted_page)
        form.addRow("Widget:", widget_row)

        more = QWidget()
        more_form = QFormLayout(more)
        more_form.setContentsMargins(0, 4, 0, 0)
        self.hosted_script = read_only()
        script_row = copy_row(self.hosted_script)
        script_row.addWidget(help_button(CUSTOM_HELP))
        more_form.addRow("Custom widgets:", script_row)
        self.hosted_api = read_only()
        more_form.addRow("API address:", copy_row(self.hosted_api))
        self.hosted_control = read_only("Only for widgets that run commands")
        new_control = QPushButton("New…")
        new_control.setToolTip("Make a new control key; the old one stops working")
        new_control.clicked.connect(self._new_hosted_control_key)
        more_form.addRow("Control key:", secret_row(self.hosted_control, new_control))
        new_links = QPushButton("New Links…")
        new_links.setToolTip("Replace every link: the old ones stop working everywhere")
        new_links.clicked.connect(self._new_hosted_links)
        account = QPushButton("My Account on the Server…")
        account.setToolTip("Opens the server's website, signed in")
        account.clicked.connect(lambda: self.controller.open_server_account(
            lambda url: QDesktopServices.openUrl(QUrl(url))))
        buttons = QHBoxLayout()
        buttons.addWidget(new_links)
        buttons.addWidget(account)
        buttons.addStretch(1)
        more_form.addRow(buttons)
        self.hosted_more = disclosure("More", more)
        form.addRow(self.hosted_more)
        self.hosted_parts = [self.hosted_page, self.hosted_url, self.hosted_more]
        return box

    # The API on this PC.

    def _local(self) -> QGroupBox:
        box, form = titled("On this PC", LOCAL_HELP)
        self.local_events = read_only()
        events_row = copy_row(self.local_events)
        events_row.addWidget(form.help)
        form.addRow("Events:", events_row)
        more = QWidget()
        more_form = QFormLayout(more)
        more_form.setContentsMargins(0, 4, 0, 0)
        self.local_key = read_only()
        renew = QPushButton("New…")
        renew.setToolTip("Make a new control key; whatever has the old one can't run commands any more")
        renew.clicked.connect(self._new_api_key)
        more_form.addRow("Control key:", secret_row(self.local_key, renew))
        self.local_command = read_only()
        more_form.addRow("Example command:", copy_row(self.local_command))
        form.addRow(disclosure("More", more))
        return box

    # Updates.

    def _fill(self) -> None:
        s, overlay = self.controller.settings, self.controller.overlay
        on = s.hosted_widgets and bool(s.hosted_base)
        server = s.backend_url or main_server()
        if on:
            self.hosted_state.setText("On")
        elif server:
            self.hosted_state.setText("Off")
        else:
            self.hosted_state.setText("Off. Set a server in Preferences, Server.")
        self.hosted_toggle.setText("Turn Off" if s.hosted_widgets else "Turn On")
        self.hosted_toggle.setEnabled(bool(server) or s.hosted_widgets)
        for part in self.hosted_parts:
            part.setEnabled(on)
        page = self.hosted_page.currentData()
        self.hosted_url.setText(f"{s.hosted_base}/{page}" if on else "")
        self.hosted_script.setText(f'<script src="{s.hosted_base}/ignite.js"></script>' if on else "")
        self.hosted_api.setText(s.hosted_api if on else "")
        self.hosted_control.setText((keychain.load(keychain.HOSTED_CONTROL_KEY) or "") if on else "")
        self.local_events.setText(overlay.api_url("/ws", websocket=True))
        self.local_key.setText(s.api_key)
        self.local_command.setText(overlay.api_url("/commands/skip_song", control=True))

    def _new_hosted_links(self) -> None:
        if QMessageBox.question(self, "New links", "Replace every hosted link? The old ones stop working right "
                                                   "away, so paste the new ones into your streaming apps.") \
                == QMessageBox.StandardButton.Yes:
            self.controller.new_hosted_links()

    def _new_hosted_control_key(self) -> None:
        if QMessageBox.question(self, "New control key", "Make a new control key for hosted widgets? Widgets "
                                                         "using the old one can't run commands any more.") \
                == QMessageBox.StandardButton.Yes:
            self.controller.new_hosted_control_key()

    def _new_api_key(self) -> None:
        if QMessageBox.question(self, "New control key", "Make a new control key for this PC? Apps and buttons "
                                                         "using the old one can't run commands any more. Events "
                                                         "keep working.") == QMessageBox.StandardButton.Yes:
            self.controller.new_api_key()
