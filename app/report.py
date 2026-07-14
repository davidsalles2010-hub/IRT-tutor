"""Diagnostic report generation.

Overall ability comes from the IRT estimate (irt.estimate_theta). Per-topic
scores use *partial pooling*: each topic's ability is a MAP estimate whose
prior is centred on the student's overall ability. With only 2-4 items per
topic that shrinkage keeps topic estimates stable while still letting a
clearly weak or strong topic pull away from the overall level.

All numbers are reported on a friendly 1-10 "level" scale (the same scale
question difficulties are authored on).
"""

from __future__ import annotations

from typing import Any

from . import irt
from .adaptive import Session

# How strongly topic estimates are shrunk toward the overall ability.
TOPIC_PRIOR_SD = 0.7
# A topic this far (in logits) below/above overall ability is flagged.
FOCUS_GAP = 0.5
STRENGTH_GAP = 0.5

LEVEL_BANDS = [
    (3.0, "Building foundations", "Core arithmetic still needs strengthening before algebra will click."),
    (4.5, "Developing", "Pre-algebra basics are forming; keep reinforcing fractions, ratios and negatives."),
    (6.0, "On level", "Solid pre-algebra skills — ready to build fluency in Algebra 1 topics."),
    (7.5, "Proficient", "Comfortable with core Algebra 1; ready for more challenging problems."),
    (10.1, "Advanced", "Strong command of Algebra 1 — ready to move beyond it."),
]

TOPIC_TIPS = {
    "arithmetic": "Practice order of operations (PEMDAS) and signed-number rules — they underpin everything else.",
    "fractions": "Review adding fractions with unlike denominators, and multiplying and dividing fractions.",
    "ratios": "Practice translating percent problems into equations: part = percent × whole.",
    "exponents": "Drill the exponent rules (product, power, negative exponents) and square roots.",
    "linear_eq": "Practice two-step equations, then equations with variables on both sides.",
    "inequalities": "Remember the sign flips when multiplying or dividing by a negative; review absolute value.",
    "systems": "Review slope-intercept form, then substitution for solving systems.",
    "quadratics": "Practice factoring trinomials and applying the quadratic formula.",
}


def level_from_theta(theta: float) -> float:
    """Map logit ability onto the 1-10 level scale, clamped."""
    return round(min(10.0, max(1.0, irt.logit_to_difficulty(theta))), 1)


def band_for_level(level: float) -> tuple[str, str]:
    for upper, name, blurb in LEVEL_BANDS:
        if level < upper:
            return name, blurb
    return LEVEL_BANDS[-1][1], LEVEL_BANDS[-1][2]


def _topic_estimate(overall_theta: float, bs: list[float], xs: list[int]) -> float:
    est = irt.estimate_theta_map(bs, xs, prior_mean=overall_theta, prior_sd=TOPIC_PRIOR_SD)
    return est.theta


def build_report(session: Session) -> dict[str, Any]:
    est = session.final_estimate()
    lo, hi = est.confidence_interval(0.95)
    level = level_from_theta(est.theta)
    band, band_blurb = band_for_level(level)

    # ---- per-topic breakdown ------------------------------------------------
    by_topic: dict[str, list] = {}
    for a in session.answered:
        by_topic.setdefault(a.question.topic, []).append(a)

    topics = []
    for topic_id, label in session.bank.topic_labels.items():
        answered = by_topic.get(topic_id, [])
        if not answered:
            topics.append({
                "topic": topic_id,
                "label": label,
                "asked": 0,
                "correct": 0,
                "level": None,
                "verdict": "not_assessed",
                "tip": None,
            })
            continue

        bs = [a.question.b for a in answered]
        xs = [1 if a.correct else 0 for a in answered]
        topic_theta = _topic_estimate(est.theta, bs, xs)
        gap = topic_theta - est.theta

        if gap <= -FOCUS_GAP:
            verdict = "focus"
        elif gap >= STRENGTH_GAP:
            verdict = "strength"
        else:
            verdict = "on_track"

        topics.append({
            "topic": topic_id,
            "label": label,
            "asked": len(answered),
            "correct": sum(xs),
            "level": level_from_theta(topic_theta),
            "verdict": verdict,
            "tip": TOPIC_TIPS.get(topic_id),
        })

    focus_topics = [t for t in topics if t["verdict"] == "focus"]
    strength_topics = [t for t in topics if t["verdict"] == "strength"]

    # ---- question-by-question review ---------------------------------------
    review = []
    for i, a in enumerate(session.answered, start=1):
        q = a.question
        review.append({
            "number": i,
            "id": q.id,
            "topic": q.topic,
            "topic_label": session.bank.topic_labels.get(q.topic, q.topic),
            "difficulty": q.difficulty,
            "text": q.text,
            "choices": list(q.choices),
            "your_index": a.choice_index,
            "correct_index": q.answer_index,
            "correct": a.correct,
            "explanation": q.explanation,
        })

    return {
        "n_questions": session.num_answered,
        "n_correct": sum(1 for a in session.answered if a.correct),
        "ability": {
            "theta": round(est.theta, 3),
            "se": round(est.se, 3),
            "ci_theta": [round(lo, 3), round(hi, 3)],
            "level": level,
            "ci_level": [level_from_theta(lo), level_from_theta(hi)],
            "band": band,
            "band_description": band_blurb,
            "method": est.method,
        },
        "topics": topics,
        "focus_topics": [t["topic"] for t in focus_topics],
        "strength_topics": [t["topic"] for t in strength_topics],
        "review": review,
        "disclaimer": (
            "Topic-level results are indicative — an adaptive session asks only a few "
            "questions per topic. The question bank is awaiting expert review, so treat "
            "this as a research preview, not a formal assessment."
        ),
    }
