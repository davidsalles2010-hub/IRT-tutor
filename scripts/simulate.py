"""Simulation study: how well does the adaptive diagnostic recover ability?

Simulates students whose answers obey the Rasch model at a known true theta,
runs each through the real adaptive engine, and reports bias, RMSE, average
test length and 95% CI coverage.

Run:  python -m scripts.simulate
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adaptive import Session  # noqa: E402
from app.bank import load_bank  # noqa: E402
from app import irt  # noqa: E402

REPS = 200
TRUE_THETAS = [-2.0, -1.0, 0.0, 1.0, 2.0]


def run_one(bank, true_theta: float, rng: np.random.Generator):
    session = Session(bank=bank)
    while not session.is_done():
        q = session.select_next()
        p = float(irt.probability_correct(true_theta, q.b))
        correct = rng.random() < p
        choice = q.answer_index if correct else (q.answer_index + 1) % len(q.choices)
        session.record_answer(q.id, choice)
    est = session.final_estimate()
    lo, hi = est.confidence_interval(0.95)
    return est.theta, lo <= true_theta <= hi, session.num_answered


def main() -> None:
    bank = load_bank()
    rng = np.random.default_rng(2026)
    print(f"{REPS} simulated students per ability level\n")
    print(f"{'true θ':>7} {'mean θ̂':>8} {'bias':>7} {'RMSE':>6} {'CI cover':>9} {'avg len':>8}")
    for true_theta in TRUE_THETAS:
        results = [run_one(bank, true_theta, rng) for _ in range(REPS)]
        thetas = np.array([r[0] for r in results])
        coverage = np.mean([r[1] for r in results])
        length = np.mean([r[2] for r in results])
        bias = thetas.mean() - true_theta
        rmse = float(np.sqrt(np.mean((thetas - true_theta) ** 2)))
        print(f"{true_theta:>7.1f} {thetas.mean():>8.2f} {bias:>+7.2f} {rmse:>6.2f} {coverage:>8.0%} {length:>8.1f}")


if __name__ == "__main__":
    main()
