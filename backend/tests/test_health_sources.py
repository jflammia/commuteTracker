import dataclasses

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.health.sources import sources_snapshot
from backend.storage.raw import RawStore


def _src_settings(settings):
    return dataclasses.replace(
        settings, path_rt_url="https://e.com/rt", path_gtfs_url="https://e.com/p.zip"
    )


def test_snapshot_reports_configured_sources_never_fetched(settings):
    s = _src_settings(settings)
    snap = sources_snapshot(s, now_iso="2026-06-10T12:00:00+00:00")
    assert snap == [
        {"name": "gtfs_path", "last_fetch_at": None, "age_seconds": None, "last_status": None},
        {"name": "rt_path", "last_fetch_at": None, "age_seconds": None, "last_status": None},
    ]


def test_snapshot_reads_latest_event(settings):
    s = _src_settings(settings)
    store = RawStore(s.data_dir)
    store.append(
        "rt_path", {"received_at": "2026-06-10T11:00:00+00:00", "payload": {"status": 200}}
    )
    store.append(
        "rt_path", {"received_at": "2026-06-10T11:59:00+00:00", "payload": {"status": 503}}
    )
    snap = sources_snapshot(s, now_iso="2026-06-10T12:00:00+00:00")
    rt = [x for x in snap if x["name"] == "rt_path"][0]
    assert rt["last_fetch_at"] == "2026-06-10T11:59:00+00:00"
    assert rt["age_seconds"] == 60
    assert rt["last_status"] == 503


def test_endpoint(settings, monkeypatch):
    # Tests must never touch the network: neuter the poll loop's fetch before
    # the lifespan starts the pollers for the configured URLs.
    from backend.sources import poller as poller_mod

    async def no_fetch(client, spec, store, state):
        return True

    monkeypatch.setattr(poller_mod, "fetch_once", no_fetch)
    app = create_app(_src_settings(settings))
    with TestClient(app) as client:
        resp = client.get("/api/health/sources")
    assert resp.status_code == 200
    assert {x["name"] for x in resp.json()} == {"gtfs_path", "rt_path"}
