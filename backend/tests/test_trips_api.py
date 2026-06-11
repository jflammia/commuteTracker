import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.synth import commute


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        # drive a commute through live ingest so a trip exists
        pts, _, _ = commute()
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
        yield c


def test_list_trips(client):
    resp = client.get("/api/trips")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["direction"] in ("outbound", "inbound", "other")
    assert body[0]["start_ts"].endswith("+00:00")


def test_list_trips_respects_limit(client):
    assert client.get("/api/trips?limit=0").json() == []


def test_trip_detail(client):
    trip_id = client.get("/api/trips").json()[0]["trip_id"]
    resp = client.get(f"/api/trips/{trip_id}")
    assert resp.status_code == 200
    d = resp.json()
    assert d["trip"]["trip_id"] == trip_id
    assert len(d["segments"]) >= 1
    assert len(d["points"]) > 10


def test_trip_detail_404(client):
    assert client.get("/api/trips/nope").status_code == 404
