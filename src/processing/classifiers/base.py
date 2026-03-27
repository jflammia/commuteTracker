"""Base types for transport mode classification.

Classifiers produce ModeScores for each point. The ensemble combines
scores from multiple classifiers to make a final decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import polars as pl


# All recognized transport modes
TRANSPORT_MODES = ("stationary", "waiting", "walking", "driving", "train")


@dataclass
class ModeScores:
    """Confidence scores for each transport mode at a single point.

    Scores are non-negative. Higher means more confident.
    They don't need to sum to 1 -- the ensemble normalizes across classifiers.
    """

    stationary: float = 0.0
    waiting: float = 0.0
    walking: float = 0.0
    driving: float = 0.0
    train: float = 0.0

    def winner(self) -> str:
        """Return the mode with the highest score."""
        scores = {
            "stationary": self.stationary,
            "waiting": self.waiting,
            "walking": self.walking,
            "driving": self.driving,
            "train": self.train,
        }
        return max(scores, key=scores.get)

    def as_dict(self) -> dict[str, float]:
        return {
            "stationary": self.stationary,
            "waiting": self.waiting,
            "walking": self.walking,
            "driving": self.driving,
            "train": self.train,
        }

    def __add__(self, other: ModeScores) -> ModeScores:
        return ModeScores(
            stationary=self.stationary + other.stationary,
            waiting=self.waiting + other.waiting,
            walking=self.walking + other.walking,
            driving=self.driving + other.driving,
            train=self.train + other.train,
        )

    def scale(self, factor: float) -> ModeScores:
        return ModeScores(
            stationary=self.stationary * factor,
            waiting=self.waiting * factor,
            walking=self.walking * factor,
            driving=self.driving * factor,
            train=self.train * factor,
        )


class TransportClassifier(Protocol):
    """Protocol for transport mode classifiers.

    Each classifier scores points independently. The ensemble
    combines scores from multiple classifiers.
    """

    @property
    def name(self) -> str:
        """Human-readable name for this classifier."""
        ...

    def score(self, df: pl.DataFrame) -> list[ModeScores]:
        """Score each row in the DataFrame.

        Args:
            df: DataFrame with at minimum: lat, lon, speed_kmh, time_delta_s, timestamp.

        Returns:
            One ModeScores per row, in the same order as the DataFrame.
        """
        ...
