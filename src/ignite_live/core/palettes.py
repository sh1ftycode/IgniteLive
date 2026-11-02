"""Ignite's colour palettes: Dark, Light, the seasons and the holidays.

Plain data, no Qt, so the desktop app (ui/theme.py) and the web dashboard use
exactly the same colours. Change a colour here and both follow.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Palette:
    name: str
    dark: bool
    window: str  # Window background: behind and between panels, menu bar, toolbar, status bar.
    panel: str  # Panels, docks, tab pages, dialogs.
    raised: str  # Inputs, normal buttons, hover.
    divider: str  # Thin dividers; also the hover of things that already sit on ``raised``.
    text: str  # Main text.
    text_muted: str  # Secondary text: labels, captions, notes.
    text_disabled: str  # Disabled controls and placeholders.
    accent: str  # Ember orange: the main action, selection, focus, progress.
    accent_hover: str
    accent_pressed: str
    accent_text: str  # Orange as text (links, gift counts): darker in light mode to stay readable.
    on_accent: str  # Text on orange.
    live: str  # Live and danger.
    live_hover: str
    live_pressed: str
    on_live: str
    healthy: str
    warning: str
    panel_alt: str  # Alternate table rows.
    selected: str  # Selected rows: a solid orange tint (item views would stack a translucent one).
    pressed: str  # A normal button while pressed.
    handle: str  # Scroll bar handles.
    tiktok: str  # Chat platform pills: coloured text on a faint tint of the same colour.
    twitch: str
    youtube: str


DARK = Palette(
    name="dark", dark=True,
    window="#0E1015", panel="#161920", raised="#1E222B", divider="#262B36",
    text="#E8EAF0", text_muted="#8B92A5", text_disabled="#646B7D",
    accent="#FF7A3D", accent_hover="#FF9461", accent_pressed="#F0692B", accent_text="#FF7A3D", on_accent="#0E1015",
    live="#F2425E", live_hover="#F45D75", live_pressed="#DE3550", on_live="#FFFFFF",
    healthy="#3DD68C", warning="#F5C451",
    panel_alt="#1A1D25", selected="#3B2925", pressed="#2E3441", handle="#262B36",
    tiktok="#25F4EE", twitch="#A970FF", youtube="#FF4E45",
)

LIGHT = Palette(
    name="light", dark=False,
    window="#E9ECF1", panel="#FFFFFF", raised="#F0F2F5", divider="#DCE0E7",
    text="#161A22", text_muted="#5E6577", text_disabled="#A1A7B4",
    accent="#FF7A3D", accent_hover="#FF9461", accent_pressed="#F0692B", accent_text="#C24E18", on_accent="#0E1015",
    live="#E0344F", live_hover="#E84D65", live_pressed="#C92B44", on_live="#FFFFFF",
    healthy="#17915A", warning="#A86B12",
    panel_alt="#F7F8FA", selected="#FFEDE4", pressed="#E3E6EC", handle="#C9CED8",
    tiktok="#0E8F8B", twitch="#7B3FE4", youtube="#D9302A",
)

# Holiday modes: dark palettes in seasonal colours. Same rules as Dark: one accent for the main action and
# selection, red only for live and danger, and text that stays readable.
CHRISTMAS = Palette(
    name="christmas", dark=True,
    window="#0B1611", panel="#10201A", raised="#172C24", divider="#22392F",
    text="#EEF4EF", text_muted="#93A99C", text_disabled="#5E7368",
    accent="#E8B54A", accent_hover="#F0C567", accent_pressed="#D9A53A", accent_text="#E8B54A", on_accent="#1A1405",
    live="#E5394B", live_hover="#EA5464", live_pressed="#CF2E40", on_live="#FFFFFF",
    healthy="#4CC38A", warning="#F28C38",
    panel_alt="#13251E", selected="#333822", pressed="#2A4237", handle="#2A4237",
    tiktok="#25F4EE", twitch="#A970FF", youtube="#FF4E45",
)

HALLOWEEN = Palette(
    name="halloween", dark=True,
    window="#0D0A12", panel="#15111C", raised="#1F1928", divider="#2C2338",
    text="#F1ECF7", text_muted="#A396B5", text_disabled="#6B5F7D",
    accent="#FF7A1A", accent_hover="#FF9447", accent_pressed="#EB6A0C", accent_text="#FF8A33", on_accent="#1A0D02",
    live="#E5394B", live_hover="#EA5464", live_pressed="#CF2E40", on_live="#FFFFFF",
    healthy="#7BD88F", warning="#F5D04C",
    panel_alt="#19141F", selected="#33204A", pressed="#33284A", handle="#3A2D4D",
    tiktok="#25F4EE", twitch="#B98CFF", youtube="#FF4E45",
)

THANKSGIVING = Palette(
    name="thanksgiving", dark=True,
    window="#140E0A", panel="#1C1510", raised="#271D16", divider="#35281E",
    text="#F6EEE4", text_muted="#B9A48F", text_disabled="#7A6656",
    accent="#E9963A", accent_hover="#F0AA5A", accent_pressed="#D6852B", accent_text="#EFA04A", on_accent="#1E1206",
    live="#E0473F", live_hover="#E6625B", live_pressed="#C93A33", on_live="#FFFFFF",
    healthy="#8DBF5A", warning="#F2C65A",
    panel_alt="#201812", selected="#3D2A17", pressed="#3A2C21", handle="#3A2C21",
    tiktok="#25F4EE", twitch="#A970FF", youtube="#FF4E45",
)

# Seasons: dark palettes too, same rules.
SPRING = Palette(
    name="spring", dark=True,
    window="#0E1512", panel="#141D19", raised="#1C2722", divider="#27352E",
    text="#EEF5F0", text_muted="#95AA9E", text_disabled="#627468",
    accent="#F28DB2", accent_hover="#F5A6C3", accent_pressed="#E57AA1", accent_text="#F49CBC", on_accent="#2A0F1A",
    live="#E5394B", live_hover="#EA5464", live_pressed="#CF2E40", on_live="#FFFFFF",
    healthy="#6CD49A", warning="#F5C451",
    panel_alt="#18221D", selected="#372F31", pressed="#2E3D35", handle="#2E3D35",
    tiktok="#25F4EE", twitch="#A970FF", youtube="#FF4E45",
)

SUMMER = Palette(
    name="summer", dark=True,
    window="#0B1420", panel="#101C2A", raised="#172636", divider="#213245",
    text="#EEF4FA", text_muted="#93A7BC", text_disabled="#5F7389",
    accent="#2EC4B6", accent_hover="#4FD3C7", accent_pressed="#22AFA2", accent_text="#3CCFC1", on_accent="#04201D",
    live="#F2425E", live_hover="#F45D75", live_pressed="#DE3550", on_live="#FFFFFF",
    healthy="#8BD66B", warning="#FFC145",
    panel_alt="#13202F", selected="#15373F", pressed="#22364B", handle="#2A3F55",
    tiktok="#25F4EE", twitch="#A970FF", youtube="#FF4E45",
)

AUTUMN = Palette(
    name="autumn", dark=True,
    window="#12120C", panel="#191A12", raised="#222419", divider="#2F3123",
    text="#F3F1E6", text_muted="#ABA88F", text_disabled="#6F6C58",
    accent="#E3B341", accent_hover="#EBC261", accent_pressed="#D4A332", accent_text="#E8BB52", on_accent="#1C1504",
    live="#E0473F", live_hover="#E6625B", live_pressed="#C93A33", on_live="#FFFFFF",
    healthy="#8FBF5A", warning="#F08A3C",
    panel_alt="#1D1E15", selected="#393219", pressed="#34372A", handle="#34372A",
    tiktok="#25F4EE", twitch="#A970FF", youtube="#FF4E45",
)

WINTER = Palette(
    name="winter", dark=True,
    window="#0C121A", panel="#121A24", raised="#19232F", divider="#24303E",
    text="#EEF3F8", text_muted="#97A6B8", text_disabled="#627184",
    accent="#7CC4FF", accent_hover="#9AD2FF", accent_pressed="#62B5F5", accent_text="#8ACBFF", on_accent="#06121F",
    live="#F2425E", live_hover="#F45D75", live_pressed="#DE3550", on_live="#FFFFFF",
    healthy="#5FD3A0", warning="#F5C451",
    panel_alt="#151E29", selected="#233547", pressed="#2A3747", handle="#2C3A4C",
    tiktok="#25F4EE", twitch="#A970FF", youtube="#FF4E45",
)

PALETTES = {"dark": DARK, "light": LIGHT, "spring": SPRING, "summer": SUMMER, "autumn": AUTUMN, "winter": WINTER,
            "halloween": HALLOWEEN, "thanksgiving": THANKSGIVING, "christmas": CHRISTMAS}


def css_variables(palette: Palette) -> str:
    """The palette as CSS custom properties (``--panel: #161920;`` …), for the web dashboard."""
    return " ".join(f"--{name.replace('_', '-')}: {value};" for name, value in asdict(palette).items()
                    if isinstance(value, str) and value.startswith("#"))
