"""The booking API's published contract (plan 7.3).

`docs/api/booking-v1.openapi.json` is the file clients are generated from, so it is committed and
compared with what the app serves: a change to a path, a field or a status code has to be a
deliberate edit, not a side effect.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.contract

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "docs/api/booking-v1.openapi.json"
EXPECTED_PATHS = {
    "/api/v1/appointments": {"get", "post"},
    "/api/v1/appointments/{appointment_id}": {"delete", "get"},
    "/api/v1/availability": {"get"},
}


def _served() -> dict[str, Any]:
    from web.routers.api.v1 import booking_openapi

    return booking_openapi()


def test_the_committed_schema_is_what_the_app_serves() -> None:
    assert SCHEMA_FILE.exists(), "commit the schema: python -m zenflow.export_openapi"
    committed = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert committed == _served(), (
        "the booking API changed — review it, then re-export with "
        "`python -m zenflow.export_openapi`"
    )


def test_the_schema_covers_the_documented_surface() -> None:
    schema = _served()
    assert schema["openapi"].startswith("3.")
    assert schema["info"]["title"] == "ZenFlow Booking API"
    assert schema["info"]["version"] == "1.0.0"
    paths = {path: set(ops) - {"parameters"} for path, ops in schema["paths"].items()}
    assert paths == EXPECTED_PATHS
    # only the booking API, never the dashboard's own endpoints
    assert not [p for p in schema["paths"] if not p.startswith("/api/v1/")]


def test_every_operation_documents_its_failures() -> None:
    schema = _served()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if method == "parameters":
                continue
            codes = set(operation["responses"])
            assert {"401", "429"} <= codes, f"{method} {path} must document auth and rate limits"
            assert "422" in codes or method == "delete", f"{method} {path} must document 422"
    create = schema["paths"]["/api/v1/appointments"]["post"]["responses"]
    assert {"201", "403", "404", "409", "422"} <= set(create)


def test_an_error_is_always_code_and_detail() -> None:
    schema = _served()
    error = schema["components"]["schemas"]["ApiError"]
    assert set(error["required"]) == {"code", "detail"}
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if method == "parameters":
                continue
            for status, response in operation["responses"].items():
                if not status.startswith(("4", "5")):
                    continue
                ref = response["content"]["application/json"]["schema"]["$ref"]
                assert ref.endswith("/ApiError"), f"{method} {path} {status}"


def test_the_appointment_shape_is_pinned() -> None:
    appointment = _served()["components"]["schemas"]["Appointment"]
    assert set(appointment["properties"]) == {
        "id",
        "therapist_id",
        "patient_id",
        "patient_name",
        "start_at",
        "local_date",
        "local_time",
        "duration_min",
        "status",
        "source",
        "summary",
        "created_at",
    }
    assert set(appointment["required"]) == set(appointment["properties"]) - {"summary"}


def test_the_schema_names_its_authentication() -> None:
    schema = _served()
    schemes = schema["components"]["securitySchemes"]
    assert schemes["ApiKey"] == {
        "type": "http",
        "scheme": "bearer",
        "description": schemes["ApiKey"]["description"],
    }
    assert "api key" in schemes["ApiKey"]["description"].lower()
    assert schema["security"] == [{"ApiKey": []}]
