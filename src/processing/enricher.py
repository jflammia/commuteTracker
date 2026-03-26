"""Enrich raw location data with computed fields.

Takes a Polars DataFrame of raw location records and adds:
- Computed speed between consecutive points
- Distance from previous point
- Time delta from previous point
- Stationary/moving flag
"""

import polars as pl

from src.processing.geo_utils import haversine_m, speed_kmh


def enrich(df: pl.DataFrame) -> pl.DataFrame:
    """Add computed columns to a DataFrame of location records.

    Expects columns: lat, lon, tst (unix timestamp).
    Adds: timestamp, distance_m, time_delta_s, speed_kmh, is_stationary.
    """
    if df.is_empty():
        return df

    # Ensure sorted by timestamp
    df = df.sort("tst")

    # Convert unix timestamp to datetime
    if "timestamp" not in df.columns:
        df = df.with_columns(
            pl.from_epoch("tst", time_unit="s").alias("timestamp"),
        )

    lats = df["lat"].to_list()
    lons = df["lon"].to_list()
    tsts = df["tst"].to_list()

    distances = [0.0]
    time_deltas = [0.0]
    speeds = [0.0]

    for i in range(1, len(lats)):
        d = haversine_m(lats[i - 1], lons[i - 1], lats[i], lons[i])
        dt = tsts[i] - tsts[i - 1]
        distances.append(d)
        time_deltas.append(float(dt))
        speeds.append(speed_kmh(d, dt))

    df = df.with_columns(
        pl.Series("distance_m", distances),
        pl.Series("time_delta_s", time_deltas),
        pl.Series("speed_kmh", speeds),
        pl.Series("is_stationary", [s < 1.0 for s in speeds]),
    )

    return df
