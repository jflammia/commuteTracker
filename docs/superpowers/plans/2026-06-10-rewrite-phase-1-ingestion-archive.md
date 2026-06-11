# Rewrite Phase 1: Ingestion + Archive Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the unbreakable write path of the rewrite — OwnTracks ingest → append-only raw JSONL → daily Parquet archive to S3 with read-back verification → DuckDB query layer — plus legacy data migration, with the new ingest fronting the old app via passthrough.

**Architecture:** New code lives in a top-level `backend/` package, fully independent of the legacy `src/` (which keeps running until cutover). The ingest endpoint appends to a local date-stamped JSONL file and returns 200 before anything else can fail. A daily job converts closed day-files to Parquet (payload kept as a verbatim JSON string — schema-stable forever), uploads to S3, verifies by read-back checksum, and only then deletes the local raw file. DuckDB queries archive + today's tail as one relation. Health is computed from the filesystem (no extra state to corrupt).

**Tech Stack:** Python 3.11, FastAPI, DuckDB, Polars, boto3 (moto for tests), httpx. No new runtime services.

**Spec:** `docs/superpowers/specs/2026-06-10-ground-up-rewrite-design.md` (this plan implements the "Storage" section, ingest portion of "Architecture", "Migration" steps 1–3 for raw data, and the ingestion-freshness slice of the watchdog).

**Phase roadmap (each phase = its own plan, written when it starts):**
1. **This plan** — ingestion + archive + migration
2. Trip engine (state machine, replay/rebuild, golden tests)
3. Sources + train matching (plugin framework, GTFS NJT/PATH, itineraries)
4. API + frontend shell + labeling workbench
5. Optimizer + Today view + Web Push + live SSE
6. Trends + Health view + cutover (legacy `src/` retired)

---

## File structure

```
backend/
  __init__.py
  config.py            # env-var settings (CT_* namespace), frozen dataclass
  app.py               # create_app(): FastAPI app, lifespan starts scheduler
  ingest/
    __init__.py
    routes.py          # POST /ingest/owntracks — always 200
    passthrough.py     # fire-and-forget forward to legacy receiver
  storage/
    __init__.py
    raw.py             # RawStore: append-only JSONL day files
    archive.py         # Archiver: closed day-files → Parquet → S3 + verify
    query.py           # DuckDB relation over archive + raw tail
  health/
    __init__.py
    ingestion.py       # filesystem-derived health snapshot
    routes.py          # GET /api/health/ingestion
  jobs/
    __init__.py
    daily.py           # minimal daily-at-hour runner (no APScheduler dep)
  tests/
    __init__.py
    conftest.py        # tmp data dir + settings fixtures
    test_config.py
    test_raw_store.py
    test_ingest.py
    test_passthrough.py
    test_archive_local.py
    test_archive_s3.py
    test_query.py
    test_health.py
    test_daily_job.py
    test_migrate_legacy.py
scripts/
  migrate_legacy_raw.py  # one-time: old SQLite/JSONL → new raw layout
Dockerfile.backend       # slim image running backend only
docker-compose.yml       # add `backend` service (modify)
pyproject.toml           # add backend/tests to testpaths, moto+httpx to dev (modify)
.github/workflows/ci.yml # lint+test backend/, build Dockerfile.backend (modify)
```

**Data layout** (`CT_DATA_DIR`, default `data_v2/`):

```
data_v2/
  raw/owntracks/2026-06-10.jsonl              # live append, one file per UTC day
  raw/owntracks_malformed/2026-06-10.jsonl    # unparseable bodies, still kept
  archive/owntracks/year=2026/month=06/day=09/data.parquet   # local mirror of S3
```

**Record envelope** (one JSON line per event):

```json
{"received_at": "2026-06-10T11:42:07.123456+00:00", "user": "justin", "device": "iphone", "payload": {…verbatim OwnTracks JSON…}}
```

Malformed: `{"received_at": "…", "raw": "<body as text>"}`.

**Parquet schema** (stable forever): `received_at TIMESTAMPTZ, user VARCHAR, device VARCHAR, payload VARCHAR` — payload is the verbatim JSON *string*; typed parsing happens at query time (`payload->>'$.tst'` etc.). This is what makes the archive immune to OwnTracks schema drift.

---

### Task 1: Scaffolding — package, pyproject, CI

**Files:**
- Create: `backend/__init__.py`, `backend/ingest/__init__.py`, `backend/storage/__init__.py`, `backend/health/__init__.py`, `backend/jobs/__init__.py`, `backend/tests/__init__.py`
- Modify: `pyproject.toml`
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Create empty packages**

```bash
mkdir -p backend/ingest backend/storage backend/health backend/jobs backend/tests
touch backend/__init__.py backend/ingest/__init__.py backend/storage/__init__.py \
      backend/health/__init__.py backend/jobs/__init__.py backend/tests/__init__.py
```

- [ ] **Step 2: Update `pyproject.toml`**

In `[project.optional-dependencies] dev`, add two entries (keep existing ones):

```toml
dev = [
    "jupyter",
    "matplotlib",
    "pytest",
    "ruff",
    "scikit-learn>=1.4",
    "moto[s3]>=5.0",
    "httpx>=0.27",
]
```

