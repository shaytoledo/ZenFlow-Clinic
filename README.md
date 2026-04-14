# ZenFlow Clinic

Telegram-based appointment booking and therapist management system for a Traditional Chinese Medicine (TCM) acupuncture clinic.

Three services run from a single command: a patient-facing Telegram bot, a therapist Telegram bot, and a therapist web dashboard.

---

## Quick Start

```bash
# 1. Copy and fill in environment variables
cp .env.example .env   # edit with your tokens

# 2. Pull the AI model (first time only)
ollama pull gemma3:latest

# 3. Start everything
python startup/launch.py
```

Web dashboard → `http://localhost:8000`

Full start guide: `startup/START.md`

---

## What the System Does

### Patient (Telegram bot)
- Book an appointment: choose therapist → week → day → hour → optional AI intake
- Cancel an appointment (soft delete, slot restored)
- Chat with their assigned therapist via relay messaging

### Therapist (Telegram bot)
- Receive forwarded patient messages
- Reply to patients (reply-to the forwarded message for routing)
- Register/activate account by sending the 8-character code to the bot

### Therapist (Web dashboard at `:8000`)
- View today's schedule and upcoming appointments
- Manage availability slots via FullCalendar drag-to-create
- Connect Google Calendar for availability sync
- View patient histories and intake summaries
- Write treatment notes (tongue/pulse observations, acupuncture points used)
- Read and respond to live patient relay conversations
- View all session history with AI TCM diagnoses

---

## Architecture

Three services, one launch command:

| Service | Entry point | Purpose |
|---|---|---|
| Patient bot | `startup/run_bots.py` → `bot/main.py` | Patient-facing (Telegram polling) |
| Therapist bot | Same process | Therapist relay + registration |
| Web dashboard | `startup/run_web.py` → `web/app.py` | FastAPI dashboard on `:8000` |

Both bots run in the same Python process via `asyncio.run(_run(patient_app, therapist_app))`.

### Data stores

| Store | What lives there |
|---|---|
| **SQLite** `data/zenflow.db` | Therapists, appointments, intake sessions, availability slots, treatment notes |
| **Redis** `localhost:6379` | AI intake history, relay routing, availability/appointment caches, activation codes |
| **Google Calendar** (optional) | Per-therapist availability slots and appointment events |
| **In-process** | Therapist registry, LLM singleton, intake history cache |

### Two-bot relay

The patient bot (`TELEGRAM_TOKEN`) forwards patient messages to therapists via `Bot(THERAPIST_BOT_TOKEN)`. When the therapist replies-to the forwarded message, the therapist bot routes it back to the patient via `Bot(TELEGRAM_TOKEN)`. The routing key is stored in Redis: `zenflow:relay:msg:{forwarded_msg_id}`.

---

## Web Dashboard Pages

| URL | What it shows |
|---|---|
| `/` | Today's appointments + quick stats |
| `/schedule` | FullCalendar 7-day view — availability slots (green) + booked appointments. Drag to create availability. |
| `/patients` | All patients with session count and last appointment date. Click to view per-patient history. |
| `/sessions` | All sessions across all patients, sortable by name / date / last access. "Complete Session" button. |
| `/treatment/{id}/{date}/{time}` | Per-session clinical editor: AI diagnosis, tongue/pulse observations, acupuncture points, therapist notes. |
| `/messages` | Two tabs — **Active Relay**: live patient conversations. **Intake History**: all intake questionnaire transcripts. |
| `/settings` | Connect/disconnect Google Calendar. View therapist activation code for bot registration. |
| `/register` | Public sign-up / sign-in (email+password or Google OAuth). |
| `/register/activate` | Enter 8-character activation code to link Telegram account. |

---

## Bot Conversation Flows

```
/start or any message
    │
    ▼
SELECTING (main menu)
    ├── 📅 Schedule → THERAPIST_SELECT → SCHEDULE_WEEK → SCHEDULE_DAY
    │                → SCHEDULE_HOUR → INTAKE_CONFIRM
    │                → [Yes] INTAKE (×5 AI questions) → save → SELECTING
    │                → [No]  save immediately          → SELECTING
    │
    ├── ❌ Cancel   → CANCEL_SELECT → confirm → soft-delete, restore slot → SELECTING
    │
    └── 💬 Connect → THERAPIST_SELECT → THERAPIST_INPUT → THERAPIST_RELAY (loop)
                                                         → [End Chat] → SELECTING
```

