import json

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.synth import commute


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
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
        yield c, app


def _vehicle_seg_index(c, trip_id):
    detail = c.get(f"/api/trips/{trip_id}").json()
    return next(s["seg_index"] for s in detail["segments"] if s["mode"] == "vehicle")


def test_post_label_applies_and_archives(client, settings):
    c, app = client
    trip_id = c.get("/api/trips").json()[0]["trip_id"]
    seg_index = _vehicle_seg_index(c, trip_id)
    resp = c.post(
        "/api/labels",
        json={
            "type": "segment_mode",
            "trip_id": trip_id,
            "seg_index": seg_index,
            "value": "train",
        },
    )
    assert resp.status_code == 201
    assert resp.json() == {"applied": True}
    # applied to derived
    detail = c.get(f"/api/trips/{trip_id}").json()
    seg = next(s for s in detail["segments"] if s["seg_index"] == seg_index)
    assert seg["mode_effective"] == "train"
    # archived as primitive FIRST — raw labels stream has the event
    label_files = list((settings.data_dir / "raw" / "labels").glob("*.jsonl"))
    assert len(label_files) == 1
    rec = json.loads(label_files[0].read_text().splitlines()[0])
    assert rec["payload"]["type"] == "segment_mode"
    assert rec["payload"]["value"] == "train"


def test_post_label_unknown_trip_archives_but_applies_false(client, settings):
    c, app = client
    resp = c.post("/api/labels", json={"type": "trip_flag", "trip_id": "nope", "value": "ok"})
    assert resp.status_code == 201
    assert resp.json() == {"applied": False}
    assert (settings.data_dir / "raw" / "labels").is_dir()  # still archived


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "nonsense", "trip_id": "t", "value": "x"},
        {"type": "segment_mode", "trip_id": "t", "value": "flying"},  # bad mode
        {"type": "segment_mode", "trip_id": "t", "value": "walk"},  # missing seg_index
        {"type": "train_match", "trip_id": "t", "seg_index": 0, "value": "maybe"},
        {"type": "trip_flag", "trip_id": "t", "value": "weird"},
        {"type": "trip_reviewed", "trip_id": "t", "value": "yes"},  # not bool
        {"trip_id": "t", "value": "x"},  # no type
    ],
)
def test_post_label_validation_rejects_garbage(client, bad):
    c, app = client
    resp = c.post("/api/labels", json=bad)
    assert resp.status_code == 400
    # rejected garbage is NOT archived as a label event — only valid labels
    # are primitive data


def test_trips_reviewed_filter_via_api(client):
    c, app = client
    trip_id = c.get("/api/trips").json()[0]["trip_id"]
    assert c.get("/api/trips?reviewed=false").json()[0]["trip_id"] == trip_id
    c.post(
        "/api/labels",
        json={"type": "trip_reviewed", "trip_id": trip_id, "value": True},
    )
    assert c.get("/api/trips?reviewed=false").json() == []
    assert c.get("/api/trips?reviewed=true").json()[0]["trip_id"] == trip_id
