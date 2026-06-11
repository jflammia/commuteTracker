from backend.optimizer.legs import LegObservation
from backend.storage.derived import DerivedStore


def _obs(trip_id, kind, dur, **kw):
    return LegObservation(
        trip_id=trip_id,
        direction="outbound",
        leg_index=kw.get("leg_index", 0),
        kind=kind,
        duration_s=dur,
        distance_m=kw.get("distance_m", 500.0),
        gtfs_trip_id=kw.get("gtfs_trip_id"),
        source=kw.get("source"),
        route_name=kw.get("route_name"),
        scheduled_dep_s=kw.get("scheduled_dep_s"),
        delta_s=kw.get("delta_s"),
        board_stop=kw.get("board_stop"),
        alight_stop=kw.get("alight_stop"),
    )


def test_write_and_read_leg_observations(settings):
    store = DerivedStore(settings)
    store.write_leg_observations(
        "t1",
        [
            _obs("t1", "access", 360.0),
            _obs("t1", "ride:gtfs_njt:NEC", 2220.0, route_name="NEC", delta_s=120.0),
        ],
    )
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
    store.write_recommendation(
        "2026-06-11",
        "outbound",
        {
            "goal": "arrive_by",
            "target_ts": "2026-06-11T13:00:00+00:00",
            "options": [{"gtfs_trip_id": "NEC3838", "leave_by_ts": "2026-06-11T11:38:00+00:00"}],
        },
    )
    rec = store.recommendation("2026-06-11", "outbound")
    assert rec["options"][0]["gtfs_trip_id"] == "NEC3838"
    assert store.recommendation("2026-06-12", "outbound") is None
