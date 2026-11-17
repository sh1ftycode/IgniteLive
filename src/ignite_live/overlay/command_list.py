"""Every chat command Ignite answers right now, for the Commands overlay (static/commands.html).

Built from the same settings the commands themselves use (controller.command_config), so the overlay never
lists one that's switched off, under a name that was changed, or without its price in points. Game commands
come with the game itself (the overlay shows them from the "game" message while one runs).
"""

from __future__ import annotations

from ignite_live.actions.rules import parse_rules
from ignite_live.core.settings import Settings
from ignite_live.music.commands import MOD_ONLY, CommandKind, Role
from ignite_live.viewers.points import parse_config as points_config

# What each built-in command does, in the words a viewer reads on stream. Order: what viewers use most first.
DESCRIPTIONS: dict[CommandKind, tuple[str, str, str]] = {  # kind -> (group, usage, what it does)
    CommandKind.REQUEST: ("Music", "song - artist", "Request a song (or paste a YouTube link)"),
    CommandKind.CURRENT: ("Music", "", "What's playing now"),
    CommandKind.QUEUE: ("Music", "", "What's coming up"),
    CommandKind.REVOKE: ("Music", "", "Take back your last request"),
    CommandKind.SKIP: ("Music", "", "Skip the song"),
    CommandKind.POINTS: ("Points", "", "Your points"),
    CommandKind.BUMP: ("Points", "", "Move your song to play next"),
    CommandKind.SAY: ("Points", "message", "Have your message read aloud"),
    CommandKind.SOUND: ("Points", "name", "Play a sound"),
    CommandKind.TTS_SKIP: ("Moderators", "", "Stop the message being read aloud"),
    CommandKind.CLIP: ("Moderators", "", "Save a clip"),
    CommandKind.SCENE: ("Moderators", "name", "Switch scenes"),
    CommandKind.GIVE: ("Moderators", "@name amount", "Give or take points"),
    CommandKind.BLOCK: ("Moderators", "@name", "Ignite ignores this viewer"),
    CommandKind.UNBLOCK: ("Moderators", "@name", "Stop ignoring this viewer"),
    CommandKind.PANIC: ("Moderators", "", "Panic: mute, pause and switch to the BRB scene"),
}
WHO = {Role.EVERYONE: "everyone", Role.FOLLOWERS: "followers", Role.MODS: "mods"}
MAX_RULE_TEXT = 80


def command_list(settings: Settings) -> list[dict]:
    """[{"group", "name": "!sr", "aliases": ["!play"], "usage", "what", "who", "cost"}], in display order."""
    from ignite_live.controller import command_config  # (It imports the overlay package.)

    config = command_config(settings)
    prefix = config.prefix
    points = points_config(settings.points)
    costs = {CommandKind.REQUEST: points.cost_request, CommandKind.BUMP: points.cost_bump,
             CommandKind.SAY: points.cost_say, CommandKind.SOUND: points.cost_sound} if points.enabled else {}
    commands = []
    for kind, (group, usage, what) in DESCRIPTIONS.items():
        names = config.aliases.get(kind)
        if not names:
            continue  # Switched off (clip, scene, panic, points…).
        if kind in MOD_ONLY:
            who = Role.MODS
        elif kind is CommandKind.REQUEST:
            who = config.request_role
        elif kind is CommandKind.SKIP:
            who = config.skip_role
        else:
            who = Role.EVERYONE
        commands.append({"group": group, "name": prefix + names[0], "aliases": [prefix + n for n in names[1:]],
                         "usage": usage, "what": what, "who": WHO[who], "cost": costs.get(kind, 0)})
    if settings.tts_enabled and settings.tts_command:
        commands.append({"group": "Text to speech", "name": prefix + settings.tts_command.lstrip(prefix),
                         "aliases": [], "usage": "message", "what": "Have your message read aloud",
                         "who": settings.tts_role if settings.tts_role in WHO.values() else "everyone", "cost": 0})
    for rule in parse_rules(settings.action_rules):
        if rule.enabled and rule.trigger.kind == "command" and rule.trigger.command:
            commands.append({"group": "More", "name": prefix + rule.trigger.command, "aliases": [], "usage": "",
                             "what": (rule.name or "")[:MAX_RULE_TEXT], "who": rule.trigger.role
                             if rule.trigger.role in WHO.values() else "everyone", "cost": 0})
    return commands
