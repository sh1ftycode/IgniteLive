"""Every message viewers see or hear, as an editable template with {placeholders}.

Streamers change these in File → Preferences → Messages. Only the changed ones
are saved (``Settings.texts``); everything else follows the defaults here, so
new defaults reach people who never touched a message.

Placeholders are plain ``{name}`` words. Unknown ones are left as typed, and
values are inserted as text, never evaluated (unlike ``str.format``, a
template can't reach into Python objects).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class Template:
    key: str
    group: str
    label: str
    default: str
    placeholders: tuple[str, ...]


# Placeholder descriptions, shown under the editor.
PLACEHOLDERS = {
    "user": "viewer's display name",
    "platform": "TikTok, Twitch or YouTube",
    "message": "what they wrote",
    "gift": "gift name (Rose, Bits, Super Chat…)",
    "count": "how many",
    "times": "“ ×5”, or nothing for a single gift",
    "amount": "money sent, e.g. $5.00 (Super Chats)",
    "value": "gift value (diamonds or money), with a leading space; empty if none",
    "badge": "(host) for you, (mod) for moderators",
    "title": "song title",
    "artist": "channel or artist",
    "position": "place in the queue",
    "requester": "who requested the song",
    "reason": "why it didn't work",
    "role": "who's allowed (followers, mods)",
    "command": "the request command, e.g. !sr song - artist",
    "seconds": "seconds left",
    "minutes": "minutes",
    "songs": "e.g. 3 songs",
    "creator": "your username on that platform",
    "scene": "OBS scene name",
    "points": "the viewer's points",
    "currency": "what points are called (Viewers tab)",
    "cost": "what it costs",
    "target": "who gets the points",
    "sound": "sound name",
    "sounds": "the sounds there are",
}

_GIFT = ("user", "platform", "gift", "count", "amount", "value", "message")

TEMPLATES: tuple[Template, ...] = (
    # Text to speech.
    Template("tts.comment", "Text to speech", "Comment", "From {platform}, {user} says {message}",
             ("user", "platform", "message")),
    Template("tts.gift", "Text to speech", "Gift", "From {platform}, {user} sent {count} {gift}{value}", _GIFT),
    Template("tts.paid", "Text to speech", "Gift with a message (Super Chat, cheer)",
             "From {platform}, {user} sent {count} {gift}{value} and says {message}", _GIFT),
    Template("tts.follow", "Text to speech", "Follow", "{user} followed on {platform}", ("user", "platform")),
    # Song request replies (toasts in the overlays and the Activity panel).
    Template("music.added_now", "Song requests", "Added, plays now", "@{user} added {title}, playing now",
             ("user", "platform", "title", "artist")),
    Template("music.added_queued", "Song requests", "Added to the queue",
             "@{user} added {title}, #{position} in the queue", ("user", "platform", "title", "artist", "position")),
    Template("music.skipped", "Song requests", "Skipped", "Skipped {title}", ("title", "requester")),
    Template("music.revoked", "Song requests", "Request removed", "@{user} removed {title}", ("user", "title")),
    Template("music.now_playing", "Song requests", "Now playing (!song)", "Now playing: {title} (requested by {requester})",
             ("title", "artist", "requester")),
    Template("music.nothing_playing", "Song requests", "Nothing playing (!song)", "Nothing is playing.", ()),
    Template("music.queue_count", "Song requests", "Queue size (!queue)", "{songs} in the queue", ("songs", "count")),
    Template("music.unplayable", "Song requests", "Video wouldn't play", "Skipped {title}: {reason}", ("title", "reason")),
    Template("music.reply", "Song requests", "Reply to a viewer", "@{user}: {reason}", ("user", "platform", "reason")),
    Template("music.denied_role", "Song requests", "Not allowed to request", "Song requests are for {role} only.", ("role",)),
    Template("music.denied_usage", "Song requests", "Request without a song",
             "Use {command}, or paste a YouTube link.", ("command",)),
    Template("music.denied_cooldown", "Song requests", "Cooldown", "Wait {seconds}s before requesting again.",
             ("seconds",)),
    Template("queue.banned", "Song requests", "Banned song", "That song is banned here.", ()),
    Template("queue.not_embeddable", "Song requests", "Can't be embedded", "That video can't be played in the overlay.", ()),
    Template("queue.age_restricted", "Song requests", "Age-restricted", "Age-restricted videos can't be played.", ()),
    Template("queue.too_long", "Song requests", "Too long", "Songs must be under {minutes} minutes.", ("minutes",)),
    Template("queue.unknown_length", "Song requests", "Length unknown",
             "Couldn't check that song's length (songs must be under {minutes} minutes).", ("minutes",)),
    Template("queue.explicit", "Song requests", "Explicit", "Explicit songs aren't allowed here. Try a clean version.", ()),
    Template("queue.blocked_word", "Song requests", "Blocked word", "That song isn't allowed here.", ()),
    Template("queue.duplicate", "Song requests", "Already queued", "That song is already in the queue.", ()),
    Template("queue.full", "Song requests", "Queue full", "The queue is full. Try again later.", ()),
    Template("queue.per_user", "Song requests", "Too many requests", "You already have {count} songs queued.", ("count",)),
    Template("queue.nothing_playing", "Song requests", "Skip with nothing playing", "Nothing is playing.", ()),
    Template("queue.not_yours", "Song requests", "Skipping someone else's song", "You can only skip your own song.", ()),
    Template("queue.no_upcoming", "Song requests", "Nothing to remove", "You have no upcoming songs.", ()),
    # OBS chat commands (moderators).
    Template("obs.clip_saved", "OBS", "Clip saved (!clip)", "Clip saved!", ("user",)),
    Template("obs.clip_unavailable", "OBS", "Clip without replay buffer",
             "Clips need the replay buffer: turn it on in OBS.", ("user",)),
    Template("obs.scene_switched", "OBS", "Scene switched (!scene)", "Switched to {scene}", ("user", "scene")),
    Template("obs.scene_unknown", "OBS", "Unknown scene", "No scene called {scene}.", ("user", "scene")),
    # Moderation.
    Template("mod.blocked", "Moderation", "Viewer blocked (!block)", "{target} is blocked.", ("user", "target")),
    Template("mod.unblocked", "Moderation", "Viewer unblocked (!unblock)", "{target} is unblocked.",
             ("user", "target")),
    # Loyalty points.
    Template("points.balance", "Points", "Balance (!points)", "@{user} has {points} {currency}",
             ("user", "points", "currency")),
    Template("points.not_enough", "Points", "Not enough points", "@{user}: that costs {cost} {currency}, you have {points}",
             ("user", "cost", "points", "currency")),
    Template("points.bumped", "Points", "Song moved up (!bump)", "@{user} moved {title} to play next",
             ("user", "title")),
    Template("points.given", "Points", "Points given (!give)", "{user} gave {count} {currency} to {target}",
             ("user", "count", "currency", "target")),
    Template("points.unknown_viewer", "Points", "Unknown viewer (!give)", "No viewer called {target} yet.",
             ("user", "target")),
    Template("points.sound_unknown", "Points", "Unknown sound (!sound)", "@{user}: sounds are {sounds}",
             ("user", "sound", "sounds")),
    Template("points.tts_off", "Points", "Text to speech off (!say)", "@{user}: text to speech is off right now",
             ("user",)),
    # Overlay labels.
    Template("overlay.song_requests", "Overlays", "Player label (idle)", "Song requests", ()),
    Template("overlay.now_playing", "Overlays", "Now playing label", "Now playing", ()),
    Template("overlay.nothing_playing", "Overlays", "Nothing playing", "Nothing playing", ()),
    Template("overlay.request_hint", "Overlays", "How to request", "Type {command} in chat", ("command",)),
    Template("overlay.next", "Overlays", "Next song prefix", "Next: ", ()),
    Template("overlay.up_next", "Overlays", "Queue title", "Up next", ()),
    Template("overlay.queue_empty", "Overlays", "Empty queue", "Request a song: {command}", ("command",)),
    Template("overlay.chat_gift", "Overlays", "Chat: gift line", "sent {gift}{times}{value}",
             ("gift", "count", "times", "value", "amount", "message", "platform")),
    Template("overlay.chat_follow", "Overlays", "Chat: follow line", "followed", ("platform",)),
    Template("overlay.alert_gift_title", "Overlays", "Alert: gift heading", "New gift", ("platform",)),
    Template("overlay.alert_gift_text", "Overlays", "Alert: gift text", "sent {gift}{value}",
             ("gift", "count", "times", "value", "amount", "message", "platform")),
    Template("overlay.alert_follow_title", "Overlays", "Alert: follow heading", "New follower", ("platform",)),
    Template("overlay.alert_follow_text", "Overlays", "Alert: follow text", "just followed", ("platform",)),
    # Discord bridge. Viewer text is escaped; your own template text is sent as written (Discord markdown works).
    Template("discord.comment", "Discord", "Comment", "[{platform}] **{user}**{badge}: {message}",
             ("user", "badge", "platform", "message")),
    Template("discord.gift", "Discord", "Gift", "[{platform}] **{user}**{badge} sent **{gift}**{times}{value}",
             ("user", "badge", "platform", "gift", "count", "times", "value", "amount", "message")),
    Template("discord.follow", "Discord", "Follow", "[{platform}] **{user}**{badge} followed",
             ("user", "badge", "platform")),
    Template("discord.live", "Discord", "Went live", "**{creator} is LIVE on {platform}.** Chat is forwarded here.",
             ("creator", "platform")),
    Template("discord.test", "Discord", "Test message", "Ignite is connected. Chat from {creator} will show up here.",
             ("creator",)),
    Template("discord.name", "Discord", "Webhook name", "LIVE chat · {creator}", ("creator",)),
)

BY_KEY = {template.key: template for template in TEMPLATES}
GROUPS = tuple(dict.fromkeys(template.group for template in TEMPLATES))

# Example values for previews in Preferences → Messages.
SAMPLE = {
    "user": "Ember", "platform": "Twitch", "message": "this song is fire", "gift": "Rose", "count": 5,
    "amount": "$5.00", "value": " worth $5.00", "times": " ×5", "badge": " (mod)", "title": "Blinding Lights", "artist": "The Weeknd",
    "position": 3, "requester": "Ember", "reason": "Wait 12s before requesting again.", "role": "followers",
    "command": "!sr song - artist", "seconds": 12, "minutes": 10, "songs": "3 songs", "creator": "@ember", "scene": "Just Chatting",
    "points": 1250, "currency": "points", "cost": 500, "target": "NightOwl", "sound": "airhorn",
    "sounds": "airhorn, boom, sad trombone",
}
MAX_LENGTH = 300


def render(text: str, values: dict) -> str:
    """Fill ``{name}`` placeholders; unknown names stay as typed."""
    return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text)


def unknown_placeholders(key: str, text: str) -> list[str]:
    allowed = set(BY_KEY[key].placeholders)
    return sorted({name for name in _PLACEHOLDER.findall(text) if name not in allowed})


class Texts:
    """The defaults with the streamer's changes on top."""

    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        self.overrides = {
            key: value[:MAX_LENGTH] for key, value in (overrides or {}).items()
            if key in BY_KEY and isinstance(value, str)
        }

    def template(self, key: str) -> str:
        return self.overrides.get(key, BY_KEY[key].default)

    def format(self, key: str, **values) -> str:
        return render(self.template(key), values)

    def group(self, prefix: str) -> dict[str, str]:
        """Templates whose key starts with ``prefix`` (e.g. the overlays' own, rendered in the page)."""
        return {t.key: self.template(t.key) for t in TEMPLATES if t.key.startswith(prefix)}
