from backend.engine.params import EngineParams


def test_defaults_are_sane():
    p = EngineParams()
    assert p.accuracy_max_m == 100.0
    assert p.teleport_speed_mps == 90.0
    assert p.move_speed_mps == 1.4
    assert p.move_window == 5
    assert p.move_min_points == 3
    assert p.move_min_displacement_m == 80.0
    assert p.dwell_radius_m == 75.0
    assert p.dwell_close_s == 300.0
    assert p.gap_close_s == 1800.0
    assert p.stationary_max_mps == 0.5
    assert p.walk_max_mps == 2.5
    assert p.min_segment_s == 30.0
    assert p.min_trip_duration_s == 120.0
    assert p.min_trip_distance_m == 300.0
    assert p.move_min_points <= p.move_window
    assert p.stationary_max_mps < p.walk_max_mps
