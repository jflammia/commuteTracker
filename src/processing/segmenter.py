"""Segment commutes into legs by transport mode.

Uses an ensemble of classifiers to label each point's transport mode.
The ensemble runs automatically with zero config (speed + speed variance),
and can be enhanced with user-defined waypoints and route corridors.

Segments are contiguous runs of the same transport mode.
Short segments (< 30s) are merged into their neighbors to reduce noise.
Waypoint boundaries force segment splits regardless of mode.
"""

from __future__ import annotations

import polars as pl

from src.processing.classifiers.ensemble import EnsembleClassifier, build_ensemble, load_zones_config


# Minimum segment duration to keep (seconds)
MIN_SEGMENT_DURATION = 30.0

# Module-level ensemble, lazily initialized
_ensemble: EnsembleClassifier | None = None


def get_ensemble() -> EnsembleClassifier:
    """Get or create the module-level ensemble classifier."""
    global _ensemble
    if _ensemble is None:
        config = load_zones_config()
        _ensemble = build_ensemble(config)
    return _ensemble


def reset_ensemble() -> None:
    """Reset the cached ensemble (e.g., after config changes). Useful for testing."""
    global _ensemble
    _ensemble = None


# --- Keep legacy function for backward compatibility with existing tests ---

def classify_transport_mode(speed_kmh: float) -> str:
    """Classify a single point's transport mode by speed.

    Legacy convenience function. For batch classification, use the ensemble.
    """
    from src.processing.classifiers.speed import SpeedClassifier
    _speed = SpeedClassifier()
    scores = _speed.score(pl.DataFrame({"speed_kmh": [speed_kmh]}))
    return scores[0].winner()


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
        counts: dict[str, int] = {}
        for m in neighborhood:
            counts[m] = counts.get(m, 0) + 1
        smoothed[i] = max(counts, key=counts.get)

    return smoothed


def _assign_segment_ids(
    modes: list[str],
    force_boundaries: list[int] | None = None,
) -> list[int]:
    """Assign segment IDs: each contiguous run of the same mode gets an ID.

    force_boundaries: indices where a new segment must start regardless of mode.
    """
    if not modes:
        return []

    boundary_set = set(force_boundaries or [])

    segment_ids = [0]
    current_id = 0
    for i in range(1, len(modes)):
        if modes[i] != modes[i - 1] or i in boundary_set:
            current_id += 1
        segment_ids.append(current_id)

    return segment_ids


def _merge_short_segments(
    modes: list[str],
    segment_ids: list[int],
    time_deltas: list[float],
    protected_boundaries: list[int] | None = None,
) -> tuple[list[str], list[int]]:
    """Merge segments shorter than MIN_SEGMENT_DURATION into neighbors.

    protected_boundaries: indices that must remain segment boundaries
    (e.g., from waypoint transitions). Short segments at these boundaries
    are not merged.
    """
    if not modes:
        return modes, segment_ids

    protected = set(protected_boundaries or [])

    # Calculate duration per segment
    seg_durations: dict[int, float] = {}
    for sid, dt in zip(segment_ids, time_deltas):
        seg_durations[sid] = seg_durations.get(sid, 0.0) + dt

    # Find the start index of each segment
    seg_starts: dict[int, int] = {}
    for i, sid in enumerate(segment_ids):
        if sid not in seg_starts:
            seg_starts[sid] = i

    # Find short segments and merge into previous
    merged_modes = list(modes)
    for sid, duration in seg_durations.items():
        if duration < MIN_SEGMENT_DURATION and sid > 0:
            # Don't merge if this segment starts at a protected boundary
            if seg_starts.get(sid, 0) in protected:
                continue

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
    new_ids = _assign_segment_ids(merged_modes, list(protected))
    return merged_modes, new_ids


def segment_commute(
    df: pl.DataFrame,
    ensemble: EnsembleClassifier | None = None,
) -> pl.DataFrame:
    """Add transport_mode and segment_id columns to a commute DataFrame.

    Expects columns: speed_kmh, time_delta_s, and (for ensemble classifiers)
    lat, lon, timestamp.

    Args:
        df: Commute DataFrame with enriched columns.
        ensemble: Classifier ensemble to use. If None, uses the module default.

    Returns:
        DataFrame with transport_mode and segment_id columns added.
    """
    if df.is_empty():
        return df.with_columns(
            pl.lit(None).alias("transport_mode"),
            pl.lit(None).alias("segment_id"),
        )

    ens = ensemble or get_ensemble()
    time_deltas = df["time_delta_s"].to_list()

    # Classify each point using the ensemble
    modes = ens.classify(df)

    # Smooth to reduce GPS noise
    modes = _smooth_modes(modes)

    # Get waypoint-forced boundaries
    waypoint_boundaries = ens.get_waypoint_boundaries(df)

    # Assign initial segment IDs (respecting waypoint boundaries)
    segment_ids = _assign_segment_ids(modes, waypoint_boundaries)

    # Merge short segments (protecting waypoint boundaries)
    modes, segment_ids = _merge_short_segments(
        modes, segment_ids, time_deltas, waypoint_boundaries
    )

    df = df.with_columns(
        pl.Series("transport_mode", modes),
        pl.Series("segment_id", segment_ids),
    )

    return df
