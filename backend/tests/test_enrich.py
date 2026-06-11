from backend.engine.enrich import enrich
from backend.engine.types import EnrichedPoint, Point


def test_first_point_has_zero_motion():
    ep = enrich(None, Point(ts=100.0, lat=40.7, lon=-74.4, accuracy_m=5.0), "home")
    assert ep.speed_mps == 0.0
    assert ep.distance_m == 0.0
    assert ep.heading_deg is None
    assert ep.geofence == "home"


def test_deltas_from_previous_point():
    prev = EnrichedPoint(
        ts=100.0,
        lat=40.7000,
        lon=-74.4000,
        accuracy_m=5.0,
        speed_mps=0.0,
        heading_deg=None,
        distance_m=0.0,
        geofence=None,
    )
    # ~111 m due north over 30 s → ~3.7 m/s heading ~0°
    ep = enrich(prev, Point(ts=130.0, lat=40.7010, lon=-74.4000, accuracy_m=5.0), None)
    assert 100 < ep.distance_m < 120
    assert 3.3 < ep.speed_mps < 4.0
    assert ep.heading_deg is not None and (ep.heading_deg < 1.0 or ep.heading_deg > 359.0)
