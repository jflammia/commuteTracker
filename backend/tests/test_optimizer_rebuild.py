import base64
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.engine.machine import TripEngine
from backend.engine.params import EngineParams
from backend.engine.rebuild import rebuild
from backend.engine.types import TripClosed
from backend.storage.raw import RawStore
from backend.tests.gtfs_fixture import build_gtfs_zip
from backend.tests.synth import commute
from backend.tests.test_rebuild import _ingest_synthetic_day

NY = ZoneInfo("America/New_York")


def _seed_schedule_aligned_to_commute(settings):
    pts, _, _ = commute()
    eng = TripEngine(EngineParams(), geofences=[])
    closed = []
    for pt in pts:
        closed.extend(e for e in eng.process(pt) if isinstance(e, TripClosed))
    seg = next(s for s in closed[0].segments if s.mode == "vehicle")
    start = next(p for p in closed[0].points if p.ts >= seg.start_ts)
    end = max((p for p in closed[0].points if p.ts <= seg.end_ts), key=lambda p: p.ts)

    def hms(epoch):
        dt = datetime.fromtimestamp(epoch, NY)
        return f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"

    z = build_gtfs_zip(
        [("S1", "Alpha", start.lat, start.lon), ("S2", "Beta", end.lat, end.lon)],
        [("T1", "WK", "Beta", [("S1", hms(start.ts)), ("S2", hms(end.ts))])],
    )
    RawStore(settings.data_dir).append(
        "gtfs_njt",
        {
            "received_at": "2026-06-09T05:00:00+00:00",
            "payload": {"url": "u", "status": 200, "b64": base64.b64encode(z).decode()},
        },
    )


def test_rebuild_populates_leg_observations(settings):
    _seed_schedule_aligned_to_commute(settings)
    _ingest_synthetic_day(settings)
    engine, store, counts = rebuild(settings)
    assert counts["trips"] == 1
    assert counts["train_matches"] == 1
    legs = store.leg_observations()
    kinds = {leg["kind"] for leg in legs}
    assert "access" in kinds
    assert any(k.startswith("ride:") for k in kinds)
    assert counts["leg_observations"] >= 2


def test_rebuild_skips_legs_for_unmatched_trips(settings):
    # commute with no GTFS schedule → trip exists, no matches → no legs
    _ingest_synthetic_day(settings)
    engine, store, counts = rebuild(settings)
    assert counts["trips"] == 1
    assert store.leg_observations() == []
