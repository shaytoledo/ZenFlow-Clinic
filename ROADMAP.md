# ZenFlow Clinic — System Status & Multi-Therapist Roadmap

> **Date written:** March 2026
> **Current build:** Single-therapist MVP, fully functional

---

## 1. What Is Built and Working Right Now

### Patient Telegram Bot
- `/start` and main menu (3 buttons: Schedule, Cancel, Connect to Therapist)
- **Scheduling flow** — patient picks week → day → hour → optional 5-question AI intake
  - Adaptive intake via LangChain + Ollama (gemma3 local model)
  - AI-generated clinical summary saved with the appointment
  - Fallback to hardcoded questions if Ollama is unreachable
- **Cancellation flow** — lists active appointments, deletes file on confirm, restores Google Calendar slot
- **Therapist relay** — two-way Telegram chat, End Chat button on every message
- Appointment data saved as JSON files in `data/appointments/{Name}_{patient_id}/`

### Therapist Telegram Bot
- Separate bot for the therapist (different token, same Python process)
- Receives forwarded patient messages
- Routes replies back to the correct patient (via `reply-to` message ID)
- Hardwired to a single `THERAPIST_TELEGRAM_ID` from `.env`

### Therapist Web Dashboard (FastAPI)
| Page | Status | What it does |
|---|---|---|
| `/` Dashboard | Done | Today's schedule, 4 stat cards, recent appointments |
| `/schedule` | Done | FullCalendar week view, drag to add availability slots, click to delete |
| `/patients` | Done | Searchable list, slide-in history drawer, link to session |
| `/treatment/{id}/{date}/{time}` | Done (partial) | AI intake review, point reference, session notes input |
| `/messages` | Done | Intake Q&A viewer per patient |
| `/settings` | Done (cosmetic) | Bot config overview, clinic info form (not saved) |

### APIs Available
```
GET  /api/appointments/today        — today's appointments from JSON files
GET  /api/patients                  — aggregated patient list
GET  /api/patients/{id}             — single patient + full appointment history
GET  /api/appointment/{id}/{d}/{t}  — single appointment detail
GET  /api/messages/active           — count of active relay sessions
GET  /api/events                    — Google Calendar events (for schedule page)
POST /api/availability              — create availability slot in GCal
DEL  /api/availability/{event_id}   — remove availability slot
```

### Google Calendar Integration
- Reads "✅ Available" events from "ZenFlow Availability" calendar to show open slots
- Creates appointment event in primary calendar when patient books
- Deletes appointment event and restores availability slot on cancellation
- Falls back to a hardcoded Mon–Sun 09:00–14:00 stub when not configured

### Infrastructure
- `launch.py` — single command boots everything (venv, deps, Ollama, bots, web)
- `run_bots.py` / `run_web.py` — dev shortcuts to run services individually
- `botLogs.text` — combined rotating log for both bots
- `.env` — all secrets and config loaded via python-dotenv

---

## 2. What Is Hardcoded / Single-Therapist Only (Current Limitations)

These are the exact points that break when you add a second therapist.

### 2.1 Single therapist identity baked into `.env`
```
THERAPIST_BOT_TOKEN=...       # one bot for one therapist
THERAPIST_TELEGRAM_ID=...     # one Telegram user ID
```
There is no concept of a therapist registry, therapist accounts, or therapist selection. Every patient always goes to the same person.

### 2.2 Single relay bot
`bot/therapist_bot/main.py` registers exactly one `filters.User(user_id=THERAPIST_TELEGRAM_ID)` handler. A second therapist would silently receive nothing and send to nobody.

### 2.3 No patient-therapist assignment
Appointments JSON files have `patient_id` and `patient_name` but no `therapist_id`. There is no way to know which therapist treated which patient. The schedule page shows one shared calendar; there are no per-therapist views.

### 2.4 No web dashboard authentication
`web/app.py` uses Google OAuth only to get a Google Calendar token — it is not a user login system. Anyone who reaches `http://localhost:8000` after the OAuth flow sees all patients and all data. There are no roles (admin, therapist), no sessions, no per-therapist data filtering.

