# Rewrite Phase 4: Labeling + Frontend Workbench — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the labeling loop — label events as unloseable primitive data with override semantics in the derived store — and the first frontend: a SvelteKit SPA with a trips list and a map-first trip workbench (MapLibre trace, segment timeline, one-click corrections, review queue), served by FastAPI from a multi-stage Docker build.

**Architecture:** Labels follow the same primitive-data discipline as GPS: every correction is appended to a raw `labels` stream (archived to S3 like everything else) BEFORE being applied to the derived store, and rebuild replays the labels stream after trips/matches — so corrections survive any rebuild and the derived store stays disposable. The frontend is a static SvelteKit build (SPA mode, no SSR) served by the existing FastAPI app behind a catch-all route; in dev, Vite proxies `/api` to uvicorn. Map tiles are plain OSM raster (no API keys).

**Tech Stack:** Python/FastAPI (existing), SvelteKit 2 + Svelte 5 (adapter-static, TypeScript), MapLibre GL JS (OSM raster tiles), Vitest (frontend unit), Playwright (one smoke test, descopable), Node 22.

**Spec:** `docs/superpowers/specs/2026-06-10-ground-up-rewrite-design.md` — "Labeling + ML loop" (map-first workbench, label events append-only primitive, label supremacy) and "Frontend" (Trips view). Deliberate Phase-4 trims, all noted in the spec-coverage sense: boundary-drag editing and the ML retrain loop are deferred (labels must accumulate before a model is trainable); Today/Optimizer/Trends/Health views are later phases — the shell shows placeholder nav entries.

---

## File structure

```
backend/
  api/labels.py          # POST /api/labels — append primitive, then apply
  api/trips.py           # modify: ?reviewed= filter
  storage/derived.py     # modify: label_overrides DDL, apply_label, effective merge
  engine/rebuild.py      # modify: replay labels stream after replay+matching
  app.py                 # modify: labels router, SPA static serving
  tests/test_labels_store.py
  tests/test_labels_api.py
  tests/test_rebuild.py  # modify: label replay tests
frontend/
  package.json           # svelte 5, kit 2, adapter-static, maplibre-gl, vitest
  svelte.config.js
  vite.config.ts         # dev proxy /api + /ingest → :8090
  tsconfig.json
  src/app.html
  src/app.d.ts
  src/routes/+layout.svelte    # nav shell (Trips live; Today/Optimizer/Trends/Health placeholders)
  src/routes/+layout.ts        # ssr = false (SPA)
  src/routes/+page.svelte      # redirect → /trips
  src/routes/trips/+page.svelte        # list + unreviewed toggle
  src/routes/trips/+page.ts            # load
  src/routes/trips/[id]/+page.svelte   # workbench
  src/routes/trips/[id]/+page.ts       # load
  src/lib/api.ts         # typed fetch wrapper
  src/lib/trace.ts       # pure: points+segments → colored GeoJSON features
  src/lib/Map.svelte     # MapLibre map of the trace
  src/lib/SegmentPanel.svelte  # timeline + label actions
  src/lib/trace.test.ts  # vitest
  e2e/workbench.spec.ts  # Playwright smoke (Task 9)
  playwright.config.ts
Dockerfile.backend       # modify: multi-stage node build
.github/workflows/ci.yml # modify: frontend job
.gitignore               # modify: node_modules, frontend/build, etc.
README.md                # modify
```

**Design decisions locked in:**

- **Label event payload** (the primitive record, appended to raw stream `labels`):
  `{"type": <kind>, "trip_id": str, "seg_index": int?, "value": <kind-specific>}`
  Kinds: `segment_mode` (value: `"stationary"|"walk"|"vehicle"|"train"`),
  `train_match` (value: `"confirmed"|"wrong"`), `trip_flag` (value:
  `"phantom"|"ok"`), `trip_reviewed` (value: `true|false`). Latest event wins
  per (trip_id, seg_index, kind).
- **Label supremacy**: `get_trip` segments expose `mode` (heuristic) AND
  `mode_effective` + `mode_source` (`"label"` or `"heuristic"`); itineraries
  are built from `mode_effective`. Train legs gain `confirmation`
  (`"confirmed"|"wrong"|null`). Trips gain `flag` and `reviewed`.
- **Replay order in rebuild**: truncate → parse schedules → replay points
  (trips+matches) → **replay labels**. A label referencing a trip that no
  longer exists (e.g. engine params changed boundaries) is skipped and
  counted — never an error.
