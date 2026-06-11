"""End-to-end: synth commute + fixture schedule → API itinerary with train leg."""

import base64
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.engine.machine import TripEngine
from backend.engine.params import EngineParams
from backend.engine.types import TripClosed
from backend.storage.raw import RawStore
from backend.tests.gtfs_fixture import build_gtfs_zip
from backend.tests.synth import commute


def test_trip_detail_includes_itinerary_with_train(settings):
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
        route_name="Test Line",
    )
    RawStore(settings.data_dir).append(
        "gtfs_path",
        {
            "received_at": "2026-06-09T05:00:00+00:00",
            "payload": {"url": "u", "status": 200, "b64": base64.b64encode(z).decode()},
        },
    )

    app = create_app(settings)
    with TestClient(app) as c:
        for pt in pts:
            c.post(
                "/ingest/owntracks",
                json={
                    "_type": "location",
                    "tst": pt.ts,
                    "lat": pt.lat,
                    "lon": pt.lon,
                    "acc": pt.accuracy_m,
                },
            )
        trip_id = c.get("/api/trips").json()[0]["trip_id"]
        detail = c.get(f"/api/trips/{trip_id}").json()

    itinerary = detail["itinerary"]
    modes = [leg["mode"] for leg in itinerary]
    assert "vehicle" in modes and "walk" in modes
    train_leg = next(leg for leg in itinerary if leg["mode"] == "vehicle")
    assert train_leg["train"]["gtfs_trip_id"] == "T1"
    assert train_leg["train"]["route_name"] == "Test Line"
    assert train_leg["train"]["board_stop"] == "Alpha"
    walk_leg = next(leg for leg in itinerary if leg["mode"] == "walk")
    assert walk_leg["train"] is None
