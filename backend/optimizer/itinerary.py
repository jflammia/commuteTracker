"""Enumerate scheduled single-ride itineraries that satisfy an arrival goal.

Phase 5 scope: single-ride origin→destination on one GTFS source. Multi-ride
(Newark transfer) composition reuses leg models per ride but the candidate
enumerator here handles the direct case; transfer enumeration is a Phase-6
extension noted in the optimizer view. The board/alight stops are configured
(home station, work terminal) — see OptimizerParams / settings.
"""

from dataclasses import dataclass

import duckdb

from backend.transit.gtfs import active_service_ids


@dataclass(frozen=True)
class Itinerary:
    source: str
    gtfs_trip_id: str
    route_name: str
    headsign: str
    board_stop: str
    alight_stop: str
    scheduled_dep_s: int  # seconds since local midnight (service date)
    scheduled_arr_s: int


def candidate_itineraries(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    board_stop: str,
    alight_stop: str,
    service_date: str,
    arrive_by_local_s: int,
    egress_pad_s: float,
) -> list[Itinerary]:
    """Trips serving board→alight in order on the active service day whose
    scheduled arrival + egress_pad is at or before the goal. Latest-departure
    first."""
    active = active_service_ids(con, source, service_date)
    if not active:
        return []
    rows = con.execute(
        "SELECT t.trip_id, t.service_id, t.headsign, r.route_name, "
        "       s1.stop_name, s2.stop_name, st1.departure_s, st2.arrival_s "
        "FROM gtfs_stop_times st1 "
        "JOIN gtfs_stop_times st2 ON st1.source = st2.source AND st1.trip_id = st2.trip_id "
        "JOIN gtfs_trips t ON t.source = st1.source AND t.trip_id = st1.trip_id "
        "JOIN gtfs_routes r ON r.source = t.source AND r.route_id = t.route_id "
        "JOIN gtfs_stops s1 ON s1.source = st1.source AND s1.stop_id = st1.stop_id "
        "JOIN gtfs_stops s2 ON s2.source = st2.source AND s2.stop_id = st2.stop_id "
        "WHERE st1.source = ? AND st1.stop_id = ? AND st2.stop_id = ? "
        "  AND st1.stop_sequence < st2.stop_sequence "
        "ORDER BY st1.departure_s DESC",
        [source, board_stop, alight_stop],
    ).fetchall()
    out = []
    for trip_id, service_id, headsign, route_name, b_name, a_name, dep_s, arr_s in rows:
        if service_id not in active:
            continue
        if arr_s + egress_pad_s > arrive_by_local_s:
            continue
        out.append(
            Itinerary(
                source=source,
                gtfs_trip_id=trip_id,
                route_name=route_name,
                headsign=headsign or "",
                board_stop=b_name,
                alight_stop=a_name,
                scheduled_dep_s=dep_s,
                scheduled_arr_s=arr_s,
            )
        )
    return out