- **Mode vocabulary grows by one**: labels may assert `train` (the heuristic
  never emits it; Phase 3's matcher attributes trains separately). The
  frontend colors it distinctly.
- **No SSR anywhere** (`ssr = false`): the app is a pure SPA; adapter-static
  `fallback: 'index.html'`; FastAPI catch-all serves the fallback for deep
  links. API routes are registered before the catch-all so they always win.
- **Playwright is one smoke test and explicitly descopable**: if CI browser
  installs prove painful, the task may mark it local-only — the decision must
  be recorded in the task's commit message.

---

### Task 1: Label overrides in the derived store

**Files:**
- Modify: `backend/storage/derived.py`
- Test: `backend/tests/test_labels_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_labels_store.py
from backend.tests.test_derived_store import _closed
from backend.storage.derived import DerivedStore


def _store_with_trip(settings):
    store = DerivedStore(settings)
    store.write_trip_closed(_closed())  # trip_id t1000, 1 vehicle segment (seg_index 0)
    return store


def test_apply_segment_mode_overrides_effective(settings):
    store = _store_with_trip(settings)
    ok = store.apply_label({"type": "segment_mode", "trip_id": "t1000",
                            "seg_index": 0, "value": "train"})
    assert ok is True
    d = store.get_trip("t1000")
    seg = d["segments"][0]
    assert seg["mode"] == "vehicle"            # heuristic untouched
    assert seg["mode_effective"] == "train"    # label wins
    assert seg["mode_source"] == "label"
    assert d["itinerary"][0]["mode"] == "train"


def test_unlabeled_segment_uses_heuristic(settings):
    store = _store_with_trip(settings)
    seg = store.get_trip("t1000")["segments"][0]
    assert seg["mode_effective"] == "vehicle"
    assert seg["mode_source"] == "heuristic"


def test_latest_label_wins(settings):
    store = _store_with_trip(settings)
    store.apply_label({"type": "segment_mode", "trip_id": "t1000",
                       "seg_index": 0, "value": "train"})
    store.apply_label({"type": "segment_mode", "trip_id": "t1000",
                       "seg_index": 0, "value": "walk"})
    assert store.get_trip("t1000")["segments"][0]["mode_effective"] == "walk"


def test_trip_flag_and_reviewed(settings):
    store = _store_with_trip(settings)
    store.apply_label({"type": "trip_flag", "trip_id": "t1000", "value": "phantom"})
    store.apply_label({"type": "trip_reviewed", "trip_id": "t1000", "value": True})
    d = store.get_trip("t1000")
    assert d["trip"]["flag"] == "phantom"
    assert d["trip"]["reviewed"] is True
    assert store.list_trips()[0]["reviewed"] is True


def test_unreviewed_default(settings):
    store = _store_with_trip(settings)
    assert store.get_trip("t1000")["trip"]["reviewed"] is False
    assert store.get_trip("t1000")["trip"]["flag"] is None


def test_train_match_confirmation_surfaces_in_itinerary(settings):
    store = _store_with_trip(settings)
    store.apply_label({"type": "train_match", "trip_id": "t1000",
                       "seg_index": 0, "value": "wrong"})
    leg = store.get_trip("t1000")["itinerary"][0]
    assert leg["confirmation"] == "wrong"


def test_apply_label_unknown_trip_returns_false(settings):
    store = DerivedStore(settings)
    ok = store.apply_label({"type": "trip_flag", "trip_id": "nope", "value": "ok"})
    assert ok is False


def test_list_trips_reviewed_filter(settings):
    store = _store_with_trip(settings)
    store.write_trip_closed(_closed(trip_id="t9000", start=9000.0))
    store.apply_label({"type": "trip_reviewed", "trip_id": "t1000", "value": True})
    assert [t["trip_id"] for t in store.list_trips(reviewed=False)] == ["t9000"]
    assert [t["trip_id"] for t in store.list_trips(reviewed=True)] == ["t1000"]
    assert len(store.list_trips()) == 2


def test_labels_survive_trip_rewrite(settings):
    store = _store_with_trip(settings)
    store.apply_label({"type": "segment_mode", "trip_id": "t1000",
                       "seg_index": 0, "value": "train"})
    store.write_trip_closed(_closed())  # same trip_id rewritten
    assert store.get_trip("t1000")["segments"][0]["mode_effective"] == "train"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_labels_store.py -v`
Expected: FAIL — no attribute apply_label / KeyError mode_effective.

- [ ] **Step 3: Implement in `backend/storage/derived.py`**

Add to `_DDL`:

```sql
CREATE TABLE IF NOT EXISTS label_overrides (
    trip_id VARCHAR, seg_index INTEGER, kind VARCHAR, value VARCHAR,
    labeled_at VARCHAR
);
```

Add `label_overrides` to `truncate()`'s tuple. Do NOT delete label_overrides
in `write_trip_closed`'s per-trip DELETE loop — labels survive trip rewrites
(they are reapplied data, not derived-from-points data).

Add imports `import json` and `from datetime import UTC, datetime` (datetime
already imported — extend as needed). Add methods:

```python
    _LABEL_KINDS = ("segment_mode", "train_match", "trip_flag", "trip_reviewed")

    def apply_label(self, payload: dict) -> bool:
        """Upsert one label override. Returns False when the trip is unknown
        (e.g. engine params changed and the trip no longer exists) — the
        primitive label event is still archived; only the application is
        skipped."""
        kind = payload.get("type")
        trip_id = payload.get("trip_id")
        seg_index = payload.get("seg_index")
        if kind not in self._LABEL_KINDS:
            return False
        exists = self._con.execute(
            "SELECT 1 FROM trips WHERE trip_id = ?", [trip_id]
        ).fetchone()
        if exists is None:
            return False
        self._con.execute(
            "DELETE FROM label_overrides WHERE trip_id = ? AND kind = ? "
            "AND seg_index IS NOT DISTINCT FROM ?",
            [trip_id, kind, seg_index],
        )
        self._con.execute(
            "INSERT INTO label_overrides VALUES (?,?,?,?,?)",
            [trip_id, seg_index, kind, json.dumps(payload.get("value")),
             datetime.now(UTC).isoformat()],
        )
        return True

    def _labels_for_trip(self, trip_id: str) -> list[tuple]:
        return self._con.execute(
            "SELECT seg_index, kind, value FROM label_overrides WHERE trip_id = ?",
            [trip_id],
        ).fetchall()
```

In `get_trip`, after segments/points/matches are built, merge label state
(replace the existing itinerary construction):

```python
        seg_mode_overrides = {}
        train_confirmations = {}
        trip_flag = None
        trip_reviewed = False
        for seg_index, kind, value in self._labels_for_trip(trip_id):
            v = json.loads(value)
            if kind == "segment_mode":
                seg_mode_overrides[seg_index] = v
            elif kind == "train_match":
                train_confirmations[seg_index] = v
            elif kind == "trip_flag":
                trip_flag = v
            elif kind == "trip_reviewed":
                trip_reviewed = bool(v)
        for s in segments:
            override = seg_mode_overrides.get(s["seg_index"])
            s["mode_effective"] = override if override is not None else s["mode"]
            s["mode_source"] = "label" if override is not None else "heuristic"
        match_by_seg = {m["seg_index"]: m for m in self.matches_for_trip(trip_id)}
        itinerary = [
            {
                "mode": s["mode_effective"],
                "start_ts": s["start_ts"],
                "end_ts": s["end_ts"],
                "duration_s": s["duration_s"],
                "distance_m": s["distance_m"],
                "train": match_by_seg.get(s["seg_index"]),
                "confirmation": train_confirmations.get(s["seg_index"]),
            }
            for s in segments
        ]
        trip_dict = self._trip_row_to_dict(row)
        trip_dict["flag"] = trip_flag
        trip_dict["reviewed"] = trip_reviewed
        return {"trip": trip_dict, "segments": segments, "points": points,
                "itinerary": itinerary}
```

In `list_trips`, add the reviewed flag and optional filter (replace the
method):

```python
    def list_trips(self, limit: int = 50, reviewed: bool | None = None) -> list[dict]:
        sql = (
            "SELECT t.trip_id, t.start_ts, t.end_ts, t.duration_s, t.distance_m, "
            "t.point_count, t.start_geofence, t.end_geofence, t.direction, "
            "COALESCE((SELECT value FROM label_overrides lo WHERE lo.trip_id = t.trip_id "
            "          AND lo.kind = 'trip_reviewed'), 'false') AS reviewed_raw "
            "FROM trips t "
        )
        params: list = []
        if reviewed is not None:
            sql += "WHERE COALESCE((SELECT value FROM label_overrides lo "
            sql += "WHERE lo.trip_id = t.trip_id AND lo.kind = 'trip_reviewed'), 'false') = ? "
            params.append("true" if reviewed else "false")
        sql += "ORDER BY t.start_ts DESC LIMIT ?"
        params.append(limit)
        rows = self._con.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = self._trip_row_to_dict(r[:9])
            d["reviewed"] = r[9] == "true"
            out.append(d)
        return out
```

NOTE: `json.dumps(True)` is the string `true`, `json.dumps(False)` is
`false` — the string comparisons above rely on that; a `trip_reviewed` with
value False compares as "false" = unreviewed, correct.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_labels_store.py backend/tests/test_derived_store.py backend/tests/test_itinerary_api.py -v`
Expected: all PASS — the itinerary test still passes because unlabeled legs
fall back to heuristic mode and `confirmation` is additive.

- [ ] **Step 5: Commit**

```bash
git add backend/storage/derived.py backend/tests/test_labels_store.py
git commit -m "feat: label overrides with effective-mode merge in derived store"
```

---

### Task 2: Labels API + reviewed filter

**Files:**
- Create: `backend/api/labels.py`
- Modify: `backend/api/trips.py`
- Modify: `backend/app.py`
- Test: `backend/tests/test_labels_api.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_labels_api.py
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.synth import commute


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        pts, _, _ = commute()
        for pt in pts:
            c.post("/ingest/owntracks", json={"_type": "location", "tst": pt.ts,
                                              "lat": pt.lat, "lon": pt.lon,
                                              "acc": pt.accuracy_m})
        yield c, app


def _vehicle_seg_index(c, trip_id):
    detail = c.get(f"/api/trips/{trip_id}").json()
    return next(s["seg_index"] for s in detail["segments"] if s["mode"] == "vehicle")


def test_post_label_applies_and_archives(client, settings):
    c, app = client
    trip_id = c.get("/api/trips").json()[0]["trip_id"]
    seg_index = _vehicle_seg_index(c, trip_id)
    resp = c.post("/api/labels", json={"type": "segment_mode", "trip_id": trip_id,
                                       "seg_index": seg_index, "value": "train"})
    assert resp.status_code == 201
    assert resp.json() == {"applied": True}
    # applied to derived
    detail = c.get(f"/api/trips/{trip_id}").json()
    seg = next(s for s in detail["segments"] if s["seg_index"] == seg_index)
    assert seg["mode_effective"] == "train"
    # archived as primitive FIRST — raw labels stream has the event
    label_files = list((settings.data_dir / "raw" / "labels").glob("*.jsonl"))
    assert len(label_files) == 1
    rec = json.loads(label_files[0].read_text().splitlines()[0])
    assert rec["payload"]["type"] == "segment_mode"
    assert rec["payload"]["value"] == "train"


def test_post_label_unknown_trip_archives_but_applies_false(client, settings):
    c, app = client
    resp = c.post("/api/labels", json={"type": "trip_flag", "trip_id": "nope",
                                       "value": "ok"})
    assert resp.status_code == 201
    assert resp.json() == {"applied": False}
    assert (settings.data_dir / "raw" / "labels").is_dir()  # still archived


@pytest.mark.parametrize("bad", [
    {"type": "nonsense", "trip_id": "t", "value": "x"},
    {"type": "segment_mode", "trip_id": "t", "value": "flying"},        # bad mode
    {"type": "segment_mode", "trip_id": "t", "value": "walk"},          # missing seg_index
    {"type": "train_match", "trip_id": "t", "seg_index": 0, "value": "maybe"},
    {"type": "trip_flag", "trip_id": "t", "value": "weird"},
    {"type": "trip_reviewed", "trip_id": "t", "value": "yes"},          # not bool
    {"trip_id": "t", "value": "x"},                                     # no type
])
def test_post_label_validation_rejects_garbage(client, bad):
    c, app = client
    resp = c.post("/api/labels", json=bad)
    assert resp.status_code == 400
    # rejected garbage is NOT archived as a label event — only valid labels
    # are primitive data


def test_trips_reviewed_filter_via_api(client):
    c, app = client
    trip_id = c.get("/api/trips").json()[0]["trip_id"]
    assert c.get("/api/trips?reviewed=false").json()[0]["trip_id"] == trip_id
    c.post("/api/labels", json={"type": "trip_reviewed", "trip_id": trip_id,
                                "value": True})
    assert c.get("/api/trips?reviewed=false").json() == []
    assert c.get("/api/trips?reviewed=true").json()[0]["trip_id"] == trip_id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_labels_api.py -v`
Expected: FAIL — 404 /api/labels.

- [ ] **Step 3: Implement**

```python
# backend/api/labels.py
"""Label events: archived as primitive data FIRST, then applied to derived.

Validation is strict — labels are human input through our own UI, so a
malformed payload is a bug, not data to preserve. Valid labels are appended
to the raw `labels` stream (same archive pipeline as GPS) before application;
rebuild replays them, so corrections are permanent."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

_SEGMENT_MODES = {"stationary", "walk", "vehicle", "train"}
_CONFIRMATIONS = {"confirmed", "wrong"}
_FLAGS = {"phantom", "ok"}


def _validate(body: dict) -> str | None:
    kind = body.get("type")
    if not isinstance(body.get("trip_id"), str):
        return "trip_id must be a string"
    if kind == "segment_mode":
        if not isinstance(body.get("seg_index"), int):
            return "segment_mode requires integer seg_index"
        if body.get("value") not in _SEGMENT_MODES:
            return f"value must be one of {sorted(_SEGMENT_MODES)}"
    elif kind == "train_match":
        if not isinstance(body.get("seg_index"), int):
            return "train_match requires integer seg_index"
        if body.get("value") not in _CONFIRMATIONS:
            return f"value must be one of {sorted(_CONFIRMATIONS)}"
    elif kind == "trip_flag":
        if body.get("value") not in _FLAGS:
            return f"value must be one of {sorted(_FLAGS)}"
    elif kind == "trip_reviewed":
        if not isinstance(body.get("value"), bool):
            return "trip_reviewed value must be a boolean"
    else:
        return "type must be one of segment_mode, train_match, trip_flag, trip_reviewed"
    return None


def make_labels_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/labels", status_code=201)
    async def post_label(request: Request) -> dict:
        body = await request.json()
        error = _validate(body)
        if error is not None:
            raise HTTPException(status_code=400, detail=error)
        record = {"received_at": datetime.now(UTC).isoformat(), "payload": body}
        request.app.state.raw_store.append("labels", record)  # primitive first
        applied = request.app.state.runner.store.apply_label(body)
        return {"applied": applied}

    return router
```

In `backend/api/trips.py`, change `list_trips` to accept the filter:

```python
    @router.get("/api/trips")
    async def list_trips(
        request: Request, limit: int = Query(default=50, ge=0),
        reviewed: bool | None = None,
    ) -> list[dict]:
        return request.app.state.runner.store.list_trips(limit=limit, reviewed=reviewed)
```

In `backend/app.py`: `from backend.api.labels import make_labels_router` and
`app.include_router(make_labels_router())` next to the other includes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_labels_api.py backend/tests/test_trips_api.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/api/labels.py backend/api/trips.py backend/app.py backend/tests/test_labels_api.py
git commit -m "feat: labels api with primitive-first archival and reviewed filter"
```

---

### Task 3: Rebuild replays labels

**Files:**
- Modify: `backend/engine/rebuild.py`
- Test: `backend/tests/test_rebuild.py` (add)

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_rebuild.py`:

```python
def test_rebuild_replays_labels(settings):
    _ingest_synthetic_day(settings)
    engine, store, counts = rebuild(settings)
    trip_id = store.list_trips()[0]["trip_id"]
    seg_index = next(
        s["seg_index"] for s in store.get_trip(trip_id)["segments"]
        if s["mode"] == "vehicle"
    )
    # label arrives as a primitive event (as the API would write it)
    RawStore(settings.data_dir).append(
        "labels",
        {"received_at": "2026-06-10T15:00:00+00:00",
         "payload": {"type": "segment_mode", "trip_id": trip_id,
                     "seg_index": seg_index, "value": "train"}},
    )
    engine, store, counts = rebuild(settings)  # derived wiped + rebuilt
    assert counts["labels_applied"] == 1
    seg = next(s for s in store.get_trip(trip_id)["segments"]
               if s["seg_index"] == seg_index)
    assert seg["mode_effective"] == "train"


def test_rebuild_skips_labels_for_vanished_trips(settings):
    _ingest_synthetic_day(settings)
    RawStore(settings.data_dir).append(
        "labels",
        {"received_at": "2026-06-10T15:00:00+00:00",
         "payload": {"type": "trip_flag", "trip_id": "t999999", "value": "ok"}},
    )
    engine, store, counts = rebuild(settings)
    assert counts["labels_skipped"] == 1
    assert counts.get("labels_applied", 0) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_rebuild.py -v`
Expected: FAIL — KeyError 'labels_applied'.

- [ ] **Step 3: Implement in `backend/engine/rebuild.py`**

After the replay loop (and before the final log/return), add:

```python
    # Replay label events last — label supremacy over heuristics and matches.
    rel = q.events("labels")
    label_rows = q.sql(
        "SELECT CAST(payload AS VARCHAR) FROM rel ORDER BY received_at", rel=rel
    ).fetchall()
    for (payload_text,) in label_rows:
        if store.apply_label(json.loads(payload_text)):
            counts["labels_applied"] += 1
        else:
            counts["labels_skipped"] += 1
```

NOTE: `q` is the EventQuery created earlier in rebuild — reuse it. The
`events("labels")` relation is empty-schema-safe when the stream has never
been written (Phase 1 behavior), so no existence check is needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_rebuild.py -v`
Expected: all PASS (existing rebuild tests unaffected — Counter keys appear
only when incremented).

- [ ] **Step 5: Commit**

```bash
git add backend/engine/rebuild.py backend/tests/test_rebuild.py
git commit -m "feat: rebuild replays archived label events"
```

---

### Task 4: SvelteKit scaffold

**Files:**
- Create: `frontend/package.json`, `frontend/svelte.config.js`,
  `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/src/app.html`,
  `frontend/src/app.d.ts`, `frontend/src/routes/+layout.svelte`,
  `frontend/src/routes/+layout.ts`, `frontend/src/routes/+page.svelte`
- Modify: `.gitignore`

No TDD for scaffolding; verification is `npm run check` + `npm run build`.

- [ ] **Step 1: Write the files**

```json
// frontend/package.json
{
  "name": "commute-tracker-frontend",
  "private": true,
  "version": "0.0.1",
  "type": "module",
  "scripts": {
    "dev": "vite dev",
    "build": "vite build",
    "preview": "vite preview",
    "check": "svelte-kit sync && svelte-check --tsconfig ./tsconfig.json",
    "test": "vitest run"
  },
  "devDependencies": {
    "@sveltejs/adapter-static": "^3.0.0",
    "@sveltejs/kit": "^2.0.0",
    "@sveltejs/vite-plugin-svelte": "^5.0.0",
    "svelte": "^5.0.0",
    "svelte-check": "^4.0.0",
    "typescript": "^5.5.0",
    "vite": "^6.0.0",
    "vitest": "^3.0.0"
  },
  "dependencies": {
    "maplibre-gl": "^5.0.0"
  }
}
```

```js
// frontend/svelte.config.js
import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    adapter: adapter({ fallback: 'index.html' }),
  },
};

export default config;
```

```ts
// frontend/vite.config.ts
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    proxy: {
      '/api': 'http://localhost:8090',
      '/ingest': 'http://localhost:8090',
    },
  },
});
```

```json
// frontend/tsconfig.json
{
  "extends": "./.svelte-kit/tsconfig.json",
  "compilerOptions": {
    "allowJs": true,
    "checkJs": true,
    "esModuleInterop": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "skipLibCheck": true,
    "sourceMap": true,
    "strict": true
  }
}
```

```html
<!-- frontend/src/app.html -->
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>CommuteTracker</title>
    %sveltekit.head%
  </head>
  <body data-sveltekit-preload-data="hover">
    <div style="display: contents">%sveltekit.body%</div>
  </body>
</html>
```

```ts
// frontend/src/app.d.ts
declare global {
  namespace App {}
}

export {};
```

```ts
// frontend/src/routes/+layout.ts
export const ssr = false;
export const prerender = false;
```

```svelte
<!-- frontend/src/routes/+layout.svelte -->
<script lang="ts">
  let { children } = $props();
</script>

<nav>
  <span class="brand">CommuteTracker</span>
  <span class="placeholder" title="Phase 5">Today</span>
  <a href="/trips">Trips</a>
  <span class="placeholder" title="Phase 5">Optimizer</span>
  <span class="placeholder" title="Phase 6">Trends</span>
  <span class="placeholder" title="Phase 6">Health</span>
</nav>
<main>
  {@render children()}
</main>

<style>
  :global(body) {
    margin: 0;
    font-family: system-ui, sans-serif;
    background: #fafafa;
    color: #222;
  }
  nav {
    display: flex;
    gap: 1.25rem;
    align-items: center;
    padding: 0.75rem 1.25rem;
    background: #1a1a2e;
    color: #eee;
  }
  .brand {
    font-weight: 700;
    margin-right: 1rem;
  }
  nav a {
    color: #fff;
    text-decoration: none;
    font-weight: 600;
  }
  .placeholder {
    color: #777;
    cursor: default;
  }
  main {
    max-width: 1100px;
    margin: 1.5rem auto;
    padding: 0 1rem;
  }
</style>
```

```svelte
<!-- frontend/src/routes/+page.svelte -->
<script lang="ts">
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  onMount(() => goto('/trips', { replaceState: true }));
</script>
```

Add to `.gitignore`:

```
node_modules/
frontend/build/
frontend/.svelte-kit/
frontend/test-results/
frontend/playwright-report/
```

- [ ] **Step 2: Install and verify**

Run: `cd frontend && npm install && npm run check && npm run build`
Expected: install succeeds (commits package-lock.json), `svelte-check` finds
0 errors, build emits `frontend/build/index.html`. (A temporary
`src/routes/trips/+page.svelte` placeholder is NOT needed — the redirect
target 404s client-side until Task 6, which is fine for the build.)

- [ ] **Step 3: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/svelte.config.js \
        frontend/vite.config.ts frontend/tsconfig.json frontend/src/ .gitignore
git commit -m "feat: sveltekit spa scaffold with app shell"
```

---

### Task 5: FastAPI SPA serving + Docker + CI

**Files:**
- Modify: `backend/app.py`
- Modify: `Dockerfile.backend`
- Modify: `.github/workflows/ci.yml`
- Test: `backend/tests/test_spa_serving.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_spa_serving.py
import dataclasses

from fastapi.testclient import TestClient

from backend.app import create_app


def _app_with_build(settings, tmp_path):
    build = tmp_path / "fake_build"
    build.mkdir()
    (build / "index.html").write_text("<html><body>SPA</body></html>")
    (build / "app.js").write_text("console.log('hi')")
    s = dataclasses.replace(settings, frontend_build_dir=build)
    return create_app(s)


def test_serves_static_file(settings, tmp_path):
    app = _app_with_build(settings, tmp_path)
    with TestClient(app) as c:
        resp = c.get("/app.js")
    assert resp.status_code == 200
    assert "console.log" in resp.text


def test_deep_link_falls_back_to_index(settings, tmp_path):
    app = _app_with_build(settings, tmp_path)
    with TestClient(app) as c:
        resp = c.get("/trips/t12345")
    assert resp.status_code == 200
    assert "SPA" in resp.text


def test_api_routes_still_win(settings, tmp_path):
    app = _app_with_build(settings, tmp_path)
    with TestClient(app) as c:
        resp = c.get("/api/trips")
    assert resp.status_code == 200
    assert resp.json() == []


def test_no_build_dir_no_spa(settings):
    app = create_app(settings)  # default: no frontend build
    with TestClient(app) as c:
        assert c.get("/trips/t1").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_spa_serving.py -v`
Expected: FAIL — Settings has no frontend_build_dir.

- [ ] **Step 3: Implement**

In `backend/config.py` add a Settings field (after the source fields):

```python
    frontend_build_dir: Path | None = None  # unset = no SPA serving
```

and in `load_settings()`:

```python
        frontend_build_dir=(
            Path(os.environ.get("CT_FRONTEND_BUILD_DIR"))
            if os.environ.get("CT_FRONTEND_BUILD_DIR")
            else (
                _default_build
                if (_default_build := Path(__file__).resolve().parent.parent
                    / "frontend" / "build").is_dir()
                else None
            )
        ),
```

(If the walrus-in-conditional reads poorly, compute `_default_build` on a
plain line above the `return Settings(...)` call — clarity over cleverness;
behavior: env var wins, else `frontend/build` next to the repo root if it
exists, else None.)

In `backend/app.py`, at the END of `create_app` (after all
`include_router` calls, before `return app`):

```python
    if settings.frontend_build_dir is not None and settings.frontend_build_dir.is_dir():
        build_dir = settings.frontend_build_dir

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            target = build_dir / path
            if path and target.is_file():
                return FileResponse(target)
            return FileResponse(build_dir / "index.html")
```

(import `from fastapi.responses import FileResponse`). Registered last, so
every earlier route — ingest, api, health — wins; only unmatched GETs fall
through to the SPA.

Also update `backend/tests/conftest.py` settings fixture: nothing needed —
the new field defaults to None... BUT `load_settings` default-discovers
`frontend/build` if present, which test fixtures don't use (they construct
Settings directly). Confirm the fixture constructs Settings explicitly
(it does) — no change.

**Dockerfile.backend** — make it multi-stage (replace the whole file):

```dockerfile
FROM node:22-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# scripts/ is copied for migrate_legacy_raw only; other scripts import the
# legacy src/ package and will not run in this image
COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY --from=frontend /fe/build ./frontend/build
RUN pip install --no-cache-dir \
    "fastapi>=0.115" "uvicorn>=0.34" "polars>=1.0" "duckdb>=1.0" \
    "boto3>=1.35" "httpx>=0.27" "pyarrow>=18.0"

# No package install: backend/ is imported from the workdir
ENV PYTHONPATH=/app

EXPOSE 8090
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8090"]
```

**.github/workflows/ci.yml** — add a frontend job alongside lint/test:

```yaml
  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - name: Install
        run: npm ci
        working-directory: frontend
      - name: Type check
        run: npm run check
        working-directory: frontend
      - name: Unit tests
        run: npm test
        working-directory: frontend
      - name: Build
        run: npm run build
        working-directory: frontend
```

(NOTE: `npm test` requires at least one vitest test — Task 6 adds it. To keep
CI green for THIS commit, use `npm test -- --passWithNoTests` in the workflow,
or reorder: acceptable to land the CI job with `--passWithNoTests` and drop
the flag in Task 6. Use the flag.)

- [ ] **Step 4: Run tests + container build**

Run: `pytest backend/tests -q`
Expected: all PASS.
Run: `podman build -f Dockerfile.backend -t ct-backend:p4 . 2>&1 | tail -3` (if podman is available; otherwise note CI will verify)
Expected: builds; the image now contains frontend/build.

- [ ] **Step 5: Commit**

```bash
git add backend/config.py backend/app.py Dockerfile.backend .github/workflows/ci.yml \
        backend/tests/test_spa_serving.py
git commit -m "feat: spa serving, multi-stage docker build, frontend ci"
```

---

### Task 6: API client, trace builder, trips list page

**Files:**
- Create: `frontend/src/lib/api.ts`, `frontend/src/lib/trace.ts`,
  `frontend/src/lib/trace.test.ts`, `frontend/src/routes/trips/+page.svelte`,
  `frontend/src/routes/trips/+page.ts`
- Modify: `.github/workflows/ci.yml` (drop `--passWithNoTests` if used)

- [ ] **Step 1: Write the vitest test (TDD for the pure logic)**

```ts
// frontend/src/lib/trace.test.ts
import { describe, expect, it } from 'vitest';
import { buildSegmentFeatures, MODE_COLORS } from './trace';

const points = [
  { ts: '2026-06-10T14:00:00+00:00', lat: 40.7, lon: -74.4 },
  { ts: '2026-06-10T14:01:00+00:00', lat: 40.71, lon: -74.4 },
  { ts: '2026-06-10T14:02:00+00:00', lat: 40.72, lon: -74.4 },
  { ts: '2026-06-10T14:03:00+00:00', lat: 40.73, lon: -74.4 },
];
const segments = [
  { seg_index: 0, mode_effective: 'walk', start_ts: '2026-06-10T14:00:00+00:00', end_ts: '2026-06-10T14:01:00+00:00' },
  { seg_index: 1, mode_effective: 'vehicle', start_ts: '2026-06-10T14:01:00+00:00', end_ts: '2026-06-10T14:03:00+00:00' },
];

describe('buildSegmentFeatures', () => {
  it('builds one feature per segment with mode colors', () => {
    const features = buildSegmentFeatures(points, segments);
    expect(features).toHaveLength(2);
    expect(features[0].properties.color).toBe(MODE_COLORS.walk);
    expect(features[1].properties.color).toBe(MODE_COLORS.vehicle);
    expect(features[0].geometry.coordinates).toEqual([
      [-74.4, 40.7],
      [-74.4, 40.71],
    ]);
  });

  it('shares boundary points between adjacent segments', () => {
    const features = buildSegmentFeatures(points, segments);
    expect(features[1].geometry.coordinates[0]).toEqual([-74.4, 40.71]);
  });

  it('handles unknown modes with a fallback color', () => {
    const features = buildSegmentFeatures(points, [
      { ...segments[0], mode_effective: 'mystery' },
    ]);
    expect(features[0].properties.color).toBe(MODE_COLORS.fallback);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run`
Expected: FAIL — cannot resolve './trace'.

- [ ] **Step 3: Implement**

```ts
// frontend/src/lib/trace.ts
// Pure GeoJSON construction — testable without a map.

export const MODE_COLORS: Record<string, string> = {
  walk: '#2e7d32',
  vehicle: '#1565c0',
  train: '#6a1b9a',
  stationary: '#9e9e9e',
  fallback: '#e65100',
};

export interface TracePoint {
  ts: string;
  lat: number;
  lon: number;
}

export interface TraceSegment {
  seg_index: number;
  mode_effective: string;
  start_ts: string;
  end_ts: string;
}

export function buildSegmentFeatures(points: TracePoint[], segments: TraceSegment[]) {
  return segments.map((seg) => {
    // ISO-8601 strings with identical offsets compare lexicographically
    const coords = points
      .filter((p) => p.ts >= seg.start_ts && p.ts <= seg.end_ts)
      .map((p) => [p.lon, p.lat]);
    return {
      type: 'Feature' as const,
      properties: {
        seg_index: seg.seg_index,
        mode: seg.mode_effective,
        color: MODE_COLORS[seg.mode_effective] ?? MODE_COLORS.fallback,
      },
      geometry: { type: 'LineString' as const, coordinates: coords },
    };
  });
}
```

```ts
// frontend/src/lib/api.ts
export interface TripSummary {
  trip_id: string;
  start_ts: string;
  end_ts: string;
  duration_s: number;
  distance_m: number;
  direction: string;
  start_geofence: string | null;
  end_geofence: string | null;
  reviewed: boolean;
}

export interface TrainInfo {
  seg_index: number;
  source: string;
  gtfs_trip_id: string;
  route_name: string;
  headsign: string;
  board_stop: string;
  alight_stop: string;
  scheduled_dep_s: number;
  delta_s: number;
}

export interface Segment {
  seg_index: number;
  mode: string;
  mode_effective: string;
  mode_source: string;
  start_ts: string;
  end_ts: string;
  duration_s: number;
  distance_m: number;
  point_count: number;
}

export interface ItineraryLeg {
  mode: string;
  start_ts: string;
  end_ts: string;
  duration_s: number;
  distance_m: number;
  train: TrainInfo | null;
  confirmation: string | null;
}

export interface TripDetail {
  trip: TripSummary & { flag: string | null };
  segments: Segment[];
  points: { ts: string; lat: number; lon: number; speed_mps: number }[];
  itinerary: ItineraryLeg[];
}

export type LabelEvent = {
  type: 'segment_mode' | 'train_match' | 'trip_flag' | 'trip_reviewed';
  trip_id: string;
  seg_index?: number;
  value: string | boolean;
};

async function check(resp: Response): Promise<Response> {
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return resp;
}

export async function getTrips(
  fetchFn: typeof fetch,
  reviewed?: boolean,
): Promise<TripSummary[]> {
  const qs = reviewed === undefined ? '' : `?reviewed=${reviewed}`;
  return (await check(await fetchFn(`/api/trips${qs}`))).json();
}

export async function getTrip(fetchFn: typeof fetch, id: string): Promise<TripDetail> {
  return (await check(await fetchFn(`/api/trips/${id}`))).json();
}

export async function postLabel(label: LabelEvent): Promise<{ applied: boolean }> {
  const resp = await fetch('/api/labels', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(label),
  });
  return (await check(resp)).json();
}
```

```ts
// frontend/src/routes/trips/+page.ts
import { getTrips } from '$lib/api';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => {
  return { trips: await getTrips(fetch) };
};
```

```svelte
<!-- frontend/src/routes/trips/+page.svelte -->
<script lang="ts">
  import { getTrips, type TripSummary } from '$lib/api';

  let { data } = $props();
  let trips: TripSummary[] = $state(data.trips);
  let unreviewedOnly = $state(false);

  async function toggleFilter() {
    unreviewedOnly = !unreviewedOnly;
    trips = await getTrips(fetch, unreviewedOnly ? false : undefined);
  }

  function fmtTime(iso: string): string {
    return new Date(iso).toLocaleString();
  }
  function fmtKm(m: number): string {
    return (m / 1000).toFixed(1) + ' km';
  }
  function fmtMin(s: number): string {
    return Math.round(s / 60) + ' min';
  }
</script>

<h1>Trips</h1>
<label class="filter">
  <input type="checkbox" checked={unreviewedOnly} onchange={toggleFilter} />
  Unreviewed only
</label>

{#if trips.length === 0}
  <p>No trips{unreviewedOnly ? ' awaiting review' : ' yet'}.</p>
{:else}
  <table>
    <thead>
      <tr><th>Start</th><th>Direction</th><th>Duration</th><th>Distance</th><th>Status</th></tr>
    </thead>
    <tbody>
      {#each trips as t (t.trip_id)}
        <tr>
          <td><a href="/trips/{t.trip_id}">{fmtTime(t.start_ts)}</a></td>
          <td>{t.direction}</td>
          <td>{fmtMin(t.duration_s)}</td>
          <td>{fmtKm(t.distance_m)}</td>
          <td>{t.reviewed ? '✓ reviewed' : '· unreviewed'}</td>
        </tr>
      {/each}
    </tbody>
  </table>
{/if}

<style>
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #e0e0e0; }
  .filter { display: inline-flex; gap: 0.4rem; align-items: center; margin-bottom: 1rem; }
</style>
```

Drop `--passWithNoTests` from ci.yml's frontend test step if it was added.

- [ ] **Step 4: Verify**

Run: `cd frontend && npx vitest run && npm run check && npm run build`
Expected: 3 vitest tests pass; svelte-check clean; build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/ frontend/src/routes/trips/ .github/workflows/ci.yml
git commit -m "feat: api client, trace builder, trips list page"
```

---

### Task 7: Map + workbench detail page

**Files:**
- Create: `frontend/src/lib/Map.svelte`,
  `frontend/src/routes/trips/[id]/+page.svelte`,
  `frontend/src/routes/trips/[id]/+page.ts`

No vitest here (map render is covered by the Playwright smoke + manual);
verification is svelte-check + build + manual dev run.

- [ ] **Step 1: Implement**

```ts
// frontend/src/routes/trips/[id]/+page.ts
import { getTrip } from '$lib/api';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, params }) => {
  return { detail: await getTrip(fetch, params.id) };
};
```

```svelte
<!-- frontend/src/lib/Map.svelte -->
<script lang="ts">
  import maplibregl from 'maplibre-gl';
  import 'maplibre-gl/dist/maplibre-gl.css';
  import { onMount } from 'svelte';
  import { buildSegmentFeatures, type TracePoint, type TraceSegment } from './trace';

  let { points, segments }: { points: TracePoint[]; segments: TraceSegment[] } = $props();
  let container: HTMLDivElement;

  onMount(() => {
    const features = buildSegmentFeatures(points, segments);
    const lats = points.map((p) => p.lat);
    const lons = points.map((p) => p.lon);
    const bounds: [[number, number], [number, number]] = [
      [Math.min(...lons), Math.min(...lats)],
      [Math.max(...lons), Math.max(...lats)],
    ];
    const map = new maplibregl.Map({
      container,
      style: {
        version: 8,
        sources: {
          osm: {
            type: 'raster',
            tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
            tileSize: 256,
            attribution: '© OpenStreetMap contributors',
          },
        },
        layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
      },
      bounds,
      fitBoundsOptions: { padding: 48 },
      attributionControl: { compact: true },
    });
    map.on('load', () => {
      map.addSource('trace', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features },
      });
      map.addLayer({
        id: 'trace',
        type: 'line',
        source: 'trace',
        paint: { 'line-color': ['get', 'color'], 'line-width': 4 },
        layout: { 'line-cap': 'round', 'line-join': 'round' },
      });
    });
    return () => map.remove();
  });
</script>

<div bind:this={container} class="map" data-testid="trip-map"></div>

<style>
  .map { height: 420px; width: 100%; border-radius: 8px; }
</style>
```

```svelte
<!-- frontend/src/routes/trips/[id]/+page.svelte -->
<script lang="ts">
  import Map from '$lib/Map.svelte';
  import SegmentPanel from '$lib/SegmentPanel.svelte';
  import { getTrip, type TripDetail } from '$lib/api';

  let { data } = $props();
  let detail: TripDetail = $state(data.detail);

  async function refresh() {
    detail = await getTrip(fetch, detail.trip.trip_id);
  }

  function fmtTime(iso: string): string {
    return new Date(iso).toLocaleString();
  }
</script>

<a href="/trips">← Trips</a>
<h1>
  {fmtTime(detail.trip.start_ts)}
  <small>{detail.trip.direction}</small>
  {#if detail.trip.flag === 'phantom'}<span class="flag">phantom</span>{/if}
  {#if detail.trip.reviewed}<span class="reviewed">✓ reviewed</span>{/if}
</h1>

{#key detail.trip.trip_id}
  <Map points={detail.points} segments={detail.segments} />
{/key}

<SegmentPanel {detail} onchange={refresh} />

<style>
  h1 small { color: #777; font-weight: 400; margin-left: 0.5rem; }
  .flag { background: #ffebee; color: #c62828; border-radius: 4px; padding: 0.1rem 0.5rem; font-size: 0.9rem; margin-left: 0.5rem; }
  .reviewed { background: #e8f5e9; color: #2e7d32; border-radius: 4px; padding: 0.1rem 0.5rem; font-size: 0.9rem; margin-left: 0.5rem; }
</style>
```

NOTE: SegmentPanel.svelte arrives in Task 8 — to keep this commit building,
create it now as the minimal version Task 8 replaces:

```svelte
<!-- frontend/src/lib/SegmentPanel.svelte (minimal; Task 8 replaces) -->
<script lang="ts">
  import type { TripDetail } from './api';
  let { detail, onchange }: { detail: TripDetail; onchange: () => void } = $props();
</script>

<p>{detail.segments.length} segments</p>
```

- [ ] **Step 2: Verify**

Run: `cd frontend && npm run check && npm run build && npx vitest run`
Expected: clean.

Optional manual check (worth doing once): `uvicorn backend.app:app --port 8090`
in one terminal with some ingested synth data, `npm run dev` in another, open
http://localhost:5173/trips and click into a trip — the map shows the colored
trace.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/Map.svelte frontend/src/lib/SegmentPanel.svelte frontend/src/routes/trips/
git commit -m "feat: maplibre trace map and trip workbench page"
```

---

### Task 8: Label actions + review flow

**Files:**
- Modify: `frontend/src/lib/SegmentPanel.svelte` (full replacement)

- [ ] **Step 1: Implement (full file replacement)**

```svelte
<!-- frontend/src/lib/SegmentPanel.svelte -->
<script lang="ts">
  import { postLabel, type TripDetail } from './api';
  import { MODE_COLORS } from './trace';

  let { detail, onchange }: { detail: TripDetail; onchange: () => void } = $props();
  let busy = $state(false);

  const MODES = ['stationary', 'walk', 'vehicle', 'train'];

  async function label(event: Parameters<typeof postLabel>[0]) {
    busy = true;
    try {
      await postLabel(event);
      onchange();
    } finally {
      busy = false;
    }
  }

  function fmtClock(iso: string): string {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  function fmtMin(s: number): string {
    return Math.round(s / 60) + ' min';
  }
  function fmtDelta(s: number): string {
    const m = Math.round(Math.abs(s) / 60);
    return s >= 0 ? `${m} min after sched` : `${m} min before sched`;
  }
</script>

<section class="segments" data-testid="segment-panel">
  <h2>Segments</h2>
  {#each detail.segments as seg, i (seg.seg_index)}
    {@const leg = detail.itinerary[i]}
    <div class="segment" data-testid="segment-{seg.seg_index}">
      <span class="swatch" style="background: {MODE_COLORS[seg.mode_effective] ?? MODE_COLORS.fallback}"></span>
      <span class="time">{fmtClock(seg.start_ts)}–{fmtClock(seg.end_ts)}</span>
      <span class="dur">{fmtMin(seg.duration_s)}</span>
      <select
        value={seg.mode_effective}
        disabled={busy}
        data-testid="mode-select-{seg.seg_index}"
        onchange={(e) =>
          label({
            type: 'segment_mode',
            trip_id: detail.trip.trip_id,
            seg_index: seg.seg_index,
            value: e.currentTarget.value,
          })}
      >
        {#each MODES as m (m)}
          <option value={m}>{m}{m === seg.mode ? ' (auto)' : ''}</option>
        {/each}
      </select>
      {#if seg.mode_source === 'label'}<span class="labeled">labeled</span>{/if}

      {#if leg?.train}
        <div class="train">
          🚆 {leg.train.route_name} → {leg.train.headsign}
          ({leg.train.board_stop} → {leg.train.alight_stop}, {fmtDelta(leg.train.delta_s)})
          {#if leg.confirmation === 'confirmed'}
            <span class="ok">✓ confirmed</span>
          {:else if leg.confirmation === 'wrong'}
            <span class="bad">✗ marked wrong</span>
          {:else}
            <button
              disabled={busy}
              data-testid="confirm-train-{seg.seg_index}"
              onclick={() =>
                label({
                  type: 'train_match',
                  trip_id: detail.trip.trip_id,
                  seg_index: seg.seg_index,
                  value: 'confirmed',
                })}>✓ right train</button>
            <button
              disabled={busy}
              onclick={() =>
                label({
                  type: 'train_match',
                  trip_id: detail.trip.trip_id,
                  seg_index: seg.seg_index,
                  value: 'wrong',
                })}>✗ wrong train</button>
          {/if}
        </div>
      {/if}
    </div>
  {/each}
</section>

<section class="trip-actions">
  <button
    disabled={busy || detail.trip.reviewed}
    data-testid="mark-reviewed"
    onclick={() =>
      label({ type: 'trip_reviewed', trip_id: detail.trip.trip_id, value: true })}
  >
    {detail.trip.reviewed ? '✓ Reviewed' : 'Mark reviewed'}
  </button>
  {#if detail.trip.flag === 'phantom'}
    <button
      disabled={busy}
      onclick={() => label({ type: 'trip_flag', trip_id: detail.trip.trip_id, value: 'ok' })}
    >Unflag phantom</button>
  {:else}
    <button
      disabled={busy}
      onclick={() =>
        label({ type: 'trip_flag', trip_id: detail.trip.trip_id, value: 'phantom' })}
    >Flag as phantom</button>
  {/if}
</section>

<style>
  .segments { margin-top: 1.5rem; }
  .segment {
    display: flex; flex-wrap: wrap; gap: 0.6rem; align-items: center;
    padding: 0.6rem 0.4rem; border-bottom: 1px solid #eee;
  }
  .swatch { width: 14px; height: 14px; border-radius: 3px; display: inline-block; }
  .time { font-variant-numeric: tabular-nums; }
  .dur { color: #777; }
  .labeled { font-size: 0.8rem; color: #6a1b9a; }
  .train { flex-basis: 100%; padding-left: 1.6rem; color: #333; }
  .ok { color: #2e7d32; }
  .bad { color: #c62828; }
  .trip-actions { margin-top: 1.25rem; display: flex; gap: 0.75rem; }
  button { cursor: pointer; }
</style>
```

- [ ] **Step 2: Verify**

Run: `cd frontend && npm run check && npm run build && npx vitest run`
Expected: clean.

Manual verification (REQUIRED this time — this is the core deliverable):
run uvicorn with ingested synth data + a fixture GTFS snapshot (reuse the
pattern from backend/tests/test_itinerary_api.py to seed, or simply ingest
synth points), `npm run dev`, exercise: change a segment mode → page
refreshes with "labeled" badge; mark reviewed → trips list shows ✓; flag
phantom → badge appears. Report what you observed.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/SegmentPanel.svelte
git commit -m "feat: segment labeling actions and review flow"
```

---

### Task 9: Playwright smoke test (descopable)

**Files:**
- Create: `frontend/playwright.config.ts`, `frontend/e2e/workbench.spec.ts`,
  `backend/tests/e2e_seed.py`
- Modify: `frontend/package.json` (devDependency `@playwright/test`, script
  `e2e`), `.github/workflows/ci.yml` (e2e steps in the frontend job)

- [ ] **Step 1: Seed script**

```python
# backend/tests/e2e_seed.py
"""Seed a data dir for the Playwright smoke test: one synthetic commute.

Usage: python -m backend.tests.e2e_seed /tmp/e2e-data
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

from backend.storage.raw import RawStore
from backend.tests.synth import commute


def seed(data_dir: Path) -> None:
    store = RawStore(data_dir)
    pts, _, _ = commute()
    for i, pt in enumerate(pts):
        store.append(
            "owntracks",
            {"received_at": f"2026-06-09T{i // 3600:02d}:{(i // 60) % 60:02d}:{i % 60:02d}+00:00",
             "user": "e2e", "device": "e2e",
             "payload": {"_type": "location", "tst": pt.ts, "lat": pt.lat,
                         "lon": pt.lon, "acc": pt.accuracy_m}},
        )
    print(f"seeded {len(pts)} points into {data_dir} at {datetime.now(UTC).isoformat()}")


if __name__ == "__main__":
    seed(Path(sys.argv[1]))
```

- [ ] **Step 2: Playwright config + spec**

```ts
// frontend/playwright.config.ts
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: 'e2e',
  use: { baseURL: 'http://localhost:8093' },
  webServer: {
    command:
      'sh -c "rm -rf /tmp/ct-e2e-data && cd .. && python -m backend.tests.e2e_seed /tmp/ct-e2e-data && CT_DATA_DIR=/tmp/ct-e2e-data CT_FRONTEND_BUILD_DIR=$(pwd)/frontend/build uvicorn backend.app:app --port 8093"',
    url: 'http://localhost:8093/api/health/ingestion',
    timeout: 60_000,
    reuseExistingServer: false,
  },
});
```

```ts
// frontend/e2e/workbench.spec.ts
import { expect, test } from '@playwright/test';

