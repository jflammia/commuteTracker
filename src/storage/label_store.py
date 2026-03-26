"""Label store: persist user corrections to transport mode classifications.

When a user corrects a segment's transport mode in the dashboard,
the correction is stored here. These labels serve as ground truth for:
    1. Overriding classifier output on re-processing
    2. Training data for future ML models
    3. Evaluating classifier accuracy over time

Labels are stored as a simple JSON file (one per commute profile).
This avoids adding a database dependency for what is a small, append-mostly dataset.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

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


class LabelStore:
    """Read/write user-provided segment labels."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._labels: list[SegmentLabel] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            with open(self.path) as f:
                data = json.load(f)
            self._labels = [SegmentLabel.from_dict(d) for d in data.get("labels", [])]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump({"labels": [l.to_dict() for l in self._labels]}, f, indent=2)

    def add_label(
        self,
        commute_id: str,
        segment_id: int,
        original_mode: str,
        corrected_mode: str,
        notes: str = "",
    ) -> SegmentLabel:
        """Add or update a label for a segment."""
        # Remove existing label for same commute+segment if present
        self._labels = [
            l for l in self._labels
            if not (l.commute_id == commute_id and l.segment_id == segment_id)
        ]

        label = SegmentLabel(
            commute_id=commute_id,
            segment_id=segment_id,
            original_mode=original_mode,
            corrected_mode=corrected_mode,
            labeled_at=datetime.utcnow().isoformat(),
            notes=notes,
        )
        self._labels.append(label)
        self._save()
        return label

    def get_labels(self, commute_id: str | None = None) -> list[SegmentLabel]:
        """Get all labels, optionally filtered by commute_id."""
        if commute_id:
            return [l for l in self._labels if l.commute_id == commute_id]
        return list(self._labels)

    def get_corrections_map(self) -> dict[tuple[str, int], str]:
        """Return a lookup: (commute_id, segment_id) -> corrected_mode.

        Useful for applying corrections during re-processing.
        """
        return {
            (l.commute_id, l.segment_id): l.corrected_mode
            for l in self._labels
        }

    def label_count(self) -> int:
        return len(self._labels)
