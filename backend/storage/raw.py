"""Append-only raw JSONL store. The write path must stay this simple."""

import json
import os
from pathlib import Path


class RawStore:
    def __init__(self, data_dir: Path):
        self._root = data_dir / "raw"

    def append(self, stream: str, record: dict, *, malformed: bool = False) -> Path:
        if malformed:
            stream = f"{stream}_malformed"
        day = record["received_at"][:10]  # ISO date prefix, UTC
        path = self._root / stream / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        return path

    def closed_day_files(self, stream: str, *, today: str) -> list[Path]:
        d = self._root / stream
        if not d.is_dir():
            return []
        return sorted(p for p in d.glob("*.jsonl") if p.stem < today)

    def day_file(self, stream: str, day: str) -> Path:
        return self._root / stream / f"{day}.jsonl"
