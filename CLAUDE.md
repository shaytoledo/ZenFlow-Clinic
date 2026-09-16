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
python startup/run_web.py           # Web dashboard only  →  http://localhost:8080

# Pull the required AI model (first time only)
ollama pull gemma3:latest

# Quality gate (Phase 0.2) — `make <target>` or, on Windows without make, `python tasks.py <target>`
python tasks.py lint        # black --check + ruff
python tasks.py type        # mypy (strict islands: bot/interfaces, web/repositories)
python tasks.py test-fast   # pytest -m "not slow" -x
python tasks.py all         # lint + type + test + security — run before every commit
python tasks.py lock        # recompile requirements*.txt from requirements*.in

# Rotate the Google-token encryption key (F7) — preview first
python -m zenflow.rotate_token_key --dry-run
```

> Work follows `docs/MASTER_PLAN_EN.md`; the living checklist is `docs/PROGRESS.md`.
> Every task ends with a pushed branch (`claude/<phase>-<task>-<slug>`) and a Pull Request into
> `master`; never push to `master` directly, never merge your own PR — the human merges.
> The ruff `ignore` list and mypy `ignore_errors` module list in `pyproject.toml` are baselines to
> ratchet DOWN — never add to them (ADR-13).

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
| `docs/DATA_LAYER.md` | **Living doc** — full data inventory, TTL logic, known breaking points, operational runbook |
| `docs/TECHNICAL_DECISIONS.md` | Architecture decision records (ADRs) |
| `docs/BOT_AUDIT.md` | Phase 2.1 handler-by-handler bot audit, ranked defects B1–B17 |
| `docs/HOSTING_AND_MONITORING.md` | Hosting options and free log-monitoring research |

> Start guide: `startup/START.md`

## Architecture

```
bot/
├── main.py            # Wires patient ConversationHandler + runs both bots via asyncio
├── db.py              # SQLite singleton: get_db(), init_db(), 5-table schema
├── redis_client.py    # get_async_redis() / get_sync_redis() singletons
├── states.py          # 10 integer state constants (SELECTING, THERAPIST_SELECT, …)
├── config.py          # Constants sourced from zenflow.settings; calls init_db(); loads THERAPISTS from SQLite
├── utils.py           # Shared: get_main_keyboard(show_change_therapist)
├── patient_bot/
│   ├── start.py       # start(), back_to_main(), change_therapist()
│   ├── schedule.py    # Booking flow: therapist → week → days → hours → intake
│   ├── cancel.py      # show_appointments → confirm_cancel (soft-delete)
│   ├── therapist.py   # show_therapist_for_contact → relay loop → end_chat
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
├── app.py                   # FastAPI app factory: middleware + static files + router wiring
├── deps.py                  # Session helpers, auth helpers, data loaders
├── gcal.py                  # Google Calendar OAuth + API wrapper
├── routers/
│   ├── pages.py             # HTML page routes (/, /schedule, /patients, /messages, /sessions, /settings, /treatment/...)
│   ├── auth.py              # Auth routes (/register, /signin, /logout, Google OAuth, /register/activate)
│   └── api/
│       ├── appointments.py  # /api/appointments/today, /api/patients, /api/patients/{id}
│       ├── treatment.py     # /api/treatment-notes/* (get, save, rediagnose, send, complete)
│       ├── availability.py  # /api/calendars, /api/events, /api/availability
│       ├── messages.py      # /api/messages/active, /conversations, /history/{pid}, /send
│       └── system.py        # /api/status, /api/my/status, /api/my/activation-code
├── services/                # Domain service layer
│   ├── appointment_service.py
│   ├── availability_service.py
│   ├── treatment_service.py
│   ├── telegram_service.py
│   ├── therapist_service.py
│   └── cache_service.py
├── templates/               # Jinja2 templates (all extend base.html)
│   ├── treatment.html       # treatment page entry (<400 lines): layout + includes + script tags
│   └── treatment/           # its partials: header, ai_points, intake, diagnosis, points, notes, advice, complete, followup, point_panel
└── static/
    ├── style.css            # zf- prefixed styles
    ├── css/treatment.css    # treatment page styles: page-scoped tp-* classes under a .tp root
    ├── js/treatment/        # treatment page: classic scripts sharing globals, loaded IN ORDER (main.js last);
    │                        #   server values come from the JSON island #treatment-config, never Jinja inside JS
    └── js/                  # FullCalendar JS — schedule page only (loaded in order)
        ├── utils.js         # $ helper, showToast, fmt
        ├── calendar-list.js # Sidebar calendar list, visibility toggles, rename
        ├── mini-calendar.js # Mini date picker
        ├── slots.js         # saveSlot — drag-to-create availability
        ├── popover.js       # Event click popover
        └── main-calendar.js # FullCalendar init + wiring

