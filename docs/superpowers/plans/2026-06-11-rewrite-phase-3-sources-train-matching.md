# Rewrite Phase 3: Sources + Train Matching — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pluggable external-data-source framework (archive-first, env-URL-gated), GTFS static + realtime poller plugins for PATH and NJ Transit, GTFS schedule tables in the derived store, and a train matcher that attributes rail segments to specific scheduled trains — surfaced as an itinerary in the trip API.

**Architecture:** Every source fetch becomes an *event in the existing Phase-1 raw pipeline* (JSONL → Parquet → S3 → EventQuery): the response body is base64-encoded into the event payload, archived verbatim BEFORE any parsing — parser bugs are recoverable forever by re-parsing the archive. Sources are enabled purely by configuring their URL (no URL = disabled = zero network in tests). Schedule tables live in the derived DuckDB and are re-parsed from the latest archived snapshot during every rebuild — same disposable-derived discipline as trips. The matcher is deterministic given (points, schedule): nearest-stop endpoints + service-day calendar + departure-time tolerance.

**Tech Stack:** Python 3.11, httpx (async fetch), zipfile/csv (GTFS parsing), DuckDB, zoneinfo (America/New_York — GTFS times are agency-local), existing RawStore/Archiver/EventQuery/DerivedStore/TripEngine.

