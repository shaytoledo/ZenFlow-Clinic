"""
web/services/booking_service.py
────────────────────────────────
The one implementation of booking (Phase 7.3).

Everything that creates or cancels an appointment goes through here: the booking API
(`/api/v1`), and — once 7.3b rewires them — the Telegram flow and the dashboard. The order of
steps is the one BOT_AUDIT B4 settled on: the row claims the hour first, the calendar follows.

    validate → resolve the patient → check availability → insert the appointment
             → take the hour out of the availability calendar → queue the confirmation

Errors are `BookingError`, which carries the code and status the API answers with.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from bot.patient_bot.services.appointments import SlotTaken, save_appointment, set_gcal_event_id
from bot.patient_bot.services.availability import book_slot, get_available_hours, restore_slot
from web.services import audit
from zenflow import clock

logger = logging.getLogger(__name__)

SLOT_MINUTES = 60
MAX_RANGE_DAYS = 31
#: `appointments.source` values this service writes
SOURCES = ("api", "telegram", "whatsapp", "manual")


class BookingError(Exception):
    def __init__(self, code: str, detail: str, status: int = 409) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status


@dataclass(frozen=True)
class PatientSpec:
    """Who the appointment is for: a known patient, a channel identity, or a new name."""

    name: str = ""
    patient_id: int | None = None
    channel: str | None = None
    external_id: str | None = None
    phone: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class BookingRequest:
    therapist_id: str
    start_at: datetime
    patient: PatientSpec
    duration_min: int = SLOT_MINUTES
    #: the clinical summary stored on the appointment row
    summary: str = ""
    #: what the calendar event says, when it differs from the stored summary
    calendar_note: str = ""
    source: str = "api"
    send_confirmation: bool = True
    #: machine clients may only take hours the therapist published
    enforce_availability: bool = True
    intake_history: list[dict[str, Any]] = field(default_factory=list)


# ── reads ──
def local_parts(start_at: datetime) -> tuple[str, str]:
    """(clinic-local date, HH:MM) for an instant."""
    local = start_at.astimezone(clock.clinic_tz())
    return local.date().isoformat(), local.strftime("%H:%M")


def instant(local_date: str, local_time: str) -> str:
    """The canonical UTC instant of a clinic-local date and time."""
    return clock.to_iso(clock.parse_iso(f"{local_date}T{local_time}", naive_tz=clock.clinic_tz()))


def as_appointment(row: dict[str, Any]) -> dict[str, Any]:
    """One appointment row in the API's shape."""
    return {
        "id": int(row["id"]),
        "therapist_id": str(row["therapist_id"] or ""),
        "patient_id": int(row["patient_id"]),
        "patient_name": str(row["patient_name"] or ""),
        "start_at": instant(str(row["date"]), str(row["time"])),
        "local_date": str(row["date"]),
        "local_time": str(row["time"]),
        "duration_min": SLOT_MINUTES,
        "status": str(row["status"] or "active"),
        "source": str(row["source"] or "telegram"),
        "summary": row.get("summary") or None,
        "created_at": _created(row.get("created_at")),
    }


def _created(value: Any) -> str:
    """A canonical instant when we can make one; the stored text otherwise (legacy rows)."""
    if not value:
        return ""
    try:
        return clock.normalize(str(value))
    except Exception:
        return str(value)


def get(appointment_id: int) -> dict[str, Any] | None:
    from web.repositories import appointment_repo

    row = appointment_repo.get_by_id(appointment_id)
    return as_appointment(row) if row else None


def list_appointments(
    therapist_id: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    status: str = "active",
    patient_id: int | None = None,
) -> list[dict[str, Any]]:
    from bot.db import get_db

    sql = ["SELECT * FROM appointments WHERE therapist_id=?"]
    params: list[Any] = [therapist_id]
    if status != "all":
        sql.append("AND status=?")
        params.append(status)
    if from_date:
        sql.append("AND date >= ?")
        params.append(from_date)
    if to_date:
        sql.append("AND date <= ?")
        params.append(to_date)
    if patient_id is not None:
        sql.append("AND patient_id=?")
        params.append(int(patient_id))
    sql.append("ORDER BY date, time, id")
    rows = get_db().execute(" ".join(sql), params).fetchall()
    return [as_appointment(dict(r)) for r in rows]


async def availability(therapist_id: str, from_date: str, to_date: str) -> list[dict[str, Any]]:
    """Free slots between two clinic-local dates, as instants."""
    start, end = _parse_range(from_date, to_date)
    _therapist(therapist_id)
    slots: list[dict[str, Any]] = []
    day = start
    while day <= end:
        for hhmm in await get_available_hours(day, therapist_id):
            slots.append(
                {
                    "start_at": instant(day.isoformat(), hhmm),
                    "local_date": day.isoformat(),
                    "local_time": hhmm,
                    "duration_min": SLOT_MINUTES,
                }
            )
        day += timedelta(days=1)
    return slots


