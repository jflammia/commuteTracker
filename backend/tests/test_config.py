from pathlib import Path

from backend.config import Settings, load_settings


def test_defaults(monkeypatch):
    for var in ("CT_DATA_DIR", "CT_S3_BUCKET", "CT_PASSTHROUGH_URL", "CT_ARCHIVE_HOUR_UTC"):
        monkeypatch.delenv(var, raising=False)
    s = load_settings()
    assert s.data_dir == Path("data_v2")
    assert s.s3_bucket is None
    assert s.s3_prefix == "commute-tracker"
    assert s.passthrough_url is None
    assert s.archive_hour_utc == 6


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("CT_DATA_DIR", "/srv/ct")
    monkeypatch.setenv("CT_S3_BUCKET", "my-bucket")
    monkeypatch.setenv("CT_S3_PREFIX", "ct-prod")
    monkeypatch.setenv("CT_PASSTHROUGH_URL", "http://legacy:8080/pub")
    monkeypatch.setenv("CT_ARCHIVE_HOUR_UTC", "7")
    s = load_settings()
    assert s == Settings(
        data_dir=Path("/srv/ct"),
        s3_bucket="my-bucket",
        s3_prefix="ct-prod",
        passthrough_url="http://legacy:8080/pub",
        archive_hour_utc=7,
    )
