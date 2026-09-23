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

# Booking API (Phase 7.3)
python -m zenflow.api_keys create <name>    # a machine client's key, printed once
python -m zenflow.export_openapi            # re-publish docs/api/booking-v1.openapi.json
python -m zenflow.export_routes             # re-publish the route authorization table (9.1)
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
| `docs/AUTH.md` | Web auth, registration, session management + the 9.2 session policy (fixation, idle/absolute expiry, logout revocation) |
| `docs/AVAILABILITY.md` | Google Calendar vs local SQLite availability |
| `docs/DATA_LAYER.md` | **Living doc** — full data inventory, TTL logic, known breaking points, operational runbook |
| `docs/TECHNICAL_DECISIONS.md` | Architecture decision records (ADRs) |
| `docs/BOT_AUDIT.md` | Phase 2.1 handler-by-handler bot audit, ranked defects B1–B17 |
| `docs/HOSTING_AND_MONITORING.md` | Hosting options and free log-monitoring research |
| `docs/POINT_CARD_DESIGN.md` | Phase 4.2 research + design of the acupuncture point cards (anatomy, selection, a11y, tokens) |
| `docs/POINT_IMAGE_SOURCING.md` | Phase 4.3d shortlist of point-image sources with licences (owner decision Q4) |
| `docs/GOOGLE_CONNECTION_UX.md` | Phase 5: recovered prior work, the `google_not_connected` 409 contract, client + background behaviour |
| `docs/FOLLOWUP.md` | Phase 6: 24h follow-up and recommendation delivery — root causes, storage, conversation, alerts |
| `docs/AUDIT.md` | Phase 8.1: the `audit_log` trail — what is recorded, who the actor is, append-only |
| `docs/BOOKING_API.md` | Phase 7.3: `/api/v1` booking — API keys, idempotency, errors, the published OpenAPI schema |
| `docs/WHATSAPP.md` | Phase 7.4: the WhatsApp channel — 24-hour window, templates, button limits, webhooks |
| `docs/CHANNELS.md` | Phase 7: the `ChannelAdapter` contract, Telegram adapter, conformance suite, adding a channel; patient identity (`patients` + `patient_channels`) |
| `docs/AUDIT.md` | Phase 8.1: the append-only `audit_log` — what changed, when, and who did it |
| `docs/AI_CALLS.md` | Phase 8.2: the AI meter — `ai_calls`, `ask()`, cost/latency/failure rate, prompt hashing |
| `docs/MESSAGE_LOG.md` | Phase 8.3: `message_log` — every message to and from a patient, both directions |
| `docs/METRICS.md` | Phase 8.4: `/healthz`, `/readyz`, `/api/admin/metrics`, Prometheus and tracing flags |
| `docs/AUTHZ.md` | Phase 9.1: every route, its auth level, its object-level check and the test that proves it |

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
├── interfaces/        # ChannelAdapter (channel.py), TelegramChannel + WhatsAppChannel (the only provider callers), factory
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
├── legacy_patient_ids.py    # pre-7.2 patient ids in API paths → internal ids (remove next release)
├── gcal.py                  # Google Calendar OAuth + API wrapper
├── routers/
│   ├── pages.py             # HTML page routes (/, /schedule, /patients, /messages, /sessions, /settings, /treatment/...)
│   ├── auth.py              # Auth routes (/register, /signin, /logout, Google OAuth, /register/activate)
│   ├── media.py             # /media/<key> — LocalStorage files, signed-in therapists only
│   └── api/
│       ├── appointments.py  # /api/appointments/today, /api/patients, /api/patients/{id}
│       ├── treatment.py     # /api/treatment-notes/* (get, save, rediagnose, send, complete)
│       ├── acupoints.py     # /api/acupoints — point reference data in the therapist's language (ETag)
│       ├── availability.py  # /api/calendars, /api/events, /api/availability
│       ├── messages.py      # /api/messages/active, /conversations, /history/{pid}, /send
│       ├── system.py        # /api/status, /api/my/status, /api/my/activation-code, /api/my/language, /api/my/preferences
│       └── whatsapp.py      # /api/webhooks/whatsapp — Meta's deliveries (404 while the flag is off)
├── services/                # Domain service layer
│   ├── appointment_service.py
│   ├── availability_service.py
│   ├── treatment_service.py
│   ├── telegram_service.py
│   ├── therapist_service.py
│   └── cache_service.py
├── templates/               # Jinja2 templates (all extend base.html)
│   ├── treatment.html       # treatment page entry (<400 lines): layout + includes + script tags
│   ├── partials/followup_card.html  # the 24h follow-up card (Phase 6.5), shared with session_archive.html
│   ├── partials/delivery_log.html   # "Messages" from message_log (6.6, both directions in 8.3)
│   ├── partials/session_history.html # "Record history" from audit_log + ai_calls (Phase 8.5)
│   └── treatment/           # its partials: header, ai_points, intake, diagnosis, points, notes, advice, complete, followup, point_lightbox, email_dialog
└── static/
    ├── style.css            # zf- prefixed styles
    ├── css/tokens.css       # design tokens (--zf-*), loaded on every page; dark theme = <html data-theme="dark"> (ADR-25)
    ├── css/shell.css        # responsive app shell (sidebar drawer < 900px) + print rules, every page
    ├── js/shell.js          # the drawer's menu button (every page)
    ├── css/followup.css     # the follow-up card (fu-* classes), treatment page + session archive
    ├── css/treatment.css    # treatment page styles: page-scoped tp-* / pc-* (point card) classes under a .tp root
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
├── seed.py                  # python -m zenflow.seed acupoints [--dry-run] — reference data from zenflow/seed_data/*.json
├── storage.py               # Storage ABC (put/get/exists/delete/url): LocalStorage (MEDIA_ROOT, served at /media) | S3Storage (ZF_STORAGE_S3; SSE-KMS, presigned links)
├── ingest_images.py         # python -m zenflow.ingest_images <folder> [--dry-run] — licensed images → WebP + thumb + acupoint_images
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
- **Patient bot** (`TELEGRAM_TOKEN`): patient-facing; forwards messages through `_therapist_channel` (a `TelegramChannel` over the therapist app's client)
- **Therapist bot** (`THERAPIST_BOT_TOKEN`): shared by all therapists; routes replies back through `_patient_channel` (over the patient app's client)
- Both bots run concurrently in the same process via `asyncio.run(_run(patient_app, therapist_app))`
- Routing key: Redis `zenflow:relay:msg:{therapist_id}:{msg_id}` stores `{patient_id, therapist_id}`

## Tests (Phase 0.3)
- `tests/conftest.py` pins the environment *before* any project import (`bot/config.py` opens the DB at import time).
- Every test gets a fresh SQLite file via `ZENFLOW_DB_PATH`; fakeredis is patched into `bot.redis_client`; never touch `data/zenflow.db` or a real Redis.
- Fixtures: `client`, `authenticated_client` (signs in through the real form), `frozen_clock`, `fake_telegram`, `fake_llm`, `make_therapist/patient/appointment/treatment_notes/completed_session`. `make_patient(telegram_id=…)` returns the internal `patient_id` and a different `telegram_id` — compare sends with `telegram_id`.
- `tests/unit` (no I/O), `tests/integration` (ASGI client + SQLite + fakes), `tests/security` (attack scenarios), `tests/contract` (adapter conformance), `tests/e2e`. Markers: `slow`, `integration`, `e2e`, `security`, `contract`.
- `tests/e2e` serves the app on a local port and drives the installed Chrome with Playwright (`ZF_E2E_BROWSER=msedge` for Edge); visual baselines live in `tests/e2e/snapshots/` per platform — after an intended UI change run `ZF_UPDATE_SNAPSHOTS=1 python -m pytest tests/e2e` and look at the images before committing.
- Known-open API routes are *strict* xfails in `tests/integration/test_smoke_web.py` — delete the entry when you fix the route.

## Key conventions
- All Telegram handlers are `async def (update, context) -> int` returning the next state constant.
- `context.user_data` holds in-flight booking state (`selected_therapist`, `selected_day`, `selected_time`, `intake_count`). Cleared on completion, skip, or cancellation.
- `allow_reentry=False` is critical — setting it True breaks INTAKE and THERAPIST_INPUT states.
- The patient conversation is persistent (`name="patient"`). A new `user_data` key is NOT persisted unless added to `PERSISTED_USER_KEYS` in `bot/persistence.py` — only add scheduling data, never clinical free text.
- Email (Phase 5): anything that sends mail goes through `web/services/email_service.send_email` and turns `EmailNotConfigured` / `EmailSendError(token_invalid=True)` into the 409 `google_not_connected` contract (`docs/GOOGLE_CONNECTION_UX.md`); pages learn the state up front from `google_connection()`. Never answer a failed send with 200. On the treatment page, email goes through `static/js/treatment/email-dialog.js` (`openEmailDialog()`, `handleGoogleRefusal(result, kind)`); its strings are `EMAIL_DIALOG_KEYS` in `web/routers/pages.py`, served in the JSON island.
- Messages to and from patients are logged in `message_log` (`docs/MESSAGE_LOG.md`): outbound sends via `followup_scheduler.log_delivery()`, relay messages via `message_log_repo.record_relay()` (both directions), a check-in answer as `direction='in'`. Always best-effort **after** the send (never a reason to retry), errors redacted, and never the message text.
- Booking (ADR-29): every appointment is created and cancelled through `web/services/booking_service.py` — never a second `INSERT INTO appointments`. `/api/v1` (`web/routers/api/v1.py`) is the same service over HTTP for machine clients; its routes declare the `_guard` dependency (API key or session) and answer `{"code", "detail"}`. After changing the API run `python -m zenflow.export_openapi` (a contract test compares the committed schema).
- Audit (ADR-31): a change to clinical data records one row — `audit.record("entity.verb", entity_type, id, before=…, after=…)` from `web/services/audit.py`. The actor comes from context (`audit.acting_as(...)`), never an argument; `audit_log` is append-only (triggers refuse UPDATE/DELETE) and recording never raises. New clinical endpoints go in `CLINICAL_MUTATIONS` in `tests/integration/test_audit_log.py`.
- Patients (ADR-28): `patient_id` everywhere in SQLite is `patients.id`, never a Telegram id. Find or create the patient behind a channel identity with `patient_repo.for_channel(channel, external_id, name)`; whether and where to message them is `patient_repo.messaging_contact(patient_id)` — never decide from the id's sign or from `source='manual'` (a test fails on `patient_id < 0`). Telegram-keyed state (relay, intake history) stays keyed by the Telegram user id.
- Cancelled appointments are **soft-deleted** (`status='cancelled'`). Records preserved for clinical history.
- `cancel_appointment(appointment_id: int)` takes an integer row ID from SQLite.
- All Ollama calls are wrapped in `asyncio.wait_for(..., timeout=100)`. Fallback questions used if unavailable.
- Read configuration through `zenflow.settings.get_settings()` — never `os.getenv` (exceptions: `bot/db.py`, `startup/launch.py`). New flags go in `FeatureFlags` with both paths tested.
- Background work (ADR-20): never `asyncio.ensure_future(...)` fire-and-forget for anything that must happen — `get_default_queue().enqueue(name, payload, run_at=..., idempotency_key=...)` and register the handler with `@default_registry.handler(name)` in `zenflow/worker.py` consumers.
- Time (ADR-19): use `zenflow.clock` — `iso_now()`, `hours_ahead(n)`, `today()` (clinic-local), `SQL_NOW` in SQL. Never `datetime.now()` / `date.today()` / `datetime('now')`; ruff `DTZ` fails the build. Stored instants are `YYYY-MM-DDTHH:MM:SSZ`.
- Logging (ADR-18): `logging.getLogger(__name__)` as usual — `zenflow/logging.py` configures the root once per process. Bind context with `zlog.bind(...)` / `with zlog.log_context(appointment_id=..)`; time calls with `zlog.timed(...)`. Never `print()` in services; never log tokens (they are redacted anyway).
- CSRF (ADR-36, 9.3): every unsafe cookie-authenticated request needs the double-submit token — `static/js/csrf.js` adds the `X-CSRF-Token` header to same-origin `fetch` and fills a `csrf_token` form field, and `csrf.protect` verifies it. New cookie-authenticated routers are included in `web/app.py` with `Depends(csrf_protect)` (skip it only for a signature-authed webhook); `tests/security/test_csrf.py` fails on an unguarded unsafe route.
- CSP & headers (ADR-37, 9.4): `web/csp.py` sets the static content headers (nosniff, frame-options, referrer, permissions) and a nonce-based Content-Security-Policy, ships **report-only** by default (`ZF_CSP_ENFORCE=1` to enforce, once SF-016's inline handlers/styles are gone). Any **inline** `<script>` a template emits (or a cross-origin script tag) must carry `nonce="{{ csp_nonce() }}"` or the browser will report/block it; external scripts/styles from a new origin must be added to `csp.policy()`. `tests/security/test_csp.py` asserts the headers and that the header nonce is the one rendered into the page.
- Authz (ADR-17, 9.1): a new route must be added to `ROUTES` in `web/authz.py` with its auth level, object-level scope and the test that proves the scope, then `python -m zenflow.export_routes` — `tests/security/test_route_inventory.py` fails otherwise. Every `/api` router is included in `web/app.py` with `dependencies=_API_AUTH`; any endpoint that touches an appointment resolves it via `resolve_owned_appointment` (404) or `require_appointment_access` (403) from `web/deps.py` — never by patient/date/time alone. Repository reads take a `therapist_id` filter.
- `availability.py` may import `appointments.py` — not the other way around (circular import risk).
- Relay Bot clients: never build `Bot(token=...)` in a module. `bot.main.wire_bots()` hands the relay channels over the running applications' own clients (BOT_AUDIT B14).
- Messaging (ADR-27): anything the system sends goes through `bot.interfaces` — `get_channel("telegram")` / `get_default_channel()` for patients, `get_staff_channel()` for therapists — never httpx to api.telegram.org (only `telegram_channel.py` may name it). Handle `ChannelError` (`permanent`, `retry_after`). A new channel implements `ChannelAdapter` and passes `tests/contract/channel_conformance.py`. Tests: `fake_telegram` is an offline Bot API (`api_calls`, `fail_next(...)`, `down`).
- AI (ADR-32): every model call goes through `ai_calls.ask(model, messages, stage=…, timeout_seconds=…)` in `web/services/ai_calls.py` — never `ainvoke` directly (a test walks the tree). It returns and raises exactly what the model did, so keep your fallback; a new call site means a new stage name. Prompts are stored as SHA-256 only — `ZF_AI_DEBUG_PROMPTS=1` keeps the text in dev alone.
- Audit (ADR-31): a clinical mutation records `audit.record(action, entity_type, id, before=…, after=…)`; the actor comes from context (`audit.acting_as(...)`), never from an argument.
- Booking: write the appointment row first (`save_appointment` raises `SlotTaken`), then touch the calendar (BOT_AUDIT B4).
- SQLite `active` column is `INTEGER` (0/1); always cast: `bool(t.get("active"))`.
- Treatment page: no `<style>` or inline `<script>` in `templates/treatment*` (only the `#treatment-config` JSON island) — tests read the page through `tests/integration/treatment_source.py`.
- Treatment page styles: never write `style="…"` (markup or JS strings) or inject `<style>`; add a `tp-*` class to `static/css/treatment.css` as `.tp .tp-x { … }` (elements outside `#treatment-root` need `tp` on their own root). Data-driven colours are classes that set variables (`tp-tone-*`, `tp-ch-*`); a data-driven size is set through the CSSOM (`el.style.width = …`). Hover/focus feedback is CSS, not JS.
- New UI colours/sizes come from `static/css/tokens.css` (`var(--zf-…)`); text colours must keep ≥ 4.5:1 (`tests/unit/test_design_tokens.py`). Every `tp-*`/`pc-*` class the treatment page uses must be defined, and every defined one used (`tests/integration/test_treatment_template.py`); point-card CSS uses logical properties only (RTL).
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
| `WHATSAPP_PHONE_NUMBER_ID` / `WHATSAPP_TOKEN` / `WHATSAPP_APP_SECRET` / `WHATSAPP_VERIFY_TOKEN` / `WHATSAPP_API_VERSION` | — / — / — / — / `v23.0` | WhatsApp Cloud API (only with `ZF_CHANNEL_WHATSAPP=1`; `docs/WHATSAPP.md`) |
| `WHATSAPP_TEMPLATE_FOLLOWUP` / `WHATSAPP_TEMPLATE_CONFIRMATION` | — | Approved template names for messages outside WhatsApp's 24-hour window |
| `TELEGRAM_WEBHOOK_SECRET` | — | Secret Telegram echoes on webhook calls (7.1); empty ⇒ every webhook refused |
| `ZF_API_RATE_PER_MINUTE` | `60` | Booking API requests per minute per caller (`0` = no limit) |
| `ZF_AI_DEBUG_PROMPTS` | `0` | `1` = keep AI prompts/answers in `ai_calls` in the clear; dev and tests only (8.2) |
| `ZF_METRICS_PROMETHEUS` | `0` | `1` = `/api/admin/metrics?format=prometheus` serves the text exposition format (8.4) |
| `ZF_TRACING` | `0` | `1` = OpenTelemetry tracing, when the packages are installed (8.4) |
| `ZF_SESSION_IDLE_MINUTES` | `720` | Idle minutes before a dashboard session ends (9.2) |
| `ZF_SESSION_MAX_HOURS` | `168` | A dashboard session's absolute lifetime after sign-in (9.2) |
| `ZF_CSP_ENFORCE` | `0` | `1` = send the Content-Security-Policy as enforcing; `0` = report-only (9.4, SF-016) |
| `ZF_AUTO_FOLLOWUP` | `0` | `1` = sessions never marked complete still get the 24h check-in (owner decision Q7) |
| `MEDIA_ROOT` | `data/media` | Where LocalStorage keeps acupoint images (Phase 4.3b) |
| `S3_BUCKET` / `S3_PREFIX` / `S3_REGION` / `S3_KMS_KEY_ID` / `S3_ENDPOINT_URL` | — / `media/` / — / — / — | S3 media store when `ZF_STORAGE_S3=1` (bucket required; credentials from the AWS chain, never `.env`) |
| `ZF_POINT_IMAGES` | `0` | `1` = `/api/acupoints` lists point images (needs licensed images, Q4) |
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
- System health API (`/api/status`, auth required) covering Redis, Ollama, bots, Google Calendar; public liveness probe `GET /healthz`, readiness gate `GET /readyz` (503 when the database is gone), operator numbers at `GET /api/admin/metrics` (jobs, follow-ups, AI latency/failures, messages, relay — `docs/METRICS.md`)
- "Change Therapist" button in main menu (appears after therapist is selected)
- In-flight flows survive a bot restart (`bot/persistence.py`, table `bot_persistence`; scheduling keys only, never clinical text)
- Test harness (`tests/`): per-test SQLite, fakeredis, ASGI client, fake Telegram/LLM, factories; `python tasks.py test`
- Multi-tenant isolation: every API/page route scoped to the session therapist; attack suite in `tests/security/`

## Planned
- Switch `USE_AI=anthropic` for production Claude API
