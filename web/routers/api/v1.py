"""
web/routers/api/v1.py
──────────────────────
The booking API (plan 7.3): `POST /api/v1/appointments` is the one path that creates one.

Clients:
- **machine clients** (the WhatsApp bridge, a clinic website) send `Authorization: Bearer <key>`
  and may book for any therapist;
- **the dashboard** keeps its session cookie and may only act on its own therapist.

Every answer that is not a success is `{"code": …, "detail": …}`. The schema clients are
generated from is `docs/api/booking-v1.openapi.json` (`python -m zenflow.export_openapi`).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Header, Query, Request, Response
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from web.services import booking_service as booking
from web.services.booking_service import BookingError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["booking"])

API_TITLE = "ZenFlow Booking API"
API_VERSION = "1.0.0"
SECURITY_SCHEMES = {
    "ApiKey": {
        "type": "http",
        "scheme": "bearer",
        "description": "A machine client's API key (`python -m zenflow.api_keys create`). "
        "The dashboard uses its session cookie instead.",
    }
}


# ── models ──
class ApiError(BaseModel):
    code: str
    detail: Any


class PatientIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    patient_id: int | None = Field(default=None, ge=1)
    channel: Literal["telegram", "whatsapp"] | None = None
    external_id: str | None = Field(default=None, min_length=1, max_length=64)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=254)


class BookingIn(BaseModel):
    therapist_id: str = Field(min_length=1, max_length=32)
    start_at: datetime
    patient: PatientIn
    duration_min: int = Field(default=60, ge=60, le=60)
    summary: str = Field(default="", max_length=2000)
    source: Literal["api", "telegram", "whatsapp", "manual"] = "api"
    send_confirmation: bool = True
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("start_at")
    @classmethod
    def _needs_a_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("start_at must carry a timezone, e.g. 2026-03-12T08:00:00Z")
        return value

    @field_validator("patient")
    @classmethod
    def _identity_is_complete(cls, value: PatientIn) -> PatientIn:
        if bool(value.channel) != bool(value.external_id):
            raise ValueError("channel and external_id go together")
        return value


class Appointment(BaseModel):
    id: int
    therapist_id: str
    patient_id: int
    patient_name: str
    start_at: str
    local_date: str
    local_time: str
    duration_min: int
    status: str
    source: str
    summary: str | None = None
    created_at: str


class AppointmentList(BaseModel):
    items: list[Appointment]
    count: int


class Slot(BaseModel):
    start_at: str
    local_date: str
    local_time: str
    duration_min: int


class SlotList(BaseModel):
    items: list[Slot]
    count: int


_ERROR = {"model": ApiError}
_COMMON: dict[int | str, dict[str, Any]] = {
    401: {"model": ApiError, "description": "No or bad credentials"},
    403: {"model": ApiError, "description": "Not this client's therapist"},
    404: {"model": ApiError, "description": "Unknown therapist, patient or appointment"},
    422: {"model": ApiError, "description": "The request does not make sense"},
    429: {"model": ApiError, "description": "Too many requests"},
}


# ── the caller ──
class Caller:
    """Who is calling: a machine client (clinic-wide) or one therapist's session."""

    def __init__(self, kind: str, name: str, therapist_id: str | None = None) -> None:
        self.kind = kind
        self.name = name
        self.therapist_id = therapist_id

    @property
    def rate_key(self) -> str:
        return f"{self.kind}:{self.name}"

    def may_act_for(self, therapist_id: str) -> bool:
        return self.therapist_id is None or self.therapist_id == therapist_id


def caller(request: Request) -> Caller:
    """API key, else the dashboard session. 401 when neither is there."""
    from web.deps import _get_session_therapist
    from web.repositories import api_client_repo

    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        client = api_client_repo.verify(header[7:].strip())
        if client is None:
            raise ApiException(401, "unauthenticated", "unknown or revoked API key")
        return Caller("client", str(client["name"]))
    if header:
        raise ApiException(401, "unauthenticated", "expected `Authorization: Bearer <key>`")
    therapist = _get_session_therapist(request)
    if therapist and bool(therapist.get("active")):
        return Caller("therapist", str(therapist["id"]), str(therapist["id"]))
    raise ApiException(401, "unauthenticated", "an API key or a signed-in session is required")


