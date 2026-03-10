# ZenFlow Clinic — System Architecture

> **Documentation index:** See `docs/` for all topic files.
> Start guide: `startup/START.md`

---

## Overview

ZenFlow is a Telegram-based clinic management system for a Traditional Chinese Medicine (TCM) acupuncture clinic. Three services run concurrently from a single launch command:

| Service | Entry point | Port/Protocol | Purpose |
|---|---|---|---|
| Patient bot | `startup/run_bots.py` → `bot/main.py` | Telegram polling | Patients book, cancel, chat |
| Therapist bot | Same process as patient bot | Telegram polling | Therapist receives and replies |
| Web dashboard | `startup/run_web.py` → `web/app.py` | HTTP :8000 | Therapist dashboard (FastAPI) |

All three start together with `python startup/launch.py`.

---

## Project File Tree

```
Clinic/
│
├── startup/                       # Launch scripts (run from project root)
│   ├── launch.py                  # Unified launcher: setup + Redis + Ollama + all services
│   ├── run_bots.py                # Bots only (development)
│   ├── run_web.py                 # Web only (development)
│   └── START.md                   # Human start guide
│
├── bot/                           # All Telegram bot code
│   ├── main.py                    # Wires ConversationHandler; asyncio.run(_run(patient, therapist))
│   ├── config.py                  # Loads .env; calls init_db(); loads THERAPISTS from SQLite
│   ├── db.py                      # SQLite singleton: get_db(), init_db(), 5-table schema
│   ├── redis_client.py            # get_async_redis() / get_sync_redis() singletons
│   ├── states.py                  # 10 integer conversation state constants
│   ├── utils.py                   # get_main_keyboard() — 3-button main menu
│   │
│   ├── patient_bot/               # Patient-facing bot handlers
│   │   ├── start.py               # /start entry point; back_to_main callback
│   │   ├── schedule.py            # Booking flow: therapist → week → days → hours → intake
│   │   ├── cancel.py              # Cancel flow: list active → confirm → soft-delete
│   │   ├── therapist.py           # Relay flow: prompt → forward → loop → end
│   │   └── services/
│   │       ├── ai_intake.py       # LangChain + Ollama adaptive intake questionnaire
│   │       ├── appointments.py    # SQLite: save/get/cancel appointments
│   │       ├── availability.py    # Google Calendar + SQLite local fallback
│   │       └── relay.py           # Redis relay write: maps msg IDs → patient/therapist
│   │
│   └── therapist_bot/             # Therapist-facing bot (separate Telegram token)
│       ├── main.py                # build_therapist_app() — single handler for all therapists
│       ├── handlers.py            # handle_therapist_message: relay OR registration
│       └── services/
│           └── relay.py           # Redis relay read-only (writes done by patient relay.py)
│
├── web/                           # Therapist web dashboard (FastAPI — multi-page)
│   ├── app.py                     # All routes + auth + data access via SQLite
│   ├── deps.py                    # FastAPI dependency helpers
│   ├── gcal.py                    # Google Calendar OAuth + API wrapper
│   ├── routers/
│   │   └── api/
│   │       └── treatment.py       # /api/treatment-notes/* endpoints
│   ├── templates/
│   │   ├── base.html              # Shared sidebar layout (zf- CSS namespace)
│   │   ├── dashboard.html         # / — today's schedule + stats
│   │   ├── schedule.html          # /schedule — FullCalendar availability manager
│   │   ├── patients.html          # /patients — searchable patient list
│   │   ├── treatment.html         # /treatment/{id}/{date}/{time}
│   │   ├── messages.html          # /messages — intake conversation viewer
│   │   ├── settings.html          # /settings — Google Calendar, bot activation
│   │   ├── register.html          # /register — sign-up / sign-in (two-tab card)
│   │   ├── register_done.html     # /register/done — activation code + bot links
│   │   └── register_activate.html # /register/activate — waiting for bot activation
│   └── static/
│       ├── style.css              # zf- prefixed styles + calendar styles
│       └── app.js                 # FullCalendar JS (schedule page only)
│
├── data/                          # Runtime data — see table below
│   ├── zenflow.db                 # SQLite database (WAL mode) — primary data store
│   └── google_tokens/             # Per-therapist Google OAuth tokens (never commit)
│       └── {therapist_id}.json    #   e.g. t1.json, t2.json
│
├── logs/
│   ├── botLogs.text               # Combined log for patient + therapist bots
│   └── webLogs.text               # Web dashboard uvicorn log
│
├── docs/                          # All project documentation (one file per topic)
│   ├── ARCHITECTURE.md            # ← this file
│   ├── MEMORY_MANAGEMENT.md       # All memory layers: lifecycle, eviction, invalidation
│   ├── REDIS.md                   # Redis key schema, TTLs, access patterns
│   ├── DATABASE.md                # SQLite schema, WAL, connections, transactions
│   ├── ERD.md                     # Entity Relationship Diagram (Mermaid)
│   ├── BOT_FLOWS.md               # Conversation state machine, all handler flows
│   ├── RELAY.md                   # Two-bot relay architecture
│   ├── AI_INTAKE.md               # Ollama/LangChain adaptive intake
│   ├── AUTH.md                    # Web auth, registration, session management
│   ├── AVAILABILITY.md            # Google Calendar vs local SQLite availability
│   └── TECHNICAL_DECISIONS.md    # Architecture decision records (ADRs)
│
├── CLAUDE.md                      # Claude Code instructions (stays at root)
├── ARCHITECTURE.md                # → moved to docs/ARCHITECTURE.md
└── TECHNICAL_DECISIONS.md         # → moved to docs/TECHNICAL_DECISIONS.md
```

