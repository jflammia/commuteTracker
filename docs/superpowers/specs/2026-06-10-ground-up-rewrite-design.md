# CommuteTracker Ground-Up Rewrite — Design

**Date:** 2026-06-10
**Status:** Approved pending final spec review

## Context

The existing app (~6,850 lines: OwnTracks receiver → Polars batch pipeline →
four-classifier ensemble → 6-page Streamlit dashboard) is being rewritten from
the ground up. Motivations, confirmed with the user:

1. **The UX is weak** — Streamlit can't support the interactions that matter
   (map-based labeling, drag corrections, instant what-if queries, live views).
2. **The results aren't useful** — the "optimizer" is descriptive charts over
   raw departure minutes. The commute is actually a **multi-leg rail commute**
   (walk/drive → NJ Transit Northeast Corridor → sometimes a forced transfer at
   Newark to PATH → 33rd St, instead of NEC direct to NY Penn). Optimization
   that ignores train schedules, specific trains, and service disruptions
   cannot produce actionable answers.

Only historical raw data survives the rewrite. Everything else — receiver,
storage formats, pipeline, classifiers, API, UI — is replaced.

## Goals

Two core functions:

1. **Commute optimization** — answer, concretely: *which train should I target,
   when must I leave, and what happens if things go wrong?* Surfaced as a daily
   recommendation (push + dashboard), an interactive what-if tool, route/
   itinerary comparison (NEC direct vs Newark→PATH), and live tracking of an
   in-progress commute against the plan — all on statistically honest,
   small-N-appropriate foundations.
2. **Data labeling + visualization** — a fast, map-first correction loop
   (fix modes, adjust boundaries, confirm/correct train matches, flag
   missed/phantom trips), data-trust visibility (gaps, GPS quality, unmatched
   segments), and a label → retrain → improve loop feeding the classifiers.

### Non-goals

- Multi-user support. This is a single-user personal system.
- A native iOS app **in this rewrite**. Explicitly deferred as a possible
  future phase; the API is designed so a native client could be added without
  server changes. OwnTracks remains the capture mechanism — its background
  location handling is battle-tested and replacing it would maximize, not
  minimize, ingestion risk.
- Driving-route optimization (traffic, alternative roads). The commute is
  rail-centric; driving appears only as first/last-mile legs.
- Real-time train *prediction* modeling beyond what the official feeds
  provide.

## Hard requirements

- **Ingestion must be nearly unbreakable.** Fewest possible moving parts
  between an OwnTracks POST and durable storage. Always return HTTP 200.
- **Primitive data is unloseable.** Raw GPS, label events, and recorded
  real-time alert history can never be regenerated. Everything derived can be.
- Existing historical raw data is migrated in.

## Architecture (approved: event-driven modular monolith)

One container. One FastAPI process hosting: ingestion, the trip engine,
REST API, SSE live stream, MCP server, Web Push, and the internal scheduler.
A SvelteKit frontend is compiled to static files at Docker build time and
served by the same FastAPI app.

```
OwnTracks POST → /ingest → append raw JSONL  (durable BEFORE anything else)
                              ↳ in-process event bus → trip engine (incremental)
                                                          ↳ derived DuckDB
GTFS static + GTFS-RT/alerts → transit feed manager → archive + derived
REST API · SSE live stream · MCP · Web Push · scheduler — one app
SvelteKit static build (installable PWA) served by FastAPI
```

Repo layout:

```
backend/
  ingest/        # OwnTracks endpoint (always 200), raw append
  storage/       # archive (JSONL→Parquet→S3), derived DuckDB, label store
  engine/        # trip state machine — pure, incremental, replayable
  sources/       # pluggable external data sources (see Extensibility)
    gtfs_njt/    #   NJ Transit GTFS static + GTFS-RT
    gtfs_path/   #   PATH schedules + realtime departures
  transit/       # train matching over source observations
  optimizer/     # leg models, itinerary composer, recommendation
  api/           # REST routes, SSE, schemas
  mcp/           # MCP tools over the same service layer
  notify/        # scheduler, Web Push (VAPID), watchdog
frontend/
  src/routes/    # Today, Trips, Optimizer, Trends, Health
  src/lib/       # map components (MapLibre), charts (ECharts), API client
```

## Storage (approved after iteration: file-based raw archive + DuckDB query layer)

Design priority: the write path must be trivially simple; durability comes
from files in object storage, not from a database.

