"""Trip segmentation by mode heuristic. (Minimal version — Task 10 adds the
real per-point mode classification, smoothing, and short-segment merging.)"""

from backend.engine.params import EngineParams
from backend.engine.types import EnrichedPoint, Segment


def segment_trip(trip_id: str, points: list[EnrichedPoint], params: EngineParams) -> list[Segment]:
    return [
        Segment(
            trip_id=trip_id,
            seg_index=0,
            mode="vehicle",
            start_ts=points[0].ts,
            end_ts=points[-1].ts,
            duration_s=points[-1].ts - points[0].ts,
            distance_m=sum(p.distance_m for p in points[1:]),
            point_count=len(points),
        )
    ]
