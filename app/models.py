"""Account and result models.

Designed for the roadmap without a rebuild:
- users.role ("student" | "teacher" | "admin") gates the future admin
  dashboard and teacher tools.
- teacher_students links one teacher to many students (and students to
  several teachers) — endpoints come later, the structure is ready.
- diagnostic_results stores one row per completed diagnostic per user,
  which is the per-user progress history.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

ROLES = ("student", "teacher", "admin")


def _uuid() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    # Nullable: Google-only accounts have no password until they set one.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="student")
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    sessions: Mapped[list["AuthSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    results: Mapped[list["DiagnosticResult"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class AuthSession(Base):
    """Server-side login sessions; the cookie holds the raw token, we store
    only its SHA-256 so a database leak can't be replayed."""

    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    user: Mapped[User] = relationship(back_populates="sessions")


class PasswordReset(Base):
    __tablename__ = "password_resets"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class EmailVerification(Base):
    __tablename__ = "email_verifications"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class TeacherStudent(Base):
    """Future feature scaffold: teachers linked to many students."""

    __tablename__ = "teacher_students"
    __table_args__ = (UniqueConstraint("teacher_id", "student_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    teacher_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class DiagnosticResult(Base):
    """One row per completed diagnostic for a logged-in user."""

    __tablename__ = "diagnostic_results"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    quiz_session_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    theta: Mapped[float] = mapped_column(Float, nullable=False)
    se: Mapped[float] = mapped_column(Float, nullable=False)
    level: Mapped[float] = mapped_column(Float, nullable=False)
    band: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    n_questions: Mapped[int] = mapped_column(Integer, nullable=False)
    n_correct: Mapped[int] = mapped_column(Integer, nullable=False)
    topics_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    user: Mapped[User] = relationship(back_populates="results")
