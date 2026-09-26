"""Phase 11.2 — the patient EHR pages (profile + read-only session archive).

`web/routers/patients.py` renders the full patient profile and a per-session archive, and redirects
away for an unknown patient, an unknown session, or an anonymous visitor. The happy render and those
redirect branches were partly uncovered (~82%). These tests drive them through the signed-in client.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.integration


def _tid(client: httpx.AsyncClient) -> str:
    return client.headers["X-Test-Therapist-Id"]


async def test_profile_renders_for_a_real_patient(
    authenticated_client: httpx.AsyncClient, make_appointment, make_patient
) -> None:
    apt = make_appointment(
        therapist={"id": _tid(authenticated_client)}, patient=make_patient("Pat")
    )
    resp = await authenticated_client.get(f"/patients/{apt['patient_id']}")
    assert resp.status_code == 200
    assert "Pat" in resp.text


async def test_profile_of_an_unknown_patient_redirects_to_the_list(
    authenticated_client: httpx.AsyncClient,
) -> None:
    resp = await authenticated_client.get("/patients/999999")
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/patients"


async def test_profile_requires_authentication(client: httpx.AsyncClient) -> None:
    resp = await client.get("/patients/1")
    assert resp.status_code in (302, 303, 307), "an anonymous visitor is redirected to sign in"


async def test_session_archive_requires_authentication(client: httpx.AsyncClient) -> None:
    resp = await client.get("/patients/1/session/1")
    assert resp.status_code in (302, 303, 307), "an anonymous visitor is redirected to sign in"


async def test_session_archive_of_an_unknown_patient_redirects_to_the_list(
    authenticated_client: httpx.AsyncClient,
) -> None:
    resp = await authenticated_client.get("/patients/999999/session/1")
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/patients"


async def test_session_archive_of_an_unknown_session_redirects_to_the_profile(
    authenticated_client: httpx.AsyncClient, make_appointment, make_patient
) -> None:
    apt = make_appointment(
        therapist={"id": _tid(authenticated_client)}, patient=make_patient("Pat")
    )
    missing = apt["id"] + 9999
    resp = await authenticated_client.get(f"/patients/{apt['patient_id']}/session/{missing}")
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == f"/patients/{apt['patient_id']}"


async def test_session_archive_renders_for_a_real_session(
    authenticated_client: httpx.AsyncClient, make_appointment, make_patient, make_treatment_notes
) -> None:
    apt = make_appointment(
        therapist={"id": _tid(authenticated_client)}, patient=make_patient("Pat")
    )
    make_treatment_notes(apt)
    resp = await authenticated_client.get(f"/patients/{apt['patient_id']}/session/{apt['id']}")
    assert resp.status_code == 200
    assert "Pat" in resp.text
