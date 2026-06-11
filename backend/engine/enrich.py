"""Per-point kinematics from the previous accepted point."""

from backend.engine.geo import bearing_deg, haversine_m
from backend.engine.types import EnrichedPoint, Point


def enrich(prev: EnrichedPoint | None, point: Point, geofence: str | None) -> EnrichedPoint:
    if prev is None:
        return EnrichedPoint(
            ts=point.ts,
            lat=point.lat,
            lon=point.lon,
            accuracy_m=point.accuracy_m,
            speed_mps=0.0,
            heading_deg=None,
            distance_m=0.0,
            geofence=geofence,
        )
    distance = haversine_m(prev.lat, prev.lon, point.lat, point.lon)
    dt = point.ts - prev.ts  # hygiene guarantees dt > 0
    return EnrichedPoint(
        ts=point.ts,
        lat=point.lat,
        lon=point.lon,
        accuracy_m=point.accuracy_m,
        speed_mps=distance / dt,
        heading_deg=bearing_deg(prev.lat, prev.lon, point.lat, point.lon),
        distance_m=distance,
        geofence=geofence,
    )
