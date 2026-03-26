"""Waypoint/transition zone classifier.

Users define named geographic zones that force segment boundaries
or bias transport mode classification. Examples:
    - A train station platform -> strong train signal when departing
    - A parking lot -> strong driving signal when departing
    - A bus stop -> transition point

Waypoints are loaded from a JSON config file. No waypoints = no effect.

Config format (zones.json):
{
    "waypoints": [
        {
            "name": "Penn Station",
            "lat": 40.7506,
            "lon": -73.9935,
            "radius_m": 100,
            "mode_hint": "train"
        },
        {
            "name": "Parking Garage",
            "lat": 40.7600,
            "lon": -73.9800,
            "radius_m": 50,
            "mode_hint": "driving"
        }
    ]
}

mode_hint is optional. If set, points within the zone get a confidence
boost for that mode. If null, the waypoint only forces a segment boundary
(handled by the segmenter, not this classifier).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

from src.processing.classifiers.base import ModeScores
from src.processing.geo_utils import haversine_m


@dataclass
class Waypoint:
    """A named geographic zone that influences classification."""

    name: str
    lat: float
    lon: float
    radius_m: float
    mode_hint: str | None = None

    def contains(self, lat: float, lon: float) -> bool:
        return haversine_m(lat, lon, self.lat, self.lon) <= self.radius_m

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "lat": self.lat,
            "lon": self.lon,
            "radius_m": self.radius_m,
            "mode_hint": self.mode_hint,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Waypoint:
        return cls(
            name=d["name"],
            lat=d["lat"],
            lon=d["lon"],
            radius_m=d["radius_m"],
            mode_hint=d.get("mode_hint"),
        )


@dataclass
class WaypointClassifier:
    """Score points based on proximity to user-defined waypoints."""

    waypoints: list[Waypoint] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "waypoint"

    def score(self, df: pl.DataFrame) -> list[ModeScores]:
        lats = df["lat"].to_list()
        lons = df["lon"].to_list()
        results: list[ModeScores] = []

        for lat, lon in zip(lats, lons):
            scores = ModeScores()

            for wp in self.waypoints:
                if wp.mode_hint and wp.contains(lat, lon):
                    current = getattr(scores, wp.mode_hint, 0.0)
                    setattr(scores, wp.mode_hint, max(current, 1.0))

            results.append(scores)

        return results

    def find_waypoints_at(self, lat: float, lon: float) -> list[Waypoint]:
        """Return all waypoints that contain the given point."""
        return [wp for wp in self.waypoints if wp.contains(lat, lon)]

    def get_boundary_indices(self, df: pl.DataFrame) -> list[int]:
        """Return indices where points enter or exit any waypoint zone.

        These are natural segment boundaries regardless of mode_hint.
        The segmenter can use these to force segment splits.
        """
        lats = df["lat"].to_list()
        lons = df["lon"].to_list()
        boundaries: list[int] = []

        prev_zones: set[str] = set()
        for i, (lat, lon) in enumerate(zip(lats, lons)):
            current_zones = {wp.name for wp in self.waypoints if wp.contains(lat, lon)}
            if current_zones != prev_zones and i > 0:
                boundaries.append(i)
            prev_zones = current_zones

        return boundaries
