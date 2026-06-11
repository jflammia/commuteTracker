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
    store: DerivedStore,
    *,
    direction: str,
    source: str,
    board_stop: str,
    alight_stop: str,
    service_date: str,
    arrive_by_local_s: int,
    access_distance_m: float,
    egress_distance_m: float,
    params: OptimizerParams,
) -> dict:
    models = LegModels.build(store.leg_observations(), params)
    cands = candidate_itineraries(
        store.con,
        source=source,
        board_stop=board_stop,
        alight_stop=alight_stop,
        service_date=service_date,
        arrive_by_local_s=arrive_by_local_s,
        egress_pad_s=models.egress(direction, egress_distance_m).quantile(0.5),
    )
    access = models.access(direction, access_distance_m)
    egress = models.egress(direction, egress_distance_m)
    options = []
    for it in cands:
        ride = models.ride(
            it.source,
            it.route_name,
            scheduled_ride_s=float(it.scheduled_arr_s - it.scheduled_dep_s),
        )
        comp = compose_itinerary(
            it,
            access=access,
            ride=ride,
            egress=egress,
            service_date_midnight_local_s=0,
            params=params,
        )
        options.append(
            {
                "gtfs_trip_id": comp["gtfs_trip_id"],
                "route_name": comp["route_name"],
                "headsign": comp["headsign"],
                "board_stop": comp["board_stop"],
                "alight_stop": comp["alight_stop"],
                "scheduled_dep_local_s": comp["scheduled_dep_s"],
                "scheduled_arr_local_s": comp["scheduled_arr_s"],
                "leave_by_local_s": comp["leave_by_s"],
                "p50_arr_local_s": comp["p50_arr_s"],
                "p90_arr_local_s": comp["p90_arr_s"],
            }
        )
    # already latest-departure-first from the enumerator; keep that order
    return {
        "goal": "arrive_by",
        "direction": direction,
        "service_date": service_date,
        "arrive_by_local_s": arrive_by_local_s,
        "options": options,
    }
