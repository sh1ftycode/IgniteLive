"""The "Seasonal" choice: which seasonal or holiday theme fits today.

Used by both the app (Preferences → Appearance → Mode) and the overlays
(Overlays tab → Overlay theme), which share these names. Holidays win over the
season they fall in:

    Halloween      October 15–31
    Thanksgiving   November 1 to Thanksgiving Day (the fourth Thursday of November, US)
    Christmas      the day after Thanksgiving to December 26
    Spring         March–May      Summer   June–August
    Autumn         September–November      Winter   December–February

Seasons follow the northern hemisphere's calendar.
"""

from __future__ import annotations

from datetime import date, timedelta

SEASONAL = "seasonal"
SEASONAL_LABEL = "Seasonal (follows the calendar)"
SEASONS = ("spring", "summer", "autumn", "winter")
HOLIDAYS = ("halloween", "thanksgiving", "christmas")
_BY_MONTH = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
             6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}


def thanksgiving(year: int) -> date:
    """US Thanksgiving: the fourth Thursday of November."""
    first = date(year, 11, 1)
    return first + timedelta(days=(3 - first.weekday()) % 7 + 21)


def theme_for(day: date) -> str:
    year = day.year
    if date(year, 10, 15) <= day <= date(year, 10, 31):
        return "halloween"
    turkey = thanksgiving(year)
    if date(year, 11, 1) <= day <= turkey:
        return "thanksgiving"
    if turkey < day <= date(year, 12, 26):
        return "christmas"
    return _BY_MONTH[day.month]


def resolve(name: str, today: date | None = None) -> str:
    """``name``, or for "seasonal" the theme that fits today."""
    return theme_for(today or date.today()) if name == SEASONAL else name
