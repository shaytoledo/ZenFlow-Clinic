import contextlib
import json
import logging
from datetime import date

from zenflow.clock import SQL_NOW

logger = logging.getLogger(__name__)


# ── public API ───────────────────────────────────────────────────────────────


class SlotTaken(Exception):
    """The hour was booked by someone else between showing it and saving (BOT_AUDIT B4)."""

    def __init__(self, therapist_id: str, day: date, time_slot: str) -> None:
        super().__init__(f"{therapist_id} {day.isoformat()} {time_slot} is already booked")
        self.therapist_id = therapist_id
        self.day = day
        self.time_slot = time_slot


def save_appointment(
    patient_id: int,
    patient_name: str,
    day: date,
    time_slot: str,
    intake_history: list[dict],
    summary: str,
    gcal_apt_event_id: str | None = None,
    therapist_id: str = "",
    source: str = "telegram",
    patient_phone: str = "",
    patient_email: str = "",
) -> int:
    """Save appointment to SQLite. Returns the appointment row ID.

    Raises `SlotTaken` when the therapist already has an active appointment at that hour: the
    hours were listed minutes ago and someone else may have taken this one since (BOT_AUDIT B4).
    The check and the insert share one `BEGIN IMMEDIATE` transaction, and the partial unique index
    `ux_appointments_active_slot` is the backstop for any writer that does not go through here.
    """
    import sqlite3

    from bot.db import get_db

    conn = get_db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        clash = conn.execute(
            """SELECT id FROM appointments
               WHERE therapist_id=? AND date=? AND time=? AND status='active'""",
            (therapist_id, day.isoformat(), time_slot),
        ).fetchone()
        if clash is not None:
            conn.execute("ROLLBACK")
            logger.info(
                f"Slot already taken: {therapist_id} {day.isoformat()} {time_slot} "
                f"(appointment {clash['id']}); refusing booking for patient {patient_id}"
            )
            raise SlotTaken(therapist_id, day, time_slot)
        cur = conn.execute(
            f"""INSERT INTO appointments
               (patient_id, patient_name, therapist_id, date, time, status, gcal_apt_event_id,
                summary, source, patient_phone, patient_email, created_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, {SQL_NOW})""",
            (
                patient_id,
                patient_name,
                therapist_id,
                day.isoformat(),
                time_slot,
                gcal_apt_event_id,
                summary,
                source,
                patient_phone,
                patient_email,
            ),
        )
        appointment_id = cur.lastrowid
        conn.execute(
            f"""INSERT INTO intake_sessions
               (appointment_id, patient_id, therapist_id, history_json, created_at)
               VALUES (?, ?, ?, ?, {SQL_NOW})""",
            (
                appointment_id,
                patient_id,
                therapist_id,
                json.dumps(intake_history, ensure_ascii=False),
            ),
        )
        conn.execute("COMMIT")
    except SlotTaken:
        raise
    except sqlite3.IntegrityError as e:
        conn.execute("ROLLBACK")
        if "ux_appointments_active_slot" in str(e):
            raise SlotTaken(therapist_id, day, time_slot) from e
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise
    logger.info(f"Appointment saved: id={appointment_id}, patient={patient_id}, {day} {time_slot}")

    # Invalidate cached appointment list
    try:
        from bot.redis_client import get_sync_redis

        get_sync_redis().delete("zenflow:apts:all")
    except Exception:
        pass

    return appointment_id


def set_gcal_event_id(appointment_id: int, gcal_apt_event_id: str | None) -> None:
    """Attach the calendar event to an appointment that is already saved.

    The appointment row is written first so it claims the slot; the calendar work happens after
    (BOT_AUDIT B4), and its id lands here.
    """
    if not gcal_apt_event_id:
        return
    from bot.db import get_db

    get_db().execute(
        "UPDATE appointments SET gcal_apt_event_id=? WHERE id=?",
        (gcal_apt_event_id, appointment_id),
    )


def update_appointment_summary(appointment_id: int, summary: str, history: list[dict]) -> None:
    """Update the summary and intake history after background AI processing completes."""
    from bot.db import get_db

    conn = get_db()
    conn.execute(
        "UPDATE appointments SET summary=? WHERE id=?",
        (summary, appointment_id),
    )
    conn.execute(
        "UPDATE intake_sessions SET history_json=? WHERE appointment_id=?",
        (json.dumps(history, ensure_ascii=False), appointment_id),
    )


def telegram_patient(user_id: int, full_name: str = "") -> int:
    """The patient behind a Telegram user — created on their first booking (Phase 7.2)."""
    from web.repositories import patient_repo

    return patient_repo.for_channel("telegram", user_id, full_name)


def find_telegram_patient(user_id: int) -> int | None:
    """The patient behind a Telegram user, or None if they never booked."""
    from web.repositories import patient_repo

    return patient_repo.find_by_channel("telegram", user_id)


