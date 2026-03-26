# Commute Tracker

Collect GPS/time data during daily commutes, segment into legs (walk, drive, train), and analyze via dashboards. Designed for durability-first: raw data is immutable and everything else is re-derivable.

## Quick Start

```bash
# Install dependencies
pip install -e ".[dev]"

# Run the receiver + API + MCP server
uvicorn src.receiver.app:app --host 0.0.0.0 --port 8080

# Run the dashboard
streamlit run src/dashboard/app.py
```

With Docker:

```bash
docker compose up        # Starts receiver (port 8080) + dashboard (port 8501)
```

## Architecture

```
OwnTracks (iPhone)
       |  HTTP POST
       v
FastAPI Server (:8080)
  ├── POST /pub          OwnTracks receiver (always returns 200)
  ├── /api/v1/*          REST API (Swagger UI at /docs)
  └── /mcp               MCP server (Streamable HTTP for LLM integration)
       |
       v
  SQLite/PostgreSQL  -->  Processing Pipeline  -->  derived/*.parquet
       |                                                  |
       v                                                  v
  S3 backup (optional)                        Streamlit Dashboard (:8501)
                                              Jupyter / ML / LLM agents
```

## Interfaces

| Interface | URL | Purpose |
|-----------|-----|---------|
| OwnTracks receiver | `POST /pub` | GPS data collection from phone |
| REST API | `/api/v1/*` | Programmatic access (24 endpoints) |
| OpenAPI docs | `/docs` | Interactive Swagger UI |
| MCP server | `/mcp` | LLM integration (11 tools, 12 resources, 4 prompts) |
| Dashboard | `:8501` | Streamlit analytics UI |

## Data

- `raw/` — Immutable JSONL files, one per day. **Not in git.** Backed up to NAS + cloud.
- `derived/` — Parquet files rebuilt from raw. **Not in git.**

## Docs

- [API Reference](docs/api-reference.md) — REST API endpoints, request/response examples
- [MCP Integration](docs/mcp-integration.md) — LLM integration guide, tools, resources, workflows
- [Project Plan](docs/project_plan.md) — Full architecture, phases, and decisions
- [OwnTracks Setup](docs/setup_owntracks.md) — Phone configuration guide
- [ADR-001: Storage](docs/decisions/adr-001-storage-architecture.md) — SQLite + S3 design
- [ADR-002: Classifier](docs/decisions/adr-002-classifier-architecture.md) — Ensemble classifier design
