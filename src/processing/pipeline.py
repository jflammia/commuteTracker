"""Processing pipeline: raw data -> enriched, segmented Parquet.

Reads raw location data (from database or JSONL), enriches it with computed
fields, detects commutes, segments them by transport mode, and writes
derived Parquet files.
"""

import json
import logging
from pathlib import Path

import polars as pl

from src.config import (
    DERIVED_DATA_DIR,
    HOME_LAT,
    HOME_LON,
    HOME_RADIUS_M,
    WORK_LAT,
    WORK_LON,
    WORK_RADIUS_M,
)
from src.processing.commute_detector import detect_commutes
from src.processing.enricher import enrich
from src.processing.segmenter import segment_commute

logger = logging.getLogger(__name__)


def process_locations(
    df: pl.DataFrame,
    label_corrections: dict[tuple[str, int], str] | None = None,
) -> pl.DataFrame:
    """Run the full processing pipeline on a DataFrame of raw locations.

    Input: raw location records with at minimum lat, lon, tst columns.
    Output: enriched DataFrame with speed, distance, commute IDs, segments.

    Args:
        df: Raw location DataFrame.
        label_corrections: Optional map of (commute_id, segment_id) -> corrected_mode.
            When provided, overrides classifier output for matching segments.
    """
    # Filter to location messages only
    if "_type" in df.columns:
        df = df.filter(pl.col("_type") == "location")

    if df.is_empty():
        return df

    # Step 1: Enrich with computed fields
    df = enrich(df)

    # Step 2: Detect commutes
    df = detect_commutes(
        df,
        home_lat=HOME_LAT,
        home_lon=HOME_LON,
        home_radius_m=HOME_RADIUS_M,
        work_lat=WORK_LAT,
        work_lon=WORK_LON,
        work_radius_m=WORK_RADIUS_M,
    )

    # Step 3: Segment commute points by transport mode
    commute_mask = df["commute_id"].is_not_null()
    if commute_mask.any():
        commute_df = df.filter(commute_mask)
        non_commute_df = df.filter(~commute_mask)

        # Segment each commute independently
        segmented_parts = []
        for cid in commute_df["commute_id"].unique().to_list():
            one_commute = commute_df.filter(pl.col("commute_id") == cid)
            one_commute = segment_commute(one_commute)

            # Apply label corrections if available
            if label_corrections:
                modes = one_commute["transport_mode"].to_list()
                seg_ids = one_commute["segment_id"].to_list()
                changed = False
                for i, (mode, sid) in enumerate(zip(modes, seg_ids)):
                    corrected = label_corrections.get((cid, sid))
                    if corrected and corrected != mode:
                        modes[i] = corrected
                        changed = True
                if changed:
                    one_commute = one_commute.with_columns(
                        pl.Series("transport_mode", modes),
                    )

            segmented_parts.append(one_commute)

        segmented_commutes = pl.concat(segmented_parts)

        # Add null segment columns to non-commute points
        non_commute_df = non_commute_df.with_columns(
            pl.lit(None).cast(pl.Utf8).alias("transport_mode"),
            pl.lit(None).cast(pl.Int64).alias("segment_id"),
        )

        df = pl.concat([segmented_commutes, non_commute_df]).sort("tst")
    else:
        df = df.with_columns(
            pl.lit(None).cast(pl.Utf8).alias("transport_mode"),
            pl.lit(None).cast(pl.Int64).alias("segment_id"),
        )

    return df


def process_from_db(db, output_dir: str | Path | None = None, filters: dict | None = None) -> dict:
    """Process records from the database and write Parquet files.

    Groups by date and writes one Parquet file per day.

    Args:
        db: Database instance.
        output_dir: Where to write Parquet files.
        filters: Optional dict with keys: since, until (YYYY-MM-DD strings),
                 user, device, msg_type.

    Returns summary of files written.
    """
    output_dir = Path(output_dir or DERIVED_DATA_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    results = {"files_written": [], "total_records": 0, "commutes_found": 0}

    # Load records from DB with optional filtering
    with db.session() as session:
        from src.storage.database import LocationRecord

        query = session.query(LocationRecord).order_by(LocationRecord.received_at)

        if filters:
            from datetime import datetime, timezone

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

        records = query.all()
        if not records:
            return results

        rows = []
        for r in records:
            payload = json.loads(r.payload)
            payload["_db_id"] = r.id
            payload["_db_user"] = r.user
            payload["_db_device"] = r.device
            rows.append(payload)

    df = pl.DataFrame(rows)
    results["total_records"] = len(df)

    # Ensure required columns exist
    required = {"lat", "lon", "tst"}
    if not required.issubset(set(df.columns)):
        logger.warning(f"Missing required columns: {required - set(df.columns)}")
        return results

    # Load label corrections from the same database
    from src.storage.label_store import LabelStore
    label_store = LabelStore(db)
    corrections = label_store.get_corrections_map() or None

    # Process
    df = process_locations(df, label_corrections=corrections)

    # Count commutes found
    if "commute_id" in df.columns:
        commute_ids = df["commute_id"].drop_nulls().unique()
        results["commutes_found"] = len(commute_ids)

    # Write Parquet files grouped by date
    df = df.with_columns(
        pl.col("timestamp").dt.date().alias("date"),
    )

    for date_val in df["date"].unique().sort().to_list():
        day_df = df.filter(pl.col("date") == date_val)
        date_str = str(date_val)
        year = date_str[:4]
        month = date_str[5:7]

        parquet_dir = output_dir / year / month
        parquet_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = parquet_dir / f"{date_str}.parquet"

        # Drop the temporary date column before writing
        day_df = day_df.drop("date")
        day_df.write_parquet(parquet_path)

        results["files_written"].append(str(parquet_path))
        logger.info(f"Wrote {parquet_path} ({len(day_df)} records)")

    return results


def process_jsonl(jsonl_path: str | Path, output_dir: str | Path | None = None) -> dict:
    """Process a single JSONL file and write enriched Parquet.

    Useful for reprocessing raw data from S3/backups.
    """
    jsonl_path = Path(jsonl_path)
    output_dir = Path(output_dir or DERIVED_DATA_DIR)

    df = pl.read_ndjson(jsonl_path)
    results = {"total_records": len(df), "commutes_found": 0, "files_written": []}

    required = {"lat", "lon", "tst"}
    if not required.issubset(set(df.columns)):
        logger.warning(f"Missing required columns in {jsonl_path}")
        return results

    df = process_locations(df)

    if "commute_id" in df.columns:
        commute_ids = df["commute_id"].drop_nulls().unique()
        results["commutes_found"] = len(commute_ids)

    # Derive output path from input filename
    stem = jsonl_path.stem  # e.g. "2026-03-26"
    year = stem[:4]
    month = stem[5:7]
    parquet_dir = output_dir / year / month
    parquet_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = parquet_dir / f"{stem}.parquet"

    df.write_parquet(parquet_path)
    results["files_written"].append(str(parquet_path))

    return results
