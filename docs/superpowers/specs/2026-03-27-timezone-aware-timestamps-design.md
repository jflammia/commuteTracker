# Timezone-Aware Timestamps Design

**Issue:** [#11 — Store timestamps with capture timezone; display in browser-local time](https://github.com/jflammia/commuteTracker/issues/11)
**Date:** 2026-03-27
**Status:** Approved

## Problem

All timestamps in the pipeline are timezone-naive UTC. OwnTracks sends Unix epoch (`tst`), and the enricher converts it to a naive datetime. This causes:

1. **Wrong dates for late-night activity** — A drive at 11 PM EDT (03:00 UTC next day) lands in the next day's Parquet file
2. **Misleading chart times** — Speed Over Time chart shows UTC, not local time
3. **Wrong commute IDs** — IDs like `2026-03-27-evening` use UTC dates, which can mismatch the user's actual local date

## Approach

**GPS-derived timezone per point** using `timezonefinder` with a cached instance. Each enriched point gets its IANA timezone resolved from its lat/lon coordinates. This handles travel, DST transitions, and cross-timezone commutes automatically.

## Design

### 1. Timezone Resolver (`src/processing/tz_resolver.py`)

New module with a cached `TimezoneFinder` instance:

```python
from functools import lru_cache
from timezonefinder import TimezoneFinder
from src.config import TIMEZONE

@lru_cache(maxsize=1)
def _finder() -> TimezoneFinder:
    return TimezoneFinder()

def resolve_timezone(lat: float, lon: float) -> str:
    return _finder().timezone_at(lng=lon, lat=lat) or TIMEZONE
```

The `TimezoneFinder` instance is expensive to create (~50ms) but cheap to query. The `lru_cache(maxsize=1)` ensures a single instance is reused across the entire process lifetime. An additional LRU cache on rounded (lat, lon) pairs skips redundant lookups for clustered GPS points.

### 2. Config (`src/config.py`)

```python
TIMEZONE = os.environ.get("TIMEZONE", "America/New_York")
```

Used only as a fallback when `timezonefinder` returns `None` (ocean, Antarctica, etc.) and for interpreting user-facing date filters (`since`/`until`).

### 3. Enricher (`src/processing/enricher.py`)

After the existing enrichment, add three operations:

1. **Resolve timezone** per point from lat/lon → `timezone` column (Utf8, IANA string)
2. **Make timestamp tz-aware** — `pl.from_epoch("tst").dt.replace_time_zone("UTC")` → `timestamp` becomes `Datetime[us, UTC]`
3. **Compute local time** — `pl.col("timestamp").dt.convert_time_zone(tz)` → `timestamp_local` column

Since a Polars Datetime column can only carry one timezone in its dtype, `timestamp_local` is stored as a **naive datetime** (timezone stripped after conversion). The conversion flow: group by `timezone` column, convert each group's UTC timestamp to local time via `dt.convert_time_zone(tz)`, then strip the timezone with `dt.replace_time_zone(None)` before recombining. The `timezone` Utf8 column preserves the IANA string for downstream consumers that need it. For the common case (all points in one timezone), this is a single operation.

### 4. Pipeline Date Grouping (`src/processing/pipeline.py`)

Replace naive date extraction:

```python
# Before
pl.col("timestamp").dt.date().alias("date")

# After
pl.col("timestamp_local").dt.date().alias("date")
```

Parquet files are named by local date. A 11 PM EDT drive lands in today's file.

### 5. Pipeline Date Filters (`src/processing/pipeline.py`)

Interpret `since`/`until` as local-timezone dates using the `TIMEZONE` config:

```python
from zoneinfo import ZoneInfo
tz = ZoneInfo(TIMEZONE)
since_dt = datetime.strptime(filters["since"], "%Y-%m-%d").replace(tzinfo=tz)
since_tst = int(since_dt.timestamp())
```

This converts local midnight to the correct UTC epoch for filtering.

### 6. Commute Detector (`src/processing/commute_detector.py`)

Use `timestamp_local` for commute ID date strings:

```python
local_timestamps = df["timestamp_local"].to_list()
date_str = local_timestamps[commute_start_idx].strftime("%Y-%m-%d")
cid = f"{date_str}-{direction}"
```

The detector reads `timestamp_local` from the DataFrame (added by enricher) instead of the UTC `timestamp` for date extraction. All other logic (geofencing, state machine) continues using positional data unchanged.

### 7. `process_jsonl` Path (`src/processing/pipeline.py`)

Currently derives the output filename from the input JSONL filename. Change to follow the same date-grouping logic as `process_from_db` — group by `timestamp_local` date and write per-day Parquet files. This fixes the case where a single JSONL file spans midnight in local time.

### 8. Derived Store (`src/storage/derived_store.py`)

`get_daily_summary` changes from:

```sql
WHERE CAST(timestamp AS DATE) = CAST($1 AS DATE)
```

To:

```sql
WHERE CAST(timestamp_local AS DATE) = CAST($1 AS DATE)
```

All other queries use `MIN(timestamp)`, `MAX(timestamp)` for ordering and duration math — these work correctly on tz-aware UTC since they compare absolute instants. No changes needed.

### 9. API Layer

**Health endpoint**: Add `timezone` (fallback config value) to the response.

**Serialization**: `.isoformat()` on tz-aware UTC datetimes produces strings with `+00:00` suffix. The dashboard's `str.to_datetime(strict=False)` in Polars preserves timezone info from ISO strings. No changes needed in `service.py`.

### 10. Dashboard Display

**Browser timezone detection** via `st.context.timezone` (Streamlit 1.36+):

```python
display_tz = st.context.timezone  # e.g. "America/New_York"
```

Falls back to `TIMEZONE` config if unavailable.

**Timestamp conversion** in each dashboard page before rendering:

```python
chart_df = day_df.with_columns(
    pl.col("timestamp").dt.convert_time_zone(display_tz).alias("display_time"),
)
```

**Pages affected**: All 5 dashboard pages (Daily Commute, Segment Analysis, Departure Optimizer, Trends, Label Commute). Each gets the same pattern: detect browser tz, convert before display.

**Departure Optimizer**: Converts `start_time` to browser timezone before extracting `.dt.hour()`:

```python
commutes = commutes.with_columns(
    pl.col("start_time").dt.convert_time_zone(display_tz).dt.hour().alias("departure_hour"),
)
```

**Date picker**: The date list comes from Parquet filenames, which are now named by local date. No change needed.

**No new dependencies** for the dashboard — `st.context.timezone` is built-in, `zoneinfo` is stdlib.

### 11. Backfill & Migration

**No schema migration needed.** The existing `rebuild_derived` pipeline reprocesses all raw data from the database:

```bash
python scripts/rebuild_derived.py --clean
```

The enricher computes `timezone` and `timestamp_local` from existing `lat`/`lon` columns. The pipeline groups by local date and writes new Parquet files. Commute IDs are regenerated with local dates.

**New Parquet columns:**

| Column | Type | Description |
|--------|------|-------------|
| `timestamp` | `Datetime[us, UTC]` | Was naive, now tz-aware UTC |
| `timezone` | `Utf8` | IANA timezone string (new) |
| `timestamp_local` | `Datetime[us]` (naive) | Local time for grouping (new, tz stripped after conversion) |

**Commute ID changes**: Some commutes near midnight UTC will get different IDs after backfill. Existing labels keyed to old commute IDs may become orphaned.

**Label migration**: After rebuild, check each existing label — if its `commute_id` no longer exists in Parquet data, attempt to match to a new commute ID by `(date +/- 1 day, direction, segment_id)`. Log unmatched labels.

### 12. New Dependency

```toml
# pyproject.toml
dependencies = [
    ...
    "timezonefinder>=6.0",
]
```

`timezonefinder` is pure Python, ~40MB installed (ships timezone boundary data). No native compilation needed.

## Testing Strategy

### Regression tests (`tests/test_audit_fixes.py`)

1. Enricher produces tz-aware UTC `timestamp` column (`Datetime[us, UTC]`)
2. Enricher adds `timezone` column with correct IANA string for known coordinates
3. Enricher adds `timestamp_local` matching expected local time
4. Commute IDs use local date — 23:30 UTC in `America/New_York` (19:30 EDT) gets today's date
5. Parquet files named by local date for same scenario
6. Pipeline filters interpret dates as local timezone — `since=2026-03-27` in EDT filters from `2026-03-27T04:00:00Z`
7. DuckDB daily summary uses local date — query returns points spanning UTC midnight
8. Timezone fallback — ocean coordinates use `TIMEZONE` config
9. `tz_resolver` caching — repeated calls reuse cached instance

### Unit tests for `tz_resolver.py`

- Known coordinates return correct timezone
- Null/ocean coordinates return fallback
- Cache works (same `TimezoneFinder` instance reused)

### Existing tests

The full test suite (220 tests) must continue passing. Tests creating mock data with naive timestamps need updating to use tz-aware UTC — mechanical change, not logic change.

## Acceptance Criteria

- [ ] All stored timestamps are timezone-aware UTC (`Datetime[us, UTC]`)
- [ ] Each point has a `timezone` column derived from GPS coordinates
- [ ] `TIMEZONE` config is fallback only (ocean/null coordinates)
- [ ] Parquet files named by local date (late-night drive in today's file)
- [ ] Commute IDs use local dates
- [ ] `since`/`until` filters interpreted as local timezone
- [ ] Dashboard detects browser timezone via `st.context.timezone`
- [ ] All charts show local time on X axis
- [ ] Existing data backfilled via `rebuild --clean`
- [ ] Orphaned labels matched to new commute IDs where possible
- [ ] No breaking changes to MCP tool queries
- [ ] All regression tests pass
