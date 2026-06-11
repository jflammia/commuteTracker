"""Point acceptance checks. Rejection never touches raw data — the caller
records the rejection in derived data and moves on."""

from backend.engine.geo import haversine_m
from backend.engine.params import EngineParams
from backend.engine.types import EnrichedPoint, Point


def check(prev: EnrichedPoint | None, point: Point, params: EngineParams) -> str | None:
    """Return a rejection reason, or None if the point is acceptable."""
    if point.accuracy_m is not None and point.accuracy_m > params.accuracy_max_m:
        return "accuracy"
    if prev is not None:
        if point.ts <= prev.ts:
            return "out_of_order"
        dt = point.ts - prev.ts
        if haversine_m(prev.lat, prev.lon, point.lat, point.lon) / dt > params.teleport_speed_mps:
            return "teleport"
    return None
