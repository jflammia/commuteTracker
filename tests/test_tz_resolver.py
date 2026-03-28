"""Tests for GPS-based timezone resolution."""

from unittest.mock import patch

from src.processing.tz_resolver import resolve_timezone, resolve_timezones


def test_resolve_known_location_nyc():
    """NYC coordinates should resolve to America/New_York."""
    tz = resolve_timezone(40.7128, -74.0060)
    assert tz == "America/New_York"


def test_resolve_known_location_london():
    """London coordinates should resolve to Europe/London."""
    tz = resolve_timezone(51.5074, -0.1278)
    assert tz == "Europe/London"


def test_resolve_known_location_tokyo():
    """Tokyo coordinates should resolve to Asia/Tokyo."""
    tz = resolve_timezone(35.6762, 139.6503)
    assert tz == "Asia/Tokyo"


def test_resolve_fallback_on_none():
    """When timezonefinder returns None, fall back to TIMEZONE config."""
    with patch("src.processing.tz_resolver._finder") as mock_finder:
        mock_finder.return_value.timezone_at.return_value = None
        tz = resolve_timezone(0.0, 0.0)
    from src.config import TIMEZONE

    assert tz == TIMEZONE


def test_resolve_timezones_batch():
    """Batch resolution should return a list of timezone strings."""
    lats = [40.7128, 51.5074, 35.6762]
    lons = [-74.0060, -0.1278, 139.6503]
    result = resolve_timezones(lats, lons)
    assert len(result) == 3
    assert result[0] == "America/New_York"
    assert result[1] == "Europe/London"
    assert result[2] == "Asia/Tokyo"


def test_resolve_timezones_empty():
    """Empty input should return empty list."""
    assert resolve_timezones([], []) == []


def test_finder_instance_reused():
    """The TimezoneFinder instance should be cached (not recreated each call)."""
    from src.processing.tz_resolver import _finder

    f1 = _finder()
    f2 = _finder()
    assert f1 is f2
