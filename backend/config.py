"""Environment-variable configuration. CT_* namespace, no dotenv."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    s3_bucket: str | None
    s3_prefix: str
    s3_region: str | None
    passthrough_url: str | None
    archive_hour_utc: int


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("CT_DATA_DIR", "data_v2")),
        s3_bucket=os.environ.get("CT_S3_BUCKET") or None,
        s3_prefix=os.environ.get("CT_S3_PREFIX", "commute-tracker"),
        s3_region=os.environ.get("CT_S3_REGION") or None,
        passthrough_url=os.environ.get("CT_PASSTHROUGH_URL") or None,
        archive_hour_utc=int(os.environ.get("CT_ARCHIVE_HOUR_UTC", "6")),
    )
