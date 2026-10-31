"""Which image URLs from chat payloads may be loaded (avatars, gift icons).

Chat comes from strangers, so only the platforms' own CDNs over HTTPS are allowed
(TikTok, YouTube avatars and gift images).
"""

from __future__ import annotations

from urllib.parse import urlsplit

IMAGE_HOSTS = (
    "tiktokcdn.com", "tiktokcdn-us.com", "tiktokcdn-eu.com", "ibyteimg.com", "byteimg.com",  # TikTok
    "ggpht.com", "ytimg.com", "www.gstatic.com",  # YouTube
)


def allowed_image_url(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in IMAGE_HOSTS)
