"""
web/authz.py
─────────────
The declared security contract of every HTTP route (Phase 9.1).

Each route says three things: **who may call it**, **what object-level check keeps one therapist
out of another's data**, and **which test proves it**. `tests/security/test_route_inventory.py`
compares this table with the running application and fails when they disagree — so a new route
cannot be merged without stating its answer to all three, and a route whose scoping is claimed
here without a test is a failing build rather than a comment.

The table is the source of `docs/AUTHZ.md` (`python -m zenflow.export_routes`).

Levels
------
`PUBLIC`      no session; deliberately reachable by anyone (a probe, the sign-in form).
`SESSION`     a signed-in therapist; `/api` answers 401, a page redirects to `/register`.
`ACTIVE`      signed in *and* activated (the dashboard's normal level).
`MACHINE`     the booking API: an API key, or a dashboard session acting for itself.
`SIGNATURE`   no session at all — the caller proves itself with a provider signature (WhatsApp).

Scopes
------
`NONE`        the route reads nothing that belongs to one therapist.
`OWN`         everything it touches is filtered by the session's therapist id.
`APPOINTMENT` the appointment is resolved through `resolve_owned_appointment` /
              `require_appointment_access` (404 / 403), never by patient+date+time alone (ADR-17).
`CONVERSATION` the relay conversation must be this therapist's (`_assert_conversation_owner`).
`PATIENT`     the patient must be one this therapist treats.
`SELF`        the object is the caller's own account or settings.
"""

from __future__ import annotations

from typing import NamedTuple

PUBLIC, SESSION, ACTIVE, MACHINE, SIGNATURE = (
    "public",
    "session",
    "active",
    "machine",
    "signature",
)
NONE, OWN, APPOINTMENT, CONVERSATION, PATIENT, SELF = (
    "none",
    "own",
    "appointment",
    "conversation",
    "patient",
    "self",
)

#: tests that prove a scope; the inventory test checks that each one exists
TENANT = "tests/security/test_tenant_isolation.py"
SURFACE = "tests/security/test_auth_surface.py"
INVENTORY = "tests/security/test_route_inventory.py"


class Access(NamedTuple):
    auth: str
    scope: str
    covered_by: str  # the test file that proves the scope (empty only when scope is NONE)
    note: str = ""


