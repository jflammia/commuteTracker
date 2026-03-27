"""Commute Tracker service layer.

Pure business logic, no transport concerns. Used by both the REST API and MCP server.
All methods return plain dicts/lists suitable for JSON serialization.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
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
            "total_records": results.get("total_records", 0),
            "commutes_found": results.get("commutes_found", 0),
            "dates_processed": [
                p.rsplit("/", 1)[-1].replace(".parquet", "")
                for p in results.get("files_written", [])
            ],
            "files_written": len(results.get("files_written", [])),
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

    # ── Multi-Level Labeling Intelligence ──────────────────────────────────

    def analyze_segment(self, commute_id: str, segment_id: int) -> dict:
        """Deep analysis of a single segment with mismatch detection.

        Returns speed statistics, point count, classified mode, and whether
        the speed profile is consistent with the classification. If a mismatch
        is detected, suggests the most likely correct mode with confidence.

        This is the LOW-LEVEL labeling operation — use it when you need to
        understand exactly what's happening in one segment before correcting.
        """
        points = self._derived_store.get_commute_points(commute_id)
        if points.is_empty():
            return {"error": f"Commute {commute_id} not found"}

        seg_points = points.filter(pl.col("segment_id") == segment_id)
        if seg_points.is_empty():
            return {"error": f"Segment {segment_id} not found in {commute_id}"}

        speed_col = seg_points["speed_kmh"]
        classified_mode = (
            seg_points["transport_mode"][0]
            if "transport_mode" in seg_points.columns
            else "unknown"
        )

        stats: dict = {
            "commute_id": commute_id,
            "segment_id": segment_id,
            "classified_mode": classified_mode,
            "point_count": len(seg_points),
            "speed_mean_kmh": round(speed_col.mean(), 1) if not speed_col.is_empty() else 0,
            "speed_median_kmh": round(speed_col.median(), 1) if not speed_col.is_empty() else 0,
            "speed_max_kmh": round(speed_col.max(), 1) if not speed_col.is_empty() else 0,
            "speed_min_kmh": round(speed_col.min(), 1) if not speed_col.is_empty() else 0,
            "speed_std_kmh": round(speed_col.std(), 1)
            if not speed_col.is_empty() and speed_col.std() is not None
            else 0,
        }

        if "timestamp" in seg_points.columns:
            ts = seg_points["timestamp"]
            duration_s = (ts.max() - ts.min()).total_seconds() if len(ts) > 1 else 0
            stats["duration_min"] = round(duration_s / 60, 1)

        if "distance_m" in seg_points.columns:
            stats["distance_m"] = round(seg_points["distance_m"].sum(), 0)

        # Context: neighboring segments
        segments = self._derived_store.get_segments(commute_id)
        if not segments.is_empty():
            seg_list = segments.to_dicts()
            idx = next(
                (i for i, s in enumerate(seg_list) if s["segment_id"] == segment_id),
                None,
            )
            if idx is not None:
                stats["prev_segment_mode"] = (
                    seg_list[idx - 1]["transport_mode"] if idx > 0 else None
                )
                stats["next_segment_mode"] = (
                    seg_list[idx + 1]["transport_mode"]
                    if idx < len(seg_list) - 1
                    else None
                )

        # Mismatch detection
        analysis = _detect_mode_mismatch(
            classified_mode,
            stats["speed_mean_kmh"],
            stats["speed_max_kmh"],
            stats["speed_std_kmh"],
            stats.get("prev_segment_mode"),
            stats.get("next_segment_mode"),
        )
        stats["mismatch"] = analysis["mismatch"]
        stats["confidence"] = analysis["confidence"]
        stats["suggested_mode"] = analysis["suggested_mode"]
        stats["reason"] = analysis["reason"]

        # Check for existing correction
        corrections = self.get_corrections_map()
        key = f"{commute_id}:{segment_id}"
        if key in corrections:
            stats["existing_correction"] = corrections[key]

        return stats

    def review_commute_segments(self, commute_id: str) -> dict:
        """Review all segments in a commute and flag suspicious classifications.

        This is the MID-LEVEL labeling operation. Analyzes every segment,
        checks speed profiles against expected ranges, and returns a summary
        with flagged segments sorted by confidence of misclassification.

        Use this to efficiently review an entire commute at once rather than
        checking segments individually.
        """
        segments = self._derived_store.get_segments(commute_id)
        if segments.is_empty():
            return {"commute_id": commute_id, "error": "No segments found"}

        points = self._derived_store.get_commute_points(commute_id)
        if points.is_empty():
            return {"commute_id": commute_id, "error": "No points found"}

        corrections = self.get_corrections_map()
        seg_list = segments.to_dicts()
        reviewed = []
        flagged = []

        for i, seg in enumerate(seg_list):
            sid = seg["segment_id"]
            mode = seg["transport_mode"]
            avg_speed = seg.get("avg_speed_kmh", 0) or 0
            max_speed = seg.get("max_speed_kmh", 0) or 0

            seg_points = points.filter(pl.col("segment_id") == sid)
            speed_std = 0.0
            if not seg_points.is_empty() and "speed_kmh" in seg_points.columns:
                std_val = seg_points["speed_kmh"].std()
                speed_std = round(std_val, 1) if std_val is not None else 0.0

            prev_mode = seg_list[i - 1]["transport_mode"] if i > 0 else None
            next_mode = (
                seg_list[i + 1]["transport_mode"] if i < len(seg_list) - 1 else None
            )

            analysis = _detect_mode_mismatch(
                mode, avg_speed, max_speed, speed_std, prev_mode, next_mode
            )

            entry = {
                "segment_id": sid,
                "classified_mode": mode,
                "duration_min": seg.get("duration_min", 0),
                "distance_m": seg.get("distance_m", 0),
                "avg_speed_kmh": avg_speed,
                "max_speed_kmh": max_speed,
                "mismatch": analysis["mismatch"],
                "confidence": analysis["confidence"],
                "suggested_mode": analysis["suggested_mode"],
                "reason": analysis["reason"],
            }

            key = f"{commute_id}:{sid}"
            if key in corrections:
                entry["existing_correction"] = corrections[key]

            reviewed.append(entry)
            if analysis["mismatch"]:
                flagged.append(entry)

        flagged.sort(key=lambda x: x["confidence"], reverse=True)

        return {
            "commute_id": commute_id,
            "total_segments": len(seg_list),
            "flagged_count": len(flagged),
            "flagged_segments": flagged,
            "all_segments": reviewed,
            "suggested_corrections": [
                {
                    "commute_id": commute_id,
                    "segment_id": f["segment_id"],
                    "original_mode": f["classified_mode"],
                    "corrected_mode": f["suggested_mode"],
                    "confidence": f["confidence"],
                    "notes": f"auto-flagged: {f['reason']}",
                }
                for f in flagged
                if f["suggested_mode"] is not None
                and f"{commute_id}:{f['segment_id']}" not in corrections
            ],
        }

    def review_recent_commutes(
        self,
        n: int = 5,
        direction: str | None = None,
    ) -> dict:
        """Review recent commutes for systematic misclassification patterns.

        This is the HIGH-LEVEL labeling operation. Reviews the last N commutes,
        aggregates mismatch patterns, and identifies systematic issues (e.g.,
        "driving segments with >30 km/h avg are frequently misclassified trains").

        Use this for batch review to find and fix patterns across multiple
        commutes rather than reviewing one at a time.
        """
        commutes = self._derived_store.get_commutes()
        if commutes.is_empty():
            return {"error": "No commutes found"}

        if direction:
            commutes = commutes.filter(pl.col("commute_direction") == direction)

        commutes = commutes.sort("start_time", descending=True).head(n)
        commute_ids = commutes["commute_id"].to_list()

        all_flagged: list[dict] = []
        commute_summaries = []

        for cid in commute_ids:
            review = self.review_commute_segments(cid)
            if "error" in review:
                continue
            commute_summaries.append({
                "commute_id": cid,
                "total_segments": review["total_segments"],
                "flagged_count": review["flagged_count"],
            })
            for f in review.get("flagged_segments", []):
                all_flagged.append({**f, "commute_id": cid})

        # Find systematic patterns: group by (classified -> suggested)
        patterns: dict[str, list] = {}
        for f in all_flagged:
            if f["suggested_mode"] is None:
                continue
            key = f"{f['classified_mode']} -> {f['suggested_mode']}"
            patterns.setdefault(key, []).append(f)

        systematic = []
        for pattern, instances in sorted(patterns.items(), key=lambda x: -len(x[1])):
            speeds = [i["avg_speed_kmh"] for i in instances]
            systematic.append({
                "pattern": pattern,
                "count": len(instances),
                "avg_speed_kmh": round(sum(speeds) / len(speeds), 1) if speeds else 0,
                "avg_confidence": round(
                    sum(i["confidence"] for i in instances) / len(instances), 2
                ),
                "commute_ids": list({i["commute_id"] for i in instances}),
            })

        corrections = self.get_corrections_map()
        all_suggestions = [
            {
                "commute_id": f["commute_id"],
                "segment_id": f["segment_id"],
                "original_mode": f["classified_mode"],
                "corrected_mode": f["suggested_mode"],
                "confidence": f["confidence"],
                "notes": f"auto-flagged: {f['reason']}",
            }
            for f in all_flagged
            if f["suggested_mode"] is not None
            and f"{f['commute_id']}:{f['segment_id']}" not in corrections
        ]

        return {
            "commutes_reviewed": len(commute_summaries),
            "commute_summaries": commute_summaries,
            "total_flagged": len(all_flagged),
            "systematic_patterns": systematic,
            "suggested_corrections": all_suggestions,
        }

    def apply_suggested_corrections(
        self,
        corrections: list[dict],
        min_confidence: float = 0.7,
    ) -> dict:
        """Apply corrections from a review, filtered by confidence threshold.

        Takes the suggested_corrections output from review_commute_segments or
        review_recent_commutes and applies them as labels. Only corrections
        with confidence >= min_confidence are applied.

        Returns the count of applied and skipped corrections with details.
        """
        applied = []
        skipped = []

        for c in corrections:
            confidence = c.get("confidence", 0)
            if confidence < min_confidence:
                skipped.append({
                    **c,
                    "skip_reason": f"confidence {confidence} < {min_confidence}",
                })
                continue

            label = self._label_store.add_label(
                commute_id=c["commute_id"],
                segment_id=c["segment_id"],
                original_mode=c["original_mode"],
                corrected_mode=c["corrected_mode"],
                notes=c.get("notes", f"auto-applied (confidence={confidence})"),
            )
            applied.append(_label_to_dict(label))

        return {
            "applied_count": len(applied),
            "skipped_count": len(skipped),
            "min_confidence": min_confidence,
            "applied": applied,
            "skipped": skipped,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

# Speed thresholds (km/h) for mode validation — mirrors SpeedClassifier
_MODE_SPEED_RANGES = {
    "stationary": (0, 1),
    "waiting": (0, 2),
    "walking": (1, 7),
    "driving": (7, 80),
    "train": (30, 300),
}


def _detect_mode_mismatch(
    classified_mode: str,
    avg_speed: float,
    max_speed: float,
    speed_std: float,
    prev_mode: str | None,
    next_mode: str | None,
) -> dict:
    """Check if a segment's speed profile is consistent with its classified mode.

    Returns a dict with mismatch (bool), confidence (0-1), suggested_mode, and reason.
    """
    if classified_mode not in _MODE_SPEED_RANGES:
        return {
            "mismatch": False,
            "confidence": 0,
            "suggested_mode": None,
            "reason": "unknown mode",
        }

    lo, hi = _MODE_SPEED_RANGES[classified_mode]

    # Check if average speed falls within expected range
    if lo <= avg_speed <= hi:
        # Speed is in range — might still be wrong contextually, but low confidence
        # Check for stationary that should be waiting (between different moving modes)
        if classified_mode == "stationary" and avg_speed < 2:
            moving = {"walking", "driving", "train"}
            if prev_mode in moving and next_mode in moving and prev_mode != next_mode:
                return {
                    "mismatch": True,
                    "confidence": 0.8,
                    "suggested_mode": "waiting",
                    "reason": f"stationary between {prev_mode} and {next_mode} — likely a transfer",
                }
        return {
            "mismatch": False,
            "confidence": 0,
            "suggested_mode": None,
            "reason": "speed profile consistent with mode",
        }

    # Speed is outside expected range — determine what it should be
    suggested = _suggest_mode(avg_speed, max_speed, speed_std)

    # Calculate confidence based on how far outside the range
    if avg_speed < lo:
        distance_ratio = (lo - avg_speed) / max(lo, 1)
    else:
        distance_ratio = (avg_speed - hi) / max(hi, 1)
    confidence = min(0.95, 0.5 + distance_ratio * 0.5)

    reason_parts = [f"avg speed {avg_speed} km/h outside {classified_mode} range ({lo}-{hi})"]
    if max_speed > 0:
        reason_parts.append(f"max {max_speed} km/h")

    return {
        "mismatch": True,
        "confidence": round(confidence, 2),
        "suggested_mode": suggested,
        "reason": ", ".join(reason_parts),
    }


def _suggest_mode(avg_speed: float, max_speed: float, speed_std: float) -> str:
    """Suggest the most likely transport mode based on speed statistics."""
    if avg_speed < 1:
        return "stationary"
    if avg_speed < 7:
        return "walking"
    if avg_speed >= 30:
        # High-speed: train has smoother speed (lower std relative to mean)
        # Driving tends to have more variation at these speeds
        if speed_std > 0 and avg_speed > 0:
            cv = speed_std / avg_speed  # coefficient of variation
            if cv < 0.4:
                return "train"
        if max_speed > 80:
            return "train"
        return "driving" if avg_speed < 60 else "train"
    # 7-30 km/h range
    return "driving"


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
