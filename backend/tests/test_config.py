from pathlib import Path

import pytest

from backend.config import load_settings


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
        "CT_PATH_GTFS_URL",
        "CT_PATH_RT_URL",
        "CT_NJT_USERNAME",
        "CT_NJT_PASSWORD",
        "CT_NJT_API_BASE",
        "CT_SOURCE_POLL_INTERVAL_S",
        "CT_GTFS_REFRESH_INTERVAL_S",
        "CT_COMMUTE_SOURCE",
        "CT_BOARD_STOP_ID",
        "CT_ALIGHT_STOP_ID",
        "CT_ARRIVE_BY_LOCAL",
        "CT_ACCESS_DISTANCE_M",
        "CT_EGRESS_DISTANCE_M",
        "OWNTRACKS_USERNAME",
        "OWNTRACKS_PASSWORD",
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
    assert s.path_gtfs_url is None
    assert s.source_poll_interval_s == 60.0
    assert s.commute_source is None
    assert s.owntracks_username is None
    assert s.owntracks_password is None


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
    monkeypatch.delenv("CT_FRONTEND_BUILD_DIR", raising=False)
    s = load_settings()
    assert s.data_dir == Path("/srv/ct")
    assert s.s3_bucket == "my-bucket"
    assert s.s3_prefix == "ct-prod"
    assert s.s3_region == "us-east-2"
    assert s.passthrough_url == "http://legacy:8080/pub"
    assert s.archive_hour_utc == 7


def test_frontend_build_dir_env_override(monkeypatch, tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    monkeypatch.setenv("CT_FRONTEND_BUILD_DIR", str(build))
    s = load_settings()
    assert s.frontend_build_dir == build


def test_frontend_build_dir_env_nonexistent_still_used(monkeypatch, tmp_path):
    """CT_FRONTEND_BUILD_DIR env var wins even when the path doesn't exist."""
    dummy = tmp_path / "no-such-build"
    monkeypatch.setenv("CT_FRONTEND_BUILD_DIR", str(dummy))
    s = load_settings()
    assert s.frontend_build_dir == dummy


def test_source_env_vars(monkeypatch):
    monkeypatch.setenv("CT_PATH_GTFS_URL", "https://example.com/path.zip")
    monkeypatch.setenv("CT_PATH_RT_URL", "https://example.com/path-rt")
    monkeypatch.setenv("CT_NJT_USERNAME", "myuser")
    monkeypatch.setenv("CT_NJT_PASSWORD", "mypass")
    monkeypatch.setenv("CT_NJT_API_BASE", "https://custom-njt.example/api/GTFSRT")
    monkeypatch.setenv("CT_SOURCE_POLL_INTERVAL_S", "30")
    monkeypatch.setenv("CT_GTFS_REFRESH_INTERVAL_S", "43200")
    s = load_settings()
    assert s.path_gtfs_url == "https://example.com/path.zip"
    assert s.path_rt_url == "https://example.com/path-rt"
    assert s.njt_username == "myuser"
    assert s.njt_password == "mypass"
    assert s.njt_api_base == "https://custom-njt.example/api/GTFSRT"
    assert s.source_poll_interval_s == 30.0
    assert s.gtfs_refresh_interval_s == 43200.0


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


def test_optimizer_env_vars(monkeypatch):
    monkeypatch.setenv("CT_COMMUTE_SOURCE", "gtfs_njt")
    monkeypatch.setenv("CT_BOARD_STOP_ID", "MP")
    monkeypatch.setenv("CT_ALIGHT_STOP_ID", "NYP")
    monkeypatch.setenv("CT_ARRIVE_BY_LOCAL", "09:00")
    monkeypatch.setenv("CT_ACCESS_DISTANCE_M", "500")
    monkeypatch.setenv("CT_EGRESS_DISTANCE_M", "650")
    s = load_settings()
    assert s.commute_source == "gtfs_njt"
    assert s.board_stop_id == "MP"
    assert s.alight_stop_id == "NYP"
    assert s.arrive_by_local == "09:00"
    assert s.access_distance_m == 500.0
    assert s.egress_distance_m == 650.0


def test_owntracks_auth_env_vars(monkeypatch):
    monkeypatch.setenv("OWNTRACKS_USERNAME", "owntracks")
    monkeypatch.setenv("OWNTRACKS_PASSWORD", "secret-pw-123")
    s = load_settings()
    assert s.owntracks_username == "owntracks"
    assert s.owntracks_password == "secret-pw-123"
