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


def parse_gtfs(
    con: duckdb.DuckDBPyConnection, source: str, zip_bytes: bytes, *, fetched_at: str
) -> dict:
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    routes = [
        r for r in _rows(zf, "routes.txt") if int(r.get("route_type", -1)) in RAIL_ROUTE_TYPES
    ]
    rail_route_ids = {r["route_id"] for r in routes}
    trips = [t for t in _rows(zf, "trips.txt") if t["route_id"] in rail_route_ids]
    rail_trip_ids = {t["trip_id"] for t in trips}
    stop_times = [st for st in _rows(zf, "stop_times.txt") if st["trip_id"] in rail_trip_ids]
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
