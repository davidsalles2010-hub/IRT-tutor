"""Authentication: email/password + Google sign-in, cookie sessions.

Design notes
------------
- Sessions are server-side rows keyed by SHA-256 of an opaque token; the raw
  token lives in an HttpOnly SameSite=Lax cookie for 30 days (persistent
  across visits, revocable server-side).
- Passwords are argon2id hashes. Login/forgot endpoints are lightly rate
  limited in-memory per (ip, email).
- Google sign-in uses the Google Identity Services ID-token flow: the
  frontend obtains a credential, we verify signature + audience server-side.
  Google-only accounts have password_hash = NULL.
- Roles: "student" (default), "teacher", "admin". Emails listed in the
  ADMIN_EMAILS env var become admins at signup/login time. Teacher accounts
  are created as students for now and upgraded by an admin later (the
  teacher_students link table already exists for that roadmap).
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from . import emails, security
from .db import get_db
from .models import AuthSession, EmailVerification, PasswordReset, User

router = APIRouter(prefix="/api/auth", tags=["auth"])

SESSION_COOKIE = "mathlens_session"
SESSION_DAYS = 30
RESET_MINUTES = 30
VERIFY_DAYS = 7


def _cookie_secure() -> bool:
    # Secure cookies on Render (HTTPS); plain HTTP locally and in tests.
    return bool(os.environ.get("RENDER") or os.environ.get("COOKIE_SECURE"))


def _admin_emails() -> set[str]:
    return {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}


def _role_for(email: str) -> str:
    return "admin" if email.lower() in _admin_emails() else "student"


# ---------------------------------------------------------------------------
# Rate limiting (in-memory; per process — matches the single-instance deploy)
# ---------------------------------------------------------------------------

_attempts: dict[str, list[float]] = defaultdict(list)


def _rate_limit(key: str, limit: int = 10, window_s: int = 300) -> None:
    now = time.time()
    bucket = [t for t in _attempts[key] if now - t < window_s]
    bucket.append(now)
    _attempts[key] = bucket
    if len(bucket) > limit:
        raise HTTPException(status_code=429, detail="Too many attempts — try again in a few minutes.")


# ---------------------------------------------------------------------------
# Session helpers / dependencies
# ---------------------------------------------------------------------------

def _issue_session(db: DbSession, response: Response, user: User) -> None:
    raw, token_hash = security.new_token()
    db.add(AuthSession(
        token_hash=token_hash,
        user_id=user.id,
        expires_at=datetime.utcnow() + timedelta(days=SESSION_DAYS),
    ))
    db.commit()
    response.set_cookie(
        SESSION_COOKIE, raw,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        path="/",
    )


def get_current_user(request: Request, db: DbSession = Depends(get_db)) -> User | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    row = db.get(AuthSession, security.hash_token(raw))
    if row is None or row.expires_at < datetime.utcnow():
        return None
    return db.get(User, row.user_id)


def require_user(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="You need to be logged in for that.")
    return user


def _user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "email_verified": user.email_verified,
        "has_password": user.password_hash is not None,
        "created_at": user.created_at.isoformat() + "Z",
    }


def _check_origin(request: Request) -> None:
    """CSRF guard: state-changing auth calls must come from our own origin
    (SameSite=Lax cookies are the primary defence; this is belt-and-braces)."""
    origin = request.headers.get("origin")
    if not origin:
        return
    allowed = {emails.app_origin(), "http://localhost:8000", "http://127.0.0.1:8000", "http://testserver"}
    if origin.rstrip("/") not in allowed:
        raise HTTPException(status_code=403, detail="Cross-origin request rejected.")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SignupBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class ForgotBody(BaseModel):
    email: EmailStr


class ResetBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=8, max_length=200)


class VerifyBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class GoogleBody(BaseModel):
    credential: str = Field(min_length=10)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/signup", status_code=201)
def signup(body: SignupBody, request: Request, response: Response, db: DbSession = Depends(get_db)):
    _check_origin(request)
    email = body.email.lower()
    existing = db.scalar(select(User).where(User.email == email))
    if existing:
        raise HTTPException(status_code=409, detail="An account with that email already exists — log in instead.")

    user = User(
        email=email,
        name=body.name.strip(),
        password_hash=security.hash_password(body.password),
        role=_role_for(email),
    )
    db.add(user)
    db.commit()

    if emails.email_enabled():
        raw, token_hash = security.new_token()
        db.add(EmailVerification(token_hash=token_hash, user_id=user.id,
                                 expires_at=datetime.utcnow() + timedelta(days=VERIFY_DAYS)))
        db.commit()
        emails.send_verification(user.email, raw)

    _issue_session(db, response, user)
    return {"user": _user_payload(user)}


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, db: DbSession = Depends(get_db)):
    _check_origin(request)
    ip = request.client.host if request.client else "?"
    _rate_limit(f"login:{ip}:{body.email.lower()}")

    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not security.verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    # Keep the admin allow-list authoritative.
    role = _role_for(user.email)
    if role == "admin" and user.role != "admin":
        user.role = "admin"
        db.commit()

    _issue_session(db, response, user)
    return {"user": _user_payload(user)}


@router.post("/logout")
def logout(request: Request, response: Response, db: DbSession = Depends(get_db)):
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        row = db.get(AuthSession, security.hash_token(raw))
        if row:
            db.delete(row)
            db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User | None = Depends(get_current_user)):
    return {"user": _user_payload(user) if user else None}


@router.post("/forgot")
def forgot(body: ForgotBody, request: Request, db: DbSession = Depends(get_db)):
    _check_origin(request)
    ip = request.client.host if request.client else "?"
    _rate_limit(f"forgot:{ip}", limit=5)

    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user and user.password_hash is not None:
        raw, token_hash = security.new_token()
        db.add(PasswordReset(token_hash=token_hash, user_id=user.id,
                             expires_at=datetime.utcnow() + timedelta(minutes=RESET_MINUTES)))
        db.commit()
        emails.send_password_reset(user.email, raw)
    # Always the same response — don't reveal whether the email exists.
    return {"ok": True}


@router.post("/reset")
def reset(body: ResetBody, request: Request, db: DbSession = Depends(get_db)):
    _check_origin(request)
    row = db.get(PasswordReset, security.hash_token(body.token))
    if row is None or row.used or row.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="That reset link is invalid or has expired — request a new one.")

    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="That reset link is invalid or has expired — request a new one.")

    user.password_hash = security.hash_password(body.password)
    row.used = True
    # Revoke every existing login session for safety.
    for s in list(user.sessions):
        db.delete(s)
    db.commit()
    return {"ok": True}


@router.post("/verify-email")
def verify_email(body: VerifyBody, request: Request, db: DbSession = Depends(get_db)):
    _check_origin(request)
    row = db.get(EmailVerification, security.hash_token(body.token))
    if row is None or row.used or row.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="That verification link is invalid or has expired.")
    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="That verification link is invalid or has expired.")
    user.email_verified = True
    row.used = True
    db.commit()
    return {"ok": True}


@router.post("/resend-verification")
def resend_verification(request: Request, db: DbSession = Depends(get_db), user: User = Depends(require_user)):
    _check_origin(request)
    if user.email_verified:
        return {"ok": True}
    if not emails.email_enabled():
        raise HTTPException(status_code=400, detail="Email sending isn't configured on this deployment yet.")
    ip = request.client.host if request.client else "?"
    _rate_limit(f"verify:{ip}", limit=3)
    raw, token_hash = security.new_token()
    db.add(EmailVerification(token_hash=token_hash, user_id=user.id,
                             expires_at=datetime.utcnow() + timedelta(days=VERIFY_DAYS)))
    db.commit()
    emails.send_verification(user.email, raw)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Google sign-in (ID token flow)
# ---------------------------------------------------------------------------

import logging

log = logging.getLogger("mathlens.auth")


def verify_google_token(credential: str) -> dict:
    """Verify a Google ID token; separated for testability.

    Any failure is mapped to a clean HTTP error — this endpoint must never
    return a 500 (an unhandled 500 is what the frontend surfaces as the
    unhelpful "something went wrong"). Unexpected errors are logged so they
    can be diagnosed from the server logs.
    """
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    if not client_id:
        raise HTTPException(status_code=400, detail="Google sign-in isn't configured on this deployment.")

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
    except ImportError as exc:  # pragma: no cover - dependency/config issue
        log.error("Google auth libraries unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="Google sign-in is temporarily unavailable.")

    try:
        claims = id_token.verify_oauth2_token(credential, google_requests.Request(), client_id)
    except ValueError as exc:
        # Malformed/expired/wrong-audience token — the normal "bad token" path.
        log.info("Google token rejected: %s", exc)
        raise HTTPException(status_code=401, detail="Google sign-in failed — please try again.")
    except Exception as exc:  # network hiccup fetching Google certs, etc.
        log.exception("Unexpected error verifying Google token: %s", exc)
        raise HTTPException(status_code=502, detail="Couldn't reach Google to verify sign-in — please try again.")

    if not isinstance(claims, dict):
        raise HTTPException(status_code=401, detail="Google sign-in failed — please try again.")
    return claims


@router.post("/google")
def google_signin(body: GoogleBody, request: Request, response: Response, db: DbSession = Depends(get_db)):
    _check_origin(request)
    claims = verify_google_token(body.credential)
    sub = claims.get("sub")
    email = (claims.get("email") or "").lower()
    if not sub or not email:
        raise HTTPException(status_code=401, detail="Google sign-in failed — please try again.")

    user = db.scalar(select(User).where(User.google_sub == sub))
    if user is None:
        user = db.scalar(select(User).where(User.email == email))
        if user is not None:
            user.google_sub = sub  # link Google to the existing account
        else:
            user = User(
                email=email,
                name=claims.get("name") or email.split("@")[0],
                password_hash=None,
                role=_role_for(email),
                google_sub=sub,
            )
            db.add(user)
    if claims.get("email_verified"):
        user.email_verified = True
    db.commit()

    _issue_session(db, response, user)
    return {"user": _user_payload(user)}