# ── writes ──
async def create(request: BookingRequest) -> dict[str, Any]:
    """Book an hour. Raises `BookingError`; the appointment row is the only source of truth."""
    _therapist(request.therapist_id)
    if request.duration_min != SLOT_MINUTES:
        raise BookingError(
            "validation_error", f"only {SLOT_MINUTES}-minute appointments exist", status=422
        )
    if request.source not in SOURCES:
        raise BookingError("validation_error", f"unknown source {request.source!r}", status=422)
    local_date, local_time = local_parts(request.start_at)
    day = date.fromisoformat(local_date)

    patient_id, patient_name = await asyncio.to_thread(_resolve_patient, request.patient)

    if request.enforce_availability:
        free = await get_available_hours(day, request.therapist_id)
        if local_time not in free:
            raise BookingError(
                "slot_unavailable", f"{local_date} {local_time} is not on offer", status=409
            )

    try:
        appointment_id = await asyncio.to_thread(
            save_appointment,
            patient_id=patient_id,
            patient_name=patient_name,
            day=day,
            time_slot=local_time,
            intake_history=request.intake_history,
            summary=request.summary,
            therapist_id=request.therapist_id,
            source=request.source,
            patient_phone=request.patient.phone or "",
            patient_email=request.patient.email or "",
        )
    except SlotTaken as e:
        raise BookingError(
            "slot_taken", f"{local_date} {local_time} is already booked", status=409
        ) from e

    # The row already holds the hour: a calendar failure must not lose the booking (B4).
    try:
        event_id = await book_slot(
            day=day,
            time_slot=local_time,
            patient_name=patient_name,
            summary=request.calendar_note or request.summary or f"Appointment for {patient_name}",
            therapist_id=request.therapist_id,
        )
        if event_id:
            await asyncio.to_thread(set_gcal_event_id, appointment_id, event_id)
    except Exception as e:  # noqa: BLE001 — the booking stands either way
        logger.warning(f"booking {appointment_id}: calendar not updated: {e}")

    if request.send_confirmation:
        await asyncio.to_thread(_queue_confirmation, appointment_id)

    appointment = await asyncio.to_thread(get, appointment_id)
    await asyncio.to_thread(
        audit.record, "appointment.created", "appointment", appointment_id, after=appointment
    )

    await asyncio.to_thread(_invalidate_caches)
    if appointment is None:  # pragma: no cover — the row was just inserted
        raise BookingError("booking_failed", "the appointment disappeared", status=500)
    logger.info(
        "booked appointment %s: %s %s %s (source=%s)",
        appointment_id,
        request.therapist_id,
        local_date,
        local_time,
        request.source,
    )
    return appointment


async def cancel(appointment_id: int) -> dict[str, Any]:
    """Soft-delete an appointment and hand its hour back. Cancelling twice is a no-op."""
    from web.repositories import appointment_repo

    row = await asyncio.to_thread(appointment_repo.get_by_id, appointment_id)
    if row is None:
        raise BookingError("unknown_appointment", "no such appointment", status=404)
    if row["status"] == "cancelled":
        return as_appointment(row)

    await asyncio.to_thread(appointment_repo.update_status, appointment_id, "cancelled")
    try:
        await restore_slot(
            date.fromisoformat(str(row["date"])),
            str(row["time"]),
            row["gcal_apt_event_id"],
            therapist_id=row["therapist_id"],
        )
    except Exception as e:  # noqa: BLE001 — the row is cancelled either way
        logger.warning(f"cancel {appointment_id}: calendar not updated: {e}")
    await asyncio.to_thread(_invalidate_caches)
    updated = await asyncio.to_thread(appointment_repo.get_by_id, appointment_id)
    appointment = as_appointment(updated or row)
    await asyncio.to_thread(
        audit.record,
        "appointment.cancelled",
        "appointment",
        appointment_id,
        before=as_appointment(dict(row)),
        after=appointment,
    )
    return appointment


# ── helpers ──
def _therapist(therapist_id: str) -> dict[str, Any]:
    from web.repositories import therapist_repo

    therapist = therapist_repo.get_by_id(therapist_id) if therapist_id else None
    if not therapist or not bool(therapist.get("active")):
        raise BookingError("unknown_therapist", "no such therapist", status=404)
    return therapist


def _resolve_patient(spec: PatientSpec) -> tuple[int, str]:
    from web.repositories import patient_repo

    name = (spec.name or "").strip()
    if spec.patient_id is not None:
        patient = patient_repo.get(int(spec.patient_id))
        if patient is None:
            raise BookingError("unknown_patient", "no such patient", status=404)
        patient_repo.update_contact(patient["id"], phone=spec.phone, email=spec.email)
        return int(patient["id"]), name or str(patient["full_name"] or "Patient")
    if spec.channel and spec.external_id:
        if spec.channel not in patient_repo.MESSAGING_CHANNELS:
            raise BookingError("validation_error", f"unknown channel {spec.channel!r}", status=422)
        patient_id = patient_repo.for_channel(spec.channel, spec.external_id, name)
        patient_repo.update_contact(patient_id, phone=spec.phone, email=spec.email)
        return patient_id, name or "Patient"
    if not name:
        raise BookingError("validation_error", "the patient needs a name", status=422)
    return patient_repo.create(name, phone=spec.phone, email=spec.email), name


def _queue_confirmation(appointment_id: int) -> None:
    """Best effort: a missing confirmation must never undo a booking."""
    from bot.services.booking_jobs import enqueue_confirmation

    try:
        enqueue_confirmation(appointment_id)
    except Exception:
        logger.exception("booking %s: confirmation not queued", appointment_id)


def _invalidate_caches() -> None:
    try:
        from bot.redis_client import get_sync_redis

        get_sync_redis().delete("zenflow:apts:all")
    except Exception:  # nosec B110 — a cold cache is not a failure
        pass


def _parse_range(from_date: str, to_date: str) -> tuple[date, date]:
    try:
        start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
    except ValueError as e:
        raise BookingError("validation_error", "dates must be YYYY-MM-DD", status=422) from e
    if end < start:
        raise BookingError("validation_error", "`to` is before `from`", status=422)
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise BookingError(
            "validation_error", f"at most {MAX_RANGE_DAYS} days per request", status=422
        )
    return start, end