test('labeling workbench end to end', async ({ page }) => {
  await page.goto('/trips');
  await expect(page.getByRole('heading', { name: 'Trips' })).toBeVisible();
  // the seeded commute produced exactly one trip
  await page.getByRole('link', { name: /\d/ }).first().click();
  await expect(page.getByTestId('trip-map')).toBeVisible();
  await expect(page.getByTestId('segment-panel')).toBeVisible();

  // change the first segment's mode to train
  const select = page.getByTestId(/mode-select-/).first();
  await select.selectOption('train');
  await expect(page.getByText('labeled').first()).toBeVisible();

  // mark reviewed
  await page.getByTestId('mark-reviewed').click();
  await expect(page.getByText('✓ Reviewed')).toBeVisible();

  // list reflects review state
  await page.goto('/trips');
  await expect(page.getByText('✓ reviewed')).toBeVisible();
});
```

package.json additions: `"@playwright/test": "^1.50.0"` in devDependencies,
script `"e2e": "playwright test"`. Run `npm install` to update the lockfile.

ci.yml frontend job additions (after the Build step):

```yaml
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install backend for e2e server
        run: pip install -e ".[dev]"
      - name: Install Playwright browsers
        run: npx playwright install --with-deps chromium
        working-directory: frontend
      - name: E2E smoke
        run: npm run e2e
        working-directory: frontend