**Spec:** `docs/superpowers/specs/2026-06-10-ground-up-rewrite-design.md` — "Extensibility: pluggable data sources" + "Transit context layer". Real-world facts verified 2026-06-11: NJ Transit GTFS + GTFS-RT require registered credentials (developer.njtransit.com) → NJT plugins are config-gated, the user supplies authenticated URLs; PATH GTFS static is public (Trillium/Transitland mirrors); PATH RT has a public community GTFS-RT feed. RT responses are ARCHIVED ONLY this phase (no protobuf parsing — delay extraction is Phase 5's optimizer work; the point is history starts accumulating now).

---

## File structure

```
backend/
  sources/
    __init__.py
    framework.py    # SourceSpec, sources_from_settings, fetch_once (archive-first)
    poller.py       # per-source asyncio poll loop + lifespan helper
  transit/
    __init__.py
    gtfs.py         # latest_snapshot, parse_gtfs (zip → schedule tables), active_service_ids
    matcher.py      # match_trip: vehicle segments → TrainMatch list
  storage/
    raw.py          # modify: RawStore.streams() discovery
    archive.py      # modify: stream-agnostic (discovery replaces STREAMS const)
    derived.py      # modify: gtfs_* + train_matches DDL, write/read match methods, .con
  health/
    ingestion.py    # modify: backlog via discovery
    sources.py      # per-source freshness snapshot
    routes.py       # modify: GET /api/health/sources
  engine/
    rebuild.py      # modify: parse schedules after truncate; match trips during replay
    runner.py       # modify: match on live trip close
  api/trips.py      # modify: itinerary in trip detail
  config.py         # modify: source URL + interval settings
  app.py            # modify: poller tasks in lifespan
  tests/
    gtfs_fixture.py # synthetic GTFS zip builder (test helper)
    test_sources_framework.py
    test_sources_poller.py
    test_health_sources.py
    test_gtfs_parse.py
    test_service_day.py
    test_matcher.py
    test_itinerary_api.py   # end-to-end
README.md           # modify: sources config, known-good public URLs
```

**Design decisions locked in:**

- **Streams = source names**: `gtfs_path`, `gtfs_njt`, `rt_path`, `rt_njt_trips`, `rt_njt_alerts`. Fetch event envelope: `{"received_at": iso, "payload": {"url", "status", "sha256", "b64"}}`; fetch errors: `{"url", "status": null, "error"}`; unchanged-content polls: `{"url", "status", "sha256", "unchanged": true}` (no b64 — keeps freshness signal without re-archiving a 25 MB zip daily).
- **No baked-in default URLs.** README documents the known-good public ones; unset = disabled. Tests never touch the network.
- **GTFS times** ("HH:MM:SS", may exceed 24:00:00) parse to integer seconds-since-local-midnight. Matching converts segment epoch → America/New_York local seconds + YYYYMMDD service date; for local times before 04:00 the previous service date with `local_s + 86400` is also tried (late-night runs).
- **Rail filter**: `route_type IN (1, 2)` (1 = subway/PATH, 2 = rail/NJT).
- **Matcher thresholds** (module constants): `STOP_RADIUS_M = 500.0`, `DEP_TOLERANCE_S = 900.0`.
- **Schedule tables are truncated with everything else** on rebuild and re-parsed from the archived snapshot — one disposability rule, no special cases.
- **Latest-snapshot matching is a deliberate Phase-3 simplification.** Rebuild
  re-parses only the NEWEST archived GTFS snapshot, so historical trips match
  against today's schedule — the spec's version-in-effect matching (each trip
  vs the schedule active on its date) is deferred; all snapshots are archived,
  so it can be implemented retroactively without data loss.

---

### Task 1: Stream-agnostic archiver + health

**Files:**
- Modify: `backend/storage/raw.py`
- Modify: `backend/storage/archive.py`
- Modify: `backend/health/ingestion.py`
- Test: `backend/tests/test_raw_store.py` (add), `backend/tests/test_archive_local.py` (add)

The archiver and health backlog currently iterate a hardcoded `STREAMS = ("owntracks", "owntracks_malformed")`. Sources add streams dynamically — switch to filesystem discovery.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_raw_store.py`:

```python
def test_streams_discovers_all_stream_dirs(settings):
    store = RawStore(settings.data_dir)
    store.append("owntracks", {"received_at": "2026-06-10T00:00:00+00:00"})
    store.append("rt_path", {"received_at": "2026-06-10T00:00:00+00:00"})
    store.append("owntracks", {"received_at": "2026-06-10T00:00:00+00:00"}, malformed=True)
    assert store.streams() == ["owntracks", "owntracks_malformed", "rt_path"]


def test_streams_empty_when_no_raw_dir(settings):
    assert RawStore(settings.data_dir).streams() == []
```

Add to `backend/tests/test_archive_local.py`:

```python
def test_archiver_covers_dynamic_streams(settings):
    store = RawStore(settings.data_dir)
    store.append("rt_path", {"received_at": "2026-06-08T00:00:00+00:00", "payload": {"x": 1}})
    results = Archiver(settings).run(today="2026-06-10")
    assert [r.stream for r in results] == ["rt_path"]
    assert results[0].ok
    pq = (
        settings.data_dir
        / "archive"
        / "rt_path"
        / "year=2026"
        / "month=06"
        / "day=08"
        / "data.parquet"
    )
    assert pq.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_raw_store.py backend/tests/test_archive_local.py -v`
Expected: FAIL — `RawStore` has no attribute `streams`; archiver returns [] for the unknown stream.

- [ ] **Step 3: Implement**

In `backend/storage/raw.py`, add to `RawStore`:

```python
    def streams(self) -> list[str]:
        """Discover every stream that has ever written raw data."""
        if not self._root.is_dir():
            return []
        return sorted(d.name for d in self._root.iterdir() if d.is_dir())
```

In `backend/storage/archive.py`:
- Delete the `STREAMS` module constant.
- In `Archiver.run`, replace `for stream in STREAMS:` with `for stream in self._store.streams():`.

In `backend/health/ingestion.py`:
- Replace `from backend.storage.archive import STREAMS` with nothing; replace the backlog line with:

```python
    backlog = sum(len(store.closed_day_files(s, today=today)) for s in store.streams())
```

- [ ] **Step 4: Run the full backend suite**

Run: `pytest backend/tests -q`
Expected: all PASS (the health test that asserted malformed-stream backlog still passes — discovery covers it).

- [ ] **Step 5: Commit**

```bash
git add backend/storage/raw.py backend/storage/archive.py backend/health/ingestion.py \
        backend/tests/test_raw_store.py backend/tests/test_archive_local.py
git commit -m "feat: archiver and health discover raw streams dynamically"
```

---

### Task 2: Source settings

**Files:**
- Modify: `backend/config.py`
- Test: `backend/tests/test_config.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_config.py`:

```python
def test_source_env_vars(monkeypatch):
    monkeypatch.setenv("CT_PATH_GTFS_URL", "https://example.com/path.zip")
    monkeypatch.setenv("CT_NJT_GTFS_URL", "https://user:pass@example.com/njt.zip")
    monkeypatch.setenv("CT_PATH_RT_URL", "https://example.com/path-rt")
    monkeypatch.setenv("CT_NJT_RT_TRIPUPDATES_URL", "https://example.com/njt-tu")
    monkeypatch.setenv("CT_NJT_RT_ALERTS_URL", "https://example.com/njt-al")
    monkeypatch.setenv("CT_SOURCE_POLL_INTERVAL_S", "30")
    monkeypatch.setenv("CT_GTFS_REFRESH_INTERVAL_S", "43200")
    s = load_settings()
    assert s.path_gtfs_url == "https://example.com/path.zip"
    assert s.njt_gtfs_url == "https://user:pass@example.com/njt.zip"
    assert s.path_rt_url == "https://example.com/path-rt"
    assert s.njt_rt_tripupdates_url == "https://example.com/njt-tu"
    assert s.njt_rt_alerts_url == "https://example.com/njt-al"
    assert s.source_poll_interval_s == 30.0
    assert s.gtfs_refresh_interval_s == 43200.0
```

Also extend the `test_defaults` delenv loop with the seven new vars and assert
`s.path_gtfs_url is None` and `s.source_poll_interval_s == 60.0` there.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_config.py -v`
Expected: FAIL — AttributeError on the new fields.

- [ ] **Step 3: Implement**

Append to `Settings` (all defaulted; after the geofence fields):

```python
    path_gtfs_url: str | None = None       # unset = source disabled
    njt_gtfs_url: str | None = None
    path_rt_url: str | None = None
    njt_rt_tripupdates_url: str | None = None
    njt_rt_alerts_url: str | None = None
    source_poll_interval_s: float = 60.0
    gtfs_refresh_interval_s: float = 86400.0
```

In `load_settings()` add:

```python
        path_gtfs_url=os.environ.get("CT_PATH_GTFS_URL") or None,
        njt_gtfs_url=os.environ.get("CT_NJT_GTFS_URL") or None,
        path_rt_url=os.environ.get("CT_PATH_RT_URL") or None,
        njt_rt_tripupdates_url=os.environ.get("CT_NJT_RT_TRIPUPDATES_URL") or None,
        njt_rt_alerts_url=os.environ.get("CT_NJT_RT_ALERTS_URL") or None,
        source_poll_interval_s=float(os.environ.get("CT_SOURCE_POLL_INTERVAL_S", "60")),
        gtfs_refresh_interval_s=float(os.environ.get("CT_GTFS_REFRESH_INTERVAL_S", "86400")),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_config.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/config.py backend/tests/test_config.py
git commit -m "feat: source url and poll interval settings"
```

---

### Task 3: Source framework — specs + archive-first fetch

**Files:**
- Create: `backend/sources/__init__.py` (empty)
- Create: `backend/sources/framework.py`
- Test: `backend/tests/test_sources_framework.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_sources_framework.py
import base64
import json

import httpx
import pytest

from backend.config import Settings
from backend.sources.framework import SourceSpec, fetch_once, sources_from_settings
from backend.storage.raw import RawStore


def _settings_with(tmp_path, **kw):
    return Settings(
        data_dir=tmp_path, s3_bucket=None, s3_prefix="x", s3_region=None,
        passthrough_url=None, archive_hour_utc=6, **kw,
    )


def test_registry_includes_only_configured_sources(tmp_path):
    s = _settings_with(tmp_path, path_gtfs_url="https://e.com/p.zip",
                       path_rt_url="https://e.com/rt")
    specs = sources_from_settings(s)
    assert [(sp.name, sp.interval_s) for sp in specs] == [
        ("gtfs_path", 86400.0),
        ("rt_path", 60.0),
    ]


def test_registry_empty_when_nothing_configured(tmp_path):
    assert sources_from_settings(_settings_with(tmp_path)) == []


@pytest.mark.anyio
async def test_fetch_once_archives_body_before_anything(tmp_path):
    async def handler(request):
        return httpx.Response(200, content=b"zipbytes")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = RawStore(tmp_path)
    spec = SourceSpec(name="gtfs_path", url="https://e.com/p.zip", interval_s=86400.0)
    state = {}
    ok = await fetch_once(client, spec, store, state)
    assert ok is True
    files = list((tmp_path / "raw" / "gtfs_path").glob("*.jsonl"))
    rec = json.loads(files[0].read_text().splitlines()[0])
    assert rec["payload"]["status"] == 200
    assert base64.b64decode(rec["payload"]["b64"]) == b"zipbytes"
    assert len(rec["payload"]["sha256"]) == 64


@pytest.mark.anyio
async def test_fetch_once_unchanged_content_archives_marker_not_body(tmp_path):
    async def handler(request):
        return httpx.Response(200, content=b"zipbytes")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = RawStore(tmp_path)
    spec = SourceSpec(name="gtfs_path", url="https://e.com/p.zip", interval_s=86400.0)
    state = {}
    await fetch_once(client, spec, store, state)
    await fetch_once(client, spec, store, state)
    lines = (
        (tmp_path / "raw" / "gtfs_path").glob("*.jsonl").__next__().read_text().splitlines()
    )
    assert len(lines) == 2
    second = json.loads(lines[1])["payload"]
    assert second["unchanged"] is True
    assert "b64" not in second


@pytest.mark.anyio
async def test_fetch_once_error_archives_error_event(tmp_path):
    async def handler(request):
        raise httpx.ConnectError("down")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = RawStore(tmp_path)
    spec = SourceSpec(name="rt_path", url="https://e.com/rt", interval_s=60.0)
    ok = await fetch_once(client, spec, store, {})
    assert ok is False
    rec = json.loads(
        (tmp_path / "raw" / "rt_path").glob("*.jsonl").__next__().read_text().splitlines()[0]
    )
    assert rec["payload"]["status"] is None
    assert "down" in rec["payload"]["error"]


@pytest.mark.anyio
async def test_fetch_once_non_200_archived_and_reported_false(tmp_path):
    async def handler(request):
        return httpx.Response(503, content=b"maintenance")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = RawStore(tmp_path)
    spec = SourceSpec(name="rt_path", url="https://e.com/rt", interval_s=60.0)
    ok = await fetch_once(client, spec, store, {})
    assert ok is False
    rec = json.loads(
        (tmp_path / "raw" / "rt_path").glob("*.jsonl").__next__().read_text().splitlines()[0]
    )
    assert rec["payload"]["status"] == 503
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_sources_framework.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.sources.framework`.

- [ ] **Step 3: Implement**

```python
# backend/sources/framework.py
"""Pluggable external data sources, archive-first.

Every fetch is recorded as a raw event BEFORE any parsing happens — external
observations are primitive data (you can't re-fetch the past). Adding a
source = one SourceSpec + one config URL. The response body travels base64
inside the event payload through the existing JSONL→Parquet→S3 pipeline.
"""

import base64
import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from backend.config import Settings
from backend.storage.raw import RawStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceSpec:
    name: str         # raw stream name
    url: str
    interval_s: float


def sources_from_settings(settings: Settings) -> list[SourceSpec]:
    """A source exists iff its URL is configured."""
    table = (
        ("gtfs_path", settings.path_gtfs_url, settings.gtfs_refresh_interval_s),
        ("gtfs_njt", settings.njt_gtfs_url, settings.gtfs_refresh_interval_s),
        ("rt_path", settings.path_rt_url, settings.source_poll_interval_s),
        ("rt_njt_trips", settings.njt_rt_tripupdates_url, settings.source_poll_interval_s),
        ("rt_njt_alerts", settings.njt_rt_alerts_url, settings.source_poll_interval_s),
    )
    return [
        SourceSpec(name=name, url=url, interval_s=interval)
        for name, url, interval in table
        if url
    ]


async def fetch_once(
    client: httpx.AsyncClient, spec: SourceSpec, store: RawStore, state: dict
) -> bool:
    """Fetch the source once and archive the outcome. Returns True on HTTP 200.

    `state` holds the last-archived body sha256 per source name (in-memory;
    a restart re-archives one full copy, which is harmless).
    """
    received_at = datetime.now(UTC).isoformat()
    try:
        resp = await client.get(spec.url, timeout=30.0)
        digest = hashlib.sha256(resp.content).hexdigest()
        if resp.status_code == 200 and state.get(spec.name) == digest:
            payload = {"url": spec.url, "status": resp.status_code,
                       "sha256": digest, "unchanged": True}
        else:
            payload = {"url": spec.url, "status": resp.status_code, "sha256": digest,
                       "b64": base64.b64encode(resp.content).decode("ascii")}
            if resp.status_code == 200:
                state[spec.name] = digest
        ok = resp.status_code == 200
    except Exception as exc:
        payload = {"url": spec.url, "status": None, "error": str(exc)}
        ok = False
    store.append(spec.name, {"received_at": received_at, "payload": payload})
    return ok
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_sources_framework.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/sources/ backend/tests/test_sources_framework.py
git commit -m "feat: archive-first source framework with url-gated registry"
```

---

### Task 4: Poller loop + lifespan wiring

**Files:**
- Create: `backend/sources/poller.py`
- Modify: `backend/app.py`
- Test: `backend/tests/test_sources_poller.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_sources_poller.py
import asyncio

import httpx
import pytest

from backend.sources import poller as poller_mod
from backend.sources.framework import SourceSpec
from backend.sources.poller import poll_source
from backend.storage.raw import RawStore


@pytest.mark.anyio
async def test_poll_source_fetches_then_sleeps(tmp_path, monkeypatch):
    calls = []
    sleeps = []

    async def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=b"x" * len(calls))  # content changes each poll

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(poller_mod.asyncio, "sleep", fake_sleep)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = RawStore(tmp_path)
    spec = SourceSpec(name="rt_path", url="https://e.com/rt", interval_s=60.0)
    with pytest.raises(asyncio.CancelledError):
        await poll_source(client, spec, store)
    assert len(calls) == 3  # fetch-first, then sleep
    assert sleeps == [60.0, 60.0, 60.0]
    day_file = next((tmp_path / "raw" / "rt_path").glob("*.jsonl"))
    assert len(day_file.read_text().splitlines()) == 3


@pytest.mark.anyio
async def test_poll_source_survives_fetch_crash(tmp_path, monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    async def boom(client, spec, store, state):
        raise RuntimeError("unexpected bug in fetch_once")

    monkeypatch.setattr(poller_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(poller_mod, "fetch_once", boom)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    spec = SourceSpec(name="rt_path", url="https://e.com/rt", interval_s=60.0)
    with pytest.raises(asyncio.CancelledError):
        await poll_source(client, spec, RawStore(tmp_path))
    assert len(sleeps) == 2  # loop kept going despite the crash
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_sources_poller.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.sources.poller`.

- [ ] **Step 3: Implement**

```python
# backend/sources/poller.py
"""One asyncio polling task per enabled source. Fetch first, then sleep, so a
static source (interval 24 h) is fetched immediately at startup."""

import asyncio
import logging

import httpx

from backend.sources.framework import SourceSpec, fetch_once
from backend.storage.raw import RawStore

log = logging.getLogger(__name__)


async def poll_source(client: httpx.AsyncClient, spec: SourceSpec, store: RawStore) -> None:
    state: dict = {}
    while True:
        try:
            await fetch_once(client, spec, store, state)
        except Exception:
            log.exception("source %s poll iteration failed", spec.name)
        await asyncio.sleep(spec.interval_s)


def start_pollers(
    client: httpx.AsyncClient, specs: list[SourceSpec], store: RawStore
) -> list[asyncio.Task]:
    tasks = [asyncio.create_task(poll_source(client, spec, store)) for spec in specs]
    if tasks:
        log.info("started %d source pollers: %s", len(tasks), [s.name for s in specs])
    return tasks
```

- [ ] **Step 4: Wire into `backend/app.py` lifespan**

Add imports:

```python
import httpx

from backend.sources.framework import sources_from_settings
from backend.sources.poller import start_pollers
```

In the lifespan, after the runner is started and before the archiver task,
add the poller startup; extend shutdown symmetrically:

```python
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runner = await asyncio.to_thread(EngineRunner.start, settings)
        archiver = Archiver(settings)
        task = asyncio.create_task(run_daily(archiver.run, hour_utc=settings.archive_hour_utc))
        source_client = httpx.AsyncClient()
        source_tasks = start_pollers(
            source_client, sources_from_settings(settings), app.state.raw_store
        )
        yield
        for st in source_tasks:
            st.cancel()
        for st in source_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await st
        await source_client.aclose()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await app.state.passthrough.aclose()
        app.state.runner.close()
```

- [ ] **Step 5: Run the whole backend suite**

Run: `pytest backend/tests -q`
Expected: all PASS — the settings fixture configures no source URLs, so
`sources_from_settings` returns `[]` and no poller (or network call) ever
starts in any existing test.

- [ ] **Step 6: Commit**

```bash
git add backend/sources/poller.py backend/app.py backend/tests/test_sources_poller.py
git commit -m "feat: per-source polling tasks wired into app lifespan"
```

---

### Task 5: Sources health endpoint

**Files:**
- Create: `backend/health/sources.py`
- Modify: `backend/health/routes.py`
- Test: `backend/tests/test_health_sources.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_health_sources.py
import dataclasses

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.health.sources import sources_snapshot
from backend.storage.raw import RawStore


def _src_settings(settings):
    return dataclasses.replace(
        settings, path_rt_url="https://e.com/rt", path_gtfs_url="https://e.com/p.zip"
    )


def test_snapshot_reports_configured_sources_never_fetched(settings):
    s = _src_settings(settings)
    snap = sources_snapshot(s, now_iso="2026-06-10T12:00:00+00:00")
    assert snap == [
        {"name": "gtfs_path", "last_fetch_at": None, "age_seconds": None,
         "last_status": None},
        {"name": "rt_path", "last_fetch_at": None, "age_seconds": None,
         "last_status": None},
    ]


def test_snapshot_reads_latest_event(settings):
    s = _src_settings(settings)
    store = RawStore(s.data_dir)
    store.append("rt_path", {"received_at": "2026-06-10T11:00:00+00:00",
                             "payload": {"status": 200}})
    store.append("rt_path", {"received_at": "2026-06-10T11:59:00+00:00",
                             "payload": {"status": 503}})
    snap = sources_snapshot(s, now_iso="2026-06-10T12:00:00+00:00")
    rt = [x for x in snap if x["name"] == "rt_path"][0]
    assert rt["last_fetch_at"] == "2026-06-10T11:59:00+00:00"
    assert rt["age_seconds"] == 60
    assert rt["last_status"] == 503


def test_endpoint(settings, monkeypatch):
    # Tests must never touch the network: neuter the poll loop's fetch before
    # the lifespan starts the pollers for the configured URLs.
    from backend.sources import poller as poller_mod

    async def no_fetch(client, spec, store, state):
        return True

    monkeypatch.setattr(poller_mod, "fetch_once", no_fetch)
    app = create_app(_src_settings(settings))
    with TestClient(app) as client:
        resp = client.get("/api/health/sources")
    assert resp.status_code == 200
    assert {x["name"] for x in resp.json()} == {"gtfs_path", "rt_path"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_health_sources.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.health.sources`.

- [ ] **Step 3: Implement**

```python
# backend/health/sources.py
"""Per-source freshness, derived from the raw stream tail — no extra state."""

import json
from datetime import datetime

from backend.config import Settings
from backend.sources.framework import sources_from_settings
from backend.storage.raw import RawStore


def sources_snapshot(settings: Settings, *, now_iso: str) -> list[dict]:
    now = datetime.fromisoformat(now_iso)
    today = now_iso[:10]
    store = RawStore(settings.data_dir)
    out = []
    for spec in sources_from_settings(settings):
        last_at = None
        last_status = None
        day_file = store.day_file(spec.name, today)
        if day_file.exists():
            lines = day_file.read_text(encoding="utf-8").splitlines()
            for line in reversed(lines):
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # torn trailing write
                last_at = rec.get("received_at")
                last_status = rec.get("payload", {}).get("status")
                break
        age = None
        if last_at is not None:
            age = int((now - datetime.fromisoformat(last_at)).total_seconds())
        out.append(
            {"name": spec.name, "last_fetch_at": last_at, "age_seconds": age,
             "last_status": last_status}
        )
    return out
```

In `backend/health/routes.py` add inside `make_health_router()`:

```python
    @router.get("/api/health/sources")
    async def health_sources(request: Request) -> list[dict]:
        return sources_snapshot(
            request.app.state.settings, now_iso=datetime.now(UTC).isoformat()
        )
```

(with `from backend.health.sources import sources_snapshot` at top).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_health_sources.py backend/tests/test_health.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/health/sources.py backend/health/routes.py backend/tests/test_health_sources.py
git commit -m "feat: per-source freshness health endpoint"
```

---

### Task 6: GTFS fixture builder (test helper)

**Files:**
- Create: `backend/tests/gtfs_fixture.py`

Test infrastructure for Tasks 7–11: builds a tiny in-memory GTFS zip with a
single rail line whose stops/times the tests position to align with the
synthetic commute tracks.

- [ ] **Step 1: Implement**

```python
# backend/tests/gtfs_fixture.py
"""Synthetic GTFS zip builder. One agency, one rail route, N stops, M trips.

stops: list of (stop_id, name, lat, lon)
trips: list of (trip_id, service_id, headsign, [(stop_id, "HH:MM:SS"), ...])
calendar: every service_id runs all 7 days across 2026.
"""

import io
import zipfile


def build_gtfs_zip(stops, trips, route_type=2, route_name="Test Line") -> bytes:
    agency = "agency_id,agency_name,agency_url,agency_timezone\n" \
             "TST,Test Agency,https://example.com,America/New_York\n"
    routes = "route_id,agency_id,route_short_name,route_long_name,route_type\n" \
             f"R1,TST,TL,{route_name},{route_type}\n"
    stops_csv = "stop_id,stop_name,stop_lat,stop_lon\n" + "".join(
        f"{sid},{name},{lat},{lon}\n" for sid, name, lat, lon in stops
    )
    service_ids = sorted({t[1] for t in trips})
    calendar = (
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
        "start_date,end_date\n"
        + "".join(f"{sid},1,1,1,1,1,1,1,20260101,20261231\n" for sid in service_ids)
    )
    trips_csv = "route_id,service_id,trip_id,trip_headsign\n" + "".join(
        f"R1,{svc},{tid},{headsign}\n" for tid, svc, headsign, _ in trips
    )
    stop_times = "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
    for tid, _, _, calls in trips:
        for seq, (sid, hms) in enumerate(calls, start=1):
            stop_times += f"{tid},{hms},{hms},{sid},{seq}\n"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("agency.txt", agency)
        z.writestr("routes.txt", routes)
        z.writestr("stops.txt", stops_csv)
        z.writestr("calendar.txt", calendar)
        z.writestr("trips.txt", trips_csv)
        z.writestr("stop_times.txt", stop_times)
    return buf.getvalue()
```

- [ ] **Step 2: Sanity-check**

Run: `python -c "
from backend.tests.gtfs_fixture import build_gtfs_zip
z = build_gtfs_zip([('S1','A',40.7,-74.4),('S2','B',40.87,-74.4)],
                   [('T1','WK','Inbound',[('S1','10:00:00'),('S2','10:24:00')])])
import zipfile, io
print(zipfile.ZipFile(io.BytesIO(z)).namelist())
"`
Expected: the six .txt names.

Run: `ruff format backend/ && ruff check backend/`

- [ ] **Step 3: Commit**

```bash
git add backend/tests/gtfs_fixture.py
git commit -m "test: synthetic gtfs zip fixture builder"
```

---

### Task 7: GTFS schedule tables + parser

**Files:**
- Create: `backend/transit/__init__.py` (empty)
- Create: `backend/transit/gtfs.py`
- Modify: `backend/storage/derived.py` (DDL additions)
- Test: `backend/tests/test_gtfs_parse.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_gtfs_parse.py
import base64

from backend.storage.derived import DerivedStore
from backend.storage.raw import RawStore
from backend.tests.gtfs_fixture import build_gtfs_zip
from backend.transit.gtfs import hms_to_seconds, latest_snapshot, parse_gtfs

STOPS = [("S1", "Alpha", 40.7000, -74.4000), ("S2", "Beta", 40.8700, -74.4000)]
TRIPS = [
    ("T1", "WK", "Inbound", [("S1", "10:00:00"), ("S2", "10:24:00")]),
    ("T2", "WK", "Inbound", [("S1", "25:10:00"), ("S2", "25:34:00")]),  # after midnight
]


def test_hms_to_seconds():
    assert hms_to_seconds("10:00:00") == 36000
    assert hms_to_seconds("25:10:00") == 90600  # GTFS times exceed 24h


def _archive_snapshot(settings, source="gtfs_path"):
    z = build_gtfs_zip(STOPS, TRIPS)
    RawStore(settings.data_dir).append(
        source,
        {"received_at": "2026-06-09T05:00:00+00:00",
         "payload": {"url": "https://e.com/p.zip", "status": 200,
                     "b64": base64.b64encode(z).decode()}},
    )


def test_latest_snapshot_returns_bytes(settings):
    _archive_snapshot(settings)
    data = latest_snapshot(settings, "gtfs_path")
    assert data is not None and data[:2] == b"PK"  # zip magic


def test_latest_snapshot_none_when_absent(settings):
    assert latest_snapshot(settings, "gtfs_path") is None


def test_latest_snapshot_skips_unchanged_and_error_events(settings):
    _archive_snapshot(settings)
    store = RawStore(settings.data_dir)
    store.append("gtfs_path", {"received_at": "2026-06-10T05:00:00+00:00",
                               "payload": {"status": 200, "sha256": "x",
                                           "unchanged": True}})
    store.append("gtfs_path", {"received_at": "2026-06-10T06:00:00+00:00",
                               "payload": {"status": None, "error": "down"}})
    assert latest_snapshot(settings, "gtfs_path") is not None


def test_parse_gtfs_loads_schedule_tables(settings):
    _archive_snapshot(settings)
    store = DerivedStore(settings)
    counts = parse_gtfs(store.con, "gtfs_path", latest_snapshot(settings, "gtfs_path"),
                        fetched_at="2026-06-09T05:00:00+00:00")
    assert counts["stops"] == 2
    assert counts["trips"] == 2
    assert counts["stop_times"] == 4
    row = store.con.execute(
        "SELECT departure_s FROM gtfs_stop_times WHERE trip_id='T2' AND stop_id='S1' "
        "AND source='gtfs_path'"
    ).fetchone()
    assert row[0] == 90600


def test_parse_gtfs_replaces_prior_rows_for_source(settings):
    _archive_snapshot(settings)
    store = DerivedStore(settings)
    snap = latest_snapshot(settings, "gtfs_path")
    parse_gtfs(store.con, "gtfs_path", snap, fetched_at="a")
    parse_gtfs(store.con, "gtfs_path", snap, fetched_at="b")
    n = store.con.execute("SELECT count(*) FROM gtfs_stops").fetchone()[0]
    assert n == 2  # not 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_gtfs_parse.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.transit.gtfs`.

- [ ] **Step 3: Add schedule DDL to `backend/storage/derived.py`**

Append to `_DDL` (inside the same string) and expose the connection:

```sql
CREATE TABLE IF NOT EXISTS gtfs_feeds (
    source VARCHAR, fetched_at VARCHAR
);
CREATE TABLE IF NOT EXISTS gtfs_stops (
    source VARCHAR, stop_id VARCHAR, stop_name VARCHAR, stop_lat DOUBLE, stop_lon DOUBLE
);
CREATE TABLE IF NOT EXISTS gtfs_routes (
    source VARCHAR, route_id VARCHAR, route_name VARCHAR, route_type INTEGER
);
CREATE TABLE IF NOT EXISTS gtfs_trips (
    source VARCHAR, trip_id VARCHAR, route_id VARCHAR, service_id VARCHAR,
    headsign VARCHAR
);
CREATE TABLE IF NOT EXISTS gtfs_stop_times (
    source VARCHAR, trip_id VARCHAR, stop_id VARCHAR, stop_sequence INTEGER,
    arrival_s INTEGER, departure_s INTEGER
);
CREATE TABLE IF NOT EXISTS gtfs_calendar (
    source VARCHAR, service_id VARCHAR, monday INTEGER, tuesday INTEGER,
    wednesday INTEGER, thursday INTEGER, friday INTEGER, saturday INTEGER,
    sunday INTEGER, start_date VARCHAR, end_date VARCHAR
);
CREATE TABLE IF NOT EXISTS gtfs_calendar_dates (
    source VARCHAR, service_id VARCHAR, date VARCHAR, exception_type INTEGER
);
```

Add a `con` property to `DerivedStore`:

```python
    @property
    def con(self) -> duckdb.DuckDBPyConnection:
        """Shared connection for schedule/match modules (single-process app)."""
        return self._con
```

And extend `truncate()`'s table tuple with the seven gtfs tables (plus
`train_matches` arrives in Task 9 — add it then).

