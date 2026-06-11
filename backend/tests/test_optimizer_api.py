import base64
import dataclasses

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.storage.raw import RawStore
from backend.tests.gtfs_fixture import build_gtfs_zip

STOPS = [("MP", "Metropark", 40.70, -74.40), ("NYP", "New York Penn", 40.75, -73.99)]
TRIPS = [
    ("NEC1", "WK", "NYP", [("MP", "07:38:00"), ("NYP", "08:15:00")]),
    ("NEC2", "WK", "NYP", [("MP", "08:02:00"), ("NYP", "08:39:00")]),
]


def _opt_settings(settings):
    return dataclasses.replace(
        settings,
        commute_source="gtfs_njt",
        board_stop_id="MP",
        alight_stop_id="NYP",
        arrive_by_local="09:00",
        access_distance_m=500.0,
        egress_distance_m=650.0,
    )


@pytest.fixture
def client(settings):
    s = _opt_settings(settings)
    z = build_gtfs_zip(STOPS, TRIPS, route_type=2, route_name="Northeast Corridor")
    RawStore(s.data_dir).append(
        "gtfs_njt",
        {
            "received_at": "2026-06-09T05:00:00+00:00",
            "payload": {"url": "u", "status": 200, "b64": base64.b64encode(z).decode()},
        },
    )
    app = create_app(s)
    with TestClient(app) as c:
        yield c


def test_whatif_returns_ranked_options(client):
    resp = client.get("/api/optimizer?date=2026-06-10&arrive_by=08:30")
    assert resp.status_code == 200
    body = resp.json()
    assert body["direction"] == "outbound"
    assert [o["gtfs_trip_id"] for o in body["options"]][0] == "NEC1"
    opt = body["options"][0]
    # API exposes ISO timestamps for display
    assert opt["leave_by"].endswith(("+00:00", "-04:00", "-05:00"))
    assert "p50_arrive" in opt and "p90_arrive" in opt


def test_whatif_defaults_to_configured_arrive_by(client):
    resp = client.get("/api/optimizer?date=2026-06-10")
    assert resp.status_code == 200
    assert resp.json()["arrive_by_local"] == "09:00"


def test_whatif_unconfigured_returns_409(settings):
    app = create_app(settings)  # no commute_source
    with TestClient(app) as c:
        resp = c.get("/api/optimizer?date=2026-06-10")
    assert resp.status_code == 409
    assert "not configured" in resp.json()["detail"].lower()


def test_recommendation_endpoint_reads_persisted(client):
    # the daily job hasn't run; the endpoint computes on-demand if absent and persists
    resp = client.get("/api/recommendation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["direction"] == "outbound"
    assert "options" in body
