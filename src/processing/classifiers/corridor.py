"""Route corridor classifier.

Users define named route corridors (e.g., a rail line or bus route)
as a sequence of points with a buffer distance. Points near a corridor
get a confidence boost for that corridor's transport mode.

This enables accurate classification even when speed alone is ambiguous
(e.g., a train slowing into a station at 20 km/h vs a car at 20 km/h).

Config format (zones.json):
{
    "corridors": [
        {
            "name": "NJ Transit Northeast Corridor",
            "mode": "train",
            "buffer_m": 150,
            "points": [
                [40.7506, -73.9935],
                [40.7650, -73.9820],
                [40.8000, -73.9700]
            ]
        }
    ]
}

Points are [lat, lon] pairs defining the corridor centerline.
buffer_m is how far from the centerline a GPS point can be
and still be considered "on" the corridor.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

from src.processing.classifiers.base import ModeScores
from src.processing.geo_utils import haversine_m


@dataclass
class Corridor:
    """A named route corridor that biases classification."""

    name: str
    mode: str
    points: list[tuple[float, float]]  # [(lat, lon), ...]
    buffer_m: float = 150.0

    def distance_to_nearest_segment(self, lat: float, lon: float) -> float:
        """Minimum distance from the point to any segment of the corridor.

        Uses point-to-segment approximation: checks distance to each
        corridor point and to the nearest interpolated point on each segment.
        """
        if not self.points:
            return float("inf")

        min_dist = float("inf")

        # Check distance to each corridor vertex
        for clat, clon in self.points:
            d = haversine_m(lat, lon, clat, clon)
            min_dist = min(min_dist, d)

        # Check distance to midpoints of each segment for better approximation
        for i in range(len(self.points) - 1):
            lat1, lon1 = self.points[i]
            lat2, lon2 = self.points[i + 1]
            # Project point onto segment using linear interpolation
            # Check several interpolated points along the segment
            for frac in (0.25, 0.5, 0.75):
                mid_lat = lat1 + (lat2 - lat1) * frac
                mid_lon = lon1 + (lon2 - lon1) * frac
                d = haversine_m(lat, lon, mid_lat, mid_lon)
                min_dist = min(min_dist, d)

        return min_dist

    def contains(self, lat: float, lon: float) -> bool:
        return self.distance_to_nearest_segment(lat, lon) <= self.buffer_m

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "mode": self.mode,
            "points": [list(p) for p in self.points],
            "buffer_m": self.buffer_m,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Corridor:
        return cls(
            name=d["name"],
            mode=d["mode"],
            points=[tuple(p) for p in d["points"]],
            buffer_m=d.get("buffer_m", 150.0),
        )


@dataclass
class CorridorClassifier:
    """Score points based on proximity to user-defined route corridors."""

    corridors: list[Corridor] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "corridor"

    def score(self, df: pl.DataFrame) -> list[ModeScores]:
        lats = df["lat"].to_list()
        lons = df["lon"].to_list()
        results: list[ModeScores] = []

        for lat, lon in zip(lats, lons):
            scores = ModeScores()

            for corridor in self.corridors:
                dist = corridor.distance_to_nearest_segment(lat, lon)
                if dist <= corridor.buffer_m:
                    # Confidence scales with proximity (closer = more confident)
                    confidence = 1.0 - (dist / corridor.buffer_m)
                    current = getattr(scores, corridor.mode, 0.0)
                    setattr(scores, corridor.mode, max(current, confidence))

            results.append(scores)

        return results
