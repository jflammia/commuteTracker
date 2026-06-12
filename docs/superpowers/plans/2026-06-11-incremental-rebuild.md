# Incremental Boot Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make backend restarts fast as history grows by replaying only events since a persisted checkpoint, instead of truncating the derived store and replaying the entire archive on every boot.

**Architecture:** `EngineRunner.start` currently calls `rebuild(settings)`, which truncates the derived store and replays *all* owntracks + label events through a fresh `TripEngine` — O(total points), ~1ms/point (321k → ~6 min on prod 2026-06-11, growing unboundedly). This plan adds an **incremental path**: persist a checkpoint `{hwm, engine_state}` (the last-processed event timestamp + the serialized `EngineState`) alongside the persistent derived store; on boot, restore the engine and replay only events with `received_at > hwm`. The engine is already "pure with respect to time" (replay == live) and `EngineState` is already serializable (`to_dict`/`from_dict`), and `test_replay_equivalence.py` already proves split-replay == full-replay at the engine level — so this is mostly wiring + a checkpoint store + a store-level equivalence test. **Full rebuild remains the fallback** whenever the checkpoint is missing, unreadable, or version-mismatched, so correctness is never worse than today.

**Tech Stack:** Python 3.11, DuckDB derived store, dataclass `EngineState`, pytest. No new dependencies.

**Key invariant:** an incremental rebuild from a valid checkpoint MUST produce byte-identical derived tables to a full rebuild over the same archive. This is the critical correctness gate (Task 5).

---

## File Structure

- `backend/storage/derived.py` (modify): make `write_train_matches` and `write_rejected` idempotent-per-trip so boundary reprocessing can't duplicate rows.
- `backend/engine/checkpoint.py` (create): `RebuildCheckpoint` — load/save `{version, hwm, engine_state}` JSON at `<data_dir>/derived/rebuild_checkpoint.json`.
- `backend/engine/rebuild.py` (modify): add an incremental path to `rebuild()`; write the checkpoint at the end of every rebuild.
- `backend/engine/runner.py` (modify): `EngineRunner.start` uses the incremental path; persist the checkpoint on trip-close and on `close()`.
- `backend/tests/test_checkpoint.py` (create): checkpoint round-trip + corruption/missing handling.
- `backend/tests/test_incremental_rebuild.py` (create): store-level equivalence (incremental == full), fallback paths, boundary trip, crash-reprocessing idempotency.
- `backend/tests/test_derived_store.py` (modify): add idempotency assertions for train_matches/rejected.

---

## Task 1: Idempotent train-match and rejected writes

**Files:**
- Modify: `backend/storage/derived.py:157` (`write_train_matches`), `:151` (`write_rejected`)
- Test: `backend/tests/test_derived_store.py`

Reprocessing a boundary trip during an incremental replay (or after a crash between a derived write and the checkpoint update) must not duplicate rows. `write_trip_closed`, `write_leg_observations`, and `apply_label` already DELETE-by-key before INSERT. `write_train_matches` and `write_rejected` are plain INSERTs.

- [ ] **Step 1: Write failing test** in `test_derived_store.py`:

```python
def test_write_train_matches_is_idempotent_per_trip(derived_store):
    from backend.engine.types import Trip, TripClosed
    # write a trip + two matches, then re-write the same matches; expect no duplication
    matches = [_synth_train_match(trip_id="t1"), _synth_train_match(trip_id="t1")]
    derived_store.write_train_matches(matches)
    derived_store.write_train_matches(matches)  # re-write (reprocessing)
    rows = derived_store.con.execute(
        "SELECT COUNT(*) FROM train_matches WHERE trip_id = 't1'"
    ).fetchone()[0]
    assert rows == 2  # not 4
```
(Use the existing fixtures/helpers in `test_derived_store.py`; mirror an existing train-match row shape. Add an analogous `test_write_rejected_is_idempotent_per_trip` keyed on `trip_id` if rejected rows carry one, else key on `(point_ts)` — inspect the `rejected_points` schema first.)

- [ ] **Step 2: Run it, confirm it fails** (currently 4 rows): `python -m pytest backend/tests/test_derived_store.py -k idempotent -v`
- [ ] **Step 3: Implement** — in `write_train_matches`, before the `executemany` INSERT, delete existing rows for the affected trip_id(s):

```python
def write_train_matches(self, matches: list) -> None:
    if not matches:
        return
    trip_ids = {m.trip_id for m in matches}
    for tid in trip_ids:
        self._con.execute("DELETE FROM train_matches WHERE trip_id = ?", [tid])
    self._con.executemany("INSERT INTO train_matches VALUES (?,?,?,?,?,?,?,?,?,?)", [...])
```
Apply the same guard to `write_rejected` keyed on its natural key. **Match the existing column tuples exactly** — read the current method bodies first.

