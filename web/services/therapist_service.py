"""
web/services/therapist_service.py
──────────────────────────────────
CRUD abstraction for the `therapists` table.
All direct SQLite access for therapist data goes through here.
"""

import hashlib
import logging
import secrets

logger = logging.getLogger(__name__)


# ── Password helpers ───────────────────────────────────────────────────────────


def hash_password(plain: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), 260_000)
    return f"{salt}:{h.hex()}"


def verify_password(plain: str, stored: str) -> bool:
    try:
        salt, h = stored.split(":", 1)
        expected = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), 260_000).hex()
        return secrets.compare_digest(expected, h)
    except Exception:
        return False


# ── Query helpers ──────────────────────────────────────────────────────────────


def _row_to_dict(row) -> dict:
    if row is None:
        return None
    d = dict(row)
    d["active"] = bool(d.get("active"))
    return d


def list_all() -> list[dict]:
    """Return all therapists from SQLite."""
    from bot.db import get_db

    rows = get_db().execute("SELECT * FROM therapists").fetchall()
    return [_row_to_dict(r) for r in rows]


def find_by_id(therapist_id: str) -> dict | None:
    from bot.db import get_db

    row = get_db().execute("SELECT * FROM therapists WHERE id=?", (therapist_id,)).fetchone()
    return _row_to_dict(row)


def find_by_email(email: str) -> dict | None:
    email_lower = (email or "").lower().strip()
    if not email_lower:
        return None
    from bot.db import get_db

    row = (
        get_db().execute("SELECT * FROM therapists WHERE lower(email)=?", (email_lower,)).fetchone()
    )
    return _row_to_dict(row)


def find_by_google_id(google_id: str) -> dict | None:
    if not google_id:
        return None
    from bot.db import get_db

    row = get_db().execute("SELECT * FROM therapists WHERE google_id=?", (google_id,)).fetchone()
    return _row_to_dict(row)


def set_active(therapist_id: str, active: bool) -> None:
    from bot.db import get_db

    conn = get_db()
    conn.execute(
        "UPDATE therapists SET active=? WHERE id=?",
        (1 if active else 0, therapist_id),
    )
    conn.commit()
