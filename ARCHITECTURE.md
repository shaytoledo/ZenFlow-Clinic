# ZenFlow Clinic — System Architecture

## Overview

ZenFlow is a Telegram-based clinic management system for a Traditional Chinese Medicine (TCM) acupuncture clinic. It consists of three services running concurrently:

| Service | Entry point | Purpose |
|---|---|---|
| Patient bot | `run_bots.py` | Patient-facing Telegram bot |
| Therapist bot | (same process as patient bot) | Therapist-facing Telegram bot |
| Web dashboard | `run_web.py` | Therapist web dashboard |

All three start together with `python launch.py`.

---

## File Tree

```
Clinic/
│
├── launch.py                 # Unified launcher — setup + starts all services
├── run_bots.py               # Bots only (dev shortcut)
├── run_web.py                # Web dashboard only (dev shortcut)
├── requirements.txt          # Python dependencies
├── .env                      # Secrets and config (never commit)
│
├── bot/                      # All Telegram bot code
│   ├── main.py               # Wires ConversationHandler; runs both bots via asyncio
│   ├── config.py             # Loads all env vars; exposes DATA_DIR
│   ├── states.py             # 9 conversation state constants (integers)
│   ├── utils.py              # get_main_keyboard() — the 3-button main menu
│   │
│   ├── patient_bot/          # Patient bot handlers + services
│   │   ├── start.py          # /start entry point; back_to_main callback
│   │   ├── schedule.py       # Booking flow: week → days → hours → intake confirm → intake Q&A
│   │   ├── cancel.py         # Cancel flow: list appointments → confirm delete
│   │   ├── therapist.py      # Relay flow: prompt → forward to therapist → end chat
│   │   └── services/
│   │       ├── ai_intake.py      # LangChain + Ollama adaptive intake questionnaire
│   │       ├── appointments.py   # File-based JSON appointment storage in data/appointments/
│   │       ├── availability.py   # Google Calendar availability; stub fallback if not set up
│   │       └── relay.py          # relay_sessions.json — maps msg IDs to patient IDs
│   │
│   └── therapist_bot/        # Therapist-facing bot (separate Telegram bot)
│       ├── main.py           # build_therapist_app() — registers the reply handler
│       ├── handlers.py       # handle_therapist_reply — routes replies back to patients
│       └── services/
│           └── relay.py      # relay_sessions.json — same shared file as patient_bot
│
├── web/                      # Therapist web dashboard (FastAPI — multi-page)
│   ├── app.py                # Routes: /, /schedule, /patients, /messages, /settings, /treatment/…
│   ├── gcal.py               # Google Calendar OAuth + API wrapper
│   ├── templates/
│   │   ├── base.html         # Shared sidebar layout (zf- CSS namespace)
│   │   ├── dashboard.html    # / — today's schedule + stats
│   │   ├── schedule.html     # /schedule — FullCalendar availability manager
│   │   ├── patients.html     # /patients — searchable patient list
│   │   ├── treatment.html    # /treatment/{id}/{date}/{time}
│   │   ├── messages.html     # /messages — intake conversation viewer
│   │   └── settings.html     # /settings
│   └── static/
│       ├── style.css         # zf- prefixed styles + calendar styles
│       └── app.js            # FullCalendar JS (schedule page only)
│
└── data/                     # Runtime data — auto-created, do not commit
    ├── appointments/
    │   └── {patient_id}/     # One JSON file per active appointment
    │       └── YYYY-MM-DD_HH-MM.json
    ├── chat_history/
    │   └── {user_id}_intake.json   # Temporary LangChain history, cleared after booking
    ├── relay_sessions.json   # Active relay mappings: therapist msg ID → patient ID
    └── google_token.json     # Google OAuth token (auto-created on first login)
```

---

## Component Details

### `bot/main.py` — Bot orchestrator

Builds the patient `ConversationHandler` with all states and handlers, then runs both bots concurrently in the same Python process:

```python
async with patient_app, therapist_app:
    await patient_app.updater.start_polling(...)
    await therapist_app.updater.start_polling(...)
    await asyncio.Event().wait()   # keep alive until Ctrl+C
```

Also starts Ollama automatically on boot if it is not already running.

**Critical setting:** `allow_reentry=False` on the `ConversationHandler`. If set to True, the catch-all entry point (`MessageHandler(filters.ALL, start)`) intercepts text messages mid-flow (e.g. during intake or therapist relay), breaking those states.

---

### `bot/config.py` — Environment variables

Loads `.env` via `python-dotenv`. All other modules import from here — nothing reads `os.environ` directly.

