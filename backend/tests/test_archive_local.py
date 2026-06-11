import json

import duckdb

from backend.storage.archive import Archiver
from backend.storage.raw import RawStore


def _seed(settings):
    store = RawStore(settings.data_dir)
    for day, n in (("2026-06-08", 2), ("2026-06-09", 3), ("2026-06-10", 1)):
        for i in range(n):
            store.append(
                "owntracks",
                {
                    "received_at": f"{day}T0{i}:00:00+00:00",
                    "user": "justin",
                    "device": "iphone",
                    "payload": {"_type": "location", "tst": i},
                },
            )
    return store


def test_archives_closed_days_to_hive_parquet(settings):
    _seed(settings)
    archiver = Archiver(settings)
    results = archiver.run(today="2026-06-10")
    assert [r.day for r in results] == ["2026-06-08", "2026-06-09"]
    assert all(r.ok for r in results)
    p8 = (
        settings.data_dir
        / "archive"
        / "owntracks"
        / "year=2026"
        / "month=06"
        / "day=08"
        / "data.parquet"
    )
    assert p8.exists()
    rows = duckdb.sql(f"SELECT count(*) c, min(payload) FROM read_parquet('{p8}')").fetchone()
    assert rows[0] == 2
    assert json.loads(rows[1])["_type"] == "location"


def test_raw_file_removed_after_local_archive(settings):
    _seed(settings)
    Archiver(settings).run(today="2026-06-10")
    raw_dir = settings.data_dir / "raw" / "owntracks"
    assert [p.name for p in sorted(raw_dir.glob("*.jsonl"))] == ["2026-06-10.jsonl"]


def test_idempotent_rerun(settings):
    _seed(settings)
    a = Archiver(settings)
    a.run(today="2026-06-10")
    results = a.run(today="2026-06-10")
    assert results == []  # nothing left to do


def test_today_file_untouched(settings):
    _seed(settings)
    Archiver(settings).run(today="2026-06-10")
    assert (settings.data_dir / "raw" / "owntracks" / "2026-06-10.jsonl").exists()


def test_malformed_line_fails_day_and_keeps_raw(settings):
    _seed(settings)
    bad_day = settings.data_dir / "raw" / "owntracks" / "2026-06-08.jsonl"
    with open(bad_day, "a", encoding="utf-8") as f:
        f.write("{this is not json\n")
    results = Archiver(settings).run(today="2026-06-10")
    by_day = {r.day: r for r in results if r.stream == "owntracks"}
    assert by_day["2026-06-08"].ok is False
    assert bad_day.exists()  # raw kept
    assert by_day["2026-06-09"].ok is True  # other days unaffected
    # rerun: still failing, still kept (permanent backlog until fixed manually)
    rerun = Archiver(settings).run(today="2026-06-10")
    assert [r for r in rerun if r.stream == "owntracks"][0].ok is False
    assert bad_day.exists()


def test_corrupt_parquet_from_crashed_run_is_overwritten(settings):
    _seed(settings)
    pq = (
        settings.data_dir
        / "archive"
        / "owntracks"
        / "year=2026"
        / "month=06"
        / "day=08"
        / "data.parquet"
    )
    pq.parent.mkdir(parents=True, exist_ok=True)
    pq.write_bytes(b"corrupt partial write")
    results = Archiver(settings).run(today="2026-06-10")
    assert all(r.ok for r in results)
    rows = duckdb.sql(f"SELECT count(*) FROM read_parquet('{pq}')").fetchone()
    assert rows[0] == 2


def test_empty_day_file_archives_cleanly(settings):
    raw = settings.data_dir / "raw" / "owntracks" / "2026-06-07.jsonl"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.touch()
    results = Archiver(settings).run(today="2026-06-10")
    r = [x for x in results if x.day == "2026-06-07"][0]
    assert r.ok is True
    assert r.rows == 0
    assert not raw.exists()


def test_malformed_stream_uses_raw_field(settings):
    store = RawStore(settings.data_dir)
    store.append(
        "owntracks",
        {"received_at": "2026-06-08T00:00:00+00:00", "raw": "\x00garbage bytes"},
        malformed=True,
    )
    results = Archiver(settings).run(today="2026-06-10")
    r = [x for x in results if x.stream == "owntracks_malformed"][0]
    assert r.ok is True
    pq = (
        settings.data_dir
        / "archive"
        / "owntracks_malformed"
        / "year=2026"
        / "month=06"
        / "day=08"
        / "data.parquet"
    )
    payload = duckdb.sql(f"SELECT payload FROM read_parquet('{pq}')").fetchone()[0]
    assert json.loads(payload) == "\x00garbage bytes"


def test_archiver_covers_dynamic_streams(settings):
    store = RawStore(settings.data_dir)
    store.append("rt_path", {"received_at": "2026-06-08T00:00:00+00:00", "payload": {"x": 1}})
    results = Archiver(settings).run(today="2026-06-10")
    assert [r.stream for r in results] == ["rt_path"]
    assert results[0].ok
    pq = (
        settings.data_dir
        / "archive"
        / "rt_path"
        / "year=2026"
        / "month=06"
        / "day=08"
        / "data.parquet"
    )
    assert pq.exists()


def test_unicode_payload_round_trips(settings):
    store = RawStore(settings.data_dir)
    rec_payload = {"note": "café ☕", "nested": {"x": [1, 2]}}
    store.append(
        "owntracks",
        {
            "received_at": "2026-06-08T00:00:00+00:00",
            "user": "j",
            "device": "d",
            "payload": rec_payload,
        },
    )
    Archiver(settings).run(today="2026-06-10")
    pq = (
        settings.data_dir
        / "archive"
        / "owntracks"
        / "year=2026"
        / "month=06"
        / "day=08"
        / "data.parquet"
    )
    payload = duckdb.sql(f"SELECT payload FROM read_parquet('{pq}')").fetchone()[0]
    assert json.loads(payload) == rec_payload
