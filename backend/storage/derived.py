"""Derived DuckDB store — trips, segments, points, rejections.

Fully disposable: rebuild truncates and replays the archive. Single-writer
(the app process). Timestamps stored as epoch DOUBLE; read methods convert
to ISO-8601 UTC strings for API consumers.
"""

from datetime import UTC, datetime
from pathlib import Path

import duckdb

from backend.config import Settings
from backend.engine.types import PointRejected, TripClosed

_DDL = """
CREATE TABLE IF NOT EXISTS trips (
    trip_id VARCHAR PRIMARY KEY, start_ts DOUBLE, end_ts DOUBLE,
    duration_s DOUBLE, distance_m DOUBLE, point_count INTEGER,
    start_geofence VARCHAR, end_geofence VARCHAR, direction VARCHAR
);
CREATE TABLE IF NOT EXISTS segments (
    trip_id VARCHAR, seg_index INTEGER, mode VARCHAR, start_ts DOUBLE,
    end_ts DOUBLE, duration_s DOUBLE, distance_m DOUBLE, point_count INTEGER
);
CREATE TABLE IF NOT EXISTS trip_points (
    trip_id VARCHAR, ts DOUBLE, lat DOUBLE, lon DOUBLE, accuracy_m DOUBLE,
    speed_mps DOUBLE, heading_deg DOUBLE, distance_m DOUBLE, geofence VARCHAR
);
CREATE TABLE IF NOT EXISTS rejected_points (
    ts DOUBLE, lat DOUBLE, lon DOUBLE, reason VARCHAR
);
CREATE TABLE IF NOT EXISTS gtfs_feeds (
    source VARCHAR, fetched_at VARCHAR
);
CREATE TABLE IF NOT EXISTS gtfs_stops (
    source VARCHAR, stop_id VARCHAR, stop_name VARCHAR, stop_lat DOUBLE, stop_lon DOUBLE
);
CREATE TABLE IF NOT EXISTS gtfs_routes (
    source VARCHAR, route_id VARCHAR, route_name VARCHAR, route_type INTEGER
);
CREATE TABLE IF NOT EXISTS gtfs_trips (
    source VARCHAR, trip_id VARCHAR, route_id VARCHAR, service_id VARCHAR,
    headsign VARCHAR
);
CREATE TABLE IF NOT EXISTS gtfs_stop_times (
    source VARCHAR, trip_id VARCHAR, stop_id VARCHAR, stop_sequence INTEGER,
    arrival_s INTEGER, departure_s INTEGER
);
CREATE TABLE IF NOT EXISTS gtfs_calendar (
    source VARCHAR, service_id VARCHAR, monday INTEGER, tuesday INTEGER,
    wednesday INTEGER, thursday INTEGER, friday INTEGER, saturday INTEGER,
    sunday INTEGER, start_date VARCHAR, end_date VARCHAR
);
CREATE TABLE IF NOT EXISTS gtfs_calendar_dates (
    source VARCHAR, service_id VARCHAR, date VARCHAR, exception_type INTEGER
);
CREATE TABLE IF NOT EXISTS train_matches (
    trip_id VARCHAR, seg_index INTEGER, source VARCHAR, gtfs_trip_id VARCHAR,
    route_name VARCHAR, headsign VARCHAR, board_stop VARCHAR, alight_stop VARCHAR,
    scheduled_dep_s INTEGER, delta_s DOUBLE
);
"""


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


