from backend.engine.geo import bearing_deg, haversine_m


def test_haversine_zero_distance():
    assert haversine_m(40.7, -74.4, 40.7, -74.4) == 0.0


def test_haversine_known_distance():
    # 0.01 deg latitude ≈ 1111.9 m
    d = haversine_m(40.70, -74.40, 40.71, -74.40)
    assert 1100 < d < 1125


def test_bearing_north_and_east():
    assert abs(bearing_deg(40.70, -74.40, 40.71, -74.40) - 0.0) < 1.0  # due north
    assert abs(bearing_deg(40.70, -74.40, 40.70, -74.39) - 90.0) < 1.0  # due east


def test_bearing_range():
    b = bearing_deg(40.71, -74.40, 40.70, -74.40)  # due south
    assert 179.0 < b < 181.0