#: (METHOD, path) → what the route promises. Keep it sorted by path; the test sorts anyway.
ROUTES: dict[tuple[str, str], Access] = {
    # ── probes and docs ──
    ("GET", "/healthz"): Access(PUBLIC, NONE, "", "liveness; says nothing else (F11)"),
    ("GET", "/readyz"): Access(PUBLIC, NONE, "", "readiness; detail only with a session (8.4)"),
    ("GET", "/docs"): Access(PUBLIC, NONE, "", "dev only — absent outside dev (SF-014)"),
    ("GET", "/docs/oauth2-redirect"): Access(PUBLIC, NONE, "", "dev only (SF-014)"),
    ("GET", "/redoc"): Access(PUBLIC, NONE, "", "dev only (SF-014)"),
    ("GET", "/openapi.json"): Access(PUBLIC, NONE, "", "dev only (SF-014)"),
    # ── registration and sign-in ──
    ("GET", "/register"): Access(PUBLIC, NONE, "", "the sign-in / sign-up form"),
    ("POST", "/register/signin"): Access(PUBLIC, NONE, "", "rate limiting is plan 9.5"),
    ("POST", "/register/signup"): Access(PUBLIC, NONE, "", "rate limiting is plan 9.5"),
    ("GET", "/register/done"): Access(PUBLIC, NONE, "", "static confirmation page"),
    ("GET", "/register/google"): Access(PUBLIC, NONE, "", "starts Google sign-in"),
    ("GET", "/register/google/callback"): Access(
        PUBLIC, NONE, "tests/security/test_google_oauth_next.py", "state + next are validated"
    ),
    ("GET", "/register/activate"): Access(SESSION, SELF, SURFACE, "activation code for the caller"),
    ("GET", "/logout"): Access(PUBLIC, NONE, "", "clears the caller's own session"),
    # ── Google Calendar / Gmail connection ──
    ("GET", "/auth/login"): Access(ACTIVE, SELF, INVENTORY, "starts consent for the caller only"),
    ("GET", "/auth/callback"): Access(SESSION, SELF, "tests/integration/test_google_connection.py"),
    ("POST", "/auth/disconnect"): Access(
        ACTIVE, SELF, "tests/integration/test_google_connection.py"
    ),
    # ── pages ──
    ("GET", "/"): Access(ACTIVE, OWN, TENANT, "dashboard"),
    ("GET", "/schedule"): Access(ACTIVE, OWN, TENANT),
    ("GET", "/patients"): Access(ACTIVE, OWN, TENANT),
    ("GET", "/patients/{patient_id}"): Access(ACTIVE, PATIENT, TENANT),
    ("GET", "/patients/{patient_id}/session/{appointment_id}"): Access(
        ACTIVE, APPOINTMENT, TENANT, "the read-only session archive"
    ),
    ("GET", "/messages"): Access(ACTIVE, OWN, TENANT),
    ("GET", "/sessions"): Access(ACTIVE, OWN, TENANT),
    ("GET", "/settings"): Access(ACTIVE, SELF, TENANT),
    ("GET", "/onboarding"): Access(SESSION, SELF, SURFACE, "shown before activation"),
    ("GET", "/treatment/{patient_id}/{apt_date}/{apt_time}"): Access(ACTIVE, APPOINTMENT, TENANT),
    ("GET", "/media/{key:path}"): Access(
        SESSION, NONE, SURFACE, "reference images, not patient data"
    ),
    # ── dashboard API: appointments and patients ──
    ("GET", "/api/appointments/today"): Access(ACTIVE, OWN, TENANT),
    ("POST", "/api/appointments"): Access(
        ACTIVE, PATIENT, "tests/integration/test_patient_identity.py"
    ),
    ("GET", "/api/appointment/{patient_id}/{apt_date}/{apt_time}"): Access(
        ACTIVE, APPOINTMENT, TENANT
    ),
    ("GET", "/api/patients"): Access(ACTIVE, OWN, TENANT),
    ("GET", "/api/patients/search"): Access(ACTIVE, OWN, TENANT),
    ("GET", "/api/patients/{patient_id}"): Access(ACTIVE, PATIENT, TENANT),
    # ── dashboard API: the treatment record ──
    ("GET", "/api/treatment-notes/sessions/history"): Access(ACTIVE, OWN, TENANT),
    ("GET", "/api/treatment-notes/{appointment_id}/debug"): Access(ACTIVE, APPOINTMENT, TENANT),
    ("GET", "/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}"): Access(
        ACTIVE, APPOINTMENT, TENANT
    ),
    ("POST", "/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}"): Access(
        ACTIVE, APPOINTMENT, TENANT
    ),
    ("POST", "/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/cancel-generation"): Access(
        ACTIVE, APPOINTMENT, TENANT
    ),
    ("POST", "/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/complete"): Access(
        ACTIVE, APPOINTMENT, TENANT
    ),
    ("POST", "/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/generate-points"): Access(
        ACTIVE, APPOINTMENT, TENANT
    ),
    ("POST", "/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/manual-feedback"): Access(
        ACTIVE, APPOINTMENT, TENANT
    ),
    (
        "POST",
        "/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/recommendations-text",
    ): Access(ACTIVE, APPOINTMENT, TENANT),
    ("POST", "/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/rediagnose"): Access(
        ACTIVE, APPOINTMENT, TENANT
    ),
    ("POST", "/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/regenerate-points"): Access(
        ACTIVE, APPOINTMENT, TENANT
    ),
    (
        "POST",
        "/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/send-recommendations",
    ): Access(ACTIVE, APPOINTMENT, TENANT, "SF-010: the re-query is tenant-scoped"),
    ("GET", "/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/stream"): Access(
        ACTIVE, APPOINTMENT, TENANT, "server-sent events for one appointment"
    ),
    # ── dashboard API: availability and calendars ──
    ("POST", "/api/availability"): Access(ACTIVE, OWN, TENANT),
    ("GET", "/api/availability/free-slots"): Access(ACTIVE, OWN, TENANT),
    ("DELETE", "/api/availability/{event_id}"): Access(ACTIVE, OWN, TENANT),
    ("GET", "/api/calendars"): Access(ACTIVE, SELF, TENANT),
    ("POST", "/api/calendars/refresh"): Access(ACTIVE, SELF, TENANT),
    ("GET", "/api/events"): Access(ACTIVE, SELF, TENANT),
    # ── dashboard API: the relay ──
    ("GET", "/api/messages/active"): Access(ACTIVE, OWN, TENANT),
    ("GET", "/api/messages/conversations"): Access(ACTIVE, OWN, TENANT),
    ("GET", "/api/messages/history/{patient_id}"): Access(ACTIVE, CONVERSATION, TENANT),
    ("DELETE", "/api/messages/history/{patient_id}"): Access(ACTIVE, CONVERSATION, TENANT),
    ("POST", "/api/messages/send"): Access(ACTIVE, CONVERSATION, TENANT),
    ("POST", "/api/messages/unread/{patient_id}"): Access(ACTIVE, CONVERSATION, TENANT),
    # ── dashboard API: the caller's own account ──
    ("GET", "/api/my/activation-code"): Access(SESSION, SELF, SURFACE),
    ("GET", "/api/my/alerts"): Access(ACTIVE, OWN, TENANT),
    ("DELETE", "/api/my/alerts"): Access(ACTIVE, OWN, TENANT),
    ("GET", "/api/my/language"): Access(ACTIVE, SELF, SURFACE),
    ("POST", "/api/my/language"): Access(ACTIVE, SELF, SURFACE),
    ("GET", "/api/my/preferences"): Access(ACTIVE, SELF, "tests/integration/test_ui_prefs.py"),
    ("PATCH", "/api/my/preferences"): Access(ACTIVE, SELF, "tests/integration/test_ui_prefs.py"),
    ("GET", "/api/my/status"): Access(SESSION, SELF, SURFACE),
    # ── dashboard API: notifications ──
    ("GET", "/api/notifications"): Access(ACTIVE, OWN, TENANT),
    ("GET", "/api/notifications/unread-count"): Access(ACTIVE, OWN, TENANT),
    ("POST", "/api/notifications/read-all"): Access(ACTIVE, OWN, TENANT),
    ("POST", "/api/notifications/{notification_id}/read"): Access(ACTIVE, OWN, TENANT),
    ("POST", "/api/notifications/{notification_id}/resolve"): Access(ACTIVE, OWN, TENANT),
    # ── dashboard API: system and reference data ──
    ("GET", "/api/status"): Access(ACTIVE, NONE, "", "service health for the dashboard widget"),
    ("GET", "/api/smtp-status"): Access(ACTIVE, SELF, SURFACE),
    ("GET", "/api/gmail-status"): Access(ACTIVE, SELF, SURFACE),
    ("GET", "/api/acupoints"): Access(ACTIVE, NONE, "", "reference data, the same for everyone"),
    ("GET", "/api/admin/flags"): Access(ACTIVE, NONE, "", "flag state; never secrets"),
    ("GET", "/api/admin/metrics"): Access(ACTIVE, NONE, "", "clinic-wide aggregates (8.4)"),
    # ── the booking API (machine clients) ──
    ("POST", "/api/v1/appointments"): Access(MACHINE, OWN, "tests/integration/test_booking_api.py"),
    ("GET", "/api/v1/appointments"): Access(MACHINE, OWN, "tests/integration/test_booking_api.py"),
    ("GET", "/api/v1/appointments/{appointment_id}"): Access(
        MACHINE, APPOINTMENT, "tests/integration/test_booking_api.py"
    ),
    ("DELETE", "/api/v1/appointments/{appointment_id}"): Access(
        MACHINE, APPOINTMENT, "tests/integration/test_booking_api.py"
    ),
    ("GET", "/api/v1/availability"): Access(MACHINE, OWN, "tests/integration/test_booking_api.py"),
    # ── the WhatsApp webhook (no session; Meta signs it) ──
    ("GET", "/api/webhooks/whatsapp"): Access(
        SIGNATURE, NONE, "tests/integration/test_whatsapp_webhook.py", "subscription handshake"
    ),
    ("POST", "/api/webhooks/whatsapp"): Access(
        SIGNATURE, NONE, "tests/integration/test_whatsapp_webhook.py", "HMAC over the raw body"
    ),
}

#: routes that exist only in development (FastAPI's own docs); absent outside dev since 9.1
DEV_ONLY = frozenset(
    {
        ("GET", "/docs"),
        ("GET", "/docs/oauth2-redirect"),
        ("GET", "/redoc"),
        ("GET", "/openapi.json"),
    }
)
#: how an anonymous caller is refused, per level
ANONYMOUS_REFUSAL = {SESSION: (401, 307, 303), ACTIVE: (401, 307, 303), MACHINE: (401,)}