def get_patient_appointments(patient_id: int) -> list[dict]:
    """Return all active appointments for a patient, sorted by date/time."""
    from bot.db import get_db

    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM appointments
           WHERE patient_id=? AND status='active'
           ORDER BY date, time""",
        (patient_id,),
    ).fetchall()
    result = [dict(row) for row in rows]
    logger.info(f"Found {len(result)} active appointments for patient {patient_id}")
    return result


def cancel_appointment(appointment_id: int) -> bool:
    """Mark an appointment as cancelled (record preserved for clinical history)."""
    from bot.db import get_db

    try:
        conn = get_db()
        conn.execute(
            "UPDATE appointments SET status='cancelled' WHERE id=?",
            (appointment_id,),
        )
        conn.commit()
        logger.info(f"Appointment {appointment_id} cancelled")
        # Invalidate cached appointment list
        try:
            from bot.redis_client import get_sync_redis

            get_sync_redis().delete("zenflow:apts:all")
        except Exception:
            pass
        return True
    except Exception as e:
        logger.error(f"Could not cancel appointment {appointment_id}: {e}")
        return False


def get_booked_slots(day: date) -> set[str]:
    """Return booked time slots for a given day (Redis-cached, 5 min TTL)."""
    import json as _json

    # Try sync Redis cache
    try:
        from bot.redis_client import get_sync_redis

        r = get_sync_redis()
        key = f"zenflow:slots:{day.isoformat()}"
        cached = r.get(key)
        if cached:
            return set(_json.loads(cached))
    except Exception:
        r = None
        key = None

    from bot.db import get_db

    conn = get_db()
    rows = conn.execute(
        "SELECT time FROM appointments WHERE date=? AND status='active'",
        (day.isoformat(),),
    ).fetchall()
    booked = {row[0] for row in rows}
    logger.debug(f"Booked slots on {day}: {booked}")

    if r and key is not None:
        with contextlib.suppress(Exception):
            r.set(key, _json.dumps(list(booked)), ex=300)

    return booked


def save_treatment_notes(appointment_id: int, patient_id: int, notes: dict) -> None:
    """Upsert treatment notes for an appointment.

    The row belongs to the appointment's patient (Phase 7.2); `patient_id` is only used when the
    appointment row is gone.
    """
    import json as _json

    from bot.db import get_db

    conn = get_db()
    conn.execute(
        """INSERT INTO treatment_notes
           (appointment_id, patient_id, tcm_pattern, treatment_principles,
            diagnosis_certainty, ai_suggested_points, ai_recommendations,
            tongue_observation, pulse_observation, session_notes, used_points,
            recommendations_sent_at, completed_at, created_at, updated_at)
           VALUES (?,COALESCE((SELECT patient_id FROM appointments WHERE id=?), ?),
                   ?,?,?,?,?,?,?,?,?,?,?,
                   strftime('%Y-%m-%dT%H:%M:%SZ','now'),strftime('%Y-%m-%dT%H:%M:%SZ','now'))
           ON CONFLICT(appointment_id) DO UPDATE SET
             tcm_pattern=COALESCE(excluded.tcm_pattern, tcm_pattern),
             treatment_principles=COALESCE(excluded.treatment_principles, treatment_principles),
             diagnosis_certainty=COALESCE(excluded.diagnosis_certainty, diagnosis_certainty),
             ai_suggested_points=COALESCE(excluded.ai_suggested_points, ai_suggested_points),
             ai_recommendations=COALESCE(excluded.ai_recommendations, ai_recommendations),
             tongue_observation=COALESCE(excluded.tongue_observation, tongue_observation),
             pulse_observation=COALESCE(excluded.pulse_observation, pulse_observation),
             session_notes=COALESCE(excluded.session_notes, session_notes),
             used_points=COALESCE(excluded.used_points, used_points),
             recommendations_sent_at=COALESCE(excluded.recommendations_sent_at, recommendations_sent_at),
             completed_at=COALESCE(excluded.completed_at, completed_at),
             updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')""",
        (
            appointment_id,
            appointment_id,
            patient_id,
            notes.get("tcm_pattern"),
            notes.get("treatment_principles"),
            (
                int(notes.get("diagnosis_certainty") or 0)
                if notes.get("diagnosis_certainty") is not None
                else None
            ),
            (
                _json.dumps(notes.get("ai_suggested_points"), ensure_ascii=False)
                if notes.get("ai_suggested_points") is not None
                else None
            ),
            (
                _json.dumps(notes.get("ai_recommendations"), ensure_ascii=False)
                if notes.get("ai_recommendations") is not None
                else None
            ),
            notes.get("tongue_observation"),
            notes.get("pulse_observation"),
            notes.get("session_notes"),
            (
                _json.dumps(notes.get("used_points"), ensure_ascii=False)
                if notes.get("used_points") is not None
                else None
            ),
            notes.get("recommendations_sent_at"),
            notes.get("completed_at"),
        ),
    )


def get_treatment_notes(appointment_id: int) -> dict | None:
    """Load treatment notes for an appointment, or None if not found."""
    import json as _json

    from bot.db import get_db

    conn = get_db()
    row = conn.execute(
        "SELECT * FROM treatment_notes WHERE appointment_id=?", (appointment_id,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    for key in ("ai_suggested_points", "ai_recommendations", "used_points"):
        val = d.get(key)
        if isinstance(val, str):
            try:
                d[key] = _json.loads(val)
            except Exception:
                d[key] = [] if key != "ai_recommendations" else {}
    return d
