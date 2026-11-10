"""Every command in the app as a QAction, shared by the menu bar, toolbar, and buttons.

One action per command keeps text, shortcuts, and enabled state consistent
wherever it appears.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QPushButton, QStyle, QWidget

from ignite_live.controller import Controller
from ignite_live.ui.theme import icon

SP = QStyle.StandardPixmap


def action_button(action: QAction, text: str | None = None) -> QPushButton:
    """A push button that mirrors an action's text, tooltip and enabled state."""
    button = QPushButton(text or action.text().replace("…", "").replace("&", ""))
    button.setToolTip(action.toolTip())
    button.clicked.connect(action.trigger)

    def sync() -> None:
        button.setEnabled(action.isEnabled())
        if text is None:
            button.setText(action.text().replace("&", ""))

    action.changed.connect(sync)
    sync()
    return button


class AppActions:
    def __init__(self, controller: Controller, parent: QWidget) -> None:
        self.controller = controller

        def make(text: str, *, theme: str = "", fallback: SP | None = None, shortcut: str = "", tip: str = "") -> QAction:
            action = QAction(text, parent)
            if theme or fallback is not None:
                action.setIcon(icon(theme, fallback if fallback is not None else SP.SP_FileIcon, parent))
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            if tip:
                action.setToolTip(tip)
                action.setStatusTip(tip)
            return action

        # File / Help
        self.preferences = make("&Preferences…", theme="preferences-system", fallback=SP.SP_FileDialogDetailedView,
                                shortcut="Ctrl+,", tip="Connections, song request rules, Discord, appearance")
        self.preferences.setMenuRole(QAction.MenuRole.PreferencesRole)
        self.open_logs = make("Open &Log Folder", theme="folder-open", fallback=SP.SP_DirOpenIcon)
        self.quit = make("&Quit", theme="application-exit", fallback=SP.SP_DialogCloseButton, shortcut="Ctrl+Q")
        self.quit.setMenuRole(QAction.MenuRole.QuitRole)
        self.about = make("&About Ignite", theme="help-about", fallback=SP.SP_MessageBoxInformation)
        self.about.setMenuRole(QAction.MenuRole.AboutRole)

        # Stream
        self.sign_in = make("&Sign In to Streamlabs…", theme="system-log-out", fallback=SP.SP_DialogYesButton,
                            tip="Opens Streamlabs in your browser")
        self.sign_out = make("Sign &Out of Streamlabs")
        self.get_key = make("&Get Stream Key", theme="go-down", fallback=SP.SP_ArrowDown, shortcut="Ctrl+K",
                            tip="Reuse the saved key while it's valid, otherwise generate one")
        self.new_key = make("Generate &New Key", tip="Always generate a fresh key through Streamlabs")
        self.send_key = make("Send Key to &OBS", theme="document-send", fallback=SP.SP_ArrowRight,
                             shortcut="Ctrl+Shift+K", tip="Put the key on the chosen OBS output")
        self.toggle_stream = make("Start &Streaming", theme="media-record", fallback=SP.SP_MediaPlay,
                                  tip="Start or stop the chosen OBS output")
        self.check_stream = make("&Check Before Going Live…", theme="dialog-ok-apply", fallback=SP.SP_DialogApplyButton,
                                 tip="OBS, key, output, audio, overlays and clips: is everything ready?")
        self.end_live = make("&End LIVE…", theme="process-stop", fallback=SP.SP_BrowserStop,
                             tip="End the TikTok LIVE and retire this key")
        self.refresh_outputs = make("&Refresh OBS Outputs", theme="view-refresh", fallback=SP.SP_BrowserReload)

        # Chat
        self.connect_chat = make("&Connect All", theme="network-connect", fallback=SP.SP_DialogApplyButton,
                                 tip="Connect every platform that has a name: TikTok, Twitch, YouTube")
        self.disconnect_chat = make("&Disconnect All", theme="network-disconnect", fallback=SP.SP_DialogCancelButton)
        self.simulate = make("&Simulate Events…", theme="system-run", fallback=SP.SP_MediaPlay,
                             tip="Send fake gifts, follows, likes and comments to test your setup without going live")
        self.popout_chat = make("&Pop Out Chat", theme="window-new", fallback=SP.SP_TitleBarNormalButton,
                                shortcut="Ctrl+Shift+C", tip="Picture-in-picture chat that stays on top of OBS or your game")

        # OBS
        self.toggle_record = make("Start &Recording", theme="media-record", fallback=SP.SP_DialogYesButton,
                                  shortcut="Ctrl+R", tip="Start or stop recording in OBS")
        self.pause_record = make("&Pause Recording", theme="media-playback-pause", fallback=SP.SP_MediaPause,
                                 shortcut="Ctrl+Shift+R")  # Not Ctrl+Shift+P: that's Panic.
        self.toggle_replay = make("Start Replay &Buffer", theme="media-playlist-repeat", fallback=SP.SP_BrowserReload,
                                  tip="The replay buffer keeps the last moments so you can save them as a clip")
        self.save_replay = make("Save &Clip", theme="document-save", fallback=SP.SP_DialogSaveButton,
                                shortcut="Ctrl+Shift+S", tip="Save the replay buffer (the last moments) as a clip")
        self.toggle_vertical_record = make("Start Vertical Re&cording", theme="media-record", fallback=SP.SP_DialogYesButton,
                                           tip="Record the Aitum vertical canvas")
        self.toggle_vertical_backtrack = make("Start Vertical &Backtrack", theme="media-playlist-repeat",
                                              fallback=SP.SP_BrowserReload,
                                              tip="Aitum's replay buffer for the vertical canvas")
        self.save_vertical_clip = make("Save Vertical C&lip", theme="document-save", fallback=SP.SP_DialogSaveButton,
                                       tip="Save the vertical canvas's backtrack as a clip")
        self.toggle_virtualcam = make("Start &Virtual Camera", theme="camera-web", fallback=SP.SP_ComputerIcon)
        self.screenshot = make("Take Screens&hot", theme="camera-photo", fallback=SP.SP_DesktopIcon, shortcut="F12",
                               tip="Save a PNG of the current OBS scene")
        self.refresh_overlays = make("Re&fresh Overlays", theme="view-refresh", fallback=SP.SP_BrowserReload,
                                     shortcut="F5", tip="Reload every Ignite overlay in OBS")
        self.add_all_overlays = make("&Add Overlays to OBS…", theme="list-add", fallback=SP.SP_FileDialogNewFolder,
                                     tip="Choose which overlays (or all of them) to add, and to which scene")

        # Text to speech
        self.skip_tts = make("S&kip Spoken Message", theme="media-skip-forward", fallback=SP.SP_MediaSkipForward,
                             shortcut="Ctrl+Shift+Right", tip="Stop the message being read aloud")
        self.mute_tts = make("&Mute Text to Speech", theme="audio-volume-muted", fallback=SP.SP_MediaVolumeMuted,
                             shortcut="Ctrl+M", tip="Stop reading chat aloud until you unmute")
        self.mute_tts.setCheckable(True)

        # Moderation
        self.panic = make("&Panic", theme="dialog-warning", fallback=SP.SP_MessageBoxWarning, shortcut="Ctrl+Shift+P",
                          tip="Mute text to speech, clear alerts, pause music and rules, switch to your BRB scene")

        # Music
        self.play_pause = make("&Pause Music", theme="media-playback-pause", fallback=SP.SP_MediaPause,
                               shortcut="Ctrl+Space", tip="Pause or play the song requests")
        self.previous_song = make("P&revious Song", theme="media-skip-backward", fallback=SP.SP_MediaSkipBackward,
                                  shortcut="Ctrl+Left",
                                  tip="Start the song over; again right away, go back to the song before it")
        self.skip = make("S&kip Song", theme="media-skip-forward", fallback=SP.SP_MediaSkipForward, shortcut="Ctrl+Right")
        self.clear_queue = make("&Clear Queue", theme="edit-clear", fallback=SP.SP_DialogResetButton)
        self.stop_playlist = make("Stop &Playlist", theme="media-playback-stop", fallback=SP.SP_MediaStop)

        self.sign_out.triggered.connect(controller.sign_out_streamlabs)
        self.sign_in.triggered.connect(controller.login_streamlabs)
        self.send_key.triggered.connect(controller.send_key_to_obs)
        # The window runs the pre-stream checklist first (ui/checklist.py go_live).
        self.go_live: Callable[[], None] = lambda: controller.set_obs_streaming(True)
        self.toggle_stream.triggered.connect(
            lambda: controller.set_obs_streaming(False) if controller.obs_streaming else self.go_live())
        self.refresh_outputs.triggered.connect(controller.refresh_targets)
        self.disconnect_chat.triggered.connect(lambda: controller.disconnect_chat())
        self.skip_tts.triggered.connect(controller.skip_tts)
        self.toggle_record.triggered.connect(controller.toggle_recording)
        self.pause_record.triggered.connect(controller.toggle_record_pause)
        self.toggle_replay.triggered.connect(controller.toggle_replay)
        self.save_replay.triggered.connect(controller.save_replay)
        self.toggle_virtualcam.triggered.connect(controller.toggle_virtualcam)
        self.toggle_vertical_record.triggered.connect(controller.toggle_vertical_recording)
        self.toggle_vertical_backtrack.triggered.connect(controller.toggle_vertical_backtrack)
        self.save_vertical_clip.triggered.connect(controller.save_vertical_clip)
        self.screenshot.triggered.connect(controller.take_screenshot)
        self.refresh_overlays.triggered.connect(controller.refresh_overlays)
        self.mute_tts.toggled.connect(controller.set_tts_muted)
        controller.tts_mute_changed.connect(self.mute_tts.setChecked)  # Panic mutes too.
        self.panic.triggered.connect(lambda: controller.resume() if controller.panicking else controller.panic())
        controller.panic_changed.connect(self._panic_changed)
        self.skip.triggered.connect(controller.skip_song)
        self.play_pause.triggered.connect(controller.toggle_music_pause)
        self.previous_song.triggered.connect(controller.previous_song)
        controller.playback_changed.connect(self._playback_changed)
        self._playback: tuple | None = None
        self._playback_changed(controller.playback_state())
        self.clear_queue.triggered.connect(controller.clear_queue)
        self.stop_playlist.triggered.connect(controller.stop_playlist)

        self._busy = False
        controller.account_changed.connect(lambda _: self.refresh())
        controller.key_changed.connect(lambda _: self.refresh())
        controller.obs_streaming_changed.connect(lambda _: self.refresh())
        controller.queue_changed.connect(lambda _: self.refresh())
        controller.playlist_changed.connect(lambda _: self.refresh())
        controller.status_changed.connect(lambda _: self.refresh())
        controller.busy_changed.connect(self._busy_changed)
        controller.settings_changed.connect(lambda _: self.refresh())
        controller.studio_changed.connect(lambda _: self.refresh())
        self.refresh()

    def _panic_changed(self, on: bool) -> None:
        self.panic.setText("&Resume" if on else "&Panic")
        self.panic.setToolTip("Undo what the panic button changed" if on else
                              "Mute text to speech, clear alerts, pause music and rules, switch to your BRB scene")

    def _playback_changed(self, state: dict) -> None:
        """Progress arrives twice a second: touch the actions only when what they show changes."""
        shown = (bool(state.get("paused")), bool(state.get("panic")),
                 bool(state.get("current")) or bool(state.get("has_previous")))
        if shown == self._playback:
            return
        self._playback = shown
        paused, panic, can_go_back = shown
        self.play_pause.setText("&Play Music" if paused else "&Pause Music")
        self.play_pause.setEnabled(not panic)  # Panic paused it: Resume starts it again.
        self.previous_song.setEnabled(can_go_back)

    def _busy_changed(self, name: str, busy: bool) -> None:
        if name in ("key", "streamlabs"):
            self._busy = busy
            self.refresh()

    def refresh(self) -> None:
        c = self.controller
        account, busy = c.account, self._busy
        has_key = c.active_key is not None
        self.sign_in.setEnabled(account is None and not busy)
        self.sign_out.setEnabled(account is not None)
        self.get_key.setEnabled(not busy and not (account is not None and not account.can_be_live))
        self.get_key.setText("Working…" if busy else "&Get Stream Key")
        self.new_key.setEnabled(not busy and account is not None and account.can_be_live)
        self.send_key.setEnabled(has_key and not busy)
        self.end_live.setEnabled(has_key and not busy)
        self.toggle_stream.setText("Stop &Streaming" if c.obs_streaming else "Start &Streaming")
        self.skip.setEnabled(bool(c.queue_snapshot.get("current")))
        self.clear_queue.setEnabled(bool(c.queue_snapshot.get("upcoming")))
        self.stop_playlist.setEnabled(c.playlist_info is not None)
        studio = c.studio
        obs = studio.connected
        self.toggle_record.setText("Stop &Recording" if studio.recording else "Start &Recording")
        self.toggle_record.setEnabled(obs)
        self.pause_record.setText("Resume &Recording" if studio.record_paused else "&Pause Recording")
        self.pause_record.setEnabled(obs and studio.recording)
        self.toggle_replay.setText("Stop Replay &Buffer" if studio.replay else "Start Replay &Buffer")
        self.toggle_replay.setEnabled(obs and studio.replay is not None)
        self.save_replay.setEnabled(obs and bool(studio.replay))
        self.toggle_virtualcam.setText("Stop &Virtual Camera" if studio.virtualcam else "Start &Virtual Camera")
        self.toggle_virtualcam.setEnabled(obs and studio.virtualcam is not None)
        for action in (self.screenshot, self.refresh_overlays, self.add_all_overlays):
            action.setEnabled(obs)
        vertical = studio.vertical if obs else None
        for action in (self.toggle_vertical_record, self.toggle_vertical_backtrack, self.save_vertical_clip):
            action.setVisible(vertical is not None)  # Only with Aitum Vertical Canvas installed.
            action.setEnabled(vertical is not None)
        if vertical is not None:
            self.toggle_vertical_record.setText("Stop Vertical Re&cording" if vertical.recording
                                                else "Start Vertical Re&cording")
            self.toggle_vertical_backtrack.setText("Stop Vertical &Backtrack" if vertical.backtrack
                                                   else "Start Vertical &Backtrack")
            self.save_vertical_clip.setEnabled(vertical.backtrack)
        self.skip_tts.setEnabled(c.settings.tts_enabled)
        self.mute_tts.setEnabled(c.settings.tts_enabled)
