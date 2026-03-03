# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project
ZenFlow Clinic — Telegram bot for a Traditional Chinese Medicine (TCM) acupuncture clinic.

## Commands

```bash
# Start everything (setup + bots + web dashboard)
python launch.py

# Individual services (development)
python run_bots.py       # Telegram bots only (patient + therapist)
python run_web.py        # Web dashboard only  →  http://localhost:8000

# Pull the required AI model (first time only)
ollama pull gemma3:latest
```

## Architecture

```
bot/
├── main.py            # Wires patient ConversationHandler + runs both bots via asyncio
├── states.py          # 9 integer state constants (SELECTING, SCHEDULE_WEEK, SCHEDULE_DAY, …)
├── config.py          # Env vars via python-dotenv (incl. GOOGLE_* vars)
├── utils.py           # Shared: get_main_keyboard()
├── patient_bot/
│   ├── start.py       # Entry point + back_to_main callback
│   ├── schedule.py    # show_week_choice → show_days → show_hours → confirm_appointment → handle_intake_answer
│   ├── cancel.py      # show_appointments → confirm_cancel
│   ├── therapist.py   # ask_therapist_message → start_relay → relay_to_therapist → end_chat
│   └── services/
│       ├── ai_intake.py      # LangChain + Ollama adaptive intake questionnaire
│       ├── appointments.py   # File-based JSON storage in data/appointments/
│       ├── availability.py   # Google Calendar integration; stubs if not configured
│       └── relay.py          # relay_sessions.json — maps therapist msg IDs → patient IDs
└── therapist_bot/
    ├── main.py        # build_therapist_app() — separate bot for therapist
    ├── handlers.py    # handle_therapist_reply — routes therapist replies to patients
    └── services/
        └── relay.py          # relay_sessions.json — same file as patient_bot's relay

web/                         # Therapist web dashboard (FastAPI — multi-page)
├── app.py                   # Routes: /, /schedule, /patients, /messages, /settings, /treatment/…
├── gcal.py                  # Google Calendar OAuth + API wrapper
├── templates/
│   ├── base.html            # Shared sidebar layout (zf- CSS namespace)
│   ├── dashboard.html       # / — today's schedule + stats
│   ├── schedule.html        # /schedule — FullCalendar availability manager
│   ├── patients.html        # /patients — searchable patient list
│   ├── treatment.html       # /treatment/{id}/{date}/{time}
│   ├── messages.html        # /messages — intake conversation viewer
│   └── settings.html        # /settings
└── static/
    ├── style.css            # zf- prefixed styles + calendar styles
    └── app.js               # FullCalendar JS (schedule page only)

data/
├── appointments/{patient_id}/   # one JSON per active appointment: {YYYY-MM-DD}_{HH-MM}.json
├── chat_history/                # {user_id}_intake.json — temp LangChain history, cleared after intake
├── relay_sessions.json          # active relay mappings
└── google_token.json            # Google OAuth token (auto-created, do not commit)
```

## Conversation state machine
```
Any message / /start → SELECTING (main menu)
  SELECTING → schedule  → SCHEDULE_WEEK (This week / Next week)
                          → SCHEDULE_DAY → SCHEDULE_HOUR → INTAKE_CONFIRM
                          → Yes → INTAKE (×5 adaptive AI questions) → SELECTING
                          → No  → SELECTING (saved without intake)
  SELECTING → cancel    → CANCEL_SELECT → SELECTING (file deleted on confirm)
  SELECTING → therapist → THERAPIST_INPUT → THERAPIST_RELAY (loop) → SELECTING
                                             patient types → forwarded to therapist bot
                                             therapist replies → delivered to patient
                                             patient presses End Chat → SELECTING
```

## Two-bot relay architecture
- **Patient bot** (`TELEGRAM_TOKEN`): patient-facing; forwards messages to therapist via `Bot(THERAPIST_BOT_TOKEN)`
- **Therapist bot** (`THERAPIST_BOT_TOKEN`): therapist-facing; receives forwarded messages, routes replies back via `Bot(TELEGRAM_TOKEN)`
- Both bots run concurrently in the same process via `asyncio.run(_run(patient_app, therapist_app))`
- Routing key: `relay_sessions.json` maps therapist-bot message ID → patient user ID
- Therapist **must reply** to the forwarded message (not type freely) for routing to work

## Key conventions
- All Telegram handlers are `async def (update, context) -> int` returning the next state constant.
- `context.user_data` holds in-flight booking state (`selected_day`, `selected_time`, `intake_count`). Cleared on completion, skip, or cancellation.
- `allow_reentry=False` is critical — setting it True causes the catch-all entry point `MessageHandler(filters.ALL, start)` to intercept text messages in active states (INTAKE, THERAPIST_INPUT), breaking those flows.
- Cancelled appointments are **deleted** (not marked). `get_patient_appointments()` returns only files that exist and have `status == "active"`.
- All Ollama calls are wrapped in `asyncio.wait_for(..., timeout=100)`. Fallback questions are used if Ollama is unreachable or slow.
- `patient_bot/services/availability.py` imports `patient_bot/services/appointments.py` (not the other way around) to avoid circular imports.

## Environment variables (`.env`)
| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_TOKEN` | — | Patient bot token from @BotFather |
| `THERAPIST_BOT_TOKEN` | — | Therapist bot token (separate bot from @BotFather) |
| `THERAPIST_TELEGRAM_ID` | — | Therapist's Telegram user ID (integer) |
| `OLLAMA_MODEL` | `gemma3:latest` | Local LLM model name |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `USE_AI` | `ollama` | `ollama` or `anthropic` (future) |
| `GOOGLE_CLIENT_ID` | — | Google OAuth client ID (from Google Cloud Console) |
| `GOOGLE_CLIENT_SECRET` | — | Google OAuth client secret |
| `GOOGLE_REDIRECT_URI` | `http://localhost:8000/auth/callback` | OAuth redirect URI |

## Current status — what works
- Schedule appointment: pick week → pick day → pick hour → optional 5-question AI intake → appointment saved with clinical summary
- Cancel appointment: lists active appointments, deletes the file on confirm, clears chat history
- Connect to therapist: two-way relay via dedicated therapist bot; End Chat button on every message
- Therapist availability frontend: FastAPI + FullCalendar at http://localhost:8000
- Google Calendar integration: availability.py reads "✅ Available" events from "ZenFlow Availability" calendar; stubs if not configured
- Ollama auto-starts on bot startup; fallback questions if model is unavailable

## Planned next steps
- `PicklePersistence` to survive bot restarts
- Switch `USE_AI=anthropic` for production
