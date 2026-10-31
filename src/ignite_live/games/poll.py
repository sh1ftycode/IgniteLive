"""Chat poll: viewers vote by typing a number, !vote <number>, or the option itself. One vote each."""

from __future__ import annotations

from collections import Counter

from ignite_live.core.events import ChatComment, ChatEvent
from ignite_live.games.base import Game, GameEnded, names, number, text


def parse(raw: object) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    options = names(raw, "options", 6) if "options" in raw else ["Yes", "No"]
    return {
        "question": text(raw, "question", "What should I do next?", 120),
        "options": options if len(options) >= 2 else ["Yes", "No"],
        "seconds": number(raw, "seconds", 60, 0, 3600),
    }


class Poll(Game):
    kind = "poll"

    def __init__(self, config: dict, clock, prefix: str = "!") -> None:
        super().__init__(config, clock, prefix)
        self.votes: dict[str, int] = {}  # user key -> option index (a later vote replaces it)
        self._lookup = {option.lower(): i for i, option in enumerate(config["options"])}

    def _choice(self, message: str) -> int | None:
        message = message.strip().lower()
        vote_word = f"{self.prefix}vote"
        if message.startswith(vote_word + " "):
            message = message[len(vote_word):].strip()
        if message.isdigit() and 1 <= int(message) <= len(self.config["options"]):
            return int(message) - 1
        return self._lookup.get(message)

    def feed(self, event: ChatEvent) -> list[GameEnded]:
        self.changed = False
        if not isinstance(event, ChatComment):
            return []
        choice = self._choice(event.text)
        if choice is None or self.votes.get(event.user.key) == choice:
            return []
        self.votes[event.user.key] = choice
        self.users[event.user.key] = event.user
        self.changed = True
        return []

    def counts(self) -> list[int]:
        tally = Counter(self.votes.values())
        return [tally.get(i, 0) for i in range(len(self.config["options"]))]

    def finish(self) -> GameEnded:
        counts = self.counts()
        best = max(counts)
        leaders = [option for option, count in zip(self.config["options"], counts) if count == best]
        if not best:
            winner = "Nobody"
        elif len(leaders) > 1:
            winner = "Tie: " + " / ".join(leaders)
        else:
            winner = leaders[0]
        return GameEnded(self.kind, winner, None, best)

    def snapshot(self) -> dict:
        counts = self.counts()
        total = sum(counts)
        return {
            "kind": self.kind,
            "question": self.config["question"],
            "options": [{"label": option, "votes": count, "percent": round(100 * count / total, 1) if total else 0}
                        for option, count in zip(self.config["options"], counts)],
            "total": total,
            "remaining": self.remaining(),
            "how": f"Type 1–{len(counts)} or {self.prefix}vote 1",
        }
