"""End-to-end API tests: run whole diagnostics through the HTTP layer."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import adaptive, irt
from app.bank import load_bank
from app.main import app

client = TestClient(app)
BANK = load_bank()


def run_session(pick_choice) -> dict:
    """Drive a full session; pick_choice(question_dict) -> choice index."""
    r = client.post("/api/sessions")
    assert r.status_code == 201
    data = r.json()
    sid = data["session_id"]
    question = data["question"]

    for _ in range(adaptive.MAX_QUESTIONS + 1):
        r = client.post(
            f"/api/sessions/{sid}/answers",
            json={"question_id": question["id"], "choice_index": pick_choice(question)},
        )
        assert r.status_code == 200, r.text
        payload = r.json()
        if payload["done"]:
            break
        question = payload["question"]

    r = client.get(f"/api/sessions/{sid}/report")
    assert r.status_code == 200, r.text
    return r.json()


def correct_choice(question: dict) -> int:
    return BANK.get(question["id"]).answer_index


def wrong_choice(question: dict) -> int:
    right = BANK.get(question["id"]).answer_index
    return (right + 1) % len(question["choices"])


def test_all_correct_student_scores_high():
    report = run_session(correct_choice)
    assert report["n_correct"] == report["n_questions"]
    assert adaptive.MIN_QUESTIONS <= report["n_questions"] <= adaptive.MAX_QUESTIONS
    assert report["ability"]["level"] >= 8.0
    assert report["ability"]["method"] == "map"  # all-correct -> MAP fallback


def test_all_wrong_student_scores_low():
    report = run_session(wrong_choice)
    assert report["n_correct"] == 0
    assert report["ability"]["level"] <= 3.0


def test_simulated_average_student_is_recovered():
    """A Rasch-obedient student with theta=0.4 should land near level ~6."""
    rng = np.random.default_rng(11)
    true_theta = 0.4

    def rasch_choice(question: dict) -> int:
        q = BANK.get(question["id"])
        p = float(irt.probability_correct(true_theta, q.b))
        return q.answer_index if rng.random() < p else (q.answer_index + 1) % 4

    reports = [run_session(rasch_choice) for _ in range(8)]
    thetas = [r["ability"]["theta"] for r in reports]
    # Average estimate across sessions should be near the true ability.
    assert abs(float(np.mean(thetas)) - true_theta) < 0.45
    # And most individual CIs should cover it.
    covered = sum(r["ability"]["ci_theta"][0] <= true_theta <= r["ability"]["ci_theta"][1] for r in reports)
    assert covered >= 6


def test_report_structure_and_topic_coverage():
    report = run_session(correct_choice)
    assert {t["topic"] for t in report["topics"]} == set(BANK.topic_labels)
    assessed = [t for t in report["topics"] if t["asked"] > 0]
    # Content balancing should reach most of the 8 topics in 15-20 questions.
    assert len(assessed) >= 6
    assert len(report["review"]) == report["n_questions"]
    first = report["review"][0]
    assert {"text", "choices", "your_index", "correct_index", "explanation"} <= first.keys()
    assert report["disclaimer"]


def test_no_answer_leakage_during_session():
    r = client.post("/api/sessions")
    data = r.json()
    assert "answer_index" not in data["question"]
    assert "difficulty" not in data["question"]


def test_error_handling():
    assert client.get("/api/sessions/nope/report").status_code == 404
    assert client.post("/api/sessions/nope/answers", json={"question_id": "x", "choice_index": 0}).status_code == 404

    r = client.post("/api/sessions")
    sid = r.json()["session_id"]
    # Wrong question id -> 400
    bad = client.post(f"/api/sessions/{sid}/answers", json={"question_id": "wrong", "choice_index": 0})
    assert bad.status_code == 400
    # Report before finishing -> 409
    assert client.get(f"/api/sessions/{sid}/report").status_code == 409


def test_questions_never_repeat_within_session():
    seen = set()

    def tracking_choice(question: dict) -> int:
        assert question["id"] not in seen
        seen.add(question["id"])
        return 0

    run_session(tracking_choice)
    assert len(seen) >= adaptive.MIN_QUESTIONS