zenflow/                     # Cross-cutting infrastructure (Phase 0.4+)
├── settings.py              # THE place env vars are read: Settings + FeatureFlags (ZF_*), fail-fast validation
├── clock.py                 # THE clock: now_utc(), iso_now(), today() (clinic tz), SQL_NOW, parse_iso(), normalize()
├── queue.py                 # TaskQueue ABC + SqliteTaskQueue (jobs table): enqueue/claim/complete/fail, idempotency, backoff
├── worker.py                # Job worker: default_registry.handler(name); in-process task or python -m zenflow.worker
├── leases.py                # Named expiring DB locks: acquire/release/held (one generation per appointment)
├── events.py                # Live-page wake-ups: notify_treatment() / subscribe_treatment() (Redis pub/sub, best effort)
├── migrate_timestamps.py    # python -m zenflow.migrate_timestamps [--dry-run] — legacy timestamps → canonical UTC
├── db_backup.py             # backup_database() via SQLite online backup (WAL-safe)
├── logging.py               # Structured logging: context (request_id…), redaction, console/JSON formatters, timed()
├── token_key.py             # Fernet derivation for google_tokens + rotate()
└── rotate_token_key.py      # python -m zenflow.rotate_token_key [--dry-run]

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
- Routing key: Redis `zenflow:relay:msg:{therapist_id}:{msg_id}` stores `{patient_id, therapist_id}`

## Tests (Phase 0.3)
- `tests/conftest.py` pins the environment *before* any project import (`bot/config.py` opens the DB at import time).
- Every test gets a fresh SQLite file via `ZENFLOW_DB_PATH`; fakeredis is patched into `bot.redis_client`; never touch `data/zenflow.db` or a real Redis.
- Fixtures: `client`, `authenticated_client` (signs in through the real form), `frozen_clock`, `fake_telegram`, `fake_llm`, `make_therapist/patient/appointment/treatment_notes/completed_session`.
- `tests/unit` (no I/O), `tests/integration` (ASGI client + SQLite + fakes), `tests/security` (attack scenarios), `tests/e2e`. Markers: `slow`, `integration`, `e2e`, `security`.
- Known-open API routes are *strict* xfails in `tests/integration/test_smoke_web.py` — delete the entry when you fix the route.

