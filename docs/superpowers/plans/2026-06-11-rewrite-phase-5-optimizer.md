# Rewrite Phase 5: Rail-Aware Optimizer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the rail-aware optimizer — empirical leg-distribution models (shrunk toward schedule for small N), an itinerary composer that ranks scheduled-train options by door-to-door arrival (P50/P90 via seeded Monte Carlo), a daily recommendation service + scheduler, the optimizer/today API, and the Optimizer + Today SvelteKit views.

**Architecture:** Leg models are pure functions over historical trips + train-matches stored in a derived `leg_observations` table (rebuilt by replay like everything else). The composer convolves leg distributions by seeded Monte Carlo (deterministic given inputs + seed), so reasoning is honest about uncertainty without assuming normality. The schedule carries most of the signal — personal data only estimates deviations, and with few observations the estimate shrinks hard toward the schedule. Everything is pull-based this phase; live SSE position-tracking and Web Push are explicitly deferred to Phase 6.

**Tech Stack:** Python 3.11 (stdlib `statistics`/`random` — no numpy/scipy dependency), DuckDB, existing engine/derived-store/GTFS/matcher, SvelteKit 2 + Svelte 5 + ECharts (uncertainty-band charts), Vitest.

**Spec:** `docs/superpowers/specs/2026-06-10-ground-up-rewrite-design.md` — the "Optimizer" section (leg models, itinerary composer, daily recommendation; live mode is Phase 6). Deliberate Phase-5 trims: alert-aware recommendation downgrades to "schedule + observed delay history" (live GTFS-RT alert integration into the recommendation lands when RT data accumulates — the RT streams are already archiving from Phase 3); no Web Push (Today card is pull-only); no live vs-plan tracking.

---

## Background: the commute being optimized

The user's commute is multi-leg rail: **walk/drive → board origin rail station → NEC train → NY Penn (or forced Newark→PATH→33rd St) → walk → office**. Phase 2 detects trips and segments; Phase 3 matches the rail (vehicle) segment to a specific scheduled GTFS trip and records `delta_s` (observed boarding − scheduled departure). Phase 4 lets the user confirm/correct those matches. This phase turns that history into *"to be at the office by 9:00 Wednesday, leave by 7:38 for NEC #3838 — P50 arrive 8:52, P90 9:04."*

