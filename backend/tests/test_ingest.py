import json

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def test_valid_payload_returns_200_and_appends(client, settings):
    body = {"_type": "location", "tst": 1781100000, "lat": 40.7, "lon": -74.2}
    resp = client.post(
        "/ingest/owntracks",
        json=body,
        headers={"X-Limit-U": "justin", "X-Limit-D": "iphone"},
    )
    assert resp.status_code == 200
    assert resp.json() == []
    files = list((settings.data_dir / "raw" / "owntracks").glob("*.jsonl"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text().splitlines()[0])
    assert rec["payload"] == body
    assert rec["user"] == "justin"
    assert rec["device"] == "iphone"
    assert rec["received_at"].endswith("+00:00")


def test_malformed_body_returns_200_and_is_kept(client, settings):
    resp = client.post(
        "/ingest/owntracks",
        content=b"\x00not json at all",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    files = list((settings.data_dir / "raw" / "owntracks_malformed").glob("*.jsonl"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text().splitlines()[0])
    assert "not json at all" in rec["raw"]


def test_store_failure_still_returns_200(client, monkeypatch):
    from backend.storage.raw import RawStore

    def boom(self, *a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(RawStore, "append", boom)
    resp = client.post("/ingest/owntracks", json={"_type": "location"})
    assert resp.status_code == 200


def test_store_failure_logs_context(client, monkeypatch, caplog):
    from backend.storage.raw import RawStore

    def boom(self, *a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(RawStore, "append", boom)
    with caplog.at_level("ERROR"):
        resp = client.post("/ingest/owntracks", json={"_type": "location"})
    assert resp.status_code == 200
    assert "data may be lost" in caplog.text
    assert "location" in caplog.text  # body prefix included
