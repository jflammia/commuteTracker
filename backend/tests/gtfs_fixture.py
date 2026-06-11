"""Synthetic GTFS zip builder. One agency, one rail route, N stops, M trips.

stops: list of (stop_id, name, lat, lon)
trips: list of (trip_id, service_id, headsign, [(stop_id, "HH:MM:SS"), ...])
calendar: every service_id runs all 7 days across 2026.
"""

import io
import zipfile


def build_gtfs_zip(stops, trips, route_type=2, route_name="Test Line") -> bytes:
    agency = (
        "agency_id,agency_name,agency_url,agency_timezone\n"
        "TST,Test Agency,https://example.com,America/New_York\n"
    )
    routes = (
        "route_id,agency_id,route_short_name,route_long_name,route_type\n"
        f"R1,TST,TL,{route_name},{route_type}\n"
    )
    stops_csv = "stop_id,stop_name,stop_lat,stop_lon\n" + "".join(
        f"{sid},{name},{lat},{lon}\n" for sid, name, lat, lon in stops
    )
    service_ids = sorted({t[1] for t in trips})
    calendar = (
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
        "start_date,end_date\n"
        + "".join(f"{sid},1,1,1,1,1,1,1,20260101,20261231\n" for sid in service_ids)
    )
    trips_csv = "route_id,service_id,trip_id,trip_headsign\n" + "".join(
        f"R1,{svc},{tid},{headsign}\n" for tid, svc, headsign, _ in trips
    )
    stop_times = "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
    for tid, _, _, calls in trips:
        for seq, (sid, hms) in enumerate(calls, start=1):
            stop_times += f"{tid},{hms},{hms},{sid},{seq}\n"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("agency.txt", agency)
        z.writestr("routes.txt", routes)
        z.writestr("stops.txt", stops_csv)
        z.writestr("calendar.txt", calendar)
        z.writestr("trips.txt", trips_csv)
        z.writestr("stop_times.txt", stop_times)
    return buf.getvalue()