**Leg taxonomy** (the composer's atoms), derived from a labeled/matched trip's segments + match:

| Leg kind | Source segment | Duration model |
|----------|----------------|----------------|
| `access` | walk/drive segment(s) before the first rail segment | empirical minutes, shrunk to a distance-based default |
| `wait` | gap between access-end and scheduled departure | computed at compose time (not observed) |
| `ride:<route>` | rail (vehicle) segment matched to a route | scheduled ride seconds + observed `delta`-adjusted arrival spread for that route |
| `egress` | walk/drive segment(s) after the last rail segment | empirical minutes, shrunk to a distance-based default |

A trip with a Newark transfer has two `ride` legs and an interior `access`/`wait` pair — the composer handles N rail legs generically.

---

## File structure

```
backend/
  optimizer/
    __init__.py
    params.py        # OptimizerParams — shrinkage, MC iters, seed, defaults
    legs.py          # decompose a trip into LegObservation rows
    distributions.py # EmpiricalDistribution (samples + shrinkage prior) + quantile/sample
    legstats.py      # aggregate leg_observations → per-leg LegModel
    itinerary.py     # enumerate candidate scheduled itineraries from GTFS for a goal
    compose.py       # Monte-Carlo door-to-door arrival distribution per itinerary
    recommend.py     # rank itineraries; pick the daily recommendation
  storage/derived.py # leg_observations + recommendations DDL, write/read methods
  engine/rebuild.py  # populate leg_observations during replay
  engine/runner.py   # populate leg_observations on live trip close
  api/optimizer.py   # GET /api/optimizer (what-if), GET /api/recommendation
  jobs/daily.py      # (reuse) — recommendation job registered in app lifespan
  app.py             # optimizer router; daily recommendation job
  tests/
    test_legs.py
    test_distributions.py
    test_legstats.py
    test_itinerary.py
    test_compose.py
    test_recommend.py
    test_optimizer_api.py
    test_optimizer_rebuild.py
frontend/
  src/lib/api.ts            # modify: optimizer + recommendation types/calls
  src/lib/echarts.ts        # tiny ECharts loader/helper
  src/lib/FanChart.svelte   # departure→arrival uncertainty band chart
  src/lib/ItineraryCard.svelte
  src/routes/optimizer/+page.svelte   # goal input → ranked options
  src/routes/optimizer/+page.ts
  src/routes/today/+page.svelte       # recommendation card
  src/routes/today/+page.ts
  src/routes/+layout.svelte           # modify: Today + Optimizer become real links
  src/lib/compose.test.ts             # vitest for any pure frontend helper
  package.json                        # modify: add echarts
README.md                # modify
```

**Design decisions locked in:**

- **No numpy/scipy.** Distributions are plain sample lists; quantiles via `statistics.quantiles`-style interpolation written explicitly; Monte Carlo via stdlib `random.Random(seed)`. Keeps the image slim and the math auditable.
- **Determinism:** every Monte Carlo call takes an explicit integer seed derived from stable inputs (`OptimizerParams.mc_seed`), so the same goal + same derived data always yields identical P50/P90. This makes the optimizer testable and the daily recommendation reproducible.
- **Shrinkage:** a leg's effective sample set = observed samples PLUS `prior_weight` synthetic samples at the prior mean (a distance/schedule-derived default). With 0 observations the model IS the prior; with many, observations dominate. Simple, honest, no hyperparameter tuning.
- **Units:** seconds internally; the API converts to ISO timestamps + minutes for display. Local time is America/New_York (matches the GTFS/matcher convention from Phase 3).
- **`leg_observations` is derived** — truncated on rebuild and repopulated from trips+matches+labels during replay, same disposability rule as everything else. Recommendations are also derived/regenerable.
- **What-if vs daily rec share one composer.** The daily recommendation is just the composer run for "today/tomorrow, arrive by the user's configured target" and persisted; the what-if API runs the same composer for arbitrary goals on demand.
- **Direction scope:** Phase 5 optimizes the **outbound** (home→work) commute, where "arrive by X" is the natural goal. Inbound is symmetric and falls out of the same code, but the recommendation scheduler targets the morning outbound; the what-if API accepts either direction.

---

### Task 1: Optimizer params + leg decomposition

**Files:**
- Create: `backend/optimizer/__init__.py` (empty)
- Create: `backend/optimizer/params.py`
- Create: `backend/optimizer/legs.py`
- Test: `backend/tests/test_legs.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_legs.py
from backend.optimizer.legs import LegObservation, decompose_trip

# A trip detail dict as returned by DerivedStore.get_trip (the shape the engine
# already produces): segments with mode_effective, itinerary legs carrying the
# matched train, trip metadata.
TRIP = {
    "trip": {"trip_id": "t100", "direction": "outbound",
             "start_ts": "2026-06-10T11:30:00+00:00",  # 07:30 EDT
             "end_ts": "2026-06-10T12:24:00+00:00"},
    "segments": [
        {"seg_index": 0, "mode_effective": "walk", "start_ts": "2026-06-10T11:30:00+00:00",
         "end_ts": "2026-06-10T11:36:00+00:00", "duration_s": 360.0, "distance_m": 480.0},
        {"seg_index": 1, "mode_effective": "train", "start_ts": "2026-06-10T11:38:00+00:00",
         "end_ts": "2026-06-10T12:15:00+00:00", "duration_s": 2220.0, "distance_m": 30000.0},
        {"seg_index": 2, "mode_effective": "walk", "start_ts": "2026-06-10T12:16:00+00:00",
         "end_ts": "2026-06-10T12:24:00+00:00", "duration_s": 480.0, "distance_m": 640.0},
    ],
    "itinerary": [
        {"mode": "walk", "train": None},
        {"mode": "train", "train": {"seg_index": 1, "source": "gtfs_njt",
            "gtfs_trip_id": "NEC3838", "route_name": "Northeast Corridor",
            "board_stop": "Metropark", "alight_stop": "New York Penn Station",
            "scheduled_dep_s": 27600, "delta_s": 120.0}},
        {"mode": "walk", "train": None},
    ],
}


def test_decompose_outbound_trip_into_legs():
    legs = decompose_trip(TRIP)
    kinds = [leg.kind for leg in legs]
    assert kinds == ["access", "ride:gtfs_njt:Northeast Corridor", "egress"]
    access, ride, egress = legs
    assert access.duration_s == 360.0
    assert access.distance_m == 480.0
    assert ride.duration_s == 2220.0
    assert ride.gtfs_trip_id == "NEC3838"
    assert ride.delta_s == 120.0
    assert egress.duration_s == 480.0


def test_decompose_merges_consecutive_access_segments():
    trip = {
        "trip": TRIP["trip"],
        "segments": [
            {"seg_index": 0, "mode_effective": "walk", "duration_s": 120.0, "distance_m": 150.0,
             "start_ts": "2026-06-10T11:30:00+00:00", "end_ts": "2026-06-10T11:32:00+00:00"},
            {"seg_index": 1, "mode_effective": "vehicle", "duration_s": 600.0, "distance_m": 4000.0,
             "start_ts": "2026-06-10T11:32:00+00:00", "end_ts": "2026-06-10T11:42:00+00:00"},
            {"seg_index": 2, "mode_effective": "train", "duration_s": 2220.0, "distance_m": 30000.0,
             "start_ts": "2026-06-10T11:44:00+00:00", "end_ts": "2026-06-10T12:21:00+00:00"},
            {"seg_index": 3, "mode_effective": "walk", "duration_s": 300.0, "distance_m": 400.0,
             "start_ts": "2026-06-10T12:22:00+00:00", "end_ts": "2026-06-10T12:27:00+00:00"},
        ],
        "itinerary": [
            {"mode": "walk", "train": None},
            {"mode": "vehicle", "train": None},
            {"mode": "train", "train": {"seg_index": 2, "source": "gtfs_njt",
                "gtfs_trip_id": "NEC3838", "route_name": "Northeast Corridor",
                "board_stop": "Metropark", "alight_stop": "New York Penn Station",
                "scheduled_dep_s": 27600, "delta_s": 0.0}},
            {"mode": "walk", "train": None},
        ],
    }
    legs = decompose_trip(trip)
    assert [leg.kind for leg in legs] == [
        "access", "ride:gtfs_njt:Northeast Corridor", "egress"]
    assert legs[0].duration_s == 720.0  # 120 + 600 merged access
    assert legs[0].distance_m == 4150.0


def test_decompose_two_rail_legs_with_transfer():
    trip = {
        "trip": TRIP["trip"],
        "segments": [
            {"seg_index": 0, "mode_effective": "walk", "duration_s": 300.0, "distance_m": 400.0,
             "start_ts": "2026-06-10T11:30:00+00:00", "end_ts": "2026-06-10T11:35:00+00:00"},
            {"seg_index": 1, "mode_effective": "train", "duration_s": 600.0, "distance_m": 8000.0,
             "start_ts": "2026-06-10T11:37:00+00:00", "end_ts": "2026-06-10T11:47:00+00:00"},
            {"seg_index": 2, "mode_effective": "walk", "duration_s": 240.0, "distance_m": 200.0,
             "start_ts": "2026-06-10T11:47:00+00:00", "end_ts": "2026-06-10T11:51:00+00:00"},
            {"seg_index": 3, "mode_effective": "train", "duration_s": 1200.0, "distance_m": 12000.0,
             "start_ts": "2026-06-10T11:53:00+00:00", "end_ts": "2026-06-10T12:13:00+00:00"},
            {"seg_index": 4, "mode_effective": "walk", "duration_s": 300.0, "distance_m": 400.0,
             "start_ts": "2026-06-10T12:14:00+00:00", "end_ts": "2026-06-10T12:19:00+00:00"},
        ],
        "itinerary": [
            {"mode": "walk", "train": None},
            {"mode": "train", "train": {"seg_index": 1, "source": "gtfs_njt",
                "gtfs_trip_id": "NEC1", "route_name": "Northeast Corridor",
                "board_stop": "Metropark", "alight_stop": "Newark Penn",
                "scheduled_dep_s": 27600, "delta_s": 0.0}},
            {"mode": "walk", "train": None},
            {"mode": "train", "train": {"seg_index": 3, "source": "gtfs_path",
                "gtfs_trip_id": "PATH1", "route_name": "PATH",
                "board_stop": "Newark", "alight_stop": "33rd St",
                "scheduled_dep_s": 28800, "delta_s": 0.0}},
            {"mode": "walk", "train": None},
        ],
    }
    legs = decompose_trip(trip)
    assert [leg.kind for leg in legs] == [
        "access", "ride:gtfs_njt:Northeast Corridor",
        "transfer", "ride:gtfs_path:PATH", "egress"]


def test_decompose_unmatched_rail_returns_no_legs():
    # a vehicle segment that the matcher could not attribute → not optimizable
    trip = {
        "trip": TRIP["trip"],
        "segments": [{"seg_index": 0, "mode_effective": "vehicle", "duration_s": 1800.0,
                      "distance_m": 20000.0, "start_ts": "2026-06-10T11:30:00+00:00",
                      "end_ts": "2026-06-10T12:00:00+00:00"}],
        "itinerary": [{"mode": "vehicle", "train": None}],
    }
    assert decompose_trip(trip) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_legs.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.optimizer.legs`.

- [ ] **Step 3: Implement params + legs**

```python
# backend/optimizer/params.py
"""Optimizer tuning. Units: seconds, meters. Pure config, no I/O."""

from dataclasses import dataclass


@dataclass(frozen=True)
class OptimizerParams:
    prior_weight: float = 4.0          # synthetic prior samples blended into each leg
    walk_speed_mps: float = 1.3        # access/egress prior: distance / speed
    access_spread_frac: float = 0.25   # prior stddev as a fraction of prior mean
    ride_delay_spread_s: float = 180.0 # prior spread on rail arrival when few rides seen
    mc_iters: int = 2000               # Monte Carlo samples per itinerary
    mc_seed: int = 20260611            # fixed seed → reproducible recommendations
    min_transfer_s: float = 120.0      # minimum feasible transfer connection time
```

```python
# backend/optimizer/legs.py
"""Decompose a matched/labeled trip into optimizer leg observations.

A leg is an atom the composer reasons about: access (door→first rail board),
ride (a matched scheduled train), transfer (walk between two rail legs),
egress (last rail alight→door). Only trips whose rail segments are all matched
to scheduled trains are decomposable — an unmatched vehicle segment yields [].
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LegObservation:
    trip_id: str
    direction: str
    leg_index: int
    kind: str                  # "access" | "transfer" | "ride:<source>:<route>" | "egress"
    duration_s: float
    distance_m: float
    gtfs_trip_id: str | None   # ride legs only
    source: str | None
    route_name: str | None
    scheduled_dep_s: int | None
    delta_s: float | None
    board_stop: str | None
    alight_stop: str | None


def decompose_trip(detail: dict) -> list[LegObservation]:
    segments = detail["segments"]
    legs_meta = detail["itinerary"]
    trip = detail["trip"]
    # A rail leg is a segment the matcher attributed to a scheduled train. The
    # heuristic never emits "train" — matched rail segments are mode "vehicle"
    # unless the user labeled them "train" — so rail-ness is "has a train
    # match", NOT mode. Non-rail segments (walk, drive-to-station, even an
    # unmatched vehicle) are lumped into the adjacent ground leg. A trip with
    # zero matched rail legs is not optimizable → [].
    # (itinerary and segments are parallel: get_trip builds the itinerary as a
    # per-segment list, so index i refers to the same atom in both.)
    rail_positions = [i for i, leg in enumerate(legs_meta) if leg.get("train")]
    if not rail_positions:
        return []

    out: list[LegObservation] = []
    leg_index = 0

    def _emit(kind, dur, dist, train=None):
        nonlocal leg_index
        out.append(LegObservation(
            trip_id=trip["trip_id"], direction=trip["direction"], leg_index=leg_index,
            kind=kind, duration_s=dur, distance_m=dist,
            gtfs_trip_id=(train or {}).get("gtfs_trip_id"),
            source=(train or {}).get("source"),
            route_name=(train or {}).get("route_name"),
            scheduled_dep_s=(train or {}).get("scheduled_dep_s"),
            delta_s=(train or {}).get("delta_s"),
            board_stop=(train or {}).get("board_stop"),
            alight_stop=(train or {}).get("alight_stop"),
        ))
        leg_index += 1

    cursor = 0
    for rail_i, pos in enumerate(rail_positions):
        # ground segments before this rail leg, after the previous rail leg
        ground = segments[cursor:pos]
        dur = sum(s["duration_s"] for s in ground)
        dist = sum(s["distance_m"] for s in ground)
        kind = "access" if rail_i == 0 else "transfer"
        _emit(kind, dur, dist)
        rail = segments[pos]
        _emit(
            f"ride:{legs_meta[pos]['train']['source']}:"
            f"{legs_meta[pos]['train']['route_name']}",
            rail["duration_s"], rail["distance_m"], train=legs_meta[pos]["train"],
        )
        cursor = pos + 1
    # egress: ground segments after the last rail leg
    ground = segments[cursor:]
    if ground:
        _emit("egress", sum(s["duration_s"] for s in ground),
              sum(s["distance_m"] for s in ground))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_legs.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/optimizer/ backend/tests/test_legs.py
git commit -m "feat: optimizer params and trip leg decomposition"
```

---

### Task 2: Empirical distribution with shrinkage

**Files:**
- Create: `backend/optimizer/distributions.py`
- Test: `backend/tests/test_distributions.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_distributions.py
import random

from backend.optimizer.distributions import EmpiricalDistribution


def test_quantile_of_known_samples():
    d = EmpiricalDistribution(samples=[100, 200, 300, 400, 500], prior_mean=0, prior_weight=0)
    assert d.quantile(0.5) == 300
    assert d.quantile(0.0) == 100
    assert d.quantile(1.0) == 500
    # linear interpolation between order statistics
    assert 200 < d.quantile(0.4) < 300


def test_empty_samples_fall_back_to_prior():
    d = EmpiricalDistribution(samples=[], prior_mean=420.0, prior_weight=4, prior_spread=60.0)
    # with no observations the distribution is the prior: median ≈ prior mean
    assert abs(d.quantile(0.5) - 420.0) < 1.0
    assert d.quantile(0.9) > d.quantile(0.5) > d.quantile(0.1)


def test_shrinkage_blends_observations_toward_prior():
    # one extreme observation, heavy prior → median pulled toward prior
    light = EmpiricalDistribution(samples=[1000.0], prior_mean=300.0, prior_weight=0,
                                  prior_spread=30.0)
    heavy = EmpiricalDistribution(samples=[1000.0], prior_mean=300.0, prior_weight=8,
                                  prior_spread=30.0)
    assert heavy.quantile(0.5) < light.quantile(0.5)
    assert heavy.quantile(0.5) < 1000.0


def test_sample_is_deterministic_under_seed():
    d = EmpiricalDistribution(samples=[100, 200, 300], prior_mean=200, prior_weight=2,
                              prior_spread=20.0)
    rng_a = random.Random(7)
    rng_b = random.Random(7)
    draws_a = [d.sample(rng_a) for _ in range(50)]
    draws_b = [d.sample(rng_b) for _ in range(50)]
    assert draws_a == draws_b
    assert all(x > 0 for x in draws_a)


def test_mean_and_count():
    d = EmpiricalDistribution(samples=[100, 200, 300], prior_mean=0, prior_weight=0)
    assert d.observed_count == 3
    assert abs(d.observed_mean - 200.0) < 1e-9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_distributions.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.optimizer.distributions`.

- [ ] **Step 3: Implement**

```python
# backend/optimizer/distributions.py
"""A leg's duration distribution: observed samples blended with a prior.

Shrinkage model: the effective sample set is the observations PLUS
`prior_weight` synthetic draws from a Gaussian prior (prior_mean, prior_spread).
With zero observations the distribution IS the prior; with many, observations
dominate. Quantiles use linear interpolation over the combined sorted samples.
No numpy — stdlib only, fully auditable.
"""

import random
from dataclasses import dataclass, field


@dataclass
class EmpiricalDistribution:
    samples: list[float]
    prior_mean: float
    prior_weight: float
    prior_spread: float = 0.0
    _prior_seed: int = 1
    _combined: list[float] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self):
        prior = []
        if self.prior_weight > 0:
            rng = random.Random(self._prior_seed)
            n = int(round(self.prior_weight))
            spread = self.prior_spread if self.prior_spread > 0 else max(
                1.0, abs(self.prior_mean) * 0.1)
            prior = [max(0.0, rng.gauss(self.prior_mean, spread)) for _ in range(n)]
        self._combined = sorted([float(s) for s in self.samples] + prior)

    @property
    def observed_count(self) -> int:
        return len(self.samples)

    @property
    def observed_mean(self) -> float:
        return sum(self.samples) / len(self.samples) if self.samples else self.prior_mean

    def quantile(self, q: float) -> float:
        xs = self._combined
        if not xs:
            return self.prior_mean
        if len(xs) == 1:
            return xs[0]
        pos = q * (len(xs) - 1)
        lo = int(pos)
        if lo >= len(xs) - 1:
            return xs[-1]
        frac = pos - lo
        return xs[lo] + frac * (xs[lo + 1] - xs[lo])

    def sample(self, rng: random.Random) -> float:
        """Draw one value via inverse-CDF on a uniform — deterministic per rng."""
        return max(0.0, self.quantile(rng.random()))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_distributions.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/optimizer/distributions.py backend/tests/test_distributions.py
git commit -m "feat: empirical leg distribution with prior shrinkage"
```

---

### Task 3: leg_observations derived table + store methods

**Files:**
- Modify: `backend/storage/derived.py`
- Test: `backend/tests/test_legstats.py` (store half), `backend/tests/test_optimizer_rebuild.py` (placeholder import check skipped — see Task 7)

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_legstats.py
from backend.optimizer.legs import LegObservation
from backend.storage.derived import DerivedStore


def _obs(trip_id, kind, dur, **kw):
    return LegObservation(
        trip_id=trip_id, direction="outbound", leg_index=kw.get("leg_index", 0),
        kind=kind, duration_s=dur, distance_m=kw.get("distance_m", 500.0),
        gtfs_trip_id=kw.get("gtfs_trip_id"), source=kw.get("source"),
        route_name=kw.get("route_name"), scheduled_dep_s=kw.get("scheduled_dep_s"),
        delta_s=kw.get("delta_s"), board_stop=kw.get("board_stop"),
        alight_stop=kw.get("alight_stop"))


def test_write_and_read_leg_observations(settings):
    store = DerivedStore(settings)
    store.write_leg_observations("t1", [
        _obs("t1", "access", 360.0),
        _obs("t1", "ride:gtfs_njt:NEC", 2220.0, route_name="NEC", delta_s=120.0),
    ])
    rows = store.leg_observations()
    assert len(rows) == 2
    kinds = {r["kind"] for r in rows}
    assert kinds == {"access", "ride:gtfs_njt:NEC"}


def test_rewriting_trip_legs_is_idempotent(settings):
    store = DerivedStore(settings)
    store.write_leg_observations("t1", [_obs("t1", "access", 360.0)])
    store.write_leg_observations("t1", [_obs("t1", "access", 999.0)])  # corrected
    rows = [r for r in store.leg_observations() if r["trip_id"] == "t1"]
    assert len(rows) == 1
    assert rows[0]["duration_s"] == 999.0


def test_truncate_clears_leg_observations(settings):
    store = DerivedStore(settings)
    store.write_leg_observations("t1", [_obs("t1", "access", 360.0)])
    store.truncate()
    assert store.leg_observations() == []


def test_write_and_read_recommendation(settings):
    store = DerivedStore(settings)
    store.write_recommendation("2026-06-11", "outbound", {
        "goal": "arrive_by", "target_ts": "2026-06-11T13:00:00+00:00",
        "options": [{"gtfs_trip_id": "NEC3838", "leave_by_ts": "2026-06-11T11:38:00+00:00"}],
    })
    rec = store.recommendation("2026-06-11", "outbound")
    assert rec["options"][0]["gtfs_trip_id"] == "NEC3838"
    assert store.recommendation("2026-06-12", "outbound") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_legstats.py -v`
Expected: FAIL — no attribute write_leg_observations.

- [ ] **Step 3: Implement in `backend/storage/derived.py`**

Add to `_DDL`:

```sql
CREATE TABLE IF NOT EXISTS leg_observations (
    trip_id VARCHAR, direction VARCHAR, leg_index INTEGER, kind VARCHAR,
    duration_s DOUBLE, distance_m DOUBLE, gtfs_trip_id VARCHAR, source VARCHAR,
    route_name VARCHAR, scheduled_dep_s INTEGER, delta_s DOUBLE,
    board_stop VARCHAR, alight_stop VARCHAR
);
CREATE TABLE IF NOT EXISTS recommendations (
    service_date VARCHAR, direction VARCHAR, payload VARCHAR
);
```

Add `leg_observations` AND `recommendations` to `truncate()`'s tuple. Add
`leg_observations` to `write_trip_closed`'s per-trip DELETE loop (legs are
derived FROM points/matches, so a trip rewrite must drop stale legs — unlike
labels, which survive). Add `import json` if not already present. Methods:

```python
    def write_leg_observations(self, trip_id: str, legs: list) -> None:
        self._con.execute("DELETE FROM leg_observations WHERE trip_id = ?", [trip_id])
        if not legs:
            return
        self._con.executemany(
            "INSERT INTO leg_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [[lg.trip_id, lg.direction, lg.leg_index, lg.kind, lg.duration_s,
              lg.distance_m, lg.gtfs_trip_id, lg.source, lg.route_name,
              lg.scheduled_dep_s, lg.delta_s, lg.board_stop, lg.alight_stop]
             for lg in legs],
        )

    def leg_observations(self) -> list[dict]:
        cols = ["trip_id", "direction", "leg_index", "kind", "duration_s",
                "distance_m", "gtfs_trip_id", "source", "route_name",
                "scheduled_dep_s", "delta_s", "board_stop", "alight_stop"]
        rows = self._con.execute(
            f"SELECT {', '.join(cols)} FROM leg_observations"
        ).fetchall()
        return [dict(zip(cols, r)) for r in rows]

    def write_recommendation(self, service_date: str, direction: str, payload: dict) -> None:
        self._con.execute(
            "DELETE FROM recommendations WHERE service_date = ? AND direction = ?",
            [service_date, direction])
        self._con.execute(
            "INSERT INTO recommendations VALUES (?,?,?)",
            [service_date, direction, json.dumps(payload)])

    def recommendation(self, service_date: str, direction: str) -> dict | None:
        row = self._con.execute(
            "SELECT payload FROM recommendations WHERE service_date = ? AND direction = ?",
            [service_date, direction]).fetchone()
        return json.loads(row[0]) if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_legstats.py backend/tests/test_derived_store.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/storage/derived.py backend/tests/test_legstats.py
