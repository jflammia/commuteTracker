"""Speed threshold classifier.

The simplest classifier: maps speed ranges to transport modes.
Works with zero configuration -- good baseline for any commute.

Default thresholds:
    stationary: < 1 km/h
    walking: 1-7 km/h
    driving: 7-30 km/h
    train: >= 30 km/h

Thresholds are configurable per-instance.
"""

from dataclasses import dataclass

import polars as pl

from src.processing.classifiers.base import ModeScores


@dataclass
class SpeedClassifier:
    """Classify transport mode by speed thresholds."""

    stationary_max_kmh: float = 1.0
    walk_max_kmh: float = 7.0
    train_min_kmh: float = 30.0

    @property
    def name(self) -> str:
        return "speed"

    def score(self, df: pl.DataFrame) -> list[ModeScores]:
        speeds = df["speed_kmh"].to_list()
        results = []

        for speed in speeds:
            if speed < self.stationary_max_kmh:
                scores = ModeScores(stationary=1.0)
            elif speed < self.walk_max_kmh:
                scores = ModeScores(walking=1.0)
            elif speed < self.train_min_kmh:
                scores = ModeScores(driving=1.0)
            else:
                scores = ModeScores(train=1.0)
            results.append(scores)

        return results
