from backend.config import Settings
from backend.engine.geofence import Geofence, geofences_from_settings, resolve_geofence

HOME = Geofence(name="home", lat=40.7000, lon=-74.4000, radius_m=50.0)
WORK = Geofence(name="work", lat=40.7500, lon=-74.1700, radius_m=150.0)
GFS = [HOME, WORK]


def test_inside_home():
    assert resolve_geofence(GFS, 40.7000, -74.4000, None) == "home"


def test_outside_everything():
    assert resolve_geofence(GFS, 40.7200, -74.3000, None) is None


def test_hysteresis_keeps_membership_within_exit_band():
    # ~60 m north of home center: outside 50 m entry radius, inside 75 m exit radius
    lat_60m_north = 40.7000 + 60 / 111120
    assert resolve_geofence(GFS, lat_60m_north, -74.4000, None) is None  # no entry
    assert resolve_geofence(GFS, lat_60m_north, -74.4000, "home") == "home"  # no exit


def test_exit_beyond_band():
    lat_100m_north = 40.7000 + 100 / 111120
    assert resolve_geofence(GFS, lat_100m_north, -74.4000, "home") is None


def test_transition_from_current_to_new_fence():
    # outside home's exit band, inside work's entry radius → switches to work
    assert resolve_geofence(GFS, 40.7500, -74.1700, "home") == "work"


def test_unknown_current_falls_back_to_fresh_scan():
    # current names a fence that no longer exists (config changed) — no crash
    assert resolve_geofence(GFS, 40.7000, -74.4000, "office") == "home"
    assert resolve_geofence(GFS, 40.7200, -74.3000, "office") is None


def test_geofences_from_settings_skips_unset():
    s = Settings(
        data_dir=None,
        s3_bucket=None,
        s3_prefix="x",
        s3_region=None,
        passthrough_url=None,
        archive_hour_utc=6,
        home_lat=40.7,
        home_lon=-74.4,
        home_radius_m=50.0,
        # work left at 0,0 default → omitted
    )
    gfs = geofences_from_settings(s)
    assert [g.name for g in gfs] == ["home"]
