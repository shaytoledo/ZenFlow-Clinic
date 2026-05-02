"""
web/services/email_service.py
──────────────────────────────
Outbound email via Gmail API (OAuth2 only — no SMTP).

Each therapist connects their Gmail account once via Settings → "Connect Google".
The stored token (encrypted in SQLite) is used to send on their behalf.

If the token is expired or revoked, a persistent notification is created so the
therapist is prompted to reconnect their Google account.

Usage
─────
    from web.services.email_service import send_email

    # synchronous — call from asyncio.to_thread(...)
    send_email(
        therapist_id="t1",
        to="patient@example.com",
        subject="Your ZenFlow lifestyle recommendations",
        body_text="...",
    )
"""
from __future__ import annotations

import base64
import logging
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


class EmailNotConfigured(RuntimeError):
    """Raised when the therapist has not connected their Google account."""


class EmailSendError(RuntimeError):
    """Raised when Gmail API call fails (token revoked, quota, network, …)."""


# ── Core send ─────────────────────────────────────────────────────────────────

def send_email(
    therapist_id: str,
    to: str,
    subject: str,
    body_text: str,
) -> None:
    """Send *body_text* to *to* via the therapist's Gmail account.

    Synchronous — call from ``asyncio.to_thread(...)``.

    Raises
    ------
    EmailNotConfigured  — therapist has never connected Google
    EmailSendError      — Gmail API rejected the request (token revoked etc.)
    """
    from web.gcal import is_gmail_authenticated, get_gmail_service

    if not is_gmail_authenticated(therapist_id):
        raise EmailNotConfigured(
            f"Therapist {therapist_id!r} has not connected Google. "
            "Go to Settings → Connect Google to enable email delivery."
        )

    try:
        service = get_gmail_service(therapist_id)
    except Exception as e:
        _notify_reconnect(therapist_id)
        raise EmailNotConfigured(
            f"Could not load Gmail credentials for {therapist_id!r}: {e}"
        ) from e

    mime = MIMEText(body_text, "plain", "utf-8")
    mime["To"]      = to
    mime["Subject"] = subject
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("utf-8")

    try:
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        logger.info(f"Email sent (Gmail API) to {to!r} — {subject!r}")
    except Exception as e:
        err_str = str(e).lower()
        if any(k in err_str for k in ("invalid_grant", "token", "revoked", "expired", "unauthorized")):
            _notify_reconnect(therapist_id)
        raise EmailSendError(f"Gmail API send failed: {e}") from e


# ── Notification helper ───────────────────────────────────────────────────────

def _notify_reconnect(therapist_id: str) -> None:
    """Create a persistent alert asking the therapist to reconnect Google."""
    try:
        from web.repositories import notification_repo
        notification_repo.create(
            therapist_id=therapist_id,
            kind="gmail_token_expired",
            severity="error",
            title="Gmail disconnected — please reconnect Google",
            body=(
                "Your Google account token has expired or been revoked. "
                "Go to Settings → Connect Google to re-authorise and resume email delivery."
            ),
            appointment_id=None,
            patient_id=None,
            patient_name="",
            persistent=True,
        )
    except Exception as e:
        logger.warning(f"Could not create reconnect notification: {e}")


# ── Legacy SMTP check (kept so old callers get a clean error) ─────────────────

def is_configured() -> bool:
    """Always False — SMTP is no longer used."""
    return False


def _config() -> dict:
    """Stub for backward compatibility."""
    return {"host": "", "port": 587, "user": "", "from": "", "from_name": "ZenFlow Clinic"}
