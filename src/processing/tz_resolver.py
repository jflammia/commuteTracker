"""Resolve IANA timezone from GPS coordinates.

Uses timezonefinder to map (lat, lon) to timezone strings like
"America/New_York". Falls back to TIMEZONE config when resolution
fails (ocean, null coordinates).
"""

from functools import lru_cache

from timezonefinder import TimezoneFinder

from src.config import TIMEZONE


@lru_cache(maxsize=1)
def _finder() -> TimezoneFinder:
    """Cached TimezoneFinder instance (~50ms to create, cheap to query)."""
    return TimezoneFinder()


def resolve_timezone(lat: float, lon: float) -> str:
    """Resolve a single coordinate pair to an IANA timezone string."""
    result = _finder().timezone_at(lng=lon, lat=lat)
    return result or TIMEZONE


def resolve_timezones(lats: list[float], lons: list[float]) -> list[str]:
    """Resolve a batch of coordinate pairs to timezone strings."""
    finder = _finder()
    return [finder.timezone_at(lng=lon, lat=lat) or TIMEZONE for lat, lon in zip(lats, lons)]
