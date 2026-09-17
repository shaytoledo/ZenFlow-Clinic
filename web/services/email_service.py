"""
web/services/email_service.py
──────────────────────────────
Outbound email via Gmail API (OAuth2 only — no SMTP).

Each therapist connects their Gmail account once via Settings → "Connect Google".
The stored token (encrypted in SQLite) is used to send on their behalf.

If the token is expired or revoked, ONE persistent notification asks the therapist to reconnect;
a later successful send (or reconnecting) resolves it. `google_connection()` tells a page, before
any click, whether email can go out (Phase 5.2, docs/GOOGLE_CONNECTION_UX.md).

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
from dataclasses import dataclass
from email.mime.text import MIMEText

from google.auth.exceptions import RefreshError, TransportError

logger = logging.getLogger(__name__)

#: why email cannot go out: no Google account was ever linked / the stored token stopped working
NOT_CONNECTED = "not_connected"
TOKEN_INVALID = "token_invalid"  # noqa: S105  # nosec B105 - a reason code, not a secret
RECONNECT_KIND = "gmail_token_expired"

#: words in a Gmail API error that mean "the credentials were refused" (reconnect needed)
_AUTH_ERROR_MARKERS = (
    "invalid_grant",
    "invalid_credentials",
    "invalid credentials",
    "revoked",
    "unauthorized",
)


class EmailNotConfigured(RuntimeError):
    """Email cannot go out through the therapist's Google account.

    `reason` is NOT_CONNECTED (never linked → "Connect Google") or TOKEN_INVALID (the stored
    token no longer works → "Reconnect Google").
    """

    def __init__(self, message: str, reason: str = NOT_CONNECTED) -> None:
        super().__init__(message)
        self.reason = reason


class EmailSendError(RuntimeError):
    """Raised when Gmail API call fails (token revoked, quota, network, …).

    `token_invalid` is True when Google refused the credentials — reconnecting is the fix,
    retrying is not.
    """

    def __init__(self, message: str, token_invalid: bool = False) -> None:
        super().__init__(message)
        self.token_invalid = token_invalid


@dataclass(frozen=True)
class GoogleConnection:
    #: None = the check itself failed; the send still guards, so callers should not block on it
    connected: bool | None
    reason: str | None = None

    def as_dict(self) -> dict[str, bool | str | None]:
        return {"connected": self.connected, "reason": self.reason}


def google_connection(therapist_id: str) -> GoogleConnection:
    """Can email go out for this therapist right now? Never raises."""
    from web.gcal import is_gmail_authenticated
    from web.repositories import notification_repo

    try:
        if not is_gmail_authenticated(therapist_id):
            return GoogleConnection(False, NOT_CONNECTED)
        if notification_repo.find_active(therapist_id, RECONNECT_KIND):
            return GoogleConnection(False, TOKEN_INVALID)
        return GoogleConnection(True)
    except Exception as e:
        logger.warning(f"Google connection check failed for {therapist_id!r}: {e}")
        return GoogleConnection(None)


def _is_transient(error: Exception) -> bool:
    """Google could not be reached or asked us to retry — the token itself may be fine."""
    return isinstance(error, TransportError) or (
        isinstance(error, RefreshError) and bool(getattr(error, "retryable", False))
    )


def _is_auth_error(error: Exception) -> bool:
    if _is_transient(error):
        return False
    if isinstance(error, RefreshError):
        return True
    status = getattr(getattr(error, "resp", None), "status", None)
    if status == 401:
        return True
    text = str(error).lower()
    return any(marker in text for marker in _AUTH_ERROR_MARKERS)


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
    from web.gcal import get_gmail_service, is_gmail_authenticated

    if not is_gmail_authenticated(therapist_id):
        raise EmailNotConfigured(
            f"Therapist {therapist_id!r} has not connected Google. "
            "Go to Settings → Connect Google to enable email delivery.",
            reason=NOT_CONNECTED,
        )

    try:
        service = get_gmail_service(therapist_id)
    except Exception as e:
        if _is_transient(e):  # retry later; no reconnect prompt
            raise EmailSendError(f"Could not reach Google for {therapist_id!r}: {e}") from e
        _notify_reconnect(therapist_id)
        raise EmailNotConfigured(
            f"Could not load Gmail credentials for {therapist_id!r}: {e}",
            reason=TOKEN_INVALID,
        ) from e

    mime = MIMEText(body_text, "plain", "utf-8")
    mime["To"] = to
    mime["Subject"] = subject
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("utf-8")

    try:
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
    except Exception as e:
        token_invalid = _is_auth_error(e)
        if token_invalid:
            _notify_reconnect(therapist_id)
        raise EmailSendError(f"Gmail API send failed: {e}", token_invalid=token_invalid) from e
    logger.info(f"Email sent (Gmail API) to {to!r} — {subject!r}")
    google_reconnected(therapist_id)  # a working token proves any reconnect alert is stale


# ── Notification helper ───────────────────────────────────────────────────────


def _notify_reconnect(therapist_id: str) -> None:
    """Ask the therapist to reconnect Google — once, until that alert is resolved."""
    try:
        from web.repositories import notification_repo

        if notification_repo.find_active(therapist_id, RECONNECT_KIND):
            return
        notification_repo.create(
            therapist_id=therapist_id,
            kind=RECONNECT_KIND,
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


def google_reconnected(therapist_id: str) -> None:
    """The therapist's Google token works again (reconnected, or a send succeeded)."""
    try:
        from web.repositories import notification_repo

        if notification_repo.resolve_kind(therapist_id, RECONNECT_KIND):
            logger.info(f"Google reconnect alert resolved for {therapist_id!r}")
    except Exception as e:
        logger.warning(f"Could not resolve the reconnect notification: {e}")


# ── Legacy SMTP check (kept so old callers get a clean error) ─────────────────


def is_configured() -> bool:
    """Always False — SMTP is no longer used."""
    return False


def _config() -> dict:
    """Stub for backward compatibility."""
    return {"host": "", "port": 587, "user": "", "from": "", "from_name": "ZenFlow Clinic"}
