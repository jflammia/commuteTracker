"""Append-only JSONL storage for raw location data.

Design principles:
- Never modify or delete existing data
- One file per day: raw/YYYY/MM/YYYY-MM-DD.jsonl
- Generate SHA256 checksums for integrity verification
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def day_file_path(base_dir: str | Path, date: datetime) -> Path:
    """Return the JSONL file path for a given date."""
    base = Path(base_dir)
    return (
        base
        / f"{date.year}"
        / f"{date.month:02d}"
        / f"{date.year}-{date.month:02d}-{date.day:02d}.jsonl"
    )


def append_record(base_dir: str | Path, record: dict) -> Path:
    """Append a single JSON record to the appropriate day file.

    Adds `received_at` timestamp if not present. Returns the file path written to.
    """
    if "received_at" not in record:
        record["received_at"] = datetime.now(timezone.utc).isoformat()

    now = datetime.now(timezone.utc)
    path = day_file_path(base_dir, now)
    path.parent.mkdir(parents=True, exist_ok=True)

    line = json.dumps(record, separators=(",", ":")) + "\n"
    with open(path, "a") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())

    return path


def read_day_file(path: str | Path) -> list[dict]:
    """Read all records from a JSONL file."""
    path = Path(path)
    if not path.exists():
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def compute_sha256(path: str | Path) -> str:
    """Compute SHA256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def write_checksum(path: str | Path) -> Path:
    """Write a .sha256 sidecar file for the given data file."""
    path = Path(path)
    digest = compute_sha256(path)
    checksum_path = path.with_suffix(path.suffix + ".sha256")
    checksum_path.write_text(f"{digest}  {path.name}\n")
    return checksum_path


def verify_checksum(path: str | Path) -> bool:
    """Verify a file against its .sha256 sidecar. Returns True if valid."""
    path = Path(path)
    checksum_path = path.with_suffix(path.suffix + ".sha256")
    if not checksum_path.exists():
        raise FileNotFoundError(f"No checksum file: {checksum_path}")
    expected = checksum_path.read_text().split()[0]
    actual = compute_sha256(path)
    return actual == expected
