"""Tests for ML feature engineering and baseline model."""

import polars as pl
import pytest

from src.ml.features import (
    FEATURE_COLUMNS,
    build_training_set,
    extract_point_features,
    _bearing,
    _mean,
    _std,
)


# --- Helper ---


def _make_enriched_df(n: int = 50) -> pl.DataFrame:
    """Create a synthetic enriched DataFrame for testing."""
    from datetime import datetime, timezone, timedelta

    base_time = datetime(2026, 3, 26, 8, 0, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        rows.append(
            {
                "lat": 40.75 + i * 0.001,
                "lon": -74.00 + i * 0.0005,
                "tst": 1711440000 + i * 10,
                "speed_kmh": 5.0 + i * 1.5,  # gradually increasing
                "distance_m": 15.0 + i * 2.0,
                "time_delta_s": 10.0,
                "timestamp": base_time + timedelta(seconds=i * 10),
                "transport_mode": "walking" if i < 15 else ("driving" if i < 35 else "train"),
                "segment_id": 0 if i < 15 else (1 if i < 35 else 2),
            }
        )
    return pl.DataFrame(rows)


# --- Feature extraction ---


def test_extract_features_adds_columns():
    df = _make_enriched_df()
    result = extract_point_features(df)
    for col in FEATURE_COLUMNS:
        assert col in result.columns, f"Missing feature: {col}"


def test_extract_features_correct_length():
    df = _make_enriched_df(30)
    result = extract_point_features(df)
    assert len(result) == 30


def test_extract_features_empty():
    df = pl.DataFrame(
        {
            "lat": [],
            "lon": [],
            "tst": [],
            "speed_kmh": [],
            "distance_m": [],
            "time_delta_s": [],
            "timestamp": [],
        }
    )
    result = extract_point_features(df)
    assert result.is_empty()


def test_acceleration_computed():
    df = _make_enriched_df()
    result = extract_point_features(df)
    accels = result["acceleration"].to_list()
    assert accels[0] == 0.0  # first point
    # Speed increases by 1.5 km/h every 10s, so acceleration should be ~0.15
    assert abs(accels[5] - 0.15) < 0.01


def test_bearing_computation():
    # North
    b = _bearing(40.0, -74.0, 41.0, -74.0)
    assert abs(b - 0.0) < 1.0 or abs(b - 360.0) < 1.0

    # East
    b = _bearing(40.0, -74.0, 40.0, -73.0)
    assert abs(b - 90.0) < 5.0


def test_stop_duration():
    df = _make_enriched_df()
    # Override first 5 points as stopped
    speeds = df["speed_kmh"].to_list()
    for i in range(5):
        speeds[i] = 0.0
    df = df.with_columns(pl.Series("speed_kmh", speeds))

    result = extract_point_features(df)
    stop_dur = result["stop_duration_s"].to_list()
    # First point has time_delta=10, so stop_duration starts at 10
    assert stop_dur[0] == 10.0
    assert stop_dur[4] == 50.0  # accumulated 5 * 10s


def test_temporal_features():
    df = _make_enriched_df()
    result = extract_point_features(df)
    assert "hour_sin" in result.columns
    assert "hour_cos" in result.columns
    assert "day_of_week" in result.columns


def test_rolling_features():
    df = _make_enriched_df()
    result = extract_point_features(df)
    assert "speed_cv_w10" in result.columns
    cvs = result["speed_cv_w10"].to_list()
    assert all(v >= 0.0 for v in cvs)


# --- Training set ---


def test_build_training_set():
    df = _make_enriched_df()
    df = extract_point_features(df)
    features, labels = build_training_set(df)
    assert len(features) == len(labels)
    assert len(features) == 50
    assert set(labels.to_list()) == {"walking", "driving", "train"}


def test_build_training_set_drops_nulls():
    df = _make_enriched_df(10)
    df = extract_point_features(df)
    # Set some labels to null
    modes = df["transport_mode"].to_list()
    modes[0] = None
    modes[1] = None
    df = df.with_columns(pl.Series("transport_mode", modes))
    features, labels = build_training_set(df)
    assert len(features) == 8


# --- Utility functions ---


def test_mean():
    assert _mean([1.0, 2.0, 3.0]) == 2.0
    assert _mean([]) == 0.0


def test_std():
    assert _std([1.0]) == 0.0
    assert _std([]) == 0.0
    assert abs(_std([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]) - 2.138) < 0.01


# --- Model (requires scikit-learn) ---


def test_model_train_and_predict():
    pytest.importorskip("sklearn")

    from src.ml.model import BaselineModel

    df = _make_enriched_df(100)
    df = extract_point_features(df)
    features, labels = build_training_set(df)

    model = BaselineModel()
    assert not model.is_trained

    metrics = model.train(features, labels, test_fraction=0.0)
    assert model.is_trained
    assert metrics.accuracy > 0.5
    assert metrics.sample_count == 100

    predictions = model.predict(df)
    assert len(predictions) == 100
    assert all(p in ["stationary", "walking", "driving", "train"] for p in predictions)


def test_model_score_protocol():
    pytest.importorskip("sklearn")

    from src.ml.model import BaselineModel
    from src.processing.classifiers.base import ModeScores

    df = _make_enriched_df(100)
    df = extract_point_features(df)
    features, labels = build_training_set(df)

    model = BaselineModel()
    model.train(features, labels, test_fraction=0.0)

    scores = model.score(df)
    assert len(scores) == 100
    assert isinstance(scores[0], ModeScores)


def test_model_untrained_returns_empty_scores():
    from src.ml.model import BaselineModel

    model = BaselineModel()
    df = _make_enriched_df(5)
    scores = model.score(df)
    assert len(scores) == 5
    assert all(s.stationary == 0.0 and s.walking == 0.0 for s in scores)


def test_model_save_and_load(tmp_path):
    pytest.importorskip("sklearn")

    from src.ml.model import BaselineModel

    df = _make_enriched_df(100)
    df = extract_point_features(df)
    features, labels = build_training_set(df)

    # Train and save
    model1 = BaselineModel()
    model1.train(features, labels, test_fraction=0.0)
    model_path = tmp_path / "baseline"
    model1.save(model_path)

    assert (tmp_path / "baseline.joblib").exists()
    assert (tmp_path / "baseline.json").exists()

    # Load into new model
    model2 = BaselineModel()
    model2.load(model_path)
    assert model2.is_trained

    # Predictions should match
    preds1 = model1.predict(df)
    preds2 = model2.predict(df)
    assert preds1 == preds2


def test_model_too_few_samples():
    pytest.importorskip("sklearn")

    from src.ml.model import BaselineModel

    df = _make_enriched_df(5)
    df = extract_point_features(df)
    features, labels = build_training_set(df)

    model = BaselineModel()
    with pytest.raises(ValueError, match="at least 10"):
        model.train(features, labels)


def test_model_feature_importances():
    pytest.importorskip("sklearn")

    from src.ml.model import BaselineModel

    df = _make_enriched_df(100)
    df = extract_point_features(df)
    features, labels = build_training_set(df)

    model = BaselineModel()
    metrics = model.train(features, labels, test_fraction=0.0)

    assert len(metrics.feature_importances) == len(FEATURE_COLUMNS)
    assert sum(metrics.feature_importances.values()) > 0.99  # should sum to ~1.0