---

## Service Startup Sequence

When `python startup/launch.py` is run:

```
1. Python 3.11+ check
2. Create / activate .venv
3. pip install -r requirements.txt
4. Validate .env (TELEGRAM_TOKEN required)
5. Start Redis  (Windows service → binary → error)
6. Start Ollama (ollama serve → pull model if missing)
7. Start Telegram bots subprocess  (startup/run_bots.py)
8. Start web dashboard subprocess  (startup/run_web.py)
9. Supervise loop: restart bots on crash (up to 5×); exit on web crash
```

---

## Runtime Startup Order (within each process)

### Bot process (`startup/run_bots.py` → `bot/main.py`)

```
import bot.config
    → load_dotenv()
    → init_db()        # creates tables, runs schema migrations
    → load THERAPISTS, THERAPIST_MAP, THERAPIST_BY_ID from SQLite

import bot.patient_bot.services.ai_intake
    → create ChatOllama singleton (_LLM)
    → _check_ollama_health()  # logs warning if Ollama unreachable

asyncio.run(_run(patient_app, therapist_app))
    → both bots poll Telegram concurrently
```

### Web process (`startup/run_web.py` → `web/app.py`)

```
uvicorn starts FastAPI app
    → SessionMiddleware attached (SESSION_SECRET)
    → All routes registered
    → First request triggers SQLite connection (thread-local)
```

---

## Component Interaction Map

