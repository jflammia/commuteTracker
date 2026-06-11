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
    home_lat: float = 0.0  # 0.0/0.0 (with home_lon) = geofence unset
    home_lon: float = 0.0
    home_radius_m: float = 50.0
    work_lat: float = 0.0  # 0.0/0.0 (with work_lon) = geofence unset
    work_lon: float = 0.0
    work_radius_m: float = 150.0
    path_gtfs_url: str | None = None  # unset = source disabled
    njt_gtfs_url: str | None = None
    path_rt_url: str | None = None
    njt_rt_tripupdates_url: str | None = None
    njt_rt_alerts_url: str | None = None
    source_poll_interval_s: float = 60.0
    gtfs_refresh_interval_s: float = 86400.0

    def __post_init__(self) -> None:
        if not 0 <= self.archive_hour_utc <= 23:
            raise ValueError(f"CT_ARCHIVE_HOUR_UTC={self.archive_hour_utc} must be in 0..23")


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("CT_DATA_DIR", "data_v2")),
        s3_bucket=os.environ.get("CT_S3_BUCKET") or None,
        s3_prefix=os.environ.get("CT_S3_PREFIX", "commute-tracker"),
        s3_region=os.environ.get("CT_S3_REGION") or None,
        passthrough_url=os.environ.get("CT_PASSTHROUGH_URL") or None,
        archive_hour_utc=int(os.environ.get("CT_ARCHIVE_HOUR_UTC", "6")),
        home_lat=float(os.environ.get("CT_HOME_LAT", "0.0")),
        home_lon=float(os.environ.get("CT_HOME_LON", "0.0")),
        home_radius_m=float(os.environ.get("CT_HOME_RADIUS_M", "50")),
        work_lat=float(os.environ.get("CT_WORK_LAT", "0.0")),
        work_lon=float(os.environ.get("CT_WORK_LON", "0.0")),
        work_radius_m=float(os.environ.get("CT_WORK_RADIUS_M", "150")),
        path_gtfs_url=os.environ.get("CT_PATH_GTFS_URL") or None,
        njt_gtfs_url=os.environ.get("CT_NJT_GTFS_URL") or None,
        path_rt_url=os.environ.get("CT_PATH_RT_URL") or None,
        njt_rt_tripupdates_url=os.environ.get("CT_NJT_RT_TRIPUPDATES_URL") or None,
        njt_rt_alerts_url=os.environ.get("CT_NJT_RT_ALERTS_URL") or None,
        source_poll_interval_s=float(os.environ.get("CT_SOURCE_POLL_INTERVAL_S", "60")),
        gtfs_refresh_interval_s=float(os.environ.get("CT_GTFS_REFRESH_INTERVAL_S", "86400")),
    )
