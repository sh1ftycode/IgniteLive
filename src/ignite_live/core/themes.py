"""Themes: one set of colours that styles the dashboard and the overlays.

A theme is five colours (background, panels, accent, second accent, text), a corner radius and an optional overlay
font. The 21 built-in ones are read from the overlay stylesheet, so the overlays and the app can never disagree about
what "Synthwave Sunset" looks like. Your own themes are saved as plain data in the settings.

Plain data and arithmetic, no Qt: ``palette_for`` turns a theme into the full ``Palette`` the desktop app (and the web
dashboard) draw with, and ``overlay_css`` into the lines that make the overlays match.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from ignite_live.core.palettes import DARK, LIGHT, PALETTES, Palette

STYLESHEET = Path(__file__).resolve().parent.parent / "overlay" / "static" / "themes.css"
SEASONAL_KEYS = ("spring", "summer", "autumn", "winter", "halloween", "thanksgiving", "christmas")
MAX_CUSTOM = 50
MAX_RADIUS = 28
EXPORT_TAG = "ignite_theme"
CSS_START, CSS_END = "/* ignite-theme */", "/* /ignite-theme */"
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
FONTS = ("", "Inter", "Space Grotesk", "Quicksand", "Rajdhani", "Orbitron", "Audiowide", "Chakra Petch", "Fredoka",
         "Nunito", "Lora", "Cinzel", "VT323")  # The families the overlay stylesheet already loads; "" is the theme's own.
CONTRAST_TEXT, CONTRAST_ACCENT = 4.5, 3.0  # WCAG AA for text, and for large text and interface colours.


# Colour arithmetic.

def to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(ch * 2 for ch in color)
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def to_hex(rgb: tuple[float, float, float]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(v))) for v in rgb)


def mix(a: str, b: str, amount: float) -> str:
    """``a`` moved ``amount`` (0 to 1) of the way to ``b``."""
    ra, rb = to_rgb(a), to_rgb(b)
    return to_hex(tuple(x + (y - x) * amount for x, y in zip(ra, rb)))


def luminance(color: str) -> float:
    def channel(v: int) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in to_rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio of two colours, 1 (none) to 21 (black on white)."""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def readable(color: str, on: str, ratio: float, toward: str) -> str:
    """``color`` nudged toward ``toward`` until it reads at ``ratio`` on ``on`` (or as close as it gets)."""
    for step in range(0, 21):
        candidate = mix(color, toward, step / 20)
        if contrast(candidate, on) >= ratio:
            return candidate
    return toward


def is_dark(color: str) -> bool:
    return luminance(color) < 0.18


# The theme.

@dataclass(frozen=True)
class Theme:
    key: str  # Built-in: the overlay theme's name. Custom: its own id.
    name: str
    bg: str
    panel: str
    accent: str
    accent2: str
    text: str
    radius: int = 14
    font: str = ""
    base: str = ""  # A custom theme's built-in starting point: which overlay theme it is built on.
    builtin: bool = True
    seasonal: bool = False
    edited: float = 0.0

    @property
    def muted(self) -> str:
        return mix(self.text, self.panel, 0.42)

    @property
    def dark(self) -> bool:
        return is_dark(self.bg)

    @property
    def overlay_base(self) -> str:
        """The built-in overlay theme the overlays start from (the custom colours then go on top)."""
        return self.key if self.builtin else (self.base if self.base in builtin_themes() else "minimal")

    def checks(self) -> dict[str, tuple[float, bool]]:
        """What the editor shows: (ratio, passes) for text on panels and the accent on the background."""
        text = contrast(self.text, self.panel)
        accent = contrast(self.accent, self.bg)
        return {"text": (text, text >= CONTRAST_TEXT), "accent": (accent, accent >= CONTRAST_ACCENT)}


def _panel_for(bg: str) -> str:
    return mix(bg, "#ffffff", 0.07) if is_dark(bg) else mix(bg, "#000000", 0.05)


@lru_cache(maxsize=1)
def builtin_themes() -> dict[str, Theme]:
    """The 21 built-in themes, in the order the overlays list them."""
    from ignite_live.overlay.server import THEMES

    try:
        css = STYLESHEET.read_text("utf-8")
    except OSError:
        css = ""
    values: dict[str, dict[str, str]] = {}
    for key, block in re.findall(r':root(?:\[data-theme="([a-z0-9-]+)"\])?\s*\{([^}]*)\}', css):
        values.setdefault(key or "minimal", {}).update(dict(re.findall(r"--([a-z0-9-]+):\s*([^;]+);", block)))
    base = values.get("minimal", {})
    themes: dict[str, Theme] = {}
    for key, name in THEMES.items():
        v = {**base, **values.get(key, {})}
        bg = _opaque(v.get("surface", ""), "#12141c")
        radius = re.match(r"\d+", v.get("radius", "14"))
        themes[key] = Theme(
            key=key, name=name, bg=bg, panel=_panel_for(bg),
            accent=_hex(v.get("accent"), "#ff7a3d"), accent2=_hex(v.get("accent-2"), "#e8ebf7"),
            text=_hex(v.get("text"), "#e8ebf7"), radius=min(MAX_RADIUS, int(radius.group())) if radius else 14,
            seasonal=key in SEASONAL_KEYS)
    return themes


