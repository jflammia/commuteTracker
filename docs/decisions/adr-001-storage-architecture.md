# ADR-001: Storage Architecture — SQLite + S3 with Configurable Backends

**Date:** 2026-03-26
**Status:** Accepted

## Context

We need a storage design for raw GPS location data received from OwnTracks that:

1. Prioritizes **raw data durability** above all else
2. Works for a single-user homelab deployment out of the box
3. Can scale to PostgreSQL if needed
4. Is portable enough to open-source (no vendor/infra lock-in)
5. Supports durable off-site backup

## Decision

### Default configuration: SQLite (local) + S3-compatible storage (durable archive)

```
OwnTracks POST
      |
      v
FastAPI Receiver
      |
      ├──> Database (SQLite or PostgreSQL, immediate write)
      ├──> S3 as JSONL (background export, every 5 min)
      └──> OwnTracks Recorder /pub (optional passthrough, fire-and-forget)
```

### Storage backends supported

| Configuration | Database | S3 | Pruning | Use case |
|---|---|---|---|---|
| **SQLite + S3** (default) | SQLite WAL | Yes | Yes, after confirmed sync + retention window | Recommended. Best durability. |
| **SQLite only** | SQLite WAL | No | **No** — SQLite is the only copy | Simple setup, no S3 needed |
| **PostgreSQL + S3** | PostgreSQL | Yes | Yes, after confirmed sync + retention window | Multi-service, scaled deployments |
| **PostgreSQL only** | PostgreSQL | No | **No** — database is the only copy | Existing Postgres infra, no S3 |

### Key rules

1. **Database is the hot store.** All writes go here first, synchronously. Data is safe before we return HTTP 200 to OwnTracks.

2. **S3 is the durable archive.** Background task exports new rows to JSONL and uploads every 5 minutes. JSONL is the portable, inspectable, long-term format. Anyone can use these files without our code.

3. **Pruning only happens when S3 is configured.** If S3 is off, the database is the only copy and nothing is ever deleted. When S3 is on, rows confirmed synced and older than the retention window (default: 90 days) are pruned from the database.

4. **Recorder passthrough is optional and off by default.** If `RECORDER_URL` is set, every incoming payload is forwarded to the OwnTracks Recorder's `/pub` endpoint, fire-and-forget. This gives users the Recorder's web UI and reverse geocoding for free. If the Recorder is down, we don't care — our data is already persisted.

5. **Never return HTTP 4xx to OwnTracks.** The iOS app permanently discards data on 4xx responses. Our receiver always returns 200, even if processing fails.

### Database abstraction

We use **SQLAlchemy** for database access. The connection string determines the backend:

```
DATABASE_URL=sqlite:///data/commute_tracker.db     # SQLite (default)
DATABASE_URL=postgresql://user:pass@host/commute    # PostgreSQL
```

No backend-specific SQL. Same codebase, same schema, swap the URL.

### Pruning logic

```
Every S3 sync cycle (5 min):
  1. SELECT rows not yet synced, grouped by day
  2. Export to JSONL (one file per day)
  3. Upload JSONL to S3
  4. Mark rows as synced (s3_synced_at timestamp)
  5. DELETE rows WHERE s3_synced_at IS NOT NULL
                   AND created_at < (now - retention_window)
```

Pruning is disabled when:
- S3 is not configured (`S3_ENDPOINT_URL` is empty)
- Retention window is set to 0 (keep forever)

### Docker volume persistence

The SQLite database file lives on a **mounted Docker volume** (`/data/commute_tracker.db`), not inside the container. Container rebuilds, upgrades, and redeployments do not affect data.

## Configuration

```env
# Database (required)
DATABASE_URL=sqlite:///data/commute_tracker.db

# S3-compatible backup (optional, enables pruning)
S3_ENDPOINT_URL=http://minio.local:9000
S3_BUCKET=commute-tracker-raw
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
S3_SYNC_INTERVAL_SECONDS=300

# Pruning (only active when S3 is configured)
LOCAL_RETENTION_DAYS=90    # 0 = keep forever (no pruning)

# OwnTracks Recorder passthrough (optional, off by default)
RECORDER_URL=              # e.g. http://recorder:8083/pub
```

## Consequences

**Positive:**
- Raw data has two independent copies (database + S3) in the default config
- SQLite WAL provides crash safety without external dependencies
- PostgreSQL path available for scaling without code changes
- JSONL in S3 is the universal portable format — survives project abandonment
- Pruning keeps local storage bounded while S3 grows unbounded (cheap)
- Open-source friendly: no vendor lock-in, works with any S3-compatible storage

**Negative:**
- SQLAlchemy adds a dependency and abstraction layer
- Two storage formats to reason about (DB rows vs JSONL files)
- Pruning logic adds complexity vs simple append-only files

**Mitigated by:**
- SQLAlchemy is extremely mature and well-understood
- The two formats serve different purposes (hot query vs cold archive)
- Pruning is conservative: only after confirmed S3 sync + retention window

## Alternatives Considered

1. **JSONL-only (original design):** Simpler, but no queryability, no crash journaling, manual fsync. Adequate for Phase 0 tracer bullet but not for production.

2. **SQLite-only (no S3):** Supported as a configuration, but not the recommended default. Single point of failure for data durability.

3. **OwnTracks Recorder as primary store:** Stores in proprietary `.rec` format, no control over durability or schema. Useful as a supplement (hence passthrough), not as primary storage.
