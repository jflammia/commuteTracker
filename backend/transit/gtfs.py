"""GTFS static parsing: archived snapshot zip → schedule tables.

Schedule tables are derived data — re-parsed from the archived snapshot on
every rebuild, replaced per-source on every parse.
"""

import base64
import csv
import io
import json
import logging
import zipfile

import duckdb

from backend.config import Settings
from backend.storage.query import EventQuery

log = logging.getLogger(__name__)

RAIL_ROUTE_TYPES = (1, 2)  # 1 = subway/PATH, 2 = rail/NJT


def hms_to_seconds(hms: str) -> int:
    h, m, s = hms.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def latest_snapshot(settings: Settings, source: str) -> bytes | None:
    """Newest archived fetch event that carries a body (status 200 + b64)."""
    q = EventQuery(settings)
    rel = q.events(source)
    rows = q.sql(
        "SELECT CAST(payload AS VARCHAR) FROM rel ORDER BY received_at DESC", rel=rel
    ).fetchall()
    for (payload_text,) in rows:
        p = json.loads(payload_text)
        if p.get("status") == 200 and "b64" in p:
            return base64.b64decode(p["b64"])
    return None


def _rows(zf: zipfile.ZipFile, name: str) -> list[dict]:
    if name not in zf.namelist():
        return []
    with zf.open(name) as f:
        return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))


def _filter_rows(zf: zipfile.ZipFile, name: str, keep) -> list[dict]:
    """Stream-filter a GTFS csv: keeps memory at kept-rows size, not file size."""
    if name not in zf.namelist():
        return []
    with zf.open(name) as f:
        return [r for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")) if keep(r)]


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_timed(value) -> bool:
    return bool(value) and value.count(":") == 2


def parse_gtfs(
    con: duckdb.DuckDBPyConnection, source: str, zip_bytes: bytes, *, fetched_at: str
) -> dict:
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    routes = [
        r for r in _rows(zf, "routes.txt") if _int_or_none(r.get("route_type")) in RAIL_ROUTE_TYPES
    ]
    rail_route_ids = {r["route_id"] for r in routes}
    trips = _filter_rows(zf, "trips.txt", lambda t: t.get("route_id") in rail_route_ids)
    rail_trip_ids = {t["trip_id"] for t in trips}
    stop_times = _filter_rows(
        zf,
        "stop_times.txt",
        lambda st: (
            st.get("trip_id") in rail_trip_ids
            and _is_timed(st.get("arrival_time"))
            and _is_timed(st.get("departure_time"))
        ),
    )
    used_stop_ids = {st["stop_id"] for st in stop_times}
    stops = [s for s in _rows(zf, "stops.txt") if s["stop_id"] in used_stop_ids]
    calendar = _rows(zf, "calendar.txt")
    calendar_dates = _rows(zf, "calendar_dates.txt")

    tables = (
        "gtfs_feeds",
        "gtfs_stops",
        "gtfs_routes",
        "gtfs_trips",
        "gtfs_stop_times",
        "gtfs_calendar",
        "gtfs_calendar_dates",
    )
    con.execute("BEGIN")
    try:
        for t in tables:
            con.execute(f"DELETE FROM {t} WHERE source = ?", [source])
        con.execute("INSERT INTO gtfs_feeds VALUES (?,?)", [source, fetched_at])
        if stops:
            con.executemany(
                "INSERT INTO gtfs_stops VALUES (?,?,?,?,?)",
                [
                    [
                        source,
                        s["stop_id"],
                        s.get("stop_name", ""),
                        float(s["stop_lat"]),
                        float(s["stop_lon"]),
                    ]
                    for s in stops
                ],
            )
        if routes:
            con.executemany(
                "INSERT INTO gtfs_routes VALUES (?,?,?,?)",
                [
                    [
                        source,
                        r["route_id"],
                        r.get("route_long_name") or r.get("route_short_name", ""),
                        int(r["route_type"]),
                    ]
                    for r in routes
                ],
            )
        if trips:
            con.executemany(
                "INSERT INTO gtfs_trips VALUES (?,?,?,?,?)",
                [
                    [
                        source,
                        t["trip_id"],
                        t["route_id"],
                        t["service_id"],
                        t.get("trip_headsign", ""),
                    ]
                    for t in trips
                ],
            )
        if stop_times:
            con.executemany(
                "INSERT INTO gtfs_stop_times VALUES (?,?,?,?,?,?)",
                [
                    [
                        source,
                        st["trip_id"],
                        st["stop_id"],
                        int(st["stop_sequence"]),
                        hms_to_seconds(st["arrival_time"]),
                        hms_to_seconds(st["departure_time"]),
                    ]
                    for st in stop_times
                ],
            )
        if calendar:
            con.executemany(
                "INSERT INTO gtfs_calendar VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [
                    [
                        source,
                        c["service_id"],
                        int(c["monday"]),
                        int(c["tuesday"]),
                        int(c["wednesday"]),
                        int(c["thursday"]),
                        int(c["friday"]),
                        int(c["saturday"]),
                        int(c["sunday"]),
                        c["start_date"],
                        c["end_date"],
                    ]
                    for c in calendar
                ],
            )
        if calendar_dates:
            con.executemany(
                "INSERT INTO gtfs_calendar_dates VALUES (?,?,?,?)",
                [
                    [source, c["service_id"], c["date"], int(c["exception_type"])]
                    for c in calendar_dates
                ],
            )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    counts = {
        "stops": len(stops),
        "routes": len(routes),
        "trips": len(trips),
        "stop_times": len(stop_times),
    }
    log.info("parsed %s gtfs: %s", source, counts)
    return counts


_WEEKDAY_COLS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def active_service_ids(con: duckdb.DuckDBPyConnection, source: str, service_date: str) -> set[str]:
    """Return the set of active service_ids for a GTFS-style YYYYMMDD date.

    Applies calendar.txt base rules then calendar_dates.txt exceptions
    (exception_type 1 = added, 2 = removed).

    ``weekday_col`` is interpolated from the fixed ``_WEEKDAY_COLS`` tuple —
    never from user input — so the f-string is safe.
    """
    from datetime import date

    d = date(int(service_date[:4]), int(service_date[4:6]), int(service_date[6:8]))
    weekday_col = _WEEKDAY_COLS[d.weekday()]
    base = {
        r[0]
        for r in con.execute(
            f"SELECT service_id FROM gtfs_calendar WHERE source = ? "
            f"AND {weekday_col} = 1 AND start_date <= ? AND end_date >= ?",
            [source, service_date, service_date],
        ).fetchall()
    }
    for service_id, exception_type in con.execute(
        "SELECT service_id, exception_type FROM gtfs_calendar_dates WHERE source = ? AND date = ?",
        [source, service_date],
    ).fetchall():
        if exception_type == 1:
            base.add(service_id)
        elif exception_type == 2:
            base.discard(service_id)
    return base