def _hex(value: str | None, fallback: str) -> str:
    return value.strip().lower() if value and HEX.match(value.strip()) else fallback


def _opaque(surface: str, backdrop: str) -> str:
    """A stylesheet surface colour as a solid one: a see-through one is laid over a dark backdrop."""
    surface = surface.strip()
    rgba = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?", surface)
    if rgba:
        r, g, b = (int(rgba.group(i)) for i in (1, 2, 3))
        alpha = float(rgba.group(4) or 1)
        return mix(backdrop, to_hex((r, g, b)), min(1.0, alpha))
    return _hex(surface, backdrop)


def theme_of(key: str, custom: list[dict] | None = None) -> Theme | None:
    """A theme by key: a built-in, or one of ``custom``."""
    builtin = builtin_themes()
    if key in builtin:
        return builtin[key]
    for item in custom or ():
        if item.get("id") == key:
            return from_dict(item)
    return None


# Your own themes.

def to_dict(theme: Theme) -> dict:
    return {"id": theme.key, "name": theme.name, "base": theme.base or theme.key if theme.builtin else theme.base,
            "bg": theme.bg, "panel": theme.panel, "accent": theme.accent, "accent2": theme.accent2, "text": theme.text,
            "radius": theme.radius, "font": theme.font, "edited": theme.edited}


def from_dict(item: dict) -> Theme:
    return Theme(key=item["id"], name=item["name"], bg=item["bg"], panel=item["panel"], accent=item["accent"],
                 accent2=item["accent2"], text=item["text"], radius=item["radius"], font=item.get("font", ""),
                 base=item.get("base", ""), builtin=False, edited=float(item.get("edited") or 0))


def clean_custom(raw: object) -> list[dict]:
    """Saved themes with anything unusable left out (a hand-edited file, an older version)."""
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        ident, name = item.get("id"), item.get("name")
        colors = {k: item.get(k) for k in ("bg", "panel", "accent", "accent2", "text")}
        if not (isinstance(ident, str) and re.fullmatch(r"[A-Za-z0-9-]{1,24}", ident) and ident not in seen
                and isinstance(name, str) and name.strip() and all(isinstance(v, str) and HEX.match(v)
                                                                  for v in colors.values())):
            continue
        radius = item.get("radius")
        font = item.get("font")
        out.append({"id": ident, "name": name.strip()[:40], "base": str(item.get("base") or "")[:24],
                    **{k: v.lower() for k, v in colors.items()},
                    "radius": max(0, min(MAX_RADIUS, radius)) if isinstance(radius, int) and not isinstance(radius, bool) else 12,
                    "font": font if font in FONTS else "",
                    "edited": float(item["edited"]) if isinstance(item.get("edited"), (int, float)) else 0.0})
        seen.add(ident)
        if len(out) >= MAX_CUSTOM:
            break
    return out


def new_id(existing: list[dict]) -> str:
    taken = {item.get("id") for item in existing}
    number = 1
    while f"t{number}" in taken:
        number += 1
    return f"t{number}"


def copy_of(source: Theme, name: str, existing: list[dict]) -> dict:
    """A new custom theme that starts as a copy of ``source``."""
    base = source.key if source.builtin else source.base
    return to_dict(replace(source, key=new_id(existing), name=name[:40], builtin=False, base=base,
                           seasonal=False, edited=time.time()))


def export_json(theme: Theme) -> str:
    data = to_dict(theme)
    data.pop("id", None)
    data.pop("edited", None)
    return json.dumps({EXPORT_TAG: 1, **data}, indent=2)