### 2.5 File-based storage does not scale
`data/appointments/{Name}_{patient_id}/*.json` is one file per appointment. Problems at scale:
- No concurrent write protection (two processes writing the same file → corruption)
- No query capability (finding "all appointments for therapist X" means scanning every file)
- No transactions (cancel + restore is not atomic)
- No relational links (patient ↔ therapist ↔ appointment)

### 2.6 Single shared Google Calendar
`bot/patient_bot/services/availability.py` reads one calendar ("ZenFlow Availability") associated with one Google account. Multiple therapists need separate calendars or a shared calendar with per-therapist filtering.

### 2.7 AI model is local Ollama
`gemma3:latest` runs locally. For a multi-therapist production deployment this would typically be replaced with Anthropic Claude or OpenAI (already stubbed in `.env` as `USE_AI=anthropic`).

### 2.8 Bot restarts wipe in-flight conversations
No `PicklePersistence`. If the bot process dies mid-intake or mid-relay, the patient is stuck. `context.user_data` is in-memory only.

---

## 3. Full Implementation Plan — Multi-Therapist System

The items below are ordered from most foundational to most optional. Do not skip steps — each one builds on the previous.

---

### Phase 1 — Data Layer (Foundation for Everything Else)

**Replace JSON files with a proper database.**

Recommended: **SQLite** (zero-ops, single file, good enough for ~20 therapists and hundreds of patients) upgradeable to PostgreSQL later.

#### Tables needed

```sql
-- Therapist accounts (managed by admin)
CREATE TABLE therapists (
    id            INTEGER PRIMARY KEY,
    telegram_id   INTEGER UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    email         TEXT,
    bot_token     TEXT,           -- if each therapist gets their own bot
    gcal_token    TEXT,           -- JSON blob, per-therapist Google token
    active        BOOLEAN DEFAULT TRUE,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Patient registry (auto-created on first booking)
CREATE TABLE patients (
    id            INTEGER PRIMARY KEY,
    telegram_id   INTEGER UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Patient-to-therapist assignment
CREATE TABLE patient_therapist (
    patient_id    INTEGER REFERENCES patients(id),
    therapist_id  INTEGER REFERENCES therapists(id),
    assigned_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (patient_id, therapist_id)
);

-- Appointments
CREATE TABLE appointments (
    id                  INTEGER PRIMARY KEY,
    patient_id          INTEGER REFERENCES patients(id),
    therapist_id        INTEGER REFERENCES therapists(id),
    date                TEXT NOT NULL,      -- YYYY-MM-DD
    time                TEXT NOT NULL,      -- HH:MM
    status              TEXT DEFAULT 'active',  -- active | cancelled | completed
    gcal_event_id       TEXT,
    summary             TEXT,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Intake Q&A (one row per exchange)
CREATE TABLE intake_messages (
    id              INTEGER PRIMARY KEY,
    appointment_id  INTEGER REFERENCES appointments(id),
    role            TEXT NOT NULL,   -- 'user' | 'assistant'
    content         TEXT NOT NULL,
    seq             INTEGER NOT NULL
);

-- Session notes (written by therapist on the treatment page)
CREATE TABLE session_notes (
    id              INTEGER PRIMARY KEY,
    appointment_id  INTEGER REFERENCES appointments(id) UNIQUE,
    tongue          TEXT,
    pulse           TEXT,
    points_used     TEXT,   -- JSON array of point codes
    notes           TEXT,
    lifestyle_sent  BOOLEAN DEFAULT FALSE,
    saved_at        DATETIME
);
```

**Migration plan:**
1. Write a `scripts/migrate_json_to_db.py` that reads every existing `data/appointments/` JSON file and inserts into SQLite.
2. Run migration once, verify counts match.
3. Switch `bot/patient_bot/services/appointments.py` to read/write SQLite instead of files.
4. Keep the old JSON files as backup for two weeks, then delete.

---

### Phase 2 — Therapist Registry & User Management

**Goal:** admin can add/remove/edit therapists without touching the code.

#### 2.1 Therapist management UI (admin web page)

