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
            for table in ("trips", "segments", "trip_points"):
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

    def rejected_count(self) -> int:
        return self._con.execute("SELECT count(*) FROM rejected_points").fetchone()[0]

    def truncate(self) -> None:
        for table in ("trips", "segments", "trip_points", "rejected_points"):
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
