"""Runs one viewer game at a time and keeps its settings. Pure logic, no I/O."""

from __future__ import annotations

import time
from typing import Callable

from ignite_live.core.events import ChatEvent
from ignite_live.games import boss, poll, tug
from ignite_live.games.base import GAMES, Game, GameEnded

_KINDS = {"tug": (tug.parse, tug.TugOfWar), "boss": (boss.parse, boss.BossFight), "poll": (poll.parse, poll.Poll)}


def parse_games(raw: object) -> dict:
    """``Settings.games``: every game's settings, cleaned."""
    raw = raw if isinstance(raw, dict) else {}
    return {kind: parse(raw.get(kind)) for kind, (parse, _game) in _KINDS.items()}


class GameManager:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.game: Game | None = None

    @property
    def running(self) -> str:
        return self.game.kind if self.game else ""

    def start(self, kind: str, config: dict, prefix: str = "!", seconds: int | None = None) -> GameEnded | None:
        """Start a game; returns the end of the one it replaces, if any. ``seconds`` overrides its timer."""
        if kind not in GAMES:
            raise ValueError(f"No game called {kind}")
        ended = self.stop()
        settings = _KINDS[kind][0](config)
        if seconds is not None:
            settings["seconds"] = seconds
        self.game = _KINDS[kind][1](settings, self._clock, prefix)
        return ended

    def stop(self) -> GameEnded | None:
        if self.game is None:
            return None
        ended, self.game = self.game.finish(), None
        return ended

    def feed(self, event: ChatEvent) -> tuple[bool, list[GameEnded]]:
        """(whether the overlay should update, results)"""
        if self.game is None:
            return False, []
        results = self.game.feed(event)
        changed = self.game.changed
        if any(result.final for result in results):
            self.game = None
            changed = True
        return changed or bool(results), results

    def tick(self) -> tuple[bool, GameEnded | None]:
        """Once a second: (whether a timer is showing, the end if it ran out)."""
        if self.game is None:
            return False, None
        ended = self.game.tick()
        if ended is not None:
            self.game = None
            return True, ended
        return self.game.remaining() is not None, None

    def snapshot(self) -> dict | None:
        return self.game.snapshot() if self.game else None
