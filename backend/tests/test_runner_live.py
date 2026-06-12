import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.engine.checkpoint import RebuildCheckpoint
from backend.engine.rebuild import rebuild
from backend.engine.runner import EngineRunner
from backend.tests.synth import commute


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c, app


def test_live_points_produce_a_trip(client, settings):
    c, app = client
    pts, _, _ = commute()
    for pt in pts:
        payload = {
            "_type": "location",
            "tst": pt.ts,
            "lat": pt.lat,
            "lon": pt.lon,
            "acc": pt.accuracy_m,
        }
        resp = c.post("/ingest/owntracks", json=payload)
        assert resp.status_code == 200
    trips = app.state.runner.store.list_trips()
    assert len(trips) == 1
    assert trips[0]["distance_m"] > 15000


def test_engine_failure_never_affects_200(client, monkeypatch):
    c, app = client

    def boom(payload):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(app.state.runner, "process_payload", boom)
    resp = c.post("/ingest/owntracks", json={"_type": "location", "tst": 1, "lat": 1, "lon": 2})
    assert resp.status_code == 200


def test_live_trip_close_triggers_matching(settings):
    import base64
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from backend.engine.machine import TripEngine
    from backend.engine.params import EngineParams
    from backend.engine.types import TripClosed
    from backend.storage.raw import RawStore
    from backend.tests.gtfs_fixture import build_gtfs_zip

    pts, _, _ = commute()
    eng = TripEngine(EngineParams(), geofences=[])
    closed = []
    for pt in pts:
        closed.extend(e for e in eng.process(pt) if isinstance(e, TripClosed))
    seg = next(s for s in closed[0].segments if s.mode == "vehicle")
    start = next(p for p in closed[0].points if p.ts >= seg.start_ts)
    end = max((p for p in closed[0].points if p.ts <= seg.end_ts), key=lambda p: p.ts)
    ny = ZoneInfo("America/New_York")

    def hms(epoch):
        dt = datetime.fromtimestamp(epoch, ny)
        return f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"

    z = build_gtfs_zip(
        [("S1", "Alpha", start.lat, start.lon), ("S2", "Beta", end.lat, end.lon)],
        [("T1", "WK", "Beta-bound", [("S1", hms(start.ts)), ("S2", hms(end.ts))])],
    )
    RawStore(settings.data_dir).append(
        "gtfs_path",
        {
            "received_at": "2026-06-09T05:00:00+00:00",
            "payload": {"url": "u", "status": 200, "b64": base64.b64encode(z).decode()},
        },
    )
    app = create_app(settings)
    with TestClient(app) as c:
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
        trips = app.state.runner.store.list_trips()
        assert len(trips) == 1
        matches = app.state.runner.store.matches_for_trip(trips[0]["trip_id"])
        assert [m["gtfs_trip_id"] for m in matches] == ["T1"]


def test_startup_rebuild_recovers_history(settings):
    # pre-existing raw data is replayed into the derived store at startup
    from backend.storage.raw import RawStore

    store = RawStore(settings.data_dir)
    pts, _, _ = commute()
    for i, pt in enumerate(pts):
        # ascending received_at: ORDER BY received_at must reproduce tst order
        # (DuckDB sort is not stable for equal keys)
        store.append(
            "owntracks",
            {
                "received_at": f"2026-06-09T{i // 3600:02d}:{(i // 60) % 60:02d}:{i % 60:02d}+00:00",
                "user": "j",
                "device": "d",
                "payload": {
                    "_type": "location",
                    "tst": pt.ts,
                    "lat": pt.lat,
                    "lon": pt.lon,
                    "acc": pt.accuracy_m,
                },
            },
        )
    app = create_app(settings)
    with TestClient(app):
        assert len(app.state.runner.store.list_trips()) == 1


def _payload(pt):
    return {"_type": "location", "tst": int(pt.ts), "lat": pt.lat, "lon": pt.lon}


def _received_at(pt):
    return datetime.fromtimestamp(pt.ts, tz=UTC).isoformat()


def _write_raw(data_dir, points):
    """Write Points as owntracks raw JSONL day-files (received_at from ts).
    Mirrors test_incremental_rebuild._write_raw so a restart can replay them."""
    by_day = {}
    for p in points:
        iso = _received_at(p)
        rec = {
            "received_at": iso,
            "user": "justin",
            "device": "iphone",
            "payload": {**_payload(p), "acc": p.accuracy_m},
        }
        by_day.setdefault(iso[:10], []).append(json.dumps(rec))
    d = data_dir / "raw" / "owntracks"
    d.mkdir(parents=True, exist_ok=True)
    for day, lines in by_day.items():
        (d / f"{day}.jsonl").write_text("\n".join(lines) + "\n")


def test_process_payload_checkpoints_on_trip_close(settings):
    runner = EngineRunner.start(settings)
    try:
        pts, _, _ = commute()
        n_trips_before = 0
        closing_received_at = None
        for pt in pts:
            runner.process_payload(_payload(pt), received_at=_received_at(pt))
            n_trips = len(runner.store.list_trips())
            if n_trips > n_trips_before:
                closing_received_at = _received_at(pt)
                n_trips_before = n_trips
        assert closing_received_at is not None, "no trip ever closed"

        cp_path = settings.data_dir / "derived" / "rebuild_checkpoint.json"
        assert cp_path.exists()
        cp = RebuildCheckpoint(settings.data_dir).load()
        assert cp is not None
        assert cp.hwm == closing_received_at
    finally:
        runner.close()


def test_close_persists_checkpoint(settings):
    runner = EngineRunner.start(settings)
    pts, _, _ = commute()
    fed = pts[:5]  # a few points, no full trip
    last_received_at = None
    for pt in fed:
        last_received_at = _received_at(pt)
        runner.process_payload(_payload(pt), received_at=last_received_at)
    runner.close()

    cp_path = settings.data_dir / "derived" / "rebuild_checkpoint.json"
    assert cp_path.exists()
    cp = RebuildCheckpoint(settings.data_dir).load()
    assert cp is not None
    assert cp.hwm == last_received_at


def test_restart_uses_incremental_and_is_stable(settings, tmp_path):
    pts, _, _ = commute()

    # Live: write raw (so a restart can replay it) and feed the runner live.
    _write_raw(settings.data_dir, pts)
    runner = EngineRunner.start(settings)
    for pt in pts:
        runner.process_payload(_payload(pt), received_at=_received_at(pt))
    trips_live = runner.store.list_trips()
    assert len(trips_live) == 1
    runner.close()  # persists a checkpoint

    # Restart: start() uses incremental=True; a checkpoint now exists.
    runner2 = EngineRunner.start(settings)
    try:
        trips_restart = runner2.store.list_trips()
    finally:
        runner2.close()

    # A from-scratch full rebuild over the same archive, in a clean data_dir.
    full_dir = tmp_path / "full"
    _write_raw(full_dir, pts)
    full_settings = type(settings)(
        data_dir=full_dir,
        s3_bucket=None,
        s3_prefix="commute-tracker",
        s3_region=None,
        passthrough_url=None,
        archive_hour_utc=6,
    )
    _, full_store, _ = rebuild(full_settings)
    trips_full = full_store.list_trips()

    assert trips_restart == trips_live
    assert trips_restart == trips_full