`allow_reentry=False` on the ConversationHandler is critical — do not change it.

---

## AI Intake System

When a patient completes all 5 intake questions:

1. **`generate_summary()`** — LLM generates a 4–5 bullet clinical summary (awaited; used in booking confirmation)
2. **`generate_tcm_diagnosis()`** — LLM generates structured TCM JSON: pattern, treatment principles, confidence %, suggested acupuncture points with rationale, lifestyle recommendations (runs **in background** — patient sees confirmation immediately)
3. Both are saved to SQLite. The summary is shown to the patient; the full diagnosis is shown to the therapist on the treatment page.

**Speed optimisations:**
- Singleton LLM instances (`_LLM` for questions — 100 token cap; `_LLM_LONG` for summary/diagnosis — 600 token cap)
- Redis-backed chat history reused per user (avoids `LRANGE` on every call)
- `_BUFFER_MAX=12` prevents mid-intake compression (max intake = 10 messages)
- `_gcal_service()` runs in `asyncio.to_thread()` — token refresh doesn't block the event loop
- TCM diagnosis fires as a background asyncio task — user receives booking confirmation without waiting for the second LLM call

---

## Memory Management

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 1 — Python in-process (lost on restart)                       │
│  _LLM / _LLM_LONG   ChatOllama singletons                           │
│  _history_cache      dict[user_id → RedisChatMessageHistory]        │
│  _rolling_summaries  dict[user_id → compressed summary str]         │
│  context.user_data   in-flight booking state (per Telegram user)    │
│  THERAPIST_MAP       dict[telegram_id → therapist] (from SQLite)    │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 2 — Redis (shared bot+web, survives restarts, 1 GB LRU cap)  │
│  Intake history · relay routing · availability/slot caches          │
│  appointment list cache · activation codes                           │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 3 — SQLite (permanent source of truth)                        │
│  5 tables: therapists · appointments · intake_sessions              │
│  availability · treatment_notes                                      │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 4 — Session cookie (web browser)                              │
│  zf_session cookie — signed HMAC, 30-day max-age                    │
│  Contains: therapist_id                                              │
└─────────────────────────────────────────────────────────────────────┘
```

**Redis key TTLs:**

| Key | TTL |
|---|---|
| `zenflow:intake:{patient_id}` | 1800 s (30 min) |
| `zenflow:relay:msg:{msg_id}` | 86400 s (24 h) |
| `zenflow:relay:active:{patient_id}` | **None** — explicit delete only |
| `zenflow:relay:history:{patient_id}` | 1800 s |
| `zenflow:slots:{date}` | 300 s (5 min) |
| `zenflow:avail:days:{tid}:{week}` | 600 s (10 min) |
| `zenflow:avail:hours:{tid}:{date}` | 600 s (10 min) |
| `zenflow:apts:all` | 30 s |
| `zenflow:gcal:events:{tid}:{s}:{e}` | 600 s (10 min) |
| `zenflow:reg:{CODE}` | 600 s (10 min) |

Redis is **never the source of truth**. Every key can expire or be evicted and the system rebuilds from SQLite on the next access.

---

## Database Schema (5 tables)

```
therapists       — id, name, telegram_id, email, password_hash, google_id, calendar_name, active
appointments     — id, patient_id, patient_name, therapist_id, date, time, status, gcal_apt_event_id, summary
intake_sessions  — id, appointment_id, patient_id, therapist_id, history_json
availability     — id (UUID), therapist_id, start_dt, end_dt
treatment_notes  — id, appointment_id, patient_id, tcm_pattern, treatment_principles,
                   diagnosis_certainty, ai_suggested_points, ai_recommendations,
                   tongue_observation, pulse_observation, session_notes,
                   used_points, recommendations_sent_at, completed_at