- [ ] **Step 4: Implement the parser**

```python
# backend/transit/gtfs.py
"""GTFS static parsing: archived snapshot zip → schedule tables.

Schedule tables are derived data — re-parsed from the archived snapshot on
every rebuild, replaced per-source on every parse.
"""

import base64
import csv
import io
import json
import logging
import zipfile

import duckdb

from backend.config import Settings
from backend.storage.query import EventQuery

log = logging.getLogger(__name__)

RAIL_ROUTE_TYPES = (1, 2)  # 1 = subway/PATH, 2 = rail/NJT


def hms_to_seconds(hms: str) -> int:
    h, m, s = hms.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def latest_snapshot(settings: Settings, source: str) -> bytes | None:
    """Newest archived fetch event that carries a body (status 200 + b64)."""
    q = EventQuery(settings)
    rel = q.events(source)
    rows = q.sql(
        "SELECT CAST(payload AS VARCHAR) FROM rel ORDER BY received_at DESC", rel=rel
    ).fetchall()
    for (payload_text,) in rows:
        p = json.loads(payload_text)
        if p.get("status") == 200 and "b64" in p:
            return base64.b64decode(p["b64"])
    return None


def _rows(zf: zipfile.ZipFile, name: str) -> list[dict]:
    if name not in zf.namelist():
        return []
    with zf.open(name) as f:
        return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))


def parse_gtfs(
    con: duckdb.DuckDBPyConnection, source: str, zip_bytes: bytes, *, fetched_at: str
) -> dict:
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    routes = [
        r for r in _rows(zf, "routes.txt")
        if int(r.get("route_type", -1)) in RAIL_ROUTE_TYPES
    ]
    rail_route_ids = {r["route_id"] for r in routes}
    trips = [t for t in _rows(zf, "trips.txt") if t["route_id"] in rail_route_ids]
    rail_trip_ids = {t["trip_id"] for t in trips}
    stop_times = [
        st for st in _rows(zf, "stop_times.txt") if st["trip_id"] in rail_trip_ids
    ]
    used_stop_ids = {st["stop_id"] for st in stop_times}
    stops = [s for s in _rows(zf, "stops.txt") if s["stop_id"] in used_stop_ids]
    calendar = _rows(zf, "calendar.txt")
    calendar_dates = _rows(zf, "calendar_dates.txt")

    tables = ("gtfs_feeds", "gtfs_stops", "gtfs_routes", "gtfs_trips",
              "gtfs_stop_times", "gtfs_calendar", "gtfs_calendar_dates")
    con.execute("BEGIN")
    try:
        for t in tables:
            con.execute(f"DELETE FROM {t} WHERE source = ?", [source])
        con.execute("INSERT INTO gtfs_feeds VALUES (?,?)", [source, fetched_at])
        if stops:
            con.executemany(
                "INSERT INTO gtfs_stops VALUES (?,?,?,?,?)",
                [[source, s["stop_id"], s.get("stop_name", ""),
                  float(s["stop_lat"]), float(s["stop_lon"])] for s in stops],
            )
        if routes:
            con.executemany(
                "INSERT INTO gtfs_routes VALUES (?,?,?,?)",
                [[source, r["route_id"],
                  r.get("route_long_name") or r.get("route_short_name", ""),
                  int(r["route_type"])] for r in routes],
            )
        if trips:
            con.executemany(
                "INSERT INTO gtfs_trips VALUES (?,?,?,?,?)",
                [[source, t["trip_id"], t["route_id"], t["service_id"],
                  t.get("trip_headsign", "")] for t in trips],
            )
        if stop_times:
            con.executemany(
                "INSERT INTO gtfs_stop_times VALUES (?,?,?,?,?,?)",
                [[source, st["trip_id"], st["stop_id"], int(st["stop_sequence"]),
                  hms_to_seconds(st["arrival_time"]),
                  hms_to_seconds(st["departure_time"])] for st in stop_times],
            )
        if calendar:
            con.executemany(
                "INSERT INTO gtfs_calendar VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [[source, c["service_id"], int(c["monday"]), int(c["tuesday"]),
                  int(c["wednesday"]), int(c["thursday"]), int(c["friday"]),
                  int(c["saturday"]), int(c["sunday"]), c["start_date"],
                  c["end_date"]] for c in calendar],
            )
        if calendar_dates:
            con.executemany(
                "INSERT INTO gtfs_calendar_dates VALUES (?,?,?,?)",
                [[source, c["service_id"], c["date"], int(c["exception_type"])]
                 for c in calendar_dates],
            )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    counts = {"stops": len(stops), "routes": len(routes), "trips": len(trips),
              "stop_times": len(stop_times)}
    log.info("parsed %s gtfs: %s", source, counts)
    return counts
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest backend/tests/test_gtfs_parse.py backend/tests/test_derived_store.py -v`
Expected: all PASS (derived-store tests unaffected by DDL additions).

