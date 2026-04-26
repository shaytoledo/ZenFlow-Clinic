"""
web/services/email_service.py
──────────────────────────────
Outbound email for cases where a patient is not on Telegram.

Uses standard SMTP via env vars:
    SMTP_HOST       e.g. smtp.gmail.com
    SMTP_PORT       e.g. 587
    SMTP_USER       login (full email)
    SMTP_PASSWORD   app password (NOT the regular account password)
    SMTP_FROM       optional — defaults to SMTP_USER
    SMTP_FROM_NAME  optional — defaults to "ZenFlow Clinic"

If `SMTP_HOST`/`SMTP_USER`/`SMTP_PASSWORD` are not set, `is_configured()`
returns False and `send_email()` raises `EmailNotConfigured` so the API can
report that cleanly to the UI.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)


class EmailNotConfigured(RuntimeError):
    """Raised when SMTP env vars are missing."""


def _config() -> dict:
    return {
        "host":      os.getenv("SMTP_HOST", "").strip(),
        "port":      int(os.getenv("SMTP_PORT", "587") or 587),
        "user":      os.getenv("SMTP_USER", "").strip(),
        "password":  os.getenv("SMTP_PASSWORD", ""),
        "from":      os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "")).strip(),
        "from_name": os.getenv("SMTP_FROM_NAME", "ZenFlow Clinic").strip(),
    }


def is_configured() -> bool:
    cfg = _config()
    return all([cfg["host"], cfg["user"], cfg["password"]])


def send_email(to: str, subject: str, body_text: str) -> None:
    """Send a plain-text email. Raises `EmailNotConfigured` if SMTP env is missing.

    Synchronous — call from a thread (`asyncio.to_thread(...)`).
    """
    cfg = _config()
    if not is_configured():
        raise EmailNotConfigured(
            "SMTP not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD in .env to enable email."
        )

    msg = EmailMessage()
    msg["From"]    = f'{cfg["from_name"]} <{cfg["from"]}>'
    msg["To"]      = to
    msg["Subject"] = subject
    msg.set_content(body_text)

    ctx = ssl.create_default_context()
    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as s:
        s.ehlo()
        s.starttls(context=ctx)
        s.login(cfg["user"], cfg["password"])
        s.send_message(msg)
    logger.info(f"Email sent to {to} (subject: {subject!r})")