class DerivedStore:
    def __init__(self, settings: Settings, filename: str = "derived.duckdb"):
        path = Path(settings.data_dir) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(_DDL)

    def write_trip_closed(self, ev: TripClosed) -> None:
        t = ev.trip
        self._con.execute("BEGIN")
        try:
            for table in ("trips", "segments", "trip_points", "train_matches"):
                self._con.execute(f"DELETE FROM {table} WHERE trip_id = ?", [t.trip_id])
            self._con.execute(
                "INSERT INTO trips VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    t.trip_id,
                    t.start_ts,
                    t.end_ts,
                    t.duration_s,
                    t.distance_m,
                    t.point_count,
                    t.start_geofence,
                    t.end_geofence,
                    t.direction,
                ],
            )
            if ev.segments:
                self._con.executemany(
                    "INSERT INTO segments VALUES (?,?,?,?,?,?,?,?)",
                    [
                        [
                            s.trip_id,
                            s.seg_index,
                            s.mode,
                            s.start_ts,
                            s.end_ts,
                            s.duration_s,
                            s.distance_m,
                            s.point_count,
                        ]
                        for s in ev.segments
                    ],
                )
            if ev.points:
                self._con.executemany(
                    "INSERT INTO trip_points VALUES (?,?,?,?,?,?,?,?,?)",
                    [
                        [
                            t.trip_id,
                            p.ts,
                            p.lat,
                            p.lon,
                            p.accuracy_m,
                            p.speed_mps,
                            p.heading_deg,
                            p.distance_m,
                            p.geofence,
                        ]
                        for p in ev.points
                    ],
                )
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise

    def write_rejected(self, ev: PointRejected) -> None:
        self._con.execute(
            "INSERT INTO rejected_points VALUES (?,?,?,?)",
            [ev.point.ts, ev.point.lat, ev.point.lon, ev.reason],
        )

    def write_train_matches(self, matches: list) -> None:
        if not matches:
            return
        self._con.executemany(
            "INSERT INTO train_matches VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                [
                    m.trip_id,
                    m.seg_index,
                    m.source,
                    m.gtfs_trip_id,
                    m.route_name,
                    m.headsign,
                    m.board_stop,
                    m.alight_stop,
                    m.scheduled_dep_s,
                    m.delta_s,
                ]
                for m in matches
            ],
        )

    def matches_for_trip(self, trip_id: str) -> list[dict]:
        rows = self._con.execute(
            "SELECT seg_index, source, gtfs_trip_id, route_name, headsign, "
            "board_stop, alight_stop, scheduled_dep_s, delta_s "
            "FROM train_matches WHERE trip_id = ? ORDER BY seg_index",
            [trip_id],
        ).fetchall()
        return [
            {
                "seg_index": r[0],
                "source": r[1],
                "gtfs_trip_id": r[2],
                "route_name": r[3],
                "headsign": r[4],
                "board_stop": r[5],
                "alight_stop": r[6],
                "scheduled_dep_s": r[7],
                "delta_s": r[8],
            }
            for r in rows
        ]

    def rejected_count(self) -> int:
        return self._con.execute("SELECT count(*) FROM rejected_points").fetchone()[0]

    @property
    def con(self) -> duckdb.DuckDBPyConnection:
        """Shared connection for schedule/match modules (single-process app)."""
        return self._con

    def truncate(self) -> None:
        for table in (
            "trips",
            "segments",
            "trip_points",
            "rejected_points",
            "gtfs_feeds",
            "gtfs_stops",
            "gtfs_routes",
            "gtfs_trips",
            "gtfs_stop_times",
            "gtfs_calendar",
            "gtfs_calendar_dates",
            "train_matches",
        ):
            self._con.execute(f"DELETE FROM {table}")

    def list_trips(self, limit: int = 50) -> list[dict]:
        rows = self._con.execute(
            "SELECT trip_id, start_ts, end_ts, duration_s, distance_m, point_count, "
            "start_geofence, end_geofence, direction "
            "FROM trips ORDER BY start_ts DESC LIMIT ?",
            [limit],
        ).fetchall()
        return [self._trip_row_to_dict(r) for r in rows]

    def get_trip(self, trip_id: str) -> dict | None:
        row = self._con.execute(
            "SELECT trip_id, start_ts, end_ts, duration_s, distance_m, point_count, "
            "start_geofence, end_geofence, direction FROM trips WHERE trip_id = ?",
            [trip_id],
        ).fetchone()
        if row is None:
            return None
        segments = [
            {
                "seg_index": s[0],
                "mode": s[1],
                "start_ts": _iso(s[2]),
                "end_ts": _iso(s[3]),
                "duration_s": s[4],
                "distance_m": s[5],
                "point_count": s[6],
            }
            for s in self._con.execute(
                "SELECT seg_index, mode, start_ts, end_ts, duration_s, distance_m, "
                "point_count FROM segments WHERE trip_id = ? ORDER BY seg_index",
                [trip_id],
            ).fetchall()
        ]
        points = [
            {
                "ts": _iso(p[0]),
                "lat": p[1],
                "lon": p[2],
                "accuracy_m": p[3],
                "speed_mps": p[4],
                "heading_deg": p[5],
                "distance_m": p[6],
                "geofence": p[7],
            }
            for p in self._con.execute(
                "SELECT ts, lat, lon, accuracy_m, speed_mps, heading_deg, distance_m, "
                "geofence FROM trip_points WHERE trip_id = ? ORDER BY ts",
                [trip_id],
            ).fetchall()
        ]
        return {"trip": self._trip_row_to_dict(row), "segments": segments, "points": points}

    @staticmethod
    def _trip_row_to_dict(r) -> dict:
        return {
            "trip_id": r[0],
            "start_ts": _iso(r[1]),
            "end_ts": _iso(r[2]),
            "duration_s": r[3],
            "distance_m": r[4],
            "point_count": r[5],
            "start_geofence": r[6],
            "end_geofence": r[7],
            "direction": r[8],
        }

    def close(self) -> None:
        self._con.close()