- [ ] **Step 6: Commit**

```bash
git add backend/transit/ backend/storage/derived.py backend/tests/test_gtfs_parse.py
git commit -m "feat: gtfs snapshot parser into derived schedule tables"
```

---

### Task 8: Service-day resolution

**Files:**
- Modify: `backend/transit/gtfs.py`
- Test: `backend/tests/test_service_day.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_service_day.py
import base64

from backend.storage.derived import DerivedStore
from backend.storage.raw import RawStore
from backend.tests.gtfs_fixture import build_gtfs_zip
from backend.transit.gtfs import active_service_ids, latest_snapshot, parse_gtfs

STOPS = [("S1", "Alpha", 40.70, -74.40), ("S2", "Beta", 40.87, -74.40)]


def _load(settings, trips, calendar_dates_csv=None):
    z = build_gtfs_zip(STOPS, trips)
    if calendar_dates_csv is not None:
        # rebuild the zip with a calendar_dates.txt injected
        import io
        import zipfile

        src = zipfile.ZipFile(io.BytesIO(z))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as out:
            for name in src.namelist():
                out.writestr(name, src.read(name))
            out.writestr("calendar_dates.txt", calendar_dates_csv)
        z = buf.getvalue()
    RawStore(settings.data_dir).append(
        "gtfs_path",
        {"received_at": "2026-06-09T05:00:00+00:00",
         "payload": {"url": "u", "status": 200,
                     "b64": base64.b64encode(z).decode()}},
    )
    store = DerivedStore(settings)
    parse_gtfs(store.con, "gtfs_path", latest_snapshot(settings, "gtfs_path"),
               fetched_at="x")
    return store


def test_weekday_service_active(settings):
    store = _load(settings, [("T1", "WK", "In", [("S1", "10:00:00"),
                                                 ("S2", "10:24:00")])])
    # 2026-06-10 is a Wednesday; fixture calendar runs all days of 2026
    assert active_service_ids(store.con, "gtfs_path", "20260610") == {"WK"}


def test_service_outside_date_range_inactive(settings):
    store = _load(settings, [("T1", "WK", "In", [("S1", "10:00:00"),
                                                 ("S2", "10:24:00")])])
    assert active_service_ids(store.con, "gtfs_path", "20270101") == set()


def test_calendar_dates_exceptions(settings):
    cal_dates = (
        "service_id,date,exception_type\n"
        "WK,20260610,2\n"      # removed on the 10th
        "HOLIDAY,20260610,1\n"  # added on the 10th
    )
    store = _load(
        settings,
        [("T1", "WK", "In", [("S1", "10:00:00"), ("S2", "10:24:00")]),
         ("T2", "HOLIDAY", "In", [("S1", "11:00:00"), ("S2", "11:24:00")])],
        calendar_dates_csv=cal_dates,
    )
    assert active_service_ids(store.con, "gtfs_path", "20260610") == {"HOLIDAY"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_service_day.py -v`
