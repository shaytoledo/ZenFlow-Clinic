# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project
ZenFlow Clinic — Telegram bot for a Traditional Chinese Medicine (TCM) acupuncture clinic.

## Commands

```bash
# Start everything (setup + bots + web dashboard) — run from project root
python startup/launch.py

# Individual services (development)
python startup/run_bots.py          # Telegram bots only (patient + therapist)
python startup/run_web.py           # Web dashboard only  →  http://localhost:8000

# Pull the required AI model (first time only)
ollama pull gemma3:latest
```

> Full start guide: `startup/START.md`

## Documentation

All technical documentation lives in `docs/` — one file per topic:

| File | Topic |
|---|---|
| `docs/ARCHITECTURE.md` | System overview, file tree, component map, env vars |
| `docs/MEMORY_MANAGEMENT.md` | All memory layers: Redis, in-process dicts, SQLite, sessions — full lifecycle |
| `docs/REDIS.md` | Redis key schema, TTLs, eviction, invalidation patterns |
| `docs/DATABASE.md` | SQLite schema, WAL mode, autocommit, all 5 tables |
| `docs/ERD.md` | Entity Relationship Diagram (Mermaid) |
| `docs/BOT_FLOWS.md` | Conversation state machine, all handler flows |
| `docs/RELAY.md` | Two-bot relay architecture |
| `docs/AI_INTAKE.md` | Ollama/LangChain adaptive intake |
| `docs/AUTH.md` | Web auth, registration, session management |
| `docs/AVAILABILITY.md` | Google Calendar vs local SQLite availability |
| `docs/TECHNICAL_DECISIONS.md` | Architecture decision records (ADRs) |

> Start guide: `startup/START.md`

## Architecture

```
bot/
├── main.py            # Wires patient ConversationHandler + runs both bots via asyncio
├── db.py              # SQLite singleton: get_db(), init_db(), 5-table schema
├── redis_client.py    # get_async_redis() / get_sync_redis() singletons
├── states.py          # 10 integer state constants (SELECTING, THERAPIST_SELECT, …)
├── config.py          # Env vars; calls init_db(); loads THERAPISTS from SQLite
├── utils.py           # Shared: get_main_keyboard()
├── patient_bot/
│   ├── start.py       # Entry point + back_to_main callback
│   ├── schedule.py    # Booking flow: therapist → week → days → hours → intake
│   ├── cancel.py      # show_appointments → confirm_cancel (soft-delete)
│   ├── therapist.py   # ask_therapist_message → relay loop → end_chat
│   └── services/
│       ├── ai_intake.py      # LangChain + Ollama adaptive intake; Redis history (30 min TTL)
│       ├── appointments.py   # SQLite: save/get/cancel appointments + get_booked_slots
│       ├── availability.py   # Google Calendar + SQLite local fallback; book_slot / restore_slot
│       └── relay.py          # Redis relay: maps therapist msg IDs → {patient_id, therapist_id}
└── therapist_bot/
    ├── main.py        # build_therapist_app() — single shared bot for all therapists
    ├── handlers.py    # handle_therapist_message: relay OR registration
    └── services/
        └── relay.py   # Redis relay (read-only)

web/                         # Therapist web dashboard (FastAPI — multi-page)
├── app.py                   # Routes + auth (SessionMiddleware) + all data access via SQLite
├── gcal.py                  # Google Calendar OAuth + API wrapper
├── templates/               # Jinja2 templates (all extend base.html)
└── static/                  # style.css + app.js (FullCalendar)

startup/
├── launch.py                # Unified launcher: setup + Redis + Ollama + supervises services
├── run_bots.py              # Bots only (development)
├── run_web.py               # Web only (development, hot-reload)
└── START.md                 # Human start guide

data/
├── zenflow.db               # SQLite database (WAL mode) — primary data store
└── google_tokens/           # Per-therapist Google OAuth tokens (auto-created, never commit)
    └── {id}.json            #   e.g. t1.json, t2.json

docs/                        # All documentation (one file per topic)
logs/                        # botLogs.text + webLogs.text (auto-created)
```

