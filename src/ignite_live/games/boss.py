"""Boss fight: likes and gifts deal damage; each defeated boss comes back stronger."""

from __future__ import annotations

from ignite_live.core.events import ChatComment, ChatEvent, ChatGift, ChatLike
from ignite_live.core.images import allowed_image_url
from ignite_live.games.base import IMAGE_NAME, Game, GameEnded, number, text


def parse(raw: object) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    image = raw.get("image")
    return {
        "name": text(raw, "name", "The Boss", 30),
        # A file in <config>/images, or a TikTok image link; empty: a plain monster.
        "image": image if isinstance(image, str) and (IMAGE_NAME.match(image) or allowed_image_url(image)) else "",
        "hp": number(raw, "hp", 1000, 10, 100_000_000),
        "growth": number(raw, "growth", 50, 0, 1000),  # % more HP for each next boss.
        "like_damage": number(raw, "like_damage", 1, 0, 1000),
        "coin_damage": number(raw, "coin_damage", 10, 0, 100_000),
        "comment_damage": number(raw, "comment_damage", 0, 0, 1000),
        "seconds": number(raw, "seconds", 0, 0, 3600),  # 0: until you stop it.
    }


class BossFight(Game):
    kind = "boss"

    def __init__(self, config: dict, clock, prefix: str = "!") -> None:
        super().__init__(config, clock, prefix)
        self.level = 1
        self.max_hp = config["hp"]
        self.hp = self.max_hp
        self.defeated = 0
        self.last_hit = ""

    def feed(self, event: ChatEvent) -> list[GameEnded]:
        self.changed = False
        user = getattr(event, "user", None)
        c = self.config
        if user is None:
            return []
        if isinstance(event, ChatLike):
            damage = event.count * c["like_damage"]
        elif isinstance(event, ChatGift):
            damage = max(1, event.diamonds) * event.count * c["coin_damage"]
        elif isinstance(event, ChatComment):
            damage = c["comment_damage"]
        else:
            return []
        if damage <= 0:
            return []
        self.hp -= damage
        self.credit(user, damage)
        self.last_hit = f"{user.nickname or user.unique_id} −{damage:,}"
        self.changed = True
        if self.hp > 0:
            return []
        # Defeated: chat wins this round; a stronger boss appears.
        mvp, amount = self.mvp()
        result = GameEnded(self.kind, "Chat", mvp, amount, final=False)
        self.defeated += 1
        self.level += 1
        self.max_hp = int(self.max_hp * (100 + c["growth"]) / 100)
        self.hp = self.max_hp
        self.contributions.clear()
        return [result]

    def finish(self) -> GameEnded:
        # Stopped with a boss still standing: it got away. Each defeated boss already counted as chat's win.
        mvp, amount = self.mvp()
        return GameEnded(self.kind, self.config["name"], mvp, amount)

    def snapshot(self) -> dict:
        return {
            "kind": self.kind,
            "name": self.config["name"],
            "image": self.config["image"],
            "level": self.level,
            "hp": max(0, self.hp),
            "max_hp": self.max_hp,
            "defeated": self.defeated,
            "last_hit": self.last_hit,
            "remaining": self.remaining(),
            "top": self.top(),
        }
