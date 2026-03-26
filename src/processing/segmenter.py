"""Segment commutes into legs by transport mode.

Uses speed patterns and stop detection to classify each point as:
- stationary: < 1 km/h
- walking: 1-7 km/h
- driving: 7-30 km/h (variable speed, stop-and-go)
- train: > 30 km/h sustained (smoother speed profile)

Segments are contiguous runs of the same transport mode.
Short segments (< 30s) are merged into their neighbors to reduce noise.
"""

import polars as pl


# Speed thresholds (km/h)
STATIONARY_MAX = 1.0
WALK_MAX = 7.0
VEHICLE_MIN = 7.0
TRAIN_MIN = 30.0

# Minimum segment duration to keep (seconds)
MIN_SEGMENT_DURATION = 30.0


def classify_transport_mode(speed_kmh: float) -> str:
    """Classify a single point's transport mode by speed."""
    if speed_kmh < STATIONARY_MAX:
        return "stationary"
    elif speed_kmh < WALK_MAX:
        return "walking"
    elif speed_kmh < TRAIN_MIN:
        return "driving"
    else:
        return "train"


def _smooth_modes(modes: list[str], window: int = 5) -> list[str]:
    """Apply majority-vote smoothing to reduce noise in mode classification.

    Each point takes the most common mode in its neighborhood.
    """
    if len(modes) <= window:
        return modes

    smoothed = list(modes)
    half = window // 2
    for i in range(half, len(modes) - half):
        neighborhood = modes[i - half : i + half + 1]
        # Count occurrences, pick most common
        counts: dict[str, int] = {}
        for m in neighborhood:
            counts[m] = counts.get(m, 0) + 1
        smoothed[i] = max(counts, key=counts.get)

    return smoothed


def _assign_segment_ids(modes: list[str]) -> list[int]:
    """Assign segment IDs: each contiguous run of the same mode gets an ID."""
    if not modes:
        return []

    segment_ids = [0]
    current_id = 0
    for i in range(1, len(modes)):
        if modes[i] != modes[i - 1]:
            current_id += 1
        segment_ids.append(current_id)

    return segment_ids


def _merge_short_segments(
    modes: list[str],
    segment_ids: list[int],
    time_deltas: list[float],
) -> tuple[list[str], list[int]]:
    """Merge segments shorter than MIN_SEGMENT_DURATION into neighbors."""
    if not modes:
        return modes, segment_ids

    # Calculate duration per segment
    seg_durations: dict[int, float] = {}
    for sid, dt in zip(segment_ids, time_deltas):
        seg_durations[sid] = seg_durations.get(sid, 0.0) + dt

    # Find short segments and merge into previous
    merged_modes = list(modes)
    for sid, duration in seg_durations.items():
        if duration < MIN_SEGMENT_DURATION and sid > 0:
            # Find the mode of the previous segment
            prev_mode = None
            for i, s in enumerate(segment_ids):
                if s == sid - 1:
                    prev_mode = merged_modes[i]
                    break

            if prev_mode is not None:
                for i in range(len(segment_ids)):
                    if segment_ids[i] == sid:
                        merged_modes[i] = prev_mode

    # Re-assign segment IDs after merging
    new_ids = _assign_segment_ids(merged_modes)
    return merged_modes, new_ids


def segment_commute(df: pl.DataFrame) -> pl.DataFrame:
    """Add transport_mode and segment_id columns to a commute DataFrame.

    Expects columns: speed_kmh, time_delta_s.
    Adds: transport_mode, segment_id.
    """
    if df.is_empty():
        return df.with_columns(
            pl.lit(None).alias("transport_mode"),
            pl.lit(None).alias("segment_id"),
        )

    speeds = df["speed_kmh"].to_list()
    time_deltas = df["time_delta_s"].to_list()

    # Classify each point
    modes = [classify_transport_mode(s) for s in speeds]

    # Smooth to reduce GPS noise
    modes = _smooth_modes(modes)

    # Assign initial segment IDs
    segment_ids = _assign_segment_ids(modes)

    # Merge short segments
    modes, segment_ids = _merge_short_segments(modes, segment_ids, time_deltas)

    df = df.with_columns(
        pl.Series("transport_mode", modes),
        pl.Series("segment_id", segment_ids),
    )

    return df
