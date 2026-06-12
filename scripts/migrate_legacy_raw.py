"""One-time migration: legacy SQLite location_records → new raw JSONL layout.

Usage:
    python -m scripts.migrate_legacy_raw data/commute_tracker.db [CT_DATA_DIR]

After running, the normal archiver converts the day files to Parquet/S3.
Idempotency: run against an EMPTY data_dir only — the script refuses to
append into a raw dir that already has files (would duplicate events).
"""

import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path


def record_from_row(received_at: str, user: str, device: str, payload_text: str) -> dict:
    payload = json.loads(payload_text)
    if not isinstance(payload, dict):
        raise ValueError(f"payload is not a JSON object: {payload_text[:80]}")
    tst = payload.get("tst")
    if isinstance(tst, (int, float)) and not isinstance(tst, bool):
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
    for stream_dir_name in ("owntracks", "owntracks_malformed"):
        d = data_dir / "raw" / stream_dir_name
        if d.exists() and any(d.glob("*.jsonl")):
            raise SystemExit(f"refusing: {d} already contains raw files")

    per_day: Counter = Counter()
    # Buffer JSON lines by (stream, day) so each day-file is written exactly once
    # (one fsync per file) instead of two fsyncs per record via RawStore.append.
    buckets: dict[tuple[str, str], list[str]] = defaultdict(list)
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT received_at, user, device, payload FROM location_records ORDER BY id"
        )
        total = 0
        malformed_count = 0
        for received_at, user, device, payload_text in rows:
            try:
                rec = record_from_row(received_at, user, device, payload_text)
                day = rec["received_at"][:10]
                line = json.dumps(rec, separators=(",", ":"), ensure_ascii=False)
                buckets[("owntracks", day)].append(line)
                per_day[day] += 1
            except (json.JSONDecodeError, ValueError, OverflowError, OSError):
                day = (received_at or "")[:10] or "1970-01-01"
                rec = {"received_at": f"{day}T00:00:00+00:00", "raw": payload_text}
                line = json.dumps(rec, separators=(",", ":"), ensure_ascii=False)
                buckets[("owntracks_malformed", day)].append(line)
                malformed_count += 1
            total += 1
    finally:
        con.close()

    raw_root = data_dir / "raw"
    touched_dirs: set[Path] = set()
    for (stream, day), lines in buckets.items():
        path = raw_root / stream / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
            f.flush()
            os.fsync(f.fileno())
        touched_dirs.add(path.parent)
    for d in touched_dirs:
        dirfd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)

    return {
        "total": total,
        "migrated": total - malformed_count,
        "malformed": malformed_count,
        "per_day": dict(sorted(per_day.items())),
    }


if __name__ == "__main__":
    db = Path(sys.argv[1])
    data_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data_v2")
    report = migrate(db, data_dir)
    print(json.dumps(report, indent=2))
