from backend.optimizer.legs import LegObservation
from backend.optimizer.legstats import LegModels
from backend.optimizer.params import OptimizerParams
from backend.storage.derived import DerivedStore

P = OptimizerParams()


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


# ---------------------------------------------------------------------------
# Task 4: Leg models from observations
# ---------------------------------------------------------------------------


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
        store.write_leg_observations(
            tid,
            [
                _obs(
                    tid,
                    "ride:gtfs_njt:NEC",
                    2220.0,
                    route_name="NEC",
                    source="gtfs_njt",
                    gtfs_trip_id="NEC3838",
                    delta_s=delta,
                    scheduled_dep_s=27600,
                )
            ],
        )
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
