from pathlib import Path

import pytest

from backend.config import Settings, load_settings


def test_defaults(monkeypatch):
    for var in (
        "CT_DATA_DIR",
        "CT_S3_BUCKET",
        "CT_S3_PREFIX",
        "CT_S3_REGION",
        "CT_PASSTHROUGH_URL",
        "CT_ARCHIVE_HOUR_UTC",
        "CT_HOME_LAT",
        "CT_HOME_LON",
        "CT_HOME_RADIUS_M",
        "CT_WORK_LAT",
        "CT_WORK_LON",
        "CT_WORK_RADIUS_M",
    ):
        monkeypatch.delenv(var, raising=False)
    s = load_settings()
    assert s.data_dir == Path("data_v2")
    assert s.s3_bucket is None
    assert s.s3_prefix == "commute-tracker"
    assert s.s3_region is None
    assert s.passthrough_url is None
    assert s.archive_hour_utc == 6
    assert s.home_lat == 0.0
    assert s.home_lon == 0.0
    assert s.home_radius_m == 50.0
    assert s.work_lat == 0.0
    assert s.work_lon == 0.0
    assert s.work_radius_m == 150.0


def test_archive_hour_out_of_range_fails_fast(monkeypatch):
    monkeypatch.setenv("CT_ARCHIVE_HOUR_UTC", "25")
    with pytest.raises(ValueError, match="0..23"):
        load_settings()


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("CT_DATA_DIR", "/srv/ct")
    monkeypatch.setenv("CT_S3_BUCKET", "my-bucket")
    monkeypatch.setenv("CT_S3_PREFIX", "ct-prod")
    monkeypatch.setenv("CT_S3_REGION", "us-east-2")
    monkeypatch.setenv("CT_PASSTHROUGH_URL", "http://legacy:8080/pub")
    monkeypatch.setenv("CT_ARCHIVE_HOUR_UTC", "7")
    s = load_settings()
    assert s == Settings(
        data_dir=Path("/srv/ct"),
        s3_bucket="my-bucket",
        s3_prefix="ct-prod",
        s3_region="us-east-2",
        passthrough_url="http://legacy:8080/pub",
        archive_hour_utc=7,
    )


def test_geofence_env_vars(monkeypatch):
    monkeypatch.setenv("CT_HOME_LAT", "40.7")
    monkeypatch.setenv("CT_HOME_LON", "-74.4")
    monkeypatch.setenv("CT_HOME_RADIUS_M", "60")
    monkeypatch.setenv("CT_WORK_LAT", "40.75")
    monkeypatch.setenv("CT_WORK_LON", "-73.99")
    monkeypatch.setenv("CT_WORK_RADIUS_M", "120")
    s = load_settings()
    assert s.home_lat == 40.7
    assert s.home_lon == -74.4
    assert s.home_radius_m == 60.0
    assert s.work_lat == 40.75
    assert s.work_lon == -73.99
    assert s.work_radius_m == 120.0
