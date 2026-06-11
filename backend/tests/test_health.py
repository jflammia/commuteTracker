from fastapi.testclient import TestClient

from backend.app import create_app
from backend.health.ingestion import ingestion_snapshot
from backend.storage.raw import RawStore


def test_snapshot_empty_system(settings):
    snap = ingestion_snapshot(settings, now_iso="2026-06-10T12:00:00+00:00")
    assert snap == {
        "last_event_at": None,
        "age_seconds": None,
        "today_event_count": 0,
        "raw_backlog_days": 0,
    }


def test_snapshot_counts_and_backlog(settings):
    store = RawStore(settings.data_dir)
    store.append("owntracks", {"received_at": "2026-06-08T01:00:00+00:00"})
    store.append("owntracks", {"received_at": "2026-06-10T11:00:00+00:00"})
    store.append("owntracks", {"received_at": "2026-06-10T11:30:00+00:00"})
    snap = ingestion_snapshot(settings, now_iso="2026-06-10T12:00:00+00:00")
    assert snap["last_event_at"] == "2026-06-10T11:30:00+00:00"
    assert snap["age_seconds"] == 1800
    assert snap["today_event_count"] == 2
    assert snap["raw_backlog_days"] == 1  # 06-08 closed but unarchived


def test_endpoint(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        resp = client.get("/api/health/ingestion")
    assert resp.status_code == 200
    assert resp.json()["today_event_count"] == 0


def test_backlog_includes_malformed_stream(settings):
    store = RawStore(settings.data_dir)
    store.append(
        "owntracks",
        {"received_at": "2026-06-08T01:00:00+00:00", "raw": "junk"},
        malformed=True,
    )
    snap = ingestion_snapshot(settings, now_iso="2026-06-10T12:00:00+00:00")
    assert snap["raw_backlog_days"] == 1


def test_torn_last_line_degrades_gracefully(settings):
    store = RawStore(settings.data_dir)
    store.append("owntracks", {"received_at": "2026-06-10T11:00:00+00:00"})
    today_file = settings.data_dir / "raw" / "owntracks" / "2026-06-10.jsonl"
    with open(today_file, "a", encoding="utf-8") as f:
        f.write('{"received_at":"2026-06-10T11:3')  # torn write, no newline
    snap = ingestion_snapshot(settings, now_iso="2026-06-10T12:00:00+00:00")
    assert snap["last_event_at"] == "2026-06-10T11:00:00+00:00"  # falls back
    assert snap["today_event_count"] == 2  # torn line still counted
