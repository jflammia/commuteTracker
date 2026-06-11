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
    path_rt_url: str | None = None
    njt_username: str | None = None  # CT_NJT_USERNAME — unset = NJT disabled
    njt_password: str | None = None  # CT_NJT_PASSWORD
    njt_api_base: str = (
        "https://raildata.njtransit.com/api/GTFSRT"  # CT_NJT_API_BASE (tests override)
    )
    source_poll_interval_s: float = 60.0
    gtfs_refresh_interval_s: float = 86400.0
    frontend_build_dir: Path | None = None  # unset = no SPA serving

    def __post_init__(self) -> None:
        if not 0 <= self.archive_hour_utc <= 23:
            raise ValueError(f"CT_ARCHIVE_HOUR_UTC={self.archive_hour_utc} must be in 0..23")


def load_settings() -> Settings:
    _default_build = Path(__file__).resolve().parent.parent / "frontend" / "build"
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
        path_rt_url=os.environ.get("CT_PATH_RT_URL") or None,
        njt_username=os.environ.get("CT_NJT_USERNAME") or None,
        njt_password=os.environ.get("CT_NJT_PASSWORD") or None,
        njt_api_base=os.environ.get("CT_NJT_API_BASE", "https://raildata.njtransit.com/api/GTFSRT"),
        source_poll_interval_s=float(os.environ.get("CT_SOURCE_POLL_INTERVAL_S", "60")),
        gtfs_refresh_interval_s=float(os.environ.get("CT_GTFS_REFRESH_INTERVAL_S", "86400")),
        frontend_build_dir=(
            Path(os.environ["CT_FRONTEND_BUILD_DIR"])
            if os.environ.get("CT_FRONTEND_BUILD_DIR")
            else (_default_build if _default_build.is_dir() else None)
        ),
    )
