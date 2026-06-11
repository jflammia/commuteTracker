from backend.engine.params import EngineParams
from backend.engine.segmenter import segment_trip
from backend.engine.types import EnrichedPoint

P = EngineParams()


def _pts(specs):
    """specs: list of (duration_s, speed_mps). 30 s cadence, due-north motion."""
    pts = []
    t, lat = 1000.0, 40.7000
    for duration, speed in specs:
        n = int(duration / 30)
        for _ in range(n):
            dist = speed * 30.0
            pts.append(
                EnrichedPoint(
                    ts=t,
                    lat=lat,
                    lon=-74.4,
                    accuracy_m=10.0,
                    speed_mps=speed,
                    heading_deg=0.0,
                    distance_m=dist if pts else 0.0,
                    geofence=None,
                )
            )
            t += 30.0
            lat += dist / 111120.0
    return pts


def test_three_phase_commute_yields_three_segments():
    pts = _pts([(300, 1.5), (900, 20.0), (300, 1.5)])
    segs = segment_trip("t1", pts, P)
    assert [s.mode for s in segs] == ["walk", "vehicle", "walk"]
    assert [s.seg_index for s in segs] == [0, 1, 2]
    assert segs[0].trip_id == "t1"
    assert segs[1].distance_m > segs[0].distance_m


def test_single_blip_is_smoothed_away():
    # walking with one 30s "vehicle" blip in the middle
    pts = _pts([(150, 1.5), (30, 10.0), (150, 1.5)])
    segs = segment_trip("t1", pts, P)
    assert [s.mode for s in segs] == ["walk"]


def test_short_segment_merges_into_neighbor():
    # 30s walk sandwich between two long vehicle stretches — below min_segment_s
    # after smoothing survivors are merged
    pts = _pts([(600, 20.0), (30, 1.5), (600, 20.0)])
    segs = segment_trip("t1", pts, P)
    assert [s.mode for s in segs] == ["vehicle"]


def test_segments_tile_the_trip():
    pts = _pts([(300, 1.5), (900, 20.0), (300, 0.2)])
    segs = segment_trip("t1", pts, P)
    assert segs[0].start_ts == pts[0].ts
    assert segs[-1].end_ts == pts[-1].ts
    for a, b in zip(segs, segs[1:]):
        assert a.end_ts <= b.start_ts
    assert sum(s.point_count for s in segs) == len(pts)