**Write path (must never break):** OwnTracks POST → append one JSON line to a
local date-stamped file (`raw/owntracks/2026-06-10.jsonl`) → 200. No database,
no schema validation, no locks on the hot path. Malformed payloads are still
appended (tagged file) — never bounced.

**Archive path (durability):** a daily job converts each *closed* day-file to
Parquet and uploads to S3, partitioned by date
(`s3://…/raw/owntracks/year=/month=/day=`). The local file is deleted only
after the uploaded object is read back and verified. Archive applies to all
primitive streams:

| Stream            | Why primitive                                  |
|-------------------|------------------------------------------------|
| Raw GPS (OwnTracks payloads) | can never be re-captured              |
| Label events      | human input, can never be recomputed           |
| GTFS-RT trip updates + service alerts (sampled/polled) | history not re-fetchable |
| GTFS static snapshots | versioned so historical trips match the schedule in effect that day |
| Every future source's raw responses (see Extensibility) | same rule: external observations can't be re-fetched |

**Read path:** DuckDB (embedded query engine) queries the Parquet archive
(S3 and/or local cache) plus today's JSONL tail through unified SQL views.
DuckDB is the *query layer*, not the durability layer.

**Derived store:** a local DuckDB file (`derived.duckdb`) holding `trips`,
`segments`, `enriched_points`, `train_matches`, `current_labels`
(materialized from label events), `model_runs`, `recommendations`,
`notifications`, `watchdog_events`. **Fully disposable** — `rebuild` deletes
and replays the archive through the engine.

**Backup:** primitive data is durable in S3 by design; the derived DB needs no
backup.

## Trip engine (approved)

A pure, deterministic state machine over an ordered point stream. The same
code processes live points and archive replays — batch/live equivalence holds
by construction (one code path). Engine state (current trip, recent-point
buffer) is a small serializable object; on process restart it resumes by
replaying today's tail.

Per-point stages:

1. **Hygiene** — reject bad-accuracy points (configurable threshold), detect
   teleports (implied speed over sanity cap), de-duplicate, handle
   out-of-order timestamps. Rejected points are flagged in derived data only;
   raw is untouched.
2. **Enrichment** — speed/heading/distance/acceleration deltas; geofence
   membership (home, work, configured stations) with hysteresis to prevent
   boundary flapping.
3. **Trip detection** — `IDLE → MOVING` on sustained movement (M of N recent
   points above thresholds); `MOVING → CLOSED` on dwell (stationary > T min)
   or data gap (> G min; close at last point). Home↔work trips tagged as
   commutes with direction.
4. **Segmentation + mode** — split segments at sustained mode changes. Mode
   per segment from a two-layer classifier:
   - transparent heuristic baseline (speed/variance profile → drive / walk /
     train / stationary), always available;
   - ML model (trained on label events) overrides only above a confidence
     threshold;
   - manual labels override everything, always.

   The previous four-classifier voting ensemble is dropped; corridor and
   waypoint signals become *features* of the single model, not voters.

## Extensibility: pluggable data sources (architectural expectation)

New external data sources **will** be added over the system's life — known
candidates (a NY Penn live departure board such as nypenn.live, with realtime
track assignments and status) and sources that don't exist yet. The
architecture treats this as an expectation, not an afterthought:

