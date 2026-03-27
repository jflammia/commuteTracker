<p align="center">
  <h1 align="center">Commute Tracker</h1>
  <p align="center">
    Self-hosted GPS commute analytics with automatic transport mode detection
    <br />
    <a href="docs/api-reference.md"><strong>API Reference</strong></a>
    &nbsp;&middot;&nbsp;
    <a href="docs/mcp-integration.md"><strong>MCP Integration</strong></a>
    &nbsp;&middot;&nbsp;
    <a href="docs/setup_owntracks.md"><strong>OwnTracks Setup</strong></a>
  </p>
</p>

<p align="center">
  <a href="https://github.com/jflammia/commuteTracker/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/jflammia/commuteTracker/actions/workflows/ci.yml/badge.svg" /></a>
  <a href="https://github.com/jflammia/commuteTracker/releases"><img alt="Release" src="https://img.shields.io/github/v/release/jflammia/commuteTracker?include_prereleases&sort=semver" /></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white" />
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green" />
  <a href="https://ghcr.io/jflammia/commutetracker"><img alt="Docker" src="https://img.shields.io/badge/ghcr.io-commutetracker-2496ED?logo=docker&logoColor=white" /></a>
  <img alt="MCP" src="https://img.shields.io/badge/MCP-enabled-8A2BE2" />
</p>

---

