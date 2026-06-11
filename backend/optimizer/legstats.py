"""Aggregate leg_observations into per-leg duration distributions.

access/egress: empirical minutes, prior = distance / walk_speed.
ride:<source>:<route>: scheduled ride seconds + the observed delay spread
(arrival lateness) for that route; prior centered on the schedule when unseen.
"""

from dataclasses import dataclass

from backend.optimizer.distributions import EmpiricalDistribution
from backend.optimizer.params import OptimizerParams


@dataclass
class LegModels:
    _by_kind: dict
    _params: OptimizerParams

    @classmethod
    def build(cls, observations: list[dict], params: OptimizerParams) -> "LegModels":
        by_kind: dict[tuple, list[dict]] = {}
        for o in observations:
            key = (o["direction"], o["kind"])
            by_kind.setdefault(key, []).append(o)
        return cls(_by_kind=by_kind, _params=params)

    def access(self, direction: str, distance_m: float | None = None) -> EmpiricalDistribution:
        return self._ground(direction, "access", distance_m)

    def egress(self, direction: str, distance_m: float | None = None) -> EmpiricalDistribution:
        return self._ground(direction, "egress", distance_m)

    def transfer(self, direction: str, distance_m: float | None = None) -> EmpiricalDistribution:
        return self._ground(direction, "transfer", distance_m)

    def _ground(self, direction: str, kind: str, distance_m: float | None) -> EmpiricalDistribution:
        obs = self._by_kind.get((direction, kind), [])
        samples = [o["duration_s"] for o in obs]
        if samples:
            prior_mean = sum(samples) / len(samples)
        elif distance_m is not None:
            prior_mean = distance_m / self._params.walk_speed_mps
        else:
            prior_mean = 300.0  # neutral 5-minute default
        return EmpiricalDistribution(
            samples=samples,
            prior_mean=prior_mean,
            prior_weight=self._params.prior_weight,
            prior_spread=prior_mean * self._params.access_spread_frac,
        )

    def ride(self, source: str, route_name: str, scheduled_ride_s: float) -> EmpiricalDistribution:
        # match any direction's ride for this source+route (rides are direction-symmetric)
        kind = f"ride:{source}:{route_name}"
        obs = [o for (d, k), lst in self._by_kind.items() if k == kind for o in lst]
        # observed arrival duration = scheduled ride + delay (delta_s ~ boarding
        # lateness; we treat it as a proxy for arrival spread around schedule)
        samples = [scheduled_ride_s + (o["delta_s"] or 0.0) for o in obs]
        return EmpiricalDistribution(
            samples=samples,
            prior_mean=scheduled_ride_s,
            prior_weight=self._params.prior_weight,
            prior_spread=self._params.ride_delay_spread_s,
        )
