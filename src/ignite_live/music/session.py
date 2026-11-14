"""Glue between chat commands, the queue, song lookup, and the Now Playing overlay (which plays the music)."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from typing import Callable

from ignite_live.core.events import ChatComment, ChatUser
from ignite_live.music.commands import Command, CommandEngine, CommandKind, Denied, Role, role_of
from ignite_live.music.queue import QueueRejected, SongQueue
from ignite_live.music.spotify import Playlist, PlaylistSong
from ignite_live.music.youtube import Track, TrackLookupError, parse_request
from ignite_live.overlay.server import OverlayServer

log = logging.getLogger(__name__)

PLAYLIST_USER = ChatUser("playlist", "Playlist")
RADIO_USER = ChatUser("autoplay", "Autoplay")
# Refusals for what the song is (not for the queue being full): these can ban it for good (auto-ban).
AUTO_BAN = {"queue.too_long", "queue.explicit", "queue.blocked_word"}
# Give up on a playlist after this many songs in a row can't be found.
MAX_PLAYLIST_MISSES = 5
# Autoplay: how many chat requests it remembers to follow, and how many songs back it avoids repeating.
RADIO_SEEDS = 20
RADIO_NO_REPEAT = 100
MAX_RADIO_MISSES = 8

# Player error codes that mean "this video will never play here".
_UNPLAYABLE = {
    2: "invalid video",
    5: "player error",
    100: "removed or private",
    101: "its owner only allows it on YouTube",
    150: "its owner only allows it on YouTube",
}


class MusicSession:
    def __init__(
        self,
        overlay: OverlayServer,
        resolve: Callable[[str], Track],
        engine: CommandEngine | None = None,
        queue: SongQueue | None = None,
        clock: Callable[[], float] = time.monotonic,
        radio: Callable[[str], list[Track]] | None = None,
    ) -> None:
        """``resolve`` and ``radio`` (songs like a video ID) are blocking; they run off-loop."""
        self.overlay = overlay
        self.engine = engine or CommandEngine()
        self.queue = queue or SongQueue()
        self._resolve = resolve
        self._radio = radio
        self._clock = clock
        self._tasks: set[asyncio.Task] = set()
        self.queue.on_change = self._queue_changed
        overlay.on_player_message = self.on_player_message
        self.playlist: Playlist | None = None
        self._playlist_order: list[int] = []
        self._playlist_pos = 0
        self._filling = False
        self.on_playlist_change: Callable[[dict | None], None] | None = None
        # Autoplay: with the queue empty and no playlist, songs like what chat requested (same genre).
        self.radio_on = False
        self.radio_start = ""  # A song or artist to start from, before chat has requested anything.
        self.radio_seeds: list[str] = []  # Video IDs chat requested, newest first. Never autoplay's own picks.
        self.on_seeds_change: Callable[[list[str]], None] | None = None
        self.on_radio_change: Callable[[str], None] | None = None  # A line for the Music tab.
        self._radio_queue: list[Track] = []
        self._radio_seed: str | None = None  # Whose radio _radio_queue is.
        self._start_seed: tuple[str, str] | None = None  # (radio_start, its video ID), looked up once.
        self._played: deque[str] = deque(maxlen=RADIO_NO_REPEAT)
        # Commands that aren't about music (e.g. !skiptts) go here.
        self.on_other_command: Callable[[Command], None] | None = None
        # Loyalty points for chat requests and !bump: charge(user, "request" | "bump") returns why it's refused
        # ("" when paid or free); refund(user, kind) gives the points back if the request fails.
        self.charge: Callable[[ChatUser, str], str] | None = None
        self.refund: Callable[[ChatUser, str], None] | None = None
        # Auto-ban: called with the track and the refusing rule's key when a song is refused for what it is.
        self.on_refused: Callable[[Track, str], None] | None = None

    @property
    def texts(self):
        return self.engine.config.texts

    # Background playlist: fills the gaps when nobody has requested anything.

    def set_playlist(self, playlist: Playlist, shuffle: bool) -> None:
        self.playlist = playlist
        self._playlist_order = list(range(len(playlist.songs)))
        if shuffle:
            random.shuffle(self._playlist_order)
        self._playlist_pos = 0
        self._playlist_changed()
        self._maybe_fill()

    def clear_playlist(self) -> None:
        self.playlist = None
        self._playlist_changed()

    def playlist_info(self) -> dict | None:
        if self.playlist is None:
            return None
        upcoming = self._peek()
        return {
            "name": self.playlist.name,
            "count": len(self.playlist.songs),
            "next": upcoming.request if upcoming else "",
        }

    def _peek(self) -> PlaylistSong | None:
        if not self.playlist or not self._playlist_order:
            return None
        index = self._playlist_order[self._playlist_pos % len(self._playlist_order)]
        return self.playlist.songs[index]

    def _playlist_changed(self) -> None:
        if self.on_playlist_change:
            self.on_playlist_change(self.playlist_info())

    def _queue_changed(self, queue: SongQueue) -> None:
        self._spawn(self.overlay.publish_state(queue.snapshot()))
        if queue.current is not None and (not self._played or self._played[-1] != queue.current.track.video_id):
            self._played.append(queue.current.track.video_id)
        self._maybe_fill()

    def _idle(self) -> bool:
        return self.queue.current is None and not self.queue.upcoming

    def _maybe_fill(self) -> None:
        if not self._idle() or self._filling:
            return
        if self.playlist is not None:
            self._filling = True
            self._spawn(self._fill_from_playlist())
        elif self.radio_on and self._radio is not None:
            self._filling = True
            self._spawn(self._fill_from_radio())

    # Autoplay ("community radio"): YouTube Music's radio for a song chat requested, so it stays in the
    # genre the room is into. It follows the newest request, then older ones as each radio runs out, and
    # never seeds from its own picks, so it doesn't drift.

    def set_radio(self, on: bool, start: str) -> None:
        start = start.strip()
        changed = bool(start) and start != self.radio_start
        self.radio_on, self.radio_start = on, start
        if changed and self.radio_seeds:
            # A new "Start from" means play that now. Chat's earlier requests (kept between streams) used to
            # win, so changing the artist did nothing; the next request still takes over, as before.
            self.radio_seeds = []
            if self.on_seeds_change:
                self.on_seeds_change([])
        if changed or (not self.radio_seeds and self._radio_seed is not None and self._radio_seed != self._start_id()):
            self._radio_queue, self._radio_seed = [], None  # A new starting point: the old artist's picks go.
        self._radio_status()
        self._maybe_fill()

    def remember_request(self, track: Track) -> None:
        """A song chat asked for: autoplay follows it next."""
        self.radio_seeds = [track.video_id] + [v for v in self.radio_seeds if v != track.video_id][:RADIO_SEEDS - 1]
        self._radio_queue, self._radio_seed = [], None
        if self.on_seeds_change:
            self.on_seeds_change(list(self.radio_seeds))
        self._radio_status()

    def _start_id(self) -> str | None:
        return self._start_seed[1] if self._start_seed and self._start_seed[0] == self.radio_start else None

    def _radio_status(self, problem: str = "") -> None:
        if not self.on_radio_change:
            return
        if not self.radio_on:
            text = "Off"
        elif problem:
            text = problem
        elif self.radio_seeds:
            text = "Plays songs like the latest requests when nothing's queued."
        elif self.radio_start:
            text = f"Plays songs like “{self.radio_start}” until chat requests something."
        else:
            text = "Waiting for the first song request (or set a song or artist to start from)."
        self.on_radio_change(text)

    async def _choose_seed(self) -> str | None:
        seeds = self.radio_seeds
        if seeds:
            if self._radio_seed in seeds:  # That radio is used up: the next older request, and around again.
                return seeds[(seeds.index(self._radio_seed) + 1) % len(seeds)]
            return seeds[0]
        if not self.radio_start:
            return None
        if self._start_id() is None:
            track = await asyncio.to_thread(self._resolve, self.radio_start)
            self._start_seed = (self.radio_start, track.video_id)
        return self._start_id()

    async def _next_radio_track(self) -> Track | None:
        for _ in range(3):  # A used-up radio, then the next seed's.
            while self._radio_queue:
                track = self._radio_queue.pop(0)
                if track.video_id not in self._played and track.video_id not in self.radio_seeds:
                    return track
            seed = await self._choose_seed()
            if seed is None:
                return None
            self._radio_seed = seed
            self._radio_queue = await asyncio.to_thread(self._radio, seed)
        return None

    async def _fill_from_radio(self) -> None:
        try:
            for _ in range(MAX_RADIO_MISSES):
                if not self.radio_on or self.playlist is not None or not self._idle():
                    return
                track = await self._next_radio_track()
                if track is None:
                    return  # Nothing to follow yet.
                if not self.radio_on or self.playlist is not None or not self._idle():
                    self._radio_queue.insert(0, track)  # A request came in meanwhile: keep it for later.
                    return
                try:
                    self.queue.add(track, RADIO_USER, privileged=True)
                    self._radio_status()
                    return
                except QueueRejected as error:  # Too long, explicit, banned…: the next one.
                    log.info("Autoplay: skipping %r: %s", track.title, error)
        except TrackLookupError as error:
            log.warning("Autoplay: %s", error)
            self._radio_status(f"Paused: {error} It tries again after the next song.")
        except Exception:
            log.exception("Autoplay failed unexpectedly")
        finally:
            self._filling = False

    async def _fill_from_playlist(self) -> None:
        """Look up the next playlist song on YouTube (one search at a time) and play it."""
        playlist = self.playlist
        try:
            for _ in range(MAX_PLAYLIST_MISSES):
                if self.playlist is not playlist or playlist is None:
                    return  # Stopped or replaced meanwhile.
                if self.queue.current is not None or self.queue.upcoming:
                    return  # A viewer request arrived first.
                song = self._peek()
                self._playlist_pos += 1  # Loops back to the start after the last song.
                try:
                    # From a YouTube playlist it's already a track; from Spotify, look it up by name.
                    track = song.track or await asyncio.to_thread(self._resolve, song.request)
                    if self.playlist is not playlist or self.queue.current is not None or self.queue.upcoming:
                        self._playlist_pos -= 1  # Not played: keep it for the next gap.
                        return
                    self.queue.add(track, PLAYLIST_USER, privileged=True)
                    self._playlist_changed()
                    return
                except (TrackLookupError, QueueRejected) as error:
                    log.info("Playlist: skipping %r: %s", song.request, error)
                except Exception:
                    log.exception("Playlist: lookup of %r failed unexpectedly", song.request)
                    return
            await self.overlay.toast(
                f"Playlist paused: {MAX_PLAYLIST_MISSES} songs in a row couldn't be played (not found on YouTube, "
                "or refused by your song request limits, e.g. too long)."
            )
            self.playlist = None
            self._playlist_changed()
        finally:
            self._filling = False

    async def handle_comment(self, comment: ChatComment) -> None:
        outcome = self.engine.handle(comment, self._clock())
        if outcome is None:
            return
        if isinstance(outcome, Denied):
            await self._reply(outcome.user, outcome.reason)
            return
        try:
            await self._run(outcome, paid=True)
        except (QueueRejected, TrackLookupError) as error:
            await self._reply(outcome.user, str(error))

    async def request(self, user: ChatUser, text: str) -> None:
        """A request from an action rule (e.g. a gift): no command cooldown, but the queue's rules still apply."""
        try:
            await self._run(Command(CommandKind.REQUEST, text.strip(), user))
        except (QueueRejected, TrackLookupError) as error:
            await self._reply(user, str(error))

    async def _reply(self, user: ChatUser, reason: str) -> None:
        await self.overlay.toast(
            self.texts.format("music.reply", user=user.nickname, platform=user.platform_name, reason=reason)
        )

    def _pay(self, user: ChatUser, kind: str) -> None:
        reason = self.charge(user, kind) if self.charge else ""
        if reason:
            raise QueueRejected(reason)

    def _give_back(self, user: ChatUser, kind: str) -> None:
        if self.refund:
            self.refund(user, kind)

    async def _run(self, command: Command, paid: bool = False) -> None:
        """``paid``: from chat, so loyalty-point costs apply (rule requests are free)."""
        user = command.user
        privileged = role_of(user) >= Role.MODS
        if command.kind is CommandKind.REQUEST:
            if parse_request(command.args) is None:
                raise TrackLookupError("Send a YouTube link or song - artist.")
            blocked = self.queue.rules.blocked
            if blocked and blocked(command.args):  # Don't even search for it.
                raise QueueRejected(self.texts.format("queue.blocked_word"), "queue.blocked_word")
            if paid:
                self._pay(user, "request")
            try:
                track = await asyncio.to_thread(self._resolve, command.args)
                try:
                    position = self.queue.add(track, user, privileged)
                except QueueRejected as error:
                    if error.key in AUTO_BAN and self.on_refused:
                        self.on_refused(track, error.key)
                    raise
            except Exception:  # Whatever went wrong, the points come back.
                if paid:
                    self._give_back(user, "request")
                raise
            self.remember_request(track)  # Autoplay follows chat's taste (its radio is official audio only).
            values = dict(user=user.nickname, platform=user.platform_name, title=track.title, artist=track.channel)
            await self.overlay.toast(
                self.texts.format("music.added_now", **values) if position == 0
                else self.texts.format("music.added_queued", position=position, **values)
            )
        elif command.kind is CommandKind.SKIP:
            skipped = self.queue.current
            self.queue.skip(user, privileged=role_of(user) >= self.engine.config.skip_role)
            if skipped:
                await self.overlay.toast(self.texts.format(
                    "music.skipped", title=skipped.track.title, requester=skipped.requested_by.nickname
                ))
        elif command.kind is CommandKind.BUMP:
            self._pay(user, "bump")
            try:
                item = self.queue.bump(user)
            except QueueRejected:
                self._give_back(user, "bump")
                raise
            await self.overlay.toast(self.texts.format("points.bumped", user=user.nickname, title=item.track.title))
        elif command.kind is CommandKind.REVOKE:
            removed = self.queue.revoke(user)
            await self.overlay.toast(self.texts.format("music.revoked", user=user.nickname, title=removed.track.title))
        elif command.kind is CommandKind.CURRENT:
            current = self.queue.current
            await self.overlay.toast(
                self.texts.format("music.now_playing", title=current.track.title, artist=current.track.channel,
                                  requester=current.requested_by.nickname)
                if current else self.texts.format("music.nothing_playing")
            )
        elif command.kind is CommandKind.QUEUE:
            count = len(self.queue.upcoming)
            songs = f"{count} song{'s' if count != 1 else ''}"
            await self.overlay.toast(self.texts.format("music.queue_count", songs=songs, count=count))
        elif self.on_other_command is not None:
            self.on_other_command(command)

    def on_player_message(self, message: dict) -> None:
        current = self.queue.current
        # Several OBS sources may report the same end; only the first counts.
        if current is None or message.get("item_id") != current.id:
            return
        if message.get("type") == "ended":
            self.queue.advance()
        elif message.get("type") == "error":
            reason = _UNPLAYABLE.get(message.get("code"), "can't be played")
            self._spawn(self.overlay.toast(self.texts.format("music.unplayable", title=current.track.title, reason=reason)))
            self.queue.advance()

    def _spawn(self, coroutine) -> None:
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
