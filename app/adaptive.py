"""Adaptive test session logic (computerized adaptive testing, CAT).

The loop:

1. Estimate ability from the answers so far (MAP estimate — stable even
   before we have both a right and a wrong answer).
2. Pick the next question that maximises Fisher information at the current
   estimate — for the Rasch model that is the unused item whose difficulty is
   closest to the estimate. Two refinements:
     * "randomesque" exposure control: choose randomly among the top few
       candidates so every student doesn't see the identical sequence;
     * light content balancing: among near-optimal items, prefer topics the
       student has seen least, so the final report covers all areas.
3. Stop after MAX_QUESTIONS, or once MIN_QUESTIONS have been asked *and* the
   standard error has dropped below SE_STOP.
"""

from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass, field

from . import irt
from .bank import Question, QuestionBank

MIN_QUESTIONS = 15
MAX_QUESTIONS = 20
SE_STOP = 0.48          # stop early once the ability estimate is this precise
TOP_K = 6               # candidate pool size for randomesque selection
STARTING_THETA = -0.33  # first question slightly below average difficulty


@dataclass
class Answered:
    question: Question
    choice_index: int
    correct: bool


@dataclass
class Session:
    bank: QuestionBank
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)
    answered: list[Answered] = field(default_factory=list)
    current_question: Question | None = None
    rng: random.Random = field(default_factory=random.Random)

    # -- ability ------------------------------------------------------------

    def _bs_xs(self) -> tuple[list[float], list[int]]:
        bs = [a.question.b for a in self.answered]
        xs = [1 if a.correct else 0 for a in self.answered]
        return bs, xs

    def running_estimate(self) -> irt.ThetaEstimate:
        if not self.answered:
            return irt.ThetaEstimate(theta=STARTING_THETA, se=irt.PRIOR_SD, method="map")
        return irt.estimate_theta_running(*self._bs_xs())

    def final_estimate(self) -> irt.ThetaEstimate:
        return irt.estimate_theta(*self._bs_xs())

    # -- state --------------------------------------------------------------

    @property
    def num_answered(self) -> int:
        return len(self.answered)

    def is_done(self) -> bool:
        if self.num_answered >= MAX_QUESTIONS:
            return True
        if self.num_answered >= len(self.bank):
            return True  # bank exhausted (shouldn't happen with 72 items)
        if self.num_answered >= MIN_QUESTIONS:
            return self.running_estimate().se <= SE_STOP
        return False

    # -- selection ----------------------------------------------------------

    def select_next(self) -> Question:
        """Pick the most informative unused question, with topic balancing."""
        theta = self.running_estimate().theta
        used = {a.question.id for a in self.answered}
        unused = [q for q in self.bank.all() if q.id not in used]

        # Top-K by Fisher information at the current ability estimate.
        unused.sort(key=lambda q: -irt.item_information(theta, q.b))
        pool = unused[:TOP_K]

        # Prefer topics we've sampled least so the report covers everything.
        counts = {t: 0 for t in self.bank.topic_labels}
        for a in self.answered:
            counts[a.question.topic] += 1
        least = min(counts[q.topic] for q in pool)
        pool = [q for q in pool if counts[q.topic] == least]

        choice = self.rng.choice(pool)
        self.current_question = choice
        return choice

    # -- answering ----------------------------------------------------------

    def record_answer(self, question_id: str, choice_index: int) -> bool:
        if self.current_question is None or self.current_question.id != question_id:
            raise ValueError("Answer does not match the current question")
        q = self.current_question
        if not (0 <= choice_index < len(q.choices)):
            raise ValueError("choice_index out of range")
        correct = choice_index == q.answer_index
        self.answered.append(Answered(question=q, choice_index=choice_index, correct=correct))
        self.current_question = None
        return correct


class SessionStore:
    """In-memory session store.

    TODO(production): replace with Redis or a database before running more
    than one server process — memory is per-process and lost on restart.
    """

    MAX_SESSIONS = 5000
    TTL_SECONDS = 6 * 3600

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self, bank: QuestionBank) -> Session:
        self._evict()
        session = Session(bank=bank)
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def _evict(self) -> None:
        now = time.time()
        expired = [sid for sid, s in self._sessions.items() if now - s.created_at > self.TTL_SECONDS]
        for sid in expired:
            del self._sessions[sid]
        while len(self._sessions) >= self.MAX_SESSIONS:
            oldest = min(self._sessions.values(), key=lambda s: s.created_at)
            del self._sessions[oldest.id]
