import random

from backend.optimizer.distributions import EmpiricalDistribution


def test_quantile_of_known_samples():
    d = EmpiricalDistribution(samples=[100, 200, 300, 400, 500], prior_mean=0, prior_weight=0)
    assert d.quantile(0.5) == 300
    assert d.quantile(0.0) == 100
    assert d.quantile(1.0) == 500
    # linear interpolation between order statistics
    assert 200 < d.quantile(0.4) < 300


def test_empty_samples_fall_back_to_prior():
    d = EmpiricalDistribution(samples=[], prior_mean=420.0, prior_weight=4, prior_spread=60.0)
    # with no observations the distribution is the prior: median ≈ prior mean
    assert abs(d.quantile(0.5) - 420.0) < 1.0
    assert d.quantile(0.9) > d.quantile(0.5) > d.quantile(0.1)


def test_shrinkage_blends_observations_toward_prior():
    # one extreme observation, heavy prior → median pulled toward prior
    light = EmpiricalDistribution(
        samples=[1000.0], prior_mean=300.0, prior_weight=0, prior_spread=30.0
    )
    heavy = EmpiricalDistribution(
        samples=[1000.0], prior_mean=300.0, prior_weight=8, prior_spread=30.0
    )
    assert heavy.quantile(0.5) < light.quantile(0.5)
    assert heavy.quantile(0.5) < 1000.0


def test_sample_is_deterministic_under_seed():
    d = EmpiricalDistribution(
        samples=[100, 200, 300], prior_mean=200, prior_weight=2, prior_spread=20.0
    )
    rng_a = random.Random(7)
    rng_b = random.Random(7)
    draws_a = [d.sample(rng_a) for _ in range(50)]
    draws_b = [d.sample(rng_b) for _ in range(50)]
    assert draws_a == draws_b
    assert all(x > 0 for x in draws_a)


def test_mean_and_count():
    d = EmpiricalDistribution(samples=[100, 200, 300], prior_mean=0, prior_weight=0)
    assert d.observed_count == 3
    assert abs(d.observed_mean - 200.0) < 1e-9
