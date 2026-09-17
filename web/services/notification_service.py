"""
web/services/notification_service.py
────────────────────────────────────
Thin domain layer over notification_repo. Centralises the business rules for
creating typed alerts so callers don't need to know about the schema.
"""

from __future__ import annotations

import logging

from web.repositories import notification_repo

logger = logging.getLogger(__name__)


def alert_recommendations_sent(
    therapist_id: str,
    appointment_id: int,
    patient_id: int,
    patient_name: str,
    channel: str,  # "telegram" | "email"
    destination: str,  # patient id or email address
) -> int:
    """Patient successfully received recommendations."""
    return notification_repo.create(
        therapist_id=therapist_id,
        kind="recommendations_sent",
        severity="success",
        title=f"Recommendations sent to {patient_name}",
        body=f"Delivered via {channel.title()} → {destination}",
        appointment_id=appointment_id,
        patient_id=patient_id,
        patient_name=patient_name,
    )


def alert_recommendations_queued(
    therapist_id: str,
    appointment_id: int,
    patient_id: int,
    patient_name: str,
    send_at_iso: str,
) -> int:
    """Recommendations queued for delayed (24h) send."""
    return notification_repo.create(
        therapist_id=therapist_id,
        kind="recommendations_queued",
        severity="info",
        title=f"Recommendations queued for {patient_name}",
        body=f"Will be sent automatically at {send_at_iso}",
        appointment_id=appointment_id,
        patient_id=patient_id,
        patient_name=patient_name,
    )


def alert_missing_contact(
    therapist_id: str,
    appointment_id: int,
    patient_id: int,
    patient_name: str,
) -> int | None:
    """Persistent alert: patient has no Telegram and no email/phone on file.

    Skipped when an unresolved alert already exists for this appointment.
    """
    existing = notification_repo.find_active_missing_contact(therapist_id, appointment_id)
    if existing:
        return None
    return notification_repo.create(
        therapist_id=therapist_id,
        kind="missing_contact",
        severity="warning",
        title=f"Manual action required: Missing contact details for {patient_name}",
        body="Add a phone or email to send post-session recommendations.",
        appointment_id=appointment_id,
        patient_id=patient_id,
        patient_name=patient_name,
        persistent=True,
    )


def alert_send_failed(
    therapist_id: str,
    appointment_id: int,
    patient_id: int,
    patient_name: str,
    reason: str,
) -> int:
    """Recommendations failed to send (Telegram/email error). One open alert per appointment."""
    existing = notification_repo.find_active(therapist_id, "send_failed", appointment_id)
    if existing:
        return int(existing["id"])
    return notification_repo.create(
        therapist_id=therapist_id,
        kind="send_failed",
        severity="error",
        title=f"Failed to send recommendations to {patient_name}",
        body=reason[:300],
        appointment_id=appointment_id,
        patient_id=patient_id,
        patient_name=patient_name,
        persistent=True,
    )


#: queued recommendations for an email-only patient, waiting for the therapist's Google (5.4)
WAITING_FOR_GOOGLE = "recommendations_waiting_google"


def alert_waiting_for_google(
    therapist_id: str, appointment_id: int, patient_id: int, patient_name: str
) -> int:
    """Queued email recommendations cannot go out until Google is connected. One open alert per
    appointment, however often the job rechecks; resolved when they are delivered or dropped."""
    existing = notification_repo.find_active(therapist_id, WAITING_FOR_GOOGLE, appointment_id)
    if existing:
        return int(existing["id"])
    return notification_repo.create(
        therapist_id=therapist_id,
        kind=WAITING_FOR_GOOGLE,
        severity="warning",
        title=f"Recommendations for {patient_name} are waiting for Google",
        body=(
            "They go out by email through your Google account, which is not connected. "
            "Connect Google in Settings and they are sent right away."
        ),
        appointment_id=appointment_id,
        patient_id=patient_id,
        patient_name=patient_name,
        persistent=True,
    )


def resolve_waiting_for_google(therapist_id: str, appointment_id: int) -> int:
    return notification_repo.resolve_kind(therapist_id, WAITING_FOR_GOOGLE, appointment_id)


def list_for_therapist(therapist_id: str, limit: int = 50) -> list[dict]:
    return notification_repo.list_for_therapist(therapist_id, limit)


def unread_count(therapist_id: str) -> int:
    return notification_repo.unread_count(therapist_id)


def mark_read(therapist_id: str, notification_id: int | None = None) -> None:
    notification_repo.mark_read(therapist_id, notification_id)


def resolve(therapist_id: str, notification_id: int) -> None:
    notification_repo.resolve(therapist_id, notification_id)
