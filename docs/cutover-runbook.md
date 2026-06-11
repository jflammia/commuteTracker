# Production Cutover Runbook — legacy app → rewrite backend

The rewrite (phases 1–5) replaces the legacy Streamlit app. As of the release
that includes this runbook, `release.yml` builds and publishes the **rewrite
backend** image (`Dockerfile.backend`: FastAPI + bundled SvelteKit frontend) to
`ghcr.io/jflammia/commutetracker:{version,latest}`. Older legacy image tags
(≤ 0.0.11's legacy build) remain pullable in GHCR for rollback.

The cutover is designed to be **non-breaking**: the new backend exposes the
OwnTracks ingest at BOTH `/ingest/owntracks` and the legacy `/pub` path, so the
phone needs **no reconfiguration**.

## Topology before / after

```
BEFORE: OwnTracks → POST /pub → commute-receiver (legacy, :8080) → SQLite → Streamlit dashboard (:8501)
AFTER:  OwnTracks → POST /pub → commute-backend (rewrite, :8090) → raw JSONL → S3 archive (SeaweedFS)
                                                                  → trips/optimizer + SvelteKit UI at /
```

## Step 0 — prerequisites

### Where the legacy data actually lives (traced 2026-06-11)
The legacy receiver writes everything to its `/data` mount: the SQLite
`commute_tracker.db` (source of truth, synchronous write) plus `raw/*.jsonl`.
That data is the **only copy of historical GPS** and is what the migration
reads. It is **not** on the arallon storage server — exhaustively verified via
`hlc truenas` and arallon's Docker API:

- arallon (TrueNAS Scale) has **no** commute dataset, NFS share, SMB share, or
  SeaweedFS S3 bucket (buckets: cars/git/ha-backups/hestia/registry-cache).
- arallon's Docker (the one Komodo reaches via its `docker-socket-proxy`) runs
  7 containers — traefik, seaweedfs(+filestash), promtail, beszel-agent,
  docker-socket-proxy, mbuffer-receiver — **none is commute-tracker**, and there
  are **zero** Docker named volumes.

So the production `commute-receiver` runs elsewhere — most likely **aviato**
(192.168.10.79, offline at cutover time) — with its `/data` in a local Docker
volume/bind on that host. **The migration requires that host online** (or a
copy of its `commute_tracker.db`).

### Archive target
- The new backend archives to an S3 bucket. arallon's SeaweedFS S3 gateway
  (`arallon:8333`) is a good target — create a bucket (e.g. `commute-tracker`)
  and an access key/secret there.

## Step 1 — deploy the backend alongside legacy (passthrough phase)
Run the new backend with `CT_PASSTHROUGH_URL` pointed at the still-running
legacy receiver so the legacy dashboard keeps updating while you validate. The
backend env (Komodo stack / compose):

```yaml
  commute-backend:
    image: ghcr.io/jflammia/commutetracker:latest   # now the rewrite backend
    ports: ["8090:8090"]
    environment:
      CT_DATA_DIR: /data
      # archive → SeaweedFS S3 gateway
      CT_S3_BUCKET: commute-tracker
      CT_S3_PREFIX: commute-tracker
      CT_S3_REGION: us-east-1
      AWS_ACCESS_KEY_ID: <seaweedfs-s3-key>
      AWS_SECRET_ACCESS_KEY: <seaweedfs-s3-secret>
      AWS_ENDPOINT_URL_S3: http://arallon:8333
      # geofences (commute detection)
      CT_HOME_LAT: "<...>"
      CT_HOME_LON: "<...>"
      CT_WORK_LAT: "<...>"
      CT_WORK_LON: "<...>"
      # optimizer (NJ Transit NEC home station → NY Penn, GTFS stop IDs)
      CT_COMMUTE_SOURCE: gtfs_njt
      CT_BOARD_STOP_ID: "<njt-home-stop-id>"
      CT_ALIGHT_STOP_ID: "<ny-penn-stop-id>"
      CT_ARRIVE_BY_LOCAL: "09:00"
      # transit feeds (PATH public now; NJT once RailData API is provisioned)
      CT_PATH_GTFS_URL: http://data.trilliumtransit.com/gtfs/path-nj-us/path-nj-us.zip
      CT_PATH_RT_URL: https://path.transitdata.nyc/gtfsrt
      CT_NJT_USERNAME: "<from 1Password once provisioned>"
      CT_NJT_PASSWORD: "<from 1Password once provisioned>"
      # keep legacy fed during validation
      CT_PASSTHROUGH_URL: http://commute-receiver:8080/pub
    volumes: ["commute-v2:/data"]
    restart: unless-stopped
```

Repoint OwnTracks at the new backend's host:port (path stays `/pub` — the alias
handles it). The backend appends every point durably (always-200) and forwards
to legacy via passthrough, so nothing is lost during validation.

> During this phase the legacy receiver *also* records the forwarded points
> (it owns `/pub`), so the same points exist in both systems. That is fine —
> the new backend's raw archive is append-only and the migration (Step 2) keys
> on the OwnTracks `tst`, so re-ingested history de-duplicates by day-file. Once
> you drop the passthrough (Step 3) only the new backend records.

Verify: `GET /api/health/ingestion` shows fresh `last_event_at`; the SvelteKit
UI loads at `/`; `GET /api/health/sources` shows PATH feeds archiving.

## Step 2 — migrate historical data
With the legacy `commute_tracker.db` copied to the host (or mounted):

```bash
# the data dir must contain no prior raw files except today's live file
python -m scripts.migrate_legacy_raw /path/to/commute_tracker.db /data
```

Compare the migration report's `total` / `per_day` against the legacy DB
(`SELECT COUNT(*) FROM location_records`) — investigate any mismatch before
proceeding. On the next backend restart (or `python -m backend.engine.rebuild`),
the engine replays the archive into trips; matched against the NJT/PATH GTFS,
the optimizer's leg models start learning from real history.

## Step 3 — verify, then retire legacy
Once trips/optimizer look right and the archive is uploading to SeaweedFS:
- Stop `commute-receiver` and `commute-dashboard`.
- Drop `CT_PASSTHROUGH_URL` from the backend (no legacy left to feed).
- Optionally repoint OwnTracks to the canonical `/ingest/owntracks` path.

## Rollback
- Pin the Komodo stack image back to the last legacy tag (`:0.0.10` or the last
  pre-cutover version) and restart.
- OwnTracks `/pub` works against legacy unchanged.
- The new backend's raw archive is independent — no legacy data is mutated by
  the cutover, so rollback is clean.

## Phase 6 (not in this cutover)
Live SSE position-vs-plan tracking, Web Push, and the Trends/Health views are
still pending. The cutover ships the optimizer + labeling UI; those land on top
of the deployed backend.
