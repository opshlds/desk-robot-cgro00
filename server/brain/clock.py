"""The time somewhere else, worked out properly.

Language models are bad at clock arithmetic (Rocky once got "10 hours ahead"
right and then added 11). The `time_in` ability does the sums here and hands
him a finished sentence to read from.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from . import config

# A few names people use that aren't a city in the zone database.
ALIASES = {
    "utc": "UTC", "gmt": "Etc/GMT", "zulu": "UTC",
    "bangladesh": "Asia/Dhaka", "india": "Asia/Kolkata", "nepal": "Asia/Kathmandu",
    "japan": "Asia/Tokyo", "china": "Asia/Shanghai", "korea": "Asia/Seoul", "south korea": "Asia/Seoul",
    "philippines": "Asia/Manila", "vietnam": "Asia/Ho_Chi_Minh", "thailand": "Asia/Bangkok",
    "singapore": "Asia/Singapore", "pakistan": "Asia/Karachi", "israel": "Asia/Jerusalem",
    "uae": "Asia/Dubai", "saudi arabia": "Asia/Riyadh", "turkey": "Europe/Istanbul",
    "uk": "Europe/London", "england": "Europe/London", "united kingdom": "Europe/London",
    "ireland": "Europe/Dublin", "france": "Europe/Paris", "germany": "Europe/Berlin",
    "spain": "Europe/Madrid", "italy": "Europe/Rome", "netherlands": "Europe/Amsterdam",
    "poland": "Europe/Warsaw", "ukraine": "Europe/Kyiv", "greece": "Europe/Athens",
    "egypt": "Africa/Cairo", "nigeria": "Africa/Lagos", "kenya": "Africa/Nairobi",
    "south africa": "Africa/Johannesburg", "new zealand": "Pacific/Auckland",
    "hawaii": "Pacific/Honolulu", "alaska": "America/Anchorage",
    "eastern": "America/New_York", "central": "America/Chicago",
    "mountain": "America/Denver", "pacific": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles", "la": "America/Los_Angeles", "san francisco": "America/Los_Angeles",
    "seattle": "America/Los_Angeles", "washington dc": "America/New_York", "boston": "America/New_York",
    "miami": "America/New_York", "atlanta": "America/New_York", "houston": "America/Chicago",
    "dallas": "America/Chicago", "beijing": "Asia/Shanghai", "mumbai": "Asia/Kolkata",
    "delhi": "Asia/Kolkata", "new delhi": "Asia/Kolkata", "bangalore": "Asia/Kolkata",
    "sydney": "Australia/Sydney", "melbourne": "Australia/Melbourne",
}


def _key(text: str) -> str:
    return re.sub(r"[\s_\-]+", " ", text.strip().lower())


@lru_cache(maxsize=1)
def _cities() -> dict[str, str]:
    """'dhaka' -> 'Asia/Dhaka', 'new york' -> 'America/New_York', ... from the zone database."""
    out: dict[str, str] = {}
    for name in sorted(available_timezones()):
        if "/" in name and not name.startswith(("Etc/", "SystemV/", "posix/", "right/")):
            out.setdefault(_key(name.rsplit("/", 1)[-1]), name)
    return out


def resolve(place: str) -> ZoneInfo | None:
    """A time zone from an IANA name ("Asia/Dhaka"), a city in the zone
    database ("Dhaka", "new york") or a common alias ("Bangladesh", "UK")."""
    place = (place or "").strip()
    if not place:
        return None
    try:
        return ZoneInfo(place)
    except (ZoneInfoNotFoundError, ValueError):
        pass
    k = _key(place)
    name = ALIASES.get(k) or _cities().get(k)
    if name is None:
        # "Dhaka, Bangladesh" / "Tokyo Japan": try each piece.
        for piece in re.split(r"[,/]", k):
            piece = piece.strip()
            name = ALIASES.get(piece) or _cities().get(piece)
            if name:
                break
    return ZoneInfo(name) if name else None


def _offset(td: timedelta | None) -> str:
    minutes = int((td or timedelta()).total_seconds() // 60)
    if minutes == 0:
        return "UTC"
    sign = "+" if minutes > 0 else "-"
    h, m = divmod(abs(minutes), 60)
    return f"UTC{sign}{h}" + (f":{m:02d}" if m else "")


def _span(minutes: int) -> str:
    h, m = divmod(abs(minutes), 60)
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h != 1 else ''}")
    if m:
        parts.append(f"{m} minutes")
    return " ".join(parts)


def _clock(dt: datetime) -> str:
    return f"{dt:%A, %B} {dt.day}, {dt.year}, {dt.hour % 12 or 12}:{dt:%M} {'AM' if dt.hour < 12 else 'PM'}"


def time_in(place: str, now: datetime | None = None) -> str:
    """One sentence for Rocky: the time there, and how it compares with home."""
    zone = resolve(place)
    if zone is None:
        return (f"Unknown place '{place}'. Call time_in again with an IANA time zone name "
                f"such as Asia/Dhaka or Europe/London.")
    try:
        home = ZoneInfo(config.TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        home = datetime.now().astimezone().tzinfo
    now = now or datetime.now(home)
    there, here = now.astimezone(zone), now.astimezone(home)
    diff = int(((there.utcoffset() or timedelta()) - (here.utcoffset() or timedelta())).total_seconds() // 60)
    if diff == 0:
        compare = "the same as your human's"
    else:
        compare = f"{_span(diff)} {'ahead of' if diff > 0 else 'behind'} your human's"
    day = ""
    if there.date() != here.date():
        day = " (already tomorrow there)" if there.date() > here.date() else " (still yesterday there)"
    return (f"In {zone.key} it is {_clock(there)} ({_offset(there.utcoffset())}){day}. "
            f"That is {compare} time ({_clock(here)}, {_offset(here.utcoffset())}). "
            f"Say this; don't recalculate it.")
