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
