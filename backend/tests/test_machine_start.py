from backend.engine.machine import EngineState, TripEngine
from backend.engine.params import EngineParams
from backend.engine.types import PointRejected
from backend.tests.synth import dwell, leg


def _engine():
    return TripEngine(EngineParams(), geofences=[])


def test_stationary_points_never_start_a_trip():
    eng = _engine()
    for pt in dwell(1000.0, 40.7, -74.4, duration_s=1200):
        assert eng.process(pt) == []
    assert eng.state.status == "idle"


def test_sustained_movement_starts_a_trip():
    eng = _engine()
    for pt in leg(1000.0, 40.7, -74.4, speed_mps=1.5, duration_s=300):
        eng.process(pt)
    assert eng.state.status == "moving"
    assert len(eng.state.trip_points) >= 3


def test_brief_jitter_does_not_start_a_trip():
    eng = _engine()
    pts = dwell(1000.0, 40.7, -74.4, duration_s=120)
    # a 60 s fast blip (2 points, second displaced ~60 m → real computed speed)
    # in the middle of stillness: at most 1 fast point in any 5-window — below
    # move_min_points=3
    pts += leg(1150.0, 40.7, -74.4, speed_mps=2.0, duration_s=60)
    pts += dwell(1240.0, pts[-1].lat, -74.4, duration_s=150)
    for pt in pts:
        eng.process(pt)
    assert eng.state.status == "idle"


def test_rejected_points_are_reported_not_processed():
    eng = _engine()
    pts = leg(1000.0, 40.7, -74.4, speed_mps=1.5, duration_s=120)
    events = []
    for pt in pts:
        events.extend(eng.process(pt))
    bad = pts[-1].__class__(ts=pts[-1].ts + 30, lat=41.5, lon=-74.4, accuracy_m=5.0)  # teleport
    ev = eng.process(bad)
    assert len(ev) == 1
    assert isinstance(ev[0], PointRejected)
    assert ev[0].reason == "teleport"


def test_state_roundtrips_dict():
    eng = _engine()
    for pt in leg(1000.0, 40.7, -74.4, speed_mps=1.5, duration_s=300):
        eng.process(pt)
    restored = EngineState.from_dict(eng.state.to_dict())
    assert restored == eng.state
