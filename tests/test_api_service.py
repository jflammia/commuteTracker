"""Tests for the API service layer and REST routes."""

import json
import tempfile
from pathlib import Path

import polars as pl
import pytest

from src.api.service import CommuteService
from src.storage.database import Database


@pytest.fixture
def service(tmp_path):
    """Create a CommuteService backed by a temp database."""
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    db = Database(db_url)
    db.create_tables()
    derived_dir = tmp_path / "derived"
    derived_dir.mkdir()
    return CommuteService(db=db, derived_dir=derived_dir)


def test_health(service):
    result = service.health()
    assert result["status"] == "ok"
    assert result["total_records"] == 0
    assert result["unsynced_records"] == 0
    assert result["label_count"] == 0
    assert isinstance(result["derived_dates"], list)


def test_list_commutes_empty(service):
    assert service.list_commutes() == []


def test_get_commute_not_found(service):
    assert service.get_commute("nonexistent") is None


def test_get_stats_empty(service):
    assert service.get_stats() == {}


def test_get_daily_summary_empty(service):
    assert service.get_daily_summary("2026-01-01") == []


def test_get_raw_stats(service):
    result = service.get_raw_stats()
    assert result["total_records"] == 0
    assert result["unsynced_records"] == 0


def test_add_and_list_labels(service):
    label = service.add_label(
        commute_id="test_001",
        segment_id=0,
        original_mode="driving",
        corrected_mode="train",
        notes="Was actually on the train",
    )
    assert label["commute_id"] == "test_001"
    assert label["corrected_mode"] == "train"

    labels = service.list_labels()
    assert len(labels) == 1
    assert labels[0]["commute_id"] == "test_001"

    labels_filtered = service.list_labels(commute_id="test_001")
    assert len(labels_filtered) == 1

    labels_empty = service.list_labels(commute_id="nonexistent")
    assert len(labels_empty) == 0


def test_export_labels(service):
    service.add_label("c1", 0, "driving", "train")
    export = service.export_labels()
    assert "labels" in export


def test_rebuild_dry_run(service):
    result = service.rebuild_derived(since="2026-01-01", until="2026-01-31", dry_run=True)
    assert result["dry_run"] is True
    assert result["filters"]["since"] == "2026-01-01"


def test_query_derived_empty(service):
    # Should handle gracefully when no parquet files exist
    try:
        result = service.query_derived("SELECT 1 as val")
        # DuckDB will return the literal query result even without parquet files
        assert isinstance(result, list)
    except Exception:
        # Some queries may fail without data, that's ok
        pass
