"""Deterministic synthetic GPS tracks for engine tests.

All tracks move due north from a start coordinate; 1 degree latitude is
~111,120 m so dlat = meters / 111120. No randomness — replay-stable."""

from backend.engine.types import Point

M_PER_DEG_LAT = 111120.0


def leg(
    t0: float,
    lat0: float,
    lon: float,
    speed_mps: float,
    duration_s: float,
    interval_s: float = 30.0,
    accuracy_m: float = 10.0,
) -> list[Point]:
    """Points moving due north at constant speed. Includes t0, excludes t0+duration."""
    pts = []
    t = t0
    lat = lat0
    while t < t0 + duration_s:
        pts.append(Point(ts=t, lat=lat, lon=lon, accuracy_m=accuracy_m))
        t += interval_s
        lat += speed_mps * interval_s / M_PER_DEG_LAT
    return pts


def dwell(
    t0: float,
    lat: float,
    lon: float,
    duration_s: float,
    interval_s: float = 30.0,
    accuracy_m: float = 10.0,
) -> list[Point]:
    """Stationary points at one location."""
    pts = []
    t = t0
    while t < t0 + duration_s:
        pts.append(Point(ts=t, lat=lat, lon=lon, accuracy_m=accuracy_m))
        t += interval_s
    return pts


def end_of(points: list[Point]) -> tuple[float, float]:
    """(next_ts, final_lat) to chain legs."""
    last = points[-1]
    return last.ts + 30.0, last.lat


def commute(t0: float = 1_781_100_000.0, lat0: float = 40.7000, lon: float = -74.4000):
    """walk 5 min → vehicle 15 min @20 m/s → walk 5 min → dwell 10 min.

    Returns (points, lat0, final_moving_lat). Total moving distance ≈ 19 km.
    """
    pts = leg(t0, lat0, lon, speed_mps=1.5, duration_s=300)
    t, lat = end_of(pts)
    pts += leg(t, lat, lon, speed_mps=20.0, duration_s=900)
    t, lat = end_of(pts)
    pts += leg(t, lat, lon, speed_mps=1.5, duration_s=300)
    t, lat = end_of(pts)
    pts += dwell(t, lat, lon, duration_s=600)
    return pts, lat0, lat