git commit -m "feat: leg_observations and recommendations derived tables"
```

---

### Task 4: Leg models from observations

**Files:**
- Create: `backend/optimizer/legstats.py`
- Test: `backend/tests/test_legstats.py` (add a model section)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_legstats.py`:

```python
from backend.optimizer.legstats import LegModels
from backend.optimizer.params import OptimizerParams

P = OptimizerParams()


def test_access_model_from_observations(settings):
    store = DerivedStore(settings)
    store.write_leg_observations("t1", [_obs("t1", "access", 300.0, distance_m=400.0)])
    store.write_leg_observations("t2", [_obs("t2", "access", 360.0, distance_m=400.0)])
    models = LegModels.build(store.leg_observations(), P)
    dist = models.access("outbound")
    assert 300.0 <= dist.quantile(0.5) <= 360.0
    assert dist.observed_count == 2


def test_access_model_no_observations_uses_distance_prior(settings):
    store = DerivedStore(settings)
    models = LegModels.build(store.leg_observations(), P)
    # no observations: caller supplies the expected distance, prior = dist/speed
    dist = models.access("outbound", distance_m=650.0)
    expected = 650.0 / P.walk_speed_mps
    assert abs(dist.quantile(0.5) - expected) < expected * 0.3
    assert dist.observed_count == 0


def test_ride_model_uses_delta_history(settings):
    store = DerivedStore(settings)
    for tid, delta in (("t1", 60.0), ("t2", 120.0), ("t3", 0.0)):
        store.write_leg_observations(tid, [
            _obs(tid, "ride:gtfs_njt:NEC", 2220.0, route_name="NEC",
                 source="gtfs_njt", gtfs_trip_id="NEC3838", delta_s=delta,
                 scheduled_dep_s=27600)])
    models = LegModels.build(store.leg_observations(), P)
    # the ride distribution is scheduled_ride + delay spread; with 3 deltas it
    # has real observations
    dist = models.ride("gtfs_njt", "NEC", scheduled_ride_s=2220.0)
    assert dist.observed_count == 3
    assert dist.quantile(0.5) >= 2220.0  # ride never beats schedule meaningfully here


def test_ride_model_unknown_route_falls_back_to_schedule(settings):
    store = DerivedStore(settings)
    models = LegModels.build(store.leg_observations(), P)
    dist = models.ride("gtfs_njt", "Unseen Line", scheduled_ride_s=1800.0)
    assert dist.observed_count == 0
    assert abs(dist.quantile(0.5) - 1800.0) < P.ride_delay_spread_s
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_legstats.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.optimizer.legstats`.

