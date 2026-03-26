"""Tests for the label store."""

from src.storage.label_store import LabelStore


def test_add_and_retrieve_label(tmp_path):
    store = LabelStore(tmp_path / "labels.json")
    store.add_label("2026-03-26-morning", 1, "driving", "train")
    labels = store.get_labels()
    assert len(labels) == 1
    assert labels[0].corrected_mode == "train"


def test_update_existing_label(tmp_path):
    store = LabelStore(tmp_path / "labels.json")
    store.add_label("2026-03-26-morning", 1, "driving", "train")
    store.add_label("2026-03-26-morning", 1, "driving", "walking")
    labels = store.get_labels()
    assert len(labels) == 1
    assert labels[0].corrected_mode == "walking"


def test_filter_by_commute_id(tmp_path):
    store = LabelStore(tmp_path / "labels.json")
    store.add_label("2026-03-26-morning", 0, "walking", "driving")
    store.add_label("2026-03-26-evening", 1, "driving", "train")
    labels = store.get_labels("2026-03-26-morning")
    assert len(labels) == 1


def test_corrections_map(tmp_path):
    store = LabelStore(tmp_path / "labels.json")
    store.add_label("2026-03-26-morning", 1, "driving", "train")
    store.add_label("2026-03-26-morning", 2, "walking", "stationary")
    corrections = store.get_corrections_map()
    assert corrections[("2026-03-26-morning", 1)] == "train"
    assert corrections[("2026-03-26-morning", 2)] == "stationary"


def test_persistence(tmp_path):
    path = tmp_path / "labels.json"
    store1 = LabelStore(path)
    store1.add_label("2026-03-26-morning", 0, "driving", "train")

    # New instance should load persisted data
    store2 = LabelStore(path)
    assert store2.label_count() == 1
    assert store2.get_labels()[0].corrected_mode == "train"


def test_empty_store(tmp_path):
    store = LabelStore(tmp_path / "labels.json")
    assert store.label_count() == 0
    assert store.get_labels() == []
    assert store.get_corrections_map() == {}
