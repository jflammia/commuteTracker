import base64
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.engine.machine import TripEngine
from backend.engine.params import EngineParams
from backend.engine.types import TripClosed
from backend.storage.derived import DerivedStore
from backend.storage.raw import RawStore
from backend.tests.gtfs_fixture import build_gtfs_zip
from backend.tests.synth import commute
from backend.transit.gtfs import latest_snapshot, parse_gtfs
from backend.transit.matcher import match_trip

NY = ZoneInfo("America/New_York")


def _closed_commute():
    pts, _, _ = commute()  # t0 = 1_781_100_000 → 2026-06-10 10:00 EDT
    eng = TripEngine(EngineParams(), geofences=[])
    closed = []
    for pt in pts:
        closed.extend(e for e in eng.process(pt) if isinstance(e, TripClosed))
    assert len(closed) == 1
    return closed[0]


def _vehicle_segment_endpoints(closed):
    seg = next(s for s in closed.segments if s.mode == "vehicle")
    start = next(p for p in closed.points if p.ts >= seg.start_ts)
    end = max((p for p in closed.points if p.ts <= seg.end_ts), key=lambda p: p.ts)
    return seg, start, end


def _hms(epoch):
    dt = datetime.fromtimestamp(epoch, NY)
    return f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"


def _load_schedule(settings, closed, dep_offset_s=120.0):
    """Build a fixture schedule with stations AT the vehicle segment endpoints
    and a trip departing dep_offset_s after the segment starts."""
    seg, start, end = _vehicle_segment_endpoints(closed)
    stops = [("S1", "Alpha", start.lat, start.lon), ("S2", "Beta", end.lat, end.lon)]
    trips = [
        (
            "T1",
            "WK",
            "Beta-bound",
            [("S1", _hms(start.ts + dep_offset_s)), ("S2", _hms(end.ts + dep_offset_s))],
        ),
        # decoy 40 minutes later — must not be picked
        (
            "T2",
            "WK",
            "Beta-bound",
            [("S1", _hms(start.ts + 2400)), ("S2", _hms(end.ts + 2400))],
        ),
    ]
    RawStore(settings.data_dir).append(
        "gtfs_path",
        {
            "received_at": "2026-06-09T05:00:00+00:00",
            "payload": {
                "url": "u",
                "status": 200,
                "b64": base64.b64encode(build_gtfs_zip(stops, trips)).decode(),
            },
        },
    )
    store = DerivedStore(settings)
    parse_gtfs(store.con, "gtfs_path", latest_snapshot(settings, "gtfs_path"), fetched_at="x")
    return store


def test_matches_vehicle_segment_to_scheduled_trip(settings):
    closed = _closed_commute()
    store = _load_schedule(settings, closed)
    matches = match_trip(store.con, closed)
    assert len(matches) == 1
    m = matches[0]
    assert m.gtfs_trip_id == "T1"
    assert m.source == "gtfs_path"
    assert m.board_stop == "Alpha"
    assert m.alight_stop == "Beta"
    assert abs(m.delta_s + 120.0) < 60.0  # observed start ~2 min BEFORE scheduled dep
    assert m.trip_id == closed.trip.trip_id
    seg = next(s for s in closed.segments if s.mode == "vehicle")
    assert m.seg_index == seg.seg_index


def test_no_match_when_no_station_nearby(settings):
    closed = _closed_commute()
    # stations far away (>500 m east)
    store = _load_schedule(settings, closed)
    store.con.execute("UPDATE gtfs_stops SET stop_lon = stop_lon + 0.02")
    assert match_trip(store.con, closed) == []


def test_no_match_when_departure_outside_tolerance(settings):
    closed = _closed_commute()
    store = _load_schedule(settings, closed, dep_offset_s=3600.0)
    # nearest trip departs an hour later: T1 out of tolerance, decoy further
    assert match_trip(store.con, closed) == []


def test_walk_segments_never_matched(settings):
    closed = _closed_commute()
    store = _load_schedule(settings, closed)
    matches = match_trip(store.con, closed)
    walk_indexes = {s.seg_index for s in closed.segments if s.mode != "vehicle"}
    assert all(m.seg_index not in walk_indexes for m in matches)
