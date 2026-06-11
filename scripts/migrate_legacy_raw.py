"""One-time migration: legacy SQLite location_records → new raw JSONL layout.

Usage:
    python -m scripts.migrate_legacy_raw data/commute_tracker.db [CT_DATA_DIR]

After running, the normal archiver converts the day files to Parquet/S3.
Idempotency: run against an EMPTY data_dir only — the script refuses to
append into a raw dir that already has files (would duplicate events).
"""

import json
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from backend.storage.raw import RawStore


def record_from_row(received_at: str, user: str, device: str, payload_text: str) -> dict:
    payload = json.loads(payload_text)
    tst = payload.get("tst")
    if isinstance(tst, (int, float)):
        ts = datetime.fromtimestamp(tst, tz=UTC)
    else:
        ts = datetime.fromisoformat(received_at.replace(" ", "T")).replace(tzinfo=UTC)
    return {
        "received_at": ts.isoformat(),
        "user": user,
        "device": device,
        "payload": payload,
    }


def migrate(db_path: Path, data_dir: Path) -> dict:
    raw_dir = data_dir / "raw" / "owntracks"
    if raw_dir.exists() and any(raw_dir.glob("*.jsonl")):
        raise SystemExit(f"refusing: {raw_dir} already contains raw files")

    store = RawStore(data_dir)
    per_day: Counter = Counter()
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT received_at, user, device, payload FROM location_records ORDER BY id"
        )
        total = 0
        for received_at, user, device, payload_text in rows:
            try:
                rec = record_from_row(received_at, user, device, payload_text)
                store.append("owntracks", rec)
                per_day[rec["received_at"][:10]] += 1
            except (json.JSONDecodeError, ValueError):
                store.append(
                    "owntracks",
                    {"received_at": f"{received_at[:10]}T00:00:00+00:00", "raw": payload_text},
                    malformed=True,
                )
            total += 1
    finally:
        con.close()
    return {"total": total, "per_day": dict(sorted(per_day.items()))}


if __name__ == "__main__":
    db = Path(sys.argv[1])
    data_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data_v2")
    report = migrate(db, data_dir)
    print(json.dumps(report, indent=2))