Expected: FAIL — ImportError: cannot import name 'active_service_ids'.

- [ ] **Step 3: Implement (append to `backend/transit/gtfs.py`)**

```python
_WEEKDAY_COLS = ("monday", "tuesday", "wednesday", "thursday", "friday",
                 "saturday", "sunday")


def active_service_ids(
    con: duckdb.DuckDBPyConnection, source: str, service_date: str
) -> set[str]:
    """service_date is GTFS-style YYYYMMDD (agency-local calendar date)."""
    from datetime import date

    d = date(int(service_date[:4]), int(service_date[4:6]), int(service_date[6:8]))
    weekday_col = _WEEKDAY_COLS[d.weekday()]
    base = {
        r[0]
        for r in con.execute(
            f"SELECT service_id FROM gtfs_calendar WHERE source = ? "
            f"AND {weekday_col} = 1 AND start_date <= ? AND end_date >= ?",
            [source, service_date, service_date],
        ).fetchall()
    }
    for service_id, exception_type in con.execute(
        "SELECT service_id, exception_type FROM gtfs_calendar_dates "
        "WHERE source = ? AND date = ?",
        [source, service_date],
    ).fetchall():
        if exception_type == 1:
            base.add(service_id)
        elif exception_type == 2:
            base.discard(service_id)
    return base
```

