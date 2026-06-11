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
        "gtfs_njt",
        {
            "received_at": "2026-06-09T05:00:00+00:00",
            "payload": {"url": "u", "status": 200, "b64": base64.b64encode(z).decode()},
        },
    )
    store = DerivedStore(settings)
    parse_gtfs(store.con, "gtfs_njt", latest_snapshot(settings, "gtfs_njt"), fetched_at="x")
    return store


def test_recommend_ranks_feasible_trains(settings):
    store = _load(settings)
    rec = recommend(
        store,
        direction="outbound",
        source="gtfs_njt",
        board_stop="MP",
        alight_stop="NYP",
        service_date="20260610",
        arrive_by_local_s=8 * 3600 + 30 * 60,
        access_distance_m=500.0,
        egress_distance_m=650.0,
        params=P,
    )
    assert rec["options"]  # at least NEC1 feasible
    top = rec["options"][0]
    assert top["gtfs_trip_id"] == "NEC1"  # latest feasible train
    assert "leave_by_local_s" in top
    assert "p50_arr_local_s" in top and "p90_arr_local_s" in top
    assert top["p90_arr_local_s"] <= 8 * 3600 + 30 * 60 + P.ride_delay_spread_s


def test_recommend_empty_when_no_trains_make_it(settings):
    store = _load(settings)
    rec = recommend(
        store,
        direction="outbound",
        source="gtfs_njt",
        board_stop="MP",
        alight_stop="NYP",
        service_date="20260610",
        arrive_by_local_s=7 * 3600,  # 07:00 — no train arrives in time
        access_distance_m=500.0,
        egress_distance_m=650.0,
        params=P,
    )
    assert rec["options"] == []
