#!/usr/bin/env python3
"""Ingest a JSONL file of OwnTracks location data into a Polars DataFrame.

Usage:
    python scripts/ingest.py <path-to-jsonl>
    python scripts/ingest.py raw/2026/03/2026-03-26.jsonl

Phase 0 tracer bullet: load raw data, print summary stats, confirm data looks right.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.processing.geo_utils import haversine_m, speed_kmh


def load_jsonl(path: str | Path) -> pl.DataFrame:
    """Load a JSONL file into a Polars DataFrame with computed columns."""
    path = Path(path)
    if not path.exists():
        print(f"Error: file not found: {path}")
        sys.exit(1)

    df = pl.read_ndjson(path)

    # Convert unix timestamp to datetime
    df = df.with_columns(
        pl.from_epoch("tst", time_unit="s").alias("timestamp"),
    )

    # Sort by timestamp
    df = df.sort("timestamp")

    # Compute distance and speed from consecutive points
    distances = [0.0]
    speeds = [0.0]
    lats = df["lat"].to_list()
    lons = df["lon"].to_list()
    tsts = df["tst"].to_list()

    for i in range(1, len(lats)):
        d = haversine_m(lats[i - 1], lons[i - 1], lats[i], lons[i])
        dt = tsts[i] - tsts[i - 1]
        distances.append(d)
        speeds.append(speed_kmh(d, dt))

    df = df.with_columns(
        pl.Series("distance_from_prev_m", distances),
        pl.Series("computed_speed_kmh", speeds),
    )

    return df


def print_summary(df: pl.DataFrame, path: str) -> None:
    """Print summary statistics for the loaded data."""
    print(f"\n{'=' * 60}")
    print(f"Commute Data Summary: {path}")
    print(f"{'=' * 60}")
    print(f"Total points:     {len(df)}")

    ts = df["timestamp"]
    start = ts.min()
    end = ts.max()
    print(f"Time range:       {start} -> {end}")
    duration_min = (end - start).total_seconds() / 60
    print(f"Duration:         {duration_min:.1f} minutes")

    total_dist = df["distance_from_prev_m"].sum()
    print(f"Total distance:   {total_dist:.0f} m ({total_dist / 1000:.2f} km)")

    print(f"Max speed:        {df['computed_speed_kmh'].max():.1f} km/h")
    print(f"Avg speed:        {df['computed_speed_kmh'].mean():.1f} km/h")

    if "batt" in df.columns:
        print(f"Battery:          {df['batt'].max()}% -> {df['batt'].min()}%")
    if "acc" in df.columns:
        print(f"Avg GPS accuracy: {df['acc'].mean():.0f} m")

    print(f"\nFirst 5 rows:")
    print(df.select("timestamp", "lat", "lon", "computed_speed_kmh", "distance_from_prev_m").head(5))
    print(f"\nLast 5 rows:")
    print(df.select("timestamp", "lat", "lon", "computed_speed_kmh", "distance_from_prev_m").tail(5))


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/ingest.py <path-to-jsonl>")
        sys.exit(1)

    path = sys.argv[1]
    df = load_jsonl(path)
    print_summary(df, path)


if __name__ == "__main__":
    main()
