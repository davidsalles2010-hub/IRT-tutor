"""Structural validation + summary of the question bank.

Run:  python -m scripts.validate_bank
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bank import load_bank  # noqa: E402


def main() -> int:
    bank = load_bank()
    problems: list[str] = []

    for q in bank.all():
        if len(q.choices) != 4:
            problems.append(f"{q.id}: expected 4 choices, got {len(q.choices)}")
        if len(set(q.choices)) != len(q.choices):
            problems.append(f"{q.id}: duplicate choices")
        if not q.text.strip():
            problems.append(f"{q.id}: empty text")
        if not q.explanation.strip():
            problems.append(f"{q.id}: missing explanation")
        if q.topic not in bank.topic_labels:
            problems.append(f"{q.id}: unknown topic {q.topic!r}")

    by_topic = Counter(q.topic for q in bank.all())
    by_difficulty = Counter(int(q.difficulty) for q in bank.all())

    print(f"Bank size: {len(bank)} questions\n")
    print("Per topic:")
    for topic, label in bank.topic_labels.items():
        print(f"  {label:<22} {by_topic.get(topic, 0):>3}")
    print("\nPer difficulty (1-10):")
    for d in range(1, 11):
        print(f"  {d:>2}  {'#' * by_difficulty.get(d, 0)} ({by_difficulty.get(d, 0)})")

    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nStructure OK. NOTE: correctness of answers/difficulties still needs human review (see REVIEW.md).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
