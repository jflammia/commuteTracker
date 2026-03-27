# Local Development Guide

How to run Commute Tracker on your machine for development and testing.

## Prerequisites

- Python 3.11+
- pip
- Docker and Docker Compose (for containerized setup)

## Option 1: Run Natively

### Install dependencies

```bash
pip install -e ".[dev]"
```

This installs the project in editable mode with development dependencies (pytest, ruff, scikit-learn, jupyter, matplotlib).

### Configure environment

Copy the example env file and fill in your geofence coordinates:

```bash
cp .env.example .env
```

Edit `.env` — the key values to set for local development:

```bash
# Use 3 slashes for local SQLite (relative path)
DATABASE_URL=sqlite:///data/commute_tracker.db

# Your home and work coordinates (required for commute detection)
HOME_LAT=40.7128
HOME_LON=-74.0060
HOME_RADIUS_M=150

WORK_LAT=40.7580
WORK_LON=-73.9855
WORK_RADIUS_M=150
```

All other settings have sensible defaults. S3 and Recorder passthrough are disabled when their URLs are empty.

### Start the receiver

```bash
uvicorn src.receiver.app:app --host 0.0.0.0 --port 8080 --reload
```

The `--reload` flag watches for file changes and restarts automatically. This starts:

- **OwnTracks receiver** at `POST /pub`
- **REST API** at `/api/v1/*`
- **Swagger UI** at `/docs`
- **MCP server** at `/mcp`
- **Health check** at `/health`

### Start the dashboard

In a second terminal:

```bash
streamlit run src/dashboard/app.py
```

Opens at http://localhost:8501. The dashboard calls the REST API at `http://localhost:8080/api/v1` by default. Override with `COMMUTE_API_URL` if the receiver is running elsewhere.

### Verify it works

```bash
# Health check
curl http://localhost:8080/health

# Send a test location payload
curl -X POST http://localhost:8080/pub \
  -H "Content-Type: application/json" \
  -H "X-Limit-U: testuser" \
  -H "X-Limit-D: phone" \
  -d '{"_type": "location", "lat": 40.7128, "lon": -74.0060, "tst": 1711500000, "acc": 10}'

# List commutes via the API
curl http://localhost:8080/api/v1/commutes
```

### Run tests

```bash
pytest                                          # All tests
pytest tests/test_receiver.py                   # Single file
pytest tests/test_api_service.py::test_list_commutes -v  # Single test
pytest -k "classifier"                          # Pattern match
```

### Lint and format

```bash
ruff check src/ tests/        # Check for issues
ruff format src/ tests/       # Auto-format (line-length 100)
```

### Seed dev data

Populate the database with synthetic commute data so you can exercise all features without a real OwnTracks device:

```bash
python scripts/seed_dev_data.py              # 20 weekdays (~40 commutes)
python scripts/seed_dev_data.py --days 5     # Quick 1-week seed
python scripts/seed_dev_data.py --days 60    # Larger dataset
python scripts/seed_dev_data.py --clean      # Wipe existing data first
python scripts/seed_dev_data.py --force      # Skip confirmation prompts
```

The script generates realistic multi-modal NJ-to-Manhattan commutes (drive, walk, wait, train, walk), inserts GPS records into the database, runs the processing pipeline to produce Parquet files, and adds sample label corrections. It also prints the geofence coordinates you need in `.env`.

Data is reproducible by default (`--seed 42`). Use a different `--seed` value for a different dataset.

### Process data

Once you have location records in the database, generate derived Parquet files:

```bash
python scripts/rebuild_derived.py
python scripts/rebuild_derived.py --date 2026-03-26          # Single day
python scripts/rebuild_derived.py --since 2026-03-01 --until 2026-03-15  # Date range
python scripts/rebuild_derived.py --clean --dry-run          # Preview a clean rebuild
```

### Optional: waypoints and corridors

Copy and edit `zones.json.example` to improve transport mode classification for known locations (train stations, bus stops) and transit routes:

```bash
cp zones.json.example zones.json
# Edit zones.json with your actual waypoints and corridor coordinates
```

---

## Option 2: Run with Docker Compose

### Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your geofence coordinates. The Docker default `DATABASE_URL` uses the `/data` volume:

```bash
DATABASE_URL=sqlite:////data/commute_tracker.db
```

> Note the four slashes — three for SQLite's URI prefix plus one for the absolute `/data` path inside the container.

### Start services

```bash
docker compose up -d
```

This starts two containers:

| Service | Container | Port | Description |
|---------|-----------|------|-------------|
| `receiver` | `commute-receiver` | 8080 | FastAPI receiver + REST API + MCP server |
| `dashboard` | `commute-dashboard` | 8501 | Streamlit dashboard |

The dashboard waits for the receiver's health check to pass before starting.

### View logs

```bash
docker compose logs -f              # All services
docker compose logs -f receiver     # Receiver only
docker compose logs -f dashboard    # Dashboard only
```

### Verify

```bash
curl http://localhost:8080/health
open http://localhost:8501           # Dashboard
open http://localhost:8080/docs      # Swagger UI
```

### Rebuild after code changes

```bash
docker compose up -d --build
```

### Stop services

```bash
docker compose down                 # Stop containers, keep data volume
docker compose down -v              # Stop containers AND delete data volume
```

### Data storage

By default, both services share a Docker volume named `commute-data` mounted at `/data`. To store data on a NAS or host path instead, edit `docker-compose.yml`:

```yaml
volumes:
  - /mnt/nas/commute-tracker:/data   # Replace the commute-data volume line
```

---

## Project Layout

```
src/
  receiver/app.py        Entry point — FastAPI server (receiver + API + MCP)
  api/                   REST API routes and service layer
  processing/            Data pipeline: enrich -> detect -> segment -> classify
  storage/               Database, raw JSONL, derived Parquet, S3 sync
  ml/                    ML classifier training
  dashboard/             Streamlit UI (communicates via REST API)
  mcp_server.py          MCP server for LLM integration
  config.py              All environment-based configuration
scripts/                 CLI utilities (rebuild, plot, train, ingest)
tests/                   pytest test suite
```
