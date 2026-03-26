"""Commute Tracker service layer.

Pure business logic, no transport concerns. Used by both the REST API and MCP server.
All methods return plain dicts/lists suitable for JSON serialization.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl

from src.config import DATABASE_URL, DERIVED_DATA_DIR
from src.storage.database import Database
from src.storage.derived_store import DerivedStore
from src.storage.label_store import LabelStore

logger = logging.getLogger(__name__)


class CommuteService:
    """Unified service for all commute tracker operations."""

    def __init__(
        self,
        db: Database | None = None,
        derived_dir: str | Path | None = None,
    ):
        self._db = db or Database(DATABASE_URL)
        self._derived_dir = Path(derived_dir or DERIVED_DATA_DIR)
        self._derived_store = DerivedStore(self._derived_dir)
        self._label_store = LabelStore(self._db)

    # ── Health & Status ───────────────────────────────────────────────────────

    def health(self) -> dict:
        """System health summary."""
        return {
            "status": "ok",
            "total_records": self._db.count_records(),
            "unsynced_records": self._db.count_unsynced(),
            "label_count": self._label_store.label_count(),
            "derived_dates": self._derived_store.list_dates(),
        }

    # ── Commute Queries ───────────────────────────────────────────────────────

    def list_commutes(self) -> list[dict]:
        """List all detected commutes with summary stats."""
        df = self._derived_store.get_commutes()
        if df.is_empty():
            return []
        return _df_to_records(df)

    def get_commute(self, commute_id: str) -> dict | None:
        """Get full details for a single commute including points and segments."""
        points = self._derived_store.get_commute_points(commute_id)
        if points.is_empty():
            return None

        segments = self._derived_store.get_segments(commute_id)
        labels = self._label_store.get_labels(commute_id)

        return {
            "commute_id": commute_id,
            "points": _df_to_records(points),
            "segments": _df_to_records(segments),
            "labels": [_label_to_dict(lb) for lb in labels],
        }

    def get_commute_segments(self, commute_id: str) -> list[dict]:
        """Get segment breakdown for a commute."""
        df = self._derived_store.get_segments(commute_id)
        if df.is_empty():
            return []
        return _df_to_records(df)

    def get_commute_points(self, commute_id: str) -> list[dict]:
        """Get all GPS points for a commute, ordered by time."""
        df = self._derived_store.get_commute_points(commute_id)
        if df.is_empty():
            return []
        return _df_to_records(df)

    # ── Analytics ─────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Aggregate statistics across all commutes."""
        df = self._derived_store.get_commute_stats()
        if df.is_empty():
            return {}
        # Stats is typically a single-row summary
        records = _df_to_records(df)
        return records[0] if len(records) == 1 else {"rows": records}

    def get_daily_summary(self, day: str) -> list[dict]:
        """Get all data points for a specific date (YYYY-MM-DD)."""
        df = self._derived_store.get_daily_summary(day)
        if df.is_empty():
            return []
        return _df_to_records(df)

    def query_derived(self, sql: str) -> list[dict]:
        """Run an arbitrary SQL query over derived Parquet data.

        Use 'commute_data' as the table name.
        Example: SELECT commute_id, avg(speed_kmh) FROM commute_data GROUP BY commute_id
        """
        df = self._derived_store.query(sql)
        return _df_to_records(df)

    # ── Labels ────────────────────────────────────────────────────────────────

    def list_labels(self, commute_id: str | None = None) -> list[dict]:
        """List all segment labels, optionally filtered by commute."""
        labels = self._label_store.get_labels(commute_id)
        return [_label_to_dict(lb) for lb in labels]

    def add_label(
        self,
        commute_id: str,
        segment_id: int,
        original_mode: str,
        corrected_mode: str,
        notes: str = "",
    ) -> dict:
        """Add or update a segment label correction."""
        label = self._label_store.add_label(
            commute_id=commute_id,
            segment_id=segment_id,
            original_mode=original_mode,
            corrected_mode=corrected_mode,
            notes=notes,
        )
        return _label_to_dict(label)

    def export_labels(self) -> dict:
        """Export all labels as JSON."""
        return self._label_store.export_json()

    # ── Processing ────────────────────────────────────────────────────────────

    def rebuild_derived(
        self,
        since: str | None = None,
        until: str | None = None,
        user: str | None = None,
        device: str | None = None,
        clean: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """Rebuild derived Parquet files from the database.

        Args:
            since: Start date inclusive (YYYY-MM-DD)
            until: End date inclusive (YYYY-MM-DD)
            user: Filter by OwnTracks user
            device: Filter by OwnTracks device
            clean: Delete existing Parquet files in date range before rebuilding
            dry_run: If True, return what would be rebuilt without writing
        """
        from src.processing.pipeline import process_from_db

        filters: dict = {}
        if since:
            filters["since"] = since
        if until:
            filters["until"] = until
        if user:
            filters["user"] = user
        if device:
            filters["device"] = device

        if dry_run:
            return {
                "dry_run": True,
                "filters": filters,
                "clean": clean,
                "output_dir": str(self._derived_dir),
            }

        if clean and (since or until):
            self._clean_parquet_range(since, until)

        results = process_from_db(
            self._db,
            output_dir=self._derived_dir,
            filters=filters if filters else None,
        )
        return {
            "dry_run": False,
            "filters": filters,
            "dates_processed": list(results.keys()),
            "files_written": sum(len(v) if isinstance(v, list) else 1 for v in results.values()),
        }

    def _clean_parquet_range(self, since: str | None, until: str | None) -> int:
        """Delete Parquet files within a date range."""
        removed = 0
        for pq in self._derived_dir.rglob("*.parquet"):
            date_str = pq.parent.name  # date-partitioned directories
            if since and date_str < since:
                continue
            if until and date_str > until:
                continue
            pq.unlink()
            removed += 1
        return removed

    # ── ML ────────────────────────────────────────────────────────────────────

    def train_model(
        self,
        max_depth: int = 10,
        test_fraction: float = 0.2,
        model_path: str | None = None,
    ) -> dict:
        """Train the ML baseline model from labeled data.

        Returns training metrics including accuracy and feature importances.
        """
        from src.ml.trainer import train_from_labels

        model, metrics = train_from_labels(
            self._db,
            derived_dir=self._derived_dir,
            model_path=model_path,
            max_depth=max_depth,
            test_fraction=test_fraction,
        )
        return {
            "accuracy": metrics.accuracy,
            "sample_count": metrics.sample_count,
            "per_class": metrics.per_class,
            "feature_importances": metrics.feature_importances,
        }

    def evaluate_classifier(self) -> dict:
        """Compare ensemble classifier output against user labels.

        Returns accuracy metrics showing where the classifier agrees/disagrees
        with human corrections.
        """
        from src.ml.trainer import evaluate_classifier_accuracy

        return evaluate_classifier_accuracy(self._db, derived_dir=self._derived_dir)

    # ── Raw Data Stats ────────────────────────────────────────────────────────

    def get_raw_stats(self) -> dict:
        """Get statistics about raw GPS data in the database."""
        return {
            "total_records": self._db.count_records(),
            "unsynced_records": self._db.count_unsynced(),
        }

    def count_raw_records(
        self,
        since: str | None = None,
        until: str | None = None,
        user: str | None = None,
        device: str | None = None,
    ) -> dict:
        """Count raw GPS records matching filters. Used for rebuild preview."""
        from sqlalchemy import func
        from src.storage.database import LocationRecord

        with self._db.session() as session:
            q = session.query(func.count(LocationRecord.id))
            if since:
                q = q.filter(LocationRecord.received_at >= since)
            if until:
                q = q.filter(LocationRecord.received_at <= until + "T23:59:59")
            if user:
                q = q.filter(LocationRecord.user == user)
            if device:
                q = q.filter(LocationRecord.device == device)
            count = q.scalar()

        return {
            "count": count,
            "filters": {
                "since": since,
                "until": until,
                "user": user,
                "device": device,
            },
        }

    # ── Dates ─────────────────────────────────────────────────────────────────

    def list_dates(self) -> list[str]:
        """List all dates that have derived (processed) data available."""
        return self._derived_store.list_dates()

    # ── Label Corrections Map ─────────────────────────────────────────────────

    def get_corrections_map(self) -> dict[str, str]:
        """Get all label corrections as a flat lookup map.

        Returns dict mapping "commute_id:segment_id" -> corrected_mode.
        Efficient for frontends to check if any segment has been corrected.
        """
        raw_map = self._label_store.get_corrections_map()
        # Convert tuple keys to string keys for JSON serialization
        return {f"{cid}:{sid}": mode for (cid, sid), mode in raw_map.items()}

    # ── Bulk Labels ───────────────────────────────────────────────────────────

    def add_labels_bulk(self, labels: list[dict]) -> list[dict]:
        """Add multiple segment label corrections at once.

        Each item must have: commute_id, segment_id, original_mode, corrected_mode.
        Optional: notes.

        Returns the list of created/updated labels.
        """
        results = []
        for item in labels:
            label = self._label_store.add_label(
                commute_id=item["commute_id"],
                segment_id=item["segment_id"],
                original_mode=item["original_mode"],
                corrected_mode=item["corrected_mode"],
                notes=item.get("notes", ""),
            )
            results.append(_label_to_dict(label))
        return results


# ── Helpers ───────────────────────────────────────────────────────────────────

def _df_to_records(df: pl.DataFrame) -> list[dict]:
    """Convert a Polars DataFrame to a list of JSON-serializable dicts."""
    records = df.to_dicts()
    for row in records:
        for key, val in row.items():
            if isinstance(val, (datetime, date)):
                row[key] = val.isoformat()
    return records


def _label_to_dict(label) -> dict:
    """Convert a SegmentLabel to a JSON-serializable dict."""
    labeled_at = label.labeled_at
    if isinstance(labeled_at, (datetime, date)):
        labeled_at = labeled_at.isoformat()
    return {
        "commute_id": label.commute_id,
        "segment_id": label.segment_id,
        "original_mode": label.original_mode,
        "corrected_mode": label.corrected_mode,
        "notes": label.notes,
        "labeled_at": labeled_at,
    }
