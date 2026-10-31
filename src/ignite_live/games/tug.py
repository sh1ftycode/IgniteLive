"""Tug-of-war: two teams pull a rope with gifts (and optionally likes) until one gets it to their side."""

from __future__ import annotations

from ignite_live.core.events import ChatComment, ChatEvent, ChatGift, ChatLike
from ignite_live.games.base import Game, GameEnded, color, names, number, text

DEFAULTS = {"left_name": "Red", "left_color": "#e5484d", "right_name": "Blue", "right_color": "#3fa7f5",
            "mode": "gifts", "left_gifts": ["Rose"], "right_gifts": ["TikTok"], "like_value": 0, "target": 1000,
            "seconds": 180}


def parse(raw: object) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "left_name": text(raw, "left_name", "Red", 20),
        "left_color": color(raw, "left_color", "#e5484d"),
        "right_name": text(raw, "right_name", "Blue", 20),
        "right_color": color(raw, "right_color", "#3fa7f5"),
        # gifts: each team has its own gifts; join: viewers type !join <team> and any gift pulls for their team.
        "mode": raw.get("mode") if raw.get("mode") in ("gifts", "join") else "gifts",
        "left_gifts": names(raw, "left_gifts") if "left_gifts" in raw else ["Rose"],
        "right_gifts": names(raw, "right_gifts") if "right_gifts" in raw else ["TikTok"],
        "like_value": number(raw, "like_value", 0, 0, 1000),  # This many likes pull 1; 0: likes don't pull.
        "target": number(raw, "target", 1000, 10, 100_000_000),  # Lead needed to win.
        "seconds": number(raw, "seconds", 180, 0, 3600),  # 0: no timer.
    }


class TugOfWar(Game):
    kind = "tug"

    def __init__(self, config: dict, clock, prefix: str = "!") -> None:
        super().__init__(config, clock, prefix)
        self.score = {"left": 0, "right": 0}
        self.teams: dict[str, str] = {}  # user key -> side (join mode)
        self._likes: dict[str, int] = {}  # user key -> likes not yet worth a pull
        self.gifts = {side: {g.lower() for g in config[f"{side}_gifts"]} for side in ("left", "right")}

    def _side_named(self, word: str) -> str | None:
        word = word.strip().lower()
        for side, number_word in (("left", "1"), ("right", "2")):
            if word in (self.config[f"{side}_name"].lower(), number_word, side):
                return side
        return None

    def feed(self, event: ChatEvent) -> list[GameEnded]:
        self.changed = False
        user = getattr(event, "user", None)
        if user is None:
            return []
        c = self.config
        side, pull = None, 0
        if isinstance(event, ChatComment):
            word, _, rest = event.text.strip().partition(" ")
            if c["mode"] == "join" and word.lower() == f"{self.prefix}join":
                side = self._side_named(rest)
                if side:
                    self.teams[user.key] = side
                    self.users[user.key] = user
                    self.changed = True
            return []
        if isinstance(event, ChatGift):
            if c["mode"] == "gifts":
                gift = event.gift_name.lower()
                side = next((s for s, gifts in self.gifts.items() if gift in gifts), None)
            else:
                side = self.teams.get(user.key)
            pull = max(1, event.diamonds) * event.count
        elif isinstance(event, ChatLike) and c["like_value"]:
            side = self.teams.get(user.key) if c["mode"] == "join" else None
            if side is None and c["mode"] == "gifts":
                return []  # In gifts mode, likes can't say which team they're for.
            total = self._likes.get(user.key, 0) + event.count
            pull, self._likes[user.key] = divmod(total, c["like_value"])
        if side is None or pull <= 0:
            return []
        self.score[side] += pull
        self.credit(user, pull)
        self.changed = True
        if abs(self.score["left"] - self.score["right"]) >= c["target"]:
            return [self.finish()]
        return []

    def finish(self) -> GameEnded:
        left, right = self.score["left"], self.score["right"]
        winner = self.config["left_name"] if left > right else self.config["right_name"] if right > left else "Nobody"
        mvp, amount = self.mvp()
        return GameEnded(self.kind, winner, mvp, amount)

    def snapshot(self) -> dict:
        c = self.config
        lead = self.score["right"] - self.score["left"]  # Negative while the left team is ahead.
        return {
            "kind": self.kind,
            "left": {"name": c["left_name"], "color": c["left_color"], "score": self.score["left"],
                     "how": self._how("left")},
            "right": {"name": c["right_name"], "color": c["right_color"], "score": self.score["right"],
                      "how": self._how("right")},
            "position": max(-1.0, min(1.0, lead / c["target"])),  # -1: left wins … 1: right wins.
            "remaining": self.remaining(),
            "top": self.top(),
        }

    def _how(self, side: str) -> str:
        if self.config["mode"] == "join":
            return f"{self.prefix}join {self.config[f'{side}_name'].lower()}"
        return ", ".join(self.config[f"{side}_gifts"]) or "no gifts set"
