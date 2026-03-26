# Commute Tracker - Project Plan

## Context

New project in an empty repo. The goal is to collect GPS/time data during daily commutes, segment them into legs (walk, train, bus, etc.), build dashboards for analysis, and eventually support ML workloads. The overriding principle: **raw data durability is paramount** -- everything else is re-derivable. We follow a tracer bullet approach: get data flowing end-to-end first, then iterate.

---

## Architecture

```
[OwnTracks on iPhone]
       | HTTP POST (or manual export in Phase 0)
       v
[FastAPI Receiver]  -->  raw/YYYY/MM/YYYY-MM-DD.jsonl  (append-only, immutable)
                                  |
                                  v
                         [Processing Pipeline]  (Python/Polars)
                                  |
                                  v
                         derived/YYYY/MM/YYYY-MM-DD.parquet + DuckDB
                                  |
                                  v
                         [Streamlit Dashboard / Jupyter / ML]
```

---

## User-Specific Details

- **Phone:** iPhone (will use OwnTracks iOS)
- **Commute modes:** Walk + Drive + Train (need to distinguish all three)
- **Infrastructure:** Proxmox homelab cluster with NAS storage -- receiver will run as a container, raw data stored on NAS
- **Segmentation challenge:** Drive vs Train discrimination. Approaches: speed variance (train is smoother), known rail corridor geofences, stop duration patterns (train stops are predictable). Start simple, iterate with real data.

---

## Technology Choices

| Layer | Tech | Why |
|-------|------|-----|
| Collection | **OwnTracks** (iOS) | Open source, background GPS, HTTP POST mode, queues on-device if server down. User has iPhone. |
| Raw Storage | **JSONL files**, date-partitioned | Plain text = max durability, append-only, no binary corruption, schema-flexible |
| Processing | **Python + Polars** | Fast, modern DataFrame library for time-series |
| Analytics DB | **DuckDB** | Zero-infrastructure SQL over Parquet/JSON, perfect for local analytics |
| Dashboard | **Streamlit** | Python-native, fast to build, interactive |
| Exploration | **Jupyter notebooks** | Ad-hoc analysis and ML prototyping |
| Maps | **Folium** | Interactive maps from Python |
| Integrity | **SHA256 checksums** per day-file | Detect corruption |
| Hosting | **Proxmox homelab** | User's existing infra; receiver runs as container |
| Backup | **S3-compatible storage** (boto3, every 5 min) | Self-hosted or cloud; durable off-site copy of raw data |

---

## Raw Data Schema

Store the full OwnTracks JSON payload as-is, plus server metadata:

```json
{"_type":"location","tid":"ph","lat":51.5074,"lon":-0.1278,"alt":25,"acc":10,"vel":12,"tst":1711440000,"batt":85,"conn":"w","received_at":"2026-03-26T08:00:00.123Z","raw_source":"owntracks"}
```

Key fields: `lat`, `lon`, `alt`, `acc` (accuracy), `vel` (velocity km/h), `tst` (device unix timestamp), `batt`, `conn` (wifi/mobile). **Never discard fields from the source.**

---

## Derived Schema (Parquet)

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | datetime | Canonical timestamp |
| `lat`, `lon` | float | Coordinates |
| `speed_kmh` | float | Computed speed |
| `distance_from_prev_m` | float | Haversine distance |
| `commute_id` | string | e.g. `2026-03-26-morning` |
| `segment_id` | int | Leg of commute |
| `transport_mode` | string | walk / drive / train / stationary |

---

## Project Structure

```
commuteTracker/
  README.md
  pyproject.toml
  .gitignore
  .env.example
  raw/                          # IMMUTABLE, gitignored, backed up separately
  derived/                      # Rebuildable from raw/, gitignored
  src/
    receiver/app.py             # FastAPI OwnTracks endpoint
    receiver/config.py
    processing/pipeline.py      # Orchestrates raw -> derived
    processing/commute_detector.py
    processing/segmenter.py
    processing/enricher.py
    processing/geo_utils.py     # Haversine, geofencing
    storage/raw_store.py        # Append-only JSONL writer + checksums
    storage/derived_store.py    # Parquet + DuckDB access
    storage/integrity.py        # SHA256 verification
    dashboard/app.py            # Streamlit entry
    dashboard/pages/daily.py
    dashboard/pages/weekly.py
    dashboard/pages/historical.py
    ml/features.py              # Phase 4
    ml/dataset.py
    config.py                   # Home/work locations, thresholds
  scripts/
    ingest.py                   # Manual import (tracer bullet)
    plot_commute.py             # Quick visualization
    verify_integrity.py         # Check checksums
    rebuild_derived.py          # Re-derive everything from raw/
  notebooks/
    01_explore_raw_data.ipynb
    02_segmentation_prototype.ipynb
  tests/
    test_commute_detector.py
    test_segmenter.py
    test_geo_utils.py
    fixtures/sample_commute.jsonl
  docs/
    project_plan.md
    data_dictionary.md
    setup_owntracks.md
```

