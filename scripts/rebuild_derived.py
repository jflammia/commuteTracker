#!/usr/bin/env python3
"""Rebuild derived data from raw JSONL files or database.

Usage:
    python scripts/rebuild_derived.py                        # Rebuild all
    python scripts/rebuild_derived.py --since 2026-03-01     # From date
    python scripts/rebuild_derived.py --since 2026-03-01 --until 2026-03-15
    python scripts/rebuild_derived.py --date 2026-03-26      # Single day
    python scripts/rebuild_derived.py --user phone --device iphone
    python scripts/rebuild_derived.py --from-jsonl DIR       # From JSONL files

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

    # Time window filters (database mode only)
    parser.add_argument("--since", type=str, help="Start date inclusive (YYYY-MM-DD)")
    parser.add_argument("--until", type=str, help="End date inclusive (YYYY-MM-DD)")
    parser.add_argument("--date", type=str, help="Single date (shorthand for --since X --until X)")

    # Attribute filters (database mode only)
    parser.add_argument("--user", type=str, help="Filter by OwnTracks user")
    parser.add_argument("--device", type=str, help="Filter by OwnTracks device")
    parser.add_argument("--msg-type", type=str, default="location", help="Filter by message type (default: location)")

    # Options
    parser.add_argument("--clean", action="store_true", help="Delete existing Parquet files in the date range before rebuilding")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be rebuilt without writing files")

    args = parser.parse_args()
    output_dir = Path(args.output)

    # Handle --date shorthand
    if args.date:
        args.since = args.date
        args.until = args.date

    if args.from_jsonl:
        _rebuild_from_jsonl(args, output_dir)
    else:
        _rebuild_from_db(args, output_dir)


def _rebuild_from_jsonl(args, output_dir: Path) -> None:
    jsonl_dir = Path(args.from_jsonl)
    if not jsonl_dir.exists():
        print(f"JSONL directory not found: {jsonl_dir}")
        sys.exit(1)

    jsonl_files = sorted(jsonl_dir.rglob("*.jsonl"))

    # Filter JSONL files by date if specified (filenames are YYYY-MM-DD.jsonl)
    if args.since:
        jsonl_files = [f for f in jsonl_files if f.stem >= args.since]
    if args.until:
        jsonl_files = [f for f in jsonl_files if f.stem <= args.until]

    if not jsonl_files:
        print("No JSONL files found matching filters.")
        return

    if args.dry_run:
        print(f"Would rebuild {len(jsonl_files)} JSONL files -> {output_dir}")
        for f in jsonl_files:
            print(f"  {f.name}")
        return

    print(f"Rebuilding from {len(jsonl_files)} JSONL files -> {output_dir}")
    total_commutes = 0
    for jsonl in jsonl_files:
        results = process_jsonl(jsonl, output_dir)
        total_commutes += results["commutes_found"]
        print(f"  {jsonl.name}: {results['total_records']} records, {results['commutes_found']} commutes")

    print(f"\nDone. {total_commutes} commutes found.")


def _rebuild_from_db(args, output_dir: Path) -> None:
    from src.storage.database import Database

    filters = {}
    if args.since:
        filters["since"] = args.since
    if args.until:
        filters["until"] = args.until
    if args.user:
        filters["user"] = args.user
    if args.device:
        filters["device"] = args.device
    if args.msg_type:
        filters["msg_type"] = args.msg_type

    filter_desc = ", ".join(f"{k}={v}" for k, v in filters.items()) or "all records"
    print(f"Rebuilding from database: {DATABASE_URL}")
    print(f"Filters: {filter_desc}")
    print(f"Output: {output_dir}")

    if args.dry_run:
        db = Database(DATABASE_URL)
        count = _count_filtered_records(db, filters)
        print(f"\nWould process {count} records.")
        return

    if args.clean and (args.since or args.until):
        _clean_parquet_range(output_dir, args.since, args.until)

    db = Database(DATABASE_URL)
    results = process_from_db(db, output_dir, filters=filters)

    print(f"\nDone. {results['total_records']} records processed, "
          f"{results['commutes_found']} commutes found, "
          f"{len(results['files_written'])} files written.")


def _count_filtered_records(db, filters: dict) -> int:
    """Count records matching filters without loading them."""
    from sqlalchemy import func
    from src.storage.database import LocationRecord

    with db.session() as session:
        query = session.query(func.count(LocationRecord.id))
        query = _apply_filters(query, filters)
        return query.scalar()


def _apply_filters(query, filters: dict):
    """Apply filters to a SQLAlchemy query."""
    from datetime import datetime, timezone
    from src.storage.database import LocationRecord

    if "since" in filters:
        since = datetime.strptime(filters["since"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        query = query.filter(LocationRecord.received_at >= since)
    if "until" in filters:
        until = datetime.strptime(filters["until"], "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
        query = query.filter(LocationRecord.received_at <= until)
    if "user" in filters:
        query = query.filter(LocationRecord.user == filters["user"])
    if "device" in filters:
        query = query.filter(LocationRecord.device == filters["device"])
    if "msg_type" in filters:
        query = query.filter(LocationRecord.msg_type == filters["msg_type"])

    return query


def _clean_parquet_range(output_dir: Path, since: str | None, until: str | None) -> None:
    """Delete existing Parquet files within the date range."""
    if not output_dir.exists():
        return

    for parquet in output_dir.rglob("*.parquet"):
        date_str = parquet.stem  # YYYY-MM-DD
        if since and date_str < since:
            continue
        if until and date_str > until:
            continue
        print(f"  Cleaning: {parquet}")
        parquet.unlink()


if __name__ == "__main__":
    main()
