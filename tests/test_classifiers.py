"""Tests for the transport mode classifier system."""

import json

import polars as pl
import pytest

from src.processing.classifiers.base import ModeScores
from src.processing.classifiers.speed import SpeedClassifier
from src.processing.classifiers.speed_variance import SpeedVarianceClassifier
from src.processing.classifiers.waypoint import Waypoint, WaypointClassifier
from src.processing.classifiers.corridor import Corridor, CorridorClassifier
from src.processing.classifiers.ensemble import (
    EnsembleClassifier,
    ClassifierEntry,
    build_ensemble,
    load_zones_config,
)


# --- ModeScores ---

def test_mode_scores_winner():
    s = ModeScores(stationary=0.1, walking=0.3, driving=0.8, train=0.2)
    assert s.winner() == "driving"


def test_mode_scores_add():
    a = ModeScores(walking=1.0, train=0.5)
    b = ModeScores(walking=0.5, train=1.0)
    c = a + b
    assert c.walking == 1.5
    assert c.train == 1.5


def test_mode_scores_scale():
    s = ModeScores(driving=1.0, train=0.5)
    scaled = s.scale(2.0)
    assert scaled.driving == 2.0
    assert scaled.train == 1.0


# --- SpeedClassifier ---

def _speed_df(speeds: list[float]) -> pl.DataFrame:
    return pl.DataFrame({"speed_kmh": speeds})


def test_speed_classifier_stationary():
    c = SpeedClassifier()
    scores = c.score(_speed_df([0.0, 0.5]))
    assert scores[0].winner() == "stationary"
    assert scores[1].winner() == "stationary"


def test_speed_classifier_walking():
    c = SpeedClassifier()
    scores = c.score(_speed_df([3.0, 6.0]))
    assert scores[0].winner() == "walking"
    assert scores[1].winner() == "walking"


def test_speed_classifier_driving():
    c = SpeedClassifier()
    scores = c.score(_speed_df([10.0, 25.0]))
    assert scores[0].winner() == "driving"
    assert scores[1].winner() == "driving"


def test_speed_classifier_train():
    c = SpeedClassifier()
    scores = c.score(_speed_df([35.0, 80.0]))
    assert scores[0].winner() == "train"
    assert scores[1].winner() == "train"


def test_speed_classifier_custom_thresholds():
    c = SpeedClassifier(stationary_max_kmh=2.0, walk_max_kmh=10.0, train_min_kmh=50.0)
    scores = c.score(_speed_df([1.5, 8.0, 40.0, 60.0]))
    assert scores[0].winner() == "stationary"
    assert scores[1].winner() == "walking"
    assert scores[2].winner() == "driving"
    assert scores[3].winner() == "train"


# --- SpeedVarianceClassifier ---

def test_speed_variance_no_signal_at_low_speed():
    c = SpeedVarianceClassifier()
    scores = c.score(_speed_df([5.0] * 20))
    # Low speed: should produce no signal (all zeros)
    for s in scores:
        assert s.driving == 0.0
        assert s.train == 0.0


def test_speed_variance_smooth_high_speed_favors_train():
    c = SpeedVarianceClassifier(window_size=5, smooth_cv_threshold=0.25)
    # Constant 60 km/h -- very smooth, should favor train
    scores = c.score(_speed_df([60.0] * 20))
    for s in scores[2:-2]:  # skip edges where window is incomplete
        assert s.train > 0.0, "Smooth high speed should produce train signal"


def test_speed_variance_variable_speed_favors_driving():
    c = SpeedVarianceClassifier(window_size=5, smooth_cv_threshold=0.25)
    # Alternating speeds -- variable, should favor driving
    speeds = [20.0, 40.0, 15.0, 45.0, 20.0, 35.0, 10.0, 50.0, 25.0, 40.0,
              15.0, 45.0, 20.0, 35.0, 10.0, 50.0, 25.0, 40.0, 15.0, 45.0]
    scores = c.score(_speed_df(speeds))
    driving_signals = [s.driving for s in scores if s.driving > 0]
    assert len(driving_signals) > 0, "Variable speed should produce driving signals"


# --- WaypointClassifier ---