def import_json(text: str, existing: list[dict]) -> dict:
    """A theme file as a new custom theme. Raises ``ValueError`` (with something fit to show) if it isn't one."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError("That file isn't a theme.") from error
    if not isinstance(data, dict) or data.get(EXPORT_TAG) != 1:
        raise ValueError("That file isn't an Ignite theme.")
    cleaned = clean_custom([{**data, "id": new_id(existing), "edited": time.time()}])
    if not cleaned:
        raise ValueError("That theme file is missing colours, or they aren't valid.")
    return cleaned[0]


# From a theme to the app and the overlays.

def palette_for(theme: Theme) -> Palette:
    """The whole desktop palette for ``theme``. Live, success and warning colours stay as they are in Ignite's own
    dark and light looks: they have to mean the same thing whatever the theme."""
    if theme.builtin and theme.key in PALETTES:
        return PALETTES[theme.key]  # The seasons and holidays are hand-tuned already.
    dark = theme.dark
    base = DARK if dark else LIGHT
    toward = "#ffffff" if dark else "#000000"
    window, panel, text, accent = theme.bg, theme.panel, theme.text, theme.accent
    raised = mix(panel, toward, 0.07)
    muted = mix(text, panel, 0.42)
    on_accent = "#14110f" if contrast(accent, "#14110f") >= contrast(accent, "#ffffff") else "#ffffff"
    hover = mix(accent, "#ffffff", 0.14) if dark else mix(accent, "#000000", 0.10)
    pressed = mix(accent, "#000000", 0.14)
    return Palette(
        name=f"theme-{theme.key}", dark=dark, window=window, panel=panel, raised=raised,
        divider=mix(panel, toward, 0.13), text=text, text_muted=muted, text_disabled=mix(text, panel, 0.65),
        accent=accent, accent_hover=hover, accent_pressed=pressed,
        accent_text=readable(accent, panel, CONTRAST_TEXT, text), on_accent=on_accent,
        live=base.live, live_hover=base.live_hover, live_pressed=base.live_pressed, on_live=base.on_live,
        healthy=base.healthy, warning=base.warning, panel_alt=mix(window, panel, 0.5), selected=mix(panel, accent, 0.18),
        pressed=mix(raised, toward, 0.07), handle=mix(raised, text, 0.16),
        tiktok=base.tiktok, twitch=base.twitch, youtube=base.youtube,
        sidebar=mix(window, "#000000", 0.35) if dark else mix(window, "#000000", 0.05),
        primary=accent, primary_hover=hover, primary_pressed=pressed, on_primary=on_accent)


def register(theme: Theme) -> str:
    """Make ``theme``'s palette one the app can switch to; returns its name."""
    palette = palette_for(theme)
    PALETTES[palette.name] = palette
    return palette.name


def overlay_css(theme: Theme) -> str:
    """The lines that put a custom theme's colours on the overlays, on top of the built-in theme it started from."""
    r, g, b = to_rgb(theme.bg)
    line = mix(theme.text, theme.bg, 0.85)
    return (f"{CSS_START}\nhtml:root[data-theme] {{ --surface: rgba({r}, {g}, {b}, 0.88); --text: {theme.text}; "
            f"--muted: {theme.muted}; --line: {line}; --radius: {theme.radius}px; }}\n{CSS_END}")


def with_overlay_css(existing: str, theme: Theme | None) -> str:
    """``existing`` (the user's own overlay CSS) with the theme's block replaced, or taken out when ``theme`` is None.
    Whatever the user wrote outside the markers is kept."""
    block = re.compile(re.escape(CSS_START) + r".*?" + re.escape(CSS_END) + r"\n?", re.DOTALL)
    rest = block.sub("", existing).rstrip()
    if theme is None or theme.builtin:
        return rest
    return (rest + "\n" if rest else "") + overlay_css(theme)


def applied(settings, key: str):
    """``settings`` with the theme ``key`` (a built-in or one of the user's own) applied to the overlays. The
    dashboard follows through ``effective_ui_theme``. Returns the same settings if there is no such theme."""
    theme = theme_of(key, settings.custom_themes)
    if theme is None:
        return settings
    if theme.builtin:
        changes = {"overlay_theme": theme.key, "theme_active": ""}
        if settings.theme_active:  # Leaving a custom theme: take back what it put on the overlays, nothing else.
            changes.update(overlay_accent="", overlay_accent_2="", overlay_font="")
        changes["overlay_css"] = with_overlay_css(settings.overlay_css, None)
    else:
        changes = {"overlay_theme": theme.overlay_base, "theme_active": theme.key, "overlay_accent": theme.accent,
                   "overlay_accent_2": theme.accent2, "overlay_font": theme.font,
                   "overlay_css": with_overlay_css(settings.overlay_css, theme)}
    return replace(settings, **changes)


def effective_ui_theme(settings) -> str:
    """The palette name the dashboard should wear: the overlays' theme while they're matched, else the dark, light
    or seasonal look chosen in Settings. Registers the palette it names."""
    from ignite_live.core import seasons

    if not settings.theme_match_overlays:
        return settings.ui_theme
    if settings.theme_active:
        theme = theme_of(settings.theme_active, settings.custom_themes)
        if theme is not None:
            return register(theme)
    key = seasons.resolve(settings.overlay_theme)
    built = builtin_themes().get(key)
    if built is not None and key != "minimal":  # Minimal is Ignite's own look: the dark or light chosen in Settings.
        return register(built)
    return settings.ui_theme
