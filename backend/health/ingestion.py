"""Ingestion health derived purely from the filesystem — no state to corrupt.

A closed day-file still sitting in raw/ means the archiver hasn't succeeded
on it yet: that IS the backlog signal (cleanup only happens after verified
upload).
"""

import json
from datetime import datetime

from backend.config import Settings
from backend.storage.raw import RawStore


def ingestion_snapshot(settings: Settings, *, now_iso: str) -> dict:
    now = datetime.fromisoformat(now_iso)
    today = now_iso[:10]
    store = RawStore(settings.data_dir)
    today_file = store.day_file("owntracks", today)

    last_event_at = None
    today_count = 0
    if today_file.exists():
        # Full-file read is intentional: today_event_count needs the line count anyway.
        lines = today_file.read_text(encoding="utf-8").splitlines()
        today_count = len(lines)
        for line in reversed(lines):
            try:
                last_event_at = json.loads(line).get("received_at")
                break
            except json.JSONDecodeError:
                continue  # torn trailing write — fall back to previous line

    age = None
    if last_event_at is not None:
        age = int((now - datetime.fromisoformat(last_event_at)).total_seconds())

    backlog = sum(len(store.closed_day_files(s, today=today)) for s in store.streams())
    return {
        "last_event_at": last_event_at,
        "age_seconds": age,
        "today_event_count": today_count,
        "raw_backlog_days": backlog,
    }