Change `[tool.pytest.ini_options]`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests", "backend/tests"]
```

- [ ] **Step 3: Update CI lint paths**

In `.github/workflows/ci.yml` change the two ruff lines:

```yaml
run: ruff check src/ tests/ backend/
```
```yaml
run: ruff format --check src/ tests/ backend/
```

- [ ] **Step 4: Install and verify the old suite still passes**

Run: `pip install -e ".[dev]" && pytest --tb=short -q`
Expected: all existing tests PASS (backend/tests is empty, collected as 0).

- [ ] **Step 5: Commit**

```bash
git add backend/ pyproject.toml .github/workflows/ci.yml
git commit -m "chore: scaffold backend package for rewrite phase 1"
```

---

### Task 2: Config module

**Files:**
- Create: `backend/config.py`
- Test: `backend/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_config.py
from pathlib import Path

from backend.config import Settings, load_settings


def test_defaults(monkeypatch):
    for var in ("CT_DATA_DIR", "CT_S3_BUCKET", "CT_PASSTHROUGH_URL", "CT_ARCHIVE_HOUR_UTC"):
        monkeypatch.delenv(var, raising=False)
    s = load_settings()
    assert s.data_dir == Path("data_v2")
    assert s.s3_bucket is None
    assert s.s3_prefix == "commute-tracker"
    assert s.passthrough_url is None
    assert s.archive_hour_utc == 6


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("CT_DATA_DIR", "/srv/ct")
    monkeypatch.setenv("CT_S3_BUCKET", "my-bucket")
    monkeypatch.setenv("CT_S3_PREFIX", "ct-prod")
    monkeypatch.setenv("CT_PASSTHROUGH_URL", "http://legacy:8080/pub")
    monkeypatch.setenv("CT_ARCHIVE_HOUR_UTC", "7")
    s = load_settings()
    assert s == Settings(
        data_dir=Path("/srv/ct"),
        s3_bucket="my-bucket",
        s3_prefix="ct-prod",
        passthrough_url="http://legacy:8080/pub",
        archive_hour_utc=7,
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError` on `backend.config`.

- [ ] **Step 3: Implement**

```python
# backend/config.py
"""Environment-variable configuration. CT_* namespace, no dotenv."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    s3_bucket: str | None
    s3_prefix: str
    passthrough_url: str | None
    archive_hour_utc: int


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("CT_DATA_DIR", "data_v2")),
        s3_bucket=os.environ.get("CT_S3_BUCKET") or None,
        s3_prefix=os.environ.get("CT_S3_PREFIX", "commute-tracker"),
        passthrough_url=os.environ.get("CT_PASSTHROUGH_URL") or None,
        archive_hour_utc=int(os.environ.get("CT_ARCHIVE_HOUR_UTC", "6")),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_config.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/config.py backend/tests/test_config.py
git commit -m "feat: backend settings via CT_* env vars"
```

---

### Task 3: RawStore — append-only JSONL day files

**Files:**
- Create: `backend/storage/raw.py`
- Create: `backend/tests/conftest.py`
- Test: `backend/tests/test_raw_store.py`

- [ ] **Step 1: Write shared fixtures**

```python
# backend/tests/conftest.py
import pytest

from backend.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path,
        s3_bucket=None,
        s3_prefix="commute-tracker",
        passthrough_url=None,
        archive_hour_utc=6,
    )
```

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/test_raw_store.py
import json

from backend.storage.raw import RawStore


def test_append_writes_one_line_to_dated_file(settings):
    store = RawStore(settings.data_dir)
    rec = {"received_at": "2026-06-10T11:42:07+00:00", "user": "justin", "payload": {"tst": 1}}
    path = store.append("owntracks", rec)
    assert path == settings.data_dir / "raw" / "owntracks" / "2026-06-10.jsonl"
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == rec


def test_append_accumulates_lines(settings):
    store = RawStore(settings.data_dir)
    for i in range(3):
        store.append("owntracks", {"received_at": "2026-06-10T00:00:00+00:00", "i": i})
    path = settings.data_dir / "raw" / "owntracks" / "2026-06-10.jsonl"
    assert len(path.read_text().splitlines()) == 3


def test_malformed_goes_to_separate_stream(settings):
    store = RawStore(settings.data_dir)
    path = store.append(
        "owntracks", {"received_at": "2026-06-10T00:00:00+00:00", "raw": "not json"},
        malformed=True,
    )
    assert path == settings.data_dir / "raw" / "owntracks_malformed" / "2026-06-10.jsonl"


def test_closed_day_files_lists_only_past_days(settings):
    store = RawStore(settings.data_dir)
    store.append("owntracks", {"received_at": "2026-06-08T00:00:00+00:00"})
    store.append("owntracks", {"received_at": "2026-06-09T00:00:00+00:00"})
    store.append("owntracks", {"received_at": "2026-06-10T00:00:00+00:00"})
    closed = store.closed_day_files("owntracks", today="2026-06-10")
    assert [p.name for p in closed] == ["2026-06-08.jsonl", "2026-06-09.jsonl"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest backend/tests/test_raw_store.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.storage.raw`.

- [ ] **Step 4: Implement**

```python
# backend/storage/raw.py
"""Append-only raw JSONL store. The write path must stay this simple."""

import json
import os
from pathlib import Path


class RawStore:
    def __init__(self, data_dir: Path):
        self._root = data_dir / "raw"

    def append(self, stream: str, record: dict, *, malformed: bool = False) -> Path:
        if malformed:
            stream = f"{stream}_malformed"
        day = record["received_at"][:10]  # ISO date prefix, UTC
        path = self._root / stream / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        return path

    def closed_day_files(self, stream: str, *, today: str) -> list[Path]:
        d = self._root / stream
        if not d.is_dir():
            return []
        return sorted(p for p in d.glob("*.jsonl") if p.stem < today)

    def day_file(self, stream: str, day: str) -> Path:
        return self._root / stream / f"{day}.jsonl"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest backend/tests/test_raw_store.py -v`
Expected: 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/storage/raw.py backend/tests/conftest.py backend/tests/test_raw_store.py
git commit -m "feat: append-only raw JSONL store with dated day files"
```

---

### Task 4: Ingest endpoint — always 200

**Files:**
- Create: `backend/ingest/routes.py`
- Create: `backend/app.py`
- Test: `backend/tests/test_ingest.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_ingest.py
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def test_valid_payload_returns_200_and_appends(client, settings):
    body = {"_type": "location", "tst": 1781100000, "lat": 40.7, "lon": -74.2}
    resp = client.post(
        "/ingest/owntracks",
        json=body,
        headers={"X-Limit-U": "justin", "X-Limit-D": "iphone"},
    )
    assert resp.status_code == 200
    assert resp.json() == []
    files = list((settings.data_dir / "raw" / "owntracks").glob("*.jsonl"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text().splitlines()[0])
    assert rec["payload"] == body
    assert rec["user"] == "justin"
    assert rec["device"] == "iphone"
    assert rec["received_at"].endswith("+00:00")


def test_malformed_body_returns_200_and_is_kept(client, settings):
    resp = client.post(
        "/ingest/owntracks",
        content=b"\x00not json at all",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    files = list((settings.data_dir / "raw" / "owntracks_malformed").glob("*.jsonl"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text().splitlines()[0])
    assert "not json at all" in rec["raw"]


def test_store_failure_still_returns_200(client, monkeypatch):
    from backend.storage.raw import RawStore

    def boom(self, *a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(RawStore, "append", boom)
    resp = client.post("/ingest/owntracks", json={"_type": "location"})
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_ingest.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.app`.

- [ ] **Step 3: Implement the app factory and route**

```python
# backend/app.py
"""FastAPI app factory for the rewrite backend."""

from fastapi import FastAPI

from backend.config import Settings, load_settings
from backend.ingest.routes import make_ingest_router
from backend.storage.raw import RawStore


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="commute-tracker backend")
    app.state.settings = settings
    app.state.raw_store = RawStore(settings.data_dir)
    app.include_router(make_ingest_router())
    return app


app = create_app()
```

```python
# backend/ingest/routes.py
"""OwnTracks ingest. Contract: ALWAYS return 200 with a JSON array body."""

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


def make_ingest_router() -> APIRouter:
    router = APIRouter()

    @router.post("/ingest/owntracks")
    async def ingest_owntracks(request: Request) -> JSONResponse:
        store = request.app.state.raw_store
        received_at = datetime.now(UTC).isoformat()
        try:
            body = await request.body()
            try:
                payload = json.loads(body)
                record = {
                    "received_at": received_at,
                    "user": request.headers.get("X-Limit-U"),
                    "device": request.headers.get("X-Limit-D"),
                    "payload": payload,
                }
                store.append("owntracks", record)
            except (json.JSONDecodeError, UnicodeDecodeError):
                store.append(
                    "owntracks",
                    {"received_at": received_at, "raw": body.decode("utf-8", errors="replace")},
                    malformed=True,
                )
        except Exception:
            log.exception("ingest failed past raw append — data may be lost")
        return JSONResponse(content=[], status_code=200)

    return router
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_ingest.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app.py backend/ingest/routes.py backend/tests/test_ingest.py
git commit -m "feat: owntracks ingest endpoint with unconditional 200"
```

---

### Task 5: Passthrough to legacy receiver

**Files:**
- Create: `backend/ingest/passthrough.py`
- Modify: `backend/ingest/routes.py`
- Modify: `backend/app.py`
- Test: `backend/tests/test_passthrough.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_passthrough.py
import asyncio

import httpx
import pytest

from backend.ingest.passthrough import Passthrough


def test_disabled_when_no_url():
    pt = Passthrough(None)
    assert pt.enabled is False


@pytest.mark.anyio
async def test_forwards_body_and_headers():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        seen["u"] = request.headers.get("X-Limit-U")
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    pt = Passthrough("http://legacy:8080/pub", transport=transport)
    await pt.forward(b'{"_type":"location"}', {"X-Limit-U": "justin"})
    await asyncio.sleep(0)  # let the fire-and-forget task run
    assert seen["url"] == "http://legacy:8080/pub"
    assert seen["body"] == b'{"_type":"location"}'
    assert seen["u"] == "justin"


@pytest.mark.anyio
async def test_legacy_failure_never_raises():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("legacy down")

    pt = Passthrough("http://legacy:8080/pub", transport=httpx.MockTransport(handler))
    await pt.forward(b"{}", {})  # must not raise
    await asyncio.sleep(0)
```

Add to `backend/tests/conftest.py`:

```python
@pytest.fixture
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_passthrough.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.ingest.passthrough`.

- [ ] **Step 3: Implement**

```python
# backend/ingest/passthrough.py
"""Fire-and-forget forward of ingested bodies to the legacy receiver.

Exists only for the migration period; failures are logged, never propagated.
"""

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

_FORWARD_HEADERS = ("X-Limit-U", "X-Limit-D", "Content-Type")


class Passthrough:
    def __init__(self, url: str | None, transport: httpx.AsyncBaseTransport | None = None):
        self._url = url
        self._client = (
            httpx.AsyncClient(transport=transport, timeout=5.0) if url is not None else None
        )

    @property
    def enabled(self) -> bool:
        return self._url is not None

    async def forward(self, body: bytes, headers: dict) -> None:
        if self._client is None:
            return
        fwd = {k: v for k, v in headers.items() if k in _FORWARD_HEADERS and v is not None}

        async def _send():
            try:
                await self._client.post(self._url, content=body, headers=fwd)
            except Exception as exc:
                log.warning("passthrough to legacy failed: %s", exc)

        asyncio.get_running_loop().create_task(_send())

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
```

- [ ] **Step 4: Wire into the ingest route and app state**

In `backend/app.py`, inside `create_app` after `app.state.raw_store = …` add:

```python
from backend.ingest.passthrough import Passthrough

app.state.passthrough = Passthrough(settings.passthrough_url)
```

In `backend/ingest/routes.py`, at the end of `ingest_owntracks` (just before `return`), add:

```python
        try:
            await request.app.state.passthrough.forward(body, dict(request.headers))
        except Exception:
            log.exception("passthrough dispatch failed")
```

Note: `body` is only bound if `await request.body()` succeeded; the surrounding
`try/except Exception` already guards this — keep the passthrough call inside
its own try so a passthrough bug can never affect the 200.  Place it inside the
outer `try`, after the append block.

- [ ] **Step 5: Run the full backend suite**

Run: `pytest backend/tests -v`
Expected: all PASS (passthrough disabled in ingest tests — no URL in settings fixture).

- [ ] **Step 6: Commit**

```bash
git add backend/ingest/passthrough.py backend/ingest/routes.py backend/app.py \
        backend/tests/test_passthrough.py backend/tests/conftest.py
git commit -m "feat: fire-and-forget passthrough to legacy receiver"
```

---

### Task 6: Archiver — closed day-files to local Parquet

**Files:**
- Create: `backend/storage/archive.py`
- Test: `backend/tests/test_archive_local.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_archive_local.py
import json

import duckdb

from backend.storage.archive import Archiver
from backend.storage.raw import RawStore


def _seed(settings):
    store = RawStore(settings.data_dir)
    for day, n in (("2026-06-08", 2), ("2026-06-09", 3), ("2026-06-10", 1)):
        for i in range(n):
            store.append(
                "owntracks",
                {
                    "received_at": f"{day}T0{i}:00:00+00:00",
                    "user": "justin",
                    "device": "iphone",
                    "payload": {"_type": "location", "tst": i},
                },
            )
    return store


def test_archives_closed_days_to_hive_parquet(settings):
    _seed(settings)
    archiver = Archiver(settings)
    results = archiver.run(today="2026-06-10")
    assert [r.day for r in results] == ["2026-06-08", "2026-06-09"]
    assert all(r.ok for r in results)
    p8 = (
        settings.data_dir / "archive" / "owntracks"
        / "year=2026" / "month=06" / "day=08" / "data.parquet"
    )
    assert p8.exists()
    rows = duckdb.sql(f"SELECT count(*) c, min(payload) FROM read_parquet('{p8}')").fetchone()
    assert rows[0] == 2
    assert json.loads(rows[1])["_type"] == "location"


def test_raw_file_removed_after_local_archive(settings):
    _seed(settings)
    Archiver(settings).run(today="2026-06-10")
    raw_dir = settings.data_dir / "raw" / "owntracks"
    assert [p.name for p in sorted(raw_dir.glob("*.jsonl"))] == ["2026-06-10.jsonl"]


def test_idempotent_rerun(settings):
    _seed(settings)
    a = Archiver(settings)
    a.run(today="2026-06-10")
    results = a.run(today="2026-06-10")
    assert results == []  # nothing left to do


def test_today_file_untouched(settings):
    _seed(settings)
    Archiver(settings).run(today="2026-06-10")
    assert (settings.data_dir / "raw" / "owntracks" / "2026-06-10.jsonl").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_archive_local.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.storage.archive`.

- [ ] **Step 3: Implement (local path; S3 hooks land in Task 7)**

```python
# backend/storage/archive.py
"""Daily archival: closed raw JSONL day-files → Parquet (→ S3, Task 7).

Local raw files are deleted only after the Parquet copy is verified.
Payload is stored as a verbatim JSON string — schema-stable forever.
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from backend.config import Settings
from backend.storage.raw import RawStore

log = logging.getLogger(__name__)

STREAMS = ("owntracks", "owntracks_malformed")


@dataclass(frozen=True)
class ArchiveResult:
    stream: str
    day: str
    rows: int
    ok: bool
    error: str | None = None


def _to_frame(jsonl_path: Path) -> pl.DataFrame:
    rows = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        rows.append(
            {
                "received_at": rec["received_at"],
                "user": rec.get("user"),
                "device": rec.get("device"),
                "payload": json.dumps(
                    rec.get("payload", rec.get("raw")), separators=(",", ":"), ensure_ascii=False
                ),
            }
        )
    return pl.DataFrame(
        rows,
        schema={"received_at": pl.String, "user": pl.String, "device": pl.String,
                "payload": pl.String},
    ).with_columns(pl.col("received_at").str.to_datetime(time_zone="UTC"))


class Archiver:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._store = RawStore(settings.data_dir)

    def _parquet_path(self, stream: str, day: str) -> Path:
        y, m, d = day.split("-")
        return (
            self._settings.data_dir / "archive" / stream
            / f"year={y}" / f"month={m}" / f"day={d}" / "data.parquet"
        )

    def run(self, today: str | None = None) -> list[ArchiveResult]:
        today = today or datetime.now(UTC).strftime("%Y-%m-%d")
        results = []
        for stream in STREAMS:
            for raw_file in self._store.closed_day_files(stream, today=today):
                results.append(self._archive_one(stream, raw_file))
        return results

    def _archive_one(self, stream: str, raw_file: Path) -> ArchiveResult:
        day = raw_file.stem
        try:
            frame = _to_frame(raw_file)
            pq = self._parquet_path(stream, day)
            pq.parent.mkdir(parents=True, exist_ok=True)
            frame.write_parquet(pq)
            if pl.read_parquet(pq).height != frame.height:
                raise RuntimeError("parquet row-count mismatch after write")
            self._upload_and_verify(stream, day, pq)
            raw_file.unlink()
            return ArchiveResult(stream=stream, day=day, rows=frame.height, ok=True)
        except Exception as exc:
            log.exception("archive failed for %s/%s — raw file kept", stream, day)
            return ArchiveResult(stream=stream, day=day, rows=0, ok=False, error=str(exc))

    def _upload_and_verify(self, stream: str, day: str, pq: Path) -> None:
        """S3 upload + read-back verification. No-op until Task 7 wires S3."""
        if self._settings.s3_bucket is None:
            return
        raise NotImplementedError  # implemented in Task 7
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_archive_local.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/storage/archive.py backend/tests/test_archive_local.py
git commit -m "feat: daily archiver converts closed raw days to hive parquet"
```

---

### Task 7: Archiver — S3 upload with read-back verification

**Files:**
- Modify: `backend/storage/archive.py`
- Test: `backend/tests/test_archive_s3.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_archive_s3.py
import boto3
import pytest
from moto import mock_aws

from backend.config import Settings
from backend.storage.archive import Archiver
from backend.storage.raw import RawStore


@pytest.fixture
def s3_settings(tmp_path):
    return Settings(
        data_dir=tmp_path,
        s3_bucket="ct-test",
        s3_prefix="commute-tracker",
        passthrough_url=None,
        archive_hour_utc=6,
    )


def _seed(s3_settings):
    store = RawStore(s3_settings.data_dir)
    store.append(
        "owntracks",
        {"received_at": "2026-06-09T01:00:00+00:00", "user": "j", "device": "d",
         "payload": {"tst": 1}},
    )


@mock_aws
def test_uploads_to_partitioned_key(s3_settings):
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="ct-test")
    _seed(s3_settings)
    results = Archiver(s3_settings).run(today="2026-06-10")
    assert results[0].ok
    objs = boto3.client("s3").list_objects_v2(Bucket="ct-test")["Contents"]
    keys = [o["Key"] for o in objs]
    assert keys == [
        "commute-tracker/raw/owntracks/year=2026/month=06/day=09/data.parquet"
    ]