- [ ] **Step 3: Implement**

```python
# backend/optimizer/legstats.py
"""Aggregate leg_observations into per-leg duration distributions.

access/egress: empirical minutes, prior = distance / walk_speed.
ride:<source>:<route>: scheduled ride seconds + the observed delay spread
(arrival lateness) for that route; prior centered on the schedule when unseen.
"""

from dataclasses import dataclass

from backend.optimizer.distributions import EmpiricalDistribution
from backend.optimizer.params import OptimizerParams


@dataclass
class LegModels:
    _by_kind: dict
    _params: OptimizerParams

    @classmethod
    def build(cls, observations: list[dict], params: OptimizerParams) -> "LegModels":
        by_kind: dict[tuple, list[dict]] = {}
        for o in observations:
            key = (o["direction"], o["kind"])
            by_kind.setdefault(key, []).append(o)
        return cls(_by_kind=by_kind, _params=params)

    def access(self, direction: str, distance_m: float | None = None) -> EmpiricalDistribution:
        return self._ground(direction, "access", distance_m)

    def egress(self, direction: str, distance_m: float | None = None) -> EmpiricalDistribution:
        return self._ground(direction, "egress", distance_m)

    def transfer(self, direction: str, distance_m: float | None = None) -> EmpiricalDistribution:
        return self._ground(direction, "transfer", distance_m)

    def _ground(self, direction, kind, distance_m) -> EmpiricalDistribution:
        obs = self._by_kind.get((direction, kind), [])
        samples = [o["duration_s"] for o in obs]
        if samples:
            prior_mean = sum(samples) / len(samples)
        elif distance_m is not None:
            prior_mean = distance_m / self._params.walk_speed_mps
        else:
            prior_mean = 300.0  # neutral 5-minute default
        return EmpiricalDistribution(
            samples=samples, prior_mean=prior_mean,
            prior_weight=self._params.prior_weight,
            prior_spread=prior_mean * self._params.access_spread_frac)

    def ride(self, source: str, route_name: str,
             scheduled_ride_s: float) -> EmpiricalDistribution:
        # match any direction's ride for this source+route (rides are direction-symmetric)
        kind = f"ride:{source}:{route_name}"
        obs = [o for (d, k), lst in self._by_kind.items() if k == kind for o in lst]
        # observed arrival duration = scheduled ride + delay (delta_s ~ boarding
        # lateness; we treat it as a proxy for arrival spread around schedule)
        samples = [scheduled_ride_s + (o["delta_s"] or 0.0) for o in obs]
        return EmpiricalDistribution(
            samples=samples, prior_mean=scheduled_ride_s,
            prior_weight=self._params.prior_weight,
            prior_spread=self._params.ride_delay_spread_s)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_legstats.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/optimizer/legstats.py backend/tests/test_legstats.py
git commit -m "feat: per-leg duration models with distance and schedule priors"
```

---

### Task 5: Itinerary enumeration from GTFS

**Files:**
- Create: `backend/optimizer/itinerary.py`
- Test: `backend/tests/test_itinerary.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_itinerary.py
import base64

from backend.optimizer.itinerary import Itinerary, candidate_itineraries
from backend.storage.derived import DerivedStore
from backend.storage.raw import RawStore
from backend.tests.gtfs_fixture import build_gtfs_zip
from backend.transit.gtfs import latest_snapshot, parse_gtfs

# Two morning NEC trains, Metropark → NY Penn, on a weekday service.
STOPS = [("MP", "Metropark", 40.7000, -74.4000), ("NYP", "New York Penn", 40.7506, -73.9935)]
TRIPS = [
    ("NEC1", "WK", "NYP", [("MP", "07:38:00"), ("NYP", "08:15:00")]),
    ("NEC2", "WK", "NYP", [("MP", "08:02:00"), ("NYP", "08:39:00")]),
    ("NEC3", "WK", "NYP", [("MP", "06:30:00"), ("NYP", "07:07:00")]),  # too early to matter
]


def _load(settings):
    z = build_gtfs_zip(STOPS, TRIPS, route_type=2, route_name="Northeast Corridor")
    RawStore(settings.data_dir).append(
        "gtfs_njt", {"received_at": "2026-06-09T05:00:00+00:00",
                     "payload": {"url": "u", "status": 200,
                                 "b64": base64.b64encode(z).decode()}})
    store = DerivedStore(settings)
    parse_gtfs(store.con, "gtfs_njt", latest_snapshot(settings, "gtfs_njt"), fetched_at="x")
    return store


def test_enumerates_trains_that_can_make_the_goal(settings):
    store = _load(settings)
    # goal: at NY Penn by 08:30 local on 2026-06-10 (Wednesday)
    its = candidate_itineraries(
        store.con, source="gtfs_njt", board_stop="MP", alight_stop="NYP",
        service_date="20260610", arrive_by_local_s=8 * 3600 + 30 * 60,
        egress_pad_s=600.0)
    ids = [it.gtfs_trip_id for it in its]
    assert "NEC1" in ids   # arrives 08:15 + egress < 08:30 → feasible
    assert "NEC2" not in ids  # arrives 08:39 → too late
    assert "NEC3" in ids   # arrives 07:07 → feasible (early but valid)


def test_itineraries_sorted_latest_departure_first(settings):
    store = _load(settings)
    its = candidate_itineraries(
        store.con, source="gtfs_njt", board_stop="MP", alight_stop="NYP",
        service_date="20260610", arrive_by_local_s=8 * 3600 + 30 * 60, egress_pad_s=600.0)
    deps = [it.scheduled_dep_s for it in its]
    assert deps == sorted(deps, reverse=True)  # latest feasible departure first


def test_no_service_day_yields_nothing(settings):
    store = _load(settings)
    its = candidate_itineraries(
        store.con, source="gtfs_njt", board_stop="MP", alight_stop="NYP",
        service_date="20270101", arrive_by_local_s=8 * 3600 + 30 * 60, egress_pad_s=600.0)
    assert its == []


def test_itinerary_carries_schedule_fields(settings):
    store = _load(settings)
    it = candidate_itineraries(
        store.con, source="gtfs_njt", board_stop="MP", alight_stop="NYP",
        service_date="20260610", arrive_by_local_s=8 * 3600 + 30 * 60, egress_pad_s=600.0)[0]
    assert isinstance(it, Itinerary)
    assert it.scheduled_dep_s == 8 * 3600 + 2 * 60 or it.scheduled_dep_s == 7 * 3600 + 38 * 60
    assert it.scheduled_arr_s > it.scheduled_dep_s
    assert it.route_name == "Northeast Corridor"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_itinerary.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.optimizer.itinerary`.

- [ ] **Step 3: Implement**

```python
# backend/optimizer/itinerary.py
"""Enumerate scheduled single-ride itineraries that satisfy an arrival goal.

Phase 5 scope: single-ride origin→destination on one GTFS source. Multi-ride
(Newark transfer) composition reuses leg models per ride but the candidate
enumerator here handles the direct case; transfer enumeration is a Phase-6
extension noted in the optimizer view. The board/alight stops are configured
(home station, work terminal) — see OptimizerParams / settings.
"""

from dataclasses import dataclass

import duckdb

from backend.transit.gtfs import active_service_ids


@dataclass(frozen=True)
class Itinerary:
    source: str
    gtfs_trip_id: str
    route_name: str
    headsign: str
    board_stop: str
    alight_stop: str
    scheduled_dep_s: int   # seconds since local midnight (service date)
    scheduled_arr_s: int


def candidate_itineraries(
    con: duckdb.DuckDBPyConnection, *, source: str, board_stop: str, alight_stop: str,
    service_date: str, arrive_by_local_s: int, egress_pad_s: float,
) -> list[Itinerary]:
    """Trips serving board→alight in order on the active service day whose
    scheduled arrival + egress_pad is at or before the goal. Latest-departure
    first."""
    active = active_service_ids(con, source, service_date)
    if not active:
        return []
    rows = con.execute(
        "SELECT t.trip_id, t.service_id, t.headsign, r.route_name, "
        "       s1.stop_name, s2.stop_name, st1.departure_s, st2.arrival_s "
        "FROM gtfs_stop_times st1 "
        "JOIN gtfs_stop_times st2 ON st1.source = st2.source AND st1.trip_id = st2.trip_id "
        "JOIN gtfs_trips t ON t.source = st1.source AND t.trip_id = st1.trip_id "
        "JOIN gtfs_routes r ON r.source = t.source AND r.route_id = t.route_id "
        "JOIN gtfs_stops s1 ON s1.source = st1.source AND s1.stop_id = st1.stop_id "
        "JOIN gtfs_stops s2 ON s2.source = st2.source AND s2.stop_id = st2.stop_id "
        "WHERE st1.source = ? AND st1.stop_id = ? AND st2.stop_id = ? "
        "  AND st1.stop_sequence < st2.stop_sequence "
        "ORDER BY st1.departure_s DESC",
        [source, board_stop, alight_stop],
    ).fetchall()
    out = []
    for trip_id, service_id, headsign, route_name, b_name, a_name, dep_s, arr_s in rows:
        if service_id not in active:
            continue
        if arr_s + egress_pad_s > arrive_by_local_s:
            continue
        out.append(Itinerary(
            source=source, gtfs_trip_id=trip_id, route_name=route_name,
            headsign=headsign or "", board_stop=b_name, alight_stop=a_name,
            scheduled_dep_s=dep_s, scheduled_arr_s=arr_s))
    return out
```

