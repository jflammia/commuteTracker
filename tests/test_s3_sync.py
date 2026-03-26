"""Tests for S3 sync module.

These tests use mocking to avoid requiring actual S3 infrastructure.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.storage.s3_sync import S3Sync


@pytest.fixture
def raw_dir(tmp_path):
    """Create a temporary raw directory with sample JSONL files."""
    day_dir = tmp_path / "2026" / "03"
    day_dir.mkdir(parents=True)

    file1 = day_dir / "2026-03-26.jsonl"
    file1.write_text('{"lat":40.75,"lon":-74.0,"tst":1}\n')

    file2 = day_dir / "2026-03-27.jsonl"
    file2.write_text('{"lat":40.76,"lon":-74.1,"tst":2}\n')

    return tmp_path


@patch("src.storage.s3_sync.boto3")
def test_sync_uploads_new_files(mock_boto3, raw_dir):
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client

    sync = S3Sync(local_dir=raw_dir, bucket="test-bucket", endpoint_url="http://localhost:9000")
    results = sync.sync()

    assert len(results["uploaded"]) == 2
    assert len(results["errors"]) == 0
    assert mock_client.upload_file.call_count == 2

    # Check S3 keys preserve directory structure
    keys = [call.args[2] for call in mock_client.upload_file.call_args_list]
    assert "raw/2026/03/2026-03-26.jsonl" in keys
    assert "raw/2026/03/2026-03-27.jsonl" in keys


@patch("src.storage.s3_sync.boto3")
def test_sync_skips_unchanged_files(mock_boto3, raw_dir):
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client

    sync = S3Sync(local_dir=raw_dir, bucket="test-bucket")

    # First sync uploads everything
    results1 = sync.sync()
    assert len(results1["uploaded"]) == 2

    # Second sync with no changes skips everything
    results2 = sync.sync()
    assert len(results2["uploaded"]) == 0
    assert len(results2["skipped"]) == 2


@patch("src.storage.s3_sync.boto3")
def test_sync_reuploads_modified_files(mock_boto3, raw_dir):
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client

    sync = S3Sync(local_dir=raw_dir, bucket="test-bucket")

    # First sync
    sync.sync()

    # Modify one file
    file1 = raw_dir / "2026" / "03" / "2026-03-26.jsonl"
    file1.write_text('{"lat":40.75,"lon":-74.0,"tst":1}\n{"lat":40.76,"lon":-74.0,"tst":2}\n')

    # Second sync should re-upload the modified file
    results = sync.sync()
    assert len(results["uploaded"]) == 1
    assert "raw/2026/03/2026-03-26.jsonl" in results["uploaded"]


@patch("src.storage.s3_sync.boto3")
def test_sync_handles_upload_errors(mock_boto3, raw_dir):
    from botocore.exceptions import ClientError

    mock_client = MagicMock()
    mock_client.upload_file.side_effect = ClientError(
        {"Error": {"Code": "500", "Message": "Internal Server Error"}},
        "PutObject",
    )
    mock_boto3.client.return_value = mock_client

    sync = S3Sync(local_dir=raw_dir, bucket="test-bucket")
    results = sync.sync()

    assert len(results["errors"]) == 2
    assert len(results["uploaded"]) == 0


@patch("src.storage.s3_sync.boto3")
def test_ensure_bucket_creates_if_missing(mock_boto3):
    from botocore.exceptions import ClientError

    mock_client = MagicMock()
    mock_client.head_bucket.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}},
        "HeadBucket",
    )
    mock_boto3.client.return_value = mock_client

    sync = S3Sync(local_dir="/tmp", bucket="new-bucket", endpoint_url="http://localhost:9000")
    result = sync.ensure_bucket()

    assert result is True
    mock_client.create_bucket.assert_called_once_with(Bucket="new-bucket")


@patch("src.storage.s3_sync.boto3")
def test_ensure_bucket_exists(mock_boto3):
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client

    sync = S3Sync(local_dir="/tmp", bucket="existing-bucket")
    result = sync.ensure_bucket()

    assert result is True
    mock_client.head_bucket.assert_called_once()
    mock_client.create_bucket.assert_not_called()