Add a `/admin` section to the web dashboard — accessible only to a designated admin account.

Pages needed:
```
/admin                          — overview: therapist count, system health
/admin/therapists               — list all therapists
/admin/therapists/new           — create therapist account
/admin/therapists/{id}/edit     — edit name, telegram ID, bot token, gcal, active flag
/admin/therapists/{id}/delete   — deactivate (soft delete)
/admin/patients                 — view/search all patients across therapists
/admin/patients/{id}/assign     — reassign patient to different therapist
```

#### 2.2 Therapist data model in `.env` → database

Remove `THERAPIST_TELEGRAM_ID` and `THERAPIST_BOT_TOKEN` from `.env`. They move into the `therapists` table. The `.env` only keeps:
```
ADMIN_TELEGRAM_ID=...          # the clinic owner / admin
ADMIN_PASSWORD=...             # for web dashboard admin login
```

#### 2.3 Therapist loading at bot startup

`bot/main.py` should query the `therapists` table at startup and build one handler per active therapist. Alternatively, use a single shared therapist bot with dynamic routing (see Phase 3).

---

### Phase 3 — Multi-Therapist Relay Architecture

Current relay is 1-patient → 1-therapist via two hardcoded bots. For N therapists, the architecture needs redesigning.

#### Option A — One therapist bot per therapist (current approach, scaled)
- Each therapist gets a Telegram bot from `@BotFather` and a token stored in the DB.
- `bot/main.py` starts N therapist bot `Application` objects at startup (one per active therapist), each filtered to its therapist's `telegram_id`.
- **Pro:** clean separation, therapist-specific bot name/avatar.
- **Con:** each bot uses a polling loop → CPU and connection overhead grows with therapist count. Works fine for up to ~20 therapists.

#### Option B — One shared therapist bot, message routing by therapist ID
- One therapist bot token for all therapists.
- All therapists message the same bot; routing is purely by their Telegram user ID.
- `relay_sessions.json` (or DB table) maps: `forwarded_msg_id → {patient_id, therapist_id}`.
- `bot/therapist_bot/handlers.py` looks up the therapist_id and routes accordingly.
- **Pro:** one polling loop, simpler infrastructure.
- **Con:** therapists cannot distinguish their bot from others'. Recommended for production.

**Recommended: Option B** (shared therapist bot).

#### Relay DB table (replaces relay_sessions.json)

```sql
CREATE TABLE relay_sessions (
    forwarded_msg_id   INTEGER PRIMARY KEY,
    patient_id         INTEGER NOT NULL,
    therapist_id       INTEGER NOT NULL,
    opened_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    active             BOOLEAN DEFAULT TRUE
);
```

---

### Phase 4 — Patient-Therapist Matching

When a patient presses "Schedule Appointment", the system currently shows all available slots globally. With multiple therapists, the patient needs to either:

#### Option A — Choose a therapist
Add a step before week selection: patient sees a list of therapist names (or specialties) and picks one. Availability slots shown are then filtered to that therapist's Google Calendar.

Bot flow with therapist selection:
```
SELECTING → THERAPIST_SELECT (new state)
         → SCHEDULE_WEEK → SCHEDULE_DAY → SCHEDULE_HOUR → INTAKE_CONFIRM → INTAKE
```

New state constant: `THERAPIST_SELECT` — adds one state to `states.py` (currently 9 → 10).

#### Option B — Automatic assignment (round-robin or rule-based)
System auto-assigns based on: specialty, load (fewest upcoming appointments), or language preference. No extra step for the patient.

**Recommended: Option A for small clinics, Option B for larger ones.**

---

### Phase 5 — Per-Therapist Web Dashboard (Auth)

Currently anyone on the network who hits `localhost:8000` sees everything after one OAuth click. This needs real authentication.

#### 5.1 Session-based login

Use `fastapi-users` or manual JWT sessions:
- Therapist goes to `/login` → enters email + password (or uses Telegram login widget).
- Server issues a signed cookie/JWT with `therapist_id` and `role` (`admin` or `therapist`).
- Every API endpoint checks the session and filters data by `therapist_id`.

