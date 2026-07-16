"""Auth flow tests: signup, login, sessions, reset, verification, Google, roles."""

import re

import pytest
from fastapi.testclient import TestClient

from app import auth as auth_module
from app import emails
from app.main import app


def fresh_client() -> TestClient:
    return TestClient(app)


def signup(client, name="Dana Learner", email="dana@example.com", password="hunter2hunter2"):
    return client.post("/api/auth/signup", json={"name": name, "email": email, "password": password})


# ---------------------------------------------------------------------------
# Signup / login / logout / persistence
# ---------------------------------------------------------------------------

def test_signup_login_logout_and_session_persistence():
    client = fresh_client()

    r = signup(client)
    assert r.status_code == 201, r.text
    user = r.json()["user"]
    assert user["email"] == "dana@example.com"
    assert user["role"] == "student"          # default role
    assert user["has_password"] is True
    assert user["created_at"]

    # Session cookie was set and works — /me reports the logged-in user.
    assert client.get("/api/auth/me").json()["user"]["email"] == "dana@example.com"

    # "Persist across visits": a brand-new client with the same cookie works.
    cookie = client.cookies.get("mathlens_session")
    returning = fresh_client()
    returning.cookies.set("mathlens_session", cookie)
    assert returning.get("/api/auth/me").json()["user"]["email"] == "dana@example.com"

    # Logout revokes server-side: the same cookie is now dead everywhere.
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json()["user"] is None
    assert returning.get("/api/auth/me").json()["user"] is None

    # Log back in.
    r = client.post("/api/auth/login", json={"email": "dana@example.com", "password": "hunter2hunter2"})
    assert r.status_code == 200
    assert client.get("/api/auth/me").json()["user"]["name"] == "Dana Learner"


def test_duplicate_email_rejected():
    client = fresh_client()
    signup(client, email="dupe@example.com")
    r = signup(client, email="DUPE@example.com")  # case-insensitive
    assert r.status_code == 409


def test_wrong_password_and_unknown_user_are_generic_401():
    client = fresh_client()
    signup(client, email="real@example.com")
    a = client.post("/api/auth/login", json={"email": "real@example.com", "password": "wrong-password"})
    b = client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "wrong-password"})
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]


def test_admin_allow_list_role():
    client = fresh_client()
    r = signup(client, name="The Boss", email="boss@example.com")
    assert r.json()["user"]["role"] == "admin"


def test_password_validation():
    client = fresh_client()
    r = signup(client, email="short@example.com", password="short")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

def extract_token(html: str, param: str) -> str:
    m = re.search(rf"\?{param}=([A-Za-z0-9_\-]+)", html)
    assert m, f"no {param} token in email"
    return m.group(1)


def test_password_reset_flow():
    client = fresh_client()
    signup(client, email="resetme@example.com", password="originalpass1")
    client.post("/api/auth/logout")

    emails.OUTBOX.clear()
    r = client.post("/api/auth/forgot", json={"email": "resetme@example.com"})
    assert r.status_code == 200
    assert len(emails.OUTBOX) == 1
    token = extract_token(emails.OUTBOX[0]["html"], "reset")

    # Unknown emails get the same 200 and no mail.
    emails.OUTBOX.clear()
    assert client.post("/api/auth/forgot", json={"email": "ghost@example.com"}).status_code == 200
    assert emails.OUTBOX == []

    r = client.post("/api/auth/reset", json={"token": token, "password": "brandnewpass1"})
    assert r.status_code == 200, r.text

    # Old password dead, token single-use, new password works.
    assert client.post("/api/auth/login", json={"email": "resetme@example.com", "password": "originalpass1"}).status_code == 401
    assert client.post("/api/auth/reset", json={"token": token, "password": "anotherpass1"}).status_code == 400
    assert client.post("/api/auth/login", json={"email": "resetme@example.com", "password": "brandnewpass1"}).status_code == 200