---

## Phased Implementation

### Phase 0: Tracer Bullet (first)
**Goal:** Data flows phone -> file -> chart. Manual, minimal, working.

1. Set up repo scaffolding: `pyproject.toml`, `.gitignore`, `README.md`, directory structure
2. Install OwnTracks on phone, configure 10-second tracking interval
3. Commute once, export data from OwnTracks as JSON
4. Save as `raw/2026/03/YYYY-MM-DD.jsonl`
5. Write `scripts/ingest.py` - loads JSONL into a Polars DataFrame
6. Write `scripts/plot_commute.py` - plots GPS trail on a Folium map + speed-over-time chart
7. Verify: see your commute on a map with timestamps

### Phase 1: Automated Collection
1. Build FastAPI receiver (`src/receiver/app.py`) - accepts OwnTracks HTTP POST, appends to daily JSONL
2. Add SHA256 checksum generation for completed day-files
3. Deploy receiver as a container on Proxmox homelab; store `raw/` on NAS
4. Configure OwnTracks on iPhone to POST to receiver (via Tailscale or local network)
5. Set up raw data backup (NAS snapshots + rclone to cloud for off-site)
6. Add daily health check: "did we receive data today?"

### Phase 2: Segmentation Engine
1. Define home/work geofences in `src/config.py`
2. Build commute detector: geofence departure -> arrival = one commute
3. Build segmenter: speed/stop patterns -> transport mode labels
4. Build processing pipeline: raw JSONL -> enriched Parquet
5. Store derived data in `derived/` as date-partitioned Parquet
6. Verify with DuckDB queries over derived data

### Phase 3: Dashboard
1. Build Streamlit app with daily/weekly/historical views
2. Daily: map with colored segments + timeline
3. Weekly: total time, mode breakdown, trends
4. Historical: duration over time, variability, best/worst days

### Phase 4: ML Preparation
1. Feature engineering: day-of-week, hour, weather, lag features
2. Training dataset export
3. Baseline model (commute time prediction)
4. Add predictions to dashboard

---

## Data Durability Strategy (Defense in Depth)

1. **OwnTracks queues on-device** if receiver is down - no data loss during outages
2. **Append-only JSONL** - receiver never modifies/deletes existing data
3. **SHA256 checksums** per day-file - detect any corruption
4. **Automated cloud backup** of `raw/` via rclone
5. **`rebuild_derived.py`** regenerates all derived data from raw - regularly tested
6. **Raw data never in git** - git is not suited for growing data files; backed up independently

---

## Key Risks

| Risk | Mitigation |
|------|------------|
| Data loss | 5-layer defense above |
| GPS gaps from phone power mgmt | Whitelist OwnTracks from battery optimization; handle gaps in segmentation |
| Receiver downtime | OwnTracks queues + retries; phone-local file as secondary |
| Segmentation accuracy | Start simple, iterate with real data; raw data allows re-segmentation |
| Scope creep | Tracer bullet forces end-to-end before polishing any layer |

---

## Verification Plan

- **Phase 0**: Visually confirm commute on Folium map; check DataFrame has expected columns and reasonable lat/lon values
- **Phase 1**: Commute with OwnTracks pointing at receiver; verify JSONL file grows; verify checksum matches
- **Phase 2**: Run pipeline on a day of data; inspect Parquet; query with DuckDB; confirm commute segments match reality
- **Phase 3**: Launch Streamlit; walk through each page with real data
- **All phases**: `pytest` on processing logic using fixture data in `tests/fixtures/`

---

## What to Build First (Implementation Session)

When implementation begins, start with Phase 0 scaffolding:
1. `pyproject.toml` with core dependencies
2. `.gitignore` (raw/, derived/, .env, __pycache__)
3. `README.md` with project overview
4. Directory structure with `__init__.py` files
5. `scripts/ingest.py` - load JSONL into DataFrame
6. `scripts/plot_commute.py` - map + speed chart
7. `src/storage/raw_store.py` - append-only JSONL writer
8. `tests/fixtures/sample_commute.jsonl` - synthetic test data
