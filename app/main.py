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

import json
import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from . import adaptive, emails, report
from .adaptive import Session, SessionStore
from .auth import get_current_user, require_user, router as auth_router
from .bank import load_bank
from .db import get_db, init_db
from .models import DiagnosticResult, User

app = FastAPI(title="MathLens API", version="0.1.0")


@app.middleware("http")
async def no_cache_html(request, call_next):
    """Serve HTML with no-cache so redeploys reach browsers immediately.

    Static JS/CSS are cache-busted with ?v= query params; index.html itself
    must always be revalidated or clients can keep a stale shell around.
    """
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache"
    return response

BANK = load_bank()
STORE = SessionStore()

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app.include_router(auth_router)


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/api/config")
def config() -> dict[str, Any]:
    """Non-secret configuration the frontend needs."""
    return {
        "google_client_id": os.environ.get("GOOGLE_CLIENT_ID"),
        "email_enabled": emails.email_enabled(),
    }


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
def get_report(
    session_id: str,
    user: User | None = Depends(get_current_user),
    db: DbSession = Depends(get_db),
) -> dict[str, Any]:
    session = _get_session_or_404(session_id)
    if not session.is_done():
        raise HTTPException(status_code=409, detail="Diagnostic is not finished yet")
    result = report.build_report(session)

    # Logged-in users get the result saved to their profile (idempotent per
    # quiz session) — the raw material for per-user progress history.
    if user is not None:
        existing = db.scalar(select(DiagnosticResult).where(DiagnosticResult.quiz_session_id == session.id))
        if existing is None:
            db.add(DiagnosticResult(
                user_id=user.id,
                quiz_session_id=session.id,
                theta=result["ability"]["theta"],
                se=result["ability"]["se"],
                level=result["ability"]["level"],
                band=result["ability"]["band"],
                n_questions=result["n_questions"],
                n_correct=result["n_correct"],
                topics_json=json.dumps([
                    {"topic": t["topic"], "level": t["level"], "verdict": t["verdict"], "asked": t["asked"], "correct": t["correct"]}
                    for t in result["topics"] if t["asked"] > 0
                ]),
            ))
            db.commit()
        result["saved_to_profile"] = True

    return result


@app.get("/api/me/results")
def my_results(user: User = Depends(require_user), db: DbSession = Depends(get_db)) -> dict[str, Any]:
    """Per-user diagnostic history (newest first) — feeds future progress UI."""
    rows = db.scalars(
        select(DiagnosticResult)
        .where(DiagnosticResult.user_id == user.id)
        .order_by(DiagnosticResult.created_at.desc())
        .limit(100)
    ).all()
    return {
        "results": [
            {
                "id": r.id,
                "level": r.level,
                "band": r.band,
                "theta": r.theta,
                "se": r.se,
                "n_questions": r.n_questions,
                "n_correct": r.n_correct,
                "topics": json.loads(r.topics_json),
                "created_at": r.created_at.isoformat() + "Z",
            }
            for r in rows
        ]
    }


# Static frontend — mounted last so /api routes take precedence.
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
