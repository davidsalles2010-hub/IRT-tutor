"""MathLens API — adaptive math diagnostic backed by a Rasch IRT model.

Endpoints
---------
POST /api/sessions                     start a diagnostic, returns first question
POST /api/sessions/{sid}/answers       submit an answer, returns next question or done
GET  /api/sessions/{sid}/report        full diagnostic report (only when finished)
GET  /api/health                       liveness probe

The static frontend in /frontend is served from the same app, so a single
process is a complete deployment. Correct answers are never sent to the
client during a session — only in the post-test report.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import adaptive, report
from .adaptive import Session, SessionStore
from .bank import load_bank

app = FastAPI(title="MathLens API", version="0.1.0")

BANK = load_bank()
STORE = SessionStore()

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class AnswerBody(BaseModel):
    question_id: str
    choice_index: int = Field(ge=0, le=7)


class RestoreAnswer(BaseModel):
    question_id: str
    choice_index: int = Field(ge=0, le=7)


class RestoreBody(BaseModel):
    answers: list[RestoreAnswer] = Field(max_length=25)


def _question_payload(session: Session) -> dict[str, Any]:
    q = session.current_question
    assert q is not None
    return {
        "id": q.id,
        "text": q.text,
        "choices": list(q.choices),
        "topic": q.topic,
        "topic_label": BANK.topic_labels.get(q.topic, q.topic),
        "number": session.num_answered + 1,
    }


def _progress_payload(session: Session) -> dict[str, Any]:
    return {
        "answered": session.num_answered,
        "min_questions": adaptive.MIN_QUESTIONS,
        "max_questions": adaptive.MAX_QUESTIONS,
    }


def _get_session_or_404(session_id: str) -> Session:
    session = STORE.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return session


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "bank_size": len(BANK)}


@app.post("/api/sessions", status_code=201)
def create_session() -> dict[str, Any]:
    session = STORE.create(BANK)
    session.select_next()
    return {
        "session_id": session.id,
        "question": _question_payload(session),
        "progress": _progress_payload(session),
    }


@app.post("/api/sessions/restore", status_code=201)
def restore_session(body: RestoreBody) -> dict[str, Any]:
    """Rebuild a session from the client-held answer history.

    The free hosting tier restarts the server process when it idles, which
    wipes the in-memory session store mid-diagnostic. The frontend keeps its
    own (question_id, choice_index) history and calls this to resume without
    losing the student's progress. Correctness is recomputed server-side.
    """
    try:
        session = adaptive.rebuild_session(BANK, [(a.question_id, a.choice_index) for a in body.answers])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    STORE.register(session)

    if session.is_done():
        return {"session_id": session.id, "done": True, "progress": _progress_payload(session)}
    session.select_next()
    return {
        "session_id": session.id,
        "done": False,
        "question": _question_payload(session),
        "progress": _progress_payload(session),
    }


@app.post("/api/sessions/{session_id}/answers")
def submit_answer(session_id: str, body: AnswerBody) -> dict[str, Any]:
    session = _get_session_or_404(session_id)
    if session.current_question is None:
        raise HTTPException(status_code=409, detail="Session is already complete")
    try:
        session.record_answer(body.question_id, body.choice_index)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if session.is_done():
        return {"done": True, "progress": _progress_payload(session)}

    session.select_next()
    return {
        "done": False,
        "question": _question_payload(session),
        "progress": _progress_payload(session),
    }


@app.get("/api/sessions/{session_id}/report")
def get_report(session_id: str) -> dict[str, Any]:
    session = _get_session_or_404(session_id)
    if not session.is_done():
        raise HTTPException(status_code=409, detail="Diagnostic is not finished yet")
    return report.build_report(session)


# Static frontend — mounted last so /api routes take precedence.
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
