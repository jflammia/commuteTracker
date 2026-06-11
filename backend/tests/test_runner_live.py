import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.synth import commute


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c, app


def test_live_points_produce_a_trip(client, settings):
    c, app = client
    pts, _, _ = commute()
    for pt in pts:
        payload = {
            "_type": "location",
            "tst": pt.ts,
            "lat": pt.lat,
            "lon": pt.lon,
            "acc": pt.accuracy_m,
        }
        resp = c.post("/ingest/owntracks", json=payload)
        assert resp.status_code == 200
    trips = app.state.runner.store.list_trips()
    assert len(trips) == 1
    assert trips[0]["distance_m"] > 15000


def test_engine_failure_never_affects_200(client, monkeypatch):
    c, app = client

    def boom(payload):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(app.state.runner, "process_payload", boom)
    resp = c.post("/ingest/owntracks", json={"_type": "location", "tst": 1, "lat": 1, "lon": 2})
    assert resp.status_code == 200


def test_live_trip_close_triggers_matching(settings):
    import base64
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from backend.engine.machine import TripEngine
    from backend.engine.params import EngineParams
    from backend.engine.types import TripClosed
    from backend.storage.raw import RawStore
    from backend.tests.gtfs_fixture import build_gtfs_zip

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
        trips = app.state.runner.store.list_trips()
        assert len(trips) == 1
        matches = app.state.runner.store.matches_for_trip(trips[0]["trip_id"])
        assert [m["gtfs_trip_id"] for m in matches] == ["T1"]


def test_startup_rebuild_recovers_history(settings):
    # pre-existing raw data is replayed into the derived store at startup
    from backend.storage.raw import RawStore

    store = RawStore(settings.data_dir)
    pts, _, _ = commute()
    for i, pt in enumerate(pts):
        # ascending received_at: ORDER BY received_at must reproduce tst order
        # (DuckDB sort is not stable for equal keys)
        store.append(
            "owntracks",
            {
                "received_at": f"2026-06-09T{i // 3600:02d}:{(i // 60) % 60:02d}:{i % 60:02d}+00:00",
                "user": "j",
                "device": "d",
                "payload": {
                    "_type": "location",
                    "tst": pt.ts,
                    "lat": pt.lat,
                    "lon": pt.lon,
                    "acc": pt.accuracy_m,
                },
            },
        )
    app = create_app(settings)
    with TestClient(app):
        assert len(app.state.runner.store.list_trips()) == 1
