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
│   ├── interfaces/                # Messaging channels (Phase 7, docs/CHANNELS.md)
│   │   ├── channel.py             # ChannelAdapter contract, InboundMessage, SentMessage, ChannelError
│   │   ├── telegram_channel.py    # TelegramChannel — the ONLY module that calls the Telegram Bot API
│   │   └── factory.py             # get_channel(name), get_default_channel(), get_staff_channel()
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
│   ├── legacy_patient_ids.py      # Pre-7.2 patient ids in API paths → internal ids (one release)
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
│   │   ├── telegram_service.py    # echo_to_therapist_chat(), get_bot_info(), check_bot(),
│   │   │                          #   relay views: get_active_relay_conversations(), get_relay_messages()…
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
3. pip install -r requirements.txt   (pinned lockfile; dev tools: requirements-dev.txt)
4. Validate .env (TELEGRAM_TOKEN required)
5. Start Redis  (Windows service → binary → error)
6. Start Ollama (ollama serve → pull model if missing)
7. Start Telegram bots subprocess  (startup/run_bots.py)
8. Start web dashboard subprocess  (startup/run_web.py)
9. Supervise loop: restart bots on crash (up to 5×); exit on web crash
```

---

## Hosting note: Vercel is disabled

A Vercel project (`zen-flow-clinic`) is connected to this GitHub repository and used to try to
deploy every commit, failing each time — this is a long-running FastAPI + Telegram-bot service
(see `Procfile` / `railway.toml`), not a serverless site. `vercel.json` sets
`git.deploymentEnabled: false` so Vercel no longer creates (failing) deployments or red checks.
To stop the integration entirely, disconnect the Git repository in the Vercel project settings.

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
│  - services/         │                      │ reply via _patient_channel
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

Template: [`.env.example`](../.env.example) (committed, no values). Copy it to `.env`.

**URL rule (security, ADR-14):** every URL that is not `localhost` / `127.0.0.1` MUST use `https://`
(and `rediss://` for Redis). Plain `http://` is only for local development. Phase 0.4 makes the
app refuse to start with a non-local `http://` URL when `ENV != dev`.

| Variable | Default | Purpose |
|---|---|---|
| `ENV` | `dev` | `dev` / `staging` / `prod` — controls fail-fast checks and cookie flags |
| `ZENFLOW_DB_PATH` | `data/zenflow.db` | SQLite file location. The test harness points it at a temp file per test; never set it to the real file in tests |
| `LOG_FORMAT` | `auto` | `console` (human-readable) / `json` (one object per line) / `auto` = console in dev, JSON otherwise |
| `LOG_LEVEL` | `INFO` | Root log level |
| `CLINIC_TZ` | `Asia/Jerusalem` | IANA zone of the clinic; `zenflow.clock.today()` is this zone's date. Stored instants are always UTC |
| `TELEGRAM_TOKEN` | — | Patient bot token (@BotFather) |
| `THERAPIST_BOT_TOKEN` | — | Therapist bot token (separate bot) |
| `OLLAMA_MODEL` | `gemma3:latest` | Local LLM model |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `USE_AI` | `ollama` | `ollama` or `anthropic` |
| `ANTHROPIC_API_KEY` | — | Only when `USE_AI=anthropic` |
| `MESSAGING_CHANNEL` | `telegram` | Default patient channel adapter (`bot/interfaces/`, `docs/CHANNELS.md`) |
| `TELEGRAM_WEBHOOK_SECRET` | — | Secret Telegram echoes on webhook calls; empty ⇒ every webhook refused (polling today) |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `SESSION_SECRET` | — | Signs `zf_session` cookie (web). ≥ 32 chars; the default value is refused outside dev |
| `TOKEN_ENCRYPTION_KEY` | — | Fernet material for `google_tokens`. Required outside dev, must differ from `SESSION_SECRET` (F7). Unset ⇒ legacy derivation from `SESSION_SECRET` |
| `GOOGLE_CLIENT_ID` | — | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | — | Google OAuth client secret |
| `GOOGLE_REDIRECT_URI` | `http://localhost:8080/auth/callback` | Calendar OAuth redirect |
| `GOOGLE_REG_REDIRECT_URI` | `http://localhost:8080/register/google/callback` | Registration OAuth redirect |
| `GOOGLE_GMAIL_REDIRECT_URI` | `http://localhost:8080/auth/gmail/callback` | Gmail OAuth redirect |
| `ZF_CLOUD` | `0` | Feature flag — running on AWS (Phase 12) |
| `ZF_STORAGE_S3` | `0` | Feature flag — S3 storage backend (Phase 4.3 / 12) |
| `ZF_QUEUE_BACKEND` | `inprocess` | Feature flag — `inprocess` / `celery` / `temporal` / `aws` (Phase 1.2 / 12) |
| `ZF_CHANNEL_WHATSAPP` | `0` | Feature flag — WhatsApp adapter (Phase 7.4) |
| `ZF_AI_PROVIDER` | — | Feature flag — `ollama` / `anthropic`; empty ⇒ `USE_AI` |
| `ZF_WEBHOOK_MODE` | `0` | Feature flag — bot webhooks instead of polling (Phase 12.2.5) |
| `ZF_SSE_UPDATES` | `0` | Feature flag — SSE instead of polling (Phase 3.4) |
| `ZF_POINT_IMAGES` | `0` | Feature flag — acupoint images (Phase 4.3) |