```

SQLite: WAL mode, `isolation_level=None` (autocommit), thread-local connections via `bot/db.py`.
Appointments are **never deleted** — only soft-deleted (`status='cancelled'`).

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_TOKEN` | — | Patient bot token (required) |
| `THERAPIST_BOT_TOKEN` | — | Therapist bot token |
| `OLLAMA_MODEL` | `gemma3:latest` | Local LLM model |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection |
| `SESSION_SECRET` | — | Signs `zf_session` cookie |
| `GOOGLE_CLIENT_ID` | — | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | — | Google OAuth client secret |
| `GOOGLE_REDIRECT_URI` | `http://localhost:8000/auth/callback` | Calendar OAuth redirect |
| `GOOGLE_REG_REDIRECT_URI` | `http://localhost:8000/register/google/callback` | Registration OAuth redirect |

---

## Therapist Registration

Two paths to register a therapist:

**Web → Bot activation:**
1. Go to `/register` → sign up with email/password or Google
2. Go to `/settings` → click "Get Activation Code" → copy 8-char code (valid 10 min)
3. Send the code to the therapist bot → account activated, `telegram_id` linked

**Bot-only registration:**
1. Send any message to the therapist bot
2. Bot prompts with registration code
3. Alternatively: use the web form at `/register/activate`

---

## Known Issues

| Issue | Fix |
|---|---|
| **409 Conflict** — old bot still polling | `taskkill /F /IM python.exe` then restart |
| **database is locked** | Kill all Python processes; or disconnect PyCharm DB plugin |
| **Orphaned relay session** | `redis-cli del "zenflow:relay:active:{telegram_user_id}"` |
| **Stale availability after booking** | Wait for 10-min TTL, or `redis-cli del "zenflow:avail:days:t1:0"` |
| **Google Calendar token expired** | Therapist: Settings → Disconnect → Reconnect |

---

## Documentation Index

All detailed technical documentation is in `docs/`:

| File | Topic |
|---|---|
| `docs/ARCHITECTURE.md` | System overview, file tree, routes, component map |
| `docs/MEMORY_MANAGEMENT.md` | All 4 memory layers with full lifecycle diagrams |
| `docs/DATABASE.md` | SQLite schema, WAL mode, connection model, migrations |
| `docs/REDIS.md` | Redis key schema, TTLs, eviction, invalidation patterns |
| `docs/ERD.md` | Entity Relationship Diagram (Mermaid) |
| `docs/BOT_FLOWS.md` | Conversation state machine, all handler flows |
| `docs/RELAY.md` | Two-bot relay architecture |
| `docs/AI_INTAKE.md` | LangChain + Ollama adaptive intake, speed optimisations |
| `docs/AUTH.md` | Web auth, registration paths, session management |
| `docs/AVAILABILITY.md` | Google Calendar vs SQLite availability |
| `docs/DATA_LAYER.md` | Living doc: data inventory, TTL logic, breaking points, runbook |
| `docs/TECHNICAL_DECISIONS.md` | Architecture decision records (ADRs) |

---

## What Works

- Full appointment booking with optional adaptive AI intake questionnaire
- Appointment cancellation (soft-delete, slot restored to availability)
- Two-way therapist relay chat with multi-therapist security isolation
- Therapist web dashboard: FullCalendar, patient list, session history, treatment notes
- Therapist registration via web + bot activation code (email/password + Google OAuth)
- Per-therapist Google Calendar integration; local SQLite fallback when not connected
- Ollama AI intake: 5 adaptive questions → clinical summary + structured TCM diagnosis
- Treatment page: AI-suggested acupuncture points with rationale, confidence %, lifestyle recs
- Session completion tracking with timestamp
- Live relay chat visible and sendable from web messages page
- System health API (`/api/status`): Redis, Ollama, bots, Google Calendar, active relay count

## Planned

- `PicklePersistence` — survive bot restarts without losing in-flight booking state
- Switch `USE_AI=anthropic` for production (Claude API instead of local Ollama)
- Real-time bot ↔ calendar sync (push notification on slot change)
- Send treatment recommendations to patient via Telegram from web UI
- Platform abstraction layer for WhatsApp / other channels
