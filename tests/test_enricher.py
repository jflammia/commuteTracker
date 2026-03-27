"""Tests for the enricher module."""

import polars as pl

from src.processing.enricher import enrich


def _make_df(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows)


def test_enrich_adds_all_columns():
    df = _make_df(
        [
            {"lat": 40.75, "lon": -74.00, "tst": 1000},
            {"lat": 40.751, "lon": -74.00, "tst": 1010},
            {"lat": 40.752, "lon": -74.00, "tst": 1020},
        ]
    )
    result = enrich(df)
    for col in ["timestamp", "distance_m", "time_delta_s", "speed_kmh", "is_stationary"]:
        assert col in result.columns, f"Missing column: {col}"


def test_enrich_first_row_zeros():
    df = _make_df(
        [
            {"lat": 40.75, "lon": -74.00, "tst": 1000},
            {"lat": 40.751, "lon": -74.00, "tst": 1010},
        ]
    )
    result = enrich(df)
    assert result["distance_m"][0] == 0.0
    assert result["time_delta_s"][0] == 0.0
    assert result["speed_kmh"][0] == 0.0


def test_enrich_computes_distance():
    df = _make_df(
        [
            {"lat": 40.75, "lon": -74.00, "tst": 1000},
            {"lat": 40.76, "lon": -74.00, "tst": 1010},
        ]
    )
    result = enrich(df)
    # ~1.11 km for 0.01 degree latitude
    assert result["distance_m"][1] > 1000
    assert result["distance_m"][1] < 1200


def test_enrich_computes_speed():
    df = _make_df(
        [
            {"lat": 40.75, "lon": -74.00, "tst": 1000},
            {"lat": 40.76, "lon": -74.00, "tst": 1100},  # 100 seconds
        ]
    )
    result = enrich(df)
    # ~1111m in 100s = ~40 km/h
    assert result["speed_kmh"][1] > 30
    assert result["speed_kmh"][1] < 50


def test_enrich_stationary_flag():
    df = _make_df(
        [
            {"lat": 40.75, "lon": -74.00, "tst": 1000},
            {"lat": 40.75, "lon": -74.00, "tst": 1010},  # same location
            {"lat": 40.76, "lon": -74.00, "tst": 1020},  # moved
        ]
    )
    result = enrich(df)
    assert result["is_stationary"][0] is True
    assert result["is_stationary"][1] is True
    assert result["is_stationary"][2] is False


def test_enrich_sorts_by_tst():
    df = _make_df(
        [
            {"lat": 40.76, "lon": -74.00, "tst": 1020},
            {"lat": 40.75, "lon": -74.00, "tst": 1000},
            {"lat": 40.755, "lon": -74.00, "tst": 1010},
        ]
    )
    result = enrich(df)
    tsts = result["tst"].to_list()
    assert tsts == [1000, 1010, 1020]


def test_enrich_empty_dataframe():
    df = pl.DataFrame({"lat": [], "lon": [], "tst": []})
    result = enrich(df)
    assert result.is_empty()


def test_enrich_single_point():
    df = _make_df([{"lat": 40.75, "lon": -74.00, "tst": 1000}])
    result = enrich(df)
    assert len(result) == 1
    assert result["distance_m"][0] == 0.0
    assert result["speed_kmh"][0] == 0.0
