"""Derived DuckDB store — trips, segments, points, rejections.

Fully disposable: rebuild truncates and replays the archive. Single-writer
(the app process). Timestamps stored as epoch DOUBLE; read methods convert
to ISO-8601 UTC strings for API consumers.
"""

import json
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
CREATE TABLE IF NOT EXISTS label_overrides (
    trip_id VARCHAR, seg_index INTEGER, kind VARCHAR, value VARCHAR,
    labeled_at VARCHAR
);
CREATE TABLE IF NOT EXISTS leg_observations (
    trip_id VARCHAR, direction VARCHAR, leg_index INTEGER, kind VARCHAR,
    duration_s DOUBLE, distance_m DOUBLE, gtfs_trip_id VARCHAR, source VARCHAR,
    route_name VARCHAR, scheduled_dep_s INTEGER, delta_s DOUBLE,
    board_stop VARCHAR, alight_stop VARCHAR
);
CREATE TABLE IF NOT EXISTS recommendations (
    service_date VARCHAR, direction VARCHAR, payload VARCHAR
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
            for table in ("trips", "segments", "trip_points", "train_matches", "leg_observations"):
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
        self._con.execute("DELETE FROM rejected_points WHERE ts = ?", [ev.point.ts])
        self._con.execute(
            "INSERT INTO rejected_points VALUES (?,?,?,?)",
            [ev.point.ts, ev.point.lat, ev.point.lon, ev.reason],
        )

    def write_train_matches(self, matches: list) -> None:
        if not matches:
            return
        for tid in {m.trip_id for m in matches}:
            self._con.execute("DELETE FROM train_matches WHERE trip_id = ?", [tid])
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

    _LABEL_KINDS = ("segment_mode", "train_match", "trip_flag", "trip_reviewed")

    def apply_label(self, payload: dict) -> bool:
        """Upsert one label override. Returns False when the trip is unknown
        (e.g. engine params changed and the trip no longer exists) — the
        primitive label event is still archived; only the application is
        skipped."""
        kind = payload.get("type")
        trip_id = payload.get("trip_id")
        seg_index = payload.get("seg_index")
        if kind not in self._LABEL_KINDS:
            return False
        exists = self._con.execute("SELECT 1 FROM trips WHERE trip_id = ?", [trip_id]).fetchone()
        if exists is None:
            return False
        self._con.execute(
            "DELETE FROM label_overrides WHERE trip_id = ? AND kind = ? "
            "AND seg_index IS NOT DISTINCT FROM ?",
            [trip_id, kind, seg_index],
        )
        self._con.execute(
            "INSERT INTO label_overrides VALUES (?,?,?,?,?)",
            [
                trip_id,
                seg_index,
                kind,
                json.dumps(payload.get("value")),
                datetime.now(UTC).isoformat(),
            ],
        )
        return True

    def _labels_for_trip(self, trip_id: str) -> list[tuple]:
        return self._con.execute(
            "SELECT seg_index, kind, value FROM label_overrides WHERE trip_id = ?",
            [trip_id],
        ).fetchall()

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
            "label_overrides",
            "leg_observations",
            "recommendations",
        ):
            self._con.execute(f"DELETE FROM {table}")

    def list_trips(self, limit: int = 50, reviewed: bool | None = None) -> list[dict]:
        sql = (
            "SELECT t.trip_id, t.start_ts, t.end_ts, t.duration_s, t.distance_m, "
            "t.point_count, t.start_geofence, t.end_geofence, t.direction, "
            "COALESCE((SELECT value FROM label_overrides lo WHERE lo.trip_id = t.trip_id "
            "          AND lo.kind = 'trip_reviewed'), 'false') AS reviewed_raw "
            "FROM trips t "
        )
        params: list = []
        if reviewed is not None:
            sql += "WHERE COALESCE((SELECT value FROM label_overrides lo "
            sql += "WHERE lo.trip_id = t.trip_id AND lo.kind = 'trip_reviewed'), 'false') = ? "
            params.append("true" if reviewed else "false")
        sql += "ORDER BY t.start_ts DESC LIMIT ?"
        params.append(limit)
        rows = self._con.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = self._trip_row_to_dict(r[:9])
            d["reviewed"] = r[9] == "true"
            out.append(d)
        return out

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
        seg_mode_overrides = {}
        train_confirmations = {}
        trip_flag = None
        trip_reviewed = False
        for seg_index, kind, value in self._labels_for_trip(trip_id):
            v = json.loads(value)
            if kind == "segment_mode":
                seg_mode_overrides[seg_index] = v
            elif kind == "train_match":
                train_confirmations[seg_index] = v
            elif kind == "trip_flag":
                trip_flag = v
            elif kind == "trip_reviewed":
                trip_reviewed = bool(v)
        for s in segments:
            override = seg_mode_overrides.get(s["seg_index"])
            s["mode_effective"] = override if override is not None else s["mode"]
            s["mode_source"] = "label" if override is not None else "heuristic"
        match_by_seg = {m["seg_index"]: m for m in self.matches_for_trip(trip_id)}
        itinerary = [
            {
                "mode": s["mode_effective"],
                "start_ts": s["start_ts"],
                "end_ts": s["end_ts"],
                "duration_s": s["duration_s"],
                "distance_m": s["distance_m"],
                "train": match_by_seg.get(s["seg_index"]),
                "confirmation": train_confirmations.get(s["seg_index"]),
            }
            for s in segments
        ]
        trip_dict = self._trip_row_to_dict(row)
        trip_dict["flag"] = trip_flag
        trip_dict["reviewed"] = trip_reviewed
        return {"trip": trip_dict, "segments": segments, "points": points, "itinerary": itinerary}

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

    def write_leg_observations(self, trip_id: str, legs: list) -> None:
        self._con.execute("DELETE FROM leg_observations WHERE trip_id = ?", [trip_id])
        if not legs:
            return
        self._con.executemany(
            "INSERT INTO leg_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                [
                    lg.trip_id,
                    lg.direction,
                    lg.leg_index,
                    lg.kind,
                    lg.duration_s,
                    lg.distance_m,
                    lg.gtfs_trip_id,
                    lg.source,
                    lg.route_name,
                    lg.scheduled_dep_s,
                    lg.delta_s,
                    lg.board_stop,
                    lg.alight_stop,
                ]
                for lg in legs
            ],
        )

    def leg_observations(self) -> list[dict]:
        cols = [
            "trip_id",
            "direction",
            "leg_index",
            "kind",
            "duration_s",
            "distance_m",
            "gtfs_trip_id",
            "source",
            "route_name",
            "scheduled_dep_s",
            "delta_s",
            "board_stop",
            "alight_stop",
        ]
        rows = self._con.execute(f"SELECT {', '.join(cols)} FROM leg_observations").fetchall()
        return [dict(zip(cols, r)) for r in rows]

    def write_recommendation(self, service_date: str, direction: str, payload: dict) -> None:
        self._con.execute(
            "DELETE FROM recommendations WHERE service_date = ? AND direction = ?",
            [service_date, direction],
        )
        self._con.execute(
            "INSERT INTO recommendations VALUES (?,?,?)",
            [service_date, direction, json.dumps(payload)],
        )

    def recommendation(self, service_date: str, direction: str) -> dict | None:
        row = self._con.execute(
            "SELECT payload FROM recommendations WHERE service_date = ? AND direction = ?",
            [service_date, direction],
        ).fetchone()
        return json.loads(row[0]) if row else None

    def close(self) -> None:
        self._con.close()
