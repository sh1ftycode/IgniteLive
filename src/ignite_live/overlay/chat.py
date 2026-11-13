"""Turn chat events into the small, sanitised payloads the chat and alerts overlays show."""

from __future__ import annotations

from ignite_live.core.events import ChatComment, ChatEvent, ChatFollow, ChatGift, ChatJoin, ChatUser
from ignite_live.core.images import allowed_image_url


def _user(user: ChatUser) -> dict:
    badges = (["HOST"] if user.is_host else []) + (["MOD"] if user.is_moderator else [])
    if user.is_subscriber:
        badges.append("MEMBER" if user.platform == "youtube" else "SUB")
    return {
        "name": (user.nickname or user.unique_id)[:40],
        "avatar": user.avatar_url if allowed_image_url(user.avatar_url) else "",
        "badges": badges,
        "platform": user.platform,
        "platform_name": user.platform_name,
    }


def chat_payload(event: ChatEvent) -> dict | None:
    if isinstance(event, ChatComment):
        return {"kind": "comment", "text": event.text[:300], **_user(event.user)}
    if isinstance(event, ChatGift):
        return {
            "kind": "gift",
            "gift": event.gift_name[:60],
            "count": event.count,
            "diamonds": event.diamonds * event.count,
            "amount": event.amount[:20],
            "message": event.message[:300],
            "image": event.image_url if allowed_image_url(event.image_url) else "",
            # A combo tick: the chat overlay floods it and fades it fast; the alerts overlay waits for the total.
            "streaking": event.streaking,
            **_user(event.user),
        }
    if isinstance(event, ChatFollow):
        return {"kind": "follow", **_user(event.user)}
    if isinstance(event, ChatJoin) and not event.listed:
        return {"kind": "join", **_user(event.user)}
    return None