def test_waypoint_contains():
    wp = Waypoint(name="Station", lat=40.75, lon=-74.00, radius_m=100)
    assert wp.contains(40.75, -74.00)
    assert not wp.contains(40.76, -74.00)  # ~1.1km away


def test_waypoint_classifier_mode_hint():
    wp = Waypoint(name="Station", lat=40.75, lon=-74.00, radius_m=500, mode_hint="train")
    c = WaypointClassifier(waypoints=[wp])
    df = pl.DataFrame({
        "lat": [40.75, 40.80],
        "lon": [-74.00, -73.95],
    })
    scores = c.score(df)
    assert scores[0].train == 1.0  # inside waypoint
    assert scores[1].train == 0.0  # outside waypoint


def test_waypoint_classifier_no_hint():
    wp = Waypoint(name="Transition", lat=40.75, lon=-74.00, radius_m=100, mode_hint=None)
    c = WaypointClassifier(waypoints=[wp])
    df = pl.DataFrame({"lat": [40.75], "lon": [-74.00]})
    scores = c.score(df)
    # No mode_hint means no score contribution
    assert scores[0] == ModeScores()


def test_waypoint_boundaries():
    wp = Waypoint(name="Station", lat=40.75, lon=-74.00, radius_m=100)
    c = WaypointClassifier(waypoints=[wp])
    df = pl.DataFrame({
        "lat": [40.80, 40.80, 40.75, 40.75, 40.80],
        "lon": [-73.95, -73.95, -74.00, -74.00, -73.95],
    })
    boundaries = c.get_boundary_indices(df)
    assert 2 in boundaries  # entering waypoint zone
    assert 4 in boundaries  # leaving waypoint zone


def test_waypoint_from_dict():
    d = {"name": "Test", "lat": 40.0, "lon": -74.0, "radius_m": 50, "mode_hint": "train"}
    wp = Waypoint.from_dict(d)
    assert wp.name == "Test"
    assert wp.mode_hint == "train"


def test_waypoint_roundtrip():
    wp = Waypoint(name="Test", lat=40.0, lon=-74.0, radius_m=50, mode_hint="driving")
    assert Waypoint.from_dict(wp.to_dict()).name == wp.name


# --- CorridorClassifier ---

def test_corridor_contains_point_on_line():
    c = Corridor(
        name="Rail",
        mode="train",
        points=[(40.75, -74.00), (40.85, -73.95)],
        buffer_m=200,
    )
    # Point right on the midpoint of the corridor
    assert c.contains(40.80, -73.975)


def test_corridor_rejects_distant_point():
    c = Corridor(
        name="Rail",
        mode="train",
        points=[(40.75, -74.00), (40.85, -73.95)],
        buffer_m=200,
    )
    # Point far from corridor
    assert not c.contains(41.00, -73.80)


def test_corridor_classifier_scores():
    corridor = Corridor(
        name="Rail",
        mode="train",
        points=[(40.75, -74.00), (40.85, -73.95)],
        buffer_m=500,
    )
    c = CorridorClassifier(corridors=[corridor])
    df = pl.DataFrame({
        "lat": [40.80, 41.00],
        "lon": [-73.975, -73.80],
    })
    scores = c.score(df)
    assert scores[0].train > 0.0  # near corridor
    assert scores[1].train == 0.0  # far from corridor


def test_corridor_confidence_scales_with_distance():
    corridor = Corridor(
        name="Rail",
        mode="train",
        points=[(40.75, -74.00)],
        buffer_m=1000,
    )
    c = CorridorClassifier(corridors=[corridor])
    df = pl.DataFrame({
        "lat": [40.75, 40.752],  # exact center vs slightly off
        "lon": [-74.00, -74.00],
    })
    scores = c.score(df)
    assert scores[0].train > scores[1].train  # closer = higher confidence


def test_corridor_from_dict():
    d = {
        "name": "Test",
        "mode": "train",
        "points": [[40.75, -74.00], [40.85, -73.95]],
        "buffer_m": 200,
    }
    c = Corridor.from_dict(d)
    assert c.name == "Test"
    assert len(c.points) == 2
    assert c.buffer_m == 200


# --- EnsembleClassifier ---

