"""Per-source freshness, derived from the raw stream tail — no extra state."""

import json
from datetime import datetime

from backend.config import Settings
from backend.sources.framework import sources_from_settings
from backend.sources.njt import njt_specs_from_settings
from backend.storage.raw import RawStore


def sources_snapshot(settings: Settings, *, now_iso: str) -> list[dict]:
    now = datetime.fromisoformat(now_iso)
    today = now_iso[:10]
    store = RawStore(settings.data_dir)
    out = []
    all_specs = list(sources_from_settings(settings)) + list(njt_specs_from_settings(settings))
    for spec in all_specs:
        last_at = None
        last_status = None
        # Note: only reads today's day file (UTC). A daily source can show
        # last_fetch_at=None from UTC midnight until its next poll.
        day_file = store.day_file(spec.name, today)
        if day_file.exists():
            lines = day_file.read_text(encoding="utf-8").splitlines()
            for line in reversed(lines):
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # torn trailing write
                last_at = rec.get("received_at")
                last_status = rec.get("payload", {}).get("status")
                break
        age = None
        if last_at is not None:
            age = int((now - datetime.fromisoformat(last_at)).total_seconds())
        out.append(
            {
                "name": spec.name,
                "last_fetch_at": last_at,
                "age_seconds": age,
                "last_status": last_status,
            }
        )
    return out
