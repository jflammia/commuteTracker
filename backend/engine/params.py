"""Every engine threshold in one frozen dataclass. Units: m, s, m/s."""

from dataclasses import dataclass


@dataclass(frozen=True)
class EngineParams:
    accuracy_max_m: float = 100.0  # reject points with worse GPS accuracy
    teleport_speed_mps: float = 90.0  # implied speed above this = GPS glitch
    move_speed_mps: float = 1.4  # "moving" threshold (slow walk)
    move_window: int = 5  # recent-point window for start detection
    move_min_points: int = 3  # M of N points must exceed move_speed
    move_min_displacement_m: float = 80.0  # window must also net-displace this far
    dwell_radius_m: float = 75.0  # stationary cluster radius
    dwell_close_s: float = 300.0  # dwell this long closes the trip
    gap_close_s: float = 1800.0  # data gap this long closes the trip
    stationary_max_mps: float = 0.5  # mode: below = stationary
    walk_max_mps: float = 2.5  # mode: below = walk, above = vehicle
    min_segment_s: float = 30.0  # segments shorter than this merge away
    min_trip_duration_s: float = 120.0  # shorter trips are phantom — dropped
    min_trip_distance_m: float = 300.0  # ditto
