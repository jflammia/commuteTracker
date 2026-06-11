# Rewrite Phase 2: Trip Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic, replayable trip engine — hygiene → enrichment → geofences → trip detection → segmentation/mode heuristic — writing to a disposable derived DuckDB store, rebuilt by replaying the Phase 1 archive, wired live into the ingest path, exposed via a minimal trips API.

**Architecture:** The engine is a pure-with-respect-to-time state machine: every decision uses only point timestamps (never the wall clock), so live processing and archive replay are literally the same computation — the spec's "one code path" guarantee. At app startup a full rebuild replays the archive through a fresh engine; the live ingest path then continues feeding the *same* engine instance, so state flows seamlessly from history into the present. The derived store is fully disposable (`rebuild` truncates and replays).

**Tech Stack:** Python 3.11, dataclasses, DuckDB (derived store), existing Phase 1 components (RawStore, Archiver, EventQuery), FastAPI.

**Spec:** `docs/superpowers/specs/2026-06-10-ground-up-rewrite-design.md` — "Trip engine" section (hygiene/enrichment/trip detection/segmentation+mode), the derived-store half of "Storage", and the `GET /api/trips` slice of "API surface". Train matching and the ML mode-override are Phase 3+; the heuristic baseline here classifies stationary/walk/vehicle (vehicle is refined into drive/train by Phase 3's matcher).

---

## File structure

```
backend/
  engine/
    __init__.py
    params.py      # EngineParams — every threshold, frozen dataclass
    geo.py         # haversine_m, bearing_deg (pure math)
    types.py       # Point, EnrichedPoint, Trip, Segment, TripClosed, PointRejected
    hygiene.py     # accept/reject: accuracy, out-of-order, teleport
    geofence.py    # Geofence + hysteresis resolution + settings adapter
    enrich.py      # per-point deltas: speed, heading, distance
    segmenter.py   # on trip close: mode heuristic, smoothing, split, merge-short
    machine.py     # TripEngine + EngineState — the state machine
    rebuild.py     # truncate derived + replay archive through a fresh engine; CLI
    runner.py      # EngineRunner — owns live engine + store, ingest hook
  storage/
    derived.py     # DerivedStore — DuckDB file: trips/segments/trip_points/rejected
  api/
    __init__.py
    trips.py       # GET /api/trips, GET /api/trips/{trip_id}
  tests/
    synth.py       # deterministic synthetic GPS track builders (test helper)
    test_engine_params.py
    test_geo.py
    test_engine_types.py
    test_hygiene.py
    test_geofence.py
    test_enrich.py
    test_machine_start.py
    test_machine_close.py
    test_segmenter.py
    test_derived_store.py
    test_rebuild.py
    test_runner_live.py
    test_replay_equivalence.py
    test_trips_api.py
backend/config.py        # modify: home/work geofence env vars
backend/app.py           # modify: runner in lifespan, trips router
backend/ingest/routes.py # modify: feed engine after raw append
backend/tests/conftest.py# modify if needed (settings fixture gains nothing — new fields default)
README.md                # modify: trips API + rebuild CLI
```

**Key design decisions locked in here:**

- **Units:** speeds in m/s, distances in meters, timestamps as epoch-seconds floats (from OwnTracks `tst`). API output converts timestamps to ISO-8601 UTC strings.
- **Determinism:** `trip_id = f"t{int(trip_start_ts)}"` — derived from data, stable across replays.
- **Mode heuristic (Phase 2):** stationary (< 0.5 m/s), walk (< 2.5 m/s), vehicle (rest). Phase 3 refines vehicle → train/drive via GTFS matching. Legacy app used walk-max 7 km/h ≈ 1.94 m/s; we use 2.5 m/s to be conservative about calling things "vehicle".
- **Startup = full rebuild.** At current scale (months of data, 1 pt/30s) a full replay takes seconds. The lifespan runs it via `asyncio.to_thread` before serving. When data grows enough to hurt, optimize then (the engine state is serializable — `EngineState.to_dict`/`from_dict` exist for the property test and future incremental resume).
- **Phantom-trip suppression:** trips shorter than 120 s or 300 m are silently dropped (params). Surfacing them for labeling is a Phase 4 concern.
- **Rejected points are recorded** in the derived store with a reason — never dropped from raw (spec requirement).

---

### Task 1: Engine parameters + geofence settings

**Files:**
- Create: `backend/engine/__init__.py` (empty)
- Create: `backend/engine/params.py`
- Modify: `backend/config.py`
- Test: `backend/tests/test_engine_params.py`
- Modify: `backend/tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_engine_params.py
from backend.engine.params import EngineParams


def test_defaults_are_sane():
    p = EngineParams()
    assert p.accuracy_max_m == 100.0
    assert p.teleport_speed_mps == 90.0
    assert p.move_speed_mps == 1.4
    assert p.move_window == 5
    assert p.move_min_points == 3
    assert p.move_min_displacement_m == 80.0
    assert p.dwell_radius_m == 75.0
    assert p.dwell_close_s == 300.0
    assert p.gap_close_s == 1800.0
    assert p.stationary_max_mps == 0.5
    assert p.walk_max_mps == 2.5
    assert p.min_segment_s == 30.0
    assert p.min_trip_duration_s == 120.0
    assert p.min_trip_distance_m == 300.0
```

Add to `backend/tests/test_config.py` (new test; also extend the delenv loop in
`test_defaults` with the six new vars, and add the six fields to the expected
`Settings` in `test_env_overrides`):

```python
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
```

In `test_env_overrides`, the expected `Settings(...)` constructor call must now
also pass the six geofence kwargs (matching the six monkeypatched values you
add there), or — simpler — keep that test as-is and rely on defaults: do NOT
set the new env vars in `test_env_overrides` and do not pass them to the
expected `Settings` (defaults on both sides match). Extend only the
`test_defaults` delenv loop:

```python
    for var in (
        "CT_DATA_DIR", "CT_S3_BUCKET", "CT_S3_PREFIX", "CT_S3_REGION",
        "CT_PASSTHROUGH_URL", "CT_ARCHIVE_HOUR_UTC",
        "CT_HOME_LAT", "CT_HOME_LON", "CT_HOME_RADIUS_M",
        "CT_WORK_LAT", "CT_WORK_LON", "CT_WORK_RADIUS_M",
    ):
        monkeypatch.delenv(var, raising=False)
```

and assert in `test_defaults`: `assert s.home_lat == 0.0` and
`assert s.work_radius_m == 150.0`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_engine_params.py backend/tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.engine.params` and `AttributeError` on the new Settings fields.

- [ ] **Step 3: Implement**

```python
# backend/engine/params.py
"""Every engine threshold in one frozen dataclass. Units: m, s, m/s."""

from dataclasses import dataclass


@dataclass(frozen=True)
class EngineParams:
    accuracy_max_m: float = 100.0          # reject points with worse GPS accuracy
    teleport_speed_mps: float = 90.0       # implied speed above this = GPS glitch
    move_speed_mps: float = 1.4            # "moving" threshold (slow walk)
    move_window: int = 5                   # recent-point window for start detection
    move_min_points: int = 3               # M of N points must exceed move_speed
    move_min_displacement_m: float = 80.0  # window must also net-displace this far
    dwell_radius_m: float = 75.0           # stationary cluster radius
    dwell_close_s: float = 300.0           # dwell this long closes the trip
    gap_close_s: float = 1800.0            # data gap this long closes the trip
    stationary_max_mps: float = 0.5        # mode: below = stationary
    walk_max_mps: float = 2.5              # mode: below = walk, above = vehicle
    min_segment_s: float = 30.0            # segments shorter than this merge away
    min_trip_duration_s: float = 120.0     # shorter trips are phantom — dropped
    min_trip_distance_m: float = 300.0     # ditto
```

In `backend/config.py`, append six fields to `Settings` (after
`archive_hour_utc`, all with defaults so existing constructions keep working):

```python
    home_lat: float = 0.0
    home_lon: float = 0.0
    home_radius_m: float = 50.0
    work_lat: float = 0.0
    work_lon: float = 0.0
    work_radius_m: float = 150.0
```

and in `load_settings()` add to the constructor call:

```python
        home_lat=float(os.environ.get("CT_HOME_LAT", "0.0")),
        home_lon=float(os.environ.get("CT_HOME_LON", "0.0")),
        home_radius_m=float(os.environ.get("CT_HOME_RADIUS_M", "50")),
        work_lat=float(os.environ.get("CT_WORK_LAT", "0.0")),
        work_lon=float(os.environ.get("CT_WORK_LON", "0.0")),
        work_radius_m=float(os.environ.get("CT_WORK_RADIUS_M", "150")),
```

Create empty `backend/engine/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_engine_params.py backend/tests/test_config.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full backend suite (Settings change ripples)**

Run: `pytest backend/tests -q`
Expected: all PASS (new fields have defaults; existing fixtures unaffected).

- [ ] **Step 6: Commit**

```bash
git add backend/engine/ backend/config.py backend/tests/test_engine_params.py backend/tests/test_config.py
git commit -m "feat: engine params and home/work geofence settings"
```

---

### Task 2: Geo math

**Files:**
- Create: `backend/engine/geo.py`
- Test: `backend/tests/test_geo.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_geo.py
from backend.engine.geo import bearing_deg, haversine_m


def test_haversine_zero_distance():
    assert haversine_m(40.7, -74.4, 40.7, -74.4) == 0.0


def test_haversine_known_distance():
    # 0.01 deg latitude ≈ 1111.9 m
    d = haversine_m(40.70, -74.40, 40.71, -74.40)
    assert 1100 < d < 1125


def test_bearing_north_and_east():
    assert abs(bearing_deg(40.70, -74.40, 40.71, -74.40) - 0.0) < 1.0    # due north
    assert abs(bearing_deg(40.70, -74.40, 40.70, -74.39) - 90.0) < 1.0   # due east


def test_bearing_range():
    b = bearing_deg(40.71, -74.40, 40.70, -74.40)  # due south
    assert 179.0 < b < 181.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_geo.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.engine.geo`.

- [ ] **Step 3: Implement**

```python
# backend/engine/geo.py
"""Pure spherical geometry. WGS-84 mean earth radius."""

import math

_EARTH_RADIUS_M = 6371008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_geo.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/engine/geo.py backend/tests/test_geo.py
git commit -m "feat: haversine and bearing geo math for trip engine"
```

---

### Task 3: Engine types

**Files:**
- Create: `backend/engine/types.py`
- Test: `backend/tests/test_engine_types.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_engine_types.py
from backend.engine.types import EnrichedPoint, Point


def test_point_from_owntracks_location():
    p = Point.from_owntracks(
        {"_type": "location", "tst": 1781100000, "lat": 40.7, "lon": -74.4, "acc": 10}
    )
    assert p == Point(ts=1781100000.0, lat=40.7, lon=-74.4, accuracy_m=10.0)


def test_point_from_owntracks_missing_acc():
    p = Point.from_owntracks({"_type": "location", "tst": 1, "lat": 1.0, "lon": 2.0})
    assert p.accuracy_m is None


def test_point_from_owntracks_non_location_returns_none():
    assert Point.from_owntracks({"_type": "transition", "tst": 1}) is None
    assert Point.from_owntracks({"_type": "location", "lat": 1.0}) is None  # missing fields
    assert Point.from_owntracks({"_type": "location", "tst": "x", "lat": 1, "lon": 2}) is None


def test_enriched_point_roundtrips_dict():
    ep = EnrichedPoint(
        ts=1.0, lat=40.7, lon=-74.4, accuracy_m=5.0,
        speed_mps=1.2, heading_deg=90.0, distance_m=36.0, geofence="home",
    )
    assert EnrichedPoint(**ep.to_dict()) == ep
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_engine_types.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.engine.types`.

- [ ] **Step 3: Implement**

```python
# backend/engine/types.py
"""Engine data types. Timestamps are epoch seconds (UTC) from OwnTracks tst."""

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Point:
    ts: float
    lat: float
    lon: float
    accuracy_m: float | None = None

    @classmethod
    def from_owntracks(cls, payload: dict) -> "Point | None":
        """Parse an OwnTracks payload; None for non-location or malformed."""
        if not isinstance(payload, dict) or payload.get("_type") != "location":
            return None
        try:
            acc = payload.get("acc")
            return cls(
                ts=float(payload["tst"]),
                lat=float(payload["lat"]),
                lon=float(payload["lon"]),
                accuracy_m=float(acc) if acc is not None else None,
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class EnrichedPoint:
    ts: float
    lat: float
    lon: float
    accuracy_m: float | None
    speed_mps: float
    heading_deg: float | None
    distance_m: float        # from previous accepted point (0.0 for the first)
    geofence: str | None     # "home" | "work" | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Segment:
    trip_id: str
    seg_index: int
    mode: str                # "stationary" | "walk" | "vehicle"
    start_ts: float
    end_ts: float
    duration_s: float
    distance_m: float
    point_count: int


@dataclass(frozen=True)
class Trip:
    trip_id: str
    start_ts: float
    end_ts: float
    duration_s: float
    distance_m: float
    point_count: int
    start_geofence: str | None
    end_geofence: str | None
    direction: str           # "outbound" | "inbound" | "other"


@dataclass(frozen=True)
class TripClosed:
    trip: Trip
    segments: list[Segment] = field(default_factory=list)
    points: list[EnrichedPoint] = field(default_factory=list)


@dataclass(frozen=True)
class PointRejected:
    point: Point
    reason: str              # "accuracy" | "out_of_order" | "teleport"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_engine_types.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/engine/types.py backend/tests/test_engine_types.py
git commit -m "feat: engine data types with owntracks point parsing"
```

---

### Task 4: Hygiene stage

**Files:**
- Create: `backend/engine/hygiene.py`
- Test: `backend/tests/test_hygiene.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_hygiene.py
from backend.engine.hygiene import check
from backend.engine.params import EngineParams
from backend.engine.types import EnrichedPoint, Point

P = EngineParams()


def _prev(ts=1000.0, lat=40.70, lon=-74.40):
    return EnrichedPoint(
        ts=ts, lat=lat, lon=lon, accuracy_m=5.0,
        speed_mps=0.0, heading_deg=None, distance_m=0.0, geofence=None,
    )


def test_accepts_clean_point():
    assert check(_prev(), Point(ts=1030.0, lat=40.7001, lon=-74.40, accuracy_m=8.0), P) is None


def test_accepts_first_point_without_prev():
    assert check(None, Point(ts=1.0, lat=40.7, lon=-74.4, accuracy_m=8.0), P) is None


def test_rejects_bad_accuracy():
    pt = Point(ts=1030.0, lat=40.7, lon=-74.4, accuracy_m=150.0)
    assert check(_prev(), pt, P) == "accuracy"


def test_accepts_missing_accuracy():
    assert check(_prev(), Point(ts=1030.0, lat=40.7, lon=-74.4, accuracy_m=None), P) is None


def test_rejects_out_of_order_and_duplicate_ts():
    assert check(_prev(ts=1000.0), Point(ts=999.0, lat=40.7, lon=-74.4), P) == "out_of_order"
    assert check(_prev(ts=1000.0), Point(ts=1000.0, lat=40.7, lon=-74.4), P) == "out_of_order"


def test_rejects_teleport():
    # ~11 km in 30 s ≈ 370 m/s
    pt = Point(ts=1030.0, lat=40.80, lon=-74.40, accuracy_m=5.0)
    assert check(_prev(ts=1000.0, lat=40.70), pt, P) == "teleport"


def test_long_gap_is_not_teleport():
    # same 11 km but over 2 hours — slow, fine
    pt = Point(ts=8200.0, lat=40.80, lon=-74.40, accuracy_m=5.0)
    assert check(_prev(ts=1000.0, lat=40.70), pt, P) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_hygiene.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.engine.hygiene`.

- [ ] **Step 3: Implement**

```python
# backend/engine/hygiene.py
"""Point acceptance checks. Rejection never touches raw data — the caller
records the rejection in derived data and moves on."""

from backend.engine.geo import haversine_m
from backend.engine.params import EngineParams
from backend.engine.types import EnrichedPoint, Point


def check(prev: EnrichedPoint | None, point: Point, params: EngineParams) -> str | None:
    """Return a rejection reason, or None if the point is acceptable."""
    if point.accuracy_m is not None and point.accuracy_m > params.accuracy_max_m:
        return "accuracy"
    if prev is not None:
        if point.ts <= prev.ts:
            return "out_of_order"
        dt = point.ts - prev.ts
        if haversine_m(prev.lat, prev.lon, point.lat, point.lon) / dt > params.teleport_speed_mps:
            return "teleport"
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_hygiene.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/engine/hygiene.py backend/tests/test_hygiene.py
git commit -m "feat: point hygiene checks for accuracy, ordering, teleports"
```

---

### Task 5: Geofences with hysteresis

**Files:**
- Create: `backend/engine/geofence.py`
- Test: `backend/tests/test_geofence.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_geofence.py
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
    assert resolve_geofence(GFS, lat_60m_north, -74.4000, None) is None       # no entry
    assert resolve_geofence(GFS, lat_60m_north, -74.4000, "home") == "home"   # no exit


def test_exit_beyond_band():
    lat_100m_north = 40.7000 + 100 / 111120
    assert resolve_geofence(GFS, lat_100m_north, -74.4000, "home") is None


def test_geofences_from_settings_skips_unset():
    s = Settings(
        data_dir=None, s3_bucket=None, s3_prefix="x", s3_region=None,
        passthrough_url=None, archive_hour_utc=6,
        home_lat=40.7, home_lon=-74.4, home_radius_m=50.0,
        # work left at 0,0 default → omitted
    )
    gfs = geofences_from_settings(s)
    assert [g.name for g in gfs] == ["home"]
```

(Note: `Settings.data_dir` is typed `Path` but `None` is fine for this
construction-only test — the dataclass doesn't validate types.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_geofence.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.engine.geofence`.

- [ ] **Step 3: Implement**

```python
# backend/engine/geofence.py
"""Geofence membership with hysteresis: enter at radius, exit at 1.5x radius.

Hysteresis prevents boundary flapping when GPS jitter straddles the fence."""

from dataclasses import dataclass

from backend.config import Settings
from backend.engine.geo import haversine_m

_EXIT_FACTOR = 1.5


@dataclass(frozen=True)
class Geofence:
    name: str
    lat: float
    lon: float
    radius_m: float


def resolve_geofence(
    geofences: list[Geofence], lat: float, lon: float, current: str | None
) -> str | None:
    """Resolve membership for a position given the previous membership."""
    if current is not None:
        cf = next((g for g in geofences if g.name == current), None)
        if cf is not None:
            if haversine_m(lat, lon, cf.lat, cf.lon) <= cf.radius_m * _EXIT_FACTOR:
                return current
    for g in geofences:
        if haversine_m(lat, lon, g.lat, g.lon) <= g.radius_m:
            return g.name
    return None


def geofences_from_settings(settings: Settings) -> list[Geofence]:
    """Build home/work fences from settings; (0,0) coordinates mean unset."""
    out = []
    if (settings.home_lat, settings.home_lon) != (0.0, 0.0):
        out.append(
            Geofence("home", settings.home_lat, settings.home_lon, settings.home_radius_m)
        )
    if (settings.work_lat, settings.work_lon) != (0.0, 0.0):
        out.append(
            Geofence("work", settings.work_lat, settings.work_lon, settings.work_radius_m)
        )
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_geofence.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/engine/geofence.py backend/tests/test_geofence.py
git commit -m "feat: geofence membership with exit hysteresis"
```

---

### Task 6: Enrichment

**Files:**
- Create: `backend/engine/enrich.py`
- Test: `backend/tests/test_enrich.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_enrich.py
from backend.engine.enrich import enrich
from backend.engine.types import EnrichedPoint, Point


def test_first_point_has_zero_motion():
    ep = enrich(None, Point(ts=100.0, lat=40.7, lon=-74.4, accuracy_m=5.0), "home")
    assert ep.speed_mps == 0.0
    assert ep.distance_m == 0.0
    assert ep.heading_deg is None
    assert ep.geofence == "home"


def test_deltas_from_previous_point():
    prev = EnrichedPoint(
        ts=100.0, lat=40.7000, lon=-74.4000, accuracy_m=5.0,
        speed_mps=0.0, heading_deg=None, distance_m=0.0, geofence=None,
    )
    # ~111 m due north over 30 s → ~3.7 m/s heading ~0°
    ep = enrich(prev, Point(ts=130.0, lat=40.7010, lon=-74.4000, accuracy_m=5.0), None)
    assert 100 < ep.distance_m < 120
    assert 3.3 < ep.speed_mps < 4.0
    assert ep.heading_deg is not None and (ep.heading_deg < 1.0 or ep.heading_deg > 359.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_enrich.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.engine.enrich`.

- [ ] **Step 3: Implement**

```python
# backend/engine/enrich.py
"""Per-point kinematics from the previous accepted point."""

from backend.engine.geo import bearing_deg, haversine_m
from backend.engine.types import EnrichedPoint, Point


def enrich(prev: EnrichedPoint | None, point: Point, geofence: str | None) -> EnrichedPoint:
    if prev is None:
        return EnrichedPoint(
            ts=point.ts, lat=point.lat, lon=point.lon, accuracy_m=point.accuracy_m,
            speed_mps=0.0, heading_deg=None, distance_m=0.0, geofence=geofence,
        )
    distance = haversine_m(prev.lat, prev.lon, point.lat, point.lon)
    dt = point.ts - prev.ts  # hygiene guarantees dt > 0
    return EnrichedPoint(
        ts=point.ts, lat=point.lat, lon=point.lon, accuracy_m=point.accuracy_m,
        speed_mps=distance / dt,
        heading_deg=bearing_deg(prev.lat, prev.lon, point.lat, point.lon),
        distance_m=distance, geofence=geofence,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_enrich.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/engine/enrich.py backend/tests/test_enrich.py
git commit -m "feat: per-point speed, heading, distance enrichment"
```

---

### Task 7: Synthetic track builder (test helper)

**Files:**
- Create: `backend/tests/synth.py`
- Test: (helper — exercised by every machine/segmenter test; smoke asserts inline)

- [ ] **Step 1: Implement the helper (no TDD — it's test infrastructure, but it carries inline assertions in later tasks)**

```python
# backend/tests/synth.py
"""Deterministic synthetic GPS tracks for engine tests.

All tracks move due north from a start coordinate; 1 degree latitude is
~111,120 m so dlat = meters / 111120. No randomness — replay-stable."""

from backend.engine.types import Point

M_PER_DEG_LAT = 111120.0


def leg(
    t0: float, lat0: float, lon: float, speed_mps: float, duration_s: float,
    interval_s: float = 30.0, accuracy_m: float = 10.0,
) -> list[Point]:
    """Points moving due north at constant speed. Includes t0, excludes t0+duration."""
    pts = []
    t = t0
    lat = lat0
    while t < t0 + duration_s:
        pts.append(Point(ts=t, lat=lat, lon=lon, accuracy_m=accuracy_m))
        t += interval_s
        lat += speed_mps * interval_s / M_PER_DEG_LAT
    return pts


def dwell(
    t0: float, lat: float, lon: float, duration_s: float,
    interval_s: float = 30.0, accuracy_m: float = 10.0,
) -> list[Point]:
    """Stationary points at one location."""
    pts = []
    t = t0
    while t < t0 + duration_s:
        pts.append(Point(ts=t, lat=lat, lon=lon, accuracy_m=accuracy_m))
        t += interval_s
    return pts


def end_of(points: list[Point]) -> tuple[float, float]:
    """(next_ts, final_lat) to chain legs."""
    last = points[-1]
    return last.ts + 30.0, last.lat


def commute(t0: float = 1_781_100_000.0, lat0: float = 40.7000, lon: float = -74.4000):
    """walk 5 min → vehicle 15 min @20 m/s → walk 5 min → dwell 10 min.

    Returns (points, lat0, final_moving_lat). Total moving distance ≈ 19 km.
    """
    pts = leg(t0, lat0, lon, speed_mps=1.5, duration_s=300)
    t, lat = end_of(pts)
    pts += leg(t, lat, lon, speed_mps=20.0, duration_s=900)
    t, lat = end_of(pts)
    pts += leg(t, lat, lon, speed_mps=1.5, duration_s=300)
    t, lat = end_of(pts)
    pts += dwell(t, lat, lon, duration_s=600)
    return pts, lat0, lat
```

- [ ] **Step 2: Sanity-check it imports and is ruff-clean**

Run: `python -c "from backend.tests.synth import commute; pts, a, b = commute(); print(len(pts), a, round(b, 4))"`
Expected: ~67 points, 40.7, ~40.87 (≈19 km north).

Run: `ruff format backend/ && ruff check backend/`

- [ ] **Step 3: Commit**

```bash
git add backend/tests/synth.py
git commit -m "test: deterministic synthetic gps track builders"
```

---

### Task 8: State machine — trip start detection

**Files:**
- Create: `backend/engine/machine.py` (start-detection half)
- Test: `backend/tests/test_machine_start.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_machine_start.py
from backend.engine.machine import EngineState, TripEngine
from backend.engine.params import EngineParams
from backend.engine.types import PointRejected
from backend.tests.synth import dwell, leg


def _engine():
    return TripEngine(EngineParams(), geofences=[])


def test_stationary_points_never_start_a_trip():
    eng = _engine()
    for pt in dwell(1000.0, 40.7, -74.4, duration_s=1200):
        assert eng.process(pt) == []
    assert eng.state.status == "idle"


def test_sustained_movement_starts_a_trip():
    eng = _engine()
    for pt in leg(1000.0, 40.7, -74.4, speed_mps=1.5, duration_s=300):
        eng.process(pt)
    assert eng.state.status == "moving"
    assert len(eng.state.trip_points) >= 3


def test_brief_jitter_does_not_start_a_trip():
    eng = _engine()
    pts = dwell(1000.0, 40.7, -74.4, duration_s=120)
    # a 60 s fast blip (2 points, second displaced ~60 m → real computed speed)
    # in the middle of stillness: at most 1 fast point in any 5-window — below
    # move_min_points=3
    pts += leg(1150.0, 40.7, -74.4, speed_mps=2.0, duration_s=60)
    pts += dwell(1240.0, pts[-1].lat, -74.4, duration_s=150)
    for pt in pts:
        eng.process(pt)
    assert eng.state.status == "idle"


def test_rejected_points_are_reported_not_processed():
    eng = _engine()
    pts = leg(1000.0, 40.7, -74.4, speed_mps=1.5, duration_s=120)
    events = []
    for pt in pts:
        events.extend(eng.process(pt))
    bad = pts[-1].__class__(ts=pts[-1].ts + 30, lat=41.5, lon=-74.4, accuracy_m=5.0)  # teleport
    ev = eng.process(bad)
    assert len(ev) == 1
    assert isinstance(ev[0], PointRejected)
    assert ev[0].reason == "teleport"


def test_state_roundtrips_dict():
    eng = _engine()
    for pt in leg(1000.0, 40.7, -74.4, speed_mps=1.5, duration_s=300):
        eng.process(pt)
    restored = EngineState.from_dict(eng.state.to_dict())
    assert restored == eng.state
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_machine_start.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.engine.machine`.

- [ ] **Step 3: Implement (start detection; close logic arrives in Task 9 — `_maybe_close` returns [] for now)**

```python
# backend/engine/machine.py
"""Trip state machine: IDLE → MOVING → (dwell | gap) → closed trip.

Pure with respect to time: every decision uses point timestamps only, never
the wall clock — live processing and archive replay are the same computation.
"""

from dataclasses import asdict, dataclass, field

from backend.engine.enrich import enrich
from backend.engine.geo import haversine_m
from backend.engine.geofence import Geofence, resolve_geofence
from backend.engine.hygiene import check
from backend.engine.params import EngineParams
from backend.engine.types import EnrichedPoint, Point, PointRejected, TripClosed


@dataclass
class EngineState:
    status: str = "idle"                       # "idle" | "moving"
    prev: EnrichedPoint | None = None          # last accepted point
    geofence: str | None = None                # current fence membership
    recent: list[EnrichedPoint] = field(default_factory=list)       # idle window
    trip_points: list[EnrichedPoint] = field(default_factory=list)  # active trip

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "prev": asdict(self.prev) if self.prev else None,
            "geofence": self.geofence,
            "recent": [asdict(p) for p in self.recent],
            "trip_points": [asdict(p) for p in self.trip_points],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EngineState":
        return cls(
            status=d["status"],
            prev=EnrichedPoint(**d["prev"]) if d["prev"] else None,
            geofence=d["geofence"],
            recent=[EnrichedPoint(**p) for p in d["recent"]],
            trip_points=[EnrichedPoint(**p) for p in d["trip_points"]],
        )


class TripEngine:
    def __init__(self, params: EngineParams, geofences: list[Geofence]):
        self.params = params
        self.geofences = geofences
        self.state = EngineState()

    def process(self, point: Point) -> list[TripClosed | PointRejected]:
        s, p = self.state, self.params
        reason = check(s.prev, point, p)
        if reason is not None:
            return [PointRejected(point=point, reason=reason)]

        events: list[TripClosed | PointRejected] = []
        if s.status == "moving" and point.ts - s.prev.ts > p.gap_close_s:
            events.extend(self._close_trip(end_index=len(s.trip_points)))

        gf = resolve_geofence(self.geofences, point.lat, point.lon, s.geofence)
        ep = enrich(s.prev, point, gf)

        if s.status == "idle":
            s.recent.append(ep)
            if len(s.recent) > p.move_window:
                s.recent.pop(0)
            if self._movement_detected():
                s.status = "moving"
                s.trip_points = list(s.recent)
                s.recent = []
        else:
            s.trip_points.append(ep)
            events.extend(self._maybe_close_on_dwell())

        s.prev = ep
        s.geofence = gf
        return events

    def _movement_detected(self) -> bool:
        s, p = self.state, self.params
        if len(s.recent) < p.move_window:
            return False
        fast = sum(1 for ep in s.recent if ep.speed_mps >= p.move_speed_mps)
        if fast < p.move_min_points:
            return False
        first, last = s.recent[0], s.recent[-1]
        return haversine_m(first.lat, first.lon, last.lat, last.lon) >= (
            p.move_min_displacement_m
        )

    def _maybe_close_on_dwell(self) -> list[TripClosed]:
        return []  # implemented in the trip-close task

    def _close_trip(self, end_index: int) -> list[TripClosed]:
        self.state.status = "idle"
        self.state.recent = []
        self.state.trip_points = []
        return []  # full assembly implemented in the trip-close task
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_machine_start.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/engine/machine.py backend/tests/test_machine_start.py
git commit -m "feat: trip start detection state machine"
```

---

### Task 9: State machine — dwell/gap close + trip assembly

**Files:**
- Modify: `backend/engine/machine.py`
- Test: `backend/tests/test_machine_close.py`

Depends on the segmenter for `TripClosed.segments` — to keep tasks independent,
this task introduces `segment_trip` as part of the next task's module but
*calls* it; implement Task 10 (segmenter) FIRST if executing out of order.
In-order execution: this task stubs nothing — write the segmenter call as
specified and let Task 10's module already exist? **No** — to keep strict
in-order TDD, this task creates a minimal `backend/engine/segmenter.py`
containing only the function signature returning a single whole-trip segment;
Task 10 replaces it with the real heuristic and its own tests. This keeps both
tasks green at every commit.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_machine_close.py
from backend.engine.geofence import Geofence
from backend.engine.machine import TripEngine
from backend.engine.params import EngineParams
from backend.engine.types import TripClosed
from backend.tests.synth import commute, dwell, end_of, leg

HOME = Geofence(name="home", lat=40.7000, lon=-74.4000, radius_m=50.0)


def _drive(events):
    return [e for e in events if isinstance(e, TripClosed)]


def _run(eng, pts):
    out = []
    for pt in pts:
        out.extend(eng.process(pt))
    return out


def test_dwell_closes_trip():
    pts, lat0, lat_end = commute()
    work = Geofence(name="work", lat=lat_end, lon=-74.4000, radius_m=150.0)
    eng = TripEngine(EngineParams(), geofences=[HOME, work])
    closed = _drive(_run(eng, pts))
    assert len(closed) == 1
    trip = closed[0].trip
    assert trip.trip_id == f"t{int(closed[0].points[0].ts)}"
    assert trip.direction == "outbound"
    assert trip.start_geofence == "home"
    assert trip.end_geofence == "work"
    assert trip.distance_m > 15000
    assert eng.state.status == "idle"


def test_gap_closes_trip_at_last_point():
    pts = leg(1000.0, 40.7000, -74.4000, speed_mps=20.0, duration_s=900)
    eng = TripEngine(EngineParams(), geofences=[])
    _run(eng, pts)
    assert eng.state.status == "moving"
    # next point 2 hours later
    t, lat = end_of(pts)
    far_later = leg(t + 7200, lat, -74.4000, speed_mps=0.0, duration_s=30)
    closed = _drive(_run(eng, far_later))
    assert len(closed) == 1
    assert closed[0].trip.end_ts == pts[-1].ts


def test_phantom_short_trip_is_dropped():
    # 90 s of movement (< min_trip_duration_s) then long dwell
    pts = leg(1000.0, 40.7000, -74.4000, speed_mps=1.5, duration_s=90)
    t, lat = end_of(pts)
    pts += dwell(t, lat, -74.4000, duration_s=600)
    eng = TripEngine(EngineParams(), geofences=[])
    closed = _drive(_run(eng, pts))
    assert closed == []
    assert eng.state.status == "idle"


def test_two_trips_in_one_day():
    pts1, lat0, lat_end = commute(t0=1_781_100_000.0)
    # return trip later from where the first ended
    pts2, _, _ = commute(t0=1_781_130_000.0, lat0=lat_end)
    eng = TripEngine(EngineParams(), geofences=[])
    closed = _drive(_run(eng, pts1 + pts2))
    assert len(closed) == 2
    assert closed[0].trip.end_ts < closed[1].trip.start_ts
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_machine_close.py -v`
Expected: FAIL — trips never close (`closed == []` where 1 expected).

- [ ] **Step 3: Create the minimal segmenter (replaced by Task 10)**

```python
# backend/engine/segmenter.py
"""Trip segmentation by mode heuristic. (Minimal version — Task 10 adds the
real per-point mode classification, smoothing, and short-segment merging.)"""

from backend.engine.params import EngineParams
from backend.engine.types import EnrichedPoint, Segment


def segment_trip(
    trip_id: str, points: list[EnrichedPoint], params: EngineParams
) -> list[Segment]:
    return [
        Segment(
            trip_id=trip_id,
            seg_index=0,
            mode="vehicle",
            start_ts=points[0].ts,
            end_ts=points[-1].ts,
            duration_s=points[-1].ts - points[0].ts,
            distance_m=sum(p.distance_m for p in points[1:]),
            point_count=len(points),
        )
    ]
```

- [ ] **Step 4: Implement close logic in `backend/engine/machine.py`**

Add imports:

```python
from backend.engine.segmenter import segment_trip
from backend.engine.types import Segment, Trip  # extend the existing types import
```

Replace `_maybe_close_on_dwell` and `_close_trip`:

```python
    def _maybe_close_on_dwell(self) -> list[TripClosed]:
        s, p = self.state, self.params
        pts = s.trip_points
        last = pts[-1]
        # find the latest point at or before the start of the dwell window
        anchor_idx = None
        for i in range(len(pts) - 1, -1, -1):
            if last.ts - pts[i].ts >= p.dwell_close_s:
                anchor_idx = i
                break
        if anchor_idx is None:
            return []
        anchor = pts[anchor_idx]
        for pt in pts[anchor_idx:]:
            if haversine_m(anchor.lat, anchor.lon, pt.lat, pt.lon) > p.dwell_radius_m:
                return []
        return self._close_trip(end_index=anchor_idx + 1)

    def _close_trip(self, end_index: int) -> list[TripClosed]:
        kept = self.state.trip_points[:end_index]
        self.state.status = "idle"
        self.state.recent = []
        self.state.trip_points = []
        if len(kept) < 2:
            return []
        duration = kept[-1].ts - kept[0].ts
        distance = sum(pt.distance_m for pt in kept[1:])
        p = self.params
        if duration < p.min_trip_duration_s or distance < p.min_trip_distance_m:
            return []
        trip_id = f"t{int(kept[0].ts)}"
        start_gf, end_gf = kept[0].geofence, kept[-1].geofence
        if start_gf == "home" and end_gf == "work":
            direction = "outbound"
        elif start_gf == "work" and end_gf == "home":
            direction = "inbound"
        else:
            direction = "other"
        trip = Trip(
            trip_id=trip_id,
            start_ts=kept[0].ts,
            end_ts=kept[-1].ts,
            duration_s=duration,
            distance_m=distance,
            point_count=len(kept),
            start_geofence=start_gf,
            end_geofence=end_gf,
            direction=direction,
        )
        segments = segment_trip(trip_id, kept, p)
        return [TripClosed(trip=trip, segments=segments, points=kept)]
```

(`Segment` is imported for type completeness of the module's public surface;
ruff will flag it unused — drop it from the import if so.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest backend/tests/test_machine_close.py backend/tests/test_machine_start.py -v`
Expected: all PASS (start tests must not regress).

- [ ] **Step 6: Commit**

```bash
git add backend/engine/machine.py backend/engine/segmenter.py backend/tests/test_machine_close.py
git commit -m "feat: dwell and gap trip closing with trip assembly"
```

---

### Task 10: Real segmenter — mode heuristic, smoothing, merge-short

**Files:**
- Modify: `backend/engine/segmenter.py` (replace minimal version)
- Test: `backend/tests/test_segmenter.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_segmenter.py
from backend.engine.params import EngineParams
from backend.engine.segmenter import segment_trip
from backend.engine.types import EnrichedPoint

P = EngineParams()


def _pts(specs):
    """specs: list of (duration_s, speed_mps). 30 s cadence, due-north motion."""
    pts = []
    t, lat = 1000.0, 40.7000
    for duration, speed in specs:
        n = int(duration / 30)
        for _ in range(n):
            dist = speed * 30.0
            pts.append(
                EnrichedPoint(
                    ts=t, lat=lat, lon=-74.4, accuracy_m=10.0,
                    speed_mps=speed, heading_deg=0.0,
                    distance_m=dist if pts else 0.0, geofence=None,
                )
            )
            t += 30.0
            lat += dist / 111120.0
    return pts


def test_three_phase_commute_yields_three_segments():
    pts = _pts([(300, 1.5), (900, 20.0), (300, 1.5)])
    segs = segment_trip("t1", pts, P)
    assert [s.mode for s in segs] == ["walk", "vehicle", "walk"]
    assert [s.seg_index for s in segs] == [0, 1, 2]
    assert segs[0].trip_id == "t1"
    assert segs[1].distance_m > segs[0].distance_m


def test_single_blip_is_smoothed_away():
    # walking with one 30s "vehicle" blip in the middle
    pts = _pts([(150, 1.5), (30, 10.0), (150, 1.5)])
    segs = segment_trip("t1", pts, P)
    assert [s.mode for s in segs] == ["walk"]


def test_short_segment_merges_into_neighbor():
    # 30s walk sandwich between two long vehicle stretches — below min_segment_s
    # after smoothing survivors are merged
    pts = _pts([(600, 20.0), (30, 1.5), (600, 20.0)])
    segs = segment_trip("t1", pts, P)
    assert [s.mode for s in segs] == ["vehicle"]


def test_segments_tile_the_trip():
    pts = _pts([(300, 1.5), (900, 20.0), (300, 0.2)])
    segs = segment_trip("t1", pts, P)
    assert segs[0].start_ts == pts[0].ts
    assert segs[-1].end_ts == pts[-1].ts
    for a, b in zip(segs, segs[1:]):
        assert a.end_ts <= b.start_ts
    assert sum(s.point_count for s in segs) == len(pts)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_segmenter.py -v`
Expected: FAIL — minimal segmenter returns a single "vehicle" segment.

- [ ] **Step 3: Implement the real segmenter (full file replacement)**

```python
# backend/engine/segmenter.py
"""Trip segmentation: per-point mode heuristic → smoothing → split → merge-short.

Modes are a transparent baseline (stationary/walk/vehicle by speed). Phase 3
refines "vehicle" into train/drive via GTFS matching; manual labels (Phase 4)
override everything.
"""

from backend.engine.params import EngineParams
from backend.engine.types import EnrichedPoint, Segment


def _raw_mode(speed_mps: float, params: EngineParams) -> str:
    if speed_mps < params.stationary_max_mps:
        return "stationary"
    if speed_mps < params.walk_max_mps:
        return "walk"
    return "vehicle"


def _smooth(modes: list[str]) -> list[str]:
    """Majority vote over a 5-wide window; ties keep the point's own mode."""
    out = []
    for i in range(len(modes)):
        lo, hi = max(0, i - 2), min(len(modes), i + 3)
        window = modes[lo:hi]
        best = max(set(window), key=lambda m: (window.count(m), m == modes[i]))
        out.append(best)
    return out


def _split(modes: list[str]) -> list[tuple[int, int, str]]:
    """Contiguous (start, end_exclusive, mode) runs."""
    runs = []
    start = 0
    for i in range(1, len(modes) + 1):
        if i == len(modes) or modes[i] != modes[start]:
            runs.append((start, i, modes[start]))
            start = i
    return runs


def _merge_short(
    runs: list[tuple[int, int, str]], points: list[EnrichedPoint], params: EngineParams
) -> list[tuple[int, int, str]]:
    """Merge runs shorter than min_segment_s into the previous run (or the
    next, for a short leading run). Mode of the absorbing run wins."""
    merged: list[tuple[int, int, str]] = []
    for run in runs:
        start, end, mode = run
        duration = points[end - 1].ts - points[start].ts
        if duration < params.min_segment_s and merged:
            pstart, _, pmode = merged.pop()
            merged.append((pstart, end, pmode))
        else:
            merged.append(run)
    # a short leading run that survived: absorb into the following run
    if len(merged) >= 2:
        start, end, _ = merged[0]
        if points[end - 1].ts - points[start].ts < params.min_segment_s:
            _, nend, nmode = merged[1]
            merged = [(start, nend, nmode)] + merged[2:]
    return merged


def segment_trip(
    trip_id: str, points: list[EnrichedPoint], params: EngineParams
) -> list[Segment]:
    modes = _smooth([_raw_mode(p.speed_mps, params) for p in points])
    runs = _merge_short(_split(modes), points, params)
    segments = []
    for idx, (start, end, mode) in enumerate(runs):
        pts = points[start:end]
        segments.append(
            Segment(
                trip_id=trip_id,
                seg_index=idx,
                mode=mode,
                start_ts=pts[0].ts,
                end_ts=pts[-1].ts,
                duration_s=pts[-1].ts - pts[0].ts,
                distance_m=sum(p.distance_m for p in pts[1 if start == 0 else 0 :]),
                point_count=len(pts),
            )
        )
    return segments
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_segmenter.py backend/tests/test_machine_close.py -v`
Expected: all PASS. NOTE: `test_dwell_closes_trip` in test_machine_close still
passes because it asserts trip fields, not segment modes — verify nothing else
asserted "vehicle"-only behavior.

- [ ] **Step 5: Commit**

```bash
git add backend/engine/segmenter.py backend/tests/test_segmenter.py
git commit -m "feat: mode heuristic segmentation with smoothing and merging"
```

---

### Task 11: Derived store (DuckDB)

**Files:**
- Create: `backend/storage/derived.py`
- Test: `backend/tests/test_derived_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_derived_store.py
from backend.engine.types import EnrichedPoint, Point, PointRejected, Segment, Trip, TripClosed
from backend.storage.derived import DerivedStore


def _closed(trip_id="t1000", start=1000.0):
    pts = [
        EnrichedPoint(ts=start, lat=40.7, lon=-74.4, accuracy_m=5.0, speed_mps=0.0,
                      heading_deg=None, distance_m=0.0, geofence="home"),
        EnrichedPoint(ts=start + 30, lat=40.701, lon=-74.4, accuracy_m=5.0, speed_mps=3.7,
                      heading_deg=0.0, distance_m=111.0, geofence=None),
        EnrichedPoint(ts=start + 600, lat=40.75, lon=-74.4, accuracy_m=5.0, speed_mps=9.5,
                      heading_deg=0.0, distance_m=5400.0, geofence="work"),
    ]
    trip = Trip(trip_id=trip_id, start_ts=start, end_ts=start + 600, duration_s=600.0,
                distance_m=5511.0, point_count=3, start_geofence="home",
                end_geofence="work", direction="outbound")
    segs = [Segment(trip_id=trip_id, seg_index=0, mode="vehicle", start_ts=start,
                    end_ts=start + 600, duration_s=600.0, distance_m=5511.0, point_count=3)]
    return TripClosed(trip=trip, segments=segs, points=pts)


def test_write_and_list_trips(settings):
    store = DerivedStore(settings)
    store.write_trip_closed(_closed())
    trips = store.list_trips()
    assert len(trips) == 1
    t = trips[0]
    assert t["trip_id"] == "t1000"
    assert t["direction"] == "outbound"
    assert t["start_ts"] == "1970-01-01T00:16:40+00:00"  # epoch 1000 as ISO UTC
    assert t["distance_m"] == 5511.0


def test_get_trip_detail(settings):
    store = DerivedStore(settings)
    store.write_trip_closed(_closed())
    d = store.get_trip("t1000")
    assert d["trip"]["trip_id"] == "t1000"
    assert [s["mode"] for s in d["segments"]] == ["vehicle"]
    assert len(d["points"]) == 3
    assert d["points"][0]["geofence"] == "home"


def test_get_missing_trip_returns_none(settings):
    assert DerivedStore(settings).get_trip("nope") is None


def test_rewrite_same_trip_id_is_idempotent(settings):
    store = DerivedStore(settings)
    store.write_trip_closed(_closed())
    store.write_trip_closed(_closed())
    assert len(store.list_trips()) == 1
    assert len(store.get_trip("t1000")["points"]) == 3


def test_rejected_points_recorded(settings):
    store = DerivedStore(settings)
    store.write_rejected(
        PointRejected(point=Point(ts=5.0, lat=1.0, lon=2.0, accuracy_m=900.0),
                      reason="accuracy")
    )
    assert store.rejected_count() == 1


def test_truncate(settings):
    store = DerivedStore(settings)
    store.write_trip_closed(_closed())
    store.truncate()
    assert store.list_trips() == []


def test_list_trips_orders_newest_first(settings):
    store = DerivedStore(settings)
    store.write_trip_closed(_closed(trip_id="t1000", start=1000.0))
    store.write_trip_closed(_closed(trip_id="t9000", start=9000.0))
    assert [t["trip_id"] for t in store.list_trips()] == ["t9000", "t1000"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_derived_store.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.storage.derived`.

- [ ] **Step 3: Implement**

```python
# backend/storage/derived.py
"""Derived DuckDB store — trips, segments, points, rejections.

Fully disposable: rebuild truncates and replays the archive. Single-writer
(the app process). Timestamps stored as epoch DOUBLE; read methods convert
to ISO-8601 UTC strings for API consumers.
"""

from datetime import UTC, datetime
from pathlib import Path

import duckdb

from backend.config import Settings
from backend.engine.types import PointRejected, TripClosed

_DDL = """
CREATE TABLE IF NOT EXISTS trips (
    trip_id VARCHAR PRIMARY KEY, start_ts DOUBLE, end_ts DOUBLE,
    duration_s DOUBLE, distance_m DOUBLE, point_count INTEGER,
    start_geofence VARCHAR, end_geofence VARCHAR, direction VARCHAR
);
CREATE TABLE IF NOT EXISTS segments (
    trip_id VARCHAR, seg_index INTEGER, mode VARCHAR, start_ts DOUBLE,
    end_ts DOUBLE, duration_s DOUBLE, distance_m DOUBLE, point_count INTEGER
);
CREATE TABLE IF NOT EXISTS trip_points (
    trip_id VARCHAR, ts DOUBLE, lat DOUBLE, lon DOUBLE, accuracy_m DOUBLE,
    speed_mps DOUBLE, heading_deg DOUBLE, distance_m DOUBLE, geofence VARCHAR
);
CREATE TABLE IF NOT EXISTS rejected_points (
    ts DOUBLE, lat DOUBLE, lon DOUBLE, reason VARCHAR
);
"""


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


class DerivedStore:
    def __init__(self, settings: Settings, filename: str = "derived.duckdb"):
        path = Path(settings.data_dir) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(_DDL)

    def write_trip_closed(self, ev: TripClosed) -> None:
        t = ev.trip
        self._con.execute("BEGIN")
        try:
            for table in ("trips", "segments", "trip_points"):
                self._con.execute(
                    f"DELETE FROM {table} WHERE trip_id = ?", [t.trip_id]
                )
            self._con.execute(
                "INSERT INTO trips VALUES (?,?,?,?,?,?,?,?,?)",
                [t.trip_id, t.start_ts, t.end_ts, t.duration_s, t.distance_m,
                 t.point_count, t.start_geofence, t.end_geofence, t.direction],
            )
            self._con.executemany(
                "INSERT INTO segments VALUES (?,?,?,?,?,?,?,?)",
                [[s.trip_id, s.seg_index, s.mode, s.start_ts, s.end_ts,
                  s.duration_s, s.distance_m, s.point_count] for s in ev.segments],
            )
            self._con.executemany(
                "INSERT INTO trip_points VALUES (?,?,?,?,?,?,?,?,?)",
                [[t.trip_id, p.ts, p.lat, p.lon, p.accuracy_m, p.speed_mps,
                  p.heading_deg, p.distance_m, p.geofence] for p in ev.points],
            )
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise

    def write_rejected(self, ev: PointRejected) -> None:
        self._con.execute(
            "INSERT INTO rejected_points VALUES (?,?,?,?)",
            [ev.point.ts, ev.point.lat, ev.point.lon, ev.reason],
        )

    def rejected_count(self) -> int:
        return self._con.execute("SELECT count(*) FROM rejected_points").fetchone()[0]

    def truncate(self) -> None:
        for table in ("trips", "segments", "trip_points", "rejected_points"):
            self._con.execute(f"DELETE FROM {table}")

    def list_trips(self, limit: int = 50) -> list[dict]:
        rows = self._con.execute(
            "SELECT trip_id, start_ts, end_ts, duration_s, distance_m, point_count, "
            "start_geofence, end_geofence, direction "
            "FROM trips ORDER BY start_ts DESC LIMIT ?",
            [limit],
        ).fetchall()
        return [self._trip_row_to_dict(r) for r in rows]

    def get_trip(self, trip_id: str) -> dict | None:
        row = self._con.execute(
            "SELECT trip_id, start_ts, end_ts, duration_s, distance_m, point_count, "
            "start_geofence, end_geofence, direction FROM trips WHERE trip_id = ?",
            [trip_id],
        ).fetchone()
        if row is None:
            return None
        segments = [
            {"seg_index": s[0], "mode": s[1], "start_ts": _iso(s[2]), "end_ts": _iso(s[3]),
             "duration_s": s[4], "distance_m": s[5], "point_count": s[6]}
            for s in self._con.execute(
                "SELECT seg_index, mode, start_ts, end_ts, duration_s, distance_m, "
                "point_count FROM segments WHERE trip_id = ? ORDER BY seg_index",
                [trip_id],
            ).fetchall()
        ]
        points = [
            {"ts": _iso(p[0]), "lat": p[1], "lon": p[2], "accuracy_m": p[3],
             "speed_mps": p[4], "heading_deg": p[5], "distance_m": p[6], "geofence": p[7]}
            for p in self._con.execute(
                "SELECT ts, lat, lon, accuracy_m, speed_mps, heading_deg, distance_m, "
                "geofence FROM trip_points WHERE trip_id = ? ORDER BY ts",
                [trip_id],
            ).fetchall()
        ]
        return {"trip": self._trip_row_to_dict(row), "segments": segments, "points": points}

    @staticmethod
    def _trip_row_to_dict(r) -> dict:
        return {
            "trip_id": r[0], "start_ts": _iso(r[1]), "end_ts": _iso(r[2]),
            "duration_s": r[3], "distance_m": r[4], "point_count": r[5],
            "start_geofence": r[6], "end_geofence": r[7], "direction": r[8],
        }

    def close(self) -> None:
        self._con.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_derived_store.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/storage/derived.py backend/tests/test_derived_store.py
git commit -m "feat: derived duckdb store for trips, segments, rejections"
```

---

### Task 12: Rebuild — replay the archive

**Files:**
- Create: `backend/engine/rebuild.py`
- Test: `backend/tests/test_rebuild.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_rebuild.py
import json

from backend.engine.rebuild import rebuild
from backend.storage.archive import Archiver
from backend.storage.raw import RawStore
from backend.tests.synth import commute


def _ingest_synthetic_day(settings, day="2026-06-09"):
    """Write a synthetic commute as raw owntracks records on a given day."""
    store = RawStore(settings.data_dir)
    pts, _, _ = commute()
    for i, pt in enumerate(pts):
        payload = {"_type": "location", "tst": pt.ts, "lat": pt.lat, "lon": pt.lon,
                   "acc": pt.accuracy_m}
        store.append(
            "owntracks",
            {"received_at": f"{day}T{i // 3600:02d}:{(i // 60) % 60:02d}:{i % 60:02d}+00:00",
             "user": "j", "device": "d", "payload": payload},
        )
    return pts


def test_rebuild_from_raw_tail(settings):
    _ingest_synthetic_day(settings)
    engine, store, counts = rebuild(settings)
    assert counts["points"] > 50
    assert counts["trips"] == 1
    assert len(store.list_trips()) == 1


def test_rebuild_spans_archive_and_tail(settings):
    _ingest_synthetic_day(settings, day="2026-06-09")
    Archiver(settings).run(today="2026-06-10")  # day file → parquet
    # engine must see archived events through EventQuery
    engine, store, counts = rebuild(settings)
    assert counts["trips"] == 1


def test_rebuild_truncates_previous_derived(settings):
    _ingest_synthetic_day(settings)
    rebuild(settings)
    engine, store, counts = rebuild(settings)  # second run: same single trip
    assert len(store.list_trips()) == 1


def test_rebuild_skips_non_location(settings):
    store = RawStore(settings.data_dir)
    store.append(
        "owntracks",
        {"received_at": "2026-06-09T00:00:00+00:00", "user": "j", "device": "d",
         "payload": {"_type": "transition", "tst": 1}},
    )
    engine, dstore, counts = rebuild(settings)
    assert counts["skipped"] == 1
    assert counts.get("trips", 0) == 0
```

NOTE: the `received_at` values in `_ingest_synthetic_day` ascend with `i`
(HH:MM:SS built from the index), so `ORDER BY received_at` preserves the
synthetic `tst` order.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_rebuild.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.engine.rebuild`.

- [ ] **Step 3: Implement**

```python
# backend/engine/rebuild.py
"""Truncate the derived store and replay the entire archive + raw tail
through a fresh engine. The returned engine carries live state (an
in-progress trip survives into live processing).

CLI: python -m backend.engine.rebuild
"""

import json
import logging
from collections import Counter

from backend.config import Settings, load_settings
from backend.engine.geofence import geofences_from_settings
from backend.engine.machine import TripEngine
from backend.engine.params import EngineParams
from backend.engine.types import Point, TripClosed
from backend.storage.derived import DerivedStore
from backend.storage.query import EventQuery

log = logging.getLogger(__name__)


def rebuild(
    settings: Settings, params: EngineParams | None = None
) -> tuple[TripEngine, DerivedStore, dict]:
    store = DerivedStore(settings)
    store.truncate()
    engine = TripEngine(params or EngineParams(), geofences_from_settings(settings))
    q = EventQuery(settings)
    rel = q.events("owntracks")
    rows = q.sql(
        "SELECT CAST(payload AS VARCHAR) FROM rel ORDER BY received_at", rel=rel
    ).fetchall()
    counts: Counter = Counter()
    for (payload_text,) in rows:
        point = Point.from_owntracks(json.loads(payload_text))
        if point is None:
            counts["skipped"] += 1
            continue
        counts["points"] += 1
        for ev in engine.process(point):
            if isinstance(ev, TripClosed):
                store.write_trip_closed(ev)
                counts["trips"] += 1
            else:
                store.write_rejected(ev)
                counts["rejected"] += 1
    log.info("rebuild complete: %s", dict(counts))
    return engine, store, dict(counts)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _, _, report = rebuild(load_settings())
    print(json.dumps(report, indent=2))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_rebuild.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/engine/rebuild.py backend/tests/test_rebuild.py
git commit -m "feat: derived store rebuild by replaying the archive"
```

---

### Task 13: Replay-equivalence property test

**Files:**
- Test: `backend/tests/test_replay_equivalence.py`

This is the spec's load-bearing guarantee: processing a stream in one pass is
identical to processing a prefix, serializing state, restoring it, and
processing the remainder. No production code changes — if the test exposes a
bug, fix it in the engine and note it.

- [ ] **Step 1: Write the test**

```python
# backend/tests/test_replay_equivalence.py
from backend.engine.machine import EngineState, TripEngine
from backend.engine.params import EngineParams
from backend.engine.types import TripClosed
from backend.tests.synth import commute


def _events(engine, pts):
    out = []
    for pt in pts:
        out.extend(engine.process(pt))
    return out


def _trips(events):
    return [e.trip for e in events if isinstance(e, TripClosed)]


def test_split_replay_equals_full_replay():
    pts1, _, lat_end = commute(t0=1_781_100_000.0)
    pts2, _, _ = commute(t0=1_781_130_000.0, lat0=lat_end)
    stream = pts1 + pts2

    full = TripEngine(EngineParams(), geofences=[])
    full_trips = _trips(_events(full, stream))

    for split in (1, len(stream) // 3, len(stream) // 2, len(stream) - 1):
        first = TripEngine(EngineParams(), geofences=[])
        trips_a = _trips(_events(first, stream[:split]))
        # serialize → restore (simulates process restart)
        second = TripEngine(EngineParams(), geofences=[])
        second.state = EngineState.from_dict(first.state.to_dict())
        trips_b = _trips(_events(second, stream[split:]))
        assert trips_a + trips_b == full_trips, f"diverged at split={split}"
        assert second.state == full.state, f"state diverged at split={split}"
```

- [ ] **Step 2: Run the test**

Run: `pytest backend/tests/test_replay_equivalence.py -v`
Expected: PASS. If it fails, the engine has hidden non-determinism (wall-clock
use, dict-ordering, float drift through serialization) — debug and fix the
engine, do not weaken the assertion. (`asdict`/reconstruct preserves float
bit-patterns exactly, so float drift would indicate real state divergence.)

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_replay_equivalence.py
git commit -m "test: property test pinning split-replay equivalence"
```

---

### Task 14: Live wiring — runner, ingest hook, lifespan

**Files:**
- Create: `backend/engine/runner.py`
- Modify: `backend/app.py`
- Modify: `backend/ingest/routes.py`
- Test: `backend/tests/test_runner_live.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_runner_live.py
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.synth import commute


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c, app


def test_live_points_produce_a_trip(client, settings):
    c, app = client
    pts, _, _ = commute()
    for pt in pts:
        payload = {"_type": "location", "tst": pt.ts, "lat": pt.lat, "lon": pt.lon,
                   "acc": pt.accuracy_m}
        resp = c.post("/ingest/owntracks", json=payload)
        assert resp.status_code == 200
    trips = app.state.runner.store.list_trips()
    assert len(trips) == 1
    assert trips[0]["distance_m"] > 15000


def test_engine_failure_never_affects_200(client, monkeypatch):
    c, app = client

    def boom(payload):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(app.state.runner, "process_payload", boom)
    resp = c.post("/ingest/owntracks", json={"_type": "location", "tst": 1, "lat": 1, "lon": 2})
    assert resp.status_code == 200


def test_startup_rebuild_recovers_history(settings):
    # pre-existing raw data is replayed into the derived store at startup
    from backend.storage.raw import RawStore

    store = RawStore(settings.data_dir)
    pts, _, _ = commute()
    for i, pt in enumerate(pts):
        # ascending received_at: ORDER BY received_at must reproduce tst order
        # (DuckDB sort is not stable for equal keys)
        store.append(
            "owntracks",
            {"received_at": f"2026-06-09T{i // 3600:02d}:{(i // 60) % 60:02d}:{i % 60:02d}+00:00",
             "user": "j", "device": "d",
             "payload": {"_type": "location", "tst": pt.ts, "lat": pt.lat,
                         "lon": pt.lon, "acc": pt.accuracy_m}},
        )
    app = create_app(settings)
    with TestClient(app):
        assert len(app.state.runner.store.list_trips()) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_runner_live.py -v`
Expected: FAIL — `app.state.runner` does not exist.

- [ ] **Step 3: Implement the runner**

```python
# backend/engine/runner.py
"""Owns the live engine + derived store. Startup performs a full rebuild so
live state continues seamlessly from history (an in-progress trip at the end
of the archive stays open in the live engine)."""

import logging

from backend.config import Settings
from backend.engine.machine import TripEngine
from backend.engine.rebuild import rebuild
from backend.engine.types import Point, TripClosed
from backend.storage.derived import DerivedStore

log = logging.getLogger(__name__)


class EngineRunner:
    def __init__(self, engine: TripEngine, store: DerivedStore):
        self.engine = engine
        self.store = store

    @classmethod
    def start(cls, settings: Settings) -> "EngineRunner":
        engine, store, counts = rebuild(settings)
        log.info("engine runner started after rebuild: %s", counts)
        return cls(engine, store)

    def process_payload(self, payload: dict) -> None:
        point = Point.from_owntracks(payload)
        if point is None:
            return
        for ev in self.engine.process(point):
            if isinstance(ev, TripClosed):
                self.store.write_trip_closed(ev)
            else:
                self.store.write_rejected(ev)

    def close(self) -> None:
        self.store.close()
```

- [ ] **Step 4: Wire into `backend/app.py`**

Add import `from backend.engine.runner import EngineRunner` and modify the
lifespan (runner starts BEFORE serving; rebuild runs off-thread to not block
the loop during a slow start):

```python
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runner = await asyncio.to_thread(EngineRunner.start, settings)
        archiver = Archiver(settings)
        task = asyncio.create_task(run_daily(archiver.run, hour_utc=settings.archive_hour_utc))
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await app.state.passthrough.aclose()
        app.state.runner.close()
```

- [ ] **Step 5: Hook the ingest route**

In `backend/ingest/routes.py`, in the valid-JSON branch, AFTER
`store.append("owntracks", record)` add:

```python
                try:
                    request.app.state.runner.process_payload(payload)
                except Exception:
                    log.exception("engine processing failed — raw is safe; rebuild recovers")
```

(Placement: inside the inner `try` that parsed the JSON, after the append —
the raw write must already be durable before the engine sees the point, and
an engine bug must never affect the 200.)

- [ ] **Step 6: Run the whole backend suite**

Run: `pytest backend/tests -q`
Expected: all PASS. The existing ingest tests pass because the lifespan now
creates a runner over the empty tmp data dir (instant rebuild).

- [ ] **Step 7: Commit**

```bash
git add backend/engine/runner.py backend/app.py backend/ingest/routes.py backend/tests/test_runner_live.py
git commit -m "feat: live engine wired into ingest with startup rebuild"
```

---

### Task 15: Trips API

**Files:**
- Create: `backend/api/__init__.py` (empty)
- Create: `backend/api/trips.py`
- Modify: `backend/app.py`
- Test: `backend/tests/test_trips_api.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_trips_api.py
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.synth import commute


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        # drive a commute through live ingest so a trip exists
        pts, _, _ = commute()
        for pt in pts:
            c.post("/ingest/owntracks", json={"_type": "location", "tst": pt.ts,
                                              "lat": pt.lat, "lon": pt.lon,
                                              "acc": pt.accuracy_m})
        yield c


def test_list_trips(client):
    resp = client.get("/api/trips")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["direction"] in ("outbound", "inbound", "other")
    assert body[0]["start_ts"].endswith("+00:00")


def test_list_trips_respects_limit(client):
    assert client.get("/api/trips?limit=0").json() == []


def test_trip_detail(client):
    trip_id = client.get("/api/trips").json()[0]["trip_id"]
    resp = client.get(f"/api/trips/{trip_id}")
    assert resp.status_code == 200
    d = resp.json()
    assert d["trip"]["trip_id"] == trip_id
    assert len(d["segments"]) >= 1
    assert len(d["points"]) > 10


def test_trip_detail_404(client):
    assert client.get("/api/trips/nope").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_trips_api.py -v`
Expected: FAIL — 404 on /api/trips (router not registered).

- [ ] **Step 3: Implement**

```python
# backend/api/trips.py
from fastapi import APIRouter, HTTPException, Request


def make_trips_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/trips")
    async def list_trips(request: Request, limit: int = 50) -> list[dict]:
        return request.app.state.runner.store.list_trips(limit=limit)

    @router.get("/api/trips/{trip_id}")
    async def trip_detail(request: Request, trip_id: str) -> dict:
        detail = request.app.state.runner.store.get_trip(trip_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="trip not found")
        return detail

    return router
```

In `backend/app.py`: add `from backend.api.trips import make_trips_router` and
`app.include_router(make_trips_router())` next to the other router includes.
Create empty `backend/api/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/api/ backend/app.py backend/tests/test_trips_api.py
git commit -m "feat: trips list and detail api endpoints"
```

---

### Task 16: Docs + final verification

**Files:**
- Modify: `README.md` (rewrite backend section)

- [ ] **Step 1: Extend the README rewrite-backend section**

Add to the endpoint list: `GET /api/trips?limit=`, `GET /api/trips/{trip_id}`.
Add env vars: `CT_HOME_LAT`/`CT_HOME_LON`/`CT_HOME_RADIUS_M`,
`CT_WORK_LAT`/`CT_WORK_LON`/`CT_WORK_RADIUS_M` (geofences for commute
direction tagging; unset = no direction detection).
Add: `python -m backend.engine.rebuild` — truncate + replay the archive into
the derived store (also runs automatically at startup).

- [ ] **Step 2: Full verification**

Run: `ruff format backend/ && ruff check src/ tests/ backend/ && ruff format --check src/ tests/ backend/ && pytest --tb=short -q`
Expected: clean, all tests pass (289 from phase 1 + ~45 new).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: trip engine endpoints, geofence env vars, rebuild cli"
```

---

## Verification at phase end

1. `pytest --tb=short -q` — entire suite green.
2. `ruff check src/ tests/ backend/ && ruff format --check src/ tests/ backend/` — clean.
3. Container smoke test (podman): build `Dockerfile.backend`, run with
   `CT_HOME_LAT/LON` + `CT_WORK_LAT/LON` set, POST a synthetic commute
   (script the `commute()` points via curl or python), then
   `GET /api/trips` shows the trip with direction "outbound".
4. `gh run list --limit 2` after push — CI green.
5. Optional realism check: run `python -m scripts.migrate_legacy_raw` against a
   copy of the local legacy DB into a scratch data dir, then
   `CT_DATA_DIR=<scratch> python -m backend.engine.rebuild` — inspect trip
   count/durations against the legacy dashboard's numbers for the same dates.
