"""Plan 9.7 — length limits on the dashboard's free-text inputs.

The booking API (`/api/v1`) already bounds every field (Phase 7.3) and Telegram caps a patient's
message at 4096 chars, but the dashboard's own JSON models accepted unbounded strings — a compromised
session (or a fat-fingered paste) could store a multi-megabyte "note" or feed it to the AI. The
models now carry generous `max_length` caps: well above any real clinical note, low enough to refuse
an abusive payload with a 422.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.security

SAVE = "/api/treatment-notes/1/2026-01-01/09-00"
SEND = "/api/messages/send"


async def test_an_oversized_session_note_is_refused(authenticated_client) -> None:
    resp = await authenticated_client.post(SAVE, json={"session_notes": "x" * 50_000})
    assert resp.status_code == 422, "a 50k-char note is over the cap"


async def test_a_normal_note_is_not_a_validation_error(authenticated_client) -> None:
    resp = await authenticated_client.post(
        SAVE,
        json={
            "session_notes": "Patient reports better sleep. " * 20,
            "tongue_observation": "pale, thin white coat",
            "therapist_diagnosis": "Spleen qi deficiency",
        },
    )
    assert resp.status_code != 422, "a realistic note must pass validation"


async def test_an_oversized_message_is_refused(authenticated_client) -> None:
    resp = await authenticated_client.post(SEND, json={"patient_id": 1, "text": "x" * 10_000})
    assert resp.status_code == 422, "a message longer than Telegram allows is refused up front"


async def test_a_normal_message_is_not_a_validation_error(authenticated_client) -> None:
    resp = await authenticated_client.post(
        SEND, json={"patient_id": 999_999, "text": "See you next week!"}
    )
    assert resp.status_code != 422, "a normal reply must pass validation"


def test_the_models_declare_caps() -> None:
    """The caps are on the models themselves, so every endpoint using them inherits the limit."""
    from web.routers.api.messages import SendMessageIn
    from web.routers.api.treatment import CompleteSessionIn, RediagnoseIn, TreatmentNotesIn

    for model, field in (
        (TreatmentNotesIn, "session_notes"),
        (CompleteSessionIn, "session_notes"),
        (RediagnoseIn, "tongue_observation"),
        (SendMessageIn, "text"),
    ):
        meta = model.model_fields[field].metadata
        assert any(
            getattr(m, "max_length", None) for m in meta
        ), f"{model.__name__}.{field} uncapped"
