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
    itin: Itinerary,
    *,
    access: EmpiricalDistribution,
    ride: EmpiricalDistribution,
    egress: EmpiricalDistribution,
    service_date_midnight_local_s: int,
    params: OptimizerParams,
) -> dict:
    rng = random.Random(params.mc_seed)
    arrivals = []
    leave_bys = []
    for _ in range(params.mc_iters):
        a = access.sample(rng)
        r = ride.sample(rng)  # actual ride seconds (schedule + delay)
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
