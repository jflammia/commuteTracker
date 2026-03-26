# ADR-002: Pluggable Transport Mode Classifier Architecture

**Date:** 2026-03-26
**Status:** Accepted

## Context

The initial segmenter classified transport mode using fixed speed thresholds:
- stationary: < 1 km/h
- walking: 1-7 km/h
- driving: 7-30 km/h
- train: >= 30 km/h

This works as a baseline but fails in ambiguous cases:
- A car on a highway at 80 km/h looks like a train
- A train slowing into a station at 20 km/h looks like driving
- Speed alone cannot distinguish a bus from a car
- No spatial awareness (rail corridors, station locations)

The project is intended for open source — the solution must be generic, not hardcoded to any specific commute.

## Decision

### Ensemble classifier with weighted voting

Replace the single speed-threshold classifier with an **ensemble of classifiers**, each producing per-point confidence scores that are combined via weighted voting.

```
GPS Point → [Classifier 1] → ModeScores(walk=0.0, drive=0.2, train=0.8, ...)
          → [Classifier 2] → ModeScores(...)
          → [Classifier 3] → ModeScores(...)
                ↓
          Weighted sum → Winner
```

### Built-in classifiers

| Classifier | Weight | Config needed | Signal |
|---|---|---|---|
| **SpeedClassifier** | 1.0 | None | Speed thresholds (configurable per-instance) |
| **SpeedVarianceClassifier** | 0.5 | None | Rolling coefficient of variation. Smooth high speed = train; variable = driving |
| **WaypointClassifier** | 1.5 | `zones.json` | User-defined geographic zones (stations, parking lots) that bias mode and force segment boundaries |
| **CorridorClassifier** | 1.2 | `zones.json` | User-defined route geometries (rail lines, bus routes) with distance-based confidence decay |

### Progressive enhancement model

The system works at three levels of user investment:

1. **Zero config** (install and run): Speed + SpeedVariance classifiers active. Reasonable for most commutes.
2. **Zone config** (`zones.json`): User defines waypoints and/or corridors. Significantly improves accuracy for multi-modal commutes.
3. **Label corrections** (dashboard): User corrects misclassified segments. Corrections are stored in the database and applied as overrides during re-processing. Ground truth for future ML.

Each level strictly improves on the previous. No level requires the next.

### Classifier protocol

Any classifier must implement:

```python
class TransportClassifier(Protocol):
    @property
    def name(self) -> str: ...
    def score(self, df: pl.DataFrame) -> list[ModeScores]: ...
```

This allows third-party classifiers (accelerometer data, GTFS schedule matching, Bluetooth beacons, etc.) to plug in without modifying core code.

### ModeScores

Each classifier returns one `ModeScores` per GPS point:

```python
@dataclass
class ModeScores:
    stationary: float = 0.0
    walking: float = 0.0
    driving: float = 0.0
    train: float = 0.0
```

Scores are non-negative. They don't need to sum to 1 — the ensemble scales by weight and picks the winner by total score.

### Waypoint boundaries

Waypoints serve two purposes:
1. **Mode bias**: If a waypoint has a `mode_hint`, points inside it get a confidence boost
2. **Forced segment boundaries**: When a point enters or exits *any* waypoint zone, a new segment is started regardless of transport mode. This prevents GPS noise from merging distinct legs (e.g., walking to a station platform vs boarding the train).

Short segment merging respects waypoint boundaries — it will not merge a segment across a waypoint transition.

### Label corrections as overrides

User corrections from the Label Store are applied **after** the ensemble classifies but **before** Parquet output. The pipeline:
1. Runs the ensemble classifier
2. Checks `LabelStore.get_corrections_map()` for `(commute_id, segment_id)` overrides
3. Replaces `transport_mode` values for matching segments

This means corrections survive re-processing. Users don't need to re-label after a pipeline rebuild.

### Zone configuration format

```json
{
    "waypoints": [
        {
            "name": "Penn Station",
            "lat": 40.7506,
            "lon": -73.9935,
            "radius_m": 100,
            "mode_hint": "train"
        }
    ],
    "corridors": [
        {
            "name": "NJ Transit Northeast Corridor",
            "mode": "train",
            "buffer_m": 150,
            "points": [[40.75, -73.99], [40.80, -73.97], [40.85, -73.95]]
        }
    ]
}
```

Config is loaded from (in order): explicit path, `ZONES_CONFIG` env var, `zones.json` in project root, or not at all (zero-config mode).

## Consequences

**Positive:**
- Works out of the box with no configuration
- Incrementally improvable without code changes (add zones.json, label corrections)
- Extensible via protocol — third parties can add classifiers
- Spatial classifiers (waypoint, corridor) solve the speed-overlap problem
- Speed variance classifier is fully automatic and improves drive vs train separation
- Label corrections are durable (SQLite) and survive re-processing
- Open-source friendly: no hardcoded coordinates, no vendor dependencies

**Negative:**
- More code complexity than a simple speed threshold
- Ensemble voting can produce surprising results if weights are poorly tuned
- Corridor distance checking is O(n × m) per point (n=corridor segments, m=points) — could be slow for very long corridors

**Mitigated by:**
- Zero-config mode means complexity is opt-in
- Default weights are conservative (speed=1.0 baseline, others additive)
- Corridor checking uses midpoint sampling, not exact projection — fast enough for commute-scale data

## Alternatives Considered

1. **ML-only classification**: Skip rules entirely, train a model. Rejected for v1: requires labeled training data that doesn't exist yet. The label correction system builds this dataset incrementally.

2. **Fixed mode sequence** (e.g., always walk→drive→train→walk): Too rigid. Commutes vary. Users might take a bus some days, drive others.

3. **Map-matching via external API** (Google, Mapbox): Requires API keys, costs money, adds latency, creates vendor lock-in. Contradicts the open-source, self-hosted design.

4. **GTFS schedule matching**: Promising for transit detection. Deferred to a future classifier — would be a great community contribution.