All variables are read in exactly one place: `zenflow/settings.py` (`get_settings()`), which
validates the environment at startup and refuses to boot outside dev on a default/short secret, a
missing `TOKEN_ENCRYPTION_KEY`, or a non-local `http://` URL (ADR-16). `GET /api/admin/flags`
(auth required) shows the live flag state. Two sanctioned exceptions read the environment
directly: `bot/db.py` (`ZENFLOW_DB_PATH`, needed before settings can be imported by the test
harness) and `startup/launch.py` (runs before dependencies are installed).

### Runbook: introduce / rotate `TOKEN_ENCRYPTION_KEY`

1. Generate a key: `python -c "import secrets; print(secrets.token_hex(32))"` and put it in `.env`
   as `TOKEN_ENCRYPTION_KEY`. Keep `SESSION_SECRET` unchanged for now.
2. Preview: `python -m zenflow.rotate_token_key --dry-run` — reports rotated / already-current /
   failed rows, writes nothing.
3. Apply: `python -m zenflow.rotate_token_key` — takes a consistent backup
   (`data/zenflow.db.bak-<timestamp>`) and re-encrypts every `google_tokens` row. Idempotent.
4. Restart the web process. Rows that could not be decrypted with either key are listed; those
   therapists must reconnect Google from Settings.
5. Rotating again later: pass the previous key with `--old-material '<old key>'`.

---

## Time (Phase 1.1, ADR-19)

One clock: `zenflow/clock.py`. Every stored instant is the canonical string
`YYYY-MM-DDTHH:MM:SSZ` (UTC, seconds, `Z`), which compares correctly as a plain string in SQL and
Python. Writes use `clock.iso_now()` / `clock.hours_ahead(n)` from Python and `clock.SQL_NOW`
(`strftime('%Y-%m-%dT%H:%M:%SZ','now')`) from SQL — never `datetime('now')`, never
`datetime.now()` (ruff `DTZ` rules fail the build). INSERTs stamp `created_at` explicitly so the
old table DEFAULTs (space-separated) are never used. Reads accept every legacy shape via
`clock.parse_iso()`. Calendar values (`appointments.date`/`time`, availability `start_dt`/`end_dt`)
are clinic wall-clock values and stay as they are; `clock.today()` is the clinic-local date.

### Runbook: normalise legacy timestamps (once per existing database)

1. `python -m zenflow.migrate_timestamps --dry-run` — counts values per shape
   (`naive-local` = written by the old Python code in the host's local time; `sqlite-utc` =
   `datetime('now')`; `aware`), lists anything unparseable, writes nothing.
2. `python -m zenflow.migrate_timestamps --local-tz <zone the old process ran in>` — takes a
   backup (`data/zenflow.db.bak-timestamps-<stamp>`) and rewrites. Idempotent; exit 1 lists
   unparseable rows to fix by hand.
3. The 24h follow-up window and the recommendation dispatcher compare canonical strings from
   now on; before this migration they could be off by the host's UTC offset (F2).

## Background jobs (Phase 1.2, ADR-20)

`zenflow/queue.py` defines the `TaskQueue` contract (`enqueue` / `claim` / `complete` / `fail` /
`cancel`) and `SqliteTaskQueue`, the only implementation until Phase 12 (`jobs` table, see
DATABASE.md). `zenflow/worker.py` runs handlers registered by name (`default_registry`) with a
per-job log context and timeout; it is started inside the bot process at `post_init` when
`ZF_QUEUE_BACKEND=inprocess` (the default) and can also run standalone with
`python -m zenflow.worker`. Guarantees tested in `tests/unit/test_task_queue.py`: a job 24 h out
is not claimed early; duplicate idempotency keys are rejected; failures retry with exponential
backoff then dead-letter; a job whose worker died is reclaimed after the lock timeout and
completes exactly once.

