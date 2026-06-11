import json
import sqlite3

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
    day_files = sorted((settings.data_dir / "raw" / "owntracks").glob("*.jsonl"))
    assert [p.name for p in day_files] == ["2025-03-19.jsonl", "2025-03-20.jsonl"]
    first = json.loads(day_files[0].read_text().splitlines()[0])
    assert first["payload"]["_type"] == "location"
    assert report["per_day"]["2025-03-19"] == 2
