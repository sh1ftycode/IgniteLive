"""Help → Setup Guide (F1): a step-by-step first setup, including tuning OBS to what this PC can do.

Opens by itself the first time (until finished); every step can be skipped and done
later, and the last page lists whatever is still open. Nothing in OBS changes without the
user pressing a button that says so.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ignite_live.controller import Controller
from ignite_live.core import keychain
from ignite_live.core.events import ConnectionState, StatusChange
from ignite_live.integrations.streamlabs import AccountInfo
from ignite_live.overlay.server import PAGES
from ignite_live.setup import tuning
from ignite_live.setup.hardware import VENDOR_NAMES, Hardware
from ignite_live.ui.icons import glyph
from ignite_live.ui.pages.stream import disclosure, note
from ignite_live.ui.theme import colors
from ignite_live.viewers.points import clean_config as clean_points

STEPS = ("Welcome", "Your PC", "Connect OBS", "Tune OBS", "TikTok", "Overlays", "Extras", "Done")
# What each step is for, shown under its title.
SUBTITLES = {
    "Welcome": "A few minutes, and every step can be skipped.",
    "Your PC": "What this PC can stream, so OBS gets settings it can keep up with.",
    "Connect OBS": "Ignite controls OBS: keys, scenes, overlays, recording.",
    "Tune OBS": "Resolution, frame rate, encoder and bitrate for TikTok.",
    "TikTok": "Your chat, and a stream key for OBS.",
    "Overlays": "Song requests, alerts and chat on your stream.",
    "Extras": "Optional: switch these on now or any time later.",
    "Done": "What's ready, and what's still open.",
}
# Steps the Done page can check off, and where to finish each one later.
FINISH_LATER = {
    "Your PC": "this guide",
    "Connect OBS": "File → Preferences → OBS",
    "Tune OBS": "this guide",
    "TikTok": "the Stream tab and the Chat panel",
    "Overlays": "the Overlays tab, or OBS → Add Overlays to OBS…",
}
TOUR = (
    ("Stream tab", "get a key, send it to OBS, Start Streaming"),
    ("OBS panel (left)", "scenes, audio, recording, clips, stream health"),
    ("Chat panel (right)", "TikTok, Twitch and YouTube chat; Pop Out Chat floats it over your game"),
    ("Music, Overlays, Actions, Games", "song requests, on-stream overlays, gift rules, viewer games"),
    ("Streams and Viewers", "a report after every stream, and everyone who's been in chat"),
    ("System tray", "closing the window keeps Ignite running; quit from File or the tray"),
)


def heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-size: 20px; font-weight: 700;")
    return label


def body(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.RichText)
    return label


class SetupGuide(QDialog):
    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.hardware: Hardware | None = None
        self.current_obs: tuning.ObsTuning | None = None
        self.setWindowTitle("Ignite Setup Guide")
        self.resize(940, 660)
        outer = QVBoxLayout(self)
        main = QHBoxLayout()
        self.steps = QListWidget()
        self.steps.setFixedWidth(190)
        for step in STEPS:
            self.steps.addItem(QListWidgetItem(step))
        self.stack = QStackedWidget()
        right = QVBoxLayout()
        self.counter = note("")
        right.addWidget(self.counter)
        self.progress = QProgressBar()
        self.progress.setRange(0, len(STEPS) - 1)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        right.addWidget(self.progress)
        right.addWidget(self.stack, 1)
        main.addWidget(self.steps)
        main.addLayout(right, 1)
        outer.addLayout(main, 1)
        buttons = QHBoxLayout()
        self.back = QPushButton("Back")
        self.skip = QPushButton("Skip")
        self.next = QPushButton("Next")
        self.next.setDefault(True)
        self.back.clicked.connect(lambda: self.go(self.stack.currentIndex() - 1))
        self.skip.clicked.connect(lambda: self.go(self.stack.currentIndex() + 1, save=False))
        self.next.clicked.connect(self._next)
        later = QPushButton("Finish Later")
        later.setToolTip("Close for now. Help → Setup Guide brings it back.")
        later.clicked.connect(self.reject)
        buttons.addWidget(later)
        buttons.addStretch(1)
        for button in (self.back, self.skip, self.next):
            buttons.addWidget(button)
        outer.addLayout(buttons)

        for step, build in zip(STEPS, (self._welcome, self._pc, self._obs, self._tune, self._tiktok, self._overlays,
                                       self._extras, self._done)):
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setSpacing(10)
            layout.addWidget(note(SUBTITLES[step]))
            build(layout)
            layout.addStretch(1)
            scroll = QScrollArea()  # Long steps scroll instead of squashing on small screens.
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.stack.addWidget(scroll)
        self.steps.currentRowChanged.connect(lambda row: self.go(row, save=False))
        controller.status_changed.connect(self._status)
        controller.account_changed.connect(self._account)
        controller.notice.connect(self._notice)
        controller.key_changed.connect(lambda _key: self._marks())
        controller.settings_changed.connect(lambda _s: self._marks())
        self.go(0)

    # Navigation.

    def go(self, index: int, save: bool = True) -> None:
        if not 0 <= index < len(STEPS):
            return
        if save:
            self._save_page(self.stack.currentIndex())
        self.stack.setCurrentIndex(index)
        self.steps.blockSignals(True)
        self.steps.setCurrentRow(index)
        self.steps.blockSignals(False)
        self.back.setEnabled(index > 0)
        last = index == len(STEPS) - 1
        self.skip.setVisible(not last and index > 0)
        self.next.setText("Finish" if last else f"Next: {STEPS[index + 1]}")
        self.counter.setText(f"Step {index + 1} of {len(STEPS)}")
        self.progress.setValue(index)
        self._entered(STEPS[index])
        self._marks()

    def _next(self) -> None:
        index = self.stack.currentIndex()
        if index == len(STEPS) - 1:
            self._save_page(index)
            self.controller.save_settings(replace(self.controller.settings, setup_done=True))
            self.accept()
        else:
            self.go(index + 1)

    def _entered(self, step: str) -> None:
        if step == "Your PC" and self.hardware is None:
            self.controller.detect_hardware(self._detected)
        elif step == "Tune OBS":
            self._read_obs()
        elif step == "Overlays":
            self._tick_overlays()
        elif step == "Done":
            self._summary()

    def _done_steps(self) -> set[str]:
        c = self.controller
        done = set()
        if self.hardware is not None:
            done.add("Your PC")
        if c.statuses["obs"].state is ConnectionState.CONNECTED:
            done.add("Connect OBS")
        if c.settings.tuning_backup:
            done.add("Tune OBS")
        if c.settings.tiktok_username and c.active_key is not None:
            done.add("TikTok")
        if "nowplaying" in c.overlay.open_pages():
            done.add("Overlays")
        return done

    def _marks(self) -> None:
        done = self._done_steps()
        for row, step in enumerate(STEPS):
            item = self.steps.item(row)
            # Only steps that can be finished get a mark; the rest keep the space, so names line up.
            mark = ("check", colors().healthy) if step in done else ("circle", colors().text_disabled if step in FINISH_LATER
                                                                  else "transparent")
            item.setIcon(glyph(*mark))
            item.setData(Qt.ItemDataRole.UserRole, step in done)

    def _save_page(self, index: int) -> None:
        step = STEPS[index]
        s = self.controller.settings
        if step == "TikTok":
            name = self.username.text().strip().lstrip("@")
            if name != s.tiktok_username:
                self.controller.save_settings(replace(s, tiktok_username=name))
        elif step == "Extras":
            points = clean_points({**s.points, "enabled": self.points_on.isChecked()})
            new = replace(s, tts_enabled=self.tts_on.isChecked(), points=points,
                          panic_scene=self.brb.currentText().strip())
            if self.tts_on.isChecked() and self.system_voice.isChecked():
                new = replace(new, tts_provider="system")
            if new != s:
                self.controller.save_settings(new)

    # Pages.

    def _welcome(self, layout: QVBoxLayout) -> None:
        layout.addWidget(heading("Welcome to Ignite"))
        layout.addWidget(body(
            "Let's get your TikTok LIVE ready. By the end you'll have:<ol>"
            "<li>OBS connected and tuned to what your PC can handle</li>"
            "<li>your TikTok chat coming in and a stream key in OBS</li>"
            "<li>overlays for song requests, alerts and chat on your stream</li></ol>"
            "Have OBS open before you start. Skip anything you like: <b>Help → Setup Guide</b> (F1) brings this "
            "back, and the last page shows what's left."))

    def _pc(self, layout: QVBoxLayout) -> None:
        layout.addWidget(heading("Your PC"))
        self.pc_info = body("Checking this PC…")
        layout.addWidget(self.pc_info)
        self.tier = QLabel()
        self.tier.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(self.tier)
        form = QFormLayout()
        self.upload = QComboBox()
        for key, (label, _mbps) in tuning.UPLOADS.items():
            self.upload.addItem(label, key)
        self.upload.currentIndexChanged.connect(lambda _i: self._show_tier())
        form.addRow("Your upload speed:", self.upload)
        layout.addLayout(form)
        layout.addWidget(note("Upload speed decides how much video you can send. A speed test website shows it; "
                              "Ignite doesn't run one, so it never contacts other services for this."))

    def _detected(self, hw: Hardware) -> None:
        self.hardware = hw
        gpu = ", ".join(hw.gpu_names) or "none found"
        ram = f"{hw.ram_gb:g} GB" if hw.ram_gb else "unknown"
        self.pc_info.setText(f"<b>Processor:</b> {hw.threads or 'unknown'} threads<br><b>Memory:</b> {ram}<br>"
                             f"<b>Graphics:</b> {_escape(gpu)}")
        self._show_tier()
        self._marks()

    def recommendation(self) -> tuning.Recommendation:
        base = self.current_obs.base if self.current_obs and all(self.current_obs.base) else (1080, 1920)
        return tuning.recommend(self.hardware or Hardware(), self.upload.currentData(), base)

    def _show_tier(self) -> None:
        if self.hardware is None:
            return
        rec = self.recommendation()
        encoder = tuning.ENCODER_LABELS[rec.encoder]
        gpu = f" with {VENDOR_NAMES[self.hardware.gpus[0]]} graphics" if self.hardware.gpus else ""
        self.tier.setText(f"{tuning.TIERS[rec.tier]} PC{gpu}: {min(rec.output)}p at {rec.fps} FPS, {encoder}, "
                          f"{rec.bitrate:,} kbps")

    def _obs(self, layout: QVBoxLayout) -> None:
        layout.addWidget(heading("Connect OBS"))
        layout.addWidget(body(
            "Ignite controls OBS through its WebSocket server (OBS 28 or newer has it built in).<ol>"
            "<li>Open OBS → <b>Tools → WebSocket Server Settings</b>.</li>"
            "<li>Tick <b>Enable WebSocket server</b>.</li>"
            "<li>Click <b>Show Connect Info</b> and copy the password.</li></ol>"))
        form = QFormLayout()
        self.obs_url = QLineEdit(self.controller.settings.obs_url)
        self.obs_password = QLineEdit()
        self.obs_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.obs_password.setPlaceholderText("Saved in your system keychain" if keychain.load(keychain.OBS_PASSWORD)
                                             else "Paste the password")
        form.addRow("Address:", self.obs_url)
        form.addRow("Password:", self.obs_password)
        layout.addLayout(form)
        connect = QPushButton("Connect")
        connect.clicked.connect(self._connect_obs)
        layout.addWidget(connect, 0, Qt.AlignmentFlag.AlignLeft)
        self.obs_status = body("")
        layout.addWidget(self.obs_status)
        self._status(self.controller.statuses["obs"])

    def _connect_obs(self) -> None:
        c = self.controller
        password = self.obs_password.text().strip()
        if password and not keychain.save(keychain.OBS_PASSWORD, password):
            self.obs_status.setText("No system keychain was found, so the password couldn't be saved.")
            return
        self.obs_password.clear()
        url = self.obs_url.text().strip() or "ws://127.0.0.1:4455"
        if url != c.settings.obs_url:
            c.save_settings(replace(c.settings, obs_url=url))  # Reconnects by itself.
        else:
            c.reconnect_obs()
        self.obs_status.setText("Connecting…")

    def _status(self, change: StatusChange) -> None:
        if change.source != "obs":
            return
        marks = {ConnectionState.CONNECTED: "Connected", ConnectionState.ERROR: "Not connected"}
        self.obs_status.setText(f"<b>{marks.get(change.state, 'Not connected yet')}</b>: {_escape(change.detail)}")
        self._marks()

    def _tune(self, layout: QVBoxLayout) -> None:
        layout.addWidget(heading("Tune OBS"))
        layout.addWidget(body("Recommended settings for TikTok from your PC and upload speed. Untick anything you "
                              "want to keep. Only OBS's output size and frame rate change, never your canvas or "
                              "scenes, and <b>Undo Tuning</b> puts everything back."))
        self.tune_table = QTableWidget(0, 4)
        self.tune_table.setHorizontalHeaderLabels(["Change", "Setting", "Now", "Recommended"])
        self.tune_table.verticalHeader().setVisible(False)
        self.tune_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tune_table.horizontalHeader().setStretchLastSection(True)
        self.tune_table.setMinimumHeight(190)
        layout.addWidget(self.tune_table)
        self.tune_notes = body("")
        layout.addWidget(self.tune_notes)
        row = QHBoxLayout()
        self.apply_tune = QPushButton("Apply to OBS")
        self.apply_tune.clicked.connect(self._apply)
        self.undo_tune = QPushButton("Undo Tuning")
        self.undo_tune.clicked.connect(lambda: self.controller.undo_tuning(self._read_obs))
        row.addWidget(self.apply_tune)
        row.addWidget(self.undo_tune)
        row.addStretch(1)
        layout.addLayout(row)
        self.tune_result = body("")
        layout.addWidget(self.tune_result)

    def _read_obs(self) -> None:
        self.undo_tune.setEnabled(bool(self.controller.settings.tuning_backup))
        if self.controller.statuses["obs"].state is not ConnectionState.CONNECTED:
            self.tune_table.setRowCount(0)
            self.apply_tune.setEnabled(False)
            self.tune_notes.setText("Connect OBS first (the previous step).")
            return
        self.tune_notes.setText("Reading OBS…")
        self.controller.read_tuning(self._show_tuning, lambda error: self.tune_notes.setText(_escape(str(error))))

    def _show_tuning(self, current: tuning.ObsTuning) -> None:
        self.current_obs = current
        if self.hardware is None:
            self.hardware = Hardware()
        rec = self.recommendation()
        target = self.controller.stream_target
        vertical = target is not None and target.kind == "vertical"
        rows = tuning.rows(current, rec, vertical)
        self.tune_rows = rows
        self.tune_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            check = QTableWidgetItem()
            if row.settable:
                check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                same = row.current == row.recommended
                check.setCheckState(Qt.CheckState.Unchecked if same else Qt.CheckState.Checked)
            else:
                check.setFlags(Qt.ItemFlag.ItemIsEnabled)
                check.setText("by hand")
            self.tune_table.setItem(index, 0, check)
            for column, text in enumerate((row.label, row.current, row.recommended), 1):
                self.tune_table.setItem(index, column, QTableWidgetItem(text))
        notes = list(rec.notes)
        manual = tuning.why_by_hand(current, vertical)
        if manual:
            notes.append(manual)
        self.tune_notes.setText("<br>".join(_escape(n) for n in notes))
        self.apply_tune.setEnabled(any(r.settable for r in rows))
        self._show_tier()

    def _chosen(self) -> set[str]:
        return {row.key for index, row in enumerate(getattr(self, "tune_rows", []))
                if row.settable and self.tune_table.item(index, 0).checkState() == Qt.CheckState.Checked}

    def _apply(self) -> None:
        keys = self._chosen()
        if not keys:
            self.tune_result.setText("Nothing ticked to change.")
            return
        self.tune_result.setText("Applying…")
        self.controller.apply_tuning(self.recommendation(), keys, self._applied)

    def _applied(self, problems: list[str]) -> None:
        if problems:
            self.tune_result.setText("<b>Not applied:</b><br>" + "<br>".join(_escape(p) for p in problems))
        else:
            self.tune_result.setText("Done: OBS has the new settings.")
        self._read_obs()

    def _tiktok(self, layout: QVBoxLayout) -> None:
        c = self.controller
        layout.addWidget(heading("TikTok"))
        form = QFormLayout()
        self.username = QLineEdit(c.settings.tiktok_username)
        self.username.setPlaceholderText("your TikTok username")
        form.addRow("Username:", self.username)
        layout.addLayout(form)
        layout.addWidget(note("Reads your LIVE chat, gifts and follows. It connects by itself once you're live."))
        layout.addWidget(body("<b>Stream key.</b> Sign in with Streamlabs, then get a key: Ignite reuses it for "
                              "48 hours, or until you end the LIVE."))
        self.account = body("")
        layout.addWidget(self.account)
        row = QHBoxLayout()
        self.sign_in = QPushButton("Sign In with Streamlabs…")
        self.sign_in.clicked.connect(c.login_streamlabs)
        self.get_key = QPushButton("Get Stream Key")
        self.get_key.clicked.connect(lambda: c.get_stream_key(c.settings.stream_title, "", "0"))
        send = QPushButton("Send Key to OBS")
        send.clicked.connect(c.send_key_to_obs)
        for button in (self.sign_in, self.get_key, send):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        self.key_status = body("")
        layout.addWidget(self.key_status)
        manual = QWidget()
        manual_form = QFormLayout(manual)
        manual_form.setContentsMargins(0, 0, 0, 0)
        self.key_server = QLineEdit()
        self.key_server.setPlaceholderText("rtmp:// or rtmps://")
        self.key_value = QLineEdit()
        self.key_value.setEchoMode(QLineEdit.EchoMode.Password)
        save = QPushButton("Use This Key")
        save.clicked.connect(self._save_key)
        manual_form.addRow("Server:", self.key_server)
        manual_form.addRow("Key:", self.key_value)
        manual_form.addRow("", save)
        layout.addWidget(disclosure("I have a key already (e.g. from TikTok LIVE Studio)", manual))
        c.key_changed.connect(self._key_changed)
        self._key_changed(c.active_key)
        self._account(c.account)

    def _account(self, info: AccountInfo | None) -> None:
        if info is None:
            text = "Not signed in to Streamlabs."
        elif info.can_be_live:
            text = f"Signed in as <b>@{_escape(info.username)}</b>, who can go LIVE."
        else:
            text = (f"Signed in as <b>@{_escape(info.username)}</b>, but TikTok hasn't approved LIVE for this "
                    f"account yet ({_escape(info.application_status.lower())}).")
        self.account.setText(text)
        self.sign_in.setVisible(info is None)
        self.get_key.setEnabled(info is not None)

    def _key_changed(self, key) -> None:
        self.key_status.setText(
            f"<span style='color: {colors().healthy}'><b>A stream key is ready.</b></span> Send it to OBS, and OBS "
            "can go live." if key is not None else "No stream key yet."
        )
        self._marks()

    def _save_key(self) -> None:
        if self.key_server.text().strip() and self.key_value.text().strip():
            self.controller.use_manual_key(self.key_server.text().strip(), self.key_value.text().strip())
            self.key_value.clear()

    def _overlays(self, layout: QVBoxLayout) -> None:
        layout.addWidget(heading("Overlays"))
        layout.addWidget(body("Pick what to show on stream. They're added to your current OBS scene; arrange them "
                              "there. Each overlay is a small browser inside OBS, so fewer is lighter on your PC."))
        self.overlay_checks: dict[str, QCheckBox] = {}
        for page, (title, _w, _h) in PAGES.items():
            check = QCheckBox(title)
            self.overlay_checks[page] = check
            layout.addWidget(check)
        self.add_overlays = QPushButton("Add to OBS")
        self.add_overlays.clicked.connect(self._add_overlays)
        layout.addWidget(self.add_overlays, 0, Qt.AlignmentFlag.AlignLeft)
        self.overlay_result = body("")
        layout.addWidget(self.overlay_result)
        layout.addWidget(note("For Now Playing (it plays the music), open its properties in OBS and tick <b>Control audio via "
                              "OBS</b>, so the music reaches your stream."))

    def _tick_overlays(self) -> None:
        suggested = self.recommendation().overlays
        for page, check in self.overlay_checks.items():
            check.setChecked(page in suggested)
        connected = self.controller.statuses["obs"].state is ConnectionState.CONNECTED
        self.add_overlays.setEnabled(connected)
        if not connected:
            self.overlay_result.setText("Connect OBS first (the Connect OBS step), then add them here.")

    def _add_overlays(self) -> None:
        chosen = [page for page, check in self.overlay_checks.items() if check.isChecked()]
        if chosen:
            self.overlay_result.setText("Adding to OBS…")
            self.controller.add_overlays(chosen)
        else:
            self.overlay_result.setText("Tick at least one overlay.")

    def _notice(self, level: str, text: str) -> None:
        """While on the Overlays step, OBS's answer shows there instead of only in the status bar."""
        if STEPS[self.stack.currentIndex()] == "Overlays" and self.overlay_result.text() == "Adding to OBS…":
            color = colors().live if level == "error" else colors().healthy
            self.overlay_result.setText(f"<span style='color: {color}'>{_escape(text)}</span>")
            self._marks()

    def _extras(self, layout: QVBoxLayout) -> None:
        s = self.controller.settings
        layout.addWidget(heading("Extras"))
        layout.addWidget(body("A few things to switch on now. All of them have more options later."))
        self.tts_on = QCheckBox("Read chat aloud (text to speech)")
        self.tts_on.setChecked(s.tts_enabled)
        self.system_voice = QCheckBox("Use this computer's own voice (lighter than online voices)")
        self.system_voice.setChecked(s.tts_provider == "system")
        self.points_on = QCheckBox("Loyalty points: viewers earn points and can spend them")
        self.points_on.setChecked(bool(s.points.get("enabled")))
        for widget in (self.tts_on, self.system_voice, self.points_on):
            layout.addWidget(widget)
        form = QFormLayout()
        self.brb = QComboBox()
        self.brb.setEditable(True)
        self.brb.addItem("")
        studio = self.controller.studio
        self.brb.addItems(list(studio.scenes) + (list(studio.vertical.scenes) if studio.vertical else []))
        self.brb.setCurrentText(s.panic_scene)
        self.brb.lineEdit().setPlaceholderText("Stay on the current scene")
        form.addRow("Panic button scene:", self.brb)
        layout.addLayout(form)
        layout.addWidget(note("The panic button (toolbar, Ctrl+Shift+P) mutes text to speech, holds alerts, pauses "
                              "music and switches to this scene; Resume undoes it."))

    def _done(self, layout: QVBoxLayout) -> None:
        self.done_heading = heading("You're set")
        layout.addWidget(self.done_heading)
        self.summary = QVBoxLayout()
        self.summary.setSpacing(4)
        layout.addLayout(self.summary)
        check = QPushButton("Check Before Going Live…")
        check.clicked.connect(self._checklist)
        layout.addWidget(check, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(body("<b>Where things are</b><ul>" + "".join(
            f"<li><b>{place}</b>: {text}</li>" for place, text in TOUR) + "</ul>"
            "Try anything safely, without going live, with <b>View → Simulate Events</b>."))

    def _summary(self) -> None:
        while self.summary.count():
            item = self.summary.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
            elif item.layout() is not None:
                while item.layout().count():
                    item.layout().takeAt(0).widget().deleteLater()
        done = self._done_steps()
        left = [step for step in FINISH_LATER if step not in done]
        self.done_heading.setText("You're set" if not left else f"Almost there: {len(left)} left")
        for step in FINISH_LATER:
            row = QHBoxLayout()
            mark = QLabel()
            mark.setPixmap((glyph("check", colors().healthy) if step in done else glyph("circle", colors().text_disabled))
                           .pixmap(16, 16))
            row.addWidget(mark)
            row.addWidget(QLabel(f"<b>{step}</b>" + ("" if step in done else
                                                    f": not done yet (or later in {FINISH_LATER[step]})")), 1)
            if step not in done:
                go = QPushButton("Do It Now")
                go.clicked.connect(lambda _=False, s=step: self.go(STEPS.index(s), save=False))
                row.addWidget(go)
            self.summary.addLayout(row)

    def _checklist(self) -> None:
        from ignite_live.ui.checklist import ChecklistDialog

        ChecklistDialog(self.controller, self.parent() or self).exec()


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
