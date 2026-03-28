"""Detect commutes from enriched location data.

A commute is a trip between two geofences (home and work).
- Morning commute: leave home geofence -> arrive at work geofence
- Evening commute: leave work geofence -> arrive at home geofence

Points outside both geofences and between a departure and arrival
are considered part of the commute.
"""

import polars as pl

from src.processing.geo_utils import in_geofence


def detect_commutes(
    df: pl.DataFrame,
    home_lat: float,
    home_lon: float,
    home_radius_m: float,
    work_lat: float,
    work_lon: float,
    work_radius_m: float,
) -> pl.DataFrame:
    """Label each point with commute_id and direction.

    Adds columns:
    - at_home: bool
    - at_work: bool
    - commute_id: str or null (e.g. "2026-03-26-morning")
    - commute_direction: "morning" | "evening" | null
    """
    if df.is_empty():
        return df

    lats = df["lat"].to_list()
    lons = df["lon"].to_list()
    timestamps = df["timestamp"].to_list()
    local_timestamps = df["timestamp_local"].to_list()

    at_home = [
        in_geofence(lat, lon, home_lat, home_lon, home_radius_m) for lat, lon in zip(lats, lons)
    ]
    at_work = [
        in_geofence(lat, lon, work_lat, work_lon, work_radius_m) for lat, lon in zip(lats, lons)
    ]

    commute_ids: list[str | None] = [None] * len(lats)
    commute_directions: list[str | None] = [None] * len(lats)

    # State machine: track when we leave one geofence and arrive at another
    in_commute = False
    commute_start_idx = 0
    departure_zone: str | None = None  # "home" or "work"
    commute_counter = 0

    for i in range(len(lats)):
        if not in_commute:
            # Check for departure from a geofence
            if i > 0:
                was_home = at_home[i - 1]
                was_work = at_work[i - 1]

                if was_home and not at_home[i] and not at_work[i]:
                    # Left home
                    in_commute = True
                    commute_start_idx = i
                    departure_zone = "home"
                elif was_work and not at_work[i] and not at_home[i]:
                    # Left work
                    in_commute = True
                    commute_start_idx = i
                    departure_zone = "work"
        else:
            # In commute, check for arrival at destination
            arrived = False
            if departure_zone == "home" and at_work[i]:
                arrived = True
                direction = "morning"
            elif departure_zone == "work" and at_home[i]:
                arrived = True
                direction = "evening"
            elif departure_zone == "home" and at_home[i]:
                # Returned home without reaching work, cancel
                in_commute = False
                departure_zone = None
                continue
            elif departure_zone == "work" and at_work[i]:
                # Returned to work without reaching home, cancel
                in_commute = False
                departure_zone = None
                continue

            if arrived:
                commute_counter += 1
                date_str = local_timestamps[commute_start_idx].strftime("%Y-%m-%d")
                cid = f"{date_str}-{direction}"

                # Label all points from departure to arrival
                for j in range(commute_start_idx, i + 1):
                    commute_ids[j] = cid
                    commute_directions[j] = direction

                in_commute = False
                departure_zone = None

    df = df.with_columns(
        pl.Series("at_home", at_home),
        pl.Series("at_work", at_work),
        pl.Series("commute_id", commute_ids),
        pl.Series("commute_direction", commute_directions),
    )

    return df
