"""Match vehicle segments to scheduled GTFS trips.

Deterministic given (trip points, schedule tables): nearest rail stop to the
segment endpoints (within STOP_RADIUS_M), candidate scheduled trips serving
board→alight in order on the active service day, departure within
DEP_TOLERANCE_S of the observed segment start; best = smallest |delta|.

GTFS times are agency-local — segment epochs convert via America/New_York.
For local times before 04:00 the previous service date is also tried with
local_s + 86400 (late-night trains are listed as 24:xx/25:xx on the prior
service day).

Sign convention: delta_s = observed boarding (local s) − scheduled departure;
positive = boarded after the scheduled time.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import duckdb

from backend.engine.geo import haversine_m
from backend.engine.types import TripClosed
from backend.transit.gtfs import active_service_ids

STOP_RADIUS_M = 500.0
DEP_TOLERANCE_S = 900.0
_NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class TrainMatch:
    trip_id: str
    seg_index: int
    source: str
    gtfs_trip_id: str
    route_name: str
    headsign: str
    board_stop: str
    alight_stop: str
    scheduled_dep_s: int  # seconds since local midnight of the service date
    delta_s: float  # observed start - scheduled departure (positive = late board)


def _nearest_stop(stops: list[tuple], lat: float, lon: float):
    """stops rows: (source, stop_id, stop_name, stop_lat, stop_lon)."""
    best, best_d = None, STOP_RADIUS_M
    for row in stops:
        d = haversine_m(lat, lon, row[3], row[4])
        if d <= best_d:
            best, best_d = row, d
    return best


def _service_day_candidates(ts: float) -> list[tuple[str, int]]:
    """(service_date YYYYMMDD, local seconds) pairs to try for an epoch."""
    dt = datetime.fromtimestamp(ts, _NY)
    local_s = dt.hour * 3600 + dt.minute * 60 + dt.second
    out = [(dt.strftime("%Y%m%d"), local_s)]
    if local_s < 4 * 3600:
        prev = (dt - timedelta(days=1)).strftime("%Y%m%d")
        out.append((prev, local_s + 86400))
    return out


def match_trip(con: duckdb.DuckDBPyConnection, closed: TripClosed) -> list["TrainMatch"]:
    stops = con.execute(
        "SELECT source, stop_id, stop_name, stop_lat, stop_lon FROM gtfs_stops"
    ).fetchall()
    if not stops:
        return []
    matches = []
    for seg in closed.segments:
        if seg.mode != "vehicle":
            continue
        start = next(p for p in closed.points if p.ts >= seg.start_ts)
        end = max((p for p in closed.points if p.ts <= seg.end_ts), key=lambda p: p.ts)
        board = _nearest_stop(stops, start.lat, start.lon)
        alight = _nearest_stop(stops, end.lat, end.lon)
        if board is None or alight is None or board[1] == alight[1]:
            continue
        if board[0] != alight[0]:
            continue  # endpoints resolved to different agencies' stops
        source = board[0]
        best = None
        for service_date, local_s in _service_day_candidates(start.ts):
            active = active_service_ids(con, source, service_date)
            if not active:
                continue
            rows = con.execute(
                "SELECT t.trip_id, t.service_id, t.headsign, r.route_name, "
                "       st1.departure_s "
                "FROM gtfs_stop_times st1 "
                "JOIN gtfs_stop_times st2 ON st1.source = st2.source "
                "  AND st1.trip_id = st2.trip_id "
                "JOIN gtfs_trips t ON t.source = st1.source AND t.trip_id = st1.trip_id "
                "JOIN gtfs_routes r ON r.source = t.source AND r.route_id = t.route_id "
                "WHERE st1.source = ? AND st1.stop_id = ? AND st2.stop_id = ? "
                "  AND st1.stop_sequence < st2.stop_sequence",
                [source, board[1], alight[1]],
            ).fetchall()
            for gtfs_trip_id, service_id, headsign, route_name, dep_s in rows:
                if service_id not in active:
                    continue
                delta = local_s - dep_s
                if abs(delta) > DEP_TOLERANCE_S:
                    continue
                if best is None or abs(delta) < abs(best.delta_s):
                    best = TrainMatch(
                        trip_id=closed.trip.trip_id,
                        seg_index=seg.seg_index,
                        source=source,
                        gtfs_trip_id=gtfs_trip_id,
                        route_name=route_name,
                        headsign=headsign,
                        board_stop=board[2],
                        alight_stop=alight[2],
                        scheduled_dep_s=dep_s,
                        delta_s=float(delta),
                    )
        if best is not None:
            matches.append(best)
    return matches
