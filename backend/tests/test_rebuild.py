from backend.engine.rebuild import rebuild
from backend.storage.archive import Archiver
from backend.storage.raw import RawStore
from backend.tests.synth import commute


def _ingest_synthetic_day(settings, day="2026-06-09"):
    """Write a synthetic commute as raw owntracks records on a given day."""
    store = RawStore(settings.data_dir)
    pts, _, _ = commute()
    for i, pt in enumerate(pts):
        payload = {
            "_type": "location",
            "tst": pt.ts,
            "lat": pt.lat,
            "lon": pt.lon,
            "acc": pt.accuracy_m,
        }
        store.append(
            "owntracks",
            {
                "received_at": f"{day}T{i // 3600:02d}:{(i // 60) % 60:02d}:{i % 60:02d}+00:00",
                "user": "j",
                "device": "d",
                "payload": payload,
            },
        )
    return pts


def test_rebuild_from_raw_tail(settings):
    _ingest_synthetic_day(settings)
    engine, store, counts = rebuild(settings)
    assert counts["points"] > 50
    assert counts["trips"] == 1
    assert len(store.list_trips()) == 1


def test_rebuild_spans_archive_and_tail(settings):
    _ingest_synthetic_day(settings, day="2026-06-09")
    Archiver(settings).run(today="2026-06-10")  # day file → parquet
    # engine must see archived events through EventQuery
    engine, store, counts = rebuild(settings)
    assert counts["trips"] == 1


def test_rebuild_truncates_previous_derived(settings):
    _ingest_synthetic_day(settings)
    rebuild(settings)
    engine, store, counts = rebuild(settings)  # second run: same single trip
    assert len(store.list_trips()) == 1


def test_rebuild_skips_non_location(settings):
    store = RawStore(settings.data_dir)
    store.append(
        "owntracks",
        {
            "received_at": "2026-06-09T00:00:00+00:00",
            "user": "j",
            "device": "d",
            "payload": {"_type": "transition", "tst": 1},
        },
    )
    engine, dstore, counts = rebuild(settings)
    assert counts["skipped"] == 1
    assert counts.get("trips", 0) == 0


def test_rebuild_replays_labels(settings):
    _ingest_synthetic_day(settings)
    engine, store, counts = rebuild(settings)
    trip_id = store.list_trips()[0]["trip_id"]
    seg_index = next(
        s["seg_index"] for s in store.get_trip(trip_id)["segments"] if s["mode"] == "vehicle"
    )
    # label arrives as a primitive event (as the API would write it)
    RawStore(settings.data_dir).append(
        "labels",
        {
            "received_at": "2026-06-10T15:00:00+00:00",
            "payload": {
                "type": "segment_mode",
                "trip_id": trip_id,
                "seg_index": seg_index,
                "value": "train",
            },
        },
    )
    engine, store, counts = rebuild(settings)  # derived wiped + rebuilt
    assert counts["labels_applied"] == 1
    seg = next(s for s in store.get_trip(trip_id)["segments"] if s["seg_index"] == seg_index)
    assert seg["mode_effective"] == "train"


def test_rebuild_skips_labels_for_vanished_trips(settings):
    _ingest_synthetic_day(settings)
    RawStore(settings.data_dir).append(
        "labels",
        {
            "received_at": "2026-06-10T15:00:00+00:00",
            "payload": {"type": "trip_flag", "trip_id": "t999999", "value": "ok"},
        },
    )
    engine, store, counts = rebuild(settings)
    assert counts["labels_skipped"] == 1
    assert counts.get("labels_applied", 0) == 0


def test_rebuild_parses_schedule_and_matches_trains(settings):
    import base64
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from backend.engine.machine import TripEngine
    from backend.engine.params import EngineParams
    from backend.engine.types import TripClosed
    from backend.tests.gtfs_fixture import build_gtfs_zip

    # determine vehicle segment endpoints offline
    pts, _, _ = commute()
    eng = TripEngine(EngineParams(), geofences=[])
    closed = []
    for pt in pts:
        closed.extend(e for e in eng.process(pt) if isinstance(e, TripClosed))
    seg = next(s for s in closed[0].segments if s.mode == "vehicle")
    start = next(p for p in closed[0].points if p.ts >= seg.start_ts)
    end = max((p for p in closed[0].points if p.ts <= seg.end_ts), key=lambda p: p.ts)
    ny = ZoneInfo("America/New_York")

    def hms(epoch):
        dt = datetime.fromtimestamp(epoch, ny)
        return f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"

    z = build_gtfs_zip(
        [("S1", "Alpha", start.lat, start.lon), ("S2", "Beta", end.lat, end.lon)],
        [("T1", "WK", "Beta-bound", [("S1", hms(start.ts)), ("S2", hms(end.ts))])],
    )
    RawStore(settings.data_dir).append(
        "gtfs_path",
        {
            "received_at": "2026-06-09T05:00:00+00:00",
            "payload": {"url": "u", "status": 200, "b64": base64.b64encode(z).decode()},
        },
    )
    _ingest_synthetic_day(settings)
    engine, store, counts = rebuild(settings)
    assert counts["trips"] == 1
    assert counts["train_matches"] == 1
    trip_id = store.list_trips()[0]["trip_id"]
    assert store.matches_for_trip(trip_id)[0]["gtfs_trip_id"] == "T1"