| Variable | Used by |
|---|---|
| `TELEGRAM_TOKEN` | Patient bot |
| `THERAPIST_BOT_TOKEN` | Therapist bot + patient relay handler |
| `THERAPIST_TELEGRAM_ID` | Therapist bot — filters messages to this user only |
| `OLLAMA_MODEL`, `OLLAMA_HOST` | `ai_intake.py` |
| `GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI` | `web/gcal.py` |
| `DATA_DIR` | `appointments.py`, `relay.py`, `ai_intake.py` |

---

### `bot/states.py` — State machine constants

Nine integer constants unpacked from `range(9)`:

```
SELECTING → SCHEDULE_WEEK → SCHEDULE_DAY → SCHEDULE_HOUR → INTAKE_CONFIRM → INTAKE
          → CANCEL_SELECT
          → THERAPIST_INPUT → THERAPIST_RELAY
```

---

### `bot/patient_bot/start.py` — Entry point

- `start()` — shown on `/start` or any unrecognised message; renders main menu
- `back_to_main()` — callback for every "⬅️ Back" button; returns to `SELECTING`

---

### `bot/patient_bot/schedule.py` — Booking flow

| Function | State transition |
|---|---|
| `show_week_choice()` | `SELECTING → SCHEDULE_WEEK` |
| `show_days()` | `SCHEDULE_WEEK → SCHEDULE_DAY` |
| `show_hours()` | `SCHEDULE_DAY → SCHEDULE_HOUR` |
| `confirm_appointment()` | `SCHEDULE_HOUR → INTAKE_CONFIRM` |
| `start_intake()` | `INTAKE_CONFIRM → INTAKE` (patient said Yes) |
| `skip_intake()` | `INTAKE_CONFIRM → SELECTING` (patient said No — saves appointment immediately) |
| `handle_intake_answer()` | `INTAKE → INTAKE` (loops up to 5 times) → `SELECTING` on last answer |

The intake loop asks 5 adaptive questions via Ollama, then calls `generate_summary()` and saves everything to the appointment JSON.

---

### `bot/patient_bot/cancel.py` — Cancellation flow

| Function | What it does |
|---|---|
| `show_appointments()` | Fetches active appointments; renders a numbered list |
| `confirm_cancel()` | Deletes the appointment JSON file; clears intake history |

Cancellation is a hard delete — no soft-delete or status flag. `get_patient_appointments()` only returns files that exist with `status == "active"`.

---

### `bot/patient_bot/therapist.py` — Patient side of relay

| Function | What it does |
|---|---|
| `ask_therapist_message()` | Shows prompt; transitions to `THERAPIST_INPUT` |
| `start_relay()` | Sends first message via `Bot(THERAPIST_BOT_TOKEN)` to therapist; saves mapping; moves to `THERAPIST_RELAY` |
| `relay_to_therapist()` | Forwards each subsequent message; sends "✅ Sent" + End Chat button |
| `end_chat()` | Calls `end_relay()`; returns to `SELECTING` |

The `End Chat` button (`therapist_end` callback) is attached to every message the patient receives so they can exit at any time.

---

### `bot/therapist_bot/main.py` — Therapist bot builder

Registers a single `MessageHandler` that only accepts text messages from the therapist's Telegram user ID. Any other user messaging this bot is silently ignored.

---

### `bot/therapist_bot/handlers.py` — Therapist side of relay

`handle_therapist_reply()`:
1. Checks the incoming message has a `reply_to_message` (required — this is the routing key)
2. Looks up the patient ID from `relay_sessions.json` using the replied-to message ID
3. Delivers the reply via `Bot(TELEGRAM_TOKEN).send_message(patient_id, ...)` with an End Chat button
4. If the message is not a reply, sends the therapist a warning to reply properly

**The therapist must reply to the forwarded message** (not type freely). This is the only way the system knows which patient to route to when multiple conversations are active simultaneously.

---

### `bot/patient_bot/services/availability.py` — Slot availability

Two modes:

**Google Calendar mode** (when `data/google_token.json` exists):
- Reads "✅ Available" events from the "ZenFlow Availability" calendar
- Filters out already-booked time slots
- Falls back to stub on any API error

**Stub mode** (no Google Calendar):
- Returns Mon–Fri + Sun for the next 14 days
- Fixed slots: 09:00–14:00 hourly
- Still filters out booked slots

---

### `bot/patient_bot/services/appointments.py` — Appointment storage

Pure file I/O — no database. Each appointment is one JSON file:

```
data/appointments/{patient_id}/{YYYY-MM-DD}_{HH-MM}.json
```

```json
{
  "patient_id": 123456,
  "patient_name": "Jane",
  "date": "2026-03-10",
  "time": "10:00",
  "created_at": "2026-03-01T12:00:00",
  "status": "active",
  "intake_history": [ ... ],
  "summary": "Chief complaint: ..."
}
```

