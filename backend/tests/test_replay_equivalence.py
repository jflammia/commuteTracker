from backend.engine.machine import EngineState, TripEngine
from backend.engine.params import EngineParams
from backend.engine.types import TripClosed
from backend.tests.synth import commute


def _events(engine, pts):
    out = []
    for pt in pts:
        out.extend(engine.process(pt))
    return out


def _trips(events):
    return [e.trip for e in events if isinstance(e, TripClosed)]


def test_split_replay_equals_full_replay():
    pts1, _, lat_end = commute(t0=1_781_100_000.0)
    pts2, _, _ = commute(t0=1_781_130_000.0, lat0=lat_end)
    stream = pts1 + pts2

    full = TripEngine(EngineParams(), geofences=[])
    full_trips = _trips(_events(full, stream))

    for split in (1, len(stream) // 3, len(stream) // 2, len(stream) - 1):
        first = TripEngine(EngineParams(), geofences=[])
        trips_a = _trips(_events(first, stream[:split]))
        # serialize → restore (simulates process restart)
        second = TripEngine(EngineParams(), geofences=[])
        second.state = EngineState.from_dict(first.state.to_dict())
        trips_b = _trips(_events(second, stream[split:]))
        assert trips_a + trips_b == full_trips, f"diverged at split={split}"
        assert second.state == full.state, f"state diverged at split={split}"