**Registered jobs (Phase 1.3, ADR-21)** — `bot/services/followup_jobs.py`:

| Job | Enqueued by | Runs at | Idempotency key | Skips when |
|---|---|---|---|---|
| `followup.send_step1` | `POST …/complete`; with `ZF_AUTO_FOLLOWUP`, also the reconcile sweep for sessions never completed (`followup:{appointment_id}:auto`, 24 h after the session ended) | `completed_at + 24h` | `followup:{appointment_id}` | session gone/cancelled, already followed up (`followup_sent_at`, conversation or rating), fired > 48 h after completion, patient has no messaging channel |
| `recommendations.dispatch` | `POST …/complete` (auto-queue) and `POST …/send-recommendations` with `schedule_hours >= 24` | queued `pending_rec_send_at` | `recommendations:{appointment_id}:{send_at}` | nothing queued any more, or the queue entry was rescheduled. An email send whose therapist has not connected Google is **deferred** (6 h rechecks, no attempt charged, one alert) and woken by `resume_after_google_connected()` when Google is connected (Phase 5.4). A delivery stamps `recommendations_sent_at` (5.5), so completing the session again does not queue them twice |

`followup_scheduler.reconcile()` runs every 30 min as a safety net: it enqueues jobs for sessions
completed in the last 26 h without a follow-up and for every queued recommendation (rows written
before Phase 1.3, or an enqueue that failed); the keys make it safe to repeat. Delivery is
at-least-once, so handlers check the database before sending.

## Logging (Phase 0.5, ADR-18)

`zenflow/logging.py` configures the root logger once per process (`configure_logging("web")`
in `web/app.py`, `configure_logging("bots")` in `bot/main.py`). Every record carries
`ts`, `level`, `logger`, `event`, `request_id`, `therapist_id`, `patient_id`, `appointment_id`,
`duration_ms` (+ `service`, and `method`/`path`/`status_code` on access lines).

- **Context**: `bind(**fields)` / `with log_context(**fields):` store fields in a `ContextVar`;
  they follow the request through `asyncio.to_thread`, `create_task` and background tasks.
  The web middleware binds `request_id` (from `X-Request-ID` or generated) and `therapist_id`
  per request and echoes `X-Request-ID` on the response; the follow-up scheduler binds a fresh
  `job-…` id per sweep and `appointment_id`/`patient_id` per item.
- **Redaction**: Telegram bot tokens, Google client secrets / access / refresh tokens / API keys,
  Anthropic keys, Fernet blobs, Bearer/JWT strings and `key=value` / `"key": "value"` secrets are
  scrubbed in the record factory (message + args) and again in the formatters (final text,
  tracebacks). `logs/` leaked bot tokens once — this is the guard.
- **Format**: `LOG_FORMAT=auto` → console in dev/test, JSON in staging/prod. Files:
  `logs/webLogs.text` (append) and `logs/botLogs.text` (truncated per start) — not in test.
- **Timing**: `with timed(logger, "ollama call", appointment_id=…):` logs `duration_ms`.

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

**Authentication (ADR-17):** every `/api/*` router is mounted with a router-level session
dependency (`require_signed_in`), so anonymous requests get `401` before body validation.
Appointment-bound endpoints additionally resolve the appointment **scoped to the session
therapist** (`resolve_owned_appointment` → 404, `require_appointment_access` → 403). The only
public endpoint is `GET /healthz` → `{"ok": true}`.


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
| `GET /auth/login?next=<path>` | No | Redirect to Google OAuth (Calendar + Gmail); a same-site `next` is kept in the session |
| `GET /auth/callback` | No | Complete Google OAuth; back to `next` with `?google=connected\|cancelled`, else `/settings` |
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
| `POST /api/treatment-notes/{pid}/{date}/{time}/send-recommendations` | Send recommendations now (Telegram, or email through the therapist's Gmail) or queue them (`schedule_hours >= 24`); 409 `google_not_connected` when email cannot go out |
| `POST /api/treatment-notes/{pid}/{date}/{time}/recommendations-text` | The email those recommendations would make, for copying by hand; sends nothing |
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
| `GET /api/gmail-status` | `{connected, reason}` — can email go out through the therapist's Google account (Phase 5.2) |
| `GET /api/my/activation-code` | Generate new 8-char bot activation code |