## Conversation state machine
```
Any message / /start → SELECTING (main menu)
  SELECTING → schedule  → THERAPIST_SELECT (skip if 1 therapist)
                          → SCHEDULE_WEEK → SCHEDULE_DAY → SCHEDULE_HOUR → INTAKE_CONFIRM
                          → Yes → INTAKE (×5 adaptive AI questions) → SELECTING
                          → No  → SELECTING (saved without intake)
  SELECTING → cancel    → CANCEL_SELECT → SELECTING (status='cancelled', record kept)
  SELECTING → therapist → THERAPIST_SELECT (skip if 1) → THERAPIST_INPUT
                          → THERAPIST_RELAY (loop) → SELECTING
```

## Two-bot relay architecture
- **Patient bot** (`TELEGRAM_TOKEN`): patient-facing; forwards messages via `Bot(THERAPIST_BOT_TOKEN)`
- **Therapist bot** (`THERAPIST_BOT_TOKEN`): shared by all therapists; routes replies back via `Bot(TELEGRAM_TOKEN)`
- Both bots run concurrently in the same process via `asyncio.run(_run(patient_app, therapist_app))`
- Routing key: Redis `zenflow:relay:msg:{msg_id}` stores `{patient_id, therapist_id}`

## Key conventions
- All Telegram handlers are `async def (update, context) -> int` returning the next state constant.
- `context.user_data` holds in-flight booking state (`selected_therapist`, `selected_day`, `selected_time`, `intake_count`). Cleared on completion, skip, or cancellation.
- `allow_reentry=False` is critical — setting it True breaks INTAKE and THERAPIST_INPUT states.
- Cancelled appointments are **soft-deleted** (`status='cancelled'`). Records preserved for clinical history.
- `cancel_appointment(appointment_id: int)` takes an integer row ID from SQLite.
- All Ollama calls are wrapped in `asyncio.wait_for(..., timeout=100)`. Fallback questions used if unavailable.
- `availability.py` may import `appointments.py` — not the other way around (circular import risk).
- SQLite `active` column is `INTEGER` (0/1); always cast: `bool(t.get("active"))`.

## data/ files
| File | Purpose |
|---|---|
| `zenflow.db` | Primary database — all clinical and operational data |
| `google_tokens/{id}.json` | Per-therapist Google OAuth token — auto-created on Calendar connect, never commit |

## Environment variables (`.env`)
| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_TOKEN` | — | Patient bot token from @BotFather |
| `THERAPIST_BOT_TOKEN` | — | Therapist bot token (separate bot) |
| `OLLAMA_MODEL` | `gemma3:latest` | Local LLM model name |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `USE_AI` | `ollama` | `ollama` or `anthropic` (future) |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `SESSION_SECRET` | — | Signs `zf_session` cookie (web dashboard) |
| `GOOGLE_CLIENT_ID` | — | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | — | Google OAuth client secret |
| `GOOGLE_REDIRECT_URI` | `http://localhost:8000/auth/callback` | Calendar OAuth redirect |
| `GOOGLE_REG_REDIRECT_URI` | `http://localhost:8000/register/google/callback` | Registration OAuth redirect |

## What works
- Appointment booking: therapist select → week → day → hour → optional AI intake → saved to SQLite
- Appointment cancellation: soft delete, slot restored to availability
- Two-way therapist relay with multi-therapist security isolation
- Therapist web dashboard (FastAPI) with FullCalendar availability manager
- Therapist registration: web form → 8-char code → bot activation; Google OAuth supported
- Per-therapist Google Calendar integration; local SQLite fallback when not connected
- Ollama adaptive intake with Redis history; fallback questions when unavailable
- Treatment notes: AI TCM diagnosis saved on booking; therapist adds tongue/pulse/points/notes

## Planned
- `PicklePersistence` to survive bot restarts without losing in-flight booking state
- Switch `USE_AI=anthropic` for production Claude API
