"""Decompose a matched/labeled trip into optimizer leg observations.

A leg is an atom the composer reasons about: access (door→first rail board),
ride (a matched scheduled train), transfer (walk between two rail legs),
egress (last rail alight→door). Only trips whose rail segments are all matched
to scheduled trains are decomposable — an unmatched vehicle segment yields [].
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LegObservation:
    trip_id: str
    direction: str
    leg_index: int
    kind: str  # "access" | "transfer" | "ride:<source>:<route>" | "egress"
    duration_s: float
    distance_m: float
    gtfs_trip_id: str | None  # ride legs only
    source: str | None
    route_name: str | None
    scheduled_dep_s: int | None
    delta_s: float | None
    board_stop: str | None
    alight_stop: str | None


def decompose_trip(detail: dict) -> list[LegObservation]:
    segments = detail["segments"]
    legs_meta = detail["itinerary"]
    trip = detail["trip"]
    # A rail leg is a segment the matcher attributed to a scheduled train. The
    # heuristic never emits "train" — matched rail segments are mode "vehicle"
    # unless the user labeled them "train" — so rail-ness is "has a train
    # match", NOT mode. Non-rail segments (walk, drive-to-station, even an
    # unmatched vehicle) are lumped into the adjacent ground leg. A trip with
    # zero matched rail legs is not optimizable → [].
    # (itinerary and segments are parallel: get_trip builds the itinerary as a
    # per-segment list, so index i refers to the same atom in both.)
    rail_positions = [i for i, leg in enumerate(legs_meta) if leg.get("train")]
    if not rail_positions:
        return []

    out: list[LegObservation] = []
    leg_index = 0

    def _emit(kind, dur, dist, train=None):
        nonlocal leg_index
        out.append(
            LegObservation(
                trip_id=trip["trip_id"],
                direction=trip["direction"],
                leg_index=leg_index,
                kind=kind,
                duration_s=dur,
                distance_m=dist,
                gtfs_trip_id=(train or {}).get("gtfs_trip_id"),
                source=(train or {}).get("source"),
                route_name=(train or {}).get("route_name"),
                scheduled_dep_s=(train or {}).get("scheduled_dep_s"),
                delta_s=(train or {}).get("delta_s"),
                board_stop=(train or {}).get("board_stop"),
                alight_stop=(train or {}).get("alight_stop"),
            )
        )
        leg_index += 1

    cursor = 0
    for rail_i, pos in enumerate(rail_positions):
        # ground segments before this rail leg, after the previous rail leg
        ground = segments[cursor:pos]
        if ground:
            dur = sum(s["duration_s"] for s in ground)
            dist = sum(s["distance_m"] for s in ground)
            kind = "access" if rail_i == 0 else "transfer"
            _emit(kind, dur, dist)
        rail = segments[pos]
        _emit(
            f"ride:{legs_meta[pos]['train']['source']}:{legs_meta[pos]['train']['route_name']}",
            rail["duration_s"],
            rail["distance_m"],
            train=legs_meta[pos]["train"],
        )
        cursor = pos + 1
    # egress: ground segments after the last rail leg
    ground = segments[cursor:]
    if ground:
        _emit("egress", sum(s["duration_s"] for s in ground), sum(s["distance_m"] for s in ground))
    return out
