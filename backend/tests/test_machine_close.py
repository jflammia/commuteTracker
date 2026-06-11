from backend.engine.geofence import Geofence
from backend.engine.machine import TripEngine
from backend.engine.params import EngineParams
from backend.engine.types import TripClosed
from backend.tests.synth import commute, dwell, end_of, leg

HOME = Geofence(name="home", lat=40.7000, lon=-74.4000, radius_m=50.0)


def _drive(events):
    return [e for e in events if isinstance(e, TripClosed)]


def _run(eng, pts):
    out = []
    for pt in pts:
        out.extend(eng.process(pt))
    return out


def test_dwell_closes_trip():
    pts, lat0, lat_end = commute()
    work = Geofence(name="work", lat=lat_end, lon=-74.4000, radius_m=150.0)
    eng = TripEngine(EngineParams(), geofences=[HOME, work])
    closed = _drive(_run(eng, pts))
    assert len(closed) == 1
    trip = closed[0].trip
    assert trip.trip_id == f"t{int(closed[0].points[0].ts)}"
    assert trip.direction == "outbound"
    assert trip.start_geofence == "home"
    assert trip.end_geofence == "work"
    assert trip.distance_m > 15000
    assert eng.state.status == "idle"


def test_gap_closes_trip_at_last_point():
    pts = leg(1000.0, 40.7000, -74.4000, speed_mps=20.0, duration_s=900)
    eng = TripEngine(EngineParams(), geofences=[])
    _run(eng, pts)
    assert eng.state.status == "moving"
    # next point 2 hours later
    t, lat = end_of(pts)
    far_later = leg(t + 7200, lat, -74.4000, speed_mps=0.0, duration_s=30)
    closed = _drive(_run(eng, far_later))
    assert len(closed) == 1
    assert closed[0].trip.end_ts == pts[-1].ts


def test_phantom_short_trip_is_dropped():
    # 90 s of movement (< min_trip_duration_s) then long dwell
    pts = leg(1000.0, 40.7000, -74.4000, speed_mps=1.5, duration_s=90)
    t, lat = end_of(pts)
    pts += dwell(t, lat, -74.4000, duration_s=600)
    eng = TripEngine(EngineParams(), geofences=[])
    closed = _drive(_run(eng, pts))
    assert closed == []
    assert eng.state.status == "idle"


def test_two_trips_in_one_day():
    pts1, lat0, lat_end = commute(t0=1_781_100_000.0)
    # return trip later from where the first ended
    pts2, _, _ = commute(t0=1_781_130_000.0, lat0=lat_end)
    eng = TripEngine(EngineParams(), geofences=[])
    closed = _drive(_run(eng, pts1 + pts2))
    assert len(closed) == 2
    assert closed[0].trip.end_ts < closed[1].trip.start_ts
