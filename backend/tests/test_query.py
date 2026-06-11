from backend.storage.archive import Archiver
from backend.storage.query import EventQuery
from backend.storage.raw import RawStore


def _seed(settings):
    store = RawStore(settings.data_dir)
    store.append(
        "owntracks",
        {
            "received_at": "2026-06-09T01:00:00+00:00",
            "user": "j",
            "device": "d",
            "payload": {"_type": "location", "tst": 100},
        },
    )
    store.append(
        "owntracks",
        {
            "received_at": "2026-06-10T02:00:00+00:00",
            "user": "j",
            "device": "d",
            "payload": {"_type": "location", "tst": 200},
        },
    )
    Archiver(settings).run(today="2026-06-10")  # 06-09 → parquet; 06-10 stays raw


def test_union_of_archive_and_tail(settings):
    _seed(settings)
    q = EventQuery(settings)
    df = q.events("owntracks").pl()
    assert df.height == 2
    assert sorted(df["source"].to_list()) == ["archive", "raw"]


def test_payload_is_queryable_json(settings):
    _seed(settings)
    q = EventQuery(settings)
    rel = q.events("owntracks")
    tsts = q.sql("SELECT CAST(payload->>'$.tst' AS INT) t FROM rel ORDER BY t", rel=rel).fetchall()
    assert [r[0] for r in tsts] == [100, 200]


def test_empty_system_returns_empty_relation(settings):
    q = EventQuery(settings)
    assert q.events("owntracks").pl().height == 0


def test_archive_only(settings):
    store = RawStore(settings.data_dir)
    store.append(
        "owntracks",
        {
            "received_at": "2026-06-09T01:00:00+00:00",
            "user": "j",
            "device": "d",
            "payload": {"tst": 1},
        },
    )
    Archiver(settings).run(today="2026-06-10")
    df = EventQuery(settings).events("owntracks").pl()
    assert df.height == 1
    assert df["source"].to_list() == ["archive"]


def test_raw_only(settings):
    store = RawStore(settings.data_dir)
    store.append(
        "owntracks",
        {
            "received_at": "2026-06-10T01:00:00+00:00",
            "user": "j",
            "device": "d",
            "payload": {"tst": 1},
        },
    )
    df = EventQuery(settings).events("owntracks").pl()
    assert df.height == 1
    assert df["source"].to_list() == ["raw"]


def test_sql_names_do_not_leak_between_calls(settings):
    _seed(settings)
    q = EventQuery(settings)
    rel = q.events("owntracks")
    q.sql("SELECT count(*) FROM rel", rel=rel).fetchall()
    import pytest as _pytest

    with _pytest.raises(Exception):
        q.sql("SELECT count(*) FROM rel").fetchall()
