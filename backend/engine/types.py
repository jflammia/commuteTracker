"""Engine data types. Timestamps are epoch seconds (UTC) from OwnTracks tst."""

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Point:
    ts: float
    lat: float
    lon: float
    accuracy_m: float | None = None

    @classmethod
    def from_owntracks(cls, payload: dict) -> "Point | None":
        """Parse an OwnTracks payload; None for non-location or malformed."""
        if not isinstance(payload, dict) or payload.get("_type") != "location":
            return None
        try:
            acc = payload.get("acc")
            return cls(
                ts=float(payload["tst"]),
                lat=float(payload["lat"]),
                lon=float(payload["lon"]),
                accuracy_m=float(acc) if acc is not None else None,
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class EnrichedPoint:
    ts: float
    lat: float
    lon: float
    accuracy_m: float | None
    speed_mps: float
    heading_deg: float | None
    distance_m: float  # from previous accepted point (0.0 for the first)
    geofence: str | None  # "home" | "work" | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Segment:
    trip_id: str
    seg_index: int
    mode: str  # "stationary" | "walk" | "vehicle"
    start_ts: float
    end_ts: float
    duration_s: float
    distance_m: float
    point_count: int


@dataclass(frozen=True)
class Trip:
    trip_id: str
    start_ts: float
    end_ts: float
    duration_s: float
    distance_m: float
    point_count: int
    start_geofence: str | None
    end_geofence: str | None
    direction: str  # "outbound" | "inbound" | "other"


@dataclass(frozen=True)
class TripClosed:
    trip: Trip
    segments: list[Segment] = field(default_factory=list)
    points: list[EnrichedPoint] = field(default_factory=list)


@dataclass(frozen=True)
class PointRejected:
    point: Point
    reason: str  # "accuracy" | "out_of_order" | "teleport"
