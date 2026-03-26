"""Tests for the commute detector module."""

import polars as pl

from src.processing.commute_detector import detect_commutes
from src.processing.enricher import enrich

# Geofences for testing
HOME = (40.75, -74.00, 200)   # lat, lon, radius_m
WORK = (40.85, -73.95, 200)


def _detect(rows: list[dict]) -> pl.DataFrame:
    """Helper: enrich then detect commutes."""
    df = pl.DataFrame(rows)
    df = enrich(df)
    return detect_commutes(
        df,
        home_lat=HOME[0], home_lon=HOME[1], home_radius_m=HOME[2],
        work_lat=WORK[0], work_lon=WORK[1], work_radius_m=WORK[2],
    )


def test_morning_commute_detected():
    """Home -> midpoint -> work should be labeled as morning commute."""
    rows = [
        {"lat": 40.750, "lon": -74.000, "tst": 1000},   # at home
        {"lat": 40.750, "lon": -74.000, "tst": 1010},   # still at home
        {"lat": 40.770, "lon": -73.990, "tst": 1020},   # left home
        {"lat": 40.800, "lon": -73.970, "tst": 1030},   # in transit
        {"lat": 40.830, "lon": -73.960, "tst": 1040},   # in transit
        {"lat": 40.850, "lon": -73.950, "tst": 1050},   # at work
    ]
    result = _detect(rows)

    assert "commute_id" in result.columns
    assert "commute_direction" in result.columns

    commute_points = result.filter(pl.col("commute_id").is_not_null())
    assert len(commute_points) > 0

    directions = commute_points["commute_direction"].unique().to_list()
    assert directions == ["morning"]


def test_evening_commute_detected():
    """Work -> midpoint -> home should be labeled as evening commute."""
    rows = [
        {"lat": 40.850, "lon": -73.950, "tst": 1000},   # at work
        {"lat": 40.850, "lon": -73.950, "tst": 1010},   # still at work
        {"lat": 40.830, "lon": -73.960, "tst": 1020},   # left work
        {"lat": 40.800, "lon": -73.970, "tst": 1030},   # in transit
        {"lat": 40.770, "lon": -73.990, "tst": 1040},   # in transit
        {"lat": 40.750, "lon": -74.000, "tst": 1050},   # at home
    ]
    result = _detect(rows)

    commute_points = result.filter(pl.col("commute_id").is_not_null())
    assert len(commute_points) > 0

    directions = commute_points["commute_direction"].unique().to_list()
    assert directions == ["evening"]


def test_no_commute_when_staying_home():
    """Points all near home should have no commute detected."""
    rows = [
        {"lat": 40.750, "lon": -74.000, "tst": 1000},
        {"lat": 40.7501, "lon": -74.0001, "tst": 1010},
        {"lat": 40.7502, "lon": -73.9999, "tst": 1020},
    ]
    result = _detect(rows)
    assert result["commute_id"].null_count() == len(result)


def test_return_to_origin_cancels_commute():
    """Leaving home then returning without reaching work should not be a commute."""
    rows = [
        {"lat": 40.750, "lon": -74.000, "tst": 1000},   # at home
        {"lat": 40.750, "lon": -74.000, "tst": 1010},   # at home
        {"lat": 40.770, "lon": -73.990, "tst": 1020},   # left home
        {"lat": 40.780, "lon": -73.985, "tst": 1030},   # in transit
        {"lat": 40.750, "lon": -74.000, "tst": 1040},   # back home
    ]
    result = _detect(rows)
    assert result["commute_id"].null_count() == len(result)


def test_round_trip_both_detected():
    """Home -> work -> home should detect both morning and evening commutes."""
    rows = [
        # Morning
        {"lat": 40.750, "lon": -74.000, "tst": 1000},   # home
        {"lat": 40.750, "lon": -74.000, "tst": 1010},   # home
        {"lat": 40.800, "lon": -73.970, "tst": 1020},   # transit
        {"lat": 40.850, "lon": -73.950, "tst": 1030},   # work
        # Stay at work
        {"lat": 40.850, "lon": -73.950, "tst": 1040},   # work
        # Evening
        {"lat": 40.800, "lon": -73.970, "tst": 1050},   # transit
        {"lat": 40.750, "lon": -74.000, "tst": 1060},   # home
    ]
    result = _detect(rows)

    commute_points = result.filter(pl.col("commute_id").is_not_null())
    directions = sorted(commute_points["commute_direction"].unique().to_list())
    assert directions == ["evening", "morning"]


def test_at_home_at_work_flags():
    """Points at home/work should have correct boolean flags."""
    rows = [
        {"lat": 40.750, "lon": -74.000, "tst": 1000},   # home
        {"lat": 40.800, "lon": -73.970, "tst": 1010},   # neither
        {"lat": 40.850, "lon": -73.950, "tst": 1020},   # work
    ]
    result = _detect(rows)
    assert result["at_home"][0] is True
    assert result["at_work"][0] is False
    assert result["at_home"][1] is False
    assert result["at_work"][1] is False
    assert result["at_home"][2] is False
    assert result["at_work"][2] is True


def test_commute_id_format():
    """Commute ID should be in YYYY-MM-DD-direction format."""
    rows = [
        {"lat": 40.750, "lon": -74.000, "tst": 1711440000},   # 2024-03-26
        {"lat": 40.750, "lon": -74.000, "tst": 1711440010},
        {"lat": 40.800, "lon": -73.970, "tst": 1711440020},
        {"lat": 40.850, "lon": -73.950, "tst": 1711440030},
    ]
    result = _detect(rows)
    commute_ids = result["commute_id"].drop_nulls().unique().to_list()
    assert len(commute_ids) == 1
    assert commute_ids[0].endswith("-morning")
    # Should start with a date
    assert commute_ids[0][:4].isdigit()
