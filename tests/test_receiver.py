"""Tests for the FastAPI receiver endpoint."""

import json

import pytest
from fastapi.testclient import TestClient

from src.storage.database import Database


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    database = Database(f"sqlite:///{db_path}")
    database.create_tables()
    return database


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr("src.receiver.app.db", db)
    monkeypatch.setattr("src.receiver.app.RECORDER_URL", "")
    from src.receiver.app import app
    return TestClient(app)


@pytest.fixture
def sample_location():
    return {
        "_type": "location",
        "tid": "ph",
        "lat": 40.7500,
        "lon": -74.0000,
        "alt": 25,
        "acc": 10,
        "vel": 12,
        "tst": 1711440000,
        "batt": 85,
        "conn": "w",
    }


def test_receive_location(client, db, sample_location):
    resp = client.post(
        "/pub",
        json=sample_location,
        headers={"X-Limit-U": "testuser", "X-Limit-D": "testdevice"},
    )

    assert resp.status_code == 200
    assert resp.json() == []
    assert db.count_records() == 1

    unsynced = db.get_unsynced_records()
    payload = json.loads(unsynced[0].payload)
    assert payload["lat"] == 40.75
    assert payload["_type"] == "location"
    assert payload["_receiver_user"] == "testuser"
    assert payload["_receiver_device"] == "testdevice"
    assert "received_at" in payload


def test_receive_empty_body(client, db):
    resp = client.post("/pub", content=b"")
    assert resp.status_code == 200
    assert resp.json() == []
    assert db.count_records() == 0


def test_receive_invalid_json_still_200(client, db):
    """OwnTracks data loss prevention: never return 4xx."""
    resp = client.post(
        "/pub",
        content=b"not valid json",
        headers={"Content-Type": "application/json"},
    )

    # Must still return 200 to prevent OwnTracks from discarding data
    assert resp.status_code == 200
    assert resp.json() == []


def test_receive_transition(client, db):
    transition = {
        "_type": "transition",
        "event": "enter",
        "desc": "Home",
        "lat": 40.75,
        "lon": -74.0,
        "tst": 1711440000,
        "tid": "ph",
        "acc": 15,
    }

    resp = client.post("/pub", json=transition)
    assert resp.status_code == 200
    assert db.count_records() == 1

    unsynced = db.get_unsynced_records()
    payload = json.loads(unsynced[0].payload)
    assert payload["_type"] == "transition"
    assert payload["event"] == "enter"


def test_health_endpoint(client, db, sample_location):
    # Insert a record first
    client.post("/pub", json=sample_location)

    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["total_records"] == 1
    assert data["unsynced_records"] == 1
    assert "database" in data
    assert "s3_enabled" in data
    assert "recorder_enabled" in data


def test_default_headers_when_missing(client, db):
    resp = client.post("/pub", json={"_type": "location", "lat": 40.0, "lon": -74.0, "tst": 1})
    assert resp.status_code == 200

    unsynced = db.get_unsynced_records()
    payload = json.loads(unsynced[0].payload)
    assert payload["_receiver_user"] == "unknown"
    assert payload["_receiver_device"] == "unknown"


def test_multiple_locations(client, db, sample_location):
    for i in range(10):
        sample_location["tst"] = 1711440000 + i * 10
        client.post("/pub", json=sample_location)

    assert db.count_records() == 10
    assert db.count_unsynced() == 10
