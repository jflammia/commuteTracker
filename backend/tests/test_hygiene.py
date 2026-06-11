from backend.engine.hygiene import check
from backend.engine.params import EngineParams
from backend.engine.types import EnrichedPoint, Point

P = EngineParams()


def _prev(ts=1000.0, lat=40.70, lon=-74.40):
    return EnrichedPoint(
        ts=ts,
        lat=lat,
        lon=lon,
        accuracy_m=5.0,
        speed_mps=0.0,
        heading_deg=None,
        distance_m=0.0,
        geofence=None,
    )


def test_accepts_clean_point():
    assert check(_prev(), Point(ts=1030.0, lat=40.7001, lon=-74.40, accuracy_m=8.0), P) is None


def test_accepts_first_point_without_prev():
    assert check(None, Point(ts=1.0, lat=40.7, lon=-74.4, accuracy_m=8.0), P) is None


def test_rejects_bad_accuracy():
    pt = Point(ts=1030.0, lat=40.7, lon=-74.4, accuracy_m=150.0)
    assert check(_prev(), pt, P) == "accuracy"


def test_accepts_missing_accuracy():
    assert check(_prev(), Point(ts=1030.0, lat=40.7, lon=-74.4, accuracy_m=None), P) is None


def test_rejects_out_of_order_and_duplicate_ts():
    assert check(_prev(ts=1000.0), Point(ts=999.0, lat=40.7, lon=-74.4), P) == "out_of_order"
    assert check(_prev(ts=1000.0), Point(ts=1000.0, lat=40.7, lon=-74.4), P) == "out_of_order"


def test_rejects_teleport():
    # ~11 km in 30 s ≈ 370 m/s
    pt = Point(ts=1030.0, lat=40.80, lon=-74.40, accuracy_m=5.0)
    assert check(_prev(ts=1000.0, lat=40.70), pt, P) == "teleport"


def test_long_gap_is_not_teleport():
    # same 11 km but over 2 hours — slow, fine
    pt = Point(ts=8200.0, lat=40.80, lon=-74.40, accuracy_m=5.0)
    assert check(_prev(ts=1000.0, lat=40.70), pt, P) is None
