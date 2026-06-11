from backend.optimizer.legs import decompose_trip

# A trip detail dict as returned by DerivedStore.get_trip (the shape the engine
# already produces): segments with mode_effective, itinerary legs carrying the
# matched train, trip metadata.
TRIP = {
    "trip": {
        "trip_id": "t100",
        "direction": "outbound",
        "start_ts": "2026-06-10T11:30:00+00:00",  # 07:30 EDT
        "end_ts": "2026-06-10T12:24:00+00:00",
    },
    "segments": [
        {
            "seg_index": 0,
            "mode_effective": "walk",
            "start_ts": "2026-06-10T11:30:00+00:00",
            "end_ts": "2026-06-10T11:36:00+00:00",
            "duration_s": 360.0,
            "distance_m": 480.0,
        },
        {
            "seg_index": 1,
            "mode_effective": "train",
            "start_ts": "2026-06-10T11:38:00+00:00",
            "end_ts": "2026-06-10T12:15:00+00:00",
            "duration_s": 2220.0,
            "distance_m": 30000.0,
        },
        {
            "seg_index": 2,
            "mode_effective": "walk",
            "start_ts": "2026-06-10T12:16:00+00:00",
            "end_ts": "2026-06-10T12:24:00+00:00",
            "duration_s": 480.0,
            "distance_m": 640.0,
        },
    ],
    "itinerary": [
        {"mode": "walk", "train": None},
        {
            "mode": "train",
            "train": {
                "seg_index": 1,
                "source": "gtfs_njt",
                "gtfs_trip_id": "NEC3838",
                "route_name": "Northeast Corridor",
                "board_stop": "Metropark",
                "alight_stop": "New York Penn Station",
                "scheduled_dep_s": 27600,
                "delta_s": 120.0,
            },
        },
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
            {
                "seg_index": 0,
                "mode_effective": "walk",
                "duration_s": 120.0,
                "distance_m": 150.0,
                "start_ts": "2026-06-10T11:30:00+00:00",
                "end_ts": "2026-06-10T11:32:00+00:00",
            },
            {
                "seg_index": 1,
                "mode_effective": "vehicle",
                "duration_s": 600.0,
                "distance_m": 4000.0,
                "start_ts": "2026-06-10T11:32:00+00:00",
                "end_ts": "2026-06-10T11:42:00+00:00",
            },
            {
                "seg_index": 2,
                "mode_effective": "train",
                "duration_s": 2220.0,
                "distance_m": 30000.0,
                "start_ts": "2026-06-10T11:44:00+00:00",
                "end_ts": "2026-06-10T12:21:00+00:00",
            },
            {
                "seg_index": 3,
                "mode_effective": "walk",
                "duration_s": 300.0,
                "distance_m": 400.0,
                "start_ts": "2026-06-10T12:22:00+00:00",
                "end_ts": "2026-06-10T12:27:00+00:00",
            },
        ],
        "itinerary": [
            {"mode": "walk", "train": None},
            {"mode": "vehicle", "train": None},
            {
                "mode": "train",
                "train": {
                    "seg_index": 2,
                    "source": "gtfs_njt",
                    "gtfs_trip_id": "NEC3838",
                    "route_name": "Northeast Corridor",
                    "board_stop": "Metropark",
                    "alight_stop": "New York Penn Station",
                    "scheduled_dep_s": 27600,
                    "delta_s": 0.0,
                },
            },
            {"mode": "walk", "train": None},
        ],
    }
    legs = decompose_trip(trip)
    assert [leg.kind for leg in legs] == ["access", "ride:gtfs_njt:Northeast Corridor", "egress"]
    assert legs[0].duration_s == 720.0  # 120 + 600 merged access
    assert legs[0].distance_m == 4150.0


def test_decompose_two_rail_legs_with_transfer():
    trip = {
        "trip": TRIP["trip"],
        "segments": [
            {
                "seg_index": 0,
                "mode_effective": "walk",
                "duration_s": 300.0,
                "distance_m": 400.0,
                "start_ts": "2026-06-10T11:30:00+00:00",
                "end_ts": "2026-06-10T11:35:00+00:00",
            },
            {
                "seg_index": 1,
                "mode_effective": "train",
                "duration_s": 600.0,
                "distance_m": 8000.0,
                "start_ts": "2026-06-10T11:37:00+00:00",
                "end_ts": "2026-06-10T11:47:00+00:00",
            },
            {
                "seg_index": 2,
                "mode_effective": "walk",
                "duration_s": 240.0,
                "distance_m": 200.0,
                "start_ts": "2026-06-10T11:47:00+00:00",
                "end_ts": "2026-06-10T11:51:00+00:00",
            },
            {
                "seg_index": 3,
                "mode_effective": "train",
                "duration_s": 1200.0,
                "distance_m": 12000.0,
                "start_ts": "2026-06-10T11:53:00+00:00",
                "end_ts": "2026-06-10T12:13:00+00:00",
            },
            {
                "seg_index": 4,
                "mode_effective": "walk",
                "duration_s": 300.0,
                "distance_m": 400.0,
                "start_ts": "2026-06-10T12:14:00+00:00",
                "end_ts": "2026-06-10T12:19:00+00:00",
            },
        ],
        "itinerary": [
            {"mode": "walk", "train": None},
            {
                "mode": "train",
                "train": {
                    "seg_index": 1,
                    "source": "gtfs_njt",
                    "gtfs_trip_id": "NEC1",
                    "route_name": "Northeast Corridor",
                    "board_stop": "Metropark",
                    "alight_stop": "Newark Penn",
                    "scheduled_dep_s": 27600,
                    "delta_s": 0.0,
                },
            },
            {"mode": "walk", "train": None},
            {
                "mode": "train",
                "train": {
                    "seg_index": 3,
                    "source": "gtfs_path",
                    "gtfs_trip_id": "PATH1",
                    "route_name": "PATH",
                    "board_stop": "Newark",
                    "alight_stop": "33rd St",
                    "scheduled_dep_s": 28800,
                    "delta_s": 0.0,
                },
            },
            {"mode": "walk", "train": None},
        ],
    }
    legs = decompose_trip(trip)
    assert [leg.kind for leg in legs] == [
        "access",
        "ride:gtfs_njt:Northeast Corridor",
        "transfer",
        "ride:gtfs_path:PATH",
        "egress",
    ]


def test_decompose_unmatched_rail_returns_no_legs():
    # a vehicle segment that the matcher could not attribute → not optimizable
    trip = {
        "trip": TRIP["trip"],
        "segments": [
            {
                "seg_index": 0,
                "mode_effective": "vehicle",
                "duration_s": 1800.0,
                "distance_m": 20000.0,
                "start_ts": "2026-06-10T11:30:00+00:00",
                "end_ts": "2026-06-10T12:00:00+00:00",
            }
        ],
        "itinerary": [{"mode": "vehicle", "train": None}],
    }
    assert decompose_trip(trip) == []
