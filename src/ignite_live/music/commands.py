"""Chat command parsing with roles and cooldowns. Pure logic, no I/O."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from ignite_live.core.events import ChatComment, ChatUser
from ignite_live.core.templates import Texts


class Role(enum.IntEnum):
    EVERYONE = 0
    FOLLOWERS = 1
    MODS = 2


class CommandKind(enum.Enum):
    REQUEST = "request"
    SKIP = "skip"
    REVOKE = "revoke"
    CURRENT = "current"
    QUEUE = "queue"
    TTS_SKIP = "tts_skip"  # Moderators: stop the text-to-speech message being read.
    CLIP = "clip"  # Moderators: save the OBS replay buffer.
    SCENE = "scene"  # Moderators: switch OBS scenes.
    # Loyalty points (only while points are on).
    POINTS = "points"  # Your balance.
    BUMP = "bump"  # Your next song plays next.
    SAY = "say"  # Paid text to speech.
    SOUND = "sound"  # Play a sound effect.
    GIVE = "give"  # Moderators: give or take points.
    # Moderation.
    BLOCK = "block"  # Moderators: Ignite ignores this viewer.
    UNBLOCK = "unblock"
    PANIC = "panic"  # Moderators: the panic button (off unless allowed).


# Commands only moderators (and the streamer) can use; anyone else is ignored silently.
MOD_ONLY = {CommandKind.TTS_SKIP, CommandKind.CLIP, CommandKind.SCENE, CommandKind.GIVE, CommandKind.BLOCK,
            CommandKind.UNBLOCK, CommandKind.PANIC}
POINTS_COMMANDS = (CommandKind.POINTS, CommandKind.BUMP, CommandKind.SAY, CommandKind.SOUND, CommandKind.GIVE)


@dataclass
class CommandConfig:
    prefix: str = "!"
    aliases: dict[CommandKind, tuple[str, ...]] = field(
        default_factory=lambda: {
            CommandKind.REQUEST: ("sr", "play"),
            CommandKind.SKIP: ("skip",),
            CommandKind.REVOKE: ("revoke", "wrongsong"),
            CommandKind.CURRENT: ("song",),
            CommandKind.QUEUE: ("queue",),
            CommandKind.TTS_SKIP: ("skiptts",),
            CommandKind.CLIP: ("clip",),
            CommandKind.SCENE: ("scene",),
            CommandKind.POINTS: ("points",),
            CommandKind.BUMP: ("bump",),
            CommandKind.SAY: ("say",),
            CommandKind.SOUND: ("sound",),
            CommandKind.GIVE: ("give",),
            CommandKind.BLOCK: ("block",),
            CommandKind.UNBLOCK: ("unblock",),
            CommandKind.PANIC: ("panic",),
        }
    )
    request_role: Role = Role.EVERYONE
    # Anyone may skip their own song; this role may skip anyone's.
    skip_role: Role = Role.MODS
    user_request_cooldown_s: float = 30.0
    global_cooldown_s: float = 2.0
    texts: Texts = field(default_factory=Texts)

    @property
    def request_usage(self) -> str:
        """How to request, e.g. "!sr song - artist" (for messages and overlays)."""
        names = self.aliases.get(CommandKind.REQUEST) or ("sr",)
        return f"{self.prefix}{names[0]} song - artist"


def parse_names(text: str) -> tuple[str, ...]:
    """"sr, play" -> ("sr", "play"): lowercase, no prefix or spaces, no duplicates."""
    names = []
    for part in text.replace(" ", ",").split(","):
        name = part.strip().lstrip("!").lower()
        if name and name not in names:
            names.append(name)
    return tuple(names)


@dataclass(frozen=True)
class Command:
    kind: CommandKind
    args: str
    user: ChatUser


@dataclass(frozen=True)
class Denied:
    user: ChatUser
    reason: str


def role_of(user: ChatUser) -> Role:
    """On Twitch and YouTube, where follows aren't visible in chat, subscribers/members count as followers."""
    if user.is_host or user.is_moderator:
        return Role.MODS
    if user.is_follower or user.is_subscriber:
        return Role.FOLLOWERS
    return Role.EVERYONE


class CommandEngine:
    def __init__(self, config: CommandConfig | None = None) -> None:
        self.config = config or CommandConfig()
        self._last_request: dict[str, float] = {}
        self._last_any = float("-inf")

    def handle(self, comment: ChatComment, now: float) -> Command | Denied | None:
        """``None`` means the comment isn't a command; ignore it silently."""
        text = comment.text.strip()
        prefix = self.config.prefix
        if not text.startswith(prefix):
            return None
        word, _, args = text[len(prefix):].partition(" ")
        kind = self._kind(word.lower())
        if kind is None:
            return None
        user = comment.user
        role = role_of(user)
        if role < Role.MODS and now - self._last_any < self.config.global_cooldown_s:
            return None  # Spam guard: drop silently rather than flood feedback.
        texts = self.config.texts
        if kind in MOD_ONLY and role < Role.MODS:
            return None
        if kind is CommandKind.REQUEST:
            if role < self.config.request_role:
                need = "followers" if self.config.request_role is Role.FOLLOWERS else "mods"
                return Denied(user, texts.format("music.denied_role", role=need))
            if not args.strip():
                return Denied(user, texts.format("music.denied_usage", command=f"{prefix}{word} song - artist"))
            last = self._last_request.get(user.key, float("-inf"))
            wait = self.config.user_request_cooldown_s - (now - last)
            if role < Role.MODS and wait > 0:
                return Denied(user, texts.format("music.denied_cooldown", seconds=int(wait) + 1))
            self._last_request[user.key] = now
        self._last_any = now
        return Command(kind, args.strip(), user)

    def _kind(self, word: str) -> CommandKind | None:
        for kind, names in self.config.aliases.items():
            if word in names:
                return kind
        return None