class ApiException(Exception):
    def __init__(
        self, status: int, code: str, detail: Any, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(str(detail))
        self.status = status
        self.code = code
        self.detail = detail
        self.headers = headers or {}


def error_response(
    status: int, code: str, detail: Any, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse({"code": code, "detail": detail}, status_code=status, headers=headers)


async def _guard(request: Request) -> Caller:
    """Authenticate, then count the request against this caller's rate limit."""
    from web.services import rate_limit
    from zenflow.settings import get_settings

    who = caller(request)
    retry_after = await rate_limit.hit(who.rate_key, get_settings().flags.api_rate_per_minute)
    if retry_after is not None:
        raise ApiException(
            429,
            "rate_limited",
            f"try again in {retry_after}s",
            headers={"Retry-After": str(retry_after)},
        )
    return who


def _require_therapist(who: Caller, therapist_id: str) -> None:
    if not who.may_act_for(therapist_id):
        raise ApiException(403, "forbidden", "that therapist is not yours")


# ── routes ──
@router.post(
    "/appointments",
    response_model=Appointment,
    status_code=201,
    responses={**_COMMON, 409: {"model": ApiError, "description": "The hour is gone"}},
    summary="Book an appointment",
)
async def create_appointment(
    response: Response,
    who: Annotated[Caller, Depends(_guard)],
    body: Annotated[BookingIn, Body()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    _require_therapist(who, body.therapist_id)
    key = body.idempotency_key or idempotency_key
    if key is None:
        appointment = await _book(body, who)
        _location(response, appointment)
        return appointment

    from web.services import idempotency

    payload = body.model_dump(mode="json")
    try:
        replay = idempotency.begin(who.rate_key, key, payload)
    except idempotency.KeyReused as e:
        raise ApiException(
            422, "idempotency_key_reused", "that key was used for a different request"
        ) from e
    except idempotency.InProgress as e:
        raise ApiException(409, "request_in_progress", "the first request is still running") from e
    if replay is not None:
        response.headers["Idempotent-Replay"] = "true"
        if replay.status_code >= 400:
            return error_response(replay.status_code, replay.body["code"], replay.body["detail"])
        response.status_code = replay.status_code
        _location(response, replay.body)
        return replay.body

    try:
        appointment = await _book(body, who)
    except ApiException as e:
        idempotency.finish(who.rate_key, key, e.status, {"code": e.code, "detail": e.detail})
        raise
    except Exception:
        idempotency.abandon(who.rate_key, key)  # nothing was decided: let the client retry
        raise
    idempotency.finish(who.rate_key, key, 201, appointment)
    _location(response, appointment)
    return appointment


async def _book(body: BookingIn, who: Caller) -> dict[str, Any]:
    request = booking.BookingRequest(
        therapist_id=body.therapist_id,
        start_at=body.start_at,
        duration_min=body.duration_min,
        patient=booking.PatientSpec(
            name=body.patient.name,
            patient_id=body.patient.patient_id,
            channel=body.patient.channel,
            external_id=body.patient.external_id,
            phone=body.patient.phone,
            email=body.patient.email,
        ),
        summary=body.summary,
        source=body.source,
        send_confirmation=body.send_confirmation,
    )
    try:
        return await booking.create(request)
    except BookingError as e:
        raise ApiException(e.status, e.code, e.detail) from e


def _location(response: Response, appointment: dict[str, Any]) -> None:
    response.headers["Location"] = f"/api/v1/appointments/{appointment['id']}"


@router.get(
    "/appointments",
    response_model=AppointmentList,
    responses=_COMMON,
    summary="List appointments",
)
async def list_appointments(
    who: Annotated[Caller, Depends(_guard)],
    therapist_id: Annotated[str, Query(min_length=1, max_length=32)],
    from_date: Annotated[str | None, Query(alias="from")] = None,
    to_date: Annotated[str | None, Query(alias="to")] = None,
    status: Annotated[Literal["active", "cancelled", "all"], Query()] = "active",
    patient_id: Annotated[int | None, Query(ge=1)] = None,
) -> Any:
    _require_therapist(who, therapist_id)
    items = await _run(
        booking.list_appointments,
        therapist_id,
        from_date=from_date,
        to_date=to_date,
        status=status,
        patient_id=patient_id,
    )
    return {"items": items, "count": len(items)}


@router.get(
    "/appointments/{appointment_id}",
    response_model=Appointment,
    responses=_COMMON,
    summary="One appointment",
)
async def get_appointment(who: Annotated[Caller, Depends(_guard)], appointment_id: int) -> Any:
    appointment = await _run(booking.get, appointment_id)
    if appointment is None:
        raise ApiException(404, "unknown_appointment", "no such appointment")
    _require_therapist(who, appointment["therapist_id"])
    return appointment


@router.delete(
    "/appointments/{appointment_id}",
    response_model=Appointment,
    responses=_COMMON,
    summary="Cancel an appointment",
)
async def cancel_appointment(who: Annotated[Caller, Depends(_guard)], appointment_id: int) -> Any:
    appointment = await _run(booking.get, appointment_id)
    if appointment is None:
        raise ApiException(404, "unknown_appointment", "no such appointment")
    _require_therapist(who, appointment["therapist_id"])
    try:
        return await booking.cancel(appointment_id)
    except BookingError as e:
        raise ApiException(e.status, e.code, e.detail) from e


@router.get(
    "/availability",
    response_model=SlotList,
    responses=_COMMON,
    summary="Free hours",
)
async def get_availability(
    who: Annotated[Caller, Depends(_guard)],
    therapist_id: Annotated[str, Query(min_length=1, max_length=32)],
    from_date: Annotated[str, Query(alias="from")],
    to_date: Annotated[str, Query(alias="to")],
) -> Any:
    _require_therapist(who, therapist_id)
    try:
        items = await booking.availability(therapist_id, from_date, to_date)
    except BookingError as e:
        raise ApiException(e.status, e.code, e.detail) from e
    return {"items": items, "count": len(items)}


async def _run(fn: Any, *args: Any, **kwargs: Any) -> Any:
    import asyncio
    import functools

    return await asyncio.to_thread(functools.partial(fn, *args, **kwargs))


# ── the published schema ──
def booking_openapi() -> dict[str, Any]:
    """The OpenAPI document for this router alone (never the dashboard's own endpoints)."""
    schema = get_openapi(
        title=API_TITLE,
        version=API_VERSION,
        description=(
            "Booking for ZenFlow Clinic. Every appointment in the system is created through "
            "`POST /api/v1/appointments`."
        ),
        routes=router.routes,
    )
    schema.setdefault("components", {})["securitySchemes"] = SECURITY_SCHEMES
    schema["security"] = [{"ApiKey": []}]
    return schema
