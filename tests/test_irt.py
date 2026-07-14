"""Unit tests for the Rasch IRT engine."""

import numpy as np
import pytest

from app import irt


def simulate_responses(theta: float, bs, rng) -> list[int]:
    p = irt.probability_correct(theta, bs)
    return (rng.random(len(bs)) < p).astype(int).tolist()


# ---------------------------------------------------------------------------
# Model basics
# ---------------------------------------------------------------------------

def test_probability_is_half_when_ability_matches_difficulty():
    assert irt.probability_correct(0.7, 0.7) == pytest.approx(0.5)


def test_probability_monotone_in_ability():
    b = 0.0
    thetas = np.linspace(-3, 3, 25)
    probs = [float(irt.probability_correct(t, b)) for t in thetas]
    assert all(a < b_ for a, b_ in zip(probs, probs[1:]))


def test_information_peaks_where_difficulty_matches_ability():
    theta = 0.5
    at_match = irt.item_information(theta, theta)
    assert at_match == pytest.approx(0.25)
    assert at_match > irt.item_information(theta, theta + 1.0)
    assert at_match > irt.item_information(theta, theta - 1.0)


def test_difficulty_scale_mapping():
    assert irt.difficulty_to_logit(1) == pytest.approx(-3.0)
    assert irt.difficulty_to_logit(5.5) == pytest.approx(0.0)
    assert irt.difficulty_to_logit(10) == pytest.approx(3.0)
    for d in (1, 3.5, 7, 10):
        assert irt.logit_to_difficulty(irt.difficulty_to_logit(d)) == pytest.approx(d)


# ---------------------------------------------------------------------------
# Maximum likelihood estimation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("true_theta", [-1.5, 0.0, 1.7])
def test_mle_recovers_true_ability_with_many_items(true_theta):
    rng = np.random.default_rng(42)
    bs = rng.uniform(-3, 3, size=600)
    responses = simulate_responses(true_theta, bs, rng)
    est = irt.estimate_theta_mle(bs, responses)
    assert est.theta == pytest.approx(true_theta, abs=0.2)
    assert est.method == "mle"


def test_mle_matches_analytic_optimum_on_tiny_case():
    # One item at b=0: right + wrong on two items at b=0 -> MLE is exactly 0.
    est = irt.estimate_theta_mle([0.0, 0.0], [1, 0])
    assert est.theta == pytest.approx(0.0, abs=1e-3)


def test_estimate_theta_falls_back_to_map_for_all_correct():
    est = irt.estimate_theta([-1.0, 0.0, 1.0], [1, 1, 1])
    assert est.method == "map"
    assert irt.THETA_MIN < est.theta < irt.THETA_MAX
    assert np.isfinite(est.se)
    # All-correct should still push the estimate clearly above the prior mean.
    assert est.theta > 0.5


def test_estimate_theta_falls_back_to_map_for_all_wrong():
    est = irt.estimate_theta([-1.0, 0.0, 1.0], [0, 0, 0])
    assert est.method == "map"
    assert est.theta < -0.5


def test_standard_error_shrinks_with_more_items():
    bs_small = [0.0] * 5
    bs_large = [0.0] * 20
    assert irt.standard_error(0.0, bs_large) < irt.standard_error(0.0, bs_small)
    # 20 perfectly-targeted items: I = 20 * 0.25 = 5 -> SE = 1/sqrt(5)
    assert irt.standard_error(0.0, bs_large) == pytest.approx(1 / np.sqrt(5))


def test_confidence_interval_is_symmetric_and_ordered():
    est = irt.ThetaEstimate(theta=0.8, se=0.4, method="mle")
    lo, hi = est.confidence_interval()
    assert lo < 0.8 < hi
    assert hi - 0.8 == pytest.approx(0.8 - lo)
    assert hi - lo == pytest.approx(2 * 1.959964 * 0.4, abs=1e-3)


def test_ci_coverage_on_simulated_students():
    """~95% CI should cover the true ability for most simulated students."""
    rng = np.random.default_rng(7)
    covered = 0
    n_students = 120
    for _ in range(n_students):
        true_theta = rng.uniform(-2, 2)
        bs = rng.uniform(true_theta - 1.5, true_theta + 1.5, size=18)
        responses = simulate_responses(true_theta, bs, rng)
        est = irt.estimate_theta(bs, responses)
        lo, hi = est.confidence_interval()
        covered += lo <= true_theta <= hi
    assert covered / n_students >= 0.85


def test_expected_correct_bounds():
    bs = [-1.0, 0.0, 1.0]
    assert 0 < irt.expected_correct(0.0, bs) < 3
    assert irt.expected_correct(4.0, bs) > irt.expected_correct(-4.0, bs)
