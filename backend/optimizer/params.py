"""Optimizer tuning. Units: seconds, meters. Pure config, no I/O."""

from dataclasses import dataclass


@dataclass(frozen=True)
class OptimizerParams:
    prior_weight: float = 4.0  # synthetic prior samples blended into each leg
    walk_speed_mps: float = 1.3  # access/egress prior: distance / speed
    access_spread_frac: float = 0.25  # prior stddev as a fraction of prior mean
    ride_delay_spread_s: float = 180.0  # prior spread on rail arrival when few rides seen
    mc_iters: int = 2000  # Monte Carlo samples per itinerary
    mc_seed: int = 20260611  # fixed seed → reproducible recommendations
    min_transfer_s: float = 120.0  # minimum feasible transfer connection time
