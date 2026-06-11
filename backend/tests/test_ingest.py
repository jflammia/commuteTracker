import base64
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_settings(settings):
    return replace(settings, owntracks_username="owntracks", owntracks_password="secret-pw-123")


@pytest.fixture
def auth_client(auth_settings):
    app = create_app(auth_settings)
    with TestClient(app) as c:
        yield c


def _basic_auth_header(user, pw):
    token = base64.b64encode(f"{user}:{pw}".encode()).decode("ascii")
    return {"Authorization": "Basic " + token}


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


def test_pub_alias_accepts_owntracks_and_returns_200(client, settings):
    body = {"_type": "location", "tst": 1781100000, "lat": 40.7, "lon": -74.2}
    resp = client.post("/pub", json=body, headers={"X-Limit-U": "justin", "X-Limit-D": "iphone"})
    assert resp.status_code == 200
    assert resp.json() == []
    # same raw stream as /ingest/owntracks — a point arrived
    import json as _json

    files = list((settings.data_dir / "raw" / "owntracks").glob("*.jsonl"))
    assert len(files) == 1
    rec = _json.loads(files[0].read_text().splitlines()[0])
    assert rec["payload"] == body


def test_pub_and_ingest_paths_share_one_stream(client, settings):
    client.post("/pub", json={"_type": "location", "tst": 1, "lat": 1.0, "lon": 2.0})
    client.post("/ingest/owntracks", json={"_type": "location", "tst": 2, "lat": 1.0, "lon": 2.0})
    files = list((settings.data_dir / "raw" / "owntracks").glob("*.jsonl"))
    assert len(files) == 1
    assert len(files[0].read_text().splitlines()) == 2


def test_auth_valid_creds_writes(auth_client, auth_settings):
    body = {"_type": "location", "tst": 1781100000, "lat": 40.7, "lon": -74.2}
    resp = auth_client.post(
        "/pub",
        json=body,
        headers={
            **_basic_auth_header("owntracks", "secret-pw-123"),
            "X-Limit-U": "justin",
            "X-Limit-D": "iphone",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == []
    files = list((auth_settings.data_dir / "raw" / "owntracks").glob("*.jsonl"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text().splitlines()[0])
    assert rec["payload"] == body


def test_auth_missing_header_returns_200_but_skips_write(auth_client, auth_settings, caplog):
    with caplog.at_level("WARNING"):
        resp = auth_client.post(
            "/pub",
            json={"_type": "location", "tst": 1, "lat": 1.0, "lon": 2.0},
            headers={"X-Limit-U": "justin", "X-Limit-D": "iphone"},
        )
    assert resp.status_code == 200
    assert resp.json() == []
    assert list((auth_settings.data_dir / "raw" / "owntracks").glob("*.jsonl")) == []
    assert "Unauthenticated /pub" in caplog.text


def test_auth_wrong_password_returns_200_but_skips_write(auth_client, auth_settings):
    resp = auth_client.post(
        "/pub",
        json={"_type": "location", "tst": 1, "lat": 1.0, "lon": 2.0},
        headers=_basic_auth_header("owntracks", "wrong-pw"),
    )
    assert resp.status_code == 200
    assert list((auth_settings.data_dir / "raw" / "owntracks").glob("*.jsonl")) == []


def test_auth_wrong_username_returns_200_but_skips_write(auth_client, auth_settings):
    resp = auth_client.post(
        "/pub",
        json={"_type": "location", "tst": 1, "lat": 1.0, "lon": 2.0},
        headers=_basic_auth_header("not-owntracks", "secret-pw-123"),
    )
    assert resp.status_code == 200
    assert list((auth_settings.data_dir / "raw" / "owntracks").glob("*.jsonl")) == []


def test_auth_applies_to_canonical_ingest_path(auth_client, auth_settings):
    resp = auth_client.post(
        "/ingest/owntracks",
        json={"_type": "location", "tst": 1, "lat": 1.0, "lon": 2.0},
    )
    assert resp.status_code == 200
    assert list((auth_settings.data_dir / "raw" / "owntracks").glob("*.jsonl")) == []


def test_auth_disabled_when_creds_unset_writes(client, settings):
    resp = client.post(
        "/pub",
        json={"_type": "location", "tst": 1, "lat": 1.0, "lon": 2.0},
    )
    assert resp.status_code == 200
    assert len(list((settings.data_dir / "raw" / "owntracks").glob("*.jsonl"))) == 1
