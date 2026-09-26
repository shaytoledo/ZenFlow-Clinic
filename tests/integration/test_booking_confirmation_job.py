"""Phase 11.2 — the booking-confirmation job's skip and dead-letter branches.

The happy send path is exercised by `test_booking_api`; this pins the edges: the confirmation is
skipped when the appointment has vanished or been cancelled, and when every delivery attempt fails the
dead-letter handler records a *failed* confirmation so the therapist can see it never arrived.
"""

from __future__ import annotations

import pytest

from bot.services.booking_jobs import confirmation_dead, handle_confirmation

pytestmark = pytest.mark.integration


def _confirmations(apt_id: int) -> list[dict]:
    from bot.db import get_db

    rows = get_db().execute(
        "SELECT * FROM message_log WHERE appointment_id=? AND kind='confirmation'", (apt_id,)
    )
    return [dict(r) for r in rows]


async def test_confirmation_is_skipped_for_a_cancelled_appointment(make_appointment) -> None:
    apt = make_appointment(status="cancelled")
    await handle_confirmation({"appointment_id": apt["id"]})  # must not raise or send
    assert _confirmations(apt["id"]) == [], "a cancelled appointment gets no confirmation"


async def test_confirmation_is_skipped_for_a_missing_appointment() -> None:
    await handle_confirmation({"appointment_id": 999_999})  # no such row — a clean no-op


async def test_dead_letter_records_a_failed_confirmation(make_appointment) -> None:
    apt = make_appointment(status="active")
    await confirmation_dead({"appointment_id": apt["id"]}, "provider unreachable")
    rows = _confirmations(apt["id"])
    assert (
        rows and rows[-1]["status"] == "failed"
    ), "the undelivered confirmation is recorded failed"


async def test_dead_letter_for_a_missing_appointment_is_a_noop() -> None:
    await confirmation_dead({"appointment_id": 999_999}, "x")  # no row — nothing recorded, no raise