#### 5.2 Per-therapist data filtering

| Endpoint | Current | After auth |
|---|---|---|
| `GET /api/patients` | Returns ALL patients | Returns only this therapist's patients |
| `GET /api/appointments/today` | Returns ALL today's apts | Returns only this therapist's apts |
| `GET /schedule` | Shows one shared calendar | Shows this therapist's Google Calendar |

#### 5.3 Role-based access control

| Role | What they can see |
|---|---|
| `therapist` | Own patients, own schedule, own treatment notes |
| `admin` | All therapists, all patients, system settings, billing |

#### 5.4 Per-therapist Google Calendar

Each therapist authenticates their own Google account. OAuth tokens stored as `gcal_token` JSON blob in the `therapists` table (not a file on disk). The `/auth/login` and `/auth/callback` routes become per-therapist flows.

---

### Phase 6 — Treatment Page Persistence

The treatment page (`/treatment/{id}/{date}/{time}`) has input fields for:
- Tongue & pulse notes
- Points used today (tag input)
- Session notes (free text)
- Lifestyle advice toggles

**Currently none of this is saved.** The `Complete Session` button does nothing.

Steps to implement:
1. Add `POST /api/session-notes` endpoint that writes to `session_notes` table.
2. Add auto-save on the frontend (debounced `fetch` every 5 seconds while typing).
3. On load, `GET /api/appointment/{id}/{d}/{t}` should also return any existing session notes.
4. "Send Approved Advice via Telegram" button calls `POST /api/send-advice` which uses `Bot(TELEGRAM_TOKEN).send_message(patient_id, ...)` — FastAPI needs a `python-telegram-bot` bot instance available (use `Bot` directly, not polling).
5. "Complete Session" sets `appointments.status = 'completed'`.

---

### Phase 7 — Bot Persistence (Survive Restarts)

Use `PicklePersistence` so patients mid-flow are not stranded when the bot restarts.

```python
# bot/main.py
from telegram.ext import PicklePersistence

persistence = PicklePersistence(filepath="data/bot_persistence")
app = Application.builder().token(TELEGRAM_TOKEN).persistence(persistence).build()
```

This saves `context.user_data` (selected_day, selected_time, intake_count) to a pickle file and restores it on restart. Zero other code changes needed.

---

### Phase 8 — AI Model (Anthropic for Production)

The `.env` has `USE_AI=ollama` with `USE_AI=anthropic` stubbed as future. Switch when moving to production:

1. Uncomment `anthropic>=0.40.0` in `requirements.txt`.
2. Add `ANTHROPIC_API_KEY=...` to `.env`.
3. In `bot/patient_bot/services/ai_intake.py`, replace `ChatOllama` with `ChatAnthropic` (LangChain supports both with the same interface — just swap the import and class name).
4. Remove Ollama startup logic from `launch.py` and `bot/main.py`.

---

### Phase 9 — Real-Time Messages Page

The `/messages` page currently shows intake Q&A history only. For live relay monitoring:

1. Add a `WebSocket` endpoint in `web/app.py` (`/ws/messages`).
2. When `bot/therapist_bot/handlers.py` delivers a reply, publish the message to a shared async queue.
3. The messages page subscribes via WebSocket and appends messages in real time.
4. Alternative (simpler): poll `GET /api/messages/active` every 3 seconds.

---

### Phase 10 — Polish & Production Hardening

These are lower priority but needed before going fully live.

| Item | What to do |
|---|---|
| HTTPS | Put nginx or caddy in front of FastAPI; get a Let's Encrypt cert |
| Process manager | Run bots + web with `supervisord` or `systemd` instead of terminal |
| Deployment | Docker compose: `bot`, `web`, `postgres` (if switching from SQLite), `ollama` services |
| Pagination | Patients page needs pagination once > 50 patients |
| Mobile | Sidebar collapses on small screens — add CSS breakpoints |
| Dark mode | Toggle in settings, save preference in browser localStorage |
| Export to PDF | jsPDF or server-side WeasyPrint for treatment session PDF export |
| TCM syndrome AI | Call Ollama/Claude from treatment page to suggest a TCM diagnosis from intake |
| More acupuncture points | Expand the 8-point JS reference to full ST, LI, LR, SP, PC, GV, CV, HT, KD, BL, GB, TE series |
| Improvement score | Add a `score` field (1–10) to appointments; show a line chart on dashboard |
| Search | Full-text search across all intake notes (patient types "lower back pain" → shows matching patients) |
| Settings page wiring | Currently cosmetic — wire clinic name / timezone / session duration to a `config.json` |
| Rate limiting | Prevent a patient from spamming the bot with rapid messages |
| Language support | Bot currently forces English questions; add Hebrew/Arabic UI option |