```
┌─────────────────────────────────────────────────────────────────┐
│  PATIENT                                                          │
│  Telegram                                                         │
└────────┬────────────────────────────────────────────────────────┘
         │ sends message
         ▼
┌─────────────────────┐        ┌─────────────────────────────────┐
│   PATIENT BOT        │        │   THERAPIST BOT                  │
│   (TELEGRAM_TOKEN)   │◄──────►│   (THERAPIST_BOT_TOKEN)          │
│                      │        │                                  │
│  handlers:           │        │  handlers:                       │
│  - start.py          │        │  - handlers.py (relay / reg)     │
│  - schedule.py       │        │                                  │
│  - cancel.py         │        └─────────────┬───────────────────┘
│  - therapist.py      │                      │
│  - services/         │                      │ reply via Bot(TELEGRAM_TOKEN)
└────────┬────────────┘                      │
         │                                    ▼
         │        ┌──────────────────────────────────────────────┐
         ├───────►│   REDIS                                       │
         │        │   - intake history (LangChain)                │
         │        │   - relay routing                             │
         │        │   - availability cache                        │
         │        │   - appointment cache                         │
         │        │   - registration codes                        │
         │        └──────────────────────────────────────────────┘
         │
         │        ┌──────────────────────────────────────────────┐
         ├───────►│   SQLITE (data/zenflow.db)                    │
         │        │   - therapists                                │
         │        │   - appointments                              │
         │        │   - intake_sessions                           │
         │        │   - availability                              │
         │        │   - treatment_notes                           │
         │        └──────────────────────────────────────────────┘
         │
         │        ┌──────────────────────────────────────────────┐
         └───────►│   OLLAMA (localhost:11434)                    │
                  │   - gemma3:latest                             │
                  │   - adaptive intake questions                 │
                  │   - clinical summary                          │
                  │   - TCM diagnosis                             │
                  └──────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│   WEB DASHBOARD (FastAPI :8000)                                  │
│   - shares SQLite DB with bot process (WAL mode)                 │
│   - shares Redis with bot process                                │
│   - therapist-only (session-gated)                               │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Conventions

| Convention | Rule |
|---|---|
| Handler signature | `async def handler(update, context) -> int` |
| State return | Returns next integer state constant from `bot/states.py` |
| `context.user_data` | Holds in-flight booking keys: `selected_therapist`, `selected_day`, `selected_time`, `intake_count` |
| `allow_reentry` | **Must be `False`** — True breaks INTAKE and THERAPIST_INPUT states |
| Cancellation | Soft delete only: `status='cancelled'`, record preserved forever |
| `cancel_appointment` arg | Takes `int` row ID (not file path) |
| SQLite `active` column | `INTEGER` (0/1) — always cast: `bool(t.get("active"))` |
| Circular import rule | `availability.py` may import `appointments.py`, not vice versa |
| Google Calendar calls | Always wrapped in `asyncio.to_thread()` (sync library) |

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_TOKEN` | — | Patient bot token (@BotFather) |
| `THERAPIST_BOT_TOKEN` | — | Therapist bot token (separate bot) |
| `OLLAMA_MODEL` | `gemma3:latest` | Local LLM model |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `USE_AI` | `ollama` | `ollama` or `anthropic` (future) |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `SESSION_SECRET` | — | Signs `zf_session` cookie (web) |
| `GOOGLE_CLIENT_ID` | — | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | — | Google OAuth client secret |
| `GOOGLE_REDIRECT_URI` | `http://localhost:8000/auth/callback` | Calendar OAuth redirect |
| `GOOGLE_REG_REDIRECT_URI` | `http://localhost:8000/register/google/callback` | Registration OAuth redirect |

---

## Runtime Data Files

| File | Created by | Purpose | Commit? |
|---|---|---|---|
| `data/zenflow.db` | `bot/db.py` on first run | Primary database — all clinical and operational data | No |
| `data/google_tokens/{id}.json` | `web/gcal.py` OAuth flow | Per-therapist Google OAuth credentials. Auto-created on Calendar connect. | **Never** |
| `logs/botLogs.text` | `startup/run_bots.py` | Combined log — patient + therapist bots | No |
| `logs/webLogs.text` | `startup/run_web.py` | Web dashboard uvicorn log | No |

## Web Dashboard Routes

| Route | Auth required | Description |
|---|---|---|
| `GET /` | Yes | Dashboard — today's appointments + stats |
| `GET /schedule` | Yes | FullCalendar availability manager |
| `GET /patients` | Yes | Searchable patient list |
| `GET /treatment/{id}/{date}/{time}` | Yes | Per-session treatment notes |
| `GET /sessions` | Yes | All session history (sortable) |
| `GET /messages` | Yes | Intake conversation viewer |
| `GET /settings` | Yes | Google Calendar, bot activation code |
| `GET /register` | No | Sign-up / sign-in |
| `GET /register/done` | No | Activation code display |
| `GET /register/activate` | No | Waiting for bot activation |
| `POST /register/signup` | No | Create account |
| `POST /register/signin` | No | Sign in |
| `GET /logout` | No | Clear session |
