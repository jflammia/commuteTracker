"""Ensemble classifier: combines multiple classifiers via weighted voting.

The ensemble is the main entry point for transport mode classification.
It runs all registered classifiers, multiplies each by its weight,
sums the scores per mode, and picks the winner.

Default setup (zero config):
    - SpeedClassifier (weight 1.0) -- always present
    - SpeedVarianceClassifier (weight 0.5) -- always present

With user config:
    - WaypointClassifier (weight 1.5) -- high weight, user knows their commute
    - CorridorClassifier (weight 1.2) -- strong spatial signal
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from src.processing.classifiers.base import ModeScores, TransportClassifier
from src.processing.classifiers.speed import SpeedClassifier
from src.processing.classifiers.speed_variance import SpeedVarianceClassifier
from src.processing.classifiers.waypoint import Waypoint, WaypointClassifier
from src.processing.classifiers.corridor import Corridor, CorridorClassifier

logger = logging.getLogger(__name__)


@dataclass
class ClassifierEntry:
    """A classifier with its weight in the ensemble."""

    classifier: TransportClassifier
    weight: float


@dataclass
class EnsembleClassifier:
    """Combine multiple classifiers via weighted voting."""

    entries: list[ClassifierEntry] = field(default_factory=list)

    def classify(self, df: pl.DataFrame) -> list[str]:
        """Run all classifiers and return the winning mode per point."""
        if df.is_empty():
            return []

        n = len(df)
        combined = [ModeScores() for _ in range(n)]

        for entry in self.entries:
            scores = entry.classifier.score(df)
            for i in range(n):
                combined[i] = combined[i] + scores[i].scale(entry.weight)

        return [s.winner() for s in combined]

    def classify_with_confidence(self, df: pl.DataFrame) -> list[tuple[str, ModeScores]]:
        """Run all classifiers and return (mode, scores) per point.

        Useful for debugging and for the dashboard to show confidence levels.
        """
        if df.is_empty():
            return []

        n = len(df)
        combined = [ModeScores() for _ in range(n)]

        for entry in self.entries:
            scores = entry.classifier.score(df)
            for i in range(n):
                combined[i] = combined[i] + scores[i].scale(entry.weight)

        return [(s.winner(), s) for s in combined]

    def get_waypoint_boundaries(self, df: pl.DataFrame) -> list[int]:
        """Get segment boundary indices from waypoint classifiers."""
        boundaries: list[int] = []
        for entry in self.entries:
            if isinstance(entry.classifier, WaypointClassifier):
                boundaries.extend(entry.classifier.get_boundary_indices(df))
        return sorted(set(boundaries))


def build_ensemble(zones_config: dict | None = None) -> EnsembleClassifier:
    """Build an ensemble from optional zone configuration.

    Always includes speed and speed-variance classifiers.
    Adds waypoint and corridor classifiers if config is provided.

    Args:
        zones_config: Parsed JSON config with optional 'waypoints' and 'corridors' keys.
                      If None, returns a zero-config ensemble (speed + variance only).
    """
    entries = [
        ClassifierEntry(SpeedClassifier(), weight=1.0),
        ClassifierEntry(SpeedVarianceClassifier(), weight=0.5),
    ]

    if zones_config:
        # Waypoints
        waypoint_dicts = zones_config.get("waypoints", [])
        if waypoint_dicts:
            waypoints = [Waypoint.from_dict(w) for w in waypoint_dicts]
            entries.append(ClassifierEntry(WaypointClassifier(waypoints), weight=1.5))
            logger.info(f"Loaded {len(waypoints)} waypoints")

        # Corridors
        corridor_dicts = zones_config.get("corridors", [])
        if corridor_dicts:
            corridors = [Corridor.from_dict(c) for c in corridor_dicts]
            entries.append(ClassifierEntry(CorridorClassifier(corridors), weight=1.2))
            logger.info(f"Loaded {len(corridors)} corridors")

    return EnsembleClassifier(entries=entries)


def load_zones_config(path: str | Path | None = None) -> dict | None:
    """Load zone configuration from a JSON file.

    Searches in order:
    1. Explicit path argument
    2. ZONES_CONFIG env var
    3. zones.json in project root
    4. Returns None (zero-config mode)
    """
    import os

    candidates: list[Path] = []

    if path:
        candidates.append(Path(path))

    env_path = os.environ.get("ZONES_CONFIG", "")
    if env_path:
        candidates.append(Path(env_path))

    # Project root
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    candidates.append(project_root / "zones.json")

    for candidate in candidates:
        if candidate.exists():
            logger.info(f"Loading zone config from {candidate}")
            with open(candidate) as f:
                return json.load(f)

    logger.debug("No zone config found, using zero-config mode")
    return None