def test_reset_revokes_existing_sessions():
    client = fresh_client()
    signup(client, email="hijacked@example.com", password="originalpass1")
    assert client.get("/api/auth/me").json()["user"] is not None

    emails.OUTBOX.clear()
    client.post("/api/auth/forgot", json={"email": "hijacked@example.com"})
    token = extract_token(emails.OUTBOX[0]["html"], "reset")
    client.post("/api/auth/reset", json={"token": token, "password": "safeagainpass1"})

    # The pre-reset session cookie no longer authenticates.
    assert client.get("/api/auth/me").json()["user"] is None


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------

def test_email_verification_flow(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "")  # keep dev mode
    client = fresh_client()

    # Force "email enabled" so signup issues a verification token.
    monkeypatch.setattr(emails, "email_enabled", lambda: True)
    real_send = emails.send_email
    monkeypatch.setattr(emails, "send_email", lambda to, s, h: (emails.OUTBOX.append({"to": to, "subject": s, "html": h}) or True))

    emails.OUTBOX.clear()
    r = signup(client, email="verifyme@example.com")
    assert r.status_code == 201
    assert r.json()["user"]["email_verified"] is False
    assert len(emails.OUTBOX) == 1
    token = extract_token(emails.OUTBOX[0]["html"], "verify")

    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 200
    assert client.get("/api/auth/me").json()["user"]["email_verified"] is True
    # Token is single-use.
    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 400


# ---------------------------------------------------------------------------
# Google sign-in (verification mocked; the endpoint logic is what we test)
# ---------------------------------------------------------------------------

def test_google_signin_creates_links_and_persists(monkeypatch):
    client = fresh_client()

    claims = {"sub": "google-sub-123", "email": "gina@example.com", "email_verified": True, "name": "Gina Google"}
    monkeypatch.setattr(auth_module, "verify_google_token", lambda cred: claims)

    r = client.post("/api/auth/google", json={"credential": "fake-jwt-for-test"})
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    assert user["email"] == "gina@example.com"
    assert user["role"] == "student"
    assert user["has_password"] is False
    assert user["email_verified"] is True

    # Same sub logs into the same account, not a duplicate.
    r2 = client.post("/api/auth/google", json={"credential": "fake-jwt-for-test"})
    assert r2.json()["user"]["id"] == user["id"]

    # Google links onto an existing email/password account.
    pw_client = fresh_client()
    signup(pw_client, email="linked@example.com", password="mypassword12")
    claims2 = {"sub": "google-sub-456", "email": "linked@example.com", "email_verified": True, "name": "Linked"}
    monkeypatch.setattr(auth_module, "verify_google_token", lambda cred: claims2)
    r3 = pw_client.post("/api/auth/google", json={"credential": "fake-jwt-2"})
    assert r3.json()["user"]["has_password"] is True  # same account, password kept


def test_google_unconfigured_returns_clear_error():
    client = fresh_client()
    r = client.post("/api/auth/google", json={"credential": "anything-at-all"})
    assert r.status_code == 400
    assert "configured" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Results persistence
# ---------------------------------------------------------------------------

def test_completed_diagnostic_saved_for_logged_in_user():
    from app.bank import load_bank
    from app import adaptive
    bank = load_bank()

    client = fresh_client()
    signup(client, email="saver@example.com")

    r = client.post("/api/sessions")
    sid = r.json()["session_id"]
    q = r.json()["question"]
    for _ in range(adaptive.MAX_QUESTIONS + 1):
        payload = client.post(
            f"/api/sessions/{sid}/answers",
            json={"question_id": q["id"], "choice_index": bank.get(q["id"]).answer_index},
        ).json()
        if payload["done"]:
            break
        q = payload["question"]

    rep = client.get(f"/api/sessions/{sid}/report").json()
    assert rep.get("saved_to_profile") is True

    results = client.get("/api/me/results").json()["results"]
    assert len(results) >= 1
    assert results[0]["level"] == rep["ability"]["level"]
    assert results[0]["topics"]

    # Anonymous users are not saved and can't list results.
    anon = fresh_client()
    assert anon.get("/api/me/results").status_code == 401