- **`Source` plugin interface.** Every external feed is a self-contained
  module implementing one small contract: a stream name, a polling/refresh
  policy (cron-like, or adaptive — e.g. "high frequency during commute
  windows"), a `fetch()` that returns raw responses, and a parser that emits
  **typed observations** (e.g. `TrainDelay`, `TrackAssignment`,
  `ServiceAlert`, `ScheduleVersion`). Registering a source is one module plus
  one config entry — no changes to core code.
- **Archive-first, always.** Every source's raw responses are archived
  verbatim through the same JSONL → Parquet → S3 pattern, under
  `raw/<source>/…`, *before* any parsing. External observations are primitive
  data — you can't re-fetch the past — so the moment a source is added its
  history starts accumulating, and any future model or matcher improvement
  can be replayed over everything collected since day one. Parser bugs are
  recoverable: re-parse the archive.
- **Consumers subscribe to observation types, not sources.** Train matching
  consumes `TrainDelay`/`ScheduleVersion` regardless of which source produced
  them; the optimizer's leg models consume delay observations from NJT
  GTFS-RT today and nypenn.live tomorrow without structural change. A new
  source enriches existing consumers for free; a new observation *type* adds
  a consumer.
- **Model features are versioned.** The feature-extraction step for the
  classifier and leg models declares named, versioned features. Adding a
  feature derived from a new source means recomputing features over the
  archive and retraining — possible precisely because raw source history is
  archived from the start.
- **Watchdog covers every registered source automatically** (staleness,
  fetch failures) — registration includes the health policy.

The GTFS and realtime feeds below are simply the first two plugins; the NY
Penn departure board is the expected third.

## Transit context layer (approved)

Slow-moving external data, managed as source plugins (above):

- **GTFS static** (NJ Transit rail + PATH): stations, routes, scheduled trips.
  Nightly refresh check; every distinct version snapshotted to the archive.
  Historical analysis always uses the schedule in effect on the trip's date.
- **Real-time:** NJ Transit GTFS-RT trip updates + service alerts; PATH
  real-time departures. Polled at higher frequency during plausible/active
  commute windows, low frequency otherwise. All observations archived
  (primitive — not re-fetchable later).
- **Train matching** (post-trip enrichment + live): rail segments are matched
  to specific scheduled trains using boarding/alighting station geofences,
  direction, alignment, and timing against that day's schedule plus recorded
  delays. Output: each commute becomes a legible itinerary, e.g.
  `walk 6 min → NEC #3838 (+2 min) → NY Penn → walk 9 min`, including forced
  reroutes (`NEC → transfer Newark → PATH → 33rd St`).
- **Disruption cross-referencing:** alerts are attached to affected trips so
  outliers are *explained* (Jun 3: NEC cancellation → +22 min) rather than
  silently polluting statistics.

Feed implementation details (exact NJ Transit/PATH endpoints, auth/keys) are
verified at implementation time; both agencies publish GTFS static, NJ Transit
publishes GTFS-RT via its developer portal, PATH real-time may use the
community API if no official feed fits.

## Optimizer (approved)

Reasons over **itineraries and discrete trains**, not continuous departure
minutes. The schedule does most of the predictive work; personal data
estimates deviations — appropriate for small N.

1. **Leg models** — empirical distributions per leg: door→station,
   station-wait, per-train ride time (schedule + that train's observed delay
   history from the RT archive + the user's own rides), Newark transfer,
   terminal→office walk. Quantile estimates with shrinkage toward schedule
   when observations are few. No black-box ML.
2. **Itinerary composer** — given a goal (e.g. "at office by 9:00" on a given
   day), enumerate feasible itineraries from the in-effect schedule (NEC
   direct; Newark→PATH), compose leg distributions into door-to-door
   distributions per option, and rank:
   *"Leave by 7:38 to catch NEC #3838 — P50 arrival 8:52, P90 9:04; PATH
   fallback adds ~12 min."* Works both directions (arrival goal → required
   departure; departure → expected arrival).
3. **Daily recommendation** — scheduler computes the morning recommendation
   factoring live alerts (NEC meltdown → recommend earlier train or PATH
   itinerary); delivered via Web Push, the Today dashboard card, REST, and
   MCP — identical logic behind all four surfaces.
4. **Live mode** — during an active commute, track actual position against
   the plan: which train was actually caught, its current delay, projected
   arrival, and whether a transfer is still advantageous. Streamed over SSE.

## Labeling + ML loop (approved)

- **Trips workbench (map-first):** segment timeline colored by mode + MapLibre
  route trace. One-click mode fix, boundary drag, train-match confirm/correct
  ("not #3838 — I caught the 8:08"), flag missed/phantom trips. Keyboard-
  driven queue of unreviewed trips.
- **Label events are append-only primitive data**, archived like raw GPS.
  Applying one immediately corrects derived data and accumulates training
  data.
- **Models & labels panel (inside the workbench):** label coverage, model
  versions + held-out accuracy, retrain button, unmatched-rail-segments work
  queue.
- Retraining produces a versioned `model_run`; the engine picks up new models
  explicitly, never silently.

## Frontend (approved, five views, SvelteKit + MapLibre + ECharts)

Installable **PWA** (manifest + service worker + Web Push on iOS 16.4+).

| View | Purpose |
|------|---------|
| **Today** (default) | Recommendation card, active alerts, live commute map + vs-plan strip during active trips (SSE-driven) |
| **Trips** | Labeling workbench (above) + models & labels panel |
| **Optimizer** | What-if: goal input → ranked itinerary options with uncertainty bands; departure→arrival fan chart |
| **Trends** | Door-to-door duration over time, per-train reliability, weekday patterns, disruption-annotated outliers |
| **Health** | Data only: ingestion freshness, gap log, GPS quality, archive/backup status, feed status, watchdog history, rebuild control |

## Notifications (approved: self-hosted Web Push, no ntfy)

- **Web Push via VAPID** directly to the installed PWA (no third-party
  service, no Apple developer account).
- **Commute pushes:** morning recommendation; mid-commute changes (your train
  cancelled, transfer recommended).
- **Watchdog alerts (separate channel/severity):** ingestion stalled (no
  points for N daytime hours), archive upload/verify failure, feed staleness
  during commute windows, raw-volume disk threshold.

## API surface (approved)

Everything the frontend uses is public API; MCP and Home Assistant consume the
same endpoints.

```
POST /ingest/owntracks                      # always 200
GET  /api/recommendation?date=&arrive_by=   # daily / what-if answer
GET  /api/trips                             # list + filters
GET  /api/trips/{id}                        # itinerary, segments, trace
POST /api/labels                            # label events (mode, boundary, train, flag)
GET  /api/live                              # SSE: position, trip state, vs-plan projection
GET  /api/trends/…    GET /api/health/…
POST /api/admin/rebuild    POST /api/admin/retrain
POST /api/push/subscribe                    # Web Push subscription management
```

MCP tools (thin wrappers over the same service layer): `get_recommendation`,
`get_live_status`, `query_trips`, `label_segment`.

## Migration (one-time)

1. Export old SQLite `location_records` + existing raw JSONL into the new
   archive layout (Parquet by date, uploaded to S3 + local cache).
2. Replay the archive through the new engine.
3. Sanity report: detected trips vs the old system's trips (count, date
   coverage, duration distribution) — human-reviewed before the old system is
   retired.