NOTE on the test: `candidate_itineraries` is passed stop IDs (`MP`/`NYP`) for
the WHERE clause but the returned `Itinerary.board_stop` is the stop *name*
(`Metropark`). The test asserts on schedule seconds and route, not stop names —
keep the SQL joining stops for the human-readable names.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_itinerary.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/optimizer/itinerary.py backend/tests/test_itinerary.py
git commit -m "feat: gtfs itinerary enumeration for an arrival goal"
```

---

### Task 6: Monte-Carlo composition + ranking

**Files:**
- Create: `backend/optimizer/compose.py`
- Create: `backend/optimizer/recommend.py`
- Test: `backend/tests/test_compose.py`, `backend/tests/test_recommend.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_compose.py
from backend.optimizer.compose import compose_itinerary
from backend.optimizer.distributions import EmpiricalDistribution
from backend.optimizer.itinerary import Itinerary
from backend.optimizer.params import OptimizerParams

P = OptimizerParams()
IT = Itinerary(source="gtfs_njt", gtfs_trip_id="NEC1", route_name="NEC", headsign="NYP",
               board_stop="Metropark", alight_stop="NY Penn",
               scheduled_dep_s=27480, scheduled_arr_s=29700)  # 07:38 → 08:15


def _fixed(mean):
    return EmpiricalDistribution(samples=[mean], prior_mean=mean, prior_weight=0)


def test_compose_produces_arrival_quantiles():
    result = compose_itinerary(
        IT, access=_fixed(360.0), ride=_fixed(2220.0), egress=_fixed(480.0),
        service_date_midnight_local_s=0, params=P)
    # leave_by = dep - access; arrival ≈ dep + ride + egress
    assert result["p50_arr_s"] > IT.scheduled_dep_s
    assert result["p90_arr_s"] >= result["p50_arr_s"]
    assert result["leave_by_s"] <= IT.scheduled_dep_s
    # with fixed legs the spread collapses
    assert abs(result["p90_arr_s"] - result["p50_arr_s"]) < 60


def test_compose_widens_with_uncertain_legs():
    tight = compose_itinerary(
        IT, access=_fixed(360.0), ride=_fixed(2220.0), egress=_fixed(480.0),
        service_date_midnight_local_s=0, params=P)
    wide_ride = EmpiricalDistribution(samples=[2000.0, 2220.0, 2600.0, 3000.0],
                                      prior_mean=2220.0, prior_weight=4, prior_spread=300.0)
    wide = compose_itinerary(
        IT, access=_fixed(360.0), ride=wide_ride, egress=_fixed(480.0),
        service_date_midnight_local_s=0, params=P)
    assert (wide["p90_arr_s"] - wide["p50_arr_s"]) > (tight["p90_arr_s"] - tight["p50_arr_s"])


def test_compose_is_deterministic():
    a = compose_itinerary(IT, access=_fixed(360.0), ride=_fixed(2220.0),
                          egress=_fixed(480.0), service_date_midnight_local_s=0, params=P)
    b = compose_itinerary(IT, access=_fixed(360.0), ride=_fixed(2220.0),
                          egress=_fixed(480.0), service_date_midnight_local_s=0, params=P)
    assert a == b
```

```python
# backend/tests/test_recommend.py
import base64

from backend.optimizer.params import OptimizerParams
from backend.optimizer.recommend import recommend
from backend.storage.derived import DerivedStore
from backend.storage.raw import RawStore
from backend.tests.gtfs_fixture import build_gtfs_zip
from backend.transit.gtfs import latest_snapshot, parse_gtfs

P = OptimizerParams()
STOPS = [("MP", "Metropark", 40.70, -74.40), ("NYP", "New York Penn", 40.75, -73.99)]
TRIPS = [
    ("NEC1", "WK", "NYP", [("MP", "07:38:00"), ("NYP", "08:15:00")]),
    ("NEC2", "WK", "NYP", [("MP", "08:02:00"), ("NYP", "08:39:00")]),
]


def _load(settings):
    z = build_gtfs_zip(STOPS, TRIPS, route_type=2, route_name="Northeast Corridor")
    RawStore(settings.data_dir).append(
        "gtfs_njt", {"received_at": "2026-06-09T05:00:00+00:00",
                     "payload": {"url": "u", "status": 200,
                                 "b64": base64.b64encode(z).decode()}})
    store = DerivedStore(settings)
    parse_gtfs(store.con, "gtfs_njt", latest_snapshot(settings, "gtfs_njt"), fetched_at="x")
    return store


def test_recommend_ranks_feasible_trains(settings):
    store = _load(settings)
    rec = recommend(
        store, direction="outbound", source="gtfs_njt", board_stop="MP",
        alight_stop="NYP", service_date="20260610",
        arrive_by_local_s=8 * 3600 + 30 * 60,
        access_distance_m=500.0, egress_distance_m=650.0, params=P)
    assert rec["options"]                       # at least NEC1 feasible
    top = rec["options"][0]
    assert top["gtfs_trip_id"] == "NEC1"        # latest feasible train
    assert "leave_by_local_s" in top
    assert "p50_arr_local_s" in top and "p90_arr_local_s" in top
    assert top["p90_arr_local_s"] <= 8 * 3600 + 30 * 60 + P.ride_delay_spread_s


