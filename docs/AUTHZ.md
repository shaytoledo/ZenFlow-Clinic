# Route authorization (Phase 9.1)

Every HTTP route, what it requires, what keeps one therapist out of another's data, and which test
proves it. The table below is **generated** from `web/authz.py`
(`python -m zenflow.export_routes`); `tests/security/test_route_inventory.py` compares that table
with the running application, so a new route cannot merge without an entry, and an entry that
claims a scope without naming a real test fails the build.

## Levels

| Level | Means | A stranger gets |
|---|---|---|
| public | no session — a probe, the sign-in form, the OAuth callbacks | the page |
| signed in | a session with a known therapist | `401` (API) or `307 → /register` (page) |
| signed in + activated | a session *and* an activated account — the normal dashboard level | `401` / `307 → /register` |
| API key or session | the booking API: `Authorization: Bearer zf_…`, or a dashboard session acting for itself | `401` |
| provider signature | no session at all: WhatsApp's `X-Hub-Signature-256` over the raw body | `404` while the channel flag is off, `403` when signed wrongly |

## Object-level checks

Authentication says *someone* is signed in; these say *this* is theirs (ADR-17).

| Scope | Means |
|---|---|
| own rows only | every read is filtered by the session's therapist id |
| own appointment | resolved through `resolve_owned_appointment` (404) or `require_appointment_access` (403) — never by patient + date + time alone |
| own conversation | `_assert_conversation_owner`: a live relay session owned by this therapist, or their own stored history (SF-008) |
| own patient | the patient must be one this therapist treats (SF-013) |
| own account | the object is the caller's own settings, tokens or code |
| — | the route touches nothing that belongs to one therapist (health, reference data, flags, clinic-wide aggregates) |

## What this audit found

- **SF-014** — `/docs`, `/redoc` and `/openapi.json` were served to anyone, in every environment:
  the complete route map of a system holding medical records. They are now development-only
  (`docs_urls()` in `web/app.py`); outside dev the routes do not exist.
- **SF-015** — `GET /auth/login` started Google consent and wrote to the session for an anonymous
  caller. It now requires an activated session and redirects a stranger to `/register`.

Both are recorded in `docs/SECURITY_FINDINGS.md` with their regressions.

## Still to come in Phase 9

The levels above are about *who*; the rest of the programme is about *how much* and *how safely*:
CSRF (9.3), security headers and CSP (9.4), rate limiting on the public forms and the AI endpoints
(9.5), and input/output safety (9.7). This table is the map those tasks work from.

<!-- generated: python -m zenflow.export_routes -->

