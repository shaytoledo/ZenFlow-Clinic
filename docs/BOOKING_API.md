# The Booking API (Phase 7.3)

`POST /api/v1/appointments` is the one path that creates an appointment. Plan:
`docs/MASTER_PLAN_EN.md` Phase 7.3. Decision record: ADR-29.

The published schema is **`docs/api/booking-v1.openapi.json`** — generate clients from it, and
re-export it with `python -m zenflow.export_openapi` whenever the API changes (a contract test
fails otherwise).

---

## 1. Who may call it

| Caller | Credential | May act for |
|---|---|---|
| A machine client (the WhatsApp bridge, a clinic website) | `Authorization: Bearer <key>` | any therapist in the clinic |
| The dashboard | its `zf_session` cookie | its own therapist only (403 otherwise) |

Keys are managed from the command line:

```bash
python -m zenflow.api_keys create whatsapp-bridge   # prints the key once
python -m zenflow.api_keys list
python -m zenflow.api_keys revoke whatsapp-bridge
```

A key is `zf_` plus 32 random bytes, and only its SHA-256 hash is stored, so a lost key is
revoked and replaced, never recovered. A key can book for any therapist, so it belongs to a
server the clinic runs — never to a browser or a person.

**Rate limit:** `ZF_API_RATE_PER_MINUTE` requests per minute per caller (60 by default, `0`
turns it off). Over the limit is `429` with `Retry-After`. If Redis is down the limiter allows
the request rather than stopping the clinic taking bookings.

## 2. The endpoints

| Method | Path | Does |
|---|---|---|
| `POST` | `/api/v1/appointments` | book an hour (201, `Location`) |
| `GET` | `/api/v1/appointments?therapist_id=&from=&to=&status=&patient_id=` | list |
| `GET` | `/api/v1/appointments/{id}` | one |
| `DELETE` | `/api/v1/appointments/{id}` | cancel: soft-delete, hand the hour back, delete the calendar event |
| `GET` | `/api/v1/availability?therapist_id=&from=&to=` | free hours (at most 31 days per request) |

**Booking:**

```http
POST /api/v1/appointments
Authorization: Bearer zf_…
Idempotency-Key: 6f1c…

{
  "therapist_id": "t1",
  "start_at": "2026-03-12T08:00:00Z",
  "duration_min": 60,
  "patient": {"channel": "telegram", "external_id": "920000101", "name": "Dana Levi",
              "phone": "050-1111111", "email": "dana@example.com"},
  "summary": "neck pain",
  "source": "api",
  "send_confirmation": true
}
```

**The patient** is named one of three ways:

- `channel` + `external_id` — the patient behind that channel identity, created on first contact;
- `patient_id` — a patient the clinic already knows;
- neither — a new patient with no messaging channel (they cannot be messaged; `docs/CHANNELS.md` §6).

**Times** are instants: `start_at` must carry a timezone, and the appointment is stored as the
clinic-local date and time it falls on. Every response carries both (`start_at`, plus
`local_date` and `local_time`). Only 60-minute appointments exist.

**Availability** is enforced: an hour the therapist has not published is `409 slot_unavailable`.

## 3. Idempotency

A client that retries after a timeout must not create a second appointment. Send an
`Idempotency-Key` header (or `idempotency_key` in the body):

- **the same key and the same body** replay the first answer, with `Idempotent-Replay: true` —
  including a refusal, so a retried `409 slot_taken` stays a `409`;
- **the same key with a different body** is `422 idempotency_key_reused`;
- **while the first request is still running**, a second one is `409 request_in_progress`;
- **keys belong to the caller** — two clients may use the same string;
- **keys are kept for 24 hours**.

Without a key, a repeated request is a new booking attempt, and the hour is already taken, so it
answers `409 slot_taken`. Two requests racing for the same hour end as exactly one `201` and one
`409`: the appointment row claims the hour, with a partial unique index behind it (B4).

## 4. Errors

Every non-success answer is `{"code": …, "detail": …}`:

| Status | Codes |
|---|---|
| 401 | `unauthenticated` |
| 403 | `forbidden` |
| 404 | `unknown_therapist`, `unknown_patient`, `unknown_appointment` |
| 409 | `slot_taken`, `slot_unavailable`, `request_in_progress` |
| 422 | `validation_error`, `idempotency_key_reused` |
| 429 | `rate_limited` |

## 5. What a booking does

`web/services/booking_service.py` is the one implementation. In order:

1. **Validate** the therapist and the request.
2. **Resolve the patient** (`patient_channels`, Phase 7.2).
3. **Check availability** (for API callers).
4. **Insert the appointment** — this is what claims the hour, and the only source of truth (B4).
5. **Take the hour out of the availability calendar** and create the calendar event. A calendar
   failure is logged; the booking stands.
6. **Queue the confirmation** (`booking.confirm`, one per appointment) unless
   `send_confirmation` is false. The job sends "your appointment is confirmed for …" to the
   patient's messaging channel and records it in `message_log` as `confirmation`. A patient with
   no channel gets nothing.

Cancelling soft-deletes the row (the clinical record is kept), hands the hour back and deletes
the calendar event. Cancelling twice is a no-op, so a retry is safe.

## 6. Tests

- **`tests/integration/test_booking_api.py`** — authentication, validation, creating,
  idempotency, the race, listing, cancelling, availability and the rate limit.
- **`tests/contract/test_booking_api_contract.py`** — the committed schema matches what the app
  serves; every operation documents 401/422/429; every error is one shape.
