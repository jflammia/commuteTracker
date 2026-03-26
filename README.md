# Commute Tracker

Collect GPS/time data during daily commutes, segment into legs (walk, drive, train), and analyze via dashboards. Designed for durability-first: raw data is immutable and everything else is re-derivable.

## Quick Start

```bash
# Install dependencies
pip install -e ".[dev]"

# Manual import (Phase 0 - tracer bullet)
python scripts/ingest.py raw/2026/03/2026-03-26.jsonl

# Plot a commute on a map
python scripts/plot_commute.py raw/2026/03/2026-03-26.jsonl

# Run the receiver (Phase 1+)
uvicorn src.receiver.app:app --host 0.0.0.0 --port 8080

# Run the dashboard (Phase 3+)
streamlit run src/dashboard/app.py
```

## Architecture

```
OwnTracks (iPhone) --> FastAPI Receiver --> raw/*.jsonl (immutable)
                                               |
                                         Processing Pipeline
                                               |
                                         derived/*.parquet + DuckDB
                                               |
                                         Streamlit Dashboard / Jupyter / ML
```

## Data

- `raw/` - Immutable JSONL files, one per day. **Not in git.** Backed up to NAS + cloud.
- `derived/` - Parquet files rebuilt from raw. **Not in git.**

## Docs

- [Project Plan](docs/project_plan.md) - Full architecture, phases, and decisions
- [Data Dictionary](docs/data_dictionary.md) - Schema reference
- [OwnTracks Setup](docs/setup_owntracks.md) - Phone configuration guide
