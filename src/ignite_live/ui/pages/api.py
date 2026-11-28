"""Integrations: chat platforms, services, and hosted links on the Ignite server, and the API on this PC, for widgets and other streaming apps."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import Controller
from ignite_live.core import keychain
from ignite_live.core.bundled import main_server
from ignite_live.core.events import ConnectionState, StatusChange
from ignite_live.overlay.server import PAGES
from ignite_live.ui.kit import (
    Card,
    PageContainer,
    StatusIndicator,
    button,
    icon_label,
    label,
    recolor,
    restyle_buttons,
)
from ignite_live.ui.pages.stream import disclosure, help_button
from ignite_live.ui.theme import colors, set_props, theme_changed

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


def titled(title: str, help_text: str, icon: str, primary: bool = False) -> tuple[Card, QFormLayout]:
    """A card with its "?" at the end of the first row."""
    card = Card(title, icon=icon, primary=primary)
    form = QFormLayout()
    card.body.addLayout(form)
    form.help = help_button(help_text)  # type: ignore[attr-defined] - placed by the caller
    return card, form


STATE_TONE = {ConnectionState.CONNECTED: "healthy", ConnectionState.CONNECTING: "warning",
              ConnectionState.DEGRADED: "warning", ConnectionState.ERROR: "error", ConnectionState.OFFLINE: "muted"}
STATE_WORD = {ConnectionState.CONNECTED: "Connected", ConnectionState.CONNECTING: "Connecting…",
              ConnectionState.DEGRADED: "Waiting", ConnectionState.ERROR: "Problem", ConnectionState.OFFLINE: "Not connected"}


class IntegrationCard(QFrame):
    """One platform or service: what it is, its state (connected, waiting, a problem, or off), the account or
    detail behind it, and what you can do about it."""

    def __init__(self, title: str, icon: str, about: str) -> None:
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(10)
        self.icon = icon_label(icon, None, 20)
        head.addWidget(self.icon)
        head.addWidget(label(title, "cardTitle"))
        head.addStretch(1)
        self.state = StatusIndicator("Not connected", "muted")
        self.state.layout().setStretch(2, 0)
        head.addWidget(self.state)
        layout.addLayout(head)
        layout.addWidget(label(about, "muted", wrap=True))
        self.detail = label("", wrap=True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.detail)
        layout.addStretch(1)
        self.button_row = QHBoxLayout()
        self.button_row.setSpacing(8)
        layout.addLayout(self.button_row)
        self.buttons: list[QPushButton] = []

    def add_button(self, text: str, icon: str, slot, kind: str = "") -> QPushButton:
        widget = button(text, icon, kind=kind)
        widget.clicked.connect(slot)
        if not self.buttons:
            self.button_row.addStretch(1)
        self.button_row.addWidget(widget)
        self.buttons.append(widget)
        return widget

    def set_state(self, text: str, tone: str, detail: str = "") -> None:
        self.state.set(text, tone)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        set_props(self.detail, tone="error" if tone == "error" else "")

    def restyle(self) -> None:
        recolor(self.icon, colors().accent)
        restyle_buttons(*self.buttons)


class ApiPage(QWidget):
    """Integrations: the chat platforms and services Ignite works with, each with its state and what to do, then
    the links and API that let other apps and widgets use Ignite. ``open_page_requested``: a Configure button
    (a Settings section, e.g. "prefs-OBS")."""

    open_page_requested = Signal(str)

    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page = PageContainer()
        outer.addWidget(page)

        page.add(label("Streaming platforms", "sectionTitle"))
        platforms = QGridLayout()
        platforms.setSpacing(16)
        self.cards: dict[str, IntegrationCard] = {}
        for column, (key, name, field) in enumerate((("tiktok", "TikTok", "tiktok_username"),
                                                     ("twitch", "Twitch", "twitch_channel"),
                                                     ("youtube", "YouTube", "youtube_channel"))):
            card = IntegrationCard(name, "radio", f"{name} chat, gifts and follows in Live Chat, alerts and overlays.")
            card.field = field  # type: ignore[attr-defined]
            card.toggle = card.add_button("Connect", "plug", lambda _=False, k=key: self._toggle_chat(k))
            card.add_button("Set Up", "sliders", lambda: self.open_page_requested.emit("chat"))
            platforms.addWidget(card, 0, column)
            self.cards[key] = card
        tiktok = IntegrationCard("TikTok account", "user",
                                 "Signed in on your Ignite server's website: chat as yourself from Ignite, and "
                                 "moderate (mod, mute, kick) from Live Chat.")
        tiktok.add_button("Open Server Website", "external-link", self._open_server)
        platforms.addWidget(tiktok, 1, 0)
        self.cards["tiktok_account"] = tiktok
        streamlabs = IntegrationCard("Streamlabs", "key", "Gets your TikTok LIVE stream key (Stream Control).")
        self.streamlabs_in = streamlabs.add_button("Sign In", "log-in", controller.login_streamlabs, "primary")
        self.streamlabs_out = streamlabs.add_button("Sign Out", "log-out", controller.sign_out_streamlabs)
        platforms.addWidget(streamlabs, 1, 1)
        self.cards["streamlabs"] = streamlabs
        page.add_layout(platforms)

        page.add(label("Services", "sectionTitle"))
        services = QGridLayout()
        services.setSpacing(16)
        for index, (key, name, icon, about, prefs) in enumerate((
                ("obs", "OBS Studio", "monitor", "Scenes, streaming, recording and clips, through OBS WebSocket.", "OBS"),
                ("backend", "Ignite server", "server", "Your account, hosted widget links, the web dashboard and "
                                                       "remote control.", "Server"),
                ("discord", "Discord", "message-circle", "Chat, gifts and follows sent to a Discord channel, and "
                                                        "Rich Presence on your profile.", "Discord"),
                ("tts", "Text to speech", "audio-lines", "Chat read aloud on stream.", "Text to speech"),
                ("minecraft", "Minecraft", "box", "Gifts and chat run commands on your server through ServerTap.",
                 "Minecraft"),
                ("overlay", "Overlay server", "layers", "Serves your overlays to OBS on this PC.", ""))):
            card = IntegrationCard(name, icon, about)
            if key == "obs":
                card.add_button("Reconnect", "refresh-cw", controller.reconnect_obs)
            if key == "minecraft":
                card.add_button("Test", "flask", self._test_minecraft)
            if prefs:
                card.add_button("Configure", "settings", lambda _=False, p=prefs: self.open_page_requested.emit(f"prefs-{p}"))
            services.addWidget(card, index // 3, index % 3)
            self.cards[key] = card
        page.add_layout(services)

        page.add(label("Widgets and API", "sectionTitle"))
        apis = QHBoxLayout()
        apis.setSpacing(16)
        self.hosted_box = self._hosted()
        self.local_box = self._local()
        apis.addWidget(self.hosted_box, 1)
        apis.addWidget(self.local_box, 1)
        page.add_layout(apis)
        footer = QHBoxLayout()
        footer.addStretch(1)
        self.docs = button("API Reference", "code")
        self.docs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(API_DOCS)))
        footer.addWidget(self.docs)
        page.add_layout(footer)
        page.finish()

        controller.hosted_changed.connect(self._fill)
        controller.server_changed.connect(lambda _up: self._fill())
        controller.settings_changed.connect(lambda _settings: self._fill())
        controller.status_changed.connect(self._status)
        controller.account_changed.connect(self._streamlabs)
        controller.tiktok_account_changed.connect(self._tiktok_account)
        controller.settings_changed.connect(lambda _s: self._minecraft())
        theme_changed().connect(self._restyle)
        for change in controller.statuses.values():
            self._status(change)
        self._streamlabs(controller.account)
        self._tiktok_account(controller.tiktok_account)
        self._minecraft()
        self._restyle()
        self._fill()

    # States.

    def _status(self, change: StatusChange) -> None:
        card = self.cards.get(change.source)
        if card is None:
            return
        text, tone = STATE_WORD[change.state], STATE_TONE[change.state]
        detail = change.detail
        if change.source in ("tiktok", "twitch", "youtube"):
            name = getattr(self.controller.settings, card.field)  # type: ignore[attr-defined]
            if change.state is ConnectionState.OFFLINE:
                text, detail = ("Not connected", f"Channel: {name}") if name else ("Not set up", "No channel yet.")
            card.toggle.setText("Disconnect" if change.state in (ConnectionState.CONNECTING, ConnectionState.CONNECTED,
                                                                  ConnectionState.DEGRADED) else "Connect")
            card.toggle.setEnabled(bool(name) or change.state is not ConnectionState.OFFLINE)
        elif change.source == "tts" and change.state is ConnectionState.OFFLINE:
            text = "Off" if not self.controller.settings.tts_enabled else text
        card.set_state(text, tone, detail)

    def _toggle_chat(self, platform: str) -> None:
        if self.controller.statuses[platform].state is not ConnectionState.OFFLINE:
            self.controller.disconnect_chat(platform)
        else:
            self.controller.connect_platform(platform, getattr(self.controller.settings, self.cards[platform].field))

    def _streamlabs(self, info) -> None:
        card = self.cards["streamlabs"]
        status = self.controller.statuses["streamlabs"]
        if info is None:
            card.set_state("Not signed in", "error" if status.state is ConnectionState.ERROR else "muted",
                           status.detail if status.state is ConnectionState.ERROR else "")
        else:
            approved = "can go LIVE" if info.can_be_live else f"not approved for LIVE ({info.application_status.lower()})"
            card.set_state("Signed in", "healthy" if info.can_be_live else "warning", f"@{info.username} · {approved}")
        self.streamlabs_in.setVisible(info is None)
        self.streamlabs_out.setVisible(info is not None)

    def _tiktok_account(self, status: dict) -> None:
        card = self.cards["tiktok_account"]
        if status.get("connected"):
            name = status.get("username")
            extra = "moderation available" if status.get("moderation") else "moderation isn't in the server's plan"
            card.set_state("Signed in", "healthy", (f"@{name} · " if name else "") + extra)
        elif status.get("error"):
            card.set_state("Problem", "error", status["error"])
        elif status.get("server") is False:
            card.set_state("Unavailable", "muted", "Sign in to your Ignite server first (Settings → Integrations).")
        elif status.get("available"):
            card.set_state("Not signed in", "muted", "Sign in to TikTok on your server's website.")
        else:
            card.set_state("Unavailable", "muted", "Your Ignite server doesn't offer TikTok sign-in.")

    def _minecraft(self) -> None:
        card = self.cards["minecraft"]
        url = self.controller.settings.minecraft_url
        card.set_state("Set up" if keychain.load(keychain.MINECRAFT_KEY) else "Not set up", "muted",
                       f"ServerTap: {url}" if url else "")

    def _test_minecraft(self) -> None:
        card = self.cards["minecraft"]
        card.set_state("Testing…", "warning", card.detail.text())

        def done(message: str, ok: bool) -> None:
            card.set_state("Connected" if ok else "Problem", "healthy" if ok else "error", message)

        self.controller.test_minecraft(done)

    def _open_server(self) -> None:
        self.controller.open_server_account(lambda url: QDesktopServices.openUrl(QUrl(url)))

    def _restyle(self, _palette=None) -> None:
        self.hosted_box.restyle()
        self.local_box.restyle()
        for card in self.cards.values():
            card.restyle()
        restyle_buttons(self.docs)

    # Hosted links.

    def _hosted(self) -> Card:
        box, form = titled("Hosted links", HOSTED_HELP, icon="puzzle", primary=True)
        self.hosted_state = QLabel()
        self.hosted_toggle = QPushButton("Turn On")
        self.hosted_toggle.clicked.connect(
            lambda: self.controller.set_hosted_widgets(not self.controller.settings.hosted_widgets))
        self.hosted_server = QLabel()
        self.hosted_server.setTextFormat(Qt.TextFormat.PlainText)
        server_settings = QPushButton("Account")
        server_settings.setToolTip("Sign in and out of your Ignite account: Preferences → Server")
        server_settings.clicked.connect(lambda: self.open_page_requested.emit("prefs-Server"))
        where = QHBoxLayout()
        where.addWidget(self.hosted_server, 1)
        where.addWidget(server_settings)
        form.addRow("Ignite account:", where)
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

    def _local(self) -> Card:
        box, form = titled("On this PC", LOCAL_HELP, icon="connect")
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
        # The server's address is built in and never shown: only whether you're signed in.
        self.hosted_server.setText(f"Signed in as {s.server_username}" if s.server_username else
                                   ("Not signed in" if server else "This version has no Ignite server"))
        if on and not self.controller.server_up:
            self.hosted_state.setText("On, but the server isn't answering. Links use this PC until it's back.")
        elif on:
            self.hosted_state.setText("On")
        elif server:
            self.hosted_state.setText("Off")
        else:
            self.hosted_state.setText("Off. This version has no Ignite server.")
        self.hosted_toggle.setText("Turn Off" if s.hosted_widgets else "Turn On")
        self.hosted_toggle.setEnabled(bool(server) or s.hosted_widgets)
        for part in self.hosted_parts:
            part.setEnabled(on)
        page = self.hosted_page.currentData()
        self.hosted_url.setText(self.controller.hosted_link(page) if on else "")
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
