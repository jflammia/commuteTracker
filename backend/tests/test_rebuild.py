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
