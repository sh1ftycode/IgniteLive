"""Blocked words, links, and blocked viewers: one filter for everything Ignite shows, says, or plays.

TikTok, Twitch and YouTube chat are read-only here, so nothing is removed from the
platforms themselves; this decides what reaches text to speech, the overlays,
Discord, song requests, and rule texts. Pure logic, no Qt.

Words match whole words, ignoring case and accents, and see through common dodges:
``b.a.d``, ``b a d``, ``b4d``, ``baaad``. A ``*`` matches the rest of a word (``bad*``).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import replace

from ignite_live.core.events import ChatComment, ChatEvent, ChatGift, ChatUser

MASK = "***"
LINK_MASK = "[link]"
# Characters people swap in for letters.
_LOOKALIKES = {"a": "a4@", "b": "b8", "e": "e3", "g": "g9", "i": "i1!|", "l": "l1|", "o": "o0", "s": "s5$",
               "t": "t7+", "z": "z2"}
_SEPARATOR = r"[\W_]*"  # Dots, spaces, dashes between letters.
_EDGE_BEFORE = r"(?<![^\W_])"  # Not in the middle of a word.
_EDGE_AFTER = r"(?![^\W_])"
LINK = re.compile(
    r"(?i)\b(?:https?://|www\.)\S+"
    r"|\b[\w-]+(?:\s*\.\s*|\s+dot\s+)(?:com|net|org|gg|io|tv|me|ly|xyz|co|link|shop|app|live|info|ru|cc)\b(?:/\S*)?"
)
MAX_WORDS = 500


def fold(text: str) -> str:
    """Lower case without accents or look-alike Unicode (ｂａｄ, bád → bad)."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _letter(char: str) -> str:
    options = _LOOKALIKES.get(char, char)
    return "[" + "".join(re.escape(c) for c in options) + "]+"


def word_pattern(word: str) -> str | None:
    """The regex for one blocked word or phrase, or None if there's nothing to match."""
    word = fold(word.strip())
    wildcard = word.endswith("*")
    word = word.rstrip("*").strip()
    if not word:
        return None
    parts = []
    for piece in word.split():
        letters = [_letter(c) for c in piece if not c.isspace()]
        parts.append(_SEPARATOR.join(letters))
    body = r"[\W_]+".join(parts)
    return _EDGE_BEFORE + body + (r"[^\W_]*" if wildcard else "") + _EDGE_AFTER


def compile_words(words: list[str]) -> re.Pattern | None:
    patterns = [p for p in (word_pattern(w) for w in words[:MAX_WORDS]) if p]
    return re.compile("|".join(patterns)) if patterns else None


def _fold_map(text: str) -> tuple[str, list[int]]:
    """The folded text, and for each folded character the index of the original one (to mask the original)."""
    folded, origin = [], []
    for index, char in enumerate(text):
        for piece in fold(char):
            folded.append(piece)
            origin.append(index)
    return "".join(folded), origin


class WordFilter:
    def __init__(self, words: list[str] = (), hide_links: bool = False) -> None:
        self.pattern = compile_words(list(words))
        self.hide_links = hide_links

    def matches(self, text: str) -> bool:
        return bool(self.pattern and self.pattern.search(fold(text)))

    def mask(self, text: str) -> str:
        """Blocked words become ***, and links [link] when links are hidden."""
        if self.hide_links:
            text = LINK.sub(LINK_MASK, text)
        if not self.pattern or not text:
            return text
        folded, origin = _fold_map(text)
        spans = [(origin[m.start()], origin[m.end() - 1] + 1) for m in self.pattern.finditer(folded) if m.end() > m.start()]
        for start, end in reversed(spans):
            text = text[:start] + MASK + text[end:]
        return text


def is_blocked(user: ChatUser, entries: list[str]) -> bool:
    """``entries``: "platform:id" (from the Viewers tab or !block), or a plain name for any platform."""
    if not entries:
        return False
    names = {user.unique_id.lower(), (user.nickname or "").lower()}
    for entry in entries:
        entry = entry.strip().lstrip("@").lower()
        if not entry:
            continue
        if ":" in entry and entry.split(":", 1)[0] in ("tiktok", "twitch", "youtube"):
            if entry == user.key.lower():
                return True
        elif entry in names:
            return True
    return False


class Moderation:
    """The streamer's moderation settings, applied where each one is switched on."""

    def __init__(self, words: list[str] = (), hide_links: bool = False, chat_mode: str = "mask",
                 blocked_users: list[str] = (), places: frozenset[str] = frozenset()) -> None:
        self.words = WordFilter(words, hide_links=False)
        self.links_and_words = WordFilter(words, hide_links=hide_links)
        self.hide_links = hide_links
        self.chat_mode = chat_mode
        self.blocked_users = list(blocked_users)
        self.places = places  # Where blocked words apply: tts, overlay, discord, songs, rules.

    @classmethod
    def from_settings(cls, s) -> "Moderation":
        places = frozenset(name for name in ("tts", "overlay", "discord", "songs", "rules")
                           if getattr(s, f"mod_filter_{name}"))
        return cls(s.blocked_words, s.mod_hide_links, s.mod_chat_mode, s.blocked_users, places)

    def blocked_user(self, user: ChatUser | None) -> bool:
        return user is not None and is_blocked(user, self.blocked_users)

    def says_blocked(self, text: str, place: str) -> bool:
        return place in self.places and self.words.matches(text)

    def clean(self, text: str, place: str) -> str:
        """For overlays, Discord, and rule texts: words masked where switched on, links hidden if set."""
        if place in self.places:
            return self.links_and_words.mask(text)
        return LINK.sub(LINK_MASK, text) if self.hide_links else text

    def shown(self, event: ChatEvent, place: str) -> ChatEvent | None:
        """The event as the overlays or Discord show it, or None to leave it out (hide mode)."""
        if isinstance(event, ChatComment):
            if self.chat_mode == "hide" and self.says_blocked(event.text + " " + event.user.nickname, place):
                return None
            return replace(event, text=self.clean(event.text, place), user=self._user(event.user, place))
        if isinstance(event, ChatGift):
            return replace(event, message=self.clean(event.message, place), user=self._user(event.user, place))
        user = getattr(event, "user", None)
        if user is not None:
            return replace(event, user=self._user(user, place))
        return event

    def _user(self, user: ChatUser, place: str) -> ChatUser:
        name = self.clean(user.nickname, place) if place in self.places else user.nickname
        return user if name == user.nickname else replace(user, nickname=name)
