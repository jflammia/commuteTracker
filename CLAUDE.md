# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Self-hosted GPS commute tracker that receives OwnTracks location data, detects commutes via geofencing, classifies transport modes (walk/drive/train/waiting/stationary), and surfaces analytics through a REST API, MCP server, and Streamlit dashboard. Designed for homelab/Proxmox deployment with durability-first design: raw data is immutable/append-only, all derived data (Parquet) is rebuildable from raw.

## Commands

```bash
# Install
pip install -e ".[dev]"              # Dev dependencies (includes pytest, ruff, scikit-learn)
pip install -e ".[ml]"               # ML dependencies only

# Test
pytest                                # All tests
pytest tests/test_api_service.py      # Single file
pytest tests/test_api_service.py::test_list_commutes -v  # Single test
pytest -k "commute_detector"          # Pattern match

# Lint
ruff check src/ tests/               # Check style (line-length=100)
ruff format --check src/ tests/      # Check formatting
ruff format src/ tests/              # Auto-format

# Run locally
uvicorn src.receiver.app:app --host 0.0.0.0 --port 8080  # Receiver + API + MCP
streamlit run src/dashboard/app.py                         # Dashboard (needs receiver running)

# Docker
docker compose up -d                  # Start receiver (8080) + dashboard (8501)
docker compose down

# Data pipeline
python scripts/rebuild_derived.py                          # Rebuild all Parquet from raw
python scripts/rebuild_derived.py --since 2026-03-01 --until 2026-03-15
python scripts/rebuild_derived.py --date 2026-03-26 --clean --dry-run
```

## Architecture

**Data flow:** OwnTracks HTTP POST -> FastAPI receiver (`/pub`, always returns 200) -> SQLite/PostgreSQL -> Processing pipeline (Polars) -> Parquet files -> Dashboard/API/MCP

**Key layers:**
- `src/receiver/` - FastAPI app with OwnTracks endpoint and optional Recorder passthrough
- `src/api/` - REST API (routes.py defines endpoints, service.py has business logic)
- `src/processing/` - Pipeline: enricher (speed/distance/acceleration) -> commute_detector (geofences) -> segmenter (mode change boundaries) -> classifiers/ (ensemble: speed, variance, corridor, waypoint)
- `src/storage/` - database.py (SQLAlchemy ORM), raw_store.py (JSONL), derived_store.py (Parquet+DuckDB), label_store.py (user corrections), s3_sync.py (backup)
- `src/ml/` - scikit-learn Decision Tree trained on user label corrections
- `src/dashboard/` - Streamlit app with 6 pages, communicates with backend via REST API client (api_client.py)
- `src/mcp_server.py` - MCP server (Streamable HTTP) mounted on the FastAPI app
- `src/config.py` - All configuration via environment variables

**Storage design:** SQLite is default (WAL mode in Docker). Raw JSONL is the source of truth. Parquet is derived/rebuildable. S3 backup is optional.

**Classifier ensemble:** Multiple classifiers (speed, speed_variance, corridor, waypoint) each vote; ensemble.py aggregates. All inherit from `BaseClassifier`.

**MCP server:** Mounted as a sub-app at `/mcp`. FastAPI does not propagate lifespan events to mounted sub-apps, so the MCP session manager is started manually in the parent lifespan (`async with mcp_session_mgr.run():`). If you change the MCP mounting code, preserve this pattern or the MCP endpoint will crash with "Task group is not initialized."

## CI/CD

GitHub Actions: `ci.yml` runs lint + test + docker build on push/PR. `lint-pr.yml` enforces conventional commits in PR titles. `release-please.yml` auto-creates a Release PR with changelog + version bump on each push to main. Merging the Release PR triggers `release.yml` which builds multi-arch (amd64+arm64) Docker image to GHCR. Use conventional commit prefixes in PR titles (`feat:`, `fix:`, `docs:`, etc.).

## Commits & PRs

Use the `commit-pr` skill (`.claude/skills/commit-pr/SKILL.md`) for all commits and PRs. Key rules:
- **Conventional commits required**: `feat:`, `fix:`, `docs:`, `ci:`, `chore:`, `refactor:`, `test:`, `perf:`, `build:`, `style:`, `revert:`
- **Never add AI attribution trailers** — no `Co-Authored-By: Claude`, `Signed-off-by`, or similar. Commits should look like any human-written commit.
- **Squash merge** feature/fix PRs (PR title becomes the conventional commit release-please parses)
- **Regular merge** release-please Release PRs (not squash — release-please needs its own commits intact)

## MCP Server

The receiver hosts an MCP server at `/mcp` (Streamable HTTP, stateless, JSON). When the receiver is running locally, Claude Code auto-connects via `.mcp.json` in the repo root. This gives Claude direct access to commute data, label intelligence, and processing tools without needing the REST API.

Start the server first: `uvicorn src.receiver.app:app --host 0.0.0.0 --port 8080`

See `docs/mcp-integration.md` for the full tool/resource reference and LLM labeling workflows.

## Key Conventions

- Receiver **must always return 200** to OwnTracks (the app retries/backs off on non-200)
- All env vars loaded in `src/config.py` with sensible defaults; no dotenv library
- Geofences (HOME_LAT/LON/RADIUS_M, WORK_LAT/LON/RADIUS_M) must be set for commute detection
- ruff line-length is 100
- Python >= 3.11 required
