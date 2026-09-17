"""
web/services/notification_service.py
────────────────────────────────────
Thin domain layer over notification_repo. Centralises the business rules for
creating typed alerts so callers don't need to know about the schema.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

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


#: a check-in answer met the red-flag rule (Phase 6.2)
FOLLOWUP_RED_FLAG = "followup_red_flag"


def alert_followup_red_flag(
    therapist_id: str,
    appointment_id: int,
    patient_id: int,
    patient_name: str,
    reasons: list[str],
) -> int:
    """High-severity, persistent, once per appointment: a patient's check-in needs attention."""
    existing = notification_repo.find_active(therapist_id, FOLLOWUP_RED_FLAG, appointment_id)
    if existing:
        return int(existing["id"])
    return notification_repo.create(
        therapist_id=therapist_id,
        kind=FOLLOWUP_RED_FLAG,
        severity="error",
        title=f"{patient_name} needs attention after their treatment",
        body=(
            "From the 24h check-in: "
            + "; ".join(reasons)
            + ". Please contact the patient and open the session."
        ),
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


#: a completed session's 24h check-in cannot be sent: the therapist calls instead (Phase 6.4)
FOLLOWUP_NO_CHANNEL = "followup_no_channel"
#: where each kind of alert takes the therapist inside the session page
_LINK_ANCHORS = {FOLLOWUP_NO_CHANNEL: "#manual-feedback-card"}


def alert_followup_no_channel(
    therapist_id: str,
    appointment_id: int,
    patient_id: int,
    patient_name: str,
    due_at: str,
) -> int | None:
    """Persistent: this patient cannot be messaged, so the 24h follow-up is a phone call.

    Raised once per appointment, ever — completing the session again or a reconciliation sweep
    does not bring back an alert the therapist already dealt with. Resolved when the outcome is
    recorded (`resolve_followup_no_channel`).
    """
    if notification_repo.ever_raised(therapist_id, FOLLOWUP_NO_CHANNEL, appointment_id):
        return None
    return notification_repo.create(
        therapist_id=therapist_id,
        kind=FOLLOWUP_NO_CHANNEL,
        severity="warning",
        title=f"Follow-up due for {patient_name} — no messaging channel",
        body=(
            f"Due {due_at}. Please call them and record the outcome in the session "
            "(Manual Patient Feedback)."
        ),
        appointment_id=appointment_id,
        patient_id=patient_id,
        patient_name=patient_name,
        persistent=True,
    )


def resolve_followup_no_channel(therapist_id: str, appointment_id: int) -> int:
    return notification_repo.resolve_kind(therapist_id, FOLLOWUP_NO_CHANNEL, appointment_id)


def session_link(patient_id: Any, apt_date: Any, apt_time: Any, anchor: str = "") -> str | None:
    """The treatment page of a session, or None when the parts are missing."""
    if patient_id is None or not apt_date or not apt_time:
        return None
    parts = (str(int(patient_id)), str(apt_date), str(apt_time).replace(":", "-"))
    return "/treatment/" + "/".join(quote(part, safe="") for part in parts) + anchor


def list_for_therapist(therapist_id: str, limit: int = 50) -> list[dict]:
    """The bell's items; those tied to one of the therapist's sessions carry a `link` to it."""
    items = []
    for row in notification_repo.list_for_therapist(therapist_id, limit):
        patient_id = row.pop("apt_patient_id", None)
        apt_date = row.pop("apt_date", None)
        apt_time = row.pop("apt_time", None)
        anchor = _LINK_ANCHORS.get(str(row.get("kind")), "")
        row["link"] = session_link(patient_id, apt_date, apt_time, anchor)
        items.append(row)
    return items


def unread_count(therapist_id: str) -> int:
    return notification_repo.unread_count(therapist_id)


def mark_read(therapist_id: str, notification_id: int | None = None) -> None:
    notification_repo.mark_read(therapist_id, notification_id)


def resolve(therapist_id: str, notification_id: int) -> None:
    notification_repo.resolve(therapist_id, notification_id)
