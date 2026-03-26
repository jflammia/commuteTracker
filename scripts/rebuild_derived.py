#!/usr/bin/env python3
"""Rebuild all derived data from raw JSONL files or database.

Usage:
    python scripts/rebuild_derived.py                    # From database
    python scripts/rebuild_derived.py --from-jsonl DIR   # From JSONL files

This is the key durability guarantee: derived data is disposable
and can always be regenerated from the raw data.
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import DATABASE_URL, DERIVED_DATA_DIR
from src.processing.pipeline import process_from_db, process_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild derived data from raw sources")
    parser.add_argument("--from-jsonl", type=str, help="Rebuild from JSONL directory instead of database")
    parser.add_argument("--output", type=str, default=str(DERIVED_DATA_DIR), help="Output directory for Parquet files")
    args = parser.parse_args()

    output_dir = Path(args.output)

    if args.from_jsonl:
        jsonl_dir = Path(args.from_jsonl)
        if not jsonl_dir.exists():
            print(f"JSONL directory not found: {jsonl_dir}")
            sys.exit(1)

        jsonl_files = sorted(jsonl_dir.rglob("*.jsonl"))
        if not jsonl_files:
            print("No JSONL files found.")
            return

        print(f"Rebuilding from {len(jsonl_files)} JSONL files -> {output_dir}")
        total_commutes = 0
        for jsonl in jsonl_files:
            results = process_jsonl(jsonl, output_dir)
            total_commutes += results["commutes_found"]
            print(f"  {jsonl.name}: {results['total_records']} records, {results['commutes_found']} commutes")

        print(f"\nDone. {total_commutes} commutes found.")

    else:
        from src.storage.database import Database

        print(f"Rebuilding from database: {DATABASE_URL}")
        print(f"Output: {output_dir}")

        db = Database(DATABASE_URL)
        results = process_from_db(db, output_dir)

        print(f"\nDone. {results['total_records']} records processed, "
              f"{results['commutes_found']} commutes found, "
              f"{len(results['files_written'])} files written.")


if __name__ == "__main__":
    main()
