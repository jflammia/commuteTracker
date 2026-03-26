"""Tests for the label store (SQLAlchemy-backed)."""

import pytest

from src.storage.database import Database
from src.storage.label_store import LabelStore


@pytest.fixture
def label_store(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'test.db'}")
    db.create_tables()
    return LabelStore(db)


def test_add_and_retrieve_label(label_store):
    label_store.add_label("2026-03-26-morning", 1, "driving", "train")
    labels = label_store.get_labels()
    assert len(labels) == 1
    assert labels[0].corrected_mode == "train"


def test_update_existing_label(label_store):
    label_store.add_label("2026-03-26-morning", 1, "driving", "train")
    label_store.add_label("2026-03-26-morning", 1, "driving", "walking")
    labels = label_store.get_labels()
    assert len(labels) == 1
    assert labels[0].corrected_mode == "walking"


def test_filter_by_commute_id(label_store):
    label_store.add_label("2026-03-26-morning", 0, "walking", "driving")
    label_store.add_label("2026-03-26-evening", 1, "driving", "train")
    labels = label_store.get_labels("2026-03-26-morning")
    assert len(labels) == 1


def test_corrections_map(label_store):
    label_store.add_label("2026-03-26-morning", 1, "driving", "train")
    label_store.add_label("2026-03-26-morning", 2, "walking", "stationary")
    corrections = label_store.get_corrections_map()
    assert corrections[("2026-03-26-morning", 1)] == "train"
    assert corrections[("2026-03-26-morning", 2)] == "stationary"


def test_persistence(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'test.db'}")
    db.create_tables()

    store1 = LabelStore(db)
    store1.add_label("2026-03-26-morning", 0, "driving", "train")

    # New LabelStore instance against same DB
    store2 = LabelStore(db)
    assert store2.label_count() == 1
    assert store2.get_labels()[0].corrected_mode == "train"


def test_empty_store(label_store):
    assert label_store.label_count() == 0
    assert label_store.get_labels() == []
    assert label_store.get_corrections_map() == {}


def test_export_json(label_store):
    label_store.add_label("2026-03-26-morning", 0, "driving", "train", notes="test note")
    export = label_store.export_json()
    assert "labels" in export
    assert len(export["labels"]) == 1
    assert export["labels"][0]["notes"] == "test note"


def test_label_with_notes(label_store):
    label_store.add_label("2026-03-26-morning", 0, "driving", "train", notes="NJT rail line")
    labels = label_store.get_labels()
    assert labels[0].notes == "NJT rail line"


def test_label_to_dict(label_store):
    label_store.add_label("2026-03-26-morning", 0, "driving", "train")
    label = label_store.get_labels()[0]
    d = label.to_dict()
    assert d["commute_id"] == "2026-03-26-morning"
    assert d["segment_id"] == 0
    assert d["original_mode"] == "driving"
    assert d["corrected_mode"] == "train"
    assert "labeled_at" in d
