"""Trip state machine: IDLE → MOVING → (dwell | gap) → closed trip.

Pure with respect to time: every decision uses point timestamps only, never
the wall clock — live processing and archive replay are the same computation.
"""

from dataclasses import asdict, dataclass, field

from backend.engine.enrich import enrich
from backend.engine.geo import haversine_m
from backend.engine.geofence import Geofence, resolve_geofence
from backend.engine.hygiene import check
from backend.engine.params import EngineParams
from backend.engine.types import EnrichedPoint, Point, PointRejected, TripClosed


@dataclass
class EngineState:
    status: str = "idle"  # "idle" | "moving"
    prev: EnrichedPoint | None = None  # last accepted point
    geofence: str | None = None  # current fence membership
    recent: list[EnrichedPoint] = field(default_factory=list)  # idle window
    trip_points: list[EnrichedPoint] = field(default_factory=list)  # active trip

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "prev": asdict(self.prev) if self.prev else None,
            "geofence": self.geofence,
            "recent": [asdict(p) for p in self.recent],
            "trip_points": [asdict(p) for p in self.trip_points],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EngineState":
        return cls(
            status=d["status"],
            prev=EnrichedPoint(**d["prev"]) if d["prev"] else None,
            geofence=d["geofence"],
            recent=[EnrichedPoint(**p) for p in d["recent"]],
            trip_points=[EnrichedPoint(**p) for p in d["trip_points"]],
        )


class TripEngine:
    def __init__(self, params: EngineParams, geofences: list[Geofence]):
        self.params = params
        self.geofences = geofences
        self.state = EngineState()

    def process(self, point: Point) -> list[TripClosed | PointRejected]:
        s, p = self.state, self.params
        reason = check(s.prev, point, p)
        if reason is not None:
            return [PointRejected(point=point, reason=reason)]

        events: list[TripClosed | PointRejected] = []
        if s.status == "moving" and point.ts - s.prev.ts > p.gap_close_s:
            events.extend(self._close_trip(end_index=len(s.trip_points)))

        gf = resolve_geofence(self.geofences, point.lat, point.lon, s.geofence)
        ep = enrich(s.prev, point, gf)

        if s.status == "idle":
            s.recent.append(ep)
            if len(s.recent) > p.move_window:
                s.recent.pop(0)
            if self._movement_detected():
                s.status = "moving"
                s.trip_points = list(s.recent)
                s.recent = []
        else:
            s.trip_points.append(ep)
            events.extend(self._maybe_close_on_dwell())

        s.prev = ep
        s.geofence = gf
        return events

    def _movement_detected(self) -> bool:
        s, p = self.state, self.params
        if len(s.recent) < p.move_window:
            return False
        fast = sum(1 for ep in s.recent if ep.speed_mps >= p.move_speed_mps)
        if fast < p.move_min_points:
            return False
        first, last = s.recent[0], s.recent[-1]
        return haversine_m(first.lat, first.lon, last.lat, last.lon) >= (p.move_min_displacement_m)

    def _maybe_close_on_dwell(self) -> list[TripClosed]:
        return []  # implemented in the trip-close task

    def _close_trip(self, end_index: int) -> list[TripClosed]:
        self.state.status = "idle"
        self.state.recent = []
        self.state.trip_points = []
        return []  # full assembly implemented in the trip-close task
