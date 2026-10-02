"""Phase-0 review ticket (→ 9.1): every lookup by patient/date/time, and every slot delete, takes a
REQUIRED `therapist_id` — no default, no unscoped branch.

These functions used to accept `therapist_id=None` and quietly run unscoped. Every caller passed the
id, so nothing leaked — but the next caller who forgot would have reintroduced F6 (cross-tenant
IDOR) without a single failing test. Now forgetting it is a TypeError, and these tests keep it so.
"""

from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.security


def _functions():
    from web.repositories import appointment_repo, availability_repo
    from web.routers.api import treatment
    from web.services import appointment_service, availability_service, treatment_service

    return [
        appointment_repo.get_by_patient_date_time,
        appointment_repo.get_id,
        appointment_service.get_by_patient_date_time,
        treatment_service.get_appointment_id,
        treatment._resolve_apt_id,
        availability_repo.delete,
        availability_service.remove_local,
    ]


@pytest.mark.parametrize("fn", _functions(), ids=lambda f: f"{f.__module__}.{f.__name__}")
def test_therapist_id_is_required(fn) -> None:
    param = inspect.signature(fn).parameters["therapist_id"]
    assert param.default is inspect.Parameter.empty, f"{fn.__name__}: therapist_id must be required"


def test_another_therapists_appointment_is_not_found(make_therapist, make_appointment) -> None:
    from web.repositories import appointment_repo

    mine, other = make_therapist(), make_therapist()
    apt = make_appointment(therapist=other)
    args = (apt["patient_id"], apt["date"], apt["time"])
    assert appointment_repo.get_id(*args, mine["id"]) is None
    assert appointment_repo.get_by_patient_date_time(*args, mine["id"]) is None
    assert appointment_repo.get_id(*args, other["id"]) == apt["id"]
    assert appointment_repo.get_id(*args, "") is None, "an empty id never means 'any therapist'"
