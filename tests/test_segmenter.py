"""Tests for the segmenter module."""

import polars as pl

from src.processing.segmenter import (
    classify_transport_mode,
    segment_commute,
    _smooth_modes,
    _assign_segment_ids,
    _merge_short_segments,
)


def test_classify_stationary():
    assert classify_transport_mode(0.0) == "stationary"
    assert classify_transport_mode(0.5) == "stationary"
    assert classify_transport_mode(0.99) == "stationary"


def test_classify_walking():
    assert classify_transport_mode(1.0) == "walking"
    assert classify_transport_mode(4.0) == "walking"
    assert classify_transport_mode(6.9) == "walking"


def test_classify_driving():
    assert classify_transport_mode(7.0) == "driving"
    assert classify_transport_mode(15.0) == "driving"
    assert classify_transport_mode(29.9) == "driving"


def test_classify_train():
    assert classify_transport_mode(30.0) == "train"
    assert classify_transport_mode(80.0) == "train"
    assert classify_transport_mode(120.0) == "train"


def test_smooth_modes_majority_vote():
    # A single "driving" in a run of "walking" should be smoothed out
    modes = ["walking"] * 5 + ["driving"] + ["walking"] * 5
    smoothed = _smooth_modes(modes, window=5)
    assert smoothed[5] == "walking"


def test_smooth_modes_short_list_unchanged():
    modes = ["walking", "driving"]
    smoothed = _smooth_modes(modes, window=5)
    assert smoothed == modes


def test_assign_segment_ids_single_mode():
    modes = ["walking", "walking", "walking"]
    ids = _assign_segment_ids(modes)
    assert ids == [0, 0, 0]


def test_assign_segment_ids_transitions():
    modes = ["walking", "walking", "driving", "driving", "train"]
    ids = _assign_segment_ids(modes)
    assert ids == [0, 0, 1, 1, 2]


def test_assign_segment_ids_empty():
    assert _assign_segment_ids([]) == []


def test_merge_short_segments():
    # A 10s segment between two longer ones should be merged
    modes = ["walking", "driving", "walking", "walking"]
    segment_ids = [0, 1, 2, 2]
    time_deltas = [100.0, 10.0, 50.0, 50.0]  # segment 1 is only 10s
    new_modes, new_ids = _merge_short_segments(modes, segment_ids, time_deltas)
    # The short driving segment should merge into walking
    assert new_modes[1] == "walking"
    assert new_ids == [0, 0, 0, 0]


def test_segment_commute_adds_columns():
    df = pl.DataFrame({
        "speed_kmh": [0.5, 3.0, 5.0, 15.0, 50.0],
        "time_delta_s": [0.0, 10.0, 10.0, 10.0, 10.0],
    })
    result = segment_commute(df)
    assert "transport_mode" in result.columns
    assert "segment_id" in result.columns


def test_segment_commute_classifies_modes():
    # Create a clear sequence: stationary -> walking -> driving -> train
    df = pl.DataFrame({
        "speed_kmh": [0.0] * 10 + [4.0] * 10 + [20.0] * 10 + [60.0] * 10,
        "time_delta_s": [10.0] * 40,
    })
    result = segment_commute(df)
    modes = result["transport_mode"].to_list()
    # First points should be stationary, last should be train
    assert modes[0] == "stationary"
    assert modes[-1] == "train"


def test_segment_commute_empty():
    df = pl.DataFrame({"speed_kmh": [], "time_delta_s": []})
    result = segment_commute(df)
    assert "transport_mode" in result.columns
    assert "segment_id" in result.columns
    assert result.is_empty()
