"""Geographic utilities: haversine distance, geofence checks, speed computation."""

import math


EARTH_RADIUS_M = 6_371_000


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in meters between two WGS-84 points."""
    lat1, lon1, lat2, lon2 = (math.radians(v) for v in (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def speed_kmh(distance_m: float, duration_s: float) -> float:
    """Compute speed in km/h from distance (meters) and duration (seconds)."""
    if duration_s <= 0:
        return 0.0
    return (distance_m / duration_s) * 3.6


def in_geofence(lat: float, lon: float, center_lat: float, center_lon: float, radius_m: float) -> bool:
    """Return True if the point is within radius_m of the center."""
    return haversine_m(lat, lon, center_lat, center_lon) <= radius_m
