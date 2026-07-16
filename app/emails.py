"""Transactional email via Resend, with a dev-mode fallback.

Without RESEND_API_KEY configured, emails are appended to OUTBOX (used by
tests) and logged — flows behave identically, nothing is delivered.

TODO(production): verify a sending domain in Resend and set MAIL_FROM;
the default onboarding@resend.dev sender can only deliver to the Resend
account owner's own address.
"""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("mathlens.email")

OUTBOX: list[dict] = []  # dev-mode capture (also used by the test suite)


def email_enabled() -> bool:
    return bool(os.environ.get("RESEND_API_KEY"))


def app_origin() -> str:
    return os.environ.get("APP_ORIGIN", "http://localhost:8000").rstrip("/")


def send_email(to: str, subject: str, html: str) -> bool:
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        OUTBOX.append({"to": to, "subject": subject, "html": html})
        log.info("Email (dev mode, not delivered) to=%s subject=%r", to, subject)
        return False
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": os.environ.get("MAIL_FROM", "MathLens <onboarding@resend.dev>"),
                "to": [to],
                "subject": subject,
                "html": html,
            },
            timeout=10,
        )
        if resp.status_code >= 400:
            log.warning("Resend error %s: %s", resp.status_code, resp.text[:300])
            return False
        return True
    except httpx.HTTPError as exc:
        log.warning("Resend request failed: %s", exc)
        return False


def _button(url: str, label: str) -> str:
    return (
        f'<a href="{url}" style="display:inline-block;background:#1E5C41;color:#ffffff;'
        f'padding:12px 22px;border-radius:10px;text-decoration:none;font-weight:600">{label}</a>'
    )


def send_password_reset(to: str, raw_token: str) -> bool:
    url = f"{app_origin()}/?reset={raw_token}"
    html = (
        "<div style='font-family:sans-serif;max-width:480px'>"
        "<h2>Reset your MathLens password</h2>"
        "<p>Someone (hopefully you) asked to reset the password for this account. "
        "The link below is valid for 30 minutes.</p>"
        f"<p>{_button(url, 'Choose a new password')}</p>"
        f"<p style='color:#5A6A61;font-size:13px'>Or paste this into your browser:<br>{url}</p>"
        "<p style='color:#5A6A61;font-size:13px'>If you didn't ask for this, you can ignore it.</p>"
        "</div>"
    )
    return send_email(to, "Reset your MathLens password", html)


def send_verification(to: str, raw_token: str) -> bool:
    url = f"{app_origin()}/?verify={raw_token}"
    html = (
        "<div style='font-family:sans-serif;max-width:480px'>"
        "<h2>Verify your email</h2>"
        "<p>Welcome to MathLens! Confirm this is your email address:</p>"
        f"<p>{_button(url, 'Verify my email')}</p>"
        f"<p style='color:#5A6A61;font-size:13px'>Or paste this into your browser:<br>{url}</p>"
        "</div>"
    )
    return send_email(to, "Verify your email for MathLens", html)
