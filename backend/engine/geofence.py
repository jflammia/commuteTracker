"""Geofence membership with hysteresis: enter at radius, exit at 1.5x radius.

Hysteresis prevents boundary flapping when GPS jitter straddles the fence."""

from dataclasses import dataclass

from backend.config import Settings
from backend.engine.geo import haversine_m

_EXIT_FACTOR = 1.5


@dataclass(frozen=True)
class Geofence:
    name: str
    lat: float
    lon: float
    radius_m: float


def resolve_geofence(
    geofences: list[Geofence], lat: float, lon: float, current: str | None
) -> str | None:
    """Resolve membership for a position given the previous membership."""
    if current is not None:
        cf = next((g for g in geofences if g.name == current), None)
        if cf is not None:
            if haversine_m(lat, lon, cf.lat, cf.lon) <= cf.radius_m * _EXIT_FACTOR:
                return current
    for g in geofences:
        if haversine_m(lat, lon, g.lat, g.lon) <= g.radius_m:
            return g.name
    return None


def geofences_from_settings(settings: Settings) -> list[Geofence]:
    """Build home/work fences from settings; (0,0) coordinates mean unset."""
    out = []
    if (settings.home_lat, settings.home_lon) != (0.0, 0.0):
        out.append(Geofence("home", settings.home_lat, settings.home_lon, settings.home_radius_m))
    if (settings.work_lat, settings.work_lon) != (0.0, 0.0):
        out.append(Geofence("work", settings.work_lat, settings.work_lon, settings.work_radius_m))
    return out