- [ ] **Step 4: Run tests, confirm pass.** Also run the full `test_derived_store.py` to ensure nothing regressed.
- [ ] **Step 5: Commit** — `fix(derived): make train-match and rejected writes idempotent per trip`

## Task 2: RebuildCheckpoint store

**Files:**
- Create: `backend/engine/checkpoint.py`
- Test: `backend/tests/test_checkpoint.py`

- [ ] **Step 1: Write failing test** `test_checkpoint.py`:

```python
from backend.engine.checkpoint import RebuildCheckpoint
from backend.engine.machine import EngineState

def test_save_and_load_roundtrip(tmp_path):
    cp = RebuildCheckpoint(tmp_path)
    state = EngineState(status="moving", geofence="home")
    cp.save(hwm="2026-06-11T12:00:00+00:00", engine_state=state)
    loaded = cp.load()
    assert loaded is not None
    assert loaded.hwm == "2026-06-11T12:00:00+00:00"
    assert loaded.engine_state.status == "moving"
    assert loaded.engine_state.geofence == "home"

def test_load_returns_none_when_absent(tmp_path):
    assert RebuildCheckpoint(tmp_path).load() is None

def test_load_returns_none_on_corruption(tmp_path):
    cp = RebuildCheckpoint(tmp_path)
    cp.path.parent.mkdir(parents=True, exist_ok=True)
    cp.path.write_text("{not json")
    assert cp.load() is None

def test_load_returns_none_on_version_mismatch(tmp_path):
    cp = RebuildCheckpoint(tmp_path)
    cp.save(hwm="x", engine_state=EngineState())
    import json
    d = json.loads(cp.path.read_text()); d["version"] = 999; cp.path.write_text(json.dumps(d))
    assert cp.load() is None
```

- [ ] **Step 2: Run, confirm fail** (module missing).
- [ ] **Step 3: Implement** `backend/engine/checkpoint.py`:

```python
"""Persisted boot-rebuild checkpoint: the last-processed event timestamp plus
the serialized engine state, so a restart replays only newer events."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from backend.engine.machine import EngineState

_VERSION = 1


@dataclass
class Checkpoint:
    hwm: str  # received_at (ISO-8601) of the last event reflected in engine_state + store
    engine_state: EngineState


class RebuildCheckpoint:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "derived" / "rebuild_checkpoint.json"

    def load(self) -> Checkpoint | None:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if d.get("version") != _VERSION:
                return None
            return Checkpoint(hwm=d["hwm"], engine_state=EngineState.from_dict(d["engine_state"]))
        except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
            return None

    def save(self, *, hwm: str, engine_state: EngineState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": _VERSION, "hwm": hwm, "engine_state": engine_state.to_dict()}
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)  # atomic
```

- [ ] **Step 4: Run, confirm pass.**
- [ ] **Step 5: Commit** — `feat(engine): add RebuildCheckpoint store (hwm + engine state)`

## Task 3: Incremental path in rebuild()

**Files:**
- Modify: `backend/engine/rebuild.py`
- Test: `backend/tests/test_incremental_rebuild.py`

Add `incremental: bool = False` to `rebuild()`. When `incremental` and a valid checkpoint loads: do NOT truncate; restore the engine from `checkpoint.engine_state`; replay only owntracks events with `received_at > checkpoint.hwm` and label events with `received_at > checkpoint.hwm`; then save a fresh checkpoint with the new max `received_at` and final engine state. When no/invalid checkpoint (or `incremental=False`): the existing full path (truncate + replay all), then save the checkpoint. The query already orders by `received_at`; capture the last `received_at` seen as the new hwm. EventQuery must expose the per-row `received_at` — extend the SQL to `SELECT received_at, CAST(payload AS VARCHAR) ...`.

- [ ] **Step 1: Write failing test** (the equivalence gate lives in Task 5; here test the mechanics): a checkpoint produced after replaying `stream[:k]` lets `rebuild(incremental=True)` process only `stream[k:]`. Assert (a) the derived `trips` table equals a full replay of the whole stream, and (b) it did NOT truncate (seed an extra sentinel row pre-call and confirm it survives only when expected). Use synth `commute()` data and a real `DerivedStore` on `tmp_path`.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement** the incremental branch in `rebuild()`. Sketch:

