# Production Cutover Runbook — legacy app → rewrite backend

> **STATUS: EXECUTED 2026-06-11.** The rewrite is live on bighead and verified
> (ingest flowing, SvelteKit UI served, `/pub` Basic Auth active, PATH feeds
> archiving; 321,237 historical records migrated). The cutover was done
> **Forgejo-native**, not via GHCR: the Komodo Build (`Dockerfile.backend`)
> publishes to `git.blueshift.xyz/justin/commutetracker` and the bighead stack
> deploys it. Both "blocking gaps" below are resolved — gap #1 by building into
> the Forgejo registry (not GHCR), gap #2 by the auth gate ported in
> commuteTracker#27. This document is retained as historical reference and a
> rollback guide. See the `prod-deploy-topology` memory for the current state.

The rewrite (phases 1–5) replaces the legacy Streamlit app. As of the release
that includes this runbook, `release.yml` builds and publishes the **rewrite
backend** image (`Dockerfile.backend`: FastAPI + bundled SvelteKit frontend) to
`ghcr.io/jflammia/commutetracker:{version,latest}`. Older legacy image tags
(≤ 0.0.11's legacy build) remain pullable in GHCR for rollback.

The cutover is designed to be **non-breaking**: the new backend exposes the
OwnTracks ingest at BOTH `/ingest/owntracks` and the legacy `/pub` path, so the
phone needs **no reconfiguration**.

## ⚠️ Blocking cutover gaps (resolve before deploying)

The production trace (2026-06-11, via `hlc komodo` + the stack repo) surfaced
two mismatches between this rewrite and the live deployment. **Both must be
closed before the cutover can actually take effect — neither is cosmetic.**

1. **Registry mismatch.** `release.yml` publishes the rewrite image to **GHCR**
   (`ghcr.io/jflammia/commutetracker`), but production pulls from the **Forgejo**
   registry: `stacks/bighead/commutetracker/compose.yaml` references
   `git.blueshift.xyz/justin/commutetracker:latest`. A GHCR-only build never
   reaches prod. **Fix:** either (a) add a `.forgejo/workflows` build that
   publishes `Dockerfile.backend` to `git.blueshift.xyz/justin/commutetracker`,
   or (b) point the Komodo stack at the GHCR image. Until one is done, merging a
   release does **not** change what bighead runs.

2. **`/pub` auth mismatch.** The legacy receiver enforces **HTTP Basic Auth** on
   `/pub` (`OWNTRACKS_USERNAME`/`OWNTRACKS_PASSWORD`; bad/missing creds → 200 +
   skip DB write). The rewrite's `/pub` alias performs **no auth** — it would
   accept and archive unauthenticated posts. OwnTracks is already configured to
   send those Basic Auth creds, so the phone works either way, but dropping the
   check widens the ingest surface. **Fix:** port the Basic Auth gate onto the
   rewrite's `/pub` (and `/ingest/owntracks`) before exposing it publicly.

## Topology before / after

```
BEFORE: OwnTracks → Traefik (bighead) → POST /pub [Basic Auth] → commute-receiver (legacy, host :8083→ctr :8080)
                                                                → SQLite /data/commute_tracker.db (bighead local disk)
                                                                → Streamlit dashboard (:8501)
AFTER:  OwnTracks → Traefik (bighead) → POST /pub [Basic Auth] → commute-backend (rewrite, :8090) → raw JSONL
                                                                → S3 archive (SeaweedFS) + trips/optimizer + SvelteKit UI at /
```

## Step 0 — prerequisites

### Where the legacy data actually lives (authoritatively traced 2026-06-11)
The legacy stack runs on the Komodo server **bighead** (podman), as containers
`commute-receiver` + `commute-dashboard` — **UP** and Komodo-managed at cutover
time. Deployment is Komodo GitOps from `git.blueshift.xyz/justin/blueshift-stacks`
→ `stacks/bighead/commutetracker/compose.yaml`.

The receiver writes everything to its `/data` mount, which is a **bind mount to
bighead's local disk** (`/var/lib/commutetracker/data:/data:Z`): the SQLite
`commute_tracker.db` (source of truth, `DATABASE_URL=sqlite:////data/commute_tracker.db`,
synchronous write) plus `raw/*.jsonl` and derived Parquet. **S3 sync is NOT
configured**, so that host directory is the **only copy of historical GPS** and
is what the migration reads. Because bighead is up and Komodo-reachable, the
migration source is **online now** — read `commute_tracker.db` via Komodo /
bighead; no host needs to be brought back up.

It is **not** on the arallon storage server (an earlier guess) — exhaustively
verified via `hlc truenas` and arallon's Docker API:

- arallon (TrueNAS Scale) has **no** commute dataset, NFS share, SMB share, or
  SeaweedFS S3 bucket (buckets: cars/git/ha-backups/hestia/registry-cache).
- arallon's Docker runs 7 containers — traefik, seaweedfs(+filestash), promtail,
  beszel-agent, docker-socket-proxy, mbuffer-receiver — **none is
  commute-tracker**, and there are **zero** Docker named volumes.

It is also **not** on aviato (a still-earlier guess from an SSH timeout; aviato
and bighead are separate Komodo servers — the timeout was not evidence of where
the stack runs).

### Archive target
- The new backend archives to an S3 bucket; prod currently configures **no** S3,
  so this is net-new. arallon's SeaweedFS S3 gateway (`arallon:8333`) is a good
  target — create a bucket (e.g. `commute-tracker`) and an access key/secret
  there. (arallon has no commute bucket today; you are creating the first one.)

## Step 1 — deploy the backend alongside legacy (passthrough phase)
Run the new backend with `CT_PASSTHROUGH_URL` pointed at the still-running
legacy receiver so the legacy dashboard keeps updating while you validate. The
backend env (Komodo stack / compose):

```yaml
  commute-backend:
    # NOTE: prod's Komodo stack pulls git.blueshift.xyz/justin/commutetracker —
    # see blocking gap #1. Use whichever registry you resolved that gap toward.
    image: git.blueshift.xyz/justin/commutetracker:latest   # rewrite backend
    ports: ["8090:8090"]
    environment:
      CT_DATA_DIR: /data
      # Basic Auth on /pub — match legacy (blocking gap #2); OwnTracks already
      # sends these creds. Requires the rewrite's /pub to enforce them.
      OWNTRACKS_USERNAME: "<from 1Password / legacy stack secret>"
      OWNTRACKS_PASSWORD: "<from 1Password / legacy stack secret>"
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
The source DB lives on bighead at `/var/lib/commutetracker/data/commute_tracker.db`
and bighead is up + Komodo-managed, so it is reachable now — copy it off via
Komodo / bighead (no host needs to be revived). With that
`commute_tracker.db` copied to the backend host (or mounted):

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
