"""Trip segmentation: per-point mode heuristic → smoothing → split → merge-short.

Modes are a transparent baseline (stationary/walk/vehicle by speed). Phase 3
refines "vehicle" into train/drive via GTFS matching; manual labels (Phase 4)
override everything.
"""

from backend.engine.params import EngineParams
from backend.engine.types import EnrichedPoint, Segment


def _raw_mode(speed_mps: float, params: EngineParams) -> str:
    if speed_mps < params.stationary_max_mps:
        return "stationary"
    if speed_mps < params.walk_max_mps:
        return "walk"
    return "vehicle"


def _smooth(modes: list[str]) -> list[str]:
    """Majority vote over a 5-wide window; ties keep the point's own mode."""
    out = []
    for i in range(len(modes)):
        lo, hi = max(0, i - 2), min(len(modes), i + 3)
        window = modes[lo:hi]
        best = max(
            sorted(set(window)), key=lambda m: (window.count(m), m == modes[i])
        )  # sorted() makes tie-breaks deterministic (set order is hash-randomized)
        out.append(best)
    return out


def _split(modes: list[str]) -> list[tuple[int, int, str]]:
    """Contiguous (start, end_exclusive, mode) runs."""
    runs = []
    start = 0
    for i in range(1, len(modes) + 1):
        if i == len(modes) or modes[i] != modes[start]:
            runs.append((start, i, modes[start]))
            start = i
    return runs


def _merge_short(
    runs: list[tuple[int, int, str]], points: list[EnrichedPoint], params: EngineParams
) -> list[tuple[int, int, str]]:
    """Merge runs shorter than min_segment_s into the previous run (or the
    next, for a short leading run). Mode of the absorbing run wins."""
    merged: list[tuple[int, int, str]] = []
    for run in runs:
        start, end, mode = run
        duration = points[end - 1].ts - points[start].ts
        if duration < params.min_segment_s and merged:
            pstart, _, pmode = merged.pop()
            merged.append((pstart, end, pmode))
        else:
            merged.append(run)
    # a short leading run that survived: absorb into the following run
    if len(merged) >= 2:
        start, end, _ = merged[0]
        if points[end - 1].ts - points[start].ts < params.min_segment_s:
            _, nend, nmode = merged[1]
            merged = [(start, nend, nmode)] + merged[2:]
    return merged


def segment_trip(trip_id: str, points: list[EnrichedPoint], params: EngineParams) -> list[Segment]:
    modes = _smooth([_raw_mode(p.speed_mps, params) for p in points])
    runs = _merge_short(_split(modes), points, params)
    segments = []
    for idx, (start, end, mode) in enumerate(runs):
        pts = points[start:end]
        segments.append(
            Segment(
                trip_id=trip_id,
                seg_index=idx,
                mode=mode,
                start_ts=pts[0].ts,
                end_ts=pts[-1].ts,
                duration_s=pts[-1].ts - pts[0].ts,
                distance_m=sum(p.distance_m for p in pts[1 if start == 0 else 0 :]),
                point_count=len(pts),
            )
        )
    return segments