```

- [ ] **Step 3: Run locally**

Run: `cd frontend && npm install && npx playwright install chromium && npm run build && npm run e2e`
Expected: 1 test passes (webServer seeds + boots uvicorn serving the build).

**Descope clause:** if the e2e proves unrunnable in this environment or
clearly CI-hostile after TWO debugging attempts, mark the spec
`test.skip(!!process.env.CI, ...)`, keep it runnable locally, note the
decision in the commit message, and move on. Do not burn hours on browser
plumbing.

- [ ] **Step 4: Commit**

```bash
git add frontend/playwright.config.ts frontend/e2e/ frontend/package.json \
        frontend/package-lock.json backend/tests/e2e_seed.py .github/workflows/ci.yml
git commit -m "test: playwright smoke for the labeling workbench"
```

---

### Task 10: Docs + final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: README** — extend the "Rewrite backend" section:
- "### Frontend" subsection: SvelteKit SPA in `frontend/`; dev loop
  (`uvicorn backend.app:app --port 8090` + `cd frontend && npm run dev`,
  Vite proxies /api); production build is baked into the Docker image and
  served by FastAPI at `/`; trips list + map workbench with labeling.
- Labels API: `POST /api/labels` (segment_mode / train_match / trip_flag /
  trip_reviewed) — events are primitive data, archived like GPS, replayed on
  rebuild; `GET /api/trips?reviewed=false` for the review queue.

- [ ] **Step 2: Final verification**

Run: `ruff format backend/ && ruff check src/ tests/ backend/ && ruff format --check src/ tests/ backend/ && pytest --tb=short -q`
Expected: clean; ~400 tests pass.
Run: `cd frontend && npm run check && npx vitest run && npm run build`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: frontend dev loop and labels api"
```

