# Timezone-Aware Timestamps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store all timestamps as timezone-aware UTC, derive per-point timezone from GPS coordinates, use local time for date grouping and display.

**Architecture:** The enricher resolves each GPS point's timezone via `timezonefinder`, stores tz-aware UTC `timestamp` + naive local `timestamp_local` + IANA `timezone` string. Pipeline and commute detector use `timestamp_local` for date grouping. Dashboard converts UTC to browser timezone for display.

**Tech Stack:** `timezonefinder` (timezone from lat/lon), `zoneinfo` (stdlib), Polars tz-aware datetimes, Streamlit `st.context.timezone`

**Spec:** `docs/superpowers/specs/2026-03-27-timezone-aware-timestamps-design.md`

---

### Task 1: Add dependency and config

**Files:**
- Modify: `pyproject.toml:6-18`
- Modify: `src/config.py:49` (append)

- [ ] **Step 1: Add timezonefinder to pyproject.toml**

In `pyproject.toml`, add `timezonefinder` to the dependencies list:

```toml
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.34",
    "polars>=1.0",
    "duckdb>=1.0",
    "streamlit>=1.41",
    "folium>=0.18",
    "streamlit-folium>=0.22",
    "altair>=5.0",
    "pyarrow>=18.0",
    "boto3>=1.35",
    "sqlalchemy>=2.0",
    "mcp>=1.0",
    "timezonefinder>=6.0",
]
```

Note: bump `streamlit` from `>=1.40` to `>=1.41` for `st.context.timezone` support.

- [ ] **Step 2: Add TIMEZONE config**

Append to `src/config.py` after line 49:

```python
# Fallback timezone when GPS-based resolution fails (ocean, null coordinates)
TIMEZONE = os.environ.get("TIMEZONE", "America/New_York")
```

- [ ] **Step 3: Install dependencies**

Run: `pip install -e ".[dev]"`

Expected: timezonefinder installs successfully.

- [ ] **Step 4: Verify import works**

Run: `python -c "from timezonefinder import TimezoneFinder; print('OK')"`

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/config.py
git commit -m "chore: add timezonefinder dependency and TIMEZONE config"
```

---

### Task 2: Create tz_resolver module (TDD)

**Files:**
- Create: `src/processing/tz_resolver.py`
- Create: `tests/test_tz_resolver.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tz_resolver.py`:

```python
"""Tests for GPS-based timezone resolution."""

from unittest.mock import patch

from src.processing.tz_resolver import resolve_timezone, resolve_timezones


def test_resolve_known_location_nyc():
    """NYC coordinates should resolve to America/New_York."""
    tz = resolve_timezone(40.7128, -74.0060)
    assert tz == "America/New_York"


def test_resolve_known_location_london():
    """London coordinates should resolve to Europe/London."""
    tz = resolve_timezone(51.5074, -0.1278)
    assert tz == "Europe/London"


def test_resolve_known_location_tokyo():
    """Tokyo coordinates should resolve to Asia/Tokyo."""
    tz = resolve_timezone(35.6762, 139.6503)
    assert tz == "Asia/Tokyo"


def test_resolve_fallback_on_none():
    """When timezonefinder returns None, fall back to TIMEZONE config."""
    with patch("src.processing.tz_resolver._finder") as mock_finder:
        mock_finder.return_value.timezone_at.return_value = None
        tz = resolve_timezone(0.0, 0.0)
    # Falls back to config TIMEZONE
    from src.config import TIMEZONE

    assert tz == TIMEZONE


def test_resolve_timezones_batch():
    """Batch resolution should return a list of timezone strings."""
    lats = [40.7128, 51.5074, 35.6762]
    lons = [-74.0060, -0.1278, 139.6503]
    result = resolve_timezones(lats, lons)
    assert len(result) == 3
    assert result[0] == "America/New_York"
    assert result[1] == "Europe/London"
    assert result[2] == "Asia/Tokyo"


def test_resolve_timezones_empty():
    """Empty input should return empty list."""
    assert resolve_timezones([], []) == []


def test_finder_instance_reused():
    """The TimezoneFinder instance should be cached (not recreated each call)."""
    from src.processing.tz_resolver import _finder

    f1 = _finder()
    f2 = _finder()
    assert f1 is f2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tz_resolver.py -v`

Expected: FAIL (module does not exist)

- [ ] **Step 3: Implement tz_resolver**

Create `src/processing/tz_resolver.py`:

```python
"""Resolve IANA timezone from GPS coordinates.

Uses timezonefinder to map (lat, lon) to timezone strings like
"America/New_York". Falls back to TIMEZONE config when resolution
fails (ocean, null coordinates).
"""

from functools import lru_cache

from timezonefinder import TimezoneFinder

from src.config import TIMEZONE


@lru_cache(maxsize=1)
def _finder() -> TimezoneFinder:
    """Cached TimezoneFinder instance (~50ms to create, cheap to query)."""
    return TimezoneFinder()


def resolve_timezone(lat: float, lon: float) -> str:
    """Resolve a single coordinate pair to an IANA timezone string."""
    result = _finder().timezone_at(lng=lon, lat=lat)
    return result or TIMEZONE