```python
def rebuild(settings, params=None, *, incremental=False):
    store = DerivedStore(settings)
    checkpoint = RebuildCheckpoint(settings.data_dir).load() if incremental else None
    if checkpoint is None:
        store.truncate()
        engine = TripEngine(params or EngineParams(), geofences_from_settings(settings))
        hwm = None
    else:
        engine = TripEngine(params or EngineParams(), geofences_from_settings(settings))
        engine.state = checkpoint.engine_state
        hwm = checkpoint.hwm
    # (re-parse GTFS as today)
    # query owntracks rows as (received_at, payload) ORDER BY received_at,
    # filtered to received_at > hwm when hwm is not None; process; track max received_at.
    # same for labels (received_at > hwm).
    # at the end: RebuildCheckpoint(settings.data_dir).save(hwm=max_seen or hwm, engine_state=engine.state)
    return engine, store, dict(counts)
```
Preserve the existing GTFS parse, label-replay-last ordering, and counts. Guard `max_seen` for the empty-tail case (no new events → keep old hwm, re-save same checkpoint).

- [ ] **Step 4: Run, confirm pass** + run `test_replay_equivalence.py` (must stay green).
- [ ] **Step 5: Commit** — `feat(engine): incremental rebuild path replaying only events past the checkpoint`

## Task 4: Wire EngineRunner to incremental + persist checkpoint live

**Files:**
- Modify: `backend/engine/runner.py`
- Test: `backend/tests/test_runner_live.py`

- [ ] **Step 1: Write failing test** — `EngineRunner.start(settings)` after a prior run leaves a checkpoint, and a second `start` does not re-replay already-processed events (assert via a spy/counter on processed rows, or that derived trips are unchanged and fast). Also: `process_payload` persists the checkpoint after a `TripClosed` (assert the checkpoint file's hwm advanced).
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement:**
  - `EngineRunner.start` calls `rebuild(settings, incremental=True)`.
  - Hold a `RebuildCheckpoint` + the last-seen `received_at`. In `process_payload`, after a `TripClosed` is written (engine back to idle — a clean checkpoint boundary), save `{hwm=<this point's received_at>, engine_state}`. NOTE: `process_payload` currently takes only `payload`; thread the point's `received_at` through (the ingest handler has it) or derive it from the payload `tst`. Prefer checkpointing at trip-close (idle state) to avoid mid-trip boundary ties.
  - `close()` saves a final checkpoint.
- [ ] **Step 4: Run, confirm pass.**
- [ ] **Step 5: Commit** — `feat(engine): EngineRunner uses incremental rebuild and checkpoints on trip close`

## Task 5: Store-level equivalence gate (the critical test)

**Files:**
- Test: `backend/tests/test_incremental_rebuild.py`

- [ ] **Step 1: Write the equivalence test** — build a multi-day synth stream spanning several trips. (A) Full rebuild over the whole archive → snapshot every derived table (`trips`, `segments`, `trip_points`, `train_matches`, `leg_observations`, `label_overrides`) as sorted rows. (B) Fresh data dir: replay the first half, force a checkpoint, then `rebuild(incremental=True)` over the full archive → snapshot the same tables. Assert **(A) == (B)** row-for-row. Parametrize the split at several boundaries, including mid-trip and at a trip edge.

```python
def _snapshot(con):
    return {t: con.execute(f"SELECT * FROM {t} ORDER BY 1,2").fetchall()
            for t in ("trips","segments","trip_points","train_matches","leg_observations")}

def test_incremental_rebuild_equals_full_rebuild(tmp_path, settings_factory):
    # ... build archive, full rebuild -> snap_full; checkpoint@split + incremental -> snap_inc
    assert snap_inc == snap_full
```

- [ ] **Step 2: Run** — if any table differs, fix the responsible write (likely a non-idempotent path or an hwm off-by-one at the boundary) until green.
- [ ] **Step 3: Add a crash-reprocessing test** — save a checkpoint with an hwm *older* than the true last-processed event (simulating a crash between a derived write and the checkpoint update); `rebuild(incremental=True)` must still equal the full rebuild (idempotent writes absorb the overlap).
- [ ] **Step 4: Commit** — `test(engine): incremental rebuild is byte-identical to full rebuild`

---

## Self-Review notes
- **Spec coverage:** Tasks cover the checkpoint store, the incremental replay path, idempotent writes (so boundary/crash overlap is safe), EngineRunner wiring + live checkpointing, and the equivalence gate. Fallback-to-full is in Task 3 (no/invalid checkpoint).
- **Type consistency:** `Checkpoint.engine_state` is an `EngineState`; `RebuildCheckpoint.save(hwm=..., engine_state=...)`; `rebuild(..., incremental=bool)`. `process_payload` signature gains the point's `received_at` (Task 4) — update the one caller in `backend/ingest/routes.py` accordingly and keep a default so existing tests pass, or thread it explicitly.
- **Risk:** the boundary hwm + idempotent writes are the correctness crux; Task 5's equivalence + crash tests are the gate. Do not mark the feature done until Task 5 is green across all split parametrizations.
