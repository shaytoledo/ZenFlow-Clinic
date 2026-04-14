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
│   ├── utils.py                   # get_main_keyboard(show_change_therapist) — 4-button main menu
│   │
│   ├── patient_bot/               # Patient-facing bot handlers
│   │   ├── start.py               # start(), back_to_main(), change_therapist()
│   │   ├── schedule.py            # Booking flow: therapist → week → days → hours → intake
│   │   ├── cancel.py              # Cancel flow: list active → confirm → soft-delete
│   │   ├── therapist.py           # Relay flow: show_therapist_for_contact → relay loop → end
│   │   └── services/
│   │       ├── ai_intake.py       # LangChain + Ollama adaptive intake; Redis history (30 min TTL)
│   │       ├── appointments.py    # SQLite: save/get/cancel appointments + treatment notes
│   │       ├── availability.py    # Google Calendar + SQLite local fallback; book_slot/restore_slot
│   │       └── relay.py           # Redis relay write: maps msg IDs → {patient_id, therapist_id}
│   │
│   └── therapist_bot/             # Therapist-facing bot (separate Telegram token)
│       ├── main.py                # build_therapist_app() — single handler for all therapists
│       ├── handlers.py            # handle_therapist_message: relay OR registration
│       └── services/
│           └── relay.py           # Redis relay read-only; get_patient_for_msg, get_current_patient
│
├── web/                           # Therapist web dashboard (FastAPI — multi-page)
│   ├── app.py                     # FastAPI app factory: middleware + static files + router wiring
│   ├── deps.py                    # Session helpers, auth helpers, data loaders
│   ├── gcal.py                    # Google Calendar OAuth + API wrapper
│   ├── routers/
│   │   ├── pages.py               # HTML page routes: /, /schedule, /patients, /messages,
│   │   │                          #   /settings, /sessions, /treatment/{pid}/{date}/{time}
│   │   ├── auth.py                # Auth routes: /register, /signin, /logout, Google OAuth,
│   │   │                          #   /register/activate
│   │   └── api/
│   │       ├── appointments.py    # /api/appointments/today, /api/patients, /api/patients/{id},
│   │       │                      #   /api/appointment/{pid}/{date}/{time}
│   │       ├── treatment.py       # /api/treatment-notes/* (get, save, rediagnose, send, complete)
│   │       ├── availability.py    # /api/calendars, /api/events, /api/availability (POST/DELETE)
│   │       ├── messages.py        # /api/messages/active, /conversations, /history/{pid}, /send
│   │       └── system.py          # /api/status, /api/my/status, /api/my/activation-code
│   ├── services/                  # Domain service layer (CRUD, caching, Telegram helpers)
│   │   ├── appointment_service.py # list_all(), list_today(), list_by_patient(), aggregate_patients()
│   │   ├── availability_service.py# list_local(), add_local(), remove_local(), to_fc_events()
│   │   ├── treatment_service.py   # get_notes(), save_notes(), complete_session(), list_all_sessions()
│   │   ├── telegram_service.py    # send_to_patient(), get_active_relay_conversations(),
│   │   │                          #   get_relay_messages(), append_relay_message()
│   │   ├── therapist_service.py   # Therapist account helpers
│   │   └── cache_service.py       # prefetch_calendar(), purge_calendar(), get_relay_count()
│   ├── templates/
│   │   ├── base.html              # Shared sidebar layout (zf- CSS namespace)
│   │   ├── dashboard.html         # / — today's schedule + stats
│   │   ├── schedule.html          # /schedule — FullCalendar availability manager
│   │   ├── patients.html          # /patients — searchable patient list
│   │   ├── treatment.html         # /treatment/{id}/{date}/{time} — session notes + AI diagnosis
│   │   ├── messages.html          # /messages — live relay chat + intake history (two tabs)
│   │   ├── sessions.html          # /sessions — all session history, sortable
│   │   ├── settings.html          # /settings — Google Calendar, bot activation
│   │   ├── register.html          # /register — sign-up / sign-in (two-tab card)
│   │   ├── register_done.html     # /register/done — activation code + bot links
│   │   └── register_activate.html # /register/activate — activation code entry
│   └── static/
│       ├── style.css              # zf- prefixed styles + calendar styles
│       └── js/                    # FullCalendar JS — schedule page only (loaded in order)
│           ├── utils.js           # $ helper, showToast, fmt
│           ├── calendar-list.js   # Sidebar calendar list, visibility toggles, rename
│           ├── mini-calendar.js   # Mini date picker (sidebar)
│           ├── slots.js           # saveSlot — drag to create availability
│           ├── popover.js         # Event click popover: show, position, delete
│           └── main-calendar.js   # FullCalendar init + DOMContentLoaded wiring
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
│   ├── DATA_LAYER.md              # Living doc: full data inventory, TTL, breaking points, runbook
│   └── TECHNICAL_DECISIONS.md    # Architecture decision records (ADRs)
│
├── CLAUDE.md                      # Claude Code instructions (stays at root)
└── README.md                      # Project roadmap + pending tasks
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

