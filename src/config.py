"""Project configuration. Loaded from environment or .env file."""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Storage paths: in containers, /data is the writable volume mount.
# Default to /data/raw and /data/derived when /data exists (container),
# otherwise use PROJECT_ROOT (local development).
_DATA_BASE = Path("/data") if Path("/data").is_dir() else PROJECT_ROOT
RAW_DATA_DIR = Path(os.environ.get("RAW_DATA_DIR", _DATA_BASE / "raw"))
DERIVED_DATA_DIR = Path(os.environ.get("DERIVED_DATA_DIR", _DATA_BASE / "derived"))

# Database
DATABASE_URL = os.environ.get(
    "DATABASE_URL", f"sqlite:///{PROJECT_ROOT / 'data' / 'commute_tracker.db'}"
)

# Receiver
RECEIVER_HOST = os.environ.get("RECEIVER_HOST", "0.0.0.0")
RECEIVER_PORT = int(os.environ.get("RECEIVER_PORT", "8080"))

# S3-compatible backup (optional)
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "commute-tracker-raw")
S3_SYNC_INTERVAL_SECONDS = int(os.environ.get("S3_SYNC_INTERVAL_SECONDS", "300"))

# Local retention (only when S3 is configured; 0 = no pruning)
LOCAL_RETENTION_DAYS = int(os.environ.get("LOCAL_RETENTION_DAYS", "90"))

# OwnTracks Recorder passthrough (optional, off by default)
RECORDER_URL = os.environ.get("RECORDER_URL", "")

# Home geofence - set these in .env
HOME_LAT = float(os.environ.get("HOME_LAT", "0.0"))
HOME_LON = float(os.environ.get("HOME_LON", "0.0"))
HOME_RADIUS_M = float(os.environ.get("HOME_RADIUS_M", "50"))

# Work geofence - set these in .env
WORK_LAT = float(os.environ.get("WORK_LAT", "0.0"))
WORK_LON = float(os.environ.get("WORK_LON", "0.0"))
WORK_RADIUS_M = float(os.environ.get("WORK_RADIUS_M", "150"))

# Speed thresholds for transport mode classification (km/h)
WALK_MAX_SPEED = 7.0
STATIONARY_MAX_SPEED = 1.0

# Fallback timezone when GPS-based resolution fails (ocean, null coordinates)
TIMEZONE = os.environ.get("TIMEZONE", "America/New_York")
