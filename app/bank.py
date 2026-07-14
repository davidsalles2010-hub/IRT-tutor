"""Question bank loading.

The bank lives in data/questions.json. Difficulties are authored on a 1-10
scale and converted to the logit scale here (see irt.difficulty_to_logit).

TODO(human-review): the bank content and difficulty ratings are AI-authored
estimates and must be reviewed/calibrated before real students rely on them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import irt

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "questions.json"


@dataclass(frozen=True)
class Question:
    id: str
    topic: str
    difficulty: float          # authored 1-10 rating
    b: float                   # difficulty on the logit scale
    text: str
    choices: tuple[str, ...]
    answer_index: int
    explanation: str


class QuestionBank:
    def __init__(self, questions: list[Question], topic_labels: dict[str, str]):
        self.questions = {q.id: q for q in questions}
        self.topic_labels = topic_labels
        if len(self.questions) != len(questions):
            raise ValueError("Duplicate question ids in bank")

    def __len__(self) -> int:
        return len(self.questions)

    def get(self, question_id: str) -> Question:
        return self.questions[question_id]

    def all(self) -> list[Question]:
        return list(self.questions.values())


def load_bank(path: Path = DATA_PATH) -> QuestionBank:
    raw = json.loads(path.read_text(encoding="utf-8"))
    questions = []
    for item in raw["questions"]:
        if not (1 <= item["difficulty"] <= 10):
            raise ValueError(f"{item['id']}: difficulty out of range")
        if not (0 <= item["answer_index"] < len(item["choices"])):
            raise ValueError(f"{item['id']}: answer_index out of range")
        questions.append(
            Question(
                id=item["id"],
                topic=item["topic"],
                difficulty=float(item["difficulty"]),
                b=irt.difficulty_to_logit(item["difficulty"]),
                text=item["text"],
                choices=tuple(item["choices"]),
                answer_index=int(item["answer_index"]),
                explanation=item.get("explanation", ""),
            )
        )
    return QuestionBank(questions, raw["_meta"]["topics"])
