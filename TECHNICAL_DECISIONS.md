# ZenFlow — Technical Decisions Reference

## Redis Key Schema

| Key | Type | TTL | Purpose |
|---|---|---|---|
| `zenflow:intake:{user_id}` | List (LangChain) | 1 h | LangChain RedisChatMessageHistory for intake |
| `zenflow:relay:msg:{msg_id}` | String (JSON) | 24 h | Maps therapist-bot msg ID → `{patient_id, therapist_id}` |
| `zenflow:relay:active:{patient_id}` | String | None | Presence key — deleted by `end_relay()` |
| `zenflow:slots:{date}` | String (JSON) | 5 min | Booked time slots for a given date |
| `zenflow:avail:days:{tid}:{week}` | String (JSON) | 5 min | Available days per therapist per week offset |
| `zenflow:avail:hours:{tid}:{date}` | String (JSON) | 5 min | Available hours per therapist per date |
| `zenflow:apts:all` | String (JSON) | 30 s | Full appointment list cache for web dashboard |

---

## Therapist Registry (`data/therapists.json`)

Each entry:
```json
{
  "id": "t1",               // Internal string ID used in relay, booking, availability
  "name": "Dr. Sarah",      // Display name shown to patients
  "telegram_id": 918187404, // Therapist's Telegram user ID (integer)
  "calendar_name": "ZenFlow Availability",  // Google Calendar to read availability from
  "active": true            // False = excluded from all routing; no restart needed after flag change
}
```

To add a therapist: append an entry with a unique `id`, set `active: true`, restart bots.

---

## Relay Architecture Decision — Shared Bot

All therapists share **one therapist bot** (`THERAPIST_BOT_TOKEN`). Per-therapist isolation is achieved via:
- One `MessageHandler` registered per active therapist, filtered by `filters.User(user_id=telegram_id)`
- The `therapist_id` is lambda-bound at handler registration, so the correct value is always passed to `handle_therapist_reply()`
- The relay Redis key stores both `patient_id` and `therapist_id`; `handle_therapist_reply()` verifies the replying therapist owns the session

Alternative considered: one bot token per therapist. Rejected because it requires each therapist to create a bot with @BotFather and would multiply infrastructure.

---

## Why `asyncio.to_thread()` Instead of an Async GCal Client

The `google-api-python-client` library is synchronous and does not support asyncio natively. There is no first-class async Google Calendar client with comparable maturity.

`asyncio.to_thread()` runs the sync call in a thread pool, releasing the event loop during the blocking HTTP call. This gives equivalent performance to an async client without requiring a library replacement.

Affected call sites: `availability.py` (all `service.events().*`, `service.calendarList().*` calls), `web/app.py` (`GCalClient.load()`, `client.get_events()`, `client.create_availability()`, `client.delete_availability()`).

---

## LangChain History Backend Choice

`RedisChatMessageHistory` (from `langchain-redis`) was chosen over `InMemoryChatMessageHistory` for two reasons:

1. **Restart resilience** — intake context survives bot restarts. The patient can continue exactly where they left off.
2. **Multi-process safety** — if the web dashboard or another process needs to inspect intake history, it reads from the same Redis store.

TTL is set to 1 hour. History is explicitly cleared by `clear_intake()` once the appointment is saved, so in practice keys are short-lived even without the TTL.

---

## Remaining Limitations

- `data/therapists.json` requires a bot restart to take effect (no hot-reload).
- `book_slot()` always creates events in the **primary** Google Calendar (hardcoded `calendarId="primary"`). In a true multi-therapist deployment each therapist would need their own primary calendar or service account.
- The web dashboard does not yet filter data per therapist; all therapists see all patients.
- `PicklePersistence` for PTB conversation state is not yet implemented — bot restarts lose in-flight booking state (though intake history itself is now Redis-backed).