---

## 4. Implementation Order (Summary)

| Priority | Phase | Estimated effort |
|---|---|---|
| Must-do first | Phase 1 — SQLite database | Medium |
| Must-do first | Phase 7 — PicklePersistence | Tiny |
| Must-do first | Phase 2 — Therapist registry & admin UI | Medium |
| Core feature | Phase 3 — Multi-therapist relay | Medium |
| Core feature | Phase 4 — Patient-therapist matching (bot flow) | Small |
| Core feature | Phase 5 — Web dashboard auth & filtering | Large |
| Core feature | Phase 6 — Treatment page persistence | Small |
| Production | Phase 8 — Switch to Anthropic API | Tiny |
| Nice to have | Phase 9 — Real-time messages | Small |
| Polish | Phase 10 — Hardening & polish | Large (many small tasks) |

**The absolute minimum to support 2+ therapists** (without any of the polish):
Phases 1 → 2 → 3 → 4 → 5 → 6, in that order.

---

## 5. Files That Will Change Most

| File | Why |
|---|---|
| `bot/config.py` | Remove single `THERAPIST_*` vars; add DB path |
| `bot/states.py` | Add `THERAPIST_SELECT` state (9 → 10) |
| `bot/main.py` | Dynamic therapist bot loading from DB |
| `bot/patient_bot/schedule.py` | Add therapist selection step |
| `bot/patient_bot/services/appointments.py` | Full rewrite → SQLite |
| `bot/patient_bot/services/relay.py` | Replace JSON file → DB table |
| `bot/patient_bot/services/availability.py` | Per-therapist calendar support |
| `bot/therapist_bot/main.py` | Load all therapists from DB; single shared bot or N bots |
| `bot/therapist_bot/handlers.py` | Route by therapist_id from DB |
| `web/app.py` | Add auth middleware, per-therapist filtering on all routes |
| `web/gcal.py` | Per-therapist OAuth token loading |
| `requirements.txt` | Add `sqlalchemy`, `fastapi-users` or `python-jose`, `anthropic` |

---

## 6. New Files to Create

| File | Purpose |
|---|---|
| `data/zenflow.db` | SQLite database (auto-created on first run) |
| `bot/db.py` | SQLAlchemy models + session factory |
| `scripts/migrate_json_to_db.py` | One-time migration of existing JSON data |
| `web/auth.py` | Session/JWT logic, login/logout routes |
| `web/templates/login.html` | Login page |
| `web/templates/admin/` | Admin panel templates |
| `bot/patient_bot/handlers/therapist_select.py` | New handler for therapist selection step |

---

## 7. Known Issues in the Current Build

| Issue | Location | Impact |
|---|---|---|
| Treatment page: "Complete Session" button saves nothing | `treatment.html` | Session notes are lost on page refresh |
| Treatment page: session count shows "Session —" | `treatment.html` | Cosmetic only |
| Settings page: clinic form does not save | `settings.html` | Cosmetic only |
| Real-time relay messages not shown in `/messages` | `messages.html` | Therapist cannot monitor live chats from web |
| `data/relay_sessions.json` grows forever | `relay.py` | msg_to_patient map never pruned; low risk for now |
| No rate limiting on bot | `bot/main.py` | Patient could flood the bot |
| Google OAuth token is one shared file | `data/google_token.json` | Will not work for multiple therapists |
| `data/therapist_messages/` folder still orphaned | `data/` | Dead files; can be manually deleted |
