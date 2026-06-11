"""A leg's duration distribution: observed samples blended with a prior.

Shrinkage model: the effective sample set is the observations PLUS
`prior_weight` synthetic samples placed at deterministic, evenly-spaced
quantile positions of a Gaussian prior (prior_mean, prior_spread).
With zero observations the distribution IS the prior; with many, observations
dominate. Quantiles use linear interpolation over the combined sorted samples.
No numpy — stdlib only, fully auditable.
"""

import random
import statistics
from dataclasses import dataclass, field


@dataclass
class EmpiricalDistribution:
    samples: list[float]
    prior_mean: float
    prior_weight: float
    prior_spread: float = 0.0
    _combined: list[float] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self):
        prior = []
        n = int(round(self.prior_weight))
        if n > 0:
            spread = (
                self.prior_spread if self.prior_spread > 0 else max(1.0, abs(self.prior_mean) * 0.1)
            )
            dist = statistics.NormalDist(self.prior_mean, spread)
            # n synthetic samples at symmetric, evenly-spaced quantile positions
            # ((i + 0.5)/n) — deterministic, median == prior_mean for any n.
            prior = [max(0.0, dist.inv_cdf((i + 0.5) / n)) for i in range(n)]
        self._combined = sorted([float(s) for s in self.samples] + prior)

    @property
    def observed_count(self) -> int:
        return len(self.samples)

    @property
    def observed_mean(self) -> float:
        return sum(self.samples) / len(self.samples) if self.samples else self.prior_mean

    def quantile(self, q: float) -> float:
        xs = self._combined
        if not xs:
            return self.prior_mean
        if len(xs) == 1:
            return xs[0]
        pos = q * (len(xs) - 1)
        lo = int(pos)
        if lo >= len(xs) - 1:
            return xs[-1]
        frac = pos - lo
        return xs[lo] + frac * (xs[lo + 1] - xs[lo])

    def sample(self, rng: random.Random) -> float:
        """Draw one value via inverse-CDF on a uniform — deterministic per rng."""
        return max(0.0, self.quantile(rng.random()))
