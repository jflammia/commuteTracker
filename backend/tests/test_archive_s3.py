import boto3
import pytest
from moto import mock_aws

from backend.config import Settings
from backend.storage.archive import Archiver
from backend.storage.raw import RawStore


@pytest.fixture
def s3_settings(tmp_path):
    return Settings(
        data_dir=tmp_path,
        s3_bucket="ct-test",
        s3_prefix="commute-tracker",
        s3_region="us-east-1",
        passthrough_url=None,
        archive_hour_utc=6,
    )


def _seed(s3_settings):
    store = RawStore(s3_settings.data_dir)
    store.append(
        "owntracks",
        {
            "received_at": "2026-06-09T01:00:00+00:00",
            "user": "j",
            "device": "d",
            "payload": {"tst": 1},
        },
    )


@mock_aws
def test_uploads_to_partitioned_key(s3_settings):
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="ct-test")
    _seed(s3_settings)
    results = Archiver(s3_settings).run(today="2026-06-10")
    assert results[0].ok
    objs = boto3.client("s3").list_objects_v2(Bucket="ct-test")["Contents"]
    keys = [o["Key"] for o in objs]
    assert keys == ["commute-tracker/raw/owntracks/year=2026/month=06/day=09/data.parquet"]


@mock_aws
def test_upload_failure_keeps_raw_file(s3_settings):
    # bucket does not exist → upload fails → raw file must survive
    _seed(s3_settings)
    results = Archiver(s3_settings).run(today="2026-06-10")
    assert results[0].ok is False
    raw = s3_settings.data_dir / "raw" / "owntracks" / "2026-06-09.jsonl"
    assert raw.exists()


@mock_aws
def test_readback_mismatch_keeps_raw_file(s3_settings, monkeypatch):
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="ct-test")
    _seed(s3_settings)
    archiver = Archiver(s3_settings)

    # Force lazy init of the real moto client so we can wrap it.
    real_client = archiver._s3

    class _TamperedBody:
        def read(self):
            return b"tampered bytes"

    class _StubClient:
        """Delegates everything to the real moto client except get_object."""

        def put_object(self, **kwargs):
            return real_client.put_object(**kwargs)

        def get_object(self, **kwargs):
            return {"Body": _TamperedBody()}

    monkeypatch.setattr(archiver, "_s3_client", _StubClient())
    results = archiver.run(today="2026-06-10")
    assert results[0].ok is False
    assert "checksum mismatch" in results[0].error
    assert (s3_settings.data_dir / "raw" / "owntracks" / "2026-06-09.jsonl").exists()