(`weekday_col` interpolation is safe: it comes from the fixed `_WEEKDAY_COLS`
tuple, never user input.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_service_day.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/transit/gtfs.py backend/tests/test_service_day.py
git commit -m "feat: gtfs service-day resolution with calendar exceptions"
```

---

### Task 9: Train matcher core

**Files:**
- Create: `backend/transit/matcher.py`
- Test: `backend/tests/test_matcher.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_matcher.py
import base64
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.engine.geofence import Geofence
from backend.engine.machine import TripEngine
from backend.engine.params import EngineParams
from backend.engine.types import TripClosed
from backend.storage.derived import DerivedStore
from backend.storage.raw import RawStore
from backend.tests.gtfs_fixture import build_gtfs_zip
from backend.tests.synth import commute
from backend.transit.gtfs import latest_snapshot, parse_gtfs
from backend.transit.matcher import match_trip

NY = ZoneInfo("America/New_York")


def _closed_commute():
    pts, _, _ = commute()  # t0 = 1_781_100_000 → 2026-06-10 10:00 EDT
    eng = TripEngine(EngineParams(), geofences=[])
    closed = []
    for pt in pts:
        closed.extend(e for e in eng.process(pt) if isinstance(e, TripClosed))
    assert len(closed) == 1
    return closed[0]


def _vehicle_segment_endpoints(closed):
    seg = next(s for s in closed.segments if s.mode == "vehicle")
    start = next(p for p in closed.points if p.ts >= seg.start_ts)
    end = max((p for p in closed.points if p.ts <= seg.end_ts), key=lambda p: p.ts)
    return seg, start, end


def _hms(epoch):
    dt = datetime.fromtimestamp(epoch, NY)
    return f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"


def _load_schedule(settings, closed, dep_offset_s=120.0):
    """Build a fixture schedule with stations AT the vehicle segment endpoints
    and a trip departing dep_offset_s after the segment starts."""
    seg, start, end = _vehicle_segment_endpoints(closed)
    stops = [("S1", "Alpha", start.lat, start.lon), ("S2", "Beta", end.lat, end.lon)]
    trips = [
        ("T1", "WK", "Beta-bound",
         [("S1", _hms(start.ts + dep_offset_s)), ("S2", _hms(end.ts + dep_offset_s))]),
        # decoy 40 minutes later — must not be picked
        ("T2", "WK", "Beta-bound",
         [("S1", _hms(start.ts + 2400)), ("S2", _hms(end.ts + 2400))]),
    ]
    RawStore(settings.data_dir).append(
        "gtfs_path",
        {"received_at": "2026-06-09T05:00:00+00:00",
         "payload": {"url": "u", "status": 200,
                     "b64": base64.b64encode(build_gtfs_zip(stops, trips)).decode()}},
    )
    store = DerivedStore(settings)
    parse_gtfs(store.con, "gtfs_path", latest_snapshot(settings, "gtfs_path"),
               fetched_at="x")
    return store


def test_matches_vehicle_segment_to_scheduled_trip(settings):
    closed = _closed_commute()
    store = _load_schedule(settings, closed)
    matches = match_trip(store.con, closed)
    assert len(matches) == 1
    m = matches[0]
    assert m.gtfs_trip_id == "T1"
    assert m.source == "gtfs_path"
    assert m.board_stop == "Alpha"
    assert m.alight_stop == "Beta"
    assert abs(m.delta_s - 120.0) < 60.0  # scheduled dep ~2 min after observed start
    assert m.trip_id == closed.trip.trip_id
    seg = next(s for s in closed.segments if s.mode == "vehicle")
    assert m.seg_index == seg.seg_index


def test_no_match_when_no_station_nearby(settings):
    closed = _closed_commute()
    # stations far away (>500 m east)
    store = _load_schedule(settings, closed)
    store.con.execute("UPDATE gtfs_stops SET stop_lon = stop_lon + 0.02")
    assert match_trip(store.con, closed) == []


def test_no_match_when_departure_outside_tolerance(settings):
    closed = _closed_commute()
    store = _load_schedule(settings, closed, dep_offset_s=3600.0)
    # nearest trip departs an hour later: T1 out of tolerance, decoy further
    assert match_trip(store.con, closed) == []


def test_walk_segments_never_matched(settings):
    closed = _closed_commute()
    store = _load_schedule(settings, closed)
    matches = match_trip(store.con, closed)
    walk_indexes = {s.seg_index for s in closed.segments if s.mode != "vehicle"}
    assert all(m.seg_index not in walk_indexes for m in matches)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_matcher.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.transit.matcher`.

- [ ] **Step 3: Implement**

```python
# backend/transit/matcher.py
"""Match vehicle segments to scheduled GTFS trips.

Deterministic given (trip points, schedule tables): nearest rail stop to the
segment endpoints (within STOP_RADIUS_M), candidate scheduled trips serving
board→alight in order on the active service day, departure within
DEP_TOLERANCE_S of the observed segment start; best = smallest |delta|.

GTFS times are agency-local — segment epochs convert via America/New_York.
For local times before 04:00 the previous service date is also tried with
local_s + 86400 (late-night trains are listed as 24:xx/25:xx on the prior
service day).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import duckdb

from backend.engine.geo import haversine_m
from backend.engine.types import TripClosed
from backend.transit.gtfs import active_service_ids

STOP_RADIUS_M = 500.0
DEP_TOLERANCE_S = 900.0
_NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class TrainMatch:
    trip_id: str
    seg_index: int
    source: str
    gtfs_trip_id: str
    route_name: str
    headsign: str
    board_stop: str
    alight_stop: str
    scheduled_dep_s: int    # seconds since local midnight of the service date
    delta_s: float          # observed start - scheduled departure (positive = late board)


def _nearest_stop(stops: list[tuple], lat: float, lon: float):
    """stops rows: (source, stop_id, stop_name, stop_lat, stop_lon)."""
    best, best_d = None, STOP_RADIUS_M
    for row in stops:
        d = haversine_m(lat, lon, row[3], row[4])
        if d <= best_d:
            best, best_d = row, d
    return best


def _service_day_candidates(ts: float) -> list[tuple[str, int]]:
    """(service_date YYYYMMDD, local seconds) pairs to try for an epoch."""
    dt = datetime.fromtimestamp(ts, _NY)
    local_s = dt.hour * 3600 + dt.minute * 60 + dt.second
    out = [(dt.strftime("%Y%m%d"), local_s)]
    if local_s < 4 * 3600:
        prev = (dt - timedelta(days=1)).strftime("%Y%m%d")
        out.append((prev, local_s + 86400))
    return out


def match_trip(con: duckdb.DuckDBPyConnection, closed: TripClosed) -> list["TrainMatch"]:
    stops = con.execute(
        "SELECT source, stop_id, stop_name, stop_lat, stop_lon FROM gtfs_stops"
    ).fetchall()
    if not stops:
        return []
    matches = []
    for seg in closed.segments:
        if seg.mode != "vehicle":
            continue
        start = next(p for p in closed.points if p.ts >= seg.start_ts)
        end = max((p for p in closed.points if p.ts <= seg.end_ts), key=lambda p: p.ts)
        board = _nearest_stop(stops, start.lat, start.lon)
        alight = _nearest_stop(stops, end.lat, end.lon)
        if board is None or alight is None or board[1] == alight[1]:
            continue
        if board[0] != alight[0]:
            continue  # endpoints resolved to different agencies' stops
        source = board[0]
        best = None
        for service_date, local_s in _service_day_candidates(start.ts):
            active = active_service_ids(con, source, service_date)
            if not active:
                continue
            rows = con.execute(
                "SELECT t.trip_id, t.service_id, t.headsign, r.route_name, "
                "       st1.departure_s "
                "FROM gtfs_stop_times st1 "
                "JOIN gtfs_stop_times st2 ON st1.source = st2.source "
                "  AND st1.trip_id = st2.trip_id "
                "JOIN gtfs_trips t ON t.source = st1.source AND t.trip_id = st1.trip_id "
                "JOIN gtfs_routes r ON r.source = t.source AND r.route_id = t.route_id "
                "WHERE st1.source = ? AND st1.stop_id = ? AND st2.stop_id = ? "
                "  AND st1.stop_sequence < st2.stop_sequence",
                [source, board[1], alight[1]],
            ).fetchall()
            for gtfs_trip_id, service_id, headsign, route_name, dep_s in rows:
                if service_id not in active:
                    continue
                delta = local_s - dep_s
                if abs(delta) > DEP_TOLERANCE_S:
                    continue
                if best is None or abs(delta) < abs(best.delta_s):
                    best = TrainMatch(
                        trip_id=closed.trip.trip_id, seg_index=seg.seg_index,
                        source=source, gtfs_trip_id=gtfs_trip_id,
                        route_name=route_name, headsign=headsign,
                        board_stop=board[2], alight_stop=alight[2],
                        scheduled_dep_s=dep_s, delta_s=float(delta),
                    )
        if best is not None:
            matches.append(best)
    return matches
```

NOTE on the delta sign: observed start − scheduled departure. The fixture
schedules departure 120 s AFTER the observed segment start, so delta is
−120 — the test asserts `abs(m.delta_s - 120.0) < 60`. **That assertion is
wrong as drafted** — fix the test to `assert abs(m.delta_s + 120.0) < 60.0`
(observed before schedule → negative delta), and double-check empirically.
Whichever way you resolve it, the test and implementation must agree on the
documented sign convention (positive = boarded after the scheduled time).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_matcher.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/transit/matcher.py backend/tests/test_matcher.py
git commit -m "feat: train matcher attributes vehicle segments to scheduled trips"
```

---

### Task 10: Matching integration — store, rebuild, live

**Files:**
- Modify: `backend/storage/derived.py`
- Modify: `backend/engine/rebuild.py`
- Modify: `backend/engine/runner.py`
- Test: `backend/tests/test_rebuild.py` (add), `backend/tests/test_runner_live.py` (add)

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_rebuild.py` (reusing matcher-test helpers — import
them from a new shared location is overkill; copy the small `_hms` helper):

```python
def test_rebuild_parses_schedule_and_matches_trains(settings):
    import base64
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from backend.engine.machine import TripEngine
    from backend.engine.params import EngineParams
    from backend.engine.types import TripClosed
    from backend.tests.gtfs_fixture import build_gtfs_zip

    # determine vehicle segment endpoints offline
    pts, _, _ = commute()
    eng = TripEngine(EngineParams(), geofences=[])
    closed = []
    for pt in pts:
        closed.extend(e for e in eng.process(pt) if isinstance(e, TripClosed))
    seg = next(s for s in closed[0].segments if s.mode == "vehicle")
    start = next(p for p in closed[0].points if p.ts >= seg.start_ts)
    end = max((p for p in closed[0].points if p.ts <= seg.end_ts), key=lambda p: p.ts)
    ny = ZoneInfo("America/New_York")

    def hms(epoch):
        dt = datetime.fromtimestamp(epoch, ny)
        return f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"

    z = build_gtfs_zip(
        [("S1", "Alpha", start.lat, start.lon), ("S2", "Beta", end.lat, end.lon)],
        [("T1", "WK", "Beta-bound", [("S1", hms(start.ts)), ("S2", hms(end.ts))])],
    )
    RawStore(settings.data_dir).append(
        "gtfs_path",
        {"received_at": "2026-06-09T05:00:00+00:00",
         "payload": {"url": "u", "status": 200,
                     "b64": base64.b64encode(z).decode()}},
    )
    _ingest_synthetic_day(settings)
    engine, store, counts = rebuild(settings)
    assert counts["trips"] == 1
    assert counts["train_matches"] == 1
    trip_id = store.list_trips()[0]["trip_id"]
    assert store.matches_for_trip(trip_id)[0]["gtfs_trip_id"] == "T1"
```

Add to `backend/tests/test_runner_live.py`:

```python
def test_live_trip_close_triggers_matching(settings):
    import base64
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from backend.engine.machine import TripEngine
    from backend.engine.params import EngineParams
    from backend.engine.types import TripClosed
    from backend.storage.raw import RawStore
    from backend.tests.gtfs_fixture import build_gtfs_zip

    pts, _, _ = commute()
    eng = TripEngine(EngineParams(), geofences=[])
    closed = []
    for pt in pts:
        closed.extend(e for e in eng.process(pt) if isinstance(e, TripClosed))
    seg = next(s for s in closed[0].segments if s.mode == "vehicle")
    start = next(p for p in closed[0].points if p.ts >= seg.start_ts)
    end = max((p for p in closed[0].points if p.ts <= seg.end_ts), key=lambda p: p.ts)
    ny = ZoneInfo("America/New_York")

    def hms(epoch):
        dt = datetime.fromtimestamp(epoch, ny)
        return f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"

    z = build_gtfs_zip(
        [("S1", "Alpha", start.lat, start.lon), ("S2", "Beta", end.lat, end.lon)],
        [("T1", "WK", "Beta-bound", [("S1", hms(start.ts)), ("S2", hms(end.ts))])],
    )
    RawStore(settings.data_dir).append(
        "gtfs_path",
        {"received_at": "2026-06-09T05:00:00+00:00",
         "payload": {"url": "u", "status": 200,
                     "b64": base64.b64encode(z).decode()}},
    )
    app = create_app(settings)
    with TestClient(app) as c:
        for pt in pts:
            c.post("/ingest/owntracks", json={"_type": "location", "tst": pt.ts,
                                              "lat": pt.lat, "lon": pt.lon,
                                              "acc": pt.accuracy_m})
        trips = app.state.runner.store.list_trips()
        assert len(trips) == 1
        matches = app.state.runner.store.matches_for_trip(trips[0]["trip_id"])
        assert [m["gtfs_trip_id"] for m in matches] == ["T1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_rebuild.py backend/tests/test_runner_live.py -v`
Expected: FAIL — KeyError 'train_matches' / no attribute matches_for_trip.

- [ ] **Step 3: Implement store methods**

In `backend/storage/derived.py` `_DDL`, add:

```sql
CREATE TABLE IF NOT EXISTS train_matches (
    trip_id VARCHAR, seg_index INTEGER, source VARCHAR, gtfs_trip_id VARCHAR,
    route_name VARCHAR, headsign VARCHAR, board_stop VARCHAR, alight_stop VARCHAR,
    scheduled_dep_s INTEGER, delta_s DOUBLE
);
```

Add `train_matches` to `truncate()`'s tuple AND to the per-trip DELETE loop
in `write_trip_closed` (a rewritten trip must drop stale matches). Add methods:

```python
    def write_train_matches(self, matches: list) -> None:
        if not matches:
            return
        self._con.executemany(
            "INSERT INTO train_matches VALUES (?,?,?,?,?,?,?,?,?,?)",
            [[m.trip_id, m.seg_index, m.source, m.gtfs_trip_id, m.route_name,
              m.headsign, m.board_stop, m.alight_stop, m.scheduled_dep_s,
              m.delta_s] for m in matches],
        )

    def matches_for_trip(self, trip_id: str) -> list[dict]:
        rows = self._con.execute(
            "SELECT seg_index, source, gtfs_trip_id, route_name, headsign, "
            "board_stop, alight_stop, scheduled_dep_s, delta_s "
            "FROM train_matches WHERE trip_id = ? ORDER BY seg_index",
            [trip_id],
        ).fetchall()
        return [
            {"seg_index": r[0], "source": r[1], "gtfs_trip_id": r[2],
             "route_name": r[3], "headsign": r[4], "board_stop": r[5],
             "alight_stop": r[6], "scheduled_dep_s": r[7], "delta_s": r[8]}
            for r in rows
        ]
```

- [ ] **Step 4: Wire into rebuild and runner**

In `backend/engine/rebuild.py`:
- imports: `from backend.transit.gtfs import latest_snapshot, parse_gtfs` and
  `from backend.transit.matcher import match_trip`
- after `store.truncate()`, parse available schedules:

```python
    for source in ("gtfs_path", "gtfs_njt"):
        snapshot = latest_snapshot(settings, source)
        if snapshot is not None:
            parse_gtfs(store.con, source, snapshot,
                       fetched_at=datetime.now(UTC).isoformat())
```

(add `from datetime import UTC, datetime` to imports)
- in the replay loop, after `store.write_trip_closed(ev)`:

```python
                train_matches = match_trip(store.con, ev)
                store.write_train_matches(train_matches)
                counts["train_matches"] += len(train_matches)
```

In `backend/engine/runner.py` `process_payload`, after `write_trip_closed`:

```python
                try:
                    self.store.write_train_matches(match_trip(self.store.con, ev))
                except Exception:
                    log.exception("train matching failed — trip stored unmatched")
```

(import `from backend.transit.matcher import match_trip`)

- [ ] **Step 5: Run the whole backend suite**

Run: `pytest backend/tests -q`
Expected: all PASS (existing rebuild tests: counts dict gains nothing when no
schedule is archived — `counts["train_matches"]` only increments when matches
exist, Counter tolerates).

- [ ] **Step 6: Commit**

```bash
git add backend/storage/derived.py backend/engine/rebuild.py backend/engine/runner.py \
        backend/tests/test_rebuild.py backend/tests/test_runner_live.py
git commit -m "feat: train matching wired into rebuild and live trip close"
```

---

### Task 11: Itinerary in trip detail API

**Files:**
- Modify: `backend/storage/derived.py`
- Test: `backend/tests/test_itinerary_api.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_itinerary_api.py
"""End-to-end: synth commute + fixture schedule → API itinerary with train leg."""

import base64
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.engine.machine import TripEngine
from backend.engine.params import EngineParams
from backend.engine.types import TripClosed
from backend.storage.raw import RawStore
from backend.tests.gtfs_fixture import build_gtfs_zip
from backend.tests.synth import commute


def test_trip_detail_includes_itinerary_with_train(settings):
    pts, _, _ = commute()
    eng = TripEngine(EngineParams(), geofences=[])
    closed = []
    for pt in pts:
        closed.extend(e for e in eng.process(pt) if isinstance(e, TripClosed))
    seg = next(s for s in closed[0].segments if s.mode == "vehicle")
    start = next(p for p in closed[0].points if p.ts >= seg.start_ts)
    end = max((p for p in closed[0].points if p.ts <= seg.end_ts), key=lambda p: p.ts)
    ny = ZoneInfo("America/New_York")

    def hms(epoch):
        dt = datetime.fromtimestamp(epoch, ny)
        return f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"

    z = build_gtfs_zip(
        [("S1", "Alpha", start.lat, start.lon), ("S2", "Beta", end.lat, end.lon)],
        [("T1", "WK", "Beta-bound", [("S1", hms(start.ts)), ("S2", hms(end.ts))])],
        route_name="Test Line",
    )
    RawStore(settings.data_dir).append(
        "gtfs_path",
        {"received_at": "2026-06-09T05:00:00+00:00",
         "payload": {"url": "u", "status": 200,
                     "b64": base64.b64encode(z).decode()}},
    )

    app = create_app(settings)
    with TestClient(app) as c:
        for pt in pts:
            c.post("/ingest/owntracks", json={"_type": "location", "tst": pt.ts,
                                              "lat": pt.lat, "lon": pt.lon,
                                              "acc": pt.accuracy_m})
        trip_id = c.get("/api/trips").json()[0]["trip_id"]
        detail = c.get(f"/api/trips/{trip_id}").json()

    itinerary = detail["itinerary"]
    modes = [leg["mode"] for leg in itinerary]
    assert "vehicle" in modes and "walk" in modes
    train_leg = next(leg for leg in itinerary if leg["mode"] == "vehicle")
    assert train_leg["train"]["gtfs_trip_id"] == "T1"
    assert train_leg["train"]["route_name"] == "Test Line"
    assert train_leg["train"]["board_stop"] == "Alpha"
    walk_leg = next(leg for leg in itinerary if leg["mode"] == "walk")
    assert walk_leg["train"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_itinerary_api.py -v`
Expected: FAIL — KeyError 'itinerary'.

- [ ] **Step 3: Implement (in `backend/storage/derived.py` `get_trip`)**

After building `segments`, build the itinerary and add it to the returned dict:

```python
        match_by_seg = {m["seg_index"]: m for m in self.matches_for_trip(trip_id)}
        itinerary = [
            {
                "mode": s["mode"],
                "start_ts": s["start_ts"],
                "end_ts": s["end_ts"],
                "duration_s": s["duration_s"],
                "distance_m": s["distance_m"],
                "train": match_by_seg.get(s["seg_index"]),
            }
            for s in segments
        ]
        return {
            "trip": self._trip_row_to_dict(row),
            "segments": segments,
            "points": points,
            "itinerary": itinerary,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_itinerary_api.py backend/tests/test_trips_api.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/storage/derived.py backend/tests/test_itinerary_api.py
git commit -m "feat: trip detail api exposes itinerary with train attribution"
```

---

### Task 12: Docs + final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Extend the README "Rewrite backend" section**

Add a "External data sources" subsection:
- Sources are enabled by configuring their URL; unset = disabled. Every fetch
  is archived verbatim before parsing (re-parseable forever).
- Env vars: `CT_PATH_GTFS_URL`, `CT_NJT_GTFS_URL`, `CT_PATH_RT_URL`,
  `CT_NJT_RT_TRIPUPDATES_URL`, `CT_NJT_RT_ALERTS_URL`,
  `CT_SOURCE_POLL_INTERVAL_S` (default 60), `CT_GTFS_REFRESH_INTERVAL_S`
  (default 86400).
- Known-good public URLs: PATH GTFS static
  `http://data.trilliumtransit.com/gtfs/path-nj-us/path-nj-us.zip`; PATH
  GTFS-RT (community) `https://path.transitdata.nyc/gtfsrt`. NJ Transit GTFS +
  GTFS-RT require a registered account at https://developer.njtransit.com —
  supply your authenticated URLs once registered.
- New endpoints: `GET /api/health/sources` (per-source freshness); trip detail
  now includes `itinerary` with per-leg train attribution.

- [ ] **Step 2: Final verification**

Run: `ruff format backend/ && ruff check src/ tests/ backend/ && ruff format --check src/ tests/ backend/ && pytest --tb=short -q`
Expected: clean; all tests pass (~375+).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: external source config and itinerary endpoints"
```

---

## Verification at phase end

1. Full suite green, ruff clean (CI paths).
2. Container smoke test (controller runs via podman): build, run with
   `CT_PATH_GTFS_URL` pointed at a local fixture file server OR simply verify
   `/api/health/sources` returns `[]` when no sources configured and the app
   boots clean.
3. `gh run list` green after push.
4. Live-data check (manual, post-deploy): set the PATH URLs in production,
   watch `/api/health/sources` freshness and S3 `raw/gtfs_path/` +
   `raw/rt_path/` objects appear.
