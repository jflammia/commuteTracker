#!/usr/bin/env python3
"""Rebuild all derived data from raw JSONL files.

Usage:
    python scripts/rebuild_derived.py [raw-dir] [derived-dir]

This is the key durability guarantee: derived data is disposable
and can always be regenerated from the immutable raw files.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    raw_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "raw"
    derived_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else PROJECT_ROOT / "derived"

    if not raw_dir.exists():
        print(f"Raw data directory not found: {raw_dir}")
        sys.exit(1)

    jsonl_files = sorted(raw_dir.rglob("*.jsonl"))
    if not jsonl_files:
        print("No JSONL files found.")
        return

    print(f"Found {len(jsonl_files)} raw files to process.")
    print(f"Derived output: {derived_dir}")
    print()

    # TODO (Phase 2): Import and run the processing pipeline
    # from src.processing.pipeline import process_day
    # for jsonl in jsonl_files:
    #     process_day(jsonl, derived_dir)

    for jsonl in jsonl_files:
        print(f"  [stub] Would process: {jsonl.relative_to(raw_dir)}")

    print(f"\nDone. Processing pipeline will be implemented in Phase 2.")


if __name__ == "__main__":
    main()
