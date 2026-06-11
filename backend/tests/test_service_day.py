import base64

from backend.storage.derived import DerivedStore
from backend.storage.raw import RawStore
from backend.tests.gtfs_fixture import build_gtfs_zip
from backend.transit.gtfs import active_service_ids, latest_snapshot, parse_gtfs

STOPS = [("S1", "Alpha", 40.70, -74.40), ("S2", "Beta", 40.87, -74.40)]


def _load(settings, trips, calendar_dates_csv=None):
    z = build_gtfs_zip(STOPS, trips)
    if calendar_dates_csv is not None:
        # rebuild the zip with a calendar_dates.txt injected
        import io
        import zipfile

        src = zipfile.ZipFile(io.BytesIO(z))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as out:
            for name in src.namelist():
                out.writestr(name, src.read(name))
            out.writestr("calendar_dates.txt", calendar_dates_csv)
        z = buf.getvalue()
    RawStore(settings.data_dir).append(
        "gtfs_path",
        {
            "received_at": "2026-06-09T05:00:00+00:00",
            "payload": {"url": "u", "status": 200, "b64": base64.b64encode(z).decode()},
        },
    )
    store = DerivedStore(settings)
    parse_gtfs(store.con, "gtfs_path", latest_snapshot(settings, "gtfs_path"), fetched_at="x")
    return store


def test_weekday_service_active(settings):
    store = _load(settings, [("T1", "WK", "In", [("S1", "10:00:00"), ("S2", "10:24:00")])])
    # 2026-06-10 is a Wednesday; fixture calendar runs all days of 2026
    assert active_service_ids(store.con, "gtfs_path", "20260610") == {"WK"}


def test_service_outside_date_range_inactive(settings):
    store = _load(settings, [("T1", "WK", "In", [("S1", "10:00:00"), ("S2", "10:24:00")])])
    assert active_service_ids(store.con, "gtfs_path", "20270101") == set()


def test_calendar_dates_exceptions(settings):
    cal_dates = (
        "service_id,date,exception_type\n"
        "WK,20260610,2\n"  # removed on the 10th
        "HOLIDAY,20260610,1\n"  # added on the 10th
    )
    store = _load(
        settings,
        [
            ("T1", "WK", "In", [("S1", "10:00:00"), ("S2", "10:24:00")]),
            ("T2", "HOLIDAY", "In", [("S1", "11:00:00"), ("S2", "11:24:00")]),
        ],
        calendar_dates_csv=cal_dates,
    )
    assert active_service_ids(store.con, "gtfs_path", "20260610") == {"HOLIDAY"}