def resolve_timezones(lats: list[float], lons: list[float]) -> list[str]:
    """Resolve a batch of coordinate pairs to timezone strings."""
    finder = _finder()
    return [finder.timezone_at(lng=lon, lat=lat) or TIMEZONE for lat, lon in zip(lats, lons)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tz_resolver.py -v`

Expected: all 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/processing/tz_resolver.py tests/test_tz_resolver.py
git commit -m "feat: add GPS-based timezone resolver"
```

---

### Task 3: Make enricher timezone-aware (TDD)

**Files:**
- Modify: `src/processing/enricher.py:1-55`
- Test: `tests/test_audit_fixes.py` (add new tests)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_audit_fixes.py` after the last test:

```python
# ── Fix 13: Timezone-aware timestamps (#11) ─────────────────────────────────


def test_enricher_timestamp_is_tz_aware_utc():
    """Enricher must produce timezone-aware UTC timestamps.

    Naive timestamps caused wrong date grouping and commute IDs when the
    server timezone differed from the user's timezone.
    See: https://github.com/jflammia/commuteTracker/issues/11
    """
    from src.processing.enricher import enrich

    df = pl.DataFrame(
        {
            "lat": [40.7128, 40.7130],
            "lon": [-74.0060, -74.0062],
            "tst": [1711440000, 1711440060],
        }
    )
    result = enrich(df)
    assert result["timestamp"].dtype == pl.Datetime("us", "UTC")


def test_enricher_adds_timezone_column():
    """Enricher must add a timezone column derived from GPS coordinates.

    See: https://github.com/jflammia/commuteTracker/issues/11
    """
    from src.processing.enricher import enrich

    df = pl.DataFrame(
        {
            "lat": [40.7128, 40.7130],
            "lon": [-74.0060, -74.0062],
            "tst": [1711440000, 1711440060],
        }
    )
    result = enrich(df)
    assert "timezone" in result.columns
    assert result["timezone"][0] == "America/New_York"


def test_enricher_adds_timestamp_local():
    """Enricher must add a timestamp_local column in the point's local timezone.

    timestamp_local is a naive datetime representing local time, used for
    date grouping and commute ID generation.
    See: https://github.com/jflammia/commuteTracker/issues/11
    """
    from datetime import datetime, timezone as dt_timezone
    from zoneinfo import ZoneInfo

    from src.processing.enricher import enrich

    tst = 1711440000  # 2024-03-26T08:00:00Z
    df = pl.DataFrame(
        {
            "lat": [40.7128],
            "lon": [-74.0060],
            "tst": [tst],
        }
    )
    result = enrich(df)
    assert "timestamp_local" in result.columns

    # Verify the local time matches manual conversion
    utc_dt = datetime.fromtimestamp(tst, tz=dt_timezone.utc)
    expected_local = utc_dt.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
    actual_local = result["timestamp_local"][0]
    assert actual_local == expected_local


def test_enricher_late_night_utc_correct_local_date():
    """A point at 03:00 UTC should have a local date of the previous day in EDT.

    This is the core bug: 03:00 UTC = 23:00 EDT previous day, so the local
    date should be the day before the UTC date.
    See: https://github.com/jflammia/commuteTracker/issues/11
    """
    from datetime import date

    from src.processing.enricher import enrich

    # 2024-03-27 03:00:00 UTC = 2024-03-26 23:00:00 EDT
    tst = 1711508400
    df = pl.DataFrame(
        {
            "lat": [40.7128],
            "lon": [-74.0060],
            "tst": [tst],
        }
    )
    result = enrich(df)
    local_date = result["timestamp_local"].dt.date()[0]
    assert local_date == date(2024, 3, 26), f"Expected 2024-03-26, got {local_date}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_audit_fixes.py::test_enricher_timestamp_is_tz_aware_utc tests/test_audit_fixes.py::test_enricher_adds_timezone_column tests/test_audit_fixes.py::test_enricher_adds_timestamp_local tests/test_audit_fixes.py::test_enricher_late_night_utc_correct_local_date -v`

Expected: FAIL (enricher doesn't produce these columns yet)

- [ ] **Step 3: Implement enricher timezone support**

Replace `src/processing/enricher.py` with:

```python
"""Enrich raw location data with computed fields.

Takes a Polars DataFrame of raw location records and adds:
- Computed speed between consecutive points
- Distance from previous point
- Time delta from previous point
- Stationary/moving flag
- Timezone (IANA string from GPS coordinates)
- Timezone-aware UTC timestamp
- Local timestamp for date grouping
"""

from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

import polars as pl

from src.processing.geo_utils import haversine_m, speed_kmh
from src.processing.tz_resolver import resolve_timezones


def enrich(df: pl.DataFrame) -> pl.DataFrame:
    """Add computed columns to a DataFrame of location records.

    Expects columns: lat, lon, tst (unix timestamp).
    Adds: timestamp (UTC), timezone, timestamp_local, distance_m,
          time_delta_s, speed_kmh, is_stationary.
    """
    if df.is_empty():
        return df

    # Ensure sorted by timestamp
    df = df.sort("tst")

    # Convert unix timestamp to timezone-aware UTC datetime
    if "timestamp" not in df.columns:
        df = df.with_columns(
            pl.from_epoch("tst", time_unit="s").dt.replace_time_zone("UTC").alias("timestamp"),
        )

    # Resolve timezone from GPS coordinates
    lats = df["lat"].to_list()
    lons = df["lon"].to_list()
    tsts = df["tst"].to_list()
    timezones = resolve_timezones(lats, lons)

    # Compute local timestamps (naive datetime in each point's local timezone)
    local_times = []
    for tst_val, tz_str in zip(tsts, timezones):
        utc_dt = datetime.fromtimestamp(tst_val, tz=dt_timezone.utc)
        local_dt = utc_dt.astimezone(ZoneInfo(tz_str))
        local_times.append(local_dt.replace(tzinfo=None))

    # Compute distance, time delta, speed
    distances = [0.0]
    time_deltas = [0.0]
    speeds = [0.0]

    for i in range(1, len(lats)):
        d = haversine_m(lats[i - 1], lons[i - 1], lats[i], lons[i])
        dt = tsts[i] - tsts[i - 1]
        distances.append(d)
        time_deltas.append(float(dt))
        speeds.append(speed_kmh(d, dt))

    df = df.with_columns(
        pl.Series("timezone", timezones),
        pl.Series("timestamp_local", local_times),
        pl.Series("distance_m", distances),
        pl.Series("time_delta_s", time_deltas),
        pl.Series("speed_kmh", speeds),
        pl.Series("is_stationary", [s < 1.0 for s in speeds]),
    )

    return df
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `pytest tests/test_audit_fixes.py::test_enricher_timestamp_is_tz_aware_utc tests/test_audit_fixes.py::test_enricher_adds_timezone_column tests/test_audit_fixes.py::test_enricher_adds_timestamp_local tests/test_audit_fixes.py::test_enricher_late_night_utc_correct_local_date -v`

Expected: all 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/processing/enricher.py tests/test_audit_fixes.py
git commit -m "feat(enricher): add GPS-derived timezone and local timestamps

Resolves per-point timezone from lat/lon via timezonefinder. Stores
timezone-aware UTC timestamp, IANA timezone string, and naive local
timestamp for date grouping.

Partial fix for #11"
```

---

### Task 4: Update commute detector to use local dates (TDD)

**Files:**
- Modify: `src/processing/commute_detector.py:38,95`
- Test: `tests/test_audit_fixes.py` (add new test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_audit_fixes.py`:

```python
def test_commute_id_uses_local_date():
    """Commute IDs must use local date, not UTC date.

    A commute starting at 03:00 UTC in America/New_York (23:00 EDT previous
    day) should have the previous day's date in its commute ID.
    See: https://github.com/jflammia/commuteTracker/issues/11
    """
    from unittest.mock import patch as _patch

    # Base time: 2024-03-27 03:00 UTC = 2024-03-26 23:00 EDT
    base_tst = 1711508400

    # Build points: at home, transit, at work (all in same UTC "day" March 27)
    rows = []
    # At home (3 points)
    for i in range(3):
        rows.append({"_type": "location", "lat": HOME[0], "lon": HOME[1], "tst": base_tst + i * 10})
    # Transit (10 points)
    for i in range(1, 11):
        frac = i / 10
        lat = HOME[0] + (WORK[0] - HOME[0]) * frac
        lon = HOME[1] + (WORK[1] - HOME[1]) * frac
        rows.append({"_type": "location", "lat": lat, "lon": lon, "tst": base_tst + 30 + i * 10})
    # At work (3 points)
    for i in range(3):
        rows.append(
            {"_type": "location", "lat": WORK[0], "lon": WORK[1], "tst": base_tst + 140 + i * 10}
        )

    df = pl.DataFrame(rows)
    with _patch.multiple(
        "src.processing.pipeline",
        HOME_LAT=HOME[0],
        HOME_LON=HOME[1],
        HOME_RADIUS_M=200.0,
        WORK_LAT=WORK[0],
        WORK_LON=WORK[1],
        WORK_RADIUS_M=200.0,
    ):
        result = process_locations(df)

    commute_ids = result["commute_id"].drop_nulls().unique().to_list()
    assert len(commute_ids) == 1

    # Commute ID should use local date (2024-03-26), not UTC date (2024-03-27)
    cid = commute_ids[0]
    assert cid.startswith("2024-03-26"), f"Expected local date 2024-03-26, got commute ID: {cid}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_audit_fixes.py::test_commute_id_uses_local_date -v`

Expected: FAIL (commute ID uses UTC date 2024-03-27)

- [ ] **Step 3: Update commute detector**

In `src/processing/commute_detector.py`, make two changes:

1. Add `timestamp_local` to the data read at line 38:

```python
    timestamps = df["timestamp"].to_list()
    local_timestamps = df["timestamp_local"].to_list()
```

2. At line 95, use `local_timestamps` instead of `timestamps` for the date string:

```python
                date_str = local_timestamps[commute_start_idx].strftime("%Y-%m-%d")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_audit_fixes.py::test_commute_id_uses_local_date -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/processing/commute_detector.py tests/test_audit_fixes.py
git commit -m "fix(commute-detector): use local date for commute IDs

Commute IDs now use timestamp_local (derived from GPS timezone) for the
date portion. A 11 PM EDT commute gets today's date, not tomorrow's.

Partial fix for #11"
```

---

### Task 5: Update pipeline date grouping and filters (TDD)

**Files:**
- Modify: `src/processing/pipeline.py:174-228,234-267`
- Test: `tests/test_audit_fixes.py` (add new tests)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_audit_fixes.py`:

```python
def test_parquet_file_named_by_local_date(db, derived_dir):
    """Parquet files must be named by local date, not UTC date.

    A point at 03:00 UTC in EDT (23:00 previous day) should land in the
    previous day's Parquet file.
    See: https://github.com/jflammia/commuteTracker/issues/11
    """
    from pathlib import Path

    # 2024-03-27 03:30 UTC = 2024-03-26 23:30 EDT
    tst = 1711510200
    _insert_location(db, 40.7128, -74.0060, tst)

    with _pipeline_config():
        results = process_from_db(db, output_dir=derived_dir)

    # Should create a file for 2024-03-26 (local date), not 2024-03-27 (UTC date)
    files = list(Path(derived_dir).rglob("*.parquet"))
    assert len(files) == 1
    assert "2024-03-26" in files[0].name, f"Expected local date 2024-03-26, got {files[0].name}"


def test_pipeline_filter_interprets_dates_as_local_tz(db, derived_dir):
    """since/until filters must be interpreted as local timezone dates.

    When filtering for 2024-03-26 in America/New_York, the range should be
    2024-03-26 00:00 EDT (04:00 UTC) to 2024-03-26 23:59 EDT (03:59 UTC next day).
    See: https://github.com/jflammia/commuteTracker/issues/11
    """
    # Point at 2024-03-26 12:00 EDT = 2024-03-26 16:00 UTC (tst=1711468800)
    tst_in_range = 1711468800
    _insert_location(db, 40.7128, -74.0060, tst_in_range)

    # Point at 2024-03-26 03:00 UTC = 2024-03-25 23:00 EDT (should be EXCLUDED)
    tst_out_of_range = 1711422000
    _insert_location(db, 40.7128, -74.0060, tst_out_of_range)

    with _pipeline_config():
        results = process_from_db(
            db, output_dir=derived_dir, filters={"since": "2024-03-26", "until": "2024-03-26"}
        )
    assert results["total_records"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_audit_fixes.py::test_parquet_file_named_by_local_date tests/test_audit_fixes.py::test_pipeline_filter_interprets_dates_as_local_tz -v`

Expected: FAIL

- [ ] **Step 3: Update pipeline date grouping**

In `src/processing/pipeline.py`, change lines 210-212 from:

```python
    df = df.with_columns(
        pl.col("timestamp").dt.date().alias("date"),
    )
```

To:

```python
    df = df.with_columns(
        pl.col("timestamp_local").dt.date().alias("date"),
    )
```

- [ ] **Step 4: Update pipeline date filters**

In `src/processing/pipeline.py`, change lines 176-188. Replace the UTC interpretation with local timezone:

```python
    if filters:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from src.config import TIMEZONE

        tz = ZoneInfo(TIMEZONE)
        if "since" in filters:
            since_dt = datetime.strptime(filters["since"], "%Y-%m-%d").replace(tzinfo=tz)
            since_tst = int(since_dt.timestamp())
            df = df.filter(pl.col("tst") >= since_tst)
        if "until" in filters:
            until_dt = datetime.strptime(filters["until"], "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=tz
            )
            until_tst = int(until_dt.timestamp())
            df = df.filter(pl.col("tst") <= until_tst)
```

Remove the `from datetime import datetime, timezone` import that was previously at line 177.

- [ ] **Step 5: Update process_jsonl to group by local date**

In `src/processing/pipeline.py`, replace the `process_jsonl` function (lines 234-267) with a version that groups by `timestamp_local` date instead of using the JSONL filename:

```python
def process_jsonl(jsonl_path: str | Path, output_dir: str | Path | None = None) -> dict:
    """Process a single JSONL file and write enriched Parquet.

    Useful for reprocessing raw data from S3/backups.
    Groups output by local date (from GPS timezone), not input filename.
    """
    jsonl_path = Path(jsonl_path)
    output_dir = Path(output_dir or DERIVED_DATA_DIR)

    df = pl.read_ndjson(jsonl_path)
    results = {"total_records": len(df), "commutes_found": 0, "files_written": []}

    required = {"lat", "lon", "tst"}
    if not required.issubset(set(df.columns)):
        logger.warning(f"Missing required columns in {jsonl_path}")
        return results

    df = process_locations(df)

    if "commute_id" in df.columns:
        commute_ids = df["commute_id"].drop_nulls().unique()
        results["commutes_found"] = len(commute_ids)

    # Group by local date and write one Parquet per day
    df = df.with_columns(
        pl.col("timestamp_local").dt.date().alias("date"),
    )

    for date_val in df["date"].unique().sort().to_list():
        day_df = df.filter(pl.col("date") == date_val)
        date_str = str(date_val)
        year = date_str[:4]
        month = date_str[5:7]

        parquet_dir = output_dir / year / month
        parquet_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = parquet_dir / f"{date_str}.parquet"

        day_df = day_df.drop("date")
        day_df.write_parquet(parquet_path)

        results["files_written"].append(str(parquet_path))
        logger.info(f"Wrote {parquet_path} ({len(day_df)} records)")

    return results
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_audit_fixes.py::test_parquet_file_named_by_local_date tests/test_audit_fixes.py::test_pipeline_filter_interprets_dates_as_local_tz -v`

Expected: both PASS

- [ ] **Step 7: Commit**

```bash
git add src/processing/pipeline.py tests/test_audit_fixes.py
git commit -m "fix(pipeline): use local timezone for date grouping and filters

Parquet files are now named by the point's local date (derived from GPS
timezone). Date filters (since/until) are interpreted as local timezone
midnight using the TIMEZONE config.

Partial fix for #11"
```

---

### Task 6: Update derived store for local date queries (TDD)

**Files:**
- Modify: `src/storage/derived_store.py:85-95`
- Test: `tests/test_audit_fixes.py` (add new test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_audit_fixes.py`:

```python
def test_daily_summary_uses_local_date(db, derived_dir):
    """get_daily_summary must query by local date, not UTC date.

    A point at 03:30 UTC in EDT (23:30 previous day) should be returned
    when querying the previous day's local date.
    See: https://github.com/jflammia/commuteTracker/issues/11
    """
    # 2024-03-27 03:30 UTC = 2024-03-26 23:30 EDT
    tst = 1711510200
    _insert_location(db, 40.7128, -74.0060, tst)
    _rebuild_all(db, derived_dir)

    store = DerivedStore(derived_dir)

    # Query by local date should find the point
    result = store.get_daily_summary("2024-03-26")
    assert not result.is_empty(), "Point at 23:30 EDT should be in 2024-03-26 local summary"

    # Query by UTC date should NOT find it (it's in a different local day)
    result_utc = store.get_daily_summary("2024-03-27")
    assert result_utc.is_empty(), "Point at 23:30 EDT should NOT be in 2024-03-27 local summary"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_audit_fixes.py::test_daily_summary_uses_local_date -v`

Expected: FAIL

- [ ] **Step 3: Update derived_store.py**

In `src/storage/derived_store.py`, change `get_daily_summary` (lines 85-95) from:

```python
    def get_daily_summary(self, date: str) -> pl.DataFrame:
        """Get all points for a given date (YYYY-MM-DD)."""
        return self.query(
            """
            SELECT *
            FROM commute_data
            WHERE CAST(timestamp AS DATE) = CAST($1 AS DATE)
            ORDER BY timestamp
            """,
            [date],
        )
```

To:

```python
    def get_daily_summary(self, date: str) -> pl.DataFrame:
        """Get all points for a given local date (YYYY-MM-DD)."""
        return self.query(
            """
            SELECT *
            FROM commute_data
            WHERE CAST(timestamp_local AS DATE) = CAST($1 AS DATE)
            ORDER BY timestamp
            """,
            [date],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_audit_fixes.py::test_daily_summary_uses_local_date -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/storage/derived_store.py tests/test_audit_fixes.py
git commit -m "fix(derived-store): query daily summary by local date

get_daily_summary now filters by timestamp_local instead of UTC
timestamp, matching the local date used for Parquet file naming.

Partial fix for #11"
```

---

### Task 7: Fix existing tests and add regression test summary

**Files:**
- Modify: `tests/test_audit_fixes.py` (fix broken tests)

The enricher now produces tz-aware UTC timestamps. Several existing tests may need adjustments:

- [ ] **Step 1: Run the full test suite**

Run: `pytest --tb=short -q`

Identify which tests fail due to the tz-aware timestamp change.

- [ ] **Step 2: Fix broken tests**

Common fixes needed:

1. **`test_pipeline_date_filter_uses_gps_tst`**: The timestamps `1711411200` (2024-03-26 00:00 UTC = 2024-03-25 20:00 EDT) will now be excluded by a `since=2024-03-26` filter interpreted as EDT. Update to use timestamps clearly within the local date:

```python
def test_pipeline_date_filter_uses_gps_tst(db, derived_dir):
    """Date filters should match GPS timestamp in local timezone."""
    # 2024-03-26 12:00 EDT = 2024-03-26 16:00 UTC (clearly within March 26 local)
    march26_tst = 1711468800
    for i in range(5):
        _insert_location(db, 40.75 + i * 0.001, -74.00, march26_tst + i * 60)

    # 2024-03-27 12:00 EDT = 2024-03-27 16:00 UTC
    march27_tst = 1711555200
    for i in range(5):
        _insert_location(db, 40.76 + i * 0.001, -74.00, march27_tst + i * 60)

    with _pipeline_config():
        results = process_from_db(
            db, output_dir=derived_dir, filters={"since": "2024-03-26", "until": "2024-03-26"}
        )
    assert results["total_records"] == 5
```

2. **`test_derived_store_parameterized_daily`**: The `_seed_commute` uses `base_tst=1711440000` (2024-03-26 08:00 UTC = 2024-03-26 04:00 EDT). The local date is still 2024-03-26, so querying `"2024-03-26"` should still work. Verify this test passes as-is.

3. **Other tests using `process_locations`**: The output DataFrame now has `timezone` and `timestamp_local` columns. Tests that check column counts or specific schemas may need updating.

Fix each broken test to work with the new tz-aware timestamps. The key principle: use timestamps that are clearly within the expected local date (noon-ish local time, not midnight UTC).

- [ ] **Step 3: Run full test suite to verify all pass**

Run: `pytest --tb=short -q`

Expected: all tests PASS (including the new timezone tests from tasks 2-6)

- [ ] **Step 4: Commit**

```bash
git add tests/test_audit_fixes.py
git commit -m "test: update existing tests for timezone-aware timestamps

Adjusts test timestamps to use values clearly within local dates
(noon EDT) instead of UTC midnight boundaries.

Partial fix for #11"
```

---

### Task 8: Migrate orphaned labels after commute ID changes

**Files:**
- Modify: `src/storage/label_store.py`
- Modify: `src/api/service.py`

Some commute IDs will change when local dates replace UTC dates (e.g., `2024-03-27-evening` becomes `2024-03-26-evening`). Labels keyed to old IDs need updating.

- [ ] **Step 1: Add a label migration method to LabelStore**

Add to `src/storage/label_store.py`:

```python
    def migrate_commute_ids(self, old_to_new: dict[str, str]) -> int:
        """Update commute_id on labels where the commute ID changed.

        Returns the number of labels updated.
        """
        count = 0
        with self._db.session() as session:
            for old_id, new_id in old_to_new.items():
                updated = (
                    session.query(SegmentLabelRecord)
                    .filter(SegmentLabelRecord.commute_id == old_id)
                    .update({SegmentLabelRecord.commute_id: new_id})
                )
                count += updated
            session.commit()
        if count:
            logger.info(f"Migrated {count} label(s) to new commute IDs")
        return count
```

- [ ] **Step 2: Add orphan detection to the rebuild service method**

In `src/api/service.py`, after the rebuild processes data, compare old and new commute IDs. In the `rebuild` method, add logic to:

1. Before rebuild: snapshot existing commute IDs from Parquet
2. After rebuild: compare old vs new commute IDs
3. For orphaned IDs (old ID not in new set), try to match by `(date ± 1 day, direction)`
4. Call `label_store.migrate_commute_ids()` with the mapping

```python
# In the rebuild method, after writing new Parquet files:
old_ids = set(old_commutes["commute_id"].to_list()) if not old_commutes.is_empty() else set()
new_ids = set(new_commutes["commute_id"].to_list()) if not new_commutes.is_empty() else set()
orphaned = old_ids - new_ids

if orphaned:
    # Try to match orphaned IDs: parse date+direction, look for ±1 day match
    import re
    id_map = {}
    for old_id in orphaned:
        match = re.match(r"(\d{4}-\d{2}-\d{2})-(morning|evening)", old_id)
        if not match:
            continue
        old_date_str, direction = match.groups()
        old_date = date.fromisoformat(old_date_str)
        # Check ±1 day
        for delta in [-1, 1]:
            candidate_date = old_date + timedelta(days=delta)
            candidate_id = f"{candidate_date}-{direction}"
            if candidate_id in new_ids:
                id_map[old_id] = candidate_id
                break
    if id_map:
        self._label_store.migrate_commute_ids(id_map)
```

- [ ] **Step 3: Commit**

```bash
git add src/storage/label_store.py src/api/service.py
git commit -m "feat: migrate orphaned labels when commute IDs change

After rebuild, detects labels keyed to old commute IDs (from UTC date
grouping) and remaps them to new IDs (local date grouping) by matching
direction and ±1 day offset.

Partial fix for #11"
```

---

### Task 9: Add timezone to API health endpoint

**Files:**
- Modify: `src/api/routes.py:128-134`
- Test: `tests/test_audit_fixes.py` (add new test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_audit_fixes.py`:

```python
def test_health_includes_timezone(client):
    """Health endpoint must include the configured timezone.

    See: https://github.com/jflammia/commuteTracker/issues/11
    """
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "timezone" in data
    assert isinstance(data["timezone"], str)
    assert len(data["timezone"]) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_audit_fixes.py::test_health_includes_timezone -v`

Expected: FAIL (timezone not in health response)

- [ ] **Step 3: Add timezone to health endpoint**

In `src/api/routes.py`, modify the `api_health` function (line 128-134):

```python
@router.get("/health", tags=["system"], summary="System health check")
def api_health():
    """Returns system status, record counts, label count, and available dates."""
    data = get_service().health()
    data["version"] = pkg_version("commute-tracker")
    data["git_commit"] = os.environ.get("GIT_COMMIT", "")
    data["timezone"] = TIMEZONE
    return data
```

Add the import at the top of the file (near the other imports):

```python
from src.config import TIMEZONE
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_audit_fixes.py::test_health_includes_timezone -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/api/routes.py tests/test_audit_fixes.py
git commit -m "feat(api): include timezone in health endpoint

Partial fix for #11"
```

---

### Task 10: Dashboard timezone display

**Files:**
- Modify: `src/dashboard/pages/1_Daily_Commute.py:138-182`
- Modify: `src/dashboard/pages/2_Segment_Analysis.py` (no timestamp display changes needed — uses dates from commute_id strings)
- Modify: `src/dashboard/pages/3_Departure_Optimizer.py:22-27`
- Modify: `src/dashboard/pages/4_Trends.py:18-21`
- Modify: `src/dashboard/pages/5_Label_Commute.py:173-181`

All dashboard pages that display timestamps need to convert UTC to the browser's timezone. The API now returns tz-aware UTC timestamps. Polars parses these as `Datetime[us, UTC]`.

- [ ] **Step 1: Verify st.context.timezone availability**

Check Streamlit docs using context7 to confirm `st.context.timezone` is available in Streamlit >= 1.41. If not available, use an alternative approach.

- [ ] **Step 2: Update 1_Daily_Commute.py**

In `src/dashboard/pages/1_Daily_Commute.py`, add display timezone detection at the top (after imports, before the date selector):

```python
from src.dashboard.api_client import list_dates, get_daily_summary, get_segments

# Detect browser timezone for display
try:
    display_tz = st.context.timezone
except (AttributeError, KeyError):
    from src.config import TIMEZONE

    display_tz = TIMEZONE
```

Then update the speed chart section (lines 138-182). Change the chart data preparation to convert timestamps:

```python
if "speed_kmh" in day_df.columns and "timestamp" in day_df.columns:
    import altair as alt

    # Convert UTC to display timezone
    day_df = day_df.with_columns(
        pl.col("timestamp").dt.convert_time_zone(display_tz).alias("display_time"),
    )
    chart_df = day_df.select(["display_time", "speed_kmh"]).to_pandas()

    if has_commutes:
        chart_df_full = day_df.select(
            ["display_time", "speed_kmh", "transport_mode", "commute_id"]
        ).to_pandas()
        chart_df_full["in_commute"] = chart_df_full["commute_id"].notna()

        chart = (
            alt.Chart(chart_df_full)
            .mark_line(strokeWidth=1.5)
            .encode(
                x=alt.X("display_time:T", title="Time"),
                y=alt.Y("speed_kmh:Q", title="Speed (km/h)"),
                ...  # rest unchanged
```

Replace all `timestamp:T` references with `display_time:T` in the chart encodings.

- [ ] **Step 3: Update 3_Departure_Optimizer.py**

Add timezone detection after imports:

```python
try:
    display_tz = st.context.timezone
except (AttributeError, KeyError):
    from src.config import TIMEZONE

    display_tz = TIMEZONE
```

Change lines 22-27 to convert `start_time` before extracting hour/minute/weekday:

```python
# Convert to display timezone before extracting time components
commutes = commutes.with_columns(
    pl.col("start_time").dt.convert_time_zone(display_tz).alias("start_time_local"),
)
commutes = commutes.with_columns(
    pl.col("start_time_local").dt.hour().alias("departure_hour"),
    pl.col("start_time_local").dt.minute().alias("departure_minute"),
    pl.col("start_time_local").dt.weekday().alias("day_of_week"),
    pl.col("start_time_local").dt.date().cast(pl.Utf8).alias("date"),
)
```

- [ ] **Step 4: Update 4_Trends.py**

Add timezone detection after imports. Change lines 18-21:

```python
commutes = commutes.with_columns(
    pl.col("start_time").dt.convert_time_zone(display_tz).alias("start_time_local"),
)
commutes = commutes.with_columns(
    pl.col("start_time_local").dt.date().alias("date"),
    pl.col("start_time_local").dt.weekday().alias("day_of_week"),
)
```

Also update the chart on line 32-38 to use `start_time_local` for the X axis:

```python
time_df = commutes.sort("start_time").select(
    [
        "start_time_local",
        "duration_min",
        "commute_direction",
        "date",
    ]
)
```

And update the Altair encodings to use `start_time_local:T`.

- [ ] **Step 5: Update 5_Label_Commute.py**

Add timezone detection after imports. In the speed timeline section (lines 173-181), convert timestamps:

```python
if "speed_kmh" in points.columns and "timestamp" in points.columns:
    points = points.with_columns(
        pl.col("timestamp").dt.convert_time_zone(display_tz).alias("display_time"),
    )
    chart_data = points.select(
        [
            "display_time",
            "speed_kmh",
            "transport_mode",
            "segment_id",
        ]
    ).to_pandas()
```

Update the Altair encodings to use `display_time:T`.

Also update the segment band start/end times (lines 189-199):

```python
    for sid in chart_data["segment_id"].unique():
        seg_rows = chart_data[chart_data["segment_id"] == sid]
        seg_bands.append(
            {
                "start": seg_rows["display_time"].min(),
                "end": seg_rows["display_time"].max(),
                "mode": seg_rows["transport_mode"].iloc[0],
                "segment_id": int(sid),
            }
        )
```

- [ ] **Step 6: Run lint and format**

Run: `ruff format src/dashboard/ && ruff check src/dashboard/`

Expected: all clean

- [ ] **Step 7: Commit**

```bash
git add src/dashboard/pages/
git commit -m "feat(dashboard): display timestamps in browser timezone

All dashboard charts and time extractions now convert UTC timestamps to
the browser's timezone via st.context.timezone. Falls back to TIMEZONE
config when browser detection is unavailable.

Partial fix for #11"
```

---

### Task 11: Final verification and integration test

**Files:**
- Modify: `tests/test_audit_fixes.py` (add integration test)

- [ ] **Step 1: Write the integration test**

Add to `tests/test_audit_fixes.py`:

```python
def test_full_pipeline_timezone_integration(db, derived_dir):
    """End-to-end: pipeline produces tz-aware data with correct local dates.

    Verifies the complete flow: raw records -> enrichment (timezone resolution)
    -> commute detection (local date IDs) -> Parquet (local date filenames)
    -> DuckDB queries (local date filtering).
    See: https://github.com/jflammia/commuteTracker/issues/11
    """
    from pathlib import Path

    # Seed a commute at 2024-03-26 20:00 EDT = 2024-03-27 00:00 UTC
    # All points are on UTC March 27, but local March 26
    base_tst = 1711497600  # 2024-03-27 00:00 UTC = 2024-03-26 20:00 EDT

    # At home
    for i in range(3):
        _insert_location(db, HOME[0], HOME[1], base_tst + i * 10)
    # Transit
    for i in range(1, 11):
        frac = i / 10
        lat = HOME[0] + (WORK[0] - HOME[0]) * frac
        lon = HOME[1] + (WORK[1] - HOME[1]) * frac
        _insert_location(db, lat, lon, base_tst + 30 + i * 10)
    # At work
    for i in range(3):
        _insert_location(db, WORK[0], WORK[1], base_tst + 140 + i * 10)

    results = _rebuild_all(db, derived_dir)

    # 1. Parquet file should be named for local date (March 26)
    files = list(Path(derived_dir).rglob("*.parquet"))
    assert any("2024-03-26" in f.name for f in files), (
        f"Expected Parquet file for 2024-03-26 (local), got: {[f.name for f in files]}"
    )

    # 2. Commute ID should use local date
    if results["commutes_found"] > 0:
        store = DerivedStore(derived_dir)
        commutes = store.get_commutes()
        if not commutes.is_empty():
            cid = commutes["commute_id"][0]
            assert "2024-03-26" in cid, f"Commute ID should use local date: {cid}"

    # 3. Daily summary for local date should return data
    store = DerivedStore(derived_dir)
    summary = store.get_daily_summary("2024-03-26")
    assert not summary.is_empty(), "Daily summary for local date should have data"

    # 4. Timestamps should be tz-aware UTC
    assert summary["timestamp"].dtype == pl.Datetime("us", "UTC")

    # 5. timezone column should be present
    assert "timezone" in summary.columns
    assert "timestamp_local" in summary.columns
```

- [ ] **Step 2: Run the integration test**

Run: `pytest tests/test_audit_fixes.py::test_full_pipeline_timezone_integration -v`

Expected: PASS

- [ ] **Step 3: Run the complete test suite**

Run: `pytest --tb=short -q`

Expected: all tests PASS

- [ ] **Step 4: Run lint and format on everything**

Run: `ruff format src/ tests/ && ruff check src/ tests/`

Expected: all clean

- [ ] **Step 5: Update test_audit_fixes.py docstring**

Update the module docstring at the top of `tests/test_audit_fixes.py` to include the new fixes:

```python
"""Regression tests for audit fixes.

Covers:
1. MCP server mounting and initialization
2. Pipeline date filters using GPS tst (not received_at)
3. Pipeline robustness with non-location and malformed records
4. SQL injection prevention in derived_store (parameterized queries)
5. SQL sandboxing in query_derived (SELECT-only)
6. Batch segments endpoint
7. Bulk labels accepting both formats
8. Vectorized day-of-week replace (map_elements removed)
9. MCP server works behind reverse proxy (no Host header rejection)
10. .mcp.json uses correct transport type for Claude Code (#5)
11. Container data paths resolve to writable volume (#6)
12. Home geofence radius default 50m (#7)
13. Timezone-aware timestamps with GPS-derived timezone (#11)
"""
```

- [ ] **Step 6: Final commit**

```bash
git add tests/test_audit_fixes.py src/
git commit -m "fix: timezone-aware timestamps with GPS-derived timezone

All timestamps stored as timezone-aware UTC. Per-point timezone resolved
from GPS coordinates via timezonefinder. Local timestamps used for date
grouping, commute IDs, and Parquet file naming. Dashboard displays times
in browser timezone.

Fixes #11"
```

- [ ] **Step 7: Push and verify**

```bash
git pull
git push
gh run list --limit 2
```

Expected: CI kicks off and passes. Issue #11 auto-closes.

- [ ] **Step 8: Verify issue closed**

Run: `gh issue view 11`

Expected: Status shows CLOSED
