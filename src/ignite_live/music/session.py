"""Glue between chat commands, the queue, song lookup, and the player overlay."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Callable

from ignite_live.core.events import ChatComment, ChatUser
from ignite_live.music.commands import Command, CommandEngine, CommandKind, Denied, Role, role_of
from ignite_live.music.queue import QueueRejected, SongQueue
from ignite_live.music.spotify import Playlist, PlaylistSong
from ignite_live.music.youtube import Track, TrackLookupError, parse_request
from ignite_live.overlay.server import OverlayServer

log = logging.getLogger(__name__)

PLAYLIST_USER = ChatUser("playlist", "Playlist")
# Refusals for what the song is (not for the queue being full): these can ban it for good (auto-ban).
AUTO_BAN = {"queue.too_long", "queue.explicit", "queue.blocked_word"}
# Give up on a playlist after this many songs in a row can't be found.
MAX_PLAYLIST_MISSES = 5

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
    ) -> None:
        """``resolve`` is blocking (YouTubeClient or BackendClient); it runs off-loop."""
        self.overlay = overlay
        self.engine = engine or CommandEngine()
        self.queue = queue or SongQueue()
        self._resolve = resolve
        self._clock = clock
        self._tasks: set[asyncio.Task] = set()
        self.queue.on_change = self._queue_changed
        overlay.on_player_message = self.on_player_message
        self.playlist: Playlist | None = None
        self._playlist_order: list[int] = []
        self._playlist_pos = 0
        self._filling = False
        self.on_playlist_change: Callable[[dict | None], None] | None = None
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
        self._maybe_fill()

    def _maybe_fill(self) -> None:
        idle = self.queue.current is None and not self.queue.upcoming
        if idle and self.playlist is not None and not self._filling:
            self._filling = True
            self._spawn(self._fill_from_playlist())

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
                    track = await asyncio.to_thread(self._resolve, song.request)
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
                f"Playlist paused: {MAX_PLAYLIST_MISSES} songs in a row couldn't be found on YouTube."
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
