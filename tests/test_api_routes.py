"""Tests for the REST API routes."""

import pytest
from fastapi.testclient import TestClient

from src.api.routes import router, set_service
from src.api.service import CommuteService
from src.storage.database import Database
from fastapi import FastAPI


@pytest.fixture
def client(tmp_path):
    """Create a test client with a temp database."""
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    db = Database(db_url)
    db.create_tables()
    derived_dir = tmp_path / "derived"
    derived_dir.mkdir()
    service = CommuteService(db=db, derived_dir=derived_dir)
    set_service(service)

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_api_health(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_api_list_commutes_empty(client):
    resp = client.get("/api/v1/commutes")
    assert resp.status_code == 200
    assert resp.json() == []


def test_api_get_commute_404(client):
    resp = client.get("/api/v1/commutes/nonexistent")
    assert resp.status_code == 404


def test_api_stats_empty(client):
    resp = client.get("/api/v1/stats")
    assert resp.status_code == 200


def test_api_raw_stats(client):
    resp = client.get("/api/v1/raw/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_records" in data


def test_api_add_and_list_labels(client):
    resp = client.post("/api/v1/labels", json={
        "commute_id": "test_001",
        "segment_id": 0,
        "original_mode": "driving",
        "corrected_mode": "train",
        "notes": "test",
    })
    assert resp.status_code == 200
    assert resp.json()["corrected_mode"] == "train"

    resp = client.get("/api/v1/labels")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_api_labels_filter(client):
    client.post("/api/v1/labels", json={
        "commute_id": "c1",
        "segment_id": 0,
        "original_mode": "driving",
        "corrected_mode": "train",
    })
    resp = client.get("/api/v1/labels?commute_id=c1")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = client.get("/api/v1/labels?commute_id=other")
    assert resp.status_code == 200
    assert len(resp.json()) == 0


def test_api_export_labels(client):
    resp = client.get("/api/v1/labels/export")
    assert resp.status_code == 200


def test_api_rebuild_dry_run(client):
    resp = client.post("/api/v1/rebuild", json={
        "since": "2026-01-01",
        "until": "2026-01-31",
        "dry_run": True,
    })
    assert resp.status_code == 200
    assert resp.json()["dry_run"] is True


def test_api_daily_empty(client):
    resp = client.get("/api/v1/daily/2026-01-01")
    assert resp.status_code == 200
    assert resp.json() == []


def test_api_dates_empty(client):
    resp = client.get("/api/v1/dates")
    assert resp.status_code == 200
    assert resp.json() == []


def test_api_raw_count(client):
    resp = client.get("/api/v1/raw/count")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_api_raw_count_filtered(client):
    resp = client.get("/api/v1/raw/count?since=2026-01-01&user=test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["filters"]["since"] == "2026-01-01"


def test_api_corrections_empty(client):
    resp = client.get("/api/v1/labels/corrections")
    assert resp.status_code == 200
    assert resp.json() == {}


def test_api_bulk_labels(client):
    labels = [
        {"commute_id": "c1", "segment_id": 0, "original_mode": "driving", "corrected_mode": "train"},
        {"commute_id": "c1", "segment_id": 1, "original_mode": "stationary", "corrected_mode": "waiting"},
    ]
    resp = client.post("/api/v1/labels/bulk", json=labels)
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    # Verify corrections map
    resp = client.get("/api/v1/labels/corrections")
    corrections = resp.json()
    assert corrections["c1:0"] == "train"
    assert corrections["c1:1"] == "waiting"


# ── Label Intelligence Routes ────────────────────────────────────────────────


def test_api_analyze_segment_404(client):
    resp = client.get("/api/v1/labels/analyze/nonexistent/0")
    assert resp.status_code == 404


def test_api_review_commute_404(client):
    resp = client.get("/api/v1/labels/review/nonexistent")
    assert resp.status_code == 404


def test_api_review_recent_no_data(client):
    resp = client.get("/api/v1/labels/review?n=5")
    assert resp.status_code == 404


def test_api_apply_corrections_empty(client):
    resp = client.post("/api/v1/labels/apply", json={
        "corrections": [],
        "min_confidence": 0.7,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["applied_count"] == 0
    assert data["skipped_count"] == 0


def test_api_apply_corrections_with_threshold(client):
    resp = client.post("/api/v1/labels/apply", json={
        "corrections": [
            {
                "commute_id": "c1",
                "segment_id": 0,
                "original_mode": "driving",
                "corrected_mode": "train",
                "confidence": 0.9,
            },
            {
                "commute_id": "c1",
                "segment_id": 1,
                "original_mode": "stationary",
                "corrected_mode": "waiting",
                "confidence": 0.4,
            },
        ],
        "min_confidence": 0.7,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["applied_count"] == 1
    assert data["skipped_count"] == 1