`get_booked_slots(day)` scans all patient directories for that date to find taken slots — this is how double-booking is prevented.

---

### `bot/patient_bot/services/ai_intake.py` — Adaptive intake questionnaire

Uses **LangChain** with **Ollama** (`gemma3:latest` by default):

- `initialize_intake()` — clears history, records the opening question
- `get_next_question()` — appends the patient's answer, calls the LLM, returns the next question
- `generate_summary()` — asks the LLM for a 4–5 bullet clinical summary after the 5th answer
- `clear_intake()` — deletes the history file after the appointment is saved

History is persisted to `data/chat_history/{user_id}_intake.json` via `FileChatMessageHistory`. All LLM calls are wrapped in `asyncio.wait_for(..., timeout=100s)`. If Ollama is unreachable or slow, falls back to five hardcoded questions.

---

### `bot/patient_bot/services/relay.py` + `bot/therapist_bot/services/relay.py` — Relay session tracking

Manages `data/relay_sessions.json`:

```json
{
  "msg_to_patient": { "8821": 918187404 },
  "active_patients": { "918187404": true }
}
```

- `save_relay_mapping(forwarded_msg_id, patient_id)` — called each time a patient message is forwarded
- `get_patient_for_msg(msg_id)` — called by the therapist bot to find who to reply to
- `end_relay(patient_id)` — removes the patient from `active_patients` (mapping entries are kept so late replies still route correctly)

---

### `web/app.py` — FastAPI web dashboard

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Serves calendar view (redirects to login if unauthenticated) |
| `/auth/login` | GET | Redirects to Google OAuth consent screen |
| `/auth/callback` | GET | Receives OAuth code; exchanges for token |
| `/api/events` | GET | Returns calendar events for a date range |
| `/api/availability` | POST | Creates an availability slot |
| `/api/availability/{id}` | DELETE | Removes an availability slot |

---

### `web/gcal.py` — Google Calendar client

Wraps the Google Calendar API v3:
- `get_auth_url()` — builds the OAuth URL
- `exchange_code(code)` — exchanges auth code for token; writes `data/google_token.json`
- `is_authenticated()` — checks if token file exists
- `GCalClient.load()` — loads credentials and builds the API service
- `get_or_create_availability_cal()` — finds or creates the "ZenFlow Availability" calendar
- `create_availability(cal_id, start, end)` — creates a "✅ Available" event
- `delete_availability(cal_id, event_id)` — deletes a slot

---

## Two-bot Relay Architecture

```
Patient                    Patient Bot               Therapist Bot           Therapist
  │                            │                          │                      │
  │── "I have back pain" ──>   │                          │                      │
  │                            │── forward via Bot(THERAPIST_BOT_TOKEN) ──>      │
  │                            │   save_relay_mapping(msg_id=8821, patient=123)  │
  │<── "✅ Sent [End Chat]" ── │                          │                      │
  │                            │                          │<── reply-to 8821 ─── │
  │                            │    get_patient_for_msg(8821) → 123              │
  │<── "👨‍⚕️ Therapist: ..." ──────────────────────────────│                      │
  │    [End Chat]              │                          │                      │
```

Two separate Telegram bots; both run in the same Python process, each with its own `Application` and polling loop.

---

## Conversation State Machine

```
Any message / /start
    └──> SELECTING  (main menu: 3 buttons)
          │
          ├── [Schedule] ──> SCHEDULE_WEEK (This week / Next week)
          │                     └── [pick week] ──> SCHEDULE_DAY
          │                                           └── [pick day] ──> SCHEDULE_HOUR
          │                                                                  └── [pick hour] ──> INTAKE_CONFIRM
          │                                                                                         ├── [Yes] ──> INTAKE (×5) ──> SELECTING
          │                                                                                         └── [No]  ──> SELECTING
          │
          ├── [Cancel] ──> CANCEL_SELECT
          │                   └── [pick appointment] ──> SELECTING
          │
          └── [Therapist] ──> THERAPIST_INPUT
                                  └── [type message] ──> THERAPIST_RELAY (loop)
                                                             ├── [type] ──> THERAPIST_RELAY
                                                             └── [End Chat] ──> SELECTING
```

---

## Runtime Data

All runtime files are auto-created. None should be committed.

| File / Directory | Created by | Purpose |
|---|---|---|
| `data/appointments/{id}/*.json` | `appointments.py` | One file per active booking |
| `data/chat_history/{id}_intake.json` | `ai_intake.py` | Temporary LangChain history |
| `data/relay_sessions.json` | `relay.py` | Therapist ↔ patient message routing |
| `data/google_token.json` | `web/gcal.py` | Google OAuth credentials |
| `botLogs.text` | `bot/main.py` | Combined log for both bots |
