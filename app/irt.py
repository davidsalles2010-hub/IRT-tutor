"""Item Response Theory engine for MathLens — 1-parameter logistic (Rasch) model.

The Rasch model says the probability a student with ability ``theta`` answers
an item of difficulty ``b`` correctly is::

    P(correct) = 1 / (1 + exp(-(theta - b)))

Both ``theta`` and ``b`` live on the same "logit" scale (roughly -3..+3).
When theta == b the student has a 50% chance — that is where an item is most
informative, which is what drives the adaptive selection in adaptive.py.

Estimation strategy
-------------------
* ``estimate_theta_mle`` — maximum likelihood via scipy, as the final estimate.
  MLE is undefined when every response is correct (or every one is wrong):
  the likelihood keeps increasing toward +/- infinity.
* ``estimate_theta_map`` — Bayesian modal estimate with a weak Normal prior.
  Always finite, so it is used (a) as the running estimate that drives item
  selection early in a session and (b) as the fallback for all-same response
  patterns.

Standard errors come from Fisher information: SE = 1 / sqrt(I(theta)), where
for the Rasch model I(theta) = sum_i P_i (1 - P_i).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.stats import norm

# Bounds keep optimisation finite even for extreme response patterns.
THETA_MIN = -4.0
THETA_MAX = 4.0

# Weakly-informative prior over ability for MAP estimation. Ability in a
# calibrated Rasch model is usually scaled so the population is ~N(0, 1);
# sd=1.2 shrinks noisy early estimates without dominating the data.
PRIOR_MEAN = 0.0
PRIOR_SD = 1.2


# ---------------------------------------------------------------------------
# Difficulty scale
# ---------------------------------------------------------------------------
# Question authors rate difficulty on a friendly 1-10 scale. Internally we
# map it linearly onto the logit scale: 1 -> -3.0, 5.5 -> 0.0, 10 -> +3.0.
#
# TODO(human-review): these author-assigned difficulties are *estimates*.
# Before real use, calibrate them from real response data (e.g. fit the Rasch
# model to a pilot sample) and replace the 1-10 guesses with fitted values.

def difficulty_to_logit(difficulty: float) -> float:
    """Map an authored 1-10 difficulty rating onto the logit scale."""
    return (float(difficulty) - 5.5) * (2.0 / 3.0)


def logit_to_difficulty(b: float) -> float:
    """Inverse of :func:`difficulty_to_logit`."""
    return b * 1.5 + 5.5


# ---------------------------------------------------------------------------
# Core Rasch functions
# ---------------------------------------------------------------------------

def probability_correct(theta: float, b) -> np.ndarray | float:
    """P(correct | theta, b) under the Rasch model. ``b`` may be an array."""
    return 1.0 / (1.0 + np.exp(-(theta - np.asarray(b, dtype=float))))


def item_information(theta: float, b) -> np.ndarray | float:
    """Fisher information one item contributes at ability ``theta``.

    For the Rasch model this is P(1-P); it peaks (at 0.25) when b == theta,
    which is why the adaptive selector picks items near the current estimate.
    """
    p = probability_correct(theta, b)
    return p * (1.0 - p)


def test_information(theta: float, bs: Sequence[float]) -> float:
    """Total Fisher information of a set of administered items."""
    return float(np.sum(item_information(theta, np.asarray(bs, dtype=float))))


def standard_error(theta: float, bs: Sequence[float]) -> float:
    """Asymptotic standard error of the ability estimate."""
    info = test_information(theta, bs)
    return float(1.0 / np.sqrt(info)) if info > 0 else float("inf")


def neg_log_likelihood(theta: float, bs, responses) -> float:
    """Negative log-likelihood of a right/wrong response vector."""
    p = probability_correct(theta, bs)
    p = np.clip(p, 1e-10, 1.0 - 1e-10)
    x = np.asarray(responses, dtype=float)
    return float(-np.sum(x * np.log(p) + (1.0 - x) * np.log(1.0 - p)))


# ---------------------------------------------------------------------------
# Ability estimation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ThetaEstimate:
    theta: float
    se: float
    method: str  # "mle" or "map"

    def confidence_interval(self, level: float = 0.95) -> tuple[float, float]:
        z = float(norm.ppf(0.5 + level / 2.0))
        return (self.theta - z * self.se, self.theta + z * self.se)


def _minimize(objective) -> float:
    result = minimize_scalar(
        objective,
        bounds=(THETA_MIN, THETA_MAX),
        method="bounded",
        options={"xatol": 1e-5},
    )
    return float(result.x)


def estimate_theta_mle(bs: Sequence[float], responses: Sequence[int]) -> ThetaEstimate:
    """Maximum-likelihood ability estimate.

    Only well-defined for *mixed* response patterns (at least one right and
    one wrong answer); otherwise the optimum sits on the boundary. Callers
    should use :func:`estimate_theta`, which handles that case.
    """
    bs = np.asarray(bs, dtype=float)
    theta = _minimize(lambda t: neg_log_likelihood(t, bs, responses))
    return ThetaEstimate(theta=theta, se=standard_error(theta, bs), method="mle")


def estimate_theta_map(
    bs: Sequence[float],
    responses: Sequence[int],
    prior_mean: float = PRIOR_MEAN,
    prior_sd: float = PRIOR_SD,
) -> ThetaEstimate:
    """Bayesian modal (MAP) estimate with a Normal prior — always finite."""
    bs = np.asarray(bs, dtype=float)

    def objective(t: float) -> float:
        penalty = 0.5 * ((t - prior_mean) / prior_sd) ** 2
        return neg_log_likelihood(t, bs, responses) + penalty

    theta = _minimize(objective)
    # SE from test information plus the prior's information (1 / sd^2).
    info = test_information(theta, bs) + 1.0 / prior_sd**2
    return ThetaEstimate(theta=theta, se=float(1.0 / np.sqrt(info)), method="map")


def estimate_theta(bs: Sequence[float], responses: Sequence[int]) -> ThetaEstimate:
    """Best available ability estimate for a response vector.

    Uses MLE when the pattern is mixed (the estimator the report is built
    on); falls back to MAP when the student has answered everything right or
    everything wrong so far.
    """
    responses = list(responses)
    if 0 < sum(responses) < len(responses):
        return estimate_theta_mle(bs, responses)
    return estimate_theta_map(bs, responses)


def estimate_theta_running(bs: Sequence[float], responses: Sequence[int]) -> ThetaEstimate:
    """Estimate used *during* the session to drive item selection.

    MAP is deliberately used for every step: early in a session the MLE can
    swing wildly (or be undefined), and shrinking toward the prior keeps the
    difficulty of the next question reasonable.
    """
    return estimate_theta_map(bs, responses)


def expected_correct(theta: float, bs: Sequence[float]) -> float:
    """Expected number of correct answers on a set of items at ``theta``."""
    return float(np.sum(probability_correct(theta, np.asarray(bs, dtype=float))))
