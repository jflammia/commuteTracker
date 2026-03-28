"""Label store: persist user corrections to transport mode classifications.

When a user corrects a segment's transport mode in the dashboard,
the correction is stored here. These labels serve as ground truth for:
    1. Overriding classifier output on re-processing
    2. Training data for future ML models
    3. Evaluating classifier accuracy over time

Labels are stored in the same SQLAlchemy database as raw GPS data,
so they get the same durability guarantees (volume persistence, S3 backup path).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone


from src.storage.database import Database, SegmentLabelRecord

logger = logging.getLogger(__name__)


@dataclass
class SegmentLabel:
    """A user-provided label for a segment of a commute."""

    commute_id: str
    segment_id: int
    original_mode: str
    corrected_mode: str
    labeled_at: str  # ISO timestamp
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "commute_id": self.commute_id,
            "segment_id": self.segment_id,
            "original_mode": self.original_mode,
            "corrected_mode": self.corrected_mode,
            "labeled_at": self.labeled_at,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SegmentLabel:
        return cls(**d)

    @classmethod
    def from_record(cls, record: SegmentLabelRecord) -> SegmentLabel:
        return cls(
            commute_id=record.commute_id,
            segment_id=record.segment_id,
            original_mode=record.original_mode,
            corrected_mode=record.corrected_mode,
            labeled_at=record.labeled_at.isoformat() if record.labeled_at else "",
            notes=record.notes or "",
        )


class LabelStore:
    """Read/write user-provided segment labels via SQLAlchemy."""

    def __init__(self, db: Database):
        self._db = db

    def add_label(
        self,
        commute_id: str,
        segment_id: int,
        original_mode: str,
        corrected_mode: str,
        notes: str = "",
    ) -> SegmentLabel:
        """Add or update a label for a segment."""
        now = datetime.now(timezone.utc)

        with self._db.session() as session:
            # Remove existing label for same commute+segment if present
            session.query(SegmentLabelRecord).filter(
                SegmentLabelRecord.commute_id == commute_id,
                SegmentLabelRecord.segment_id == segment_id,
            ).delete(synchronize_session=False)

            record = SegmentLabelRecord(
                commute_id=commute_id,
                segment_id=segment_id,
                original_mode=original_mode,
                corrected_mode=corrected_mode,
                notes=notes,
                labeled_at=now,
            )
            session.add(record)
            session.commit()

        return SegmentLabel(
            commute_id=commute_id,
            segment_id=segment_id,
            original_mode=original_mode,
            corrected_mode=corrected_mode,
            labeled_at=now.isoformat(),
            notes=notes,
        )

    def get_labels(self, commute_id: str | None = None) -> list[SegmentLabel]:
        """Get all labels, optionally filtered by commute_id."""
        with self._db.session() as session:
            query = session.query(SegmentLabelRecord)
            if commute_id:
                query = query.filter(SegmentLabelRecord.commute_id == commute_id)
            query = query.order_by(SegmentLabelRecord.commute_id, SegmentLabelRecord.segment_id)
            return [SegmentLabel.from_record(r) for r in query.all()]

    def get_corrections_map(self) -> dict[tuple[str, int], str]:
        """Return a lookup: (commute_id, segment_id) -> corrected_mode.

        Useful for applying corrections during re-processing.
        """
        labels = self.get_labels()
        return {(lb.commute_id, lb.segment_id): lb.corrected_mode for lb in labels}

    def label_count(self) -> int:
        with self._db.session() as session:
            return session.query(SegmentLabelRecord).count()

    def export_json(self) -> dict:
        """Export all labels as a JSON-serializable dict."""
        return {"labels": [lb.to_dict() for lb in self.get_labels()]}

    def migrate_commute_ids(self, old_to_new: dict[str, str]) -> int:
        """Update commute_id on labels where the commute ID changed.

        Returns the number of labels updated.
        """
        count = 0
        with self._db.session() as session:
            for old_id, new_id in old_to_new.items():
                updated = (
                    session.query(SegmentLabelRecord)
                    .filter(SegmentLabelRecord.commute_id == old_id)
                    .update({SegmentLabelRecord.commute_id: new_id})
                )
                count += updated
            session.commit()
        if count:
            logger.info(f"Migrated {count} label(s) to new commute IDs")
        return count
