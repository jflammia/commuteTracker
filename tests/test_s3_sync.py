"""Tests for S3 sync module (database-to-JSONL export)."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.storage.database import Database, LocationRecord
from src.storage.s3_sync import S3Sync


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
        "received_at": "2026-03-26T08:00:00Z",
    }


@patch("src.storage.s3_sync.boto3")
def test_sync_exports_unsynced_records(mock_boto3, db, sample_payload):
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client

    # Insert records
    for i in range(5):
        db.insert_record(sample_payload, user="testuser", device="phone")

    sync = S3Sync(bucket="test-bucket", endpoint_url="http://localhost:9000")
    results = sync.sync_from_db(db, retention_days=0)

    assert results["synced"] == 5
    assert len(results["uploaded"]) == 1  # All same day = one file
    assert len(results["errors"]) == 0
    assert mock_client.upload_file.call_count == 1

    # Records should now be marked as synced
    assert db.count_unsynced() == 0


@patch("src.storage.s3_sync.boto3")
def test_sync_skips_already_synced(mock_boto3, db, sample_payload):
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client

    for i in range(5):
        db.insert_record(sample_payload, user="testuser", device="phone")

    sync = S3Sync(bucket="test-bucket")

    # First sync
    results1 = sync.sync_from_db(db)
    assert results1["synced"] == 5

    # Second sync with no new records
    results2 = sync.sync_from_db(db)
    assert results2["synced"] == 0
    assert len(results2["uploaded"]) == 0


@patch("src.storage.s3_sync.boto3")
def test_sync_with_pruning(mock_boto3, db, sample_payload):
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client

    ids = []
    for i in range(5):
        ids.append(db.insert_record(sample_payload, user="testuser", device="phone"))

    # Backdate records to 100 days ago
    old_date = datetime.now(timezone.utc) - timedelta(days=100)
    with db.session() as session:
        session.query(LocationRecord).update(
            {LocationRecord.received_at: old_date},
            synchronize_session=False,
        )
        session.commit()

    sync = S3Sync(bucket="test-bucket")
    results = sync.sync_from_db(db, retention_days=90)

    assert results["synced"] == 5
    assert results["pruned"] == 5
    assert db.count_records() == 0


@patch("src.storage.s3_sync.boto3")
def test_sync_no_pruning_without_retention(mock_boto3, db, sample_payload):
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client

    for i in range(5):
        db.insert_record(sample_payload, user="testuser", device="phone")

    # Backdate
    old_date = datetime.now(timezone.utc) - timedelta(days=100)
    with db.session() as session:
        session.query(LocationRecord).update(
            {LocationRecord.received_at: old_date},
            synchronize_session=False,
        )
        session.commit()

    sync = S3Sync(bucket="test-bucket")
    results = sync.sync_from_db(db, retention_days=0)

    assert results["synced"] == 5
    assert results["pruned"] == 0
    assert db.count_records() == 5  # Still there


@patch("src.storage.s3_sync.boto3")
def test_sync_handles_upload_errors(mock_boto3, db, sample_payload):
    from botocore.exceptions import ClientError

    mock_client = MagicMock()
    mock_client.upload_file.side_effect = ClientError(
        {"Error": {"Code": "500", "Message": "Internal Server Error"}},
        "PutObject",
    )
    mock_boto3.client.return_value = mock_client

    for i in range(5):
        db.insert_record(sample_payload, user="testuser", device="phone")

    sync = S3Sync(bucket="test-bucket")
    results = sync.sync_from_db(db)

    assert len(results["errors"]) > 0
    assert results["synced"] == 0
    # Records should still be unsynced
    assert db.count_unsynced() == 5


@patch("src.storage.s3_sync.boto3")
def test_ensure_bucket_creates_if_missing(mock_boto3):
    from botocore.exceptions import ClientError

    mock_client = MagicMock()
    mock_client.head_bucket.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}},
        "HeadBucket",
    )
    mock_boto3.client.return_value = mock_client

    sync = S3Sync(bucket="new-bucket", endpoint_url="http://localhost:9000")
    result = sync.ensure_bucket()

    assert result is True
    mock_client.create_bucket.assert_called_once_with(Bucket="new-bucket")


@patch("src.storage.s3_sync.boto3")
def test_ensure_bucket_exists(mock_boto3):
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client

    sync = S3Sync(bucket="existing-bucket")
    result = sync.ensure_bucket()

    assert result is True
    mock_client.head_bucket.assert_called_once()
    mock_client.create_bucket.assert_not_called()