Commute Tracker collects GPS data from your phone via [OwnTracks](https://owntracks.org/), automatically segments commutes into transport modes (walking, driving, train, waiting), and provides dashboards and analytics to optimize your daily travel. Raw data is immutable and everything else is re-derivable.

<!-- Screenshots — replace these with your own once you have real data -->
<!--
<p align="center">
  <img src="docs/screenshots/dashboard-map.png" alt="Dashboard map view" width="48%" />
  &nbsp;
  <img src="docs/screenshots/departure-optimizer.png" alt="Departure optimizer" width="48%" />
</p>
<p align="center">
  <em>Left: Daily commute map with color-coded segments. Right: Departure time optimizer.</em>
</p>
-->

**Key design principles:**
- **Durability-first** — raw GPS data is append-only, backed up to S3, never modified
- **AI-first** — native MCP server for LLM integration alongside REST API
- **Self-hosted** — runs on your hardware, no cloud dependencies, no vendor lock-in
- **Progressive** — zero-config start, add geofences/labels/ML as you go

## Features

- **Automatic GPS collection** from OwnTracks (iOS/Android) via HTTP POST
- **Transport mode detection** with pluggable ensemble classifier (speed, variance, waypoints, corridors)
- **5 transport modes**: walking, driving, train, waiting, stationary
- **Interactive dashboard** with maps, speed timelines, segment analysis, departure optimizer, and trends
- **Segment labeling UI** for correcting classifications and building ML training data
- **Multi-level label intelligence** — LLMs can review and correct at segment, commute, or batch level
- **REST API** with 24 endpoints and interactive Swagger docs
- **MCP server** for LLM integration (11 tools, 12 resources, 4 prompts)
- **SQL queries** over processed data via DuckDB
- **ML pipeline** with decision tree classifier trained on your corrections
- **S3-compatible backup** with configurable local retention and pruning
- **OwnTracks Recorder passthrough** for parallel use with existing setups

## Quick Start

### Docker (recommended)

Using a pre-built image from GHCR:

```bash
docker pull ghcr.io/jflammia/commutetracker:latest
```

Or build from source:

```bash
git clone https://github.com/jflammia/commuteTracker.git
cd commuteTracker
cp .env.example .env
# Edit .env with your home/work coordinates

docker compose up -d
```

This starts:
- **Receiver + API + MCP** on port `8080`
- **Dashboard** on port `8501`

Point OwnTracks to `http://your-server:8080/pub` and you're collecting data.

### Local Development

```bash
pip install -e ".[dev]"
git config core.hooksPath .githooks   # Enable pre-commit lint checks

# Start the server (receiver + API + MCP)
uvicorn src.receiver.app:app --host 0.0.0.0 --port 8080

# In a second terminal, start the dashboard
streamlit run src/dashboard/app.py
```

### Verify It's Working

```bash
# Health check
curl http://localhost:8080/api/v1/health

# Swagger docs
open http://localhost:8080/docs

# Dashboard
open http://localhost:8501
```

## Architecture

```
OwnTracks (iOS/Android)
       |  HTTP POST
       v
FastAPI Server (:8080)
  ├── POST /pub            OwnTracks receiver (always returns 200)
  ├── GET  /api/v1/*       REST API (Swagger UI at /docs)
  ├── POST /api/v1/*       Label corrections, rebuild, ML training
  └── /mcp                 MCP server (Streamable HTTP)
       |
       v
  SQLite / PostgreSQL ──> Processing Pipeline ──> derived/*.parquet
       |                    (enrich, detect,        |
       v                     segment, classify)     v
  S3 backup (optional)                    Streamlit Dashboard (:8501)
                                          Jupyter / ML / LLM agents
```

### How It Works

1. **Collect** — OwnTracks sends GPS points every 10 seconds to the receiver
2. **Store** — Raw JSON payloads are written to SQLite (and optionally backed up to S3 as JSONL)
3. **Process** — The pipeline enriches points with speed/distance, detects commutes via geofences, segments by transport mode changes, and classifies each segment
4. **Analyze** — Dashboard, API, SQL, or MCP — pick your interface
5. **Improve** — Label corrections feed back into the classifier and ML model

## Configuration

Copy `.env.example` to `.env` and configure:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///data/commute_tracker.db` | SQLite or PostgreSQL connection string |
| `HOME_LAT`, `HOME_LON` | `0.0` | Home location for commute detection |
| `WORK_LAT`, `WORK_LON` | `0.0` | Work location for commute detection |
| `HOME_RADIUS_M` | `150` | Geofence radius for home (meters) |
| `WORK_RADIUS_M` | `150` | Geofence radius for work (meters) |
| `S3_ENDPOINT_URL` | — | S3-compatible storage for backup (optional) |
| `S3_BUCKET` | `commute-tracker-raw` | S3 bucket name |
| `S3_SYNC_INTERVAL_SECONDS` | `300` | How often to sync to S3 |
| `LOCAL_RETENTION_DAYS` | `90` | Prune synced records older than this (0 = keep all) |
| `RECORDER_URL` | — | Forward payloads to OwnTracks Recorder (optional) |
| `DERIVED_DATA_DIR` | `./derived` | Where to write processed Parquet files |

## Interfaces

### REST API

24 endpoints at `/api/v1/*` — interactive docs at [`/docs`](http://localhost:8080/docs).

```bash
# List commutes
curl http://localhost:8080/api/v1/commutes

# Get segments for a commute
curl http://localhost:8080/api/v1/commutes/2026-03-26-morning/segments

# Run a SQL query
curl -X POST http://localhost:8080/api/v1/query \
  -H 'Content-Type: application/json' \
  -d '{"sql": "SELECT commute_direction, avg(speed_kmh) FROM commute_data GROUP BY commute_direction"}'

# Review classifications with AI-powered mismatch detection
curl http://localhost:8080/api/v1/labels/review?n=5
```

See [API Reference](docs/api-reference.md) for all endpoints.

### MCP Server (LLM Integration)

Native [Model Context Protocol](https://modelcontextprotocol.io/) server at `/mcp` — Streamable HTTP, stateless, JSON responses. Connect Claude to your commute data with one config entry.

#### Claude Code

If you cloned the repo, it's already configured — `.mcp.json` in the repo root auto-connects when the server is running. Just start the server:

```bash
uvicorn src.receiver.app:app --host 0.0.0.0 --port 8080
```

To connect to a remote instance instead, edit `.mcp.json`:

```json
{
  "mcpServers": {
    "commute-tracker": {
      "type": "url",
      "url": "https://your-server/mcp/"
    }
  }
}
```

#### Claude Desktop

Open **Settings > Developer > Edit Config** and add:

```json
{
  "mcpServers": {
    "commute-tracker": {
      "type": "url",
      "url": "https://your-server:8080/mcp/"
    }
  }
}
```

Replace `your-server:8080` with your actual host. If you're running behind a reverse proxy (e.g., `https://commute.example.com`), use that URL with `/mcp/` appended.

#### What You Get

Once connected, Claude has direct access to:
- **12 resources** — read commutes, segments, points, stats, labels
- **11 tools** — query data, add labels, review classifications, rebuild, train ML
- **4 prompts** — analyze commute, optimize departure, review classifications, weekly report

Try asking Claude: *"Review my last 5 commutes and flag any misclassified segments"* — it will use the MCP tools automatically.

See [MCP Integration Guide](docs/mcp-integration.md) for the full tool reference and LLM labeling workflows.

### Dashboard

Six-page Streamlit dashboard at port `8501`:

1. **Daily Commute** — map with colored segments, speed timeline
2. **Segment Analysis** — per-leg performance over time, variability
3. **Departure Optimizer** — find the best time to leave
4. **Trends & Patterns** — weekly/monthly aggregates, mode split
5. **Label Commute** — interactive correction UI for segment classifications
6. **Rebuild** — re-process raw data with filters

## Data Storage

| Directory | Contents | In Git | Backed Up |
|-----------|----------|--------|-----------|
| `raw/` | Immutable JSONL files (one per day) | No | NAS + S3 |
| `derived/` | Parquet files (rebuilt from raw) | No | Not needed |
| SQLite DB | Raw records + labels | No | S3 sync |

**Durability guarantees:**
- OwnTracks queues data on-device if the server is unreachable
- The receiver always returns HTTP 200 (OwnTracks discards data on 4xx)
- Raw data is append-only and never modified
- S3 sync runs every 5 minutes with automatic retry
- All derived data can be rebuilt from raw at any time

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run linter
ruff check src/ tests/

# Run a single test file
pytest tests/test_api_service.py -v
```

### Project Structure

```
src/
  receiver/        # FastAPI OwnTracks endpoint
  api/             # REST API routes + service layer
  mcp_server.py    # MCP server for LLM integration
  processing/      # Pipeline: enricher, commute detector, segmenter, classifiers
  storage/         # Database, derived store, label store, S3 sync
  dashboard/       # Streamlit app + pages
  ml/              # ML model training and evaluation
  config.py        # Environment-based configuration
tests/             # 188 tests
docs/              # API reference, MCP guide, ADRs
```

## Documentation

| Document | Description |
|----------|-------------|
| [API Reference](docs/api-reference.md) | REST API endpoints, request/response examples |
| [MCP Integration](docs/mcp-integration.md) | LLM integration guide, tools, resources, workflows |
| [Releasing](docs/releasing.md) | Version scheme, release process, Docker tags |
| [OwnTracks Setup](docs/setup_owntracks.md) | Phone configuration guide |
| [Project Plan](docs/project_plan.md) | Architecture, phases, and decisions |
| [ADR-001: Storage](docs/decisions/adr-001-storage-architecture.md) | SQLite + S3 storage design |
| [ADR-002: Classifier](docs/decisions/adr-002-classifier-architecture.md) | Ensemble classifier architecture |

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Run tests (`pytest`) and linting (`ruff check src/ tests/`)
4. Commit your changes
5. Open a pull request

## License

MIT