def test_ensemble_zero_config():
    ens = build_ensemble(zones_config=None)
    assert len(ens.entries) == 2  # speed + speed_variance
    assert ens.entries[0].classifier.name == "speed"
    assert ens.entries[1].classifier.name == "speed_variance"


def test_ensemble_with_waypoints():
    config = {
        "waypoints": [
            {"name": "Station", "lat": 40.75, "lon": -74.00, "radius_m": 100, "mode_hint": "train"}
        ],
    }
    ens = build_ensemble(zones_config=config)
    assert len(ens.entries) == 3  # speed + variance + waypoint


def test_ensemble_with_corridors():
    config = {
        "corridors": [
            {"name": "Rail", "mode": "train", "points": [[40.75, -74.00]], "buffer_m": 100}
        ],
    }
    ens = build_ensemble(zones_config=config)
    assert len(ens.entries) == 3  # speed + variance + corridor


def test_ensemble_full_config():
    config = {
        "waypoints": [
            {"name": "Station", "lat": 40.75, "lon": -74.00, "radius_m": 100, "mode_hint": "train"}
        ],
        "corridors": [
            {"name": "Rail", "mode": "train", "points": [[40.75, -74.00]], "buffer_m": 100}
        ],
    }
    ens = build_ensemble(zones_config=config)
    assert len(ens.entries) == 4  # speed + variance + waypoint + corridor


def test_ensemble_classify_basic():
    ens = build_ensemble(zones_config=None)
    df = pl.DataFrame({
        "speed_kmh": [0.0, 4.0, 20.0, 60.0],
        "lat": [40.0, 40.0, 40.0, 40.0],
        "lon": [-74.0, -74.0, -74.0, -74.0],
        "time_delta_s": [0.0, 10.0, 10.0, 10.0],
        "timestamp": [1000, 1010, 1020, 1030],
    })
    modes = ens.classify(df)
    assert modes[0] == "stationary"
    assert modes[1] == "walking"
    assert modes[2] == "driving"
    assert modes[3] == "train"


def test_ensemble_classify_with_confidence():
    ens = build_ensemble(zones_config=None)
    df = pl.DataFrame({
        "speed_kmh": [0.0, 60.0],
        "lat": [40.0, 40.0],
        "lon": [-74.0, -74.0],
        "time_delta_s": [0.0, 10.0],
        "timestamp": [1000, 1010],
    })
    results = ens.classify_with_confidence(df)
    assert len(results) == 2
    assert results[0][0] == "stationary"
    assert isinstance(results[0][1], ModeScores)


def test_ensemble_empty_df():
    ens = build_ensemble(zones_config=None)
    df = pl.DataFrame({"speed_kmh": [], "lat": [], "lon": [], "time_delta_s": [], "timestamp": []})
    assert ens.classify(df) == []


def test_ensemble_waypoint_overrides_speed():
    """A waypoint with strong weight should override speed-based classification."""
    config = {
        "waypoints": [
            {"name": "Station", "lat": 40.75, "lon": -74.00, "radius_m": 500, "mode_hint": "train"}
        ],
    }
    ens = build_ensemble(zones_config=config)
    # Point at low speed (normally "walking") but inside train station waypoint
    df = pl.DataFrame({
        "speed_kmh": [3.0],
        "lat": [40.75],
        "lon": [-74.00],
        "time_delta_s": [10.0],
        "timestamp": [1000],
    })
    modes = ens.classify(df)
    # Waypoint (weight 1.5) + speed (weight 1.0 for walking) -- train should win
    # waypoint gives train=1.5, speed gives walking=1.0
    assert modes[0] == "train"


# --- Config loading ---

def test_load_zones_config_from_file(tmp_path):
    config_file = tmp_path / "zones.json"
    config = {
        "waypoints": [{"name": "Test", "lat": 0, "lon": 0, "radius_m": 100}],
    }
    config_file.write_text(json.dumps(config))

    loaded = load_zones_config(config_file)
    assert loaded is not None
    assert len(loaded["waypoints"]) == 1


def test_load_zones_config_missing():
    loaded = load_zones_config("/nonexistent/path/zones.json")
    assert loaded is None
