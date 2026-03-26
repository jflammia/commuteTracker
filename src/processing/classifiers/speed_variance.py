"""Speed variance classifier.

Distinguishes driving from train by analyzing speed smoothness.
Trains maintain relatively constant speed between stations.
Cars have high speed variance from acceleration, braking, traffic.

This classifier only contributes signal when speed is in the ambiguous
zone (above walking, could be driving or train). It doesn't override
the speed classifier for clear cases.

Requires no configuration -- works automatically from GPS data.
"""

import statistics
from dataclasses import dataclass

import polars as pl

from src.processing.classifiers.base import ModeScores


@dataclass
class SpeedVarianceClassifier:
    """Use rolling speed variance to disambiguate driving vs train."""

    window_size: int = 15
    # Coefficient of variation threshold: below this, speed is "smooth" (train-like)
    smooth_cv_threshold: float = 0.25
    # Only contribute scores when speed is in the ambiguous zone
    min_speed_kmh: float = 15.0

    @property
    def name(self) -> str:
        return "speed_variance"

    def score(self, df: pl.DataFrame) -> list[ModeScores]:
        speeds = df["speed_kmh"].to_list()
        n = len(speeds)
        half = self.window_size // 2
        results: list[ModeScores] = []

        for i in range(n):
            speed = speeds[i]

            # Only contribute signal in the ambiguous speed range
            if speed < self.min_speed_kmh:
                results.append(ModeScores())
                continue

            # Get the window around this point
            start = max(0, i - half)
            end = min(n, i + half + 1)
            window = speeds[start:end]

            # Need at least 3 points for meaningful variance
            if len(window) < 3:
                results.append(ModeScores())
                continue

            mean = statistics.mean(window)
            if mean < 1.0:
                results.append(ModeScores())
                continue

            stdev = statistics.stdev(window)
            cv = stdev / mean  # Coefficient of variation

            if cv < self.smooth_cv_threshold:
                # Smooth speed profile -> likely train
                confidence = 1.0 - (cv / self.smooth_cv_threshold)
                results.append(ModeScores(train=confidence))
            else:
                # Variable speed profile -> likely driving
                confidence = min(1.0, (cv - self.smooth_cv_threshold) / self.smooth_cv_threshold)
                results.append(ModeScores(driving=confidence))

        return results