## Key conventions
- All Telegram handlers are `async def (update, context) -> int` returning the next state constant.
- `context.user_data` holds in-flight booking state (`selected_therapist`, `selected_day`, `selected_time`, `intake_count`). Cleared on completion, skip, or cancellation.
- `allow_reentry=False` is critical — setting it True breaks INTAKE and THERAPIST_INPUT states.
- The patient conversation is persistent (`name="patient"`). A new `user_data` key is NOT persisted unless added to `PERSISTED_USER_KEYS` in `bot/persistence.py` — only add scheduling data, never clinical free text.
- Cancelled appointments are **soft-deleted** (`status='cancelled'`). Records preserved for clinical history.
- `cancel_appointment(appointment_id: int)` takes an integer row ID from SQLite.
- All Ollama calls are wrapped in `asyncio.wait_for(..., timeout=100)`. Fallback questions used if unavailable.
- Read configuration through `zenflow.settings.get_settings()` — never `os.getenv` (exceptions: `bot/db.py`, `startup/launch.py`). New flags go in `FeatureFlags` with both paths tested.
- Background work (ADR-20): never `asyncio.ensure_future(...)` fire-and-forget for anything that must happen — `get_default_queue().enqueue(name, payload, run_at=..., idempotency_key=...)` and register the handler with `@default_registry.handler(name)` in `zenflow/worker.py` consumers.
- Time (ADR-19): use `zenflow.clock` — `iso_now()`, `hours_ahead(n)`, `today()` (clinic-local), `SQL_NOW` in SQL. Never `datetime.now()` / `date.today()` / `datetime('now')`; ruff `DTZ` fails the build. Stored instants are `YYYY-MM-DDTHH:MM:SSZ`.
- Logging (ADR-18): `logging.getLogger(__name__)` as usual — `zenflow/logging.py` configures the root once per process. Bind context with `zlog.bind(...)` / `with zlog.log_context(appointment_id=..)`; time calls with `zlog.timed(...)`. Never `print()` in services; never log tokens (they are redacted anyway).
- Authz (ADR-17): every `/api` router is included in `web/app.py` with `dependencies=_API_AUTH`; any endpoint that touches an appointment resolves it via `resolve_owned_appointment` (404) or `require_appointment_access` (403) from `web/deps.py` — never by patient/date/time alone. Repository reads take a `therapist_id` filter.
- `availability.py` may import `appointments.py` — not the other way around (circular import risk).
- Relay Bot clients: never build `Bot(token=...)` in a module. `bot.main.wire_bots()` hands the relay the running applications' own clients (BOT_AUDIT B14).
- Booking: write the appointment row first (`save_appointment` raises `SlotTaken`), then touch the calendar (BOT_AUDIT B4).
- SQLite `active` column is `INTEGER` (0/1); always cast: `bool(t.get("active"))`.
- Treatment page: no `<style>` or inline `<script>` in `templates/treatment*` (only the `#treatment-config` JSON island) — tests read the page through `tests/integration/treatment_source.py`.
- Treatment page styles: never write `style="…"` (markup or JS strings) or inject `<style>`; add a `tp-*` class to `static/css/treatment.css` as `.tp .tp-x { … }` (elements outside `#treatment-root` need `tp` on their own root). Data-driven colours are classes that set variables (`tp-tone-*`, `tp-ch-*`); a data-driven size is set through the CSSOM (`el.style.width = …`). Hover/focus feedback is CSS, not JS.
- Treatment page events: never write `on*="…"`; declare `data-action="name"` (+ `data-*` args) and add the handler to `CLICK_ACTIONS` in `static/js/treatment/events.js`. Any value that reaches `innerHTML` goes through `escHtml()` unless it is on the reviewed list in `tests/security/test_treatment_page_xss.py` (SF-011).

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
| `USE_AI` | `ollama` | `ollama` or `anthropic` (`ZF_AI_PROVIDER` overrides) |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `SESSION_SECRET` | — | Signs `zf_session` cookie (web dashboard); default refused outside dev |
| `TOKEN_ENCRYPTION_KEY` | — | Fernet material for stored Google tokens; required outside dev, must differ from `SESSION_SECRET` |
| `ENV` | `dev` | `dev` / `test` / `staging` / `prod` — enables fail-fast + HTTPS-only validation |
| `LOG_FORMAT` / `LOG_LEVEL` | `auto` / `INFO` | console in dev, JSON otherwise; root level |
| `CLINIC_TZ` | `Asia/Jerusalem` | clinic zone for `today()`; stored instants are always UTC |
| `ZF_*` | see `.env.example` | Typed feature flags (`zenflow/settings.py`); `GET /api/admin/flags` shows them |
| `ZF_CONV_TIMEOUT_MINUTES` | `30` | Idle minutes before a patient flow is closed; `0` = never |
| `ZF_SSE_UPDATES` | `0` | `1` = the treatment page follows a generation over server-sent events instead of 2 s polling |
| `GOOGLE_CLIENT_ID` | — | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | — | Google OAuth client secret |
| `GOOGLE_REDIRECT_URI` | `http://localhost:8080/auth/callback` | Calendar OAuth redirect |
| `GOOGLE_REG_REDIRECT_URI` | `http://localhost:8080/register/google/callback` | Registration OAuth redirect |

## What works
- Appointment booking: therapist select → week → day → hour → optional AI intake → saved to SQLite
- Appointment cancellation: soft delete, slot restored to availability
- Two-way therapist relay with multi-therapist security isolation
- Therapist web dashboard (FastAPI) with FullCalendar availability manager
- Therapist registration: web form → 8-char code → bot or web activation; Google OAuth supported
- Per-therapist Google Calendar integration; local SQLite fallback when not connected
- Ollama adaptive intake with Redis history; fallback questions when unavailable
- Treatment notes: AI summary → TCM diagnosis → two point batches generated by queued jobs right after the intake (`bot/services/pipeline_jobs.py`, `points_status` GENERATING_STAGE_0…COMPLETED/FAILED/CANCELLED); the treatment page never generates on load, "Regenerate points" is a queued run (202) and any run can be cancelled; therapist adds tongue/pulse/points/notes
- Session history page (`/sessions`): all sessions sortable by name/date/last access
- "Complete Session" button sets `completed_at` and enqueues the 24h follow-up + recommendation jobs (durable, exactly-once via idempotency keys; `bot/services/followup_jobs.py`)
- Live relay chat visible and sendable from web messages page (`/messages`)
- System health API (`/api/status`, auth required) covering Redis, Ollama, bots, Google Calendar; public liveness probe `GET /healthz`
- "Change Therapist" button in main menu (appears after therapist is selected)
- In-flight flows survive a bot restart (`bot/persistence.py`, table `bot_persistence`; scheduling keys only, never clinical text)
- Test harness (`tests/`): per-test SQLite, fakeredis, ASGI client, fake Telegram/LLM, factories; `python tasks.py test`
- Multi-tenant isolation: every API/page route scoped to the session therapist; attack suite in `tests/security/`

## Planned
- Switch `USE_AI=anthropic` for production Claude API