---

## Verification at phase end

1. Full python suite green; ruff clean; svelte-check clean; vitest green.
2. Playwright smoke green locally (and in CI unless descoped with rationale).
3. Container smoke (controller, podman): build the multi-stage image, run it,
   `GET /` returns the SPA index, `GET /api/trips` returns `[]`, ingest a
   synthetic commute, `GET /trips/<id>` deep link returns the SPA (200).
4. `gh run list` green after push (including the new frontend CI job).

---

### Task 11 (added after live API validation): NJT token-auth source strategy

**Validated against the live API 2026-06-11:** NJ Transit's RailData API has no
authenticated-URL form. The real shape (probed with the user's credentials):

- `POST https://raildata.njtransit.com/api/GTFSRT/getToken` — multipart form
  fields `username`, `password` → `{"UserToken": "..."}` on success,
  `{"errorMessage": "..."}` with HTTP 500 on failure (observed:
  `"Missing user account."` until the portal provisions API access).
- Data endpoints (`POST .../api/GTFSRT/getGTFS|getTripUpdates|getAlerts`) —
  multipart form field `token`; missing/invalid token → HTTP 500
  `{"errorMessage": "Missing token."}`.
- Tokens are rate-limited per day → the token MUST be cached in memory AND
  persisted to disk (`<data_dir>/njt_token.txt`) so restarts don't burn quota.

**Files:**
- Modify: `backend/config.py` (replace the three `njt_*_url` settings with
  `njt_username`/`njt_password` + `njt_api_base`), `backend/tests/test_config.py`
- Create: `backend/sources/njt.py`
- Modify: `backend/sources/framework.py`, `backend/sources/poller.py`,
  `backend/health/sources.py` (registry covers NJT specs)
- Test: `backend/tests/test_sources_njt.py`
- Modify: `README.md` (NJT section: username/password env vars, provisioning note)

**Settings changes** (remove `njt_gtfs_url`, `njt_rt_tripupdates_url`,
`njt_rt_alerts_url` — they shipped in Phase 3 but were never usable; update
`test_config.py` accordingly):

```python
    njt_username: str | None = None        # CT_NJT_USERNAME — unset = NJT disabled
    njt_password: str | None = None        # CT_NJT_PASSWORD
    njt_api_base: str = "https://raildata.njtransit.com/api/GTFSRT"  # CT_NJT_API_BASE (tests override)
```

**`backend/sources/njt.py`** — complete implementation:

```python
"""NJ Transit RailData GTFS-RT source: token-exchange auth.

POST getToken (multipart username/password) -> {"UserToken": ...}; data
endpoints take a multipart `token` field. Tokens are daily-rate-limited, so
the manager caches in memory AND persists to <data_dir>/njt_token.txt; it
re-exchanges only when an endpoint reports a token problem."""

import base64
import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from backend.storage.raw import RawStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NjtSpec:
    name: str          # raw stream name, e.g. "gtfs_njt"
    endpoint: str      # e.g. "getGTFS"
    interval_s: float


class NjtTokenManager:
    def __init__(self, api_base: str, username: str, password: str, data_dir: Path):
        self._api_base = api_base.rstrip("/")
        self._username = username
        self._password = password
        self._path = data_dir / "njt_token.txt"
        self._token: str | None = (
            self._path.read_text().strip() if self._path.exists() else None
        ) or None

    async def token(self, client: httpx.AsyncClient) -> str | None:
        if self._token:
            return self._token
        return await self.refresh(client)

    async def refresh(self, client: httpx.AsyncClient) -> str | None:
        try:
            resp = await client.post(
                f"{self._api_base}/getToken",
                files={"username": (None, self._username),
                       "password": (None, self._password)},
                timeout=30.0,
            )
            data = resp.json()
            token = data.get("UserToken")
            if token:
                self._token = token
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(token)
                return token
            log.warning("njt getToken failed: %s", data.get("errorMessage"))
        except Exception:
            log.exception("njt getToken request failed")
        return None


def njt_specs_from_settings(settings) -> list[NjtSpec]:
    if not (settings.njt_username and settings.njt_password):
        return []
    return [
        NjtSpec(name="gtfs_njt", endpoint="getGTFS",
                interval_s=settings.gtfs_refresh_interval_s),
        NjtSpec(name="rt_njt_trips", endpoint="getTripUpdates",
                interval_s=settings.source_poll_interval_s),
        NjtSpec(name="rt_njt_alerts", endpoint="getAlerts",
                interval_s=settings.source_poll_interval_s),
    ]


def _is_token_error(resp: httpx.Response) -> bool:
    if resp.status_code != 500:
        return False
    try:
        msg = (resp.json().get("errorMessage") or "").lower()
    except Exception:
        return False
    return "token" in msg


async def fetch_njt_once(
    client: httpx.AsyncClient, manager: NjtTokenManager, spec: NjtSpec,
    store: RawStore, state: dict,
) -> bool:
    received_at = datetime.now(UTC).isoformat()
    url = f"{manager._api_base}/{spec.endpoint}"
    try:
        token = await manager.token(client)
        if token is None:
            payload = {"url": url, "status": None, "error": "no njt token available"}
            store.append(spec.name, {"received_at": received_at, "payload": payload})
            return False
        resp = await client.post(url, files={"token": (None, token)}, timeout=60.0)
        if _is_token_error(resp):
            token = await manager.refresh(client)
            if token is not None:
                resp = await client.post(url, files={"token": (None, token)},
                                         timeout=60.0)
        digest = hashlib.sha256(resp.content).hexdigest()
        if resp.status_code == 200 and state.get(spec.name) == digest:
            payload = {"url": url, "status": resp.status_code, "sha256": digest,
                       "unchanged": True}
        else:
            payload = {"url": url, "status": resp.status_code, "sha256": digest,
                       "b64": base64.b64encode(resp.content).decode("ascii")}
            if resp.status_code == 200:
                state[spec.name] = digest
        ok = resp.status_code == 200
    except Exception as exc:
        payload = {"url": url, "status": None, "error": str(exc)}
        ok = False
    store.append(spec.name, {"received_at": received_at, "payload": payload})
    return ok
```

**Poller wiring:** `poll_source` gains an njt variant (or a generic
`poll(fetch_coro_factory, spec, store)` refactor — implementer's choice, keep
both loop bodies crash-proof). `backend/app.py` lifespan builds ONE
`NjtTokenManager` shared by all three NJT pollers (one token, one quota), only
when `njt_specs_from_settings(settings)` is non-empty.

**Health:** `sources_snapshot` must iterate BOTH `sources_from_settings` and
`njt_specs_from_settings` (names/streams only — the freshness logic is
identical).

**Tests (`backend/tests/test_sources_njt.py`)** — MockTransport replicating
the OBSERVED API shape:

```python
import asyncio
import json

import httpx
import pytest

from backend.sources.njt import (NjtSpec, NjtTokenManager, fetch_njt_once,
                                 njt_specs_from_settings)
from backend.storage.raw import RawStore

API = "https://test-njt.example/api/GTFSRT"


def _transport(state):
    """Replicates raildata.njtransit.com behavior observed 2026-06-11."""

    async def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode(errors="replace")
        if str(request.url).endswith("/getToken"):
            state["token_calls"] = state.get("token_calls", 0) + 1
            if "gooduser" in body:
                return httpx.Response(200, json={"UserToken": f"tok{state['token_calls']}"})
            return httpx.Response(500, json={"errorMessage": "Missing user account."})
        # data endpoint: needs a CURRENT token
        current = f"tok{state.get('token_calls', 0)}"
        if current not in body or state.get("expire_all"):
            return httpx.Response(500, json={"errorMessage": "Invalid token."})
        return httpx.Response(200, content=state.get("body", b"protobytes"))

    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_token_exchange_then_fetch(tmp_path):
    state = {}
    client = httpx.AsyncClient(transport=_transport(state))
    mgr = NjtTokenManager(API, "gooduser", "pw", tmp_path)
    spec = NjtSpec(name="rt_njt_trips", endpoint="getTripUpdates", interval_s=60.0)
    store = RawStore(tmp_path)
    ok = await fetch_njt_once(client, mgr, spec, store, {})
    assert ok is True
    assert state["token_calls"] == 1
    assert (tmp_path / "njt_token.txt").read_text() == "tok1"
    rec = json.loads(next((tmp_path / "raw" / "rt_njt_trips").glob("*.jsonl"))
                     .read_text().splitlines()[0])
    assert rec["payload"]["status"] == 200


@pytest.mark.anyio
async def test_persisted_token_skips_exchange(tmp_path):
    (tmp_path / "njt_token.txt").write_text("tok1")
    state = {"token_calls": 1}  # pretend tok1 was issued earlier
    client = httpx.AsyncClient(transport=_transport(state))
    mgr = NjtTokenManager(API, "gooduser", "pw", tmp_path)
    spec = NjtSpec(name="rt_njt_trips", endpoint="getTripUpdates", interval_s=60.0)
    ok = await fetch_njt_once(client, mgr, spec, RawStore(tmp_path), {})
    assert ok is True
    assert state["token_calls"] == 1  # no new exchange


@pytest.mark.anyio
async def test_invalid_token_triggers_one_refresh(tmp_path):
    (tmp_path / "njt_token.txt").write_text("stale")
    state = {"token_calls": 1}  # current valid token is tok1, ours is "stale"
    client = httpx.AsyncClient(transport=_transport(state))
    mgr = NjtTokenManager(API, "gooduser", "pw", tmp_path)
    spec = NjtSpec(name="rt_njt_trips", endpoint="getTripUpdates", interval_s=60.0)
    ok = await fetch_njt_once(client, mgr, spec, RawStore(tmp_path), {})
    assert ok is True
    assert state["token_calls"] == 2  # exactly one refresh
    assert (tmp_path / "njt_token.txt").read_text() == "tok2"


@pytest.mark.anyio
async def test_unprovisioned_account_archives_error(tmp_path):
    state = {}
    client = httpx.AsyncClient(transport=_transport(state))
    mgr = NjtTokenManager(API, "newuser", "pw", tmp_path)  # not provisioned
    spec = NjtSpec(name="gtfs_njt", endpoint="getGTFS", interval_s=86400.0)
    ok = await fetch_njt_once(client, mgr, spec, RawStore(tmp_path), {})
    assert ok is False
    rec = json.loads(next((tmp_path / "raw" / "gtfs_njt").glob("*.jsonl"))
                     .read_text().splitlines()[0])
    assert rec["payload"]["status"] is None
    assert "token" in rec["payload"]["error"]


def test_specs_gated_on_credentials(tmp_path):
    import dataclasses

    from backend.config import Settings

    base = Settings(data_dir=tmp_path, s3_bucket=None, s3_prefix="x",
                    s3_region=None, passthrough_url=None, archive_hour_utc=6)
    assert njt_specs_from_settings(base) == []
    with_creds = dataclasses.replace(base, njt_username="u", njt_password="p")
    assert [s.name for s in njt_specs_from_settings(with_creds)] == [
        "gtfs_njt", "rt_njt_trips", "rt_njt_alerts"]
```

TDD as usual; full suite + ruff; commit:
`feat: njt token-exchange source strategy with persisted token`

**Production config note for README:** `CT_NJT_USERNAME` + `CT_NJT_PASSWORD`
(from 1Password in the deployment); access requires the RailData API product
to be provisioned on the NJT developer account — until then the source
archives `no njt token available` errors and the health endpoint shows the
failure, which is the designed degraded mode.
