from backend.engine.types import EnrichedPoint, Point, PointRejected, Segment, Trip, TripClosed
from backend.storage.derived import DerivedStore
from backend.transit.matcher import TrainMatch


def _closed(trip_id="t1000", start=1000.0):
    pts = [
        EnrichedPoint(
            ts=start,
            lat=40.7,
            lon=-74.4,
            accuracy_m=5.0,
            speed_mps=0.0,
            heading_deg=None,
            distance_m=0.0,
            geofence="home",
        ),
        EnrichedPoint(
            ts=start + 30,
            lat=40.701,
            lon=-74.4,
            accuracy_m=5.0,
            speed_mps=3.7,
            heading_deg=0.0,
            distance_m=111.0,
            geofence=None,
        ),
        EnrichedPoint(
            ts=start + 600,
            lat=40.75,
            lon=-74.4,
            accuracy_m=5.0,
            speed_mps=9.5,
            heading_deg=0.0,
            distance_m=5400.0,
            geofence="work",
        ),
    ]
    trip = Trip(
        trip_id=trip_id,
        start_ts=start,
        end_ts=start + 600,
        duration_s=600.0,
        distance_m=5511.0,
        point_count=3,
        start_geofence="home",
        end_geofence="work",
        direction="outbound",
    )
    segs = [
        Segment(
            trip_id=trip_id,
            seg_index=0,
            mode="vehicle",
            start_ts=start,
            end_ts=start + 600,
            duration_s=600.0,
            distance_m=5511.0,
            point_count=3,
        )
    ]
    return TripClosed(trip=trip, segments=segs, points=pts)


def test_write_and_list_trips(settings):
    store = DerivedStore(settings)
    store.write_trip_closed(_closed())
    trips = store.list_trips()
    assert len(trips) == 1
    t = trips[0]
    assert t["trip_id"] == "t1000"
    assert t["direction"] == "outbound"
    assert t["start_ts"] == "1970-01-01T00:16:40+00:00"  # epoch 1000 as ISO UTC
    assert t["distance_m"] == 5511.0


def test_get_trip_detail(settings):
    store = DerivedStore(settings)
    store.write_trip_closed(_closed())
    d = store.get_trip("t1000")
    assert d["trip"]["trip_id"] == "t1000"
    assert [s["mode"] for s in d["segments"]] == ["vehicle"]
    assert len(d["points"]) == 3
    assert d["points"][0]["geofence"] == "home"


def test_get_missing_trip_returns_none(settings):
    assert DerivedStore(settings).get_trip("nope") is None


def test_rewrite_same_trip_id_is_idempotent(settings):
    store = DerivedStore(settings)
    store.write_trip_closed(_closed())
    store.write_trip_closed(_closed())
    assert len(store.list_trips()) == 1
    assert len(store.get_trip("t1000")["points"]) == 3


def test_rejected_points_recorded(settings):
    store = DerivedStore(settings)
    store.write_rejected(
        PointRejected(point=Point(ts=5.0, lat=1.0, lon=2.0, accuracy_m=900.0), reason="accuracy")
    )
    assert store.rejected_count() == 1


def test_truncate(settings):
    store = DerivedStore(settings)
    store.write_trip_closed(_closed())
    store.truncate()
    assert store.list_trips() == []


def test_list_trips_orders_newest_first(settings):
    store = DerivedStore(settings)
    store.write_trip_closed(_closed(trip_id="t1000", start=1000.0))
    store.write_trip_closed(_closed(trip_id="t9000", start=9000.0))
    assert [t["trip_id"] for t in store.list_trips()] == ["t9000", "t1000"]


def test_trip_with_no_segments_or_points_writes_cleanly(settings):
    store = DerivedStore(settings)
    bare = TripClosed(trip=_closed().trip)  # default empty segments/points
    store.write_trip_closed(bare)
    assert len(store.list_trips()) == 1
    d = store.get_trip("t1000")
    assert d["segments"] == []
    assert d["points"] == []


def _match(trip_id="t1", seg_index=0):
    return TrainMatch(
        trip_id=trip_id,
        seg_index=seg_index,
        source="njt",
        gtfs_trip_id="g1",
        route_name="Morristown Line",
        headsign="New York",
        board_stop="Madison",
        alight_stop="New York Penn",
        scheduled_dep_s=28800,
        delta_s=12.0,
    )


def test_write_train_matches_idempotent_per_trip(settings):
    store = DerivedStore(settings)
    matches = [_match(seg_index=0), _match(seg_index=1)]
    store.write_train_matches(matches)
    store.write_train_matches(matches)
    count = store.con.execute("SELECT COUNT(*) FROM train_matches WHERE trip_id = 't1'").fetchone()[
        0
    ]
    assert count == 2


def test_write_rejected_idempotent_by_ts(settings):
    store = DerivedStore(settings)
    rejected = PointRejected(
        point=Point(ts=5.0, lat=1.0, lon=2.0, accuracy_m=900.0), reason="accuracy"
    )
    store.write_rejected(rejected)
    store.write_rejected(rejected)
    count = store.con.execute("SELECT COUNT(*) FROM rejected_points WHERE ts = 5.0").fetchone()[0]
    assert count == 1
