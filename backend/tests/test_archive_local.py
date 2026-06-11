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
