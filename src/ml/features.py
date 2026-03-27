"""Feature engineering for transport mode classification.

Extracts features from enriched GPS data for ML model training.
Each row (GPS point) gets features derived from:
    1. Current point properties (speed, acceleration)
    2. Rolling window statistics (smoothness, variance)
    3. Spatial context (distance to waypoints/corridors if configured)
    4. Temporal patterns (time of day, day of week)
    5. Sequence context (previous/next point features)

Features are designed to distinguish between transport modes where
speed alone is ambiguous (especially driving vs train).
"""

from __future__ import annotations

import math

import polars as pl


def extract_point_features(df: pl.DataFrame) -> pl.DataFrame:
    """Extract per-point features from an enriched DataFrame.

    Expects columns: lat, lon, tst, speed_kmh, distance_m, time_delta_s, timestamp.
    Returns DataFrame with original columns plus computed features.
    """
    if df.is_empty():
        return df

    speeds = df["speed_kmh"].to_list()
    time_deltas = df["time_delta_s"].to_list()
    lats = df["lat"].to_list()
    lons = df["lon"].to_list()
    n = len(speeds)

    # --- Acceleration ---
    acceleration = [0.0]
    for i in range(1, n):
        dt = time_deltas[i]
        if dt > 0:
            # km/h per second
            acceleration.append((speeds[i] - speeds[i - 1]) / dt)
        else:
            acceleration.append(0.0)

    # --- Bearing and bearing change ---
    bearings = [0.0]
    for i in range(1, n):
        bearings.append(_bearing(lats[i - 1], lons[i - 1], lats[i], lons[i]))

    bearing_change = [0.0]
    for i in range(1, n):
        diff = abs(bearings[i] - bearings[i - 1])
        if diff > 180:
            diff = 360 - diff
        bearing_change.append(diff)

    # --- Rolling window features ---
    window = 10
    half = window // 2

    speed_mean = []
    speed_std = []
    speed_cv = []
    accel_std = []
    bearing_change_mean = []
    distance_sum = []

    for i in range(n):
        start = max(0, i - half)
        end = min(n, i + half + 1)
        w_speeds = speeds[start:end]
        w_accel = acceleration[start:end]
        w_bearing = bearing_change[start:end]
        w_dist = df["distance_m"].to_list()[start:end]

        s_mean = _mean(w_speeds)
        s_std = _std(w_speeds)

        speed_mean.append(s_mean)
        speed_std.append(s_std)
        speed_cv.append(s_std / s_mean if s_mean > 0 else 0.0)
        accel_std.append(_std(w_accel))
        bearing_change_mean.append(_mean(w_bearing))
        distance_sum.append(sum(w_dist))

    # --- Temporal features ---
    if "timestamp" in df.columns:
        hours = []
        minutes = []
        day_of_week = []
        for ts in df["timestamp"].to_list():
            hours.append(ts.hour)
            minutes.append(ts.minute)
            day_of_week.append(ts.weekday())
        hour_sin = [math.sin(2 * math.pi * h / 24) for h in hours]
        hour_cos = [math.cos(2 * math.pi * h / 24) for h in hours]
    else:
        hours = [0] * n
        minutes = [0] * n
        day_of_week = [0] * n
        hour_sin = [0.0] * n
        hour_cos = [0.0] * n

    # --- Stop detection features ---
    is_stopped = [1 if s < 1.0 else 0 for s in speeds]
    stop_duration = _compute_stop_durations(is_stopped, time_deltas)

    df = df.with_columns(
        # Point features
        pl.Series("acceleration", acceleration),
        pl.Series("bearing", bearings),
        pl.Series("bearing_change", bearing_change),
        # Rolling features
        pl.Series("speed_mean_w10", speed_mean),
        pl.Series("speed_std_w10", speed_std),
        pl.Series("speed_cv_w10", speed_cv),
        pl.Series("accel_std_w10", accel_std),
        pl.Series("bearing_change_mean_w10", bearing_change_mean),
        pl.Series("distance_sum_w10", distance_sum),
        # Temporal
        pl.Series("hour", hours),
        pl.Series("minute", minutes),
        pl.Series("day_of_week", day_of_week),
        pl.Series("hour_sin", hour_sin),
        pl.Series("hour_cos", hour_cos),
        # Stop features
        pl.Series("is_stopped", is_stopped),
        pl.Series("stop_duration_s", stop_duration),
    )

    return df


# Feature columns suitable for ML model input
FEATURE_COLUMNS = [
    "speed_kmh",
    "acceleration",
    "bearing_change",
    "speed_mean_w10",
    "speed_std_w10",
    "speed_cv_w10",
    "accel_std_w10",
    "bearing_change_mean_w10",
    "distance_sum_w10",
    "hour_sin",
    "hour_cos",
    "day_of_week",
    "is_stopped",
    "stop_duration_s",
]


def build_training_set(
    df: pl.DataFrame,
    label_col: str = "transport_mode",
) -> tuple[pl.DataFrame, pl.Series]:
    """Build feature matrix and labels from a labeled DataFrame.

    Args:
        df: DataFrame with features extracted and a label column.
        label_col: Column name containing transport mode labels.

    Returns:
        (features_df, labels) where features_df has FEATURE_COLUMNS
        and labels is a string Series.
    """
    # Drop rows without labels
    labeled = df.filter(pl.col(label_col).is_not_null())
    if labeled.is_empty():
        return pl.DataFrame(), pl.Series("label", [], dtype=pl.Utf8)

    features = labeled.select(FEATURE_COLUMNS)
    labels = labeled[label_col]

    return features, labels


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute bearing in degrees from point 1 to point 2."""
    lat1, lon1, lat2, lon2 = (math.radians(v) for v in (lat1, lon1, lat2, lon2))
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return (sum((v - m) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def _compute_stop_durations(is_stopped: list[int], time_deltas: list[float]) -> list[float]:
    """For each point, compute how long the current stop has lasted so far."""
    durations = [0.0] * len(is_stopped)
    running = 0.0
    for i in range(len(is_stopped)):
        if is_stopped[i]:
            running += time_deltas[i]
        else:
            running = 0.0
        durations[i] = running
    return durations