def test_recommend_empty_when_no_trains_make_it(settings):
    store = _load(settings)
    rec = recommend(
        store, direction="outbound", source="gtfs_njt", board_stop="MP",
        alight_stop="NYP", service_date="20260610",
        arrive_by_local_s=7 * 3600,  # 07:00 — no train arrives in time
        access_distance_m=500.0, egress_distance_m=650.0, params=P)
    assert rec["options"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_compose.py backend/tests/test_recommend.py -v`
Expected: FAIL — modules missing.

- [ ] **Step 3: Implement compose + recommend**

```python
# backend/optimizer/compose.py
"""Monte-Carlo composition of an itinerary's door-to-door arrival.

Draw access + (ride includes scheduled arrival) + egress; the train departs on
schedule (we don't model missing the train here — the recommendation leaves
margin via leave_by = dep - access_p90). Deterministic under params.mc_seed.
"""

import random

from backend.optimizer.distributions import EmpiricalDistribution
from backend.optimizer.itinerary import Itinerary
from backend.optimizer.params import OptimizerParams


def _quantile(sorted_xs: list[float], q: float) -> float:
    if not sorted_xs:
        return 0.0
    pos = q * (len(sorted_xs) - 1)
    lo = int(pos)
    if lo >= len(sorted_xs) - 1:
        return sorted_xs[-1]
    return sorted_xs[lo] + (pos - lo) * (sorted_xs[lo + 1] - sorted_xs[lo])


def compose_itinerary(
    itin: Itinerary, *, access: EmpiricalDistribution, ride: EmpiricalDistribution,
    egress: EmpiricalDistribution, service_date_midnight_local_s: int,
    params: OptimizerParams,
) -> dict:
    rng = random.Random(params.mc_seed)
    arrivals = []
    leave_bys = []
    for _ in range(params.mc_iters):
        a = access.sample(rng)
        r = ride.sample(rng)          # actual ride seconds (schedule + delay)
        e = egress.sample(rng)
        # board on schedule; arrival = dep + ride + egress (seconds-of-day local)
        arr = itin.scheduled_dep_s + r + e
        arrivals.append(arr)
        leave_bys.append(itin.scheduled_dep_s - a)
    arrivals.sort()
    leave_bys.sort()
    return {
        "gtfs_trip_id": itin.gtfs_trip_id,
        "route_name": itin.route_name,
        "headsign": itin.headsign,
        "board_stop": itin.board_stop,
        "alight_stop": itin.alight_stop,
        "scheduled_dep_s": itin.scheduled_dep_s,
        "scheduled_arr_s": itin.scheduled_arr_s,
        "p50_arr_s": round(_quantile(arrivals, 0.5)),
        "p90_arr_s": round(_quantile(arrivals, 0.9)),
        # leave by the 10th percentile of leave-by → conservative (leave earlier)
        "leave_by_s": round(_quantile(leave_bys, 0.1)),
    }
```

```python
# backend/optimizer/recommend.py
"""Rank candidate itineraries for an arrival goal and shape the API payload.

Reuses the leg models built from history + the GTFS itinerary enumerator. The
ranking is: feasible (p90 arrival within goal + ride spread) trains, latest
departure first (catch the latest train you safely can)."""

from backend.optimizer.compose import compose_itinerary
from backend.optimizer.itinerary import candidate_itineraries
from backend.optimizer.legstats import LegModels
from backend.optimizer.params import OptimizerParams
from backend.storage.derived import DerivedStore


def recommend(
    store: DerivedStore, *, direction: str, source: str, board_stop: str,
    alight_stop: str, service_date: str, arrive_by_local_s: int,
    access_distance_m: float, egress_distance_m: float, params: OptimizerParams,
) -> dict:
    models = LegModels.build(store.leg_observations(), params)
    cands = candidate_itineraries(
        store.con, source=source, board_stop=board_stop, alight_stop=alight_stop,
        service_date=service_date, arrive_by_local_s=arrive_by_local_s,
        egress_pad_s=models.egress(direction, egress_distance_m).quantile(0.5))
    access = models.access(direction, access_distance_m)
    egress = models.egress(direction, egress_distance_m)
    options = []
    for it in cands:
        ride = models.ride(it.source, it.route_name,
                           scheduled_ride_s=float(it.scheduled_arr_s - it.scheduled_dep_s))
        comp = compose_itinerary(
            it, access=access, ride=ride, egress=egress,
            service_date_midnight_local_s=0, params=params)
        options.append({
            "gtfs_trip_id": comp["gtfs_trip_id"], "route_name": comp["route_name"],
            "headsign": comp["headsign"], "board_stop": comp["board_stop"],
            "alight_stop": comp["alight_stop"],
            "scheduled_dep_local_s": comp["scheduled_dep_s"],
            "scheduled_arr_local_s": comp["scheduled_arr_s"],
            "leave_by_local_s": comp["leave_by_s"],
            "p50_arr_local_s": comp["p50_arr_s"], "p90_arr_local_s": comp["p90_arr_s"],
        })
    # already latest-departure-first from the enumerator; keep that order
    return {"goal": "arrive_by", "direction": direction, "service_date": service_date,
            "arrive_by_local_s": arrive_by_local_s, "options": options}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_compose.py backend/tests/test_recommend.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/optimizer/compose.py backend/optimizer/recommend.py \
        backend/tests/test_compose.py backend/tests/test_recommend.py
git commit -m "feat: monte-carlo itinerary composition and ranking"
```

---

### Task 7: Wire leg_observations into rebuild + live close

**Files:**
- Modify: `backend/engine/rebuild.py`
- Modify: `backend/engine/runner.py`
- Test: `backend/tests/test_optimizer_rebuild.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_optimizer_rebuild.py
import base64
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.engine.machine import TripEngine
from backend.engine.params import EngineParams
from backend.engine.rebuild import rebuild
from backend.engine.types import TripClosed
from backend.storage.raw import RawStore
from backend.tests.gtfs_fixture import build_gtfs_zip
from backend.tests.synth import commute
from backend.tests.test_rebuild import _ingest_synthetic_day

NY = ZoneInfo("America/New_York")


def _seed_schedule_aligned_to_commute(settings):
    pts, _, _ = commute()
    eng = TripEngine(EngineParams(), geofences=[])
    closed = []
    for pt in pts:
        closed.extend(e for e in eng.process(pt) if isinstance(e, TripClosed))
    seg = next(s for s in closed[0].segments if s.mode == "vehicle")
    start = next(p for p in closed[0].points if p.ts >= seg.start_ts)
    end = max((p for p in closed[0].points if p.ts <= seg.end_ts), key=lambda p: p.ts)

    def hms(epoch):
        dt = datetime.fromtimestamp(epoch, NY)
        return f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"

    z = build_gtfs_zip(
        [("S1", "Alpha", start.lat, start.lon), ("S2", "Beta", end.lat, end.lon)],
        [("T1", "WK", "Beta", [("S1", hms(start.ts)), ("S2", hms(end.ts))])])
    RawStore(settings.data_dir).append(
        "gtfs_njt", {"received_at": "2026-06-09T05:00:00+00:00",
                     "payload": {"url": "u", "status": 200,
                                 "b64": base64.b64encode(z).decode()}})


def test_rebuild_populates_leg_observations(settings):
    _seed_schedule_aligned_to_commute(settings)
    _ingest_synthetic_day(settings)
    engine, store, counts = rebuild(settings)
    assert counts["trips"] == 1
    assert counts["train_matches"] == 1
    legs = store.leg_observations()
    kinds = {leg["kind"] for leg in legs}
    assert "access" in kinds
    assert any(k.startswith("ride:") for k in kinds)
    assert counts["leg_observations"] >= 2


def test_rebuild_skips_legs_for_unmatched_trips(settings):
    # commute with no GTFS schedule → trip exists, no matches → no legs
    _ingest_synthetic_day(settings)
    engine, store, counts = rebuild(settings)
    assert counts["trips"] == 1
    assert store.leg_observations() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_optimizer_rebuild.py -v`
Expected: FAIL — KeyError 'leg_observations' / empty.

- [ ] **Step 3: Implement**

In `backend/engine/rebuild.py`, add imports `from backend.optimizer.legs import decompose_trip`. In the replay loop, AFTER `store.write_train_matches(...)` and its count, add:

```python
                legs = decompose_trip(store.get_trip(ev.trip.trip_id))
                store.write_leg_observations(ev.trip.trip_id, legs)
                counts["leg_observations"] += len(legs)
```

(`store.get_trip` returns the merged detail including the just-written matches —
decompose needs the itinerary with the train objects.)

In `backend/engine/runner.py` `process_payload`, after the train-match write
block, add (inside a try/except so optimizer failures never affect ingest):

```python
                try:
                    self.store.write_leg_observations(
                        ev.trip.trip_id, decompose_trip(self.store.get_trip(ev.trip.trip_id)))
                except Exception:
                    log.exception("leg decomposition failed — trip stored without legs")
```

(import `from backend.optimizer.legs import decompose_trip`)

- [ ] **Step 4: Run the whole backend suite**

Run: `pytest backend/tests -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/engine/rebuild.py backend/engine/runner.py \
        backend/tests/test_optimizer_rebuild.py
git commit -m "feat: populate leg observations on rebuild and live trip close"
```

---

### Task 8: Optimizer settings + API

**Files:**
- Modify: `backend/config.py`
- Create: `backend/api/optimizer.py`
- Modify: `backend/app.py`
- Modify: `backend/tests/test_config.py`
- Test: `backend/tests/test_optimizer_api.py`

Optimizer needs to know the commute's stations + target arrival. These are
config (like geofences).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_config.py`:

```python
def test_optimizer_env_vars(monkeypatch):
    monkeypatch.setenv("CT_COMMUTE_SOURCE", "gtfs_njt")
    monkeypatch.setenv("CT_BOARD_STOP_ID", "MP")
    monkeypatch.setenv("CT_ALIGHT_STOP_ID", "NYP")
    monkeypatch.setenv("CT_ARRIVE_BY_LOCAL", "09:00")
    monkeypatch.setenv("CT_ACCESS_DISTANCE_M", "500")
    monkeypatch.setenv("CT_EGRESS_DISTANCE_M", "650")
    s = load_settings()
    assert s.commute_source == "gtfs_njt"
    assert s.board_stop_id == "MP"
    assert s.alight_stop_id == "NYP"
    assert s.arrive_by_local == "09:00"
    assert s.access_distance_m == 500.0
    assert s.egress_distance_m == 650.0
```

Also extend `test_defaults` delenv loop + assert `s.commute_source is None`.

```python
# backend/tests/test_optimizer_api.py
import base64
import dataclasses

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.storage.raw import RawStore
from backend.tests.gtfs_fixture import build_gtfs_zip

STOPS = [("MP", "Metropark", 40.70, -74.40), ("NYP", "New York Penn", 40.75, -73.99)]
TRIPS = [("NEC1", "WK", "NYP", [("MP", "07:38:00"), ("NYP", "08:15:00")]),
         ("NEC2", "WK", "NYP", [("MP", "08:02:00"), ("NYP", "08:39:00")])]


def _opt_settings(settings):
    return dataclasses.replace(
        settings, commute_source="gtfs_njt", board_stop_id="MP", alight_stop_id="NYP",
        arrive_by_local="09:00", access_distance_m=500.0, egress_distance_m=650.0)


@pytest.fixture
def client(settings):
    s = _opt_settings(settings)
    z = build_gtfs_zip(STOPS, TRIPS, route_type=2, route_name="Northeast Corridor")
    RawStore(s.data_dir).append(
        "gtfs_njt", {"received_at": "2026-06-09T05:00:00+00:00",
                     "payload": {"url": "u", "status": 200,
                                 "b64": base64.b64encode(z).decode()}})
    app = create_app(s)
    with TestClient(app) as c:
        yield c


def test_whatif_returns_ranked_options(client):
    resp = client.get("/api/optimizer?date=2026-06-10&arrive_by=08:30")
    assert resp.status_code == 200
    body = resp.json()
    assert body["direction"] == "outbound"
    assert [o["gtfs_trip_id"] for o in body["options"]][0] == "NEC1"
    opt = body["options"][0]
    # API exposes ISO timestamps for display
    assert opt["leave_by"].endswith(("+00:00", "-04:00", "-05:00"))
    assert "p50_arrive" in opt and "p90_arrive" in opt


def test_whatif_defaults_to_configured_arrive_by(client):
    resp = client.get("/api/optimizer?date=2026-06-10")
    assert resp.status_code == 200
    assert resp.json()["arrive_by_local"] == "09:00"


def test_whatif_unconfigured_returns_409(settings):
    app = create_app(settings)  # no commute_source
    with TestClient(app) as c:
        resp = c.get("/api/optimizer?date=2026-06-10")
    assert resp.status_code == 409
    assert "not configured" in resp.json()["detail"].lower()


def test_recommendation_endpoint_reads_persisted(client):
    # the daily job hasn't run; the endpoint computes on-demand if absent and persists
    resp = client.get("/api/recommendation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["direction"] == "outbound"
    assert "options" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_optimizer_api.py backend/tests/test_config.py -v`
Expected: FAIL — settings fields + /api/optimizer missing.

- [ ] **Step 3: Implement settings**

Append to `Settings` (all defaulted, after frontend_build_dir):

```python
    commute_source: str | None = None      # e.g. "gtfs_njt" — unset = optimizer disabled
    board_stop_id: str | None = None
    alight_stop_id: str | None = None
    arrive_by_local: str = "09:00"         # HH:MM local target
    access_distance_m: float = 500.0
    egress_distance_m: float = 650.0
```

In `load_settings()`:

```python
        commute_source=os.environ.get("CT_COMMUTE_SOURCE") or None,
        board_stop_id=os.environ.get("CT_BOARD_STOP_ID") or None,
        alight_stop_id=os.environ.get("CT_ALIGHT_STOP_ID") or None,
        arrive_by_local=os.environ.get("CT_ARRIVE_BY_LOCAL", "09:00"),
        access_distance_m=float(os.environ.get("CT_ACCESS_DISTANCE_M", "500")),
        egress_distance_m=float(os.environ.get("CT_EGRESS_DISTANCE_M", "650")),
```

- [ ] **Step 4: Implement the API**

```python
# backend/api/optimizer.py
"""Optimizer API: what-if (GET /api/optimizer) + daily recommendation
(GET /api/recommendation). Both reuse recommend() over the live derived store.

Local time is America/New_York; the API converts local-seconds-of-day to ISO
timestamps on the given service date for display.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request

from backend.optimizer.params import OptimizerParams
from backend.optimizer.recommend import recommend

_NY = ZoneInfo("America/New_York")


def _hhmm_to_local_s(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 3600 + int(m) * 60


def _local_s_to_iso(service_date: str, local_s: int) -> str:
    y, mo, d = int(service_date[:4]), int(service_date[5:7]), int(service_date[8:10])
    midnight = datetime(y, mo, d, tzinfo=_NY)
    return (midnight + timedelta(seconds=local_s)).isoformat()


def _service_date_yyyymmdd(date_str: str) -> str:
    return date_str.replace("-", "")


def _shape(rec: dict, date_str: str) -> dict:
    out = dict(rec)
    out["arrive_by_local"] = (
        f"{rec['arrive_by_local_s'] // 3600:02d}:{(rec['arrive_by_local_s'] % 3600) // 60:02d}")
    out["options"] = [
        {**o,
         "leave_by": _local_s_to_iso(date_str, o["leave_by_local_s"]),
         "scheduled_dep": _local_s_to_iso(date_str, o["scheduled_dep_local_s"]),
         "scheduled_arr": _local_s_to_iso(date_str, o["scheduled_arr_local_s"]),
         "p50_arrive": _local_s_to_iso(date_str, o["p50_arr_local_s"]),
         "p90_arrive": _local_s_to_iso(date_str, o["p90_arr_local_s"])}
        for o in rec["options"]
    ]
    return out


def make_optimizer_router() -> APIRouter:
    router = APIRouter()

    def _require_config(settings):
        if not (settings.commute_source and settings.board_stop_id and settings.alight_stop_id):
            raise HTTPException(
                status_code=409,
                detail="optimizer not configured: set CT_COMMUTE_SOURCE, "
                       "CT_BOARD_STOP_ID, CT_ALIGHT_STOP_ID")

    @router.get("/api/optimizer")
    async def whatif(request: Request, date: str, arrive_by: str | None = None) -> dict:
        settings = request.app.state.settings
        _require_config(settings)
        arrive_local = _hhmm_to_local_s(arrive_by or settings.arrive_by_local)
        rec = recommend(
            request.app.state.runner.store, direction="outbound",
            source=settings.commute_source, board_stop=settings.board_stop_id,
            alight_stop=settings.alight_stop_id,
            service_date=_service_date_yyyymmdd(date), arrive_by_local_s=arrive_local,
            access_distance_m=settings.access_distance_m,
            egress_distance_m=settings.egress_distance_m, params=OptimizerParams())
        return _shape(rec, date)

    @router.get("/api/recommendation")
    async def recommendation(request: Request) -> dict:
        settings = request.app.state.settings
        _require_config(settings)
        today = datetime.now(_NY).strftime("%Y-%m-%d")
        store = request.app.state.runner.store
        cached = store.recommendation(today.replace("-", ""), "outbound")
        if cached is not None:
            return cached
        arrive_local = _hhmm_to_local_s(settings.arrive_by_local)
        rec = recommend(
            store, direction="outbound", source=settings.commute_source,
            board_stop=settings.board_stop_id, alight_stop=settings.alight_stop_id,
            service_date=today.replace("-", ""), arrive_by_local_s=arrive_local,
            access_distance_m=settings.access_distance_m,
            egress_distance_m=settings.egress_distance_m, params=OptimizerParams())
        shaped = _shape(rec, today)
        store.write_recommendation(today.replace("-", ""), "outbound", shaped)
        return shaped

    return router
```

In `backend/app.py`: `from backend.api.optimizer import make_optimizer_router`
and `app.include_router(make_optimizer_router())` (before the SPA catch-all).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest backend/tests/test_optimizer_api.py backend/tests/test_config.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/config.py backend/api/optimizer.py backend/app.py \
        backend/tests/test_optimizer_api.py backend/tests/test_config.py
git commit -m "feat: optimizer config and what-if + recommendation api"
```

---

### Task 9: Daily recommendation job

**Files:**
- Modify: `backend/app.py`
- Test: `backend/tests/test_optimizer_api.py` (add a job test)

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_optimizer_api.py`:

```python
def test_compute_daily_recommendation_persists(client, settings):
    # call the module-level job function directly with configured settings
    import dataclasses

    from backend.app import compute_daily_recommendation

    s = _opt_settings(settings)
    # the client fixture already archived the schedule into s.data_dir
    from backend.engine.runner import EngineRunner

    runner = EngineRunner.start(s)
    compute_daily_recommendation(s, runner.store)
    from datetime import datetime
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y%m%d")
    rec = runner.store.recommendation(today, "outbound")
    assert rec is not None
    assert rec["direction"] == "outbound"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_optimizer_api.py::test_compute_daily_recommendation_persists -v`
Expected: FAIL — no `compute_daily_recommendation`.

- [ ] **Step 3: Implement in `backend/app.py`**

Add a module-level function and register it in the lifespan as a daily job
(reusing `run_daily`):

```python
def compute_daily_recommendation(settings, store) -> None:
    """Compute and persist tomorrow's-ish outbound recommendation. No-op when
    the optimizer isn't configured."""
    if not (settings.commute_source and settings.board_stop_id and settings.alight_stop_id):
        return
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from backend.api.optimizer import _hhmm_to_local_s, _shape
    from backend.optimizer.params import OptimizerParams
    from backend.optimizer.recommend import recommend

    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    rec = recommend(
        store, direction="outbound", source=settings.commute_source,
        board_stop=settings.board_stop_id, alight_stop=settings.alight_stop_id,
        service_date=today.replace("-", ""),
        arrive_by_local_s=_hhmm_to_local_s(settings.arrive_by_local),
        access_distance_m=settings.access_distance_m,
        egress_distance_m=settings.egress_distance_m, params=OptimizerParams())
    store.write_recommendation(today.replace("-", ""), "outbound", _shape(rec, today))
```

In the lifespan, alongside the archiver daily task, add a recommendation task
(runs a few hours before the morning target — reuse `run_daily` with an hour
config; default 9 UTC ≈ 5am ET is fine for a morning rec; the existing
`archive_hour_utc` pattern is the template). Add a settings field
`recommendation_hour_utc: int = 9` (with env `CT_RECOMMENDATION_HOUR_UTC`) OR
reuse a literal — keep it simple with a literal `hour_utc=9` and a comment.
Wire:

```python
        rec_task = asyncio.create_task(run_daily(
            lambda: compute_daily_recommendation(settings, app.state.runner.store),
            hour_utc=9))
```

and cancel/await it in shutdown like the other tasks.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_optimizer_api.py -q && pytest backend/tests -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app.py backend/tests/test_optimizer_api.py
git commit -m "feat: daily recommendation job in app lifespan"
```

---

### Task 10: Frontend — ECharts + Optimizer view

**Files:**
- Modify: `frontend/package.json` (add `echarts`)
- Create: `frontend/src/lib/echarts.ts`, `frontend/src/lib/FanChart.svelte`,
  `frontend/src/lib/ItineraryCard.svelte`,
  `frontend/src/routes/optimizer/+page.svelte`,
  `frontend/src/routes/optimizer/+page.ts`
- Modify: `frontend/src/lib/api.ts`, `frontend/src/routes/+layout.svelte`

- [ ] **Step 1: Add types + client to `frontend/src/lib/api.ts`**

```ts
export interface ItineraryOption {
  gtfs_trip_id: string;
  route_name: string;
  headsign: string;
  board_stop: string;
  alight_stop: string;
  leave_by: string;
  scheduled_dep: string;
  scheduled_arr: string;
  p50_arrive: string;
  p90_arrive: string;
}

export interface OptimizerResult {
  direction: string;
  service_date: string;
  arrive_by_local: string;
  options: ItineraryOption[];
}

export async function getOptimizer(
  fetchFn: typeof fetch, date: string, arriveBy?: string,
): Promise<OptimizerResult> {
  const qs = new URLSearchParams({ date });
  if (arriveBy) qs.set('arrive_by', arriveBy);
  const resp = await fetchFn(`/api/optimizer?${qs}`);
  if (resp.status === 409) throw new Error('optimizer-unconfigured');
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return resp.json();
}

export async function getRecommendation(fetchFn: typeof fetch): Promise<OptimizerResult> {
  const resp = await fetchFn('/api/recommendation');
  if (resp.status === 409) throw new Error('optimizer-unconfigured');
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return resp.json();
}
```

- [ ] **Step 2: ECharts helper + FanChart + ItineraryCard**

```ts
// frontend/src/lib/echarts.ts
// Minimal ECharts wrapper so components don't each import the whole library.
import * as echarts from 'echarts';

export function mountChart(el: HTMLElement, option: echarts.EChartsOption) {
  const chart = echarts.init(el);
  chart.setOption(option);
  const onResize = () => chart.resize();
  window.addEventListener('resize', onResize);
  return () => {
    window.removeEventListener('resize', onResize);
    chart.dispose();
  };
}
```

```svelte
<!-- frontend/src/lib/FanChart.svelte -->
<!-- Each option as a horizontal P50–P90 band positioned on a time axis. -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { mountChart } from './echarts';
  import type { ItineraryOption } from './api';

  let { options }: { options: ItineraryOption[] } = $props();
  let el: HTMLDivElement;

  function ms(iso: string): number {
    return new Date(iso).getTime();
  }

  onMount(() => {
    const rows = options.map((o) => o.gtfs_trip_id);
    const p50 = options.map((o, i) => [ms(o.p50_arrive), i]);
    const p90 = options.map((o, i) => [ms(o.p90_arrive), i]);
    return mountChart(el, {
      grid: { left: 90, right: 24, top: 16, bottom: 40 },
      xAxis: { type: 'time', name: 'arrival' },
      yAxis: { type: 'category', data: rows },
      series: [
        { type: 'line', data: p50, symbol: 'circle', symbolSize: 8,
          lineStyle: { opacity: 0 }, name: 'P50' },
        { type: 'line', data: p90, symbol: 'diamond', symbolSize: 8,
          lineStyle: { opacity: 0 }, name: 'P90' },
      ],
      tooltip: { trigger: 'item' },
    });
  });
</script>

<div bind:this={el} class="chart" data-testid="fan-chart"></div>

<style>.chart { height: 260px; width: 100%; }</style>
```

```svelte
<!-- frontend/src/lib/ItineraryCard.svelte -->
<script lang="ts">
  import type { ItineraryOption } from './api';
  let { option, best }: { option: ItineraryOption; best: boolean } = $props();
  function clock(iso: string): string {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
</script>

<div class="card" class:best data-testid="itinerary-{option.gtfs_trip_id}">
  {#if best}<span class="badge">recommended</span>{/if}
  <div class="leave">Leave by <strong>{clock(option.leave_by)}</strong></div>
  <div class="train">🚆 {option.route_name} · {option.gtfs_trip_id} → {option.headsign}</div>
  <div class="stops">{option.board_stop} → {option.alight_stop}</div>
  <div class="arrive">
    Arrive {clock(option.p50_arrive)} <span class="p90">(P90 {clock(option.p90_arrive)})</span>
  </div>
</div>

<style>
  .card { border: 1px solid #ddd; border-radius: 8px; padding: 0.75rem 1rem; margin: 0.5rem 0; }
  .card.best { border-color: #1565c0; background: #f3f8ff; }
  .badge { background: #1565c0; color: #fff; font-size: 0.75rem; border-radius: 4px;
           padding: 0.1rem 0.5rem; }
  .leave { font-size: 1.1rem; margin: 0.25rem 0; }
  .train { color: #333; }
  .stops { color: #777; font-size: 0.9rem; }
  .p90 { color: #999; }
</style>
```

- [ ] **Step 3: Optimizer route + load**

```ts
// frontend/src/routes/optimizer/+page.ts
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
  const today = new Date().toISOString().slice(0, 10);
  return { today };
};
```

```svelte
<!-- frontend/src/routes/optimizer/+page.svelte -->
<script lang="ts">
  import { getOptimizer, type OptimizerResult } from '$lib/api';
  import FanChart from '$lib/FanChart.svelte';
  import ItineraryCard from '$lib/ItineraryCard.svelte';

  let { data } = $props();
  let date = $state(data.today);
  let arriveBy = $state('09:00');
  let result: OptimizerResult | null = $state(null);
  let error = $state('');
  let loading = $state(false);

  async function run() {
    loading = true;
    error = '';
    try {
      result = await getOptimizer(fetch, date, arriveBy);
    } catch (e) {
      error = e instanceof Error && e.message === 'optimizer-unconfigured'
        ? 'The optimizer is not configured (set the commute stations in the backend).'
        : 'Could not compute itineraries.';
      result = null;
    } finally {
      loading = false;
    }
  }
</script>

<h1>Optimizer</h1>
<form class="goal" onsubmit={(e) => { e.preventDefault(); run(); }}>
  <label>Date <input type="date" bind:value={date} /></label>
  <label>Arrive by <input type="time" bind:value={arriveBy} /></label>
  <button type="submit" disabled={loading}>{loading ? 'Computing…' : 'Find trains'}</button>
</form>

{#if error}<p class="error">{error}</p>{/if}

{#if result}
  {#if result.options.length === 0}
    <p>No trains arrive in time for that goal.</p>
  {:else}
    <FanChart options={result.options} />
    {#each result.options as opt, i (opt.gtfs_trip_id)}
      <ItineraryCard option={opt} best={i === 0} />
    {/each}
  {/if}
{/if}

<style>
  .goal { display: flex; gap: 1rem; align-items: end; margin-bottom: 1rem; }
  .goal label { display: flex; flex-direction: column; gap: 0.25rem; }
  .error { color: #c62828; }
</style>
```

- [ ] **Step 4: Make Optimizer + Today real nav links**

In `frontend/src/routes/+layout.svelte`, change the `Optimizer` and `Today`
placeholders to `<a href="/optimizer">Optimizer</a>` and
`<a href="/today">Today</a>`. Leave Trends/Health as placeholders.

Add `"echarts": "^5.5.0"` to `frontend/package.json` dependencies; run
`cd frontend && npm install`.

- [ ] **Step 5: Verify**

Run: `cd frontend && npm install && npm run check && npm run build`
Expected: clean; build succeeds (ECharts adds a chunk-size advisory — benign).

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/lib/echarts.ts \
        frontend/src/lib/FanChart.svelte frontend/src/lib/ItineraryCard.svelte \
        frontend/src/lib/api.ts frontend/src/routes/optimizer/ frontend/src/routes/+layout.svelte
git commit -m "feat: optimizer view with itinerary cards and fan chart"
```

---

### Task 11: Frontend — Today recommendation card

**Files:**
- Create: `frontend/src/routes/today/+page.svelte`, `frontend/src/routes/today/+page.ts`

- [ ] **Step 1: Load + page**

```ts
// frontend/src/routes/today/+page.ts
import { getRecommendation, type OptimizerResult } from '$lib/api';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => {
  try {
    const rec: OptimizerResult = await getRecommendation(fetch);
    return { rec, configured: true as const };
  } catch (e) {
    if (e instanceof Error && e.message === 'optimizer-unconfigured') {
      return { rec: null, configured: false as const };
    }
    throw e;
  }
};
```

```svelte
<!-- frontend/src/routes/today/+page.svelte -->
<script lang="ts">
  import ItineraryCard from '$lib/ItineraryCard.svelte';

  let { data } = $props();
</script>

<h1>Today</h1>

{#if !data.configured}
  <p class="muted">
    The optimizer isn't configured yet. Set your commute stations and target arrival
    time in the backend to see a daily recommendation here.
  </p>
{:else if data.rec && data.rec.options.length > 0}
  <p class="goal">Get to {data.rec.options[0].alight_stop} by {data.rec.arrive_by_local}:</p>
  <ItineraryCard option={data.rec.options[0]} best={true} />
  {#if data.rec.options.length > 1}
    <h2>Alternatives</h2>
    {#each data.rec.options.slice(1, 4) as opt (opt.gtfs_trip_id)}
      <ItineraryCard option={opt} best={false} />
    {/each}
  {/if}
  <p class="hint"><a href="/optimizer">Try a different arrival time →</a></p>
{:else}
  <p class="muted">No trains arrive in time for your target today.</p>
{/if}

<style>
  .goal { font-size: 1.1rem; }
  .muted { color: #777; }
  .hint { margin-top: 1.5rem; }
</style>
```

- [ ] **Step 2: Verify**

Run: `cd frontend && npm run check && npm run build && npx vitest run`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/today/
git commit -m "feat: today view recommendation card"
```

---

### Task 12: Docs + final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: README** — extend the "Rewrite backend" section:
- "### Optimizer" subsection: the rail-aware optimizer — leg models from
  matched history shrunk toward schedule, itinerary composer ranking scheduled
  trains by P50/P90 door-to-door arrival (seeded Monte Carlo). Config env vars:
  `CT_COMMUTE_SOURCE` (e.g. `gtfs_njt`), `CT_BOARD_STOP_ID`, `CT_ALIGHT_STOP_ID`
  (GTFS stop IDs for your home station + work terminal), `CT_ARRIVE_BY_LOCAL`
  (HH:MM, default 09:00), `CT_ACCESS_DISTANCE_M` / `CT_EGRESS_DISTANCE_M`
  (door↔station distances, defaults 500/650). Endpoints: `GET /api/optimizer?date=&arrive_by=`
  (what-if), `GET /api/recommendation` (today's, computed-and-cached). A daily
  job precomputes the morning recommendation. Frontend: Today (recommendation
  card) and Optimizer (goal → ranked options with a P50/P90 fan chart) views.
- Note: live position-vs-plan tracking and push notifications are Phase 6.

- [ ] **Step 2: Final verification**

Run: `ruff format backend/ && ruff check src/ tests/ backend/ && ruff format --check src/ tests/ backend/ && pytest --tb=short -q`
Expected: clean; ~445 tests pass.
Run: `cd frontend && npm run check && npx vitest run && npm run build`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: optimizer config, endpoints, and views"
```

---

## Verification at phase end

1. Full python suite green; ruff clean; svelte-check clean; vitest green.
2. Determinism: call `recommend(...)` twice on identical derived data → byte-identical options (the seeded Monte Carlo guarantees this; a test asserts it in test_recommend or compose).
3. Container smoke (controller, podman): build the image; with `CT_COMMUTE_SOURCE`/`CT_BOARD_STOP_ID`/`CT_ALIGHT_STOP_ID` set and a GTFS snapshot ingested, `GET /api/optimizer?date=&arrive_by=` returns ranked options; `GET /today` and `GET /optimizer` deep-links serve the SPA. Without config, `/api/optimizer` returns 409 and the Today view shows the "not configured" message.
4. `gh run list` green after push.
5. Realism note (manual, post-deploy): with real NJT GTFS configured (board/alight stop IDs from the NJT feed) the what-if returns actual NEC trains; leg models stay schedule-dominated until matched-trip history accumulates.
