"""Append-only raw JSONL store. The write path must stay this simple."""

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _day_from_record(record: dict) -> str:
    """Return the YYYY-MM-DD date from record['received_at'], or today's UTC date on failure."""
    try:
        value = record["received_at"]
        if isinstance(value, str) and _ISO_DATE_RE.match(value[:10]):
            return value[:10]
    except (KeyError, TypeError):
        pass
    return datetime.now(UTC).strftime("%Y-%m-%d")


class RawStore:
    def __init__(self, data_dir: Path):
        self._root = data_dir / "raw"

    def append(self, stream: str, record: dict, *, malformed: bool = False) -> Path:
        """Append record as a JSONL line to the day file for the given stream.

        Day is taken from record['received_at'] (ISO-8601); falls back to current UTC date
        when absent or malformed. The record is always written as given.
        """
        if malformed:
            stream = f"{stream}_malformed"
        day = _day_from_record(record)
        path = self._root / stream / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        dirfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
        return path

    def closed_day_files(self, stream: str, *, today: str) -> list[Path]:
        d = self._root / stream
        if not d.is_dir():
            return []
        return sorted(p for p in d.glob("*.jsonl") if p.stem < today)

    def day_file(self, stream: str, day: str) -> Path:
        return self._root / stream / f"{day}.jsonl"