4. The 8 existing segment labels import as label events.

## Deployment

Same shape as today: one Docker image (multi-stage: Node builds the frontend →
Python runtime serves everything), GHCR via release-please, Komodo redeploy.
Configuration stays env-var based: geofences, station list, feed keys/URLs,
VAPID keys, S3 credentials. CI: lint (ruff + eslint/svelte-check), tests,
Docker build — GitHub Actions as today.

## Error handling principles

- Ingest returns 200 unconditionally; failures past the raw append are
  logged + surfaced via watchdog, never propagated to OwnTracks.
- The engine quarantines (flags) bad points rather than dropping them.
- Feed failures degrade gracefully: optimizer falls back to schedule-only
  reasoning and says so in its output.
- Archive uploads verify by read-back before local cleanup; failures alert.

## Testing strategy

- **Engine:** golden tests from recorded real days (fixtures in repo);
  property test: replay(prefix) + replay(remainder) ≡ replay(full) —
  guarantees live/batch equivalence.
- **Train matcher:** fixtures pairing recorded GPS days with the GTFS
  snapshot + RT observations from those days; assert matched train IDs.
- **Optimizer:** distribution composition unit tests; regression tests on
  recommendation output for fixed fixture inputs.
- **API:** contract tests (schemas, status codes) on all endpoints.
- **Frontend:** Playwright smoke tests on the labeling flow (highest-value
  interaction) and the Today card.

## Decisions log

| Decision | Choice | Over | Why |
|----------|--------|------|-----|
| Architecture | Event-driven modular monolith | Batch core; microservices | Live view first-class; one container; no broker to babysit |
| Frontend | SvelteKit + MapLibre + ECharts | React; Vue; Streamlit | Real map interactions; small output; single-dev ergonomics |
| Raw storage | JSONL append → daily Parquet → S3; DuckDB as query engine | SQLite-only; DuckDB-as-database; 3-tier JSONL+SQLite+Parquet | Unbreakable write path; durable, future-proof archive; user priority: never lose primitive data |
| Classifiers | Heuristic baseline + single ML model + label supremacy | Four-voter ensemble | Legible, debuggable; ensemble complexity unjustified at this N |
| Optimization framing | Discrete trains/itineraries via GTFS | Continuous departure-time regression | The commute is rail; schedule carries the signal; small-N honest |
| Platform | Web app as installable PWA + Web Push | Native iOS app; web+ntfy | Background location capture stays on battle-tested OwnTracks; no $99/yr + APNs + Xcode loop; ntfy made redundant by Web Push; native client deferred, API-ready |
| Health UX | Proactive watchdog pushes + slim Health tab | Passive dashboard | A dashboard nobody checks doesn't protect ingestion |
| Future data sources | Pluggable `Source` interface, archive-first, typed observations | Hard-coded feed integrations | New sources (nypenn.live, unknown future ones) are an expectation; archived history from day one makes future features retroactively trainable |
