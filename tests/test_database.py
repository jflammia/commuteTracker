"""Tests for SQLAlchemy database storage."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.storage.database import Database, LocationRecord


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    database = Database(f"sqlite:///{db_path}")
    database.create_tables()
    return database


@pytest.fixture
def sample_payload():
    return {
        "_type": "location",
        "lat": 40.75,
        "lon": -74.0,
        "tst": 1711440000,
        "acc": 10,
        "vel": 12,
        "batt": 85,
    }


def test_insert_and_count(db, sample_payload):
    record_id = db.insert_record(sample_payload, user="testuser", device="phone")
    assert record_id > 0
    assert db.count_records() == 1


def test_insert_preserves_payload(db, sample_payload):
    db.insert_record(sample_payload, user="testuser", device="phone")

    with db.session() as session:
        record = session.query(LocationRecord).first()
        payload = json.loads(record.payload)
        assert payload["lat"] == 40.75
        assert payload["_type"] == "location"
        assert record.user == "testuser"
        assert record.device == "phone"
        assert record.msg_type == "location"


def test_insert_multiple(db, sample_payload):
    for i in range(10):
        sample_payload["tst"] = 1711440000 + i * 10
        db.insert_record(sample_payload, user="testuser", device="phone")

    assert db.count_records() == 10


def test_unsynced_records(db, sample_payload):
    for i in range(5):
        db.insert_record(sample_payload, user="testuser", device="phone")

    assert db.count_unsynced() == 5

    unsynced = db.get_unsynced_records()
    assert len(unsynced) == 5


def test_mark_synced(db, sample_payload):
    ids = []
    for i in range(5):
        ids.append(db.insert_record(sample_payload, user="testuser", device="phone"))

    assert db.count_unsynced() == 5

    db.mark_synced(ids[:3])
    assert db.count_unsynced() == 2

    db.mark_synced(ids[3:])
    assert db.count_unsynced() == 0


def test_prune_old_synced(db, sample_payload):
    # Insert and sync some records
    ids = []
    for i in range(5):
        ids.append(db.insert_record(sample_payload, user="testuser", device="phone"))

    db.mark_synced(ids)

    # Manually backdate the received_at and s3_synced_at to 100 days ago
    old_date = datetime.now(timezone.utc) - timedelta(days=100)
    with db.session() as session:
        session.query(LocationRecord).update(
            {
                LocationRecord.received_at: old_date,
                LocationRecord.s3_synced_at: old_date,
            },
            synchronize_session=False,
        )
        session.commit()

    # Prune with 90-day retention
    pruned = db.prune_old_synced(retention_days=90)
    assert pruned == 5
    assert db.count_records() == 0


def test_prune_does_not_delete_recent(db, sample_payload):
    ids = []
    for i in range(5):
        ids.append(db.insert_record(sample_payload, user="testuser", device="phone"))

    db.mark_synced(ids)

    # Records are recent, so prune should delete nothing
    pruned = db.prune_old_synced(retention_days=90)
    assert pruned == 0
    assert db.count_records() == 5


def test_prune_does_not_delete_unsynced(db, sample_payload):
    for i in range(5):
        db.insert_record(sample_payload, user="testuser", device="phone")

    # Backdate but don't sync
    old_date = datetime.now(timezone.utc) - timedelta(days=100)
    with db.session() as session:
        session.query(LocationRecord).update(
            {LocationRecord.received_at: old_date},
            synchronize_session=False,
        )
        session.commit()

    # Should not prune unsynced records even if old
    pruned = db.prune_old_synced(retention_days=90)
    assert pruned == 0
    assert db.count_records() == 5


def test_prune_zero_retention_does_nothing(db, sample_payload):
    ids = []
    for i in range(5):
        ids.append(db.insert_record(sample_payload, user="testuser", device="phone"))

    db.mark_synced(ids)

    # retention_days=0 means no pruning
    pruned = db.prune_old_synced(retention_days=0)
    assert pruned == 0
    assert db.count_records() == 5
