"""Tests for the processing pipeline."""

from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest

from src.processing.pipeline import process_locations, process_jsonl

FIXTURES_DIR = Path(__file__).parent / "fixtures"

HOME = (40.75, -74.00)
WORK = (40.85, -73.95)


def _pipeline_config():
    """Return a mock.patch context that sets pipeline geofence config."""
    return patch.multiple(
        "src.processing.pipeline",
        HOME_LAT=HOME[0],
        HOME_LON=HOME[1],
        HOME_RADIUS_M=200.0,
        WORK_LAT=WORK[0],
        WORK_LON=WORK[1],
        WORK_RADIUS_M=200.0,
    )


def _make_commute_df():
    """Create a minimal DataFrame simulating home -> work commute."""
    rows = []
    tst = 1711440000
    # At home (2 points)
    for i in range(2):
        rows.append({"_type": "location", "lat": HOME[0], "lon": HOME[1], "tst": tst})
        tst += 10

    # Transit points (moving toward work)
    steps = 10
    for i in range(1, steps + 1):
        frac = i / steps
        lat = HOME[0] + (WORK[0] - HOME[0]) * frac
        lon = HOME[1] + (WORK[1] - HOME[1]) * frac
        rows.append({"_type": "location", "lat": lat, "lon": lon, "tst": tst})
        tst += 10

    # At work (2 points)
    for i in range(2):
        rows.append({"_type": "location", "lat": WORK[0], "lon": WORK[1], "tst": tst})
        tst += 10

    return pl.DataFrame(rows)


def test_process_locations_adds_expected_columns():
    with _pipeline_config():
        df = _make_commute_df()
        result = process_locations(df)
        expected_cols = [
            "timestamp",
            "distance_m",
            "speed_kmh",
            "commute_id",
            "commute_direction",
            "transport_mode",
            "segment_id",
        ]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"


def test_process_locations_filters_non_location():
    with _pipeline_config():
        df = pl.DataFrame(
            [
                {"_type": "location", "lat": 40.75, "lon": -74.00, "tst": 1000},
                {"_type": "transition", "lat": 40.75, "lon": -74.00, "tst": 1010},
                {"_type": "location", "lat": 40.76, "lon": -74.00, "tst": 1020},
            ]
        )
        result = process_locations(df)
        assert len(result) == 2


def test_process_locations_empty_df():
    with _pipeline_config():
        df = pl.DataFrame({"_type": [], "lat": [], "lon": [], "tst": []})
        result = process_locations(df)
        assert result.is_empty()


def test_process_locations_detects_commute():
    with _pipeline_config():
        df = _make_commute_df()
        result = process_locations(df)
        commute_points = result.filter(pl.col("commute_id").is_not_null())
        assert len(commute_points) > 0


def test_process_locations_assigns_segments():
    with _pipeline_config():
        df = _make_commute_df()
        result = process_locations(df)
        commute_points = result.filter(pl.col("commute_id").is_not_null())
        if len(commute_points) > 0:
            assert commute_points["segment_id"].null_count() == 0
            assert commute_points["transport_mode"].null_count() == 0


def test_process_jsonl_writes_parquet(tmp_path):
    """Process the sample fixture JSONL and verify Parquet output."""
    fixture = FIXTURES_DIR / "sample_commute.jsonl"
    if not fixture.exists():
        pytest.skip("Fixture file not available")

    output_dir = tmp_path / "derived"
    results = process_jsonl(fixture, output_dir)

    assert results["total_records"] > 0
    assert len(results["files_written"]) > 0

    # Verify the Parquet file was written and is readable
    parquet_path = Path(results["files_written"][0])
    assert parquet_path.exists()
    df = pl.read_parquet(parquet_path)
    assert len(df) > 0
    assert "timestamp" in df.columns
    assert "speed_kmh" in df.columns


def test_process_jsonl_missing_columns(tmp_path):
    """JSONL without required columns should return zero results."""
    bad_jsonl = tmp_path / "bad.jsonl"
    bad_jsonl.write_text('{"foo": "bar"}\n{"baz": 1}\n')

    output_dir = tmp_path / "derived"
    results = process_jsonl(bad_jsonl, output_dir)
    assert results["commutes_found"] == 0
    assert results["files_written"] == []
