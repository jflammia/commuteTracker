"""Tests for geographic utility functions."""

from src.processing.geo_utils import haversine_m, in_geofence, speed_kmh


def test_haversine_same_point():
    assert haversine_m(40.0, -74.0, 40.0, -74.0) == 0.0


def test_haversine_known_distance():
    # NYC to LA is approximately 3,944 km
    d = haversine_m(40.7128, -74.0060, 34.0522, -118.2437)
    assert 3_930_000 < d < 3_960_000


def test_haversine_short_distance():
    # ~111m for 0.001 degree latitude at equator
    d = haversine_m(0.0, 0.0, 0.001, 0.0)
    assert 100 < d < 120


def test_speed_kmh():
    # 100m in 10s = 36 km/h
    assert speed_kmh(100, 10) == 36.0


def test_speed_zero_duration():
    assert speed_kmh(100, 0) == 0.0


def test_in_geofence_inside():
    assert in_geofence(40.7500, -74.0000, 40.7500, -74.0000, 100)


def test_in_geofence_outside():
    assert not in_geofence(40.76, -74.0, 40.75, -74.0, 100)


def test_in_geofence_boundary():
    # ~111m away, with 150m radius should be inside
    assert in_geofence(0.001, 0.0, 0.0, 0.0, 150)
