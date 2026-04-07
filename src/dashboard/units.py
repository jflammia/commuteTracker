"""Imperial unit conversions for dashboard display.

The processing pipeline and API use metric (km/h, meters). The dashboard
converts to imperial (mph, miles) at the display layer only.
"""

from __future__ import annotations

import polars as pl

KMH_TO_MPH = 0.621371
M_TO_MI = 1 / 1609.34


def add_imperial_speed(
    df: pl.DataFrame, src: str = "speed_kmh", dst: str = "speed_mph"
) -> pl.DataFrame:
    """Add a speed column converted from km/h to mph."""
    if src in df.columns:
        df = df.with_columns((pl.col(src).cast(pl.Float64) * KMH_TO_MPH).round(1).alias(dst))
    return df


def add_imperial_distance(
    df: pl.DataFrame, src: str = "distance_m", dst: str = "distance_mi"
) -> pl.DataFrame:
    """Add a distance column converted from meters to miles."""
    if src in df.columns:
        df = df.with_columns((pl.col(src).cast(pl.Float64) * M_TO_MI).round(2).alias(dst))
    return df


def format_distance(meters: float | None) -> str:
    """Format a distance in meters as an imperial string."""
    if not meters:
        return "0 mi"
    miles = meters * M_TO_MI
    if miles < 0.1:
        return f"{meters * 3.28084:.0f} ft"
    return f"{miles:.1f} mi"


def format_speed(kmh: float | None) -> str:
    """Format a speed in km/h as mph string."""
    if not kmh:
        return "-"
    return f"{kmh * KMH_TO_MPH:.1f} mph"
