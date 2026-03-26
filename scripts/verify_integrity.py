#!/usr/bin/env python3
"""Verify SHA256 checksums for all raw data files.

Usage:
    python scripts/verify_integrity.py [raw-data-dir]
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.storage.raw_store import compute_sha256


def main() -> None:
    raw_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "raw"

    if not raw_dir.exists():
        print(f"Raw data directory not found: {raw_dir}")
        sys.exit(1)

    jsonl_files = sorted(raw_dir.rglob("*.jsonl"))
    if not jsonl_files:
        print("No JSONL files found.")
        return

    ok = 0
    fail = 0
    missing = 0

    for jsonl in jsonl_files:
        checksum_file = jsonl.with_suffix(jsonl.suffix + ".sha256")
        if not checksum_file.exists():
            print(f"  MISSING  {jsonl.relative_to(raw_dir)}")
            missing += 1
            continue

        expected = checksum_file.read_text().split()[0]
        actual = compute_sha256(jsonl)
        if actual == expected:
            print(f"  OK       {jsonl.relative_to(raw_dir)}")
            ok += 1
        else:
            print(f"  FAIL     {jsonl.relative_to(raw_dir)}")
            fail += 1

    print(f"\nResults: {ok} ok, {fail} failed, {missing} missing checksum")
    if fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
