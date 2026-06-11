import json
import sqlite3

import pytest

from scripts.migrate_legacy_raw import migrate, record_from_row


def test_record_uses_payload_tst_for_received_at():
    row = ("2026-03-27 04:42:07", "justin", "iphone", '{"_type":"location","tst":1742400000}')
    rec = record_from_row(*row)
    assert rec["received_at"] == "2025-03-19T16:00:00+00:00"  # 1742400000 epoch
    assert rec["payload"]["tst"] == 1742400000
    assert rec["user"] == "justin"


def test_record_falls_back_to_received_at():
    row = ("2026-03-27 04:42:07", "justin", "iphone", '{"_type":"status"}')
    rec = record_from_row(*row)
    assert rec["received_at"] == "2026-03-27T04:42:07+00:00"


def test_migrate_writes_day_files_and_reports(settings, tmp_path):
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE location_records (id INTEGER PRIMARY KEY, received_at TEXT, "
        "msg_type TEXT, user TEXT, device TEXT, payload TEXT, s3_synced_at TEXT)"
    )
    con.executemany(
        "INSERT INTO location_records (received_at, msg_type, user, device, payload) "
        "VALUES (?, 'location', 'justin', 'iphone', ?)",
        [
            ("2026-03-27 04:42:07", '{"_type":"location","tst":1742400000}'),
            ("2026-03-27 04:42:08", '{"_type":"location","tst":1742400060}'),
            ("2026-03-27 04:42:09", '{"_type":"location","tst":1742500000}'),
        ],
    )
    con.commit()
    con.close()

    report = migrate(db, settings.data_dir)
    assert report["total"] == 3
    assert report["malformed"] == 0
    day_files = sorted((settings.data_dir / "raw" / "owntracks").glob("*.jsonl"))
    assert [p.name for p in day_files] == ["2025-03-19.jsonl", "2025-03-20.jsonl"]
    first = json.loads(day_files[0].read_text().splitlines()[0])
    assert first["payload"]["_type"] == "location"
    assert report["per_day"]["2025-03-19"] == 2


def test_non_dict_payload_goes_to_malformed(settings, tmp_path):
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE location_records (id INTEGER PRIMARY KEY, received_at TEXT, "
        "msg_type TEXT, user TEXT, device TEXT, payload TEXT, s3_synced_at TEXT)"
    )
    con.executemany(
        "INSERT INTO location_records (received_at, msg_type, user, device, payload) "
        "VALUES (?, 'location', 'justin', 'iphone', ?)",
        [
            ("2026-03-27 04:42:07", '{"_type":"location","tst":1742400000}'),
            ("2026-03-27 04:42:08", "null"),
            ("2026-03-27 04:42:09", "{not json"),
        ],
    )
    con.commit()
    con.close()
    report = migrate(db, settings.data_dir)
    assert report["total"] == 3
    assert report["migrated"] == 1
    assert report["malformed"] == 2
    malformed_files = list((settings.data_dir / "raw" / "owntracks_malformed").glob("*.jsonl"))
    assert len(malformed_files) == 1
    lines = malformed_files[0].read_text().splitlines()
    assert len(lines) == 2


def test_refuses_rerun_when_only_malformed_exists(settings, tmp_path):
    from backend.storage.raw import RawStore

    RawStore(settings.data_dir).append(
        "owntracks",
        {"received_at": "2026-03-27T00:00:00+00:00", "raw": "x"},
        malformed=True,
    )
    db = tmp_path / "legacy.db"
    sqlite3.connect(db).close()
    with pytest.raises(SystemExit, match="refusing"):
        migrate(db, settings.data_dir)
