"""Project configuration. Loaded from environment or .env file."""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

RAW_DATA_DIR = Path(os.environ.get("RAW_DATA_DIR", PROJECT_ROOT / "raw"))
DERIVED_DATA_DIR = Path(os.environ.get("DERIVED_DATA_DIR", PROJECT_ROOT / "derived"))

# Home geofence - set these in .env
HOME_LAT = float(os.environ.get("HOME_LAT", "0.0"))
HOME_LON = float(os.environ.get("HOME_LON", "0.0"))
HOME_RADIUS_M = float(os.environ.get("HOME_RADIUS_M", "150"))

# Work geofence - set these in .env
WORK_LAT = float(os.environ.get("WORK_LAT", "0.0"))
WORK_LON = float(os.environ.get("WORK_LON", "0.0"))
WORK_RADIUS_M = float(os.environ.get("WORK_RADIUS_M", "150"))

# Speed thresholds for transport mode classification (km/h)
WALK_MAX_SPEED = 7.0
STATIONARY_MAX_SPEED = 1.0
