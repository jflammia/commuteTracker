from backend.optimizer.compose import compose_itinerary
from backend.optimizer.distributions import EmpiricalDistribution
from backend.optimizer.itinerary import Itinerary
from backend.optimizer.params import OptimizerParams

P = OptimizerParams()
IT = Itinerary(
    source="gtfs_njt",
    gtfs_trip_id="NEC1",
    route_name="NEC",
    headsign="NYP",
    board_stop="Metropark",
    alight_stop="NY Penn",
    scheduled_dep_s=27480,
    scheduled_arr_s=29700,
)  # 07:38 → 08:15


def _fixed(mean):
    return EmpiricalDistribution(samples=[mean], prior_mean=mean, prior_weight=0)


def test_compose_produces_arrival_quantiles():
    result = compose_itinerary(
        IT,
        access=_fixed(360.0),
        ride=_fixed(2220.0),
        egress=_fixed(480.0),
        params=P,
    )
    # leave_by = dep - access; arrival ≈ dep + ride + egress
    assert result["p50_arr_s"] > IT.scheduled_dep_s
    assert result["p90_arr_s"] >= result["p50_arr_s"]
    assert result["leave_by_s"] <= IT.scheduled_dep_s
    # with fixed legs the spread collapses
    assert abs(result["p90_arr_s"] - result["p50_arr_s"]) < 60


def test_compose_widens_with_uncertain_legs():
    tight = compose_itinerary(
        IT,
        access=_fixed(360.0),
        ride=_fixed(2220.0),
        egress=_fixed(480.0),
        params=P,
    )
    wide_ride = EmpiricalDistribution(
        samples=[2000.0, 2220.0, 2600.0, 3000.0],
        prior_mean=2220.0,
        prior_weight=4,
        prior_spread=300.0,
    )
    wide = compose_itinerary(
        IT,
        access=_fixed(360.0),
        ride=wide_ride,
        egress=_fixed(480.0),
        params=P,
    )
    assert (wide["p90_arr_s"] - wide["p50_arr_s"]) > (tight["p90_arr_s"] - tight["p50_arr_s"])


def test_compose_is_deterministic():
    a = compose_itinerary(
        IT,
        access=_fixed(360.0),
        ride=_fixed(2220.0),
        egress=_fixed(480.0),
        params=P,
    )
    b = compose_itinerary(
        IT,
        access=_fixed(360.0),
        ride=_fixed(2220.0),
        egress=_fixed(480.0),
        params=P,
    )
    assert a == b
