"""Which chat events get read aloud, and the exact words. Pure logic, no Qt.

Everything comes from Settings (File → Preferences → Text to speech) and the
``tts.*`` message templates, so streamers decide the wording, e.g. the default
"From {platform}, {user} says {message}".
"""

from __future__ import annotations

import re
import time
from typing import Callable

from ignite_live.core.events import ChatComment, ChatEvent, ChatFollow, ChatGift
from ignite_live.core.settings import Settings
from ignite_live.core.templates import Texts
from ignite_live.moderation.filter import Moderation
from ignite_live.music.commands import Role, role_of

_LINK = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_SPACES = re.compile(r"\s{2,}")


def clean(text: str, skip_links: bool) -> str:
    text = " ".join(text.split())
    if skip_links:
        text = _LINK.sub("link", text)
    return text


def shorten(text: str, limit: int) -> str:
    """Cut at a word boundary so the voice doesn't stop mid-word."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return (cut or text[:limit]).rstrip(",.;:") + "…"


def gift_value(event: ChatGift) -> str:
    if event.amount:
        return f" worth {event.amount}"
    if event.diamonds:
        return f" worth {event.diamonds * event.count:,} diamonds"
    return ""


class TtsRules:
    def __init__(self, settings: Settings | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.settings = settings or Settings()
        self.texts = Texts(self.settings.texts)
        self.moderation = Moderation.from_settings(self.settings)
        self._clock = clock
        self._last: dict[str, float] = {}

    def update(self, settings: Settings) -> None:
        self.settings = settings
        self.texts = Texts(settings.texts)
        self.moderation = Moderation.from_settings(settings)

    def text_for(self, event: ChatEvent) -> str | None:
        """What to say for this event, or None to stay quiet."""
        s = self.settings
        user = getattr(event, "user", None)
        if not s.tts_enabled or user is None or user.platform not in s.tts_platforms:
            return None
        if self.moderation.blocked_user(user):
            return None
        name = user.nickname or user.unique_id
        values = {"user": "someone" if self.moderation.says_blocked(name, "tts") else name,
                  "platform": user.platform_name}
        if isinstance(event, ChatComment):
            message = self._comment(event)
            if message is None:
                return None
            text = self.texts.format("tts.comment", message=message, **values)
        elif isinstance(event, ChatGift):
            if not s.tts_gifts or event.streaking:  # A combo is read once, at its total.
                return None
            message = clean(event.message, s.tts_skip_links)
            if message and self.moderation.says_blocked(message, "tts"):
                message = ""
            values.update(gift=event.gift_name, count=event.count, amount=event.amount, value=gift_value(event),
                          message=shorten(message, s.tts_max_chars))
            text = self.texts.format("tts.paid" if message else "tts.gift", **values)
        elif isinstance(event, ChatFollow):
            if not s.tts_follows:
                return None
            text = self.texts.format("tts.follow", **values)
        else:
            return None
        text = _SPACES.sub(" ", text).strip()
        return text or None

    def _comment(self, event: ChatComment) -> str | None:
        s = self.settings
        if not s.tts_comments or role_of(event.user) < Role[s.tts_role.upper()]:
            return None
        text = event.text.strip()
        prefix = s.command_prefix or "!"
        if s.tts_command:
            word, _, rest = text.partition(" ")
            if word.lower() != f"{prefix}{s.tts_command.lstrip(prefix).lower()}":
                return None
            text = rest
        elif s.tts_skip_commands and text.startswith(prefix):
            return None
        text = clean(text, s.tts_skip_links)
        if not text or self.moderation.says_blocked(text, "tts"):
            return None
        if s.tts_user_cooldown_s and role_of(event.user) < Role.MODS:
            now = self._clock()
            if now - self._last.get(event.user.key, float("-inf")) < s.tts_user_cooldown_s:
                return None
            self._last[event.user.key] = now
        return shorten(text, s.tts_max_chars)