| Method | Route | Who may call it | Object-level check | Proven by |
|---|---|---|---|---|
| `GET` | `/` <br><sub>dashboard</sub> | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `GET` | `/api/acupoints` <br><sub>reference data, the same for everyone</sub> | signed in + activated | — | — |
| `GET` | `/api/admin/flags` <br><sub>flag state; never secrets</sub> | signed in + activated | — | — |
| `GET` | `/api/admin/metrics` <br><sub>clinic-wide aggregates (8.4)</sub> | signed in + activated | — | — |
| `GET` | `/api/appointment/{patient_id}/{apt_date}/{apt_time}` | signed in + activated | own appointment | `tests/security/test_tenant_isolation.py` |
| `POST` | `/api/appointments` | signed in + activated | own patient | `tests/integration/test_patient_identity.py` |
| `GET` | `/api/appointments/today` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `POST` | `/api/availability` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `GET` | `/api/availability/free-slots` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `DELETE` | `/api/availability/{event_id}` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `GET` | `/api/calendars` | signed in + activated | own account | `tests/security/test_tenant_isolation.py` |
| `POST` | `/api/calendars/refresh` | signed in + activated | own account | `tests/security/test_tenant_isolation.py` |
| `GET` | `/api/events` | signed in + activated | own account | `tests/security/test_tenant_isolation.py` |
| `GET` | `/api/gmail-status` | signed in + activated | own account | `tests/security/test_auth_surface.py` |
| `GET` | `/api/messages/active` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `GET` | `/api/messages/conversations` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `DELETE` | `/api/messages/history/{patient_id}` | signed in + activated | own conversation | `tests/security/test_tenant_isolation.py` |
| `GET` | `/api/messages/history/{patient_id}` | signed in + activated | own conversation | `tests/security/test_tenant_isolation.py` |
| `POST` | `/api/messages/send` | signed in + activated | own conversation | `tests/security/test_tenant_isolation.py` |
| `POST` | `/api/messages/unread/{patient_id}` | signed in + activated | own conversation | `tests/security/test_tenant_isolation.py` |
| `GET` | `/api/my/activation-code` | signed in | own account | `tests/security/test_auth_surface.py` |
| `DELETE` | `/api/my/alerts` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `GET` | `/api/my/alerts` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `GET` | `/api/my/language` | signed in + activated | own account | `tests/security/test_auth_surface.py` |
| `POST` | `/api/my/language` | signed in + activated | own account | `tests/security/test_auth_surface.py` |
| `GET` | `/api/my/preferences` | signed in + activated | own account | `tests/integration/test_ui_prefs.py` |
| `PATCH` | `/api/my/preferences` | signed in + activated | own account | `tests/integration/test_ui_prefs.py` |
| `GET` | `/api/my/status` | signed in | own account | `tests/security/test_auth_surface.py` |
| `GET` | `/api/notifications` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `POST` | `/api/notifications/read-all` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `GET` | `/api/notifications/unread-count` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `POST` | `/api/notifications/{notification_id}/read` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `POST` | `/api/notifications/{notification_id}/resolve` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `GET` | `/api/patients` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `GET` | `/api/patients/search` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `GET` | `/api/patients/{patient_id}` | signed in + activated | own patient | `tests/security/test_tenant_isolation.py` |
| `GET` | `/api/smtp-status` | signed in + activated | own account | `tests/security/test_auth_surface.py` |
| `GET` | `/api/status` <br><sub>service health for the dashboard widget</sub> | signed in + activated | — | — |
| `GET` | `/api/treatment-notes/sessions/history` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `GET` | `/api/treatment-notes/{appointment_id}/debug` | signed in + activated | own appointment | `tests/security/test_tenant_isolation.py` |
| `GET` | `/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}` | signed in + activated | own appointment | `tests/security/test_tenant_isolation.py` |
| `POST` | `/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}` | signed in + activated | own appointment | `tests/security/test_tenant_isolation.py` |
| `POST` | `/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/cancel-generation` | signed in + activated | own appointment | `tests/security/test_tenant_isolation.py` |
| `POST` | `/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/complete` | signed in + activated | own appointment | `tests/security/test_tenant_isolation.py` |
| `POST` | `/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/generate-points` | signed in + activated | own appointment | `tests/security/test_tenant_isolation.py` |
| `POST` | `/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/manual-feedback` | signed in + activated | own appointment | `tests/security/test_tenant_isolation.py` |
| `POST` | `/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/recommendations-text` | signed in + activated | own appointment | `tests/security/test_tenant_isolation.py` |
| `POST` | `/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/rediagnose` | signed in + activated | own appointment | `tests/security/test_tenant_isolation.py` |
| `POST` | `/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/regenerate-points` | signed in + activated | own appointment | `tests/security/test_tenant_isolation.py` |
| `POST` | `/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/send-recommendations` <br><sub>SF-010: the re-query is tenant-scoped</sub> | signed in + activated | own appointment | `tests/security/test_tenant_isolation.py` |
| `GET` | `/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/stream` <br><sub>server-sent events for one appointment</sub> | signed in + activated | own appointment | `tests/security/test_tenant_isolation.py` |
| `GET` | `/api/v1/appointments` | API key or session | own rows only | `tests/integration/test_booking_api.py` |
| `POST` | `/api/v1/appointments` | API key or session | own rows only | `tests/integration/test_booking_api.py` |
| `DELETE` | `/api/v1/appointments/{appointment_id}` | API key or session | own appointment | `tests/integration/test_booking_api.py` |
| `GET` | `/api/v1/appointments/{appointment_id}` | API key or session | own appointment | `tests/integration/test_booking_api.py` |
| `GET` | `/api/v1/availability` | API key or session | own rows only | `tests/integration/test_booking_api.py` |
| `GET` | `/api/webhooks/whatsapp` <br><sub>subscription handshake</sub> | provider signature | — | `tests/integration/test_whatsapp_webhook.py` |
| `POST` | `/api/webhooks/whatsapp` <br><sub>HMAC over the raw body</sub> | provider signature | — | `tests/integration/test_whatsapp_webhook.py` |
| `GET` | `/auth/callback` | signed in | own account | `tests/integration/test_google_connection.py` |
| `POST` | `/auth/disconnect` | signed in + activated | own account | `tests/integration/test_google_connection.py` |
| `GET` | `/auth/login` <br><sub>starts consent for the caller only</sub> | signed in + activated | own account | `tests/security/test_route_inventory.py` |
| `GET` | `/docs` <br><sub>dev only — absent outside dev (SF-014)</sub> | public | — | — |
| `GET` | `/docs/oauth2-redirect` <br><sub>dev only (SF-014)</sub> | public | — | — |
| `GET` | `/healthz` <br><sub>liveness; says nothing else (F11)</sub> | public | — | — |
| `GET` | `/logout` <br><sub>clears the caller's own session</sub> | public | — | — |
| `GET` | `/media/{key:path}` <br><sub>reference images, not patient data</sub> | signed in | — | `tests/security/test_auth_surface.py` |
| `GET` | `/messages` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `GET` | `/onboarding` <br><sub>shown before activation</sub> | signed in | own account | `tests/security/test_auth_surface.py` |
| `GET` | `/openapi.json` <br><sub>dev only (SF-014)</sub> | public | — | — |
| `GET` | `/patients` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `GET` | `/patients/{patient_id}` | signed in + activated | own patient | `tests/security/test_tenant_isolation.py` |
| `GET` | `/patients/{patient_id}/session/{appointment_id}` <br><sub>the read-only session archive</sub> | signed in + activated | own appointment | `tests/security/test_tenant_isolation.py` |
| `GET` | `/readyz` <br><sub>readiness; detail only with a session (8.4)</sub> | public | — | — |
| `GET` | `/redoc` <br><sub>dev only (SF-014)</sub> | public | — | — |
| `GET` | `/register` <br><sub>the sign-in / sign-up form</sub> | public | — | — |
| `GET` | `/register/activate` <br><sub>activation code for the caller</sub> | signed in | own account | `tests/security/test_auth_surface.py` |
| `GET` | `/register/done` <br><sub>static confirmation page</sub> | public | — | — |
| `GET` | `/register/google` <br><sub>starts Google sign-in</sub> | public | — | — |
| `GET` | `/register/google/callback` <br><sub>state + next are validated</sub> | public | — | `tests/security/test_google_oauth_next.py` |
| `POST` | `/register/signin` <br><sub>rate limiting is plan 9.5</sub> | public | — | — |
| `POST` | `/register/signup` <br><sub>rate limiting is plan 9.5</sub> | public | — | — |
| `GET` | `/schedule` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `GET` | `/sessions` | signed in + activated | own rows only | `tests/security/test_tenant_isolation.py` |
| `GET` | `/settings` | signed in + activated | own account | `tests/security/test_tenant_isolation.py` |
| `GET` | `/treatment/{patient_id}/{apt_date}/{apt_time}` | signed in + activated | own appointment | `tests/security/test_tenant_isolation.py` |

<!-- /generated -->
