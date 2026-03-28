"""Derived data store: read processed Parquet files via DuckDB.

Provides SQL access to the processed commute data stored as Parquet files.
"""

import logging
from pathlib import Path

import duckdb
import polars as pl

from src.config import DERIVED_DATA_DIR

logger = logging.getLogger(__name__)


class DerivedStore:
    def __init__(self, derived_dir: str | Path | None = None):
        self.derived_dir = Path(derived_dir or DERIVED_DATA_DIR)
        self._conn = duckdb.connect()

    def _parquet_glob(self) -> str:
        return str(self.derived_dir / "**" / "*.parquet")

    def _has_parquet_files(self) -> bool:
        """Check if any Parquet files exist in the derived directory."""
        return any(self.derived_dir.rglob("*.parquet"))

    def query(self, sql: str, params: list | None = None) -> pl.DataFrame:
        """Run a SQL query over all derived Parquet files.

        Use 'commute_data' as the table name in your query.
        Use $1, $2, etc. for parameterized values passed via `params`.
        Returns an empty DataFrame if no Parquet files exist.
        """
        if not self._has_parquet_files():
            return pl.DataFrame()
        glob = self._parquet_glob()
        full_sql = (
            f"WITH commute_data AS (SELECT * FROM read_parquet('{glob}', union_by_name=true)) {sql}"
        )
        if params:
            return self._conn.execute(full_sql, params).pl()
        return self._conn.execute(full_sql).pl()

    def get_commutes(self) -> pl.DataFrame:
        """Get summary of all detected commutes."""
        return self.query("""
            SELECT
                commute_id,
                commute_direction,
                MIN(timestamp) as start_time,
                MAX(timestamp) as end_time,
                COUNT(*) as point_count,
                SUM(distance_m) as total_distance_m,
                ROUND(EXTRACT(EPOCH FROM (MAX(timestamp) - MIN(timestamp))) / 60, 1) as duration_min
            FROM commute_data
            WHERE commute_id IS NOT NULL
            GROUP BY commute_id, commute_direction
            ORDER BY start_time
        """)

    def get_segments(self, commute_id: str) -> pl.DataFrame:
        """Get segment breakdown for a specific commute."""
        return self.query(
            """
            SELECT
                segment_id,
                transport_mode,
                MIN(timestamp) as start_time,
                MAX(timestamp) as end_time,
                COUNT(*) as point_count,
                ROUND(SUM(distance_m), 0) as distance_m,
                ROUND(EXTRACT(EPOCH FROM (MAX(timestamp) - MIN(timestamp))) / 60, 1) as duration_min,
                ROUND(AVG(speed_kmh), 1) as avg_speed_kmh,
                ROUND(MAX(speed_kmh), 1) as max_speed_kmh
            FROM commute_data
            WHERE commute_id = $1
            GROUP BY segment_id, transport_mode
            ORDER BY segment_id
            """,
            [commute_id],
        )

    def get_daily_summary(self, date: str) -> pl.DataFrame:
        """Get all points for a given local date (YYYY-MM-DD)."""
        return self.query(
            """
            SELECT *
            FROM commute_data
            WHERE CAST(timestamp_local AS DATE) = CAST($1 AS DATE)
            ORDER BY timestamp
            """,
            [date],
        )

    def get_all_segments(self, direction: str | None = None) -> pl.DataFrame:
        """Get segment breakdown for all commutes, optionally filtered by direction."""
        if direction:
            return self.query(
                """
                SELECT
                    commute_id,
                    segment_id,
                    transport_mode,
                    MIN(timestamp) as start_time,
                    MAX(timestamp) as end_time,
                    COUNT(*) as point_count,
                    ROUND(SUM(distance_m), 0) as distance_m,
                    ROUND(EXTRACT(EPOCH FROM (MAX(timestamp) - MIN(timestamp))) / 60, 1)
                        as duration_min,
                    ROUND(AVG(speed_kmh), 1) as avg_speed_kmh,
                    ROUND(MAX(speed_kmh), 1) as max_speed_kmh
                FROM commute_data
                WHERE commute_id IS NOT NULL AND commute_direction = $1
                GROUP BY commute_id, segment_id, transport_mode
                ORDER BY commute_id, segment_id
                """,
                [direction],
            )
        return self.query("""
            SELECT
                commute_id,
                segment_id,
                transport_mode,
                MIN(timestamp) as start_time,
                MAX(timestamp) as end_time,
                COUNT(*) as point_count,
                ROUND(SUM(distance_m), 0) as distance_m,
                ROUND(EXTRACT(EPOCH FROM (MAX(timestamp) - MIN(timestamp))) / 60, 1)
                    as duration_min,
                ROUND(AVG(speed_kmh), 1) as avg_speed_kmh,
                ROUND(MAX(speed_kmh), 1) as max_speed_kmh
            FROM commute_data
            WHERE commute_id IS NOT NULL
            GROUP BY commute_id, segment_id, transport_mode
            ORDER BY commute_id, segment_id
        """)

    def get_commute_stats(self) -> pl.DataFrame:
        """Get aggregate statistics across all commutes."""
        return self.query("""
            SELECT
                commute_direction,
                COUNT(DISTINCT commute_id) as num_commutes,
                ROUND(AVG(duration_min), 1) as avg_duration_min,
                ROUND(MIN(duration_min), 1) as min_duration_min,
                ROUND(MAX(duration_min), 1) as max_duration_min,
                ROUND(STDDEV(duration_min), 1) as stddev_duration_min
            FROM (
                SELECT
                    commute_id,
                    commute_direction,
                    EXTRACT(EPOCH FROM (MAX(timestamp) - MIN(timestamp))) / 60 as duration_min
                FROM commute_data
                WHERE commute_id IS NOT NULL
                GROUP BY commute_id, commute_direction
            )
            GROUP BY commute_direction
        """)

    def list_dates(self) -> list[str]:
        """List all dates that have derived data."""
        parquet_files = sorted(self.derived_dir.rglob("*.parquet"))
        return [f.stem for f in parquet_files]

    def get_commute_points(self, commute_id: str) -> pl.DataFrame:
        """Get all points for a specific commute, ordered by timestamp."""
        return self.query(
            """
            SELECT *
            FROM commute_data
            WHERE commute_id = $1
            ORDER BY timestamp
            """,
            [commute_id],
        )
