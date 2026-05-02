"""
web/repositories/notification_repo.py
─────────────────────────────────────
SQL access for the `notifications` table — therapist-scoped alerts shown in the
bell-icon dropdown on the topbar.
"""
from __future__ import annotations


def _conn():
    from bot.db import get_db
    return get_db()


def create(
    therapist_id: str,
    kind: str,
    title: str,
    body: str = "",
    severity: str = "info",
    appointment_id: int | None = None,
    patient_id: int | None = None,
    patient_name: str = "",
    persistent: bool = False,
) -> int:
    """Insert a new notification. Returns the new id."""
    cur = _conn().execute(
        """INSERT INTO notifications
           (therapist_id, kind, severity, title, body, appointment_id, patient_id,
            patient_name, persistent)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (therapist_id, kind, severity, title, body, appointment_id, patient_id,
         patient_name, 1 if persistent else 0),
    )
    return int(cur.lastrowid)


def list_for_therapist(therapist_id: str, limit: int = 50) -> list[dict]:
    """Newest notifications first. Persistent unresolved alerts always rank first."""
    rows = _conn().execute(
        """SELECT * FROM notifications
           WHERE therapist_id=?
           ORDER BY (CASE WHEN persistent=1 AND resolved_at IS NULL THEN 0 ELSE 1 END),
                    created_at DESC
           LIMIT ?""",
        (therapist_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def unread_count(therapist_id: str) -> int:
    row = _conn().execute(
        """SELECT COUNT(*) AS n FROM notifications
           WHERE therapist_id=?
             AND read_at IS NULL
             AND (persistent=0 OR resolved_at IS NULL)""",
        (therapist_id,),
    ).fetchone()
    return int(row["n"]) if row else 0


def mark_read(therapist_id: str, notification_id: int | None = None) -> None:
    """Mark one (by id) or all notifications as read for the given therapist."""
    if notification_id is None:
        _conn().execute(
            "UPDATE notifications SET read_at=datetime('now') WHERE therapist_id=? AND read_at IS NULL",
            (therapist_id,),
        )
    else:
        _conn().execute(
            "UPDATE notifications SET read_at=datetime('now') WHERE id=? AND therapist_id=?",
            (notification_id, therapist_id),
        )


def resolve(therapist_id: str, notification_id: int) -> None:
    """Mark a persistent notification as resolved (no longer requires action)."""
    _conn().execute(
        "UPDATE notifications SET resolved_at=datetime('now'), read_at=COALESCE(read_at, datetime('now')) WHERE id=? AND therapist_id=?",
        (notification_id, therapist_id),
    )


def find_active_missing_contact(therapist_id: str, appointment_id: int) -> dict | None:
    """Return an unresolved missing-contact notification for this appointment, if any.

    Used to avoid creating duplicates when the therapist completes a session
    multiple times in a row without fixing the patient's contact details.
    """
    row = _conn().execute(
        """SELECT * FROM notifications
           WHERE therapist_id=? AND appointment_id=? AND kind='missing_contact'
             AND resolved_at IS NULL
           ORDER BY created_at DESC LIMIT 1""",
        (therapist_id, appointment_id),
    ).fetchone()
    return dict(row) if row else None
