import pytest

from backend.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path,
        s3_bucket=None,
        s3_prefix="commute-tracker",
        passthrough_url=None,
        archive_hour_utc=6,
    )
