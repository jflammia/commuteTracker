from backend.engine.types import EnrichedPoint, Point


def test_point_from_owntracks_location():
    p = Point.from_owntracks(
        {"_type": "location", "tst": 1781100000, "lat": 40.7, "lon": -74.4, "acc": 10}
    )
    assert p == Point(ts=1781100000.0, lat=40.7, lon=-74.4, accuracy_m=10.0)


def test_point_from_owntracks_missing_acc():
    p = Point.from_owntracks({"_type": "location", "tst": 1, "lat": 1.0, "lon": 2.0})
    assert p.accuracy_m is None


def test_point_from_owntracks_non_location_returns_none():
    assert Point.from_owntracks({"_type": "transition", "tst": 1}) is None
    assert Point.from_owntracks({"_type": "location", "lat": 1.0}) is None  # missing fields
    assert Point.from_owntracks({"_type": "location", "tst": "x", "lat": 1, "lon": 2}) is None


def test_enriched_point_roundtrips_dict():
    ep = EnrichedPoint(
        ts=1.0,
        lat=40.7,
        lon=-74.4,
        accuracy_m=5.0,
        speed_mps=1.2,
        heading_deg=90.0,
        distance_m=36.0,
        geofence="home",
    )
    assert EnrichedPoint(**ep.to_dict()) == ep
