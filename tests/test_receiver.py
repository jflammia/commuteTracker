"""Tests for the FastAPI receiver endpoint."""

import json

import pytest
from fastapi.testclient import TestClient

from src.receiver.app import app


@pytest.fixture
def client():
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


def test_receive_location(client, sample_location, tmp_path, monkeypatch):
    monkeypatch.setattr("src.receiver.app.RAW_DATA_DIR", tmp_path)
    monkeypatch.setattr("src.storage.raw_store.os.fsync", lambda fd: None)

    resp = client.post(
        "/pub",
        json=sample_location,
        headers={"X-Limit-U": "testuser", "X-Limit-D": "testdevice"},
    )

    assert resp.status_code == 200
    assert resp.json() == []

    # Verify data was written
    jsonl_files = list(tmp_path.rglob("*.jsonl"))
    assert len(jsonl_files) == 1

    with open(jsonl_files[0]) as f:
        record = json.loads(f.readline())
    assert record["lat"] == 40.75
    assert record["_type"] == "location"
    assert record["_receiver_user"] == "testuser"
    assert record["_receiver_device"] == "testdevice"
    assert "received_at" in record


def test_receive_empty_body(client, monkeypatch, tmp_path):
    monkeypatch.setattr("src.receiver.app.RAW_DATA_DIR", tmp_path)

    resp = client.post("/pub", content=b"")
    assert resp.status_code == 200
    assert resp.json() == []

    # No file should be written
    jsonl_files = list(tmp_path.rglob("*.jsonl"))
    assert len(jsonl_files) == 0


def test_receive_invalid_json_still_200(client, monkeypatch, tmp_path):
    """OwnTracks data loss prevention: never return 4xx."""
    monkeypatch.setattr("src.receiver.app.RAW_DATA_DIR", tmp_path)

    resp = client.post(
        "/pub",
        content=b"not valid json",
        headers={"Content-Type": "application/json"},
    )

    # Must still return 200 to prevent OwnTracks from discarding data
    assert resp.status_code == 200
    assert resp.json() == []


def test_receive_transition(client, tmp_path, monkeypatch):
    monkeypatch.setattr("src.receiver.app.RAW_DATA_DIR", tmp_path)
    monkeypatch.setattr("src.storage.raw_store.os.fsync", lambda fd: None)

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

    jsonl_files = list(tmp_path.rglob("*.jsonl"))
    assert len(jsonl_files) == 1
    with open(jsonl_files[0]) as f:
        record = json.loads(f.readline())
    assert record["_type"] == "transition"
    assert record["event"] == "enter"


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "raw_data_dir" in data
    assert "s3_enabled" in data


def test_default_headers_when_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr("src.receiver.app.RAW_DATA_DIR", tmp_path)
    monkeypatch.setattr("src.storage.raw_store.os.fsync", lambda fd: None)

    resp = client.post("/pub", json={"_type": "location", "lat": 40.0, "lon": -74.0, "tst": 1})
    assert resp.status_code == 200

    jsonl_files = list(tmp_path.rglob("*.jsonl"))
    with open(jsonl_files[0]) as f:
        record = json.loads(f.readline())
    assert record["_receiver_user"] == "unknown"
    assert record["_receiver_device"] == "unknown"
