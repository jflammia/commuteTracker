from backend.tests.test_derived_store import _closed
from backend.storage.derived import DerivedStore


def _store_with_trip(settings):
    store = DerivedStore(settings)
    store.write_trip_closed(_closed())  # trip_id t1000, 1 vehicle segment (seg_index 0)
    return store


def test_apply_segment_mode_overrides_effective(settings):
    store = _store_with_trip(settings)
    ok = store.apply_label(
        {"type": "segment_mode", "trip_id": "t1000", "seg_index": 0, "value": "train"}
    )
    assert ok is True
    d = store.get_trip("t1000")
    seg = d["segments"][0]
    assert seg["mode"] == "vehicle"  # heuristic untouched
    assert seg["mode_effective"] == "train"  # label wins
    assert seg["mode_source"] == "label"
    assert d["itinerary"][0]["mode"] == "train"


def test_unlabeled_segment_uses_heuristic(settings):
    store = _store_with_trip(settings)
    seg = store.get_trip("t1000")["segments"][0]
    assert seg["mode_effective"] == "vehicle"
    assert seg["mode_source"] == "heuristic"


def test_latest_label_wins(settings):
    store = _store_with_trip(settings)
    store.apply_label(
        {"type": "segment_mode", "trip_id": "t1000", "seg_index": 0, "value": "train"}
    )
    store.apply_label({"type": "segment_mode", "trip_id": "t1000", "seg_index": 0, "value": "walk"})
    assert store.get_trip("t1000")["segments"][0]["mode_effective"] == "walk"


def test_trip_flag_and_reviewed(settings):
    store = _store_with_trip(settings)
    store.apply_label({"type": "trip_flag", "trip_id": "t1000", "value": "phantom"})
    store.apply_label({"type": "trip_reviewed", "trip_id": "t1000", "value": True})
    d = store.get_trip("t1000")
    assert d["trip"]["flag"] == "phantom"
    assert d["trip"]["reviewed"] is True
    assert store.list_trips()[0]["reviewed"] is True


def test_unreviewed_default(settings):
    store = _store_with_trip(settings)
    assert store.get_trip("t1000")["trip"]["reviewed"] is False
    assert store.get_trip("t1000")["trip"]["flag"] is None


def test_train_match_confirmation_surfaces_in_itinerary(settings):
    store = _store_with_trip(settings)
    store.apply_label({"type": "train_match", "trip_id": "t1000", "seg_index": 0, "value": "wrong"})
    leg = store.get_trip("t1000")["itinerary"][0]
    assert leg["confirmation"] == "wrong"


def test_apply_label_unknown_trip_returns_false(settings):
    store = DerivedStore(settings)
    ok = store.apply_label({"type": "trip_flag", "trip_id": "nope", "value": "ok"})
    assert ok is False


def test_list_trips_reviewed_filter(settings):
    store = _store_with_trip(settings)
    store.write_trip_closed(_closed(trip_id="t9000", start=9000.0))
    store.apply_label({"type": "trip_reviewed", "trip_id": "t1000", "value": True})
    assert [t["trip_id"] for t in store.list_trips(reviewed=False)] == ["t9000"]
    assert [t["trip_id"] for t in store.list_trips(reviewed=True)] == ["t1000"]
    assert len(store.list_trips()) == 2


def test_labels_survive_trip_rewrite(settings):
    store = _store_with_trip(settings)
    store.apply_label(
        {"type": "segment_mode", "trip_id": "t1000", "seg_index": 0, "value": "train"}
    )
    store.write_trip_closed(_closed())  # same trip_id rewritten
    assert store.get_trip("t1000")["segments"][0]["mode_effective"] == "train"