## Frontend JavaScript Modules (`web/static/js/`)

The schedule page (`/schedule`) is the only page that loads JavaScript. The monolithic `app.js` was split into six focused modules loaded in order via `{% block extra_scripts %}` in `schedule.html`:

| File | Responsibility | Key exports |
|---|---|---|
| `utils.js` | DOM helper + toast notifications | `$()`, `showToast()`, `fmt()` |
| `calendar-list.js` | Sidebar calendar list, visibility toggles, context-menu rename | `_hiddenCals`, `loadCalendarList()` |
| `mini-calendar.js` | Mini month picker in sidebar | `miniDate`, `renderMiniCal()` |
| `slots.js` | Drag-to-create availability slot | `saveSlot()` |
| `popover.js` | Event click popover (info + delete confirm) | `showEventPopover()`, `closeEventPopover()`, `deleteSlot()` |
| `main-calendar.js` | FullCalendar init, event renderer, DOMContentLoaded wiring | `mainCal` |

**Load order matters.** All files share the browser's global scope. `main-calendar.js` is last because it references symbols (`_hiddenCals`, `showToast`, `saveSlot`, `showEventPopover`, `renderMiniCal`) defined in earlier files. `mainCal` is declared in `main-calendar.js` and referenced by `calendar-list.js` and `mini-calendar.js` at runtime (after `DOMContentLoaded` fires), so forward-reference is safe.

---

## Web Dashboard Routes

### Pages

| Route | Auth | Description |
|---|---|---|
| `GET /` | Yes | Dashboard — today's appointments + stats |
| `GET /schedule` | Yes | FullCalendar availability manager |
| `GET /patients` | Yes | Searchable patient list |
| `GET /treatment/{id}/{date}/{time}` | Yes | Per-session treatment notes + AI diagnosis |
| `GET /sessions` | Yes | All session history (sortable by name/date/last access) |
| `GET /messages` | Yes | Live relay chat + intake history (two tabs) |
| `GET /settings` | Yes | Google Calendar, bot activation code |
| `GET /register` | No | Sign-up / sign-in (two-tab card) |
| `GET /register/done` | No | Activation code display + bot links |
| `GET /register/activate` | No | Activation code entry form |
| `POST /register/signup` | No | Create account |
| `POST /register/signin` | No | Sign in |
| `POST /register/activate` | No | Submit activation code (sets active=True) |
| `GET /register/google` | No | Start Google OAuth registration |
| `GET /register/google/callback` | No | Complete Google OAuth registration |
| `GET /auth/login` | No | Redirect to Google OAuth (calendar) |
| `GET /auth/callback` | No | Complete Google Calendar OAuth |
| `POST /auth/disconnect` | Yes | Remove Google Calendar token |
| `GET /logout` | No | Clear session, redirect to /register |

### API

| Route | Description |
|---|---|
| `GET /api/appointments/today` | Today's active appointments (JSON) |
| `GET /api/patients` | All patients aggregated from appointments (JSON) |
| `GET /api/patients/{patient_id}` | Patient detail + appointment list (JSON) |
| `GET /api/appointment/{pid}/{date}/{time}` | Single appointment detail (JSON) |
| `GET /api/treatment-notes/{pid}/{date}/{time}` | Fetch treatment notes (JSON) |
| `POST /api/treatment-notes/{pid}/{date}/{time}` | Save treatment notes |
| `POST /api/treatment-notes/{pid}/{date}/{time}/rediagnose` | Re-generate TCM AI diagnosis |
| `POST /api/treatment-notes/{pid}/{date}/{time}/send-recommendations` | Send recommendations to patient via Telegram |
| `POST /api/treatment-notes/{pid}/{date}/{time}/complete` | Mark session completed |
| `GET /api/calendars` | List Google Calendar calendars (JSON) |
| `GET /api/events?start=X&end=Y` | FullCalendar events — Google or local (JSON) |
| `POST /api/availability` | Create availability slot |
| `DELETE /api/availability/{id}` | Delete availability slot |
| `GET /api/messages/active` | Count of active relay sessions (JSON) |
| `GET /api/messages/conversations` | List active relay conversations (JSON) |
| `GET /api/messages/history/{patient_id}` | Relay chat history for patient (JSON) |
| `POST /api/messages/send` | Send message to patient via therapist bot |
| `GET /api/status` | System health snapshot (Redis, Ollama, bots, Google Calendar) |
| `GET /api/my/status` | Current therapist status (active, name) |
| `GET /api/my/activation-code` | Generate new 8-char bot activation code |