@mock_aws
def test_upload_failure_keeps_raw_file(s3_settings):
    # bucket does not exist → upload fails → raw file must survive
    _seed(s3_settings)
    results = Archiver(s3_settings).run(today="2026-06-10")
    assert results[0].ok is False
    raw = s3_settings.data_dir / "raw" / "owntracks" / "2026-06-09.jsonl"
    assert raw.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_archive_s3.py -v`
Expected: first test FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement `_upload_and_verify`**

Replace the stub in `backend/storage/archive.py`:

```python
    def _upload_and_verify(self, stream: str, day: str, pq: Path) -> None:
        if self._settings.s3_bucket is None:
            return
        import hashlib

        import boto3

        y, m, d = day.split("-")
        key = (
            f"{self._settings.s3_prefix}/raw/{stream}/"
            f"year={y}/month={m}/day={d}/data.parquet"
        )
        data = pq.read_bytes()
        client = boto3.client("s3")
        client.put_object(Bucket=self._settings.s3_bucket, Key=key, Body=data)
        echoed = client.get_object(Bucket=self._settings.s3_bucket, Key=key)["Body"].read()
        if hashlib.sha256(echoed).digest() != hashlib.sha256(data).digest():
            raise RuntimeError(f"S3 read-back checksum mismatch for {key}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_archive_s3.py backend/tests/test_archive_local.py -v`
Expected: all PASS (local tests still pass — bucket is None there).

- [ ] **Step 5: Commit**

```bash
git add backend/storage/archive.py backend/tests/test_archive_s3.py
git commit -m "feat: archive uploads to s3 with read-back checksum verification"
```

---

### Task 8: DuckDB query layer — archive + raw tail as one relation

**Files:**
- Create: `backend/storage/query.py`
- Test: `backend/tests/test_query.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_query.py
from backend.storage.archive import Archiver
from backend.storage.query import EventQuery
from backend.storage.raw import RawStore


def _seed(settings):
    store = RawStore(settings.data_dir)
    store.append(
        "owntracks",
        {"received_at": "2026-06-09T01:00:00+00:00", "user": "j", "device": "d",
         "payload": {"_type": "location", "tst": 100}},
    )
    store.append(
        "owntracks",
        {"received_at": "2026-06-10T02:00:00+00:00", "user": "j", "device": "d",
         "payload": {"_type": "location", "tst": 200}},
    )
    Archiver(settings).run(today="2026-06-10")  # 06-09 → parquet; 06-10 stays raw


def test_union_of_archive_and_tail(settings):
    _seed(settings)
    q = EventQuery(settings)
    df = q.events("owntracks").pl()
    assert df.height == 2
    assert sorted(df["source"].to_list()) == ["archive", "raw"]


def test_payload_is_queryable_json(settings):
    _seed(settings)
    q = EventQuery(settings)
    rel = q.events("owntracks")
    tsts = q.sql(
        "SELECT CAST(payload->>'$.tst' AS INT) t FROM rel ORDER BY t", rel=rel
    ).fetchall()
    assert [r[0] for r in tsts] == [100, 200]


def test_empty_system_returns_empty_relation(settings):
    q = EventQuery(settings)
    assert q.events("owntracks").pl().height == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_query.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.storage.query`.

- [ ] **Step 3: Implement**

```python
# backend/storage/query.py
"""DuckDB view over the parquet archive plus today's raw JSONL tail."""

import duckdb

from backend.config import Settings

_COLS = "received_at TIMESTAMPTZ, \"user\" VARCHAR, device VARCHAR, payload VARCHAR"
_RAW_COLUMNS = (
    "{received_at: 'TIMESTAMPTZ', user: 'VARCHAR', device: 'VARCHAR', payload: 'JSON'}"
)


class EventQuery:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._con = duckdb.connect()

    def events(self, stream: str) -> duckdb.DuckDBPyRelation:
        archive_glob = str(
            self._settings.data_dir / "archive" / stream / "**" / "data.parquet"
        )
        raw_glob = str(self._settings.data_dir / "raw" / stream / "*.jsonl")
        parts = []
        if list((self._settings.data_dir / "archive" / stream).glob("**/data.parquet")):
            parts.append(
                f"SELECT received_at, \"user\", device, payload, 'archive' AS source "
                f"FROM read_parquet('{archive_glob}')"
            )
        if list((self._settings.data_dir / "raw" / stream).glob("*.jsonl")):
            parts.append(
                f"SELECT received_at, \"user\", device, CAST(payload AS VARCHAR) AS payload, "
                f"'raw' AS source "
                f"FROM read_json('{raw_glob}', format='newline_delimited', "
                f"columns={_RAW_COLUMNS})"
            )
        if not parts:
            parts.append(
                f"SELECT * FROM (VALUES (NULL::TIMESTAMPTZ, NULL::VARCHAR, NULL::VARCHAR, "
                f"NULL::VARCHAR, NULL::VARCHAR)) t(received_at, \"user\", device, payload, "
                f"source) WHERE false"
            )
        return self._con.sql(" UNION ALL ".join(parts))

    def sql(self, query: str, **relations: duckdb.DuckDBPyRelation):
        for name, rel in relations.items():
            self._con.register(name, rel)
        return self._con.sql(query)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_query.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/storage/query.py backend/tests/test_query.py
git commit -m "feat: duckdb event query over archive plus raw tail"
```

---

### Task 9: Daily job runner

**Files:**
- Create: `backend/jobs/daily.py`
- Modify: `backend/app.py`
- Test: `backend/tests/test_daily_job.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_daily_job.py
from datetime import UTC, datetime

from backend.jobs.daily import next_run_at


def test_next_run_today_if_before_hour():
    now = datetime(2026, 6, 10, 4, 30, tzinfo=UTC)
    assert next_run_at(now, hour_utc=6) == datetime(2026, 6, 10, 6, 0, tzinfo=UTC)


def test_next_run_tomorrow_if_past_hour():
    now = datetime(2026, 6, 10, 6, 0, 1, tzinfo=UTC)
    assert next_run_at(now, hour_utc=6) == datetime(2026, 6, 11, 6, 0, tzinfo=UTC)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_daily_job.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.jobs.daily`.

- [ ] **Step 3: Implement**

```python
# backend/jobs/daily.py
"""Minimal daily-at-hour scheduler. One asyncio task, no library dependency."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)


def next_run_at(now: datetime, *, hour_utc: int) -> datetime:
    candidate = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


async def run_daily(fn: Callable[[], object], *, hour_utc: int) -> None:
    while True:
        now = datetime.now(UTC)
        wait = (next_run_at(now, hour_utc=hour_utc) - now).total_seconds()
        await asyncio.sleep(wait)
        try:
            fn()
        except Exception:
            log.exception("daily job failed; will retry tomorrow")
```

- [ ] **Step 4: Wire into the app lifespan**

Replace `backend/app.py` content with:

```python
# backend/app.py
"""FastAPI app factory for the rewrite backend."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.config import Settings, load_settings
from backend.ingest.passthrough import Passthrough
from backend.ingest.routes import make_ingest_router
from backend.jobs.daily import run_daily
from backend.storage.archive import Archiver
from backend.storage.raw import RawStore


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        archiver = Archiver(settings)
        task = asyncio.create_task(
            run_daily(archiver.run, hour_utc=settings.archive_hour_utc)
        )
        yield
        task.cancel()
        await app.state.passthrough.aclose()

    app = FastAPI(title="commute-tracker backend", lifespan=lifespan)
    app.state.settings = settings
    app.state.raw_store = RawStore(settings.data_dir)
    app.state.passthrough = Passthrough(settings.passthrough_url)
    app.include_router(make_ingest_router())
    return app


app = create_app()
```

- [ ] **Step 5: Run the whole backend suite**

Run: `pytest backend/tests -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/jobs/daily.py backend/app.py backend/tests/test_daily_job.py
git commit -m "feat: daily archive job wired into app lifespan"
```

---

### Task 10: Ingestion health endpoint

**Files:**
- Create: `backend/health/ingestion.py`
- Create: `backend/health/routes.py`
- Modify: `backend/app.py`
- Test: `backend/tests/test_health.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_health.py
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.health.ingestion import ingestion_snapshot
from backend.storage.raw import RawStore


def test_snapshot_empty_system(settings):
    snap = ingestion_snapshot(settings, now_iso="2026-06-10T12:00:00+00:00")
    assert snap == {
        "last_event_at": None,
        "age_seconds": None,
        "today_event_count": 0,
        "raw_backlog_days": 0,
    }


def test_snapshot_counts_and_backlog(settings):
    store = RawStore(settings.data_dir)
    store.append("owntracks", {"received_at": "2026-06-08T01:00:00+00:00"})
    store.append("owntracks", {"received_at": "2026-06-10T11:00:00+00:00"})
    store.append("owntracks", {"received_at": "2026-06-10T11:30:00+00:00"})
    snap = ingestion_snapshot(settings, now_iso="2026-06-10T12:00:00+00:00")
    assert snap["last_event_at"] == "2026-06-10T11:30:00+00:00"
    assert snap["age_seconds"] == 1800
    assert snap["today_event_count"] == 2
    assert snap["raw_backlog_days"] == 1  # 06-08 closed but unarchived


def test_endpoint(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        resp = client.get("/api/health/ingestion")
    assert resp.status_code == 200
    assert resp.json()["today_event_count"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.health.ingestion`.

- [ ] **Step 3: Implement**

```python
# backend/health/ingestion.py
"""Ingestion health derived purely from the filesystem — no state to corrupt.

A closed day-file still sitting in raw/ means the archiver hasn't succeeded
on it yet: that IS the backlog signal (cleanup only happens after verified
upload).
"""

import json
from datetime import datetime

from backend.config import Settings
from backend.storage.raw import RawStore


def ingestion_snapshot(settings: Settings, *, now_iso: str) -> dict:
    now = datetime.fromisoformat(now_iso)
    today = now_iso[:10]
    store = RawStore(settings.data_dir)
    today_file = store.day_file("owntracks", today)

    last_event_at = None
    today_count = 0
    if today_file.exists():
        lines = today_file.read_text(encoding="utf-8").splitlines()
        today_count = len(lines)
        if lines:
            last_event_at = json.loads(lines[-1])["received_at"]

    age = None
    if last_event_at is not None:
        age = int((now - datetime.fromisoformat(last_event_at)).total_seconds())

    backlog = len(store.closed_day_files("owntracks", today=today))
    return {
        "last_event_at": last_event_at,
        "age_seconds": age,
        "today_event_count": today_count,
        "raw_backlog_days": backlog,
    }
```

```python
# backend/health/routes.py
from datetime import UTC, datetime

from fastapi import APIRouter, Request

from backend.health.ingestion import ingestion_snapshot


def make_health_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/health/ingestion")
    async def health_ingestion(request: Request) -> dict:
        return ingestion_snapshot(
            request.app.state.settings, now_iso=datetime.now(UTC).isoformat()
        )

    return router
```

In `backend/app.py` add the import and, after the ingest router include:

```python
from backend.health.routes import make_health_router

    app.include_router(make_health_router())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/health/ backend/app.py backend/tests/test_health.py
git commit -m "feat: filesystem-derived ingestion health endpoint"
```

---

### Task 11: Legacy migration script

**Files:**
- Create: `scripts/migrate_legacy_raw.py`
- Test: `backend/tests/test_migrate_legacy.py`

The old system stores events in SQLite `location_records(id, received_at, msg_type, user, device, payload TEXT, s3_synced_at)`. `received_at` is unreliable (bulk imports), so day partitioning uses the OwnTracks `tst` epoch inside the payload, falling back to `received_at` when absent.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_migrate_legacy.py
import json
import sqlite3

from scripts.migrate_legacy_raw import migrate, record_from_row


def test_record_uses_payload_tst_for_received_at():
    row = ("2026-03-27 04:42:07", "justin", "iphone", '{"_type":"location","tst":1742400000}')
    rec = record_from_row(*row)
    assert rec["received_at"] == "2025-03-19T16:00:00+00:00"  # 1742400000 epoch
    assert rec["payload"]["tst"] == 1742400000
    assert rec["user"] == "justin"


def test_record_falls_back_to_received_at():
    row = ("2026-03-27 04:42:07", "justin", "iphone", '{"_type":"status"}')
    rec = record_from_row(*row)
    assert rec["received_at"] == "2026-03-27T04:42:07+00:00"


def test_migrate_writes_day_files_and_reports(settings, tmp_path):
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE location_records (id INTEGER PRIMARY KEY, received_at TEXT, "
        "msg_type TEXT, user TEXT, device TEXT, payload TEXT, s3_synced_at TEXT)"
    )
    con.executemany(
        "INSERT INTO location_records (received_at, msg_type, user, device, payload) "
        "VALUES (?, 'location', 'justin', 'iphone', ?)",
        [
            ("2026-03-27 04:42:07", '{"_type":"location","tst":1742400000}'),
            ("2026-03-27 04:42:08", '{"_type":"location","tst":1742400060}'),
            ("2026-03-27 04:42:09", '{"_type":"location","tst":1742500000}'),
        ],
    )
    con.commit()
    con.close()

    report = migrate(db, settings.data_dir)
    assert report["total"] == 3
    day_files = sorted((settings.data_dir / "raw" / "owntracks").glob("*.jsonl"))
    assert [p.name for p in day_files] == ["2025-03-19.jsonl", "2025-03-20.jsonl"]
    first = json.loads(day_files[0].read_text().splitlines()[0])
    assert first["payload"]["_type"] == "location"
    assert report["per_day"]["2025-03-19"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_migrate_legacy.py -v`
Expected: FAIL — `ModuleNotFoundError: scripts.migrate_legacy_raw` (add `scripts/__init__.py` if import fails on package resolution — create it as an empty file in that case).

- [ ] **Step 3: Implement**

```python
# scripts/migrate_legacy_raw.py
"""One-time migration: legacy SQLite location_records → new raw JSONL layout.

Usage:
    python -m scripts.migrate_legacy_raw data/commute_tracker.db [CT_DATA_DIR]

After running, the normal archiver converts the day files to Parquet/S3.
Idempotency: run against an EMPTY data_dir only — the script refuses to
append into a raw dir that already has files (would duplicate events).
"""

import json
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from backend.storage.raw import RawStore


def record_from_row(received_at: str, user: str, device: str, payload_text: str) -> dict:
    payload = json.loads(payload_text)
    tst = payload.get("tst")
    if isinstance(tst, (int, float)):
        ts = datetime.fromtimestamp(tst, tz=UTC)
    else:
        ts = datetime.fromisoformat(received_at.replace(" ", "T")).replace(tzinfo=UTC)
    return {
        "received_at": ts.isoformat(),
        "user": user,
        "device": device,
        "payload": payload,
    }


def migrate(db_path: Path, data_dir: Path) -> dict:
    raw_dir = data_dir / "raw" / "owntracks"
    if raw_dir.exists() and any(raw_dir.glob("*.jsonl")):
        raise SystemExit(f"refusing: {raw_dir} already contains raw files")

    store = RawStore(data_dir)
    per_day: Counter = Counter()
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT received_at, user, device, payload FROM location_records ORDER BY id"
        )
        total = 0
        for received_at, user, device, payload_text in rows:
            try:
                rec = record_from_row(received_at, user, device, payload_text)
                store.append("owntracks", rec)
                per_day[rec["received_at"][:10]] += 1
            except (json.JSONDecodeError, ValueError):
                store.append(
                    "owntracks",
                    {"received_at": f"{received_at[:10]}T00:00:00+00:00", "raw": payload_text},
                    malformed=True,
                )
            total += 1
    finally:
        con.close()
    return {"total": total, "per_day": dict(sorted(per_day.items()))}


if __name__ == "__main__":
    db = Path(sys.argv[1])
    data_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data_v2")
    report = migrate(db, data_dir)
    print(json.dumps(report, indent=2))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_migrate_legacy.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/migrate_legacy_raw.py backend/tests/test_migrate_legacy.py
git commit -m "feat: legacy sqlite to raw jsonl migration with day report"
```

---

### Task 12: Docker image + compose + CI build

**Files:**
- Create: `Dockerfile.backend`
- Modify: `docker-compose.yml`
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Write `Dockerfile.backend`**

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend/ ./backend/
COPY scripts/ ./scripts/
RUN pip install --no-cache-dir fastapi uvicorn polars duckdb boto3 httpx pyarrow

# No package install: backend/ is imported from the workdir
ENV PYTHONPATH=/app

EXPOSE 8090
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8090"]
```

- [ ] **Step 2: Add the service to `docker-compose.yml`**

Read the existing file first; add this service alongside the existing ones (do not touch them):

```yaml
  backend:
    build:
      context: .
      dockerfile: Dockerfile.backend
    ports:
      - "8090:8090"
    environment:
      CT_DATA_DIR: /data
      CT_PASSTHROUGH_URL: http://receiver:8080/pub
    volumes:
      - ./data_v2:/data
    restart: unless-stopped
```

(If the legacy receiver service has a different name than `receiver` in the existing compose file, use that name in `CT_PASSTHROUGH_URL`.)

- [ ] **Step 3: Add CI build step**

In `.github/workflows/ci.yml`, after the existing docker build step add:

```yaml
      - name: Build backend Docker image
        run: docker build -f Dockerfile.backend -t commute-tracker-backend:test .
```

- [ ] **Step 4: Verify locally**

Run: `docker build -f Dockerfile.backend -t commute-tracker-backend:test . && docker run --rm -d -p 8090:8090 --name ct-backend-test commute-tracker-backend:test && sleep 2 && curl -s http://localhost:8090/api/health/ingestion && docker rm -f ct-backend-test`
Expected: JSON health snapshot (`{"last_event_at": null, …}`).

- [ ] **Step 5: Run the entire suite (old + new) and lint**

Run: `ruff format backend/ scripts/ && ruff check backend/ scripts/ && pytest --tb=short -q`
Expected: clean format, no lint errors, all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile.backend docker-compose.yml .github/workflows/ci.yml
git commit -m "feat: backend docker image and compose service with passthrough"
```

---

### Task 13: Production migration runbook (manual, after deploy)

Not code — operator steps once Phase 1 is deployed. Captured here so they aren't lost:

- [ ] **Step 1:** Deploy the new backend container alongside the legacy app (compose/Komodo). Point OwnTracks at `https://<host>/ingest/owntracks` (new front door); confirm legacy keeps receiving via passthrough (legacy dashboard still updates).
- [ ] **Step 2:** Copy the production legacy SQLite + any existing raw JSONL to the host, run `python -m scripts.migrate_legacy_raw <db> <CT_DATA_DIR>` (data dir must be empty of prior raw files except today's live file — run it BEFORE switching OwnTracks, or into a staging dir merged carefully).
- [ ] **Step 3:** Compare the migration report's totals/per-day counts against `SELECT COUNT(*) FROM location_records` and the legacy system's daily counts. Investigate any mismatch before proceeding.
- [ ] **Step 4:** Set `CT_S3_BUCKET` + AWS credentials, trigger archive (restart container or wait for the daily hour), verify Parquet objects appear in S3 and `raw_backlog_days` drops to 0 in `/api/health/ingestion`.

---

## Verification at phase end

1. `pytest --tb=short -q` — entire suite (legacy + backend) green.
2. `ruff check src/ tests/ backend/ scripts/ && ruff format --check src/ tests/ backend/ scripts/` — clean.
3. `gh run list --limit 2` after push — CI green.
4. Manual: POST a sample OwnTracks payload to local `/ingest/owntracks`, see it in the raw file, run `Archiver.run(today=<tomorrow>)` in a REPL, confirm Parquet + (with moto or real bucket) S3 object, query both via `EventQuery`.
