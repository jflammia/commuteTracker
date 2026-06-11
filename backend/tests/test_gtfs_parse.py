import base64

from backend.storage.derived import DerivedStore
from backend.storage.raw import RawStore
from backend.tests.gtfs_fixture import build_gtfs_zip
from backend.transit.gtfs import hms_to_seconds, latest_snapshot, parse_gtfs

STOPS = [("S1", "Alpha", 40.7000, -74.4000), ("S2", "Beta", 40.8700, -74.4000)]
TRIPS = [
    ("T1", "WK", "Inbound", [("S1", "10:00:00"), ("S2", "10:24:00")]),
    ("T2", "WK", "Inbound", [("S1", "25:10:00"), ("S2", "25:34:00")]),  # after midnight
]


def test_hms_to_seconds():
    assert hms_to_seconds("10:00:00") == 36000
    assert hms_to_seconds("25:10:00") == 90600  # GTFS times exceed 24h


def _archive_snapshot(settings, source="gtfs_path"):
    z = build_gtfs_zip(STOPS, TRIPS)
    RawStore(settings.data_dir).append(
        source,
        {
            "received_at": "2026-06-09T05:00:00+00:00",
            "payload": {
                "url": "https://e.com/p.zip",
                "status": 200,
                "b64": base64.b64encode(z).decode(),
            },
        },
    )


def test_latest_snapshot_returns_bytes(settings):
    _archive_snapshot(settings)
    data = latest_snapshot(settings, "gtfs_path")
    assert data is not None and data[:2] == b"PK"  # zip magic


def test_latest_snapshot_none_when_absent(settings):
    assert latest_snapshot(settings, "gtfs_path") is None


def test_latest_snapshot_skips_unchanged_and_error_events(settings):
    _archive_snapshot(settings)
    store = RawStore(settings.data_dir)
    store.append(
        "gtfs_path",
        {
            "received_at": "2026-06-10T05:00:00+00:00",
            "payload": {"status": 200, "sha256": "x", "unchanged": True},
        },
    )
    store.append(
        "gtfs_path",
        {"received_at": "2026-06-10T06:00:00+00:00", "payload": {"status": None, "error": "down"}},
    )
    assert latest_snapshot(settings, "gtfs_path") is not None


def test_parse_gtfs_loads_schedule_tables(settings):
    _archive_snapshot(settings)
    store = DerivedStore(settings)
    counts = parse_gtfs(
        store.con,
        "gtfs_path",
        latest_snapshot(settings, "gtfs_path"),
        fetched_at="2026-06-09T05:00:00+00:00",
    )
    assert counts["stops"] == 2
    assert counts["trips"] == 2
    assert counts["stop_times"] == 4
    row = store.con.execute(
        "SELECT departure_s FROM gtfs_stop_times WHERE trip_id='T2' AND stop_id='S1' "
        "AND source='gtfs_path'"
    ).fetchone()
    assert row[0] == 90600


def test_parse_gtfs_replaces_prior_rows_for_source(settings):
    _archive_snapshot(settings)
    store = DerivedStore(settings)
    snap = latest_snapshot(settings, "gtfs_path")
    parse_gtfs(store.con, "gtfs_path", snap, fetched_at="a")
    parse_gtfs(store.con, "gtfs_path", snap, fetched_at="b")
    n = store.con.execute("SELECT count(*) FROM gtfs_stops").fetchone()[0]
    assert n == 2  # not 4


def test_parse_gtfs_tolerates_malformed_route_type(settings):
    import io
    import zipfile

    z = build_gtfs_zip(STOPS, TRIPS)
    src = zipfile.ZipFile(io.BytesIO(z))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for name in src.namelist():
            if name == "routes.txt":
                out.writestr(
                    name,
                    "route_id,agency_id,route_short_name,route_long_name,route_type\n"
                    "R1,TST,TL,Test Line,2\n"
                    "RBAD,TST,X,Broken,\n",  # empty route_type must not crash
                )
            else:
                out.writestr(name, src.read(name))
    store = DerivedStore(settings)
    counts = parse_gtfs(store.con, "gtfs_path", buf.getvalue(), fetched_at="x")
    assert counts["routes"] == 1  # bad row skipped, good row kept


def test_latest_snapshot_survives_archival(settings):
    from backend.storage.archive import Archiver

    _archive_snapshot(settings)  # writes to 2026-06-09 raw day file
    results = Archiver(settings).run(today="2026-06-10")
    assert all(r.ok for r in results)
    raw_file = settings.data_dir / "raw" / "gtfs_path" / "2026-06-09.jsonl"
    assert not raw_file.exists()  # raw deleted — snapshot now ONLY in parquet
    data = latest_snapshot(settings, "gtfs_path")
    assert data is not None and data[:2] == b"PK"
    # and it still parses
    store = DerivedStore(settings)
    counts = parse_gtfs(store.con, "gtfs_path", data, fetched_at="x")
    assert counts["stops"] == 2


def test_parse_gtfs_skips_untimed_stop_time_rows(settings):
    import io
    import zipfile

    z = build_gtfs_zip(STOPS, [("T1", "WK", "In", [("S1", "10:00:00"), ("S2", "10:24:00")])])
    src = zipfile.ZipFile(io.BytesIO(z))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for name in src.namelist():
            if name == "stop_times.txt":
                out.writestr(
                    name,
                    "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
                    "T1,10:00:00,10:00:00,S1,1\n"
                    "T1,,,SMID,2\n"  # untimed non-timepoint row: skipped, not fatal
                    "T1,10:24:00,10:24:00,S2,3\n",
                )
            else:
                out.writestr(name, src.read(name))
    store = DerivedStore(settings)
    counts = parse_gtfs(store.con, "gtfs_path", buf.getvalue(), fetched_at="x")
    assert counts["stop_times"] == 2  # the two timed rows
