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
        "gtfs_njt",
        {
            "received_at": "2026-06-09T05:00:00+00:00",
            "payload": {
                "url": "u",
                "status": 200,
                "b64": base64.b64encode(z).decode(),
            },
        },
    )
    store = DerivedStore(settings)
    parse_gtfs(store.con, "gtfs_njt", latest_snapshot(settings, "gtfs_njt"), fetched_at="x")
    return store


def test_enumerates_trains_that_can_make_the_goal(settings):
    store = _load(settings)
    # goal: at NY Penn by 08:30 local on 2026-06-10 (Wednesday)
    its = candidate_itineraries(
        store.con,
        source="gtfs_njt",
        board_stop="MP",
        alight_stop="NYP",
        service_date="20260610",
        arrive_by_local_s=8 * 3600 + 30 * 60,
        egress_pad_s=600.0,
    )
    ids = [it.gtfs_trip_id for it in its]
    assert "NEC1" in ids  # arrives 08:15 + egress < 08:30 → feasible
    assert "NEC2" not in ids  # arrives 08:39 → too late
    assert "NEC3" in ids  # arrives 07:07 → feasible (early but valid)


def test_itineraries_sorted_latest_departure_first(settings):
    store = _load(settings)
    its = candidate_itineraries(
        store.con,
        source="gtfs_njt",
        board_stop="MP",
        alight_stop="NYP",
        service_date="20260610",
        arrive_by_local_s=8 * 3600 + 30 * 60,
        egress_pad_s=600.0,
    )
    deps = [it.scheduled_dep_s for it in its]
    assert deps == sorted(deps, reverse=True)  # latest feasible departure first


def test_no_service_day_yields_nothing(settings):
    store = _load(settings)
    its = candidate_itineraries(
        store.con,
        source="gtfs_njt",
        board_stop="MP",
        alight_stop="NYP",
        service_date="20270101",
        arrive_by_local_s=8 * 3600 + 30 * 60,
        egress_pad_s=600.0,
    )
    assert its == []


def test_itinerary_carries_schedule_fields(settings):
    store = _load(settings)
    it = candidate_itineraries(
        store.con,
        source="gtfs_njt",
        board_stop="MP",
        alight_stop="NYP",
        service_date="20260610",
        arrive_by_local_s=8 * 3600 + 30 * 60,
        egress_pad_s=600.0,
    )[0]
    assert isinstance(it, Itinerary)
    assert it.scheduled_dep_s == 8 * 3600 + 2 * 60 or it.scheduled_dep_s == 7 * 3600 + 38 * 60
    assert it.scheduled_arr_s > it.scheduled_dep_s
    assert it.route_name == "Northeast Corridor"
