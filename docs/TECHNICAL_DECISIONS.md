# ZenFlow — Technical Decisions (ADRs)

> Architecture Decision Records — explains WHY each major technical choice was made,
> what alternatives were considered, and what trade-offs were accepted.

---

## ADR-01: SQLite as Primary Data Store

**Decision:** Use SQLite (`data/zenflow.db`) as the primary database.

**Alternatives considered:**
- PostgreSQL / MySQL (external server)
- MongoDB (document store)
- JSON files (original approach)

**Reasons:**
- Single-process clinic deployment — no need for a network database server
- Zero infrastructure to manage (no separate DB process, no connection pooling)
- WAL mode enables concurrent reads from bot + web without locking
- Full ACID transactions for appointment save atomicity
- SQLite is fast enough for < 1,000 appointments and < 20 therapists

**Trade-offs accepted:**
- Cannot scale horizontally across multiple servers (acceptable for a single clinic)
- No built-in replication (mitigated by: file backup + WAL checkpoint)
- No ORM — raw SQL used throughout (acceptable for a small, stable schema)

---

## ADR-02: `isolation_level=None` (Autocommit)

**Decision:** SQLite connections use `isolation_level=None` (autocommit mode).

**Problem solved:** Python's default `isolation_level=""` auto-issues `BEGIN` before every `INSERT`/`UPDATE`/`DELETE`. If a write fails and raises an exception, the connection retains an **open transaction**. When asyncio's thread pool reuses the same thread for the next request, calling `execute()` returns `SQLITE_LOCKED` **immediately** (not `SQLITE_BUSY`) — bypassing `busy_timeout` completely. This caused persistent "database is locked" errors on the treatment notes save endpoint.

**Root cause detail:**
- `SQLITE_BUSY` = cross-process lock → handled by `busy_timeout` (waits up to 30 seconds)
- `SQLITE_LOCKED` = within-process stale transaction → immediate failure, no retry

**Alternatives considered:**
- `conn.rollback()` on exception — unreliable; Python's transaction tracking state can diverge from SQLite's actual state
- Per-request connections — expensive; connection creation + PRAGMA setup on every handler call
- ORM with proper transaction management — too heavy for this project

**Trade-offs accepted:**
- `conn.commit()` calls throughout the codebase become no-ops (harmless but noisy)
- Multi-statement atomicity must be managed explicitly with `BEGIN`/`COMMIT`/`ROLLBACK`

---

## ADR-03: Redis as Cache + Messaging Layer

**Decision:** Use Redis for availability cache, appointment list cache, intake history, relay routing, and registration codes.

**Alternatives considered:**
- Pure in-memory Python dicts — lost on restart, not shared between processes
- Memcached — no Pub/Sub, no sorted sets, weaker data types for LangChain
- Database queries on every request — acceptable latency but unnecessary load

**Reasons:**
- **Cross-process sharing:** Bot process and web process both read the appointment/availability cache
- **Restart resilience:** Intake history survives bot crashes (patient can continue from where they left off)
- **LangChain compatibility:** `RedisChatMessageHistory` (from `langchain-redis`) integrates directly with LangChain's history API
- **TTL semantics:** Redis native key expiry for session management (relay sessions, registration codes)
- **Eviction policy:** `allkeys-lru` ensures the cache stays under 1 GB automatically

**Trade-offs accepted:**
- Redis must be running before the bot/web start (mitigated by `launch.py` auto-starting Redis)
- Relay sessions with no TTL (`zenflow:relay:active:*`) can accumulate if the bot crashes mid-session

---

## ADR-04: Two-Bot Relay Architecture

**Decision:** Use two separate Telegram bots (patient bot + therapist bot) for the relay.

**Alternatives considered:**
- Single bot with `/forward` command — Telegram does not allow a bot to forward its own messages back to itself
- One bot per therapist — each therapist creates their own bot; multiplies polling connections and setup complexity

**Reasons:**
- Telegram does not deliver messages a bot sends to itself — a single bot cannot bridge patient ↔ therapist
- Two tokens, two polling loops in the same process — minimal overhead
- All therapists share one therapist bot — single set of credentials to manage
- Routing is done in software (Redis `zenflow:relay:msg:{msg_id}`) not at the Telegram level

**Trade-offs accepted:**
- Both tokens must be set in `.env`
- Therapist must **reply-to** the forwarded message for precise routing (this is clear UX once explained)
- Cross-therapist reply security requires an extra check in the reply handler

---

## ADR-05: Local LLM (Ollama) Over Cloud API

**Decision:** Use Ollama with `gemma3:latest` as the default LLM for AI intake.

**Alternatives considered:**
- OpenAI GPT-4 API
- Anthropic Claude API (planned future switch via `USE_AI=anthropic`)
- No AI (static questions only)

**Reasons:**
- **Privacy:** Patient medical information never leaves the clinic network
- **Cost:** No per-token API fees
- **Reliability:** No dependency on external API availability or rate limits
- **Compliance:** Medical data stays local (relevant for HIPAA/GDPR contexts)

**Trade-offs accepted:**
- Requires installation and running of Ollama on the same machine
- Response time is slower than cloud API (2–30 seconds depending on hardware)
- Model quality is lower than GPT-4 (mitigated by 5-question limit + fallback questions)
- If Ollama is unavailable, bot falls back to static questions silently (logged as warning)

**Future path:** `USE_AI=anthropic` in `.env` will switch to Claude API (code path not yet implemented).

---

## ADR-06: `asyncio.to_thread()` for Google Calendar API

**Decision:** All Google Calendar API calls are wrapped in `asyncio.to_thread()`.

**Problem:** The `google-api-python-client` library is synchronous-only. Calling it directly in an `async def` handler would block the entire asyncio event loop for the duration of the HTTP request.

**Alternatives considered:**
- `aiohttp` + manual Google Calendar API implementation — significant effort, no official async client
- `gspread-asyncio` — wrong library (Sheets, not Calendar)
- Thread pool executor — same as `asyncio.to_thread()` but more verbose

**Pattern used:**
```python
events = await asyncio.to_thread(
    service.events().list(...).execute   # pass .execute (not .execute()) as callable
)
```

---

## ADR-07: Soft Delete for Appointments

**Decision:** Cancelled appointments set `status='cancelled'` — the row is never deleted.

**Alternatives considered:**
- Hard delete (`DELETE FROM appointments WHERE id=?`) — simpler queries
- Separate `cancelled_appointments` table — unnecessary join complexity

**Reasons:**
- Clinical records must be preserved for medical history, billing, and compliance
- The web dashboard `/sessions` page shows all historical sessions including cancelled
- Analytics (future) can compare booking vs cancellation rates
- No meaningful storage cost for preserving text rows

**Trade-offs accepted:**
- `get_patient_appointments()` must always filter `WHERE status='active'`
- Appointment list cache (`zenflow:apts:all`) includes all rows; display filtering done in Python

---

## ADR-08: Per-Therapist Google Calendar Tokens

**Decision:** Each therapist has their own `data/google_tokens/{id}.json` file. No cross-therapist fallback.

**Alternatives considered:**
- Single shared service account — requires Google Workspace admin setup; complex permissions
- Single shared OAuth token — all therapists' appointments go to one calendar; no isolation

**Reasons:**
- Each therapist sees their own availability, not shared clinic availability
- Google Calendar is an optional integration — therapists without Google connect use local SQLite
- `_resolve_token_file()` returning `None` cleanly triggers local mode without any error

**Trade-offs accepted:**
- Each therapist must independently complete the Google OAuth flow
- Token files must be excluded from git (`.gitignore`)

---

## ADR-09: Session Cookies vs JWT

**Decision:** Use `SessionMiddleware` (server-side signed cookie) instead of JWT.

**Alternatives considered:**
- JWT (stateless token) — more complex; requires token invalidation mechanism for logout
- Database session tokens — additional table + query on every request
- HTTP Basic Auth — not suitable for a web dashboard

**Reasons:**
- `itsdangerous` `SessionMiddleware` is built into Starlette (FastAPI's base)
- Server signs the cookie with `SESSION_SECRET` — no separate session store needed
- Logout is a true "forget" — just clear the cookie
- 30-day max-age is appropriate for a trusted device (therapist's own machine)

**Trade-offs accepted:**
- If `SESSION_SECRET` is rotated, all existing sessions are invalidated
- No token revocation (a stolen cookie is valid until expiry) — acceptable for local deployment

---

## ADR-10: No `PicklePersistence` for Bot State

**Decision:** PTB `PicklePersistence` is not currently implemented. `context.user_data` is lost on bot restart.

**Impact:**
- A patient mid-booking who experiences a bot restart must start over from `/start`
- Intake history (Redis-backed) survives the restart; only the booking state (day/time/count) is lost

**Why accepted:**
- The `startup/launch.py` supervisor restarts the bot automatically on crash (up to 5 times)
- Bot restarts are rare in normal operation
- `PicklePersistence` adds file I/O to every state change, which can cause its own issues

**Planned:** Add `PicklePersistence` as a future improvement.

---

## ADR-11: In-Memory Therapist Registry Mutation

**Decision:** When a therapist activates via bot, `THERAPIST_MAP` and `THERAPIST_BY_ID` are mutated in-place immediately, without requiring a bot restart.

**Pattern:**
```python
# In _register_therapist_to_db():
import bot.config as _cfg
_cfg.THERAPISTS.append(new_therapist)
_cfg.THERAPIST_MAP[user_id] = new_therapist
_cfg.THERAPIST_BY_ID[new_therapist["id"]] = new_therapist
```

**Alternatives considered:**
- Restart the bot after activation — poor UX; activation code would need to survive restart
- Query SQLite on every message from the therapist bot — added latency for every message

**Reasons:**
- Immediate activation without any UX gap
- Therapist dicts are small; in-memory mutation is safe
- The web process has its own copy (`_load_therapists_fresh()`) — not affected

**Trade-offs accepted:**
- Module-level state mutation is not "clean" Python design
- Web process does not see the new therapist until next web request (reads fresh from SQLite each time — this is fine)

---

## ADR-12: Split `app.js` into Focused Modules

**Decision:** The single `web/static/app.js` (427 lines) was split into six files under `web/static/js/`.

**Files and responsibilities:**

| File | Responsibility |
|---|---|
| `utils.js` | `$` DOM helper, `showToast`, `fmt` |
| `calendar-list.js` | Sidebar calendar list, visibility toggles, context-menu rename |
| `mini-calendar.js` | Mini month date picker |
| `slots.js` | Drag-to-create availability slot (`saveSlot`) |
| `popover.js` | Event click popover: render, position, delete confirm |
| `main-calendar.js` | FullCalendar init + DOMContentLoaded event wiring |

**Alternatives considered:**
- ES modules with `import`/`export` — requires a bundler or HTTP/2 server; adds build tooling complexity with no other frontend build step in the project
- Keep as one file — works but makes targeted changes and git diffs harder to review

**Reasons:**
- Each file now has a single clear purpose — easier to locate, change, and review in isolation
- Smaller diffs per PR: a change to the popover does not touch the mini-calendar or slot code
- No bundler needed — files share the global scope and are loaded in dependency order via `<script>` tags in `schedule.html`

**Load order (must be preserved):** `utils.js` → `calendar-list.js` → `mini-calendar.js` → `slots.js` → `popover.js` → `main-calendar.js`

**Trade-offs accepted:**
- All files still share the browser global scope (no true encapsulation without a bundler)
- `mainCal` is declared in `main-calendar.js` but referenced in earlier files — safe because those references only execute after `DOMContentLoaded`, when `mainCal` has already been assigned
- The legacy `web/static/app.js` remains on disk but is no longer loaded anywhere

---

## ADR-13: Code-Quality Toolchain and the Baseline-Ratchet Policy

**Date:** 2026-09-14 (Phase 0.2 of `docs/MASTER_PLAN_EN.md`)

**Decision:** `black` (100 cols, py312) is the single formatter; `ruff` lints
(`E,F,W,I,B,UP,S,ASYNC,C4,SIM`); `mypy` runs everywhere but is **strict only** for
`bot/interfaces` and `web/repositories`; `pytest` with `asyncio_mode=auto`; coverage gate via
`fail_under`. Everything is configured in one `pyproject.toml`, enforced by `.pre-commit-config.yaml`
(pre-commit-hooks, black, ruff, gitleaks, local pytest) and exposed as `make` / `tasks.py` targets.

**Baseline-ratchet policy.** On 2026-09-14 the code had 283 ruff findings, 533 mypy errors and 0 %
test coverage. Instead of fixing everything in one risky sweep (clinical code with no test suite
yet) the tooling encodes the measured baseline explicitly:
- `[tool.ruff.lint] ignore` lists the pre-existing rule codes with the phase that removes each;
- `[[tool.mypy.overrides]] ignore_errors = true` lists the 38 modules with pre-existing type errors;
- `[tool.coverage.report] fail_under = 0`.
Each is a debt list: a phase that touches a module removes it from the list and fixes it; the
coverage floor rises +5 per phase. **Adding to any of these lists is forbidden.**

**Alternatives considered:**
- `ruff format` instead of / in addition to black — rejected: the two disagree on method chains
  around multi-line SQL strings (seen in `web/repositories/appointment_repo.py`); two formatters
  that fight make the gate impossible. The plan named black; black stays.
- Fix all findings up front — rejected: ~800 mechanical edits with no tests to catch regressions.
- `uv` for locking — deferred; `pip-tools` needs no new binary and the launcher already uses pip.

**Lockfile policy:** `requirements.in` / `requirements-dev.in` are the human-edited inputs;
`requirements.txt` / `requirements-dev.txt` are compiled by pip-tools and **constrained to the
versions the venv was verified with** (so locking did not silently upgrade transitive packages).
`cryptography` (imported by `web/gcal.py`) and `langchain-anthropic` (lazily imported) were missing
and are now pinned. The index URL is written explicitly as `https://pypi.org/simple`.

**Consequences:** `make all` is green today by construction; real quality comes from shrinking the
baselines phase by phase, which `docs/PROGRESS.md` tracks.

---

## ADR-14: HTTPS-Only for Every Non-Local URL

**Date:** 2026-09-14 (owner decision during Phase 0.2)

**Decision:** any configured URL that does not point at `localhost` / `127.0.0.1` must use
`https://` (`rediss://` for Redis, `wss://` for websockets). Plain `http://` is permitted for local
development only. This applies to `OLLAMA_HOST`, `REDIS_URL`, all `GOOGLE_*_REDIRECT_URI` values,
the package index URL, and any future webhook / CDN / S3 endpoint.

**Enforcement path:** documented now in `.env.example`, `docs/ARCHITECTURE.md` and the plan
(Phase 0.4 settings validation rejects non-local `http://` when `ENV != dev`; Phase 0.5 makes the
session cookie `https_only` outside dev; Phase 9.4 adds HSTS). `tests/unit/test_tooling.py` already
fails if `.env.example` or the lockfiles contain a non-local `http://`.

**Reasons:** the system carries medical records, OAuth tokens and bot tokens; an OAuth redirect or a
Redis connection over plain HTTP exposes them on the wire.

---

## ADR-15: Test Harness Design — Path-Injectable SQLite, In-Process Fakes

**Date:** 2026-09-14 (Phase 0.3)

**Problem:** `bot/config.py` reads every env var and calls `init_db()` at import time, and
`bot/db.py` hard-coded `data/zenflow.db`. Any test that imported project code would have written to
the production database.

**Decision:**
- `bot/db.py` resolves the file from `ZENFLOW_DB_PATH` on every `get_db()` call and reconnects a
  thread whose cached connection points elsewhere. This is the smallest change that makes the DB
  injectable without restructuring `bot/config.py` (Phase 0.4 does that).
- `tests/conftest.py` sets the whole environment at module top, before any project import, and
  asserts the resolved path is never the real file.
- Redis is `fakeredis` (one `FakeServer` shared by the sync and async clients) patched into the
  `bot.redis_client` singletons — no Redis process in CI.
- The web app is exercised through `httpx.ASGITransport`; the authenticated client signs in through
  the real `/register/signin` form so the session cookie path is covered, not bypassed.
- Telegram and the LLM are replaced at the narrowest seam: `telegram_service._send` and the three
  module-level chat models in `ai_intake` (plus an in-memory chat history), so the parsing and
  fallback logic around them still runs.
- Factories write through the repositories, not raw SQL, so schema drift surfaces in the factories.

**Alternatives considered:** a `create_app()` factory with dependency injection (right long-term,
too invasive for Phase 0); an in-memory `:memory:` SQLite (breaks the thread-local / WAL model the
app relies on); a real Redis container (slower, and fakeredis covers every command used).

**Consequences:** importing `ai_intake` still performs a 3-second-timeout Ollama health probe at
import time (pointed at a closed port in tests, so it fails instantly). Phase 0.4 centralised the
environment reads; the import-time `init_db()` / therapist-registry load in `bot/config.py` stays
until Phase 12.2.4 removes the mutable module globals.

---

## ADR-16: One Settings Module, Fail-Fast Validation, Typed Feature Flags

**Date:** 2026-09-15 (Phase 0.4)

**Decision:** `zenflow/settings.py` (pydantic-settings) is the only place environment variables
are read. `bot/config.py` keeps its module-level constant names (about twenty importers) but
sources every value from `get_settings()`. Validation runs at construction, so a bad environment
stops the process before it serves a request:
- outside `dev`/`test`: `SESSION_SECRET` must be set, non-default and ≥ 32 chars;
  `TOKEN_ENCRYPTION_KEY` must be set, ≥ 32 chars and different from `SESSION_SECRET` (F7);
  every non-localhost URL must be `https://` (`rediss://` for Redis) (ADR-14);
- feature flags are a typed `FeatureFlags` model with the `ZF_` prefix; unknown values are
  rejected; `GET /api/admin/flags` (auth required) shows the live state and never secrets.

**Key separation (F7):** `web/gcal.py` now derives the Fernet key from `TOKEN_ENCRYPTION_KEY`
when set and from `SESSION_SECRET` otherwise, so pre-0.4 rows keep decrypting.
`python -m zenflow.rotate_token_key` re-encrypts `google_tokens` from the old material to the
new one: dry-run mode, consistent SQLite backup (via the backup API, so WAL content is included),
idempotent, and rows that decrypt with neither key are reported and left untouched.

**Rule for flags:** every flag has BOTH values exercised in tests (`tests/unit/test_settings.py`
parametrises all eight). A flag that is never exercised is a lie. Consumers arrive with their
phases (queue backend 1.2, SSE 3.4, images 4.3, WhatsApp 7.4, cloud 12); until then the only
runtime consumers are `ai_provider` (intake LLM selection) and `channel_whatsapp` (channel factory).

**Sanctioned exceptions:** `bot/db.py` reads `ZENFLOW_DB_PATH` itself because the test harness
must redirect the database before any project import; `startup/launch.py` parses `.env` by hand
because it runs before dependencies are installed.

**Alternatives considered:** keep `os.getenv` and add a validator function (no single source of
truth, easy to bypass); `dynaconf` / `environs` (another dependency for the same result;
pydantic-settings was already installed transitively); a module of plain constants that reads
`.env` (no typing, no validation).

**Correction (review of PR #3):** an earlier version of this ADR moved the default Google
redirect URIs to port 8000 "to match the web app". That was wrong — `startup/run_web.py` and
`startup/launch.py` serve the dev dashboard on **8080** (the `Procfile` uses `$PORT`, default
8000, only in production where the URIs are set explicitly). The defaults are back on 8080 and
the docs that said 8000 for local dev were corrected. Also: `.env` is loaded only from the
project root (python-dotenv used to search parent directories), and `ZENFLOW_DOTENV=0` disables
the file entirely (the test harness sets it so a developer's real `.env` never leaks into tests).
`bot/db.py` reads `ZENFLOW_DB_PATH` from the environment first and from settings second, since
pydantic-settings never exports `.env` values into `os.environ`.

---

## ADR-17: Authorization Model — Router-Level Authentication, Object-Level Tenant Scoping

**Date:** 2026-09-15 (Phase 0.5 security triage: F6, F11, SF-005…SF-007)

**Decision:**
1. **Authentication is attached at router level.** `web/app.py` includes every `/api/*` router
   with `dependencies=[Depends(require_signed_in)]`. FastAPI resolves dependencies before it
   validates the body, so an anonymous caller always gets `401` — never a `422` that reveals the
   request schema — and no endpoint can forget the check. `GET /healthz` (outside `/api`) is the
   only public endpoint and returns exactly `{"ok": true}`.
2. **Two authentication levels.** `require_signed_in` = the session names an existing therapist
   (used at router level, because onboarding endpoints such as `/api/my/status` must work for
   not-yet-activated accounts). `require_active_therapist` = signed in *and* activated (used by
   data endpoints).
3. **Authorization is object-level and tenant-scoped.** `resolve_owned_appointment(request,
   patient_id, date, time)` resolves the triplet **filtered by the session therapist** and
   answers `404` otherwise (no existence leak). `require_appointment_access(request,
   appointment_id)` loads by row id and answers `403` when the row belongs to someone else
   (the id path is already enumerable, so hiding existence buys nothing). Repository read
   functions gained an optional `therapist_id` filter; list endpoints filter on it.
4. **Relay conversations are owned by the therapist recorded in the active session key**
   (`zenflow:relay:active:{patient_id}`). Without an active session, a conversation is
   reachable only by a therapist who has an appointment with that patient.
5. **Session cookie:** `Secure` outside dev/test, `SameSite=lax`, explicit `max_age` (30 d),
   `HttpOnly` (always set by the middleware).

**Alternatives considered:** a global middleware keyed on the path prefix (works, but hides the
requirement from the route table and bypasses FastAPI's dependency graph); per-endpoint
decorators only (what existed — and what SF-005 proved is forgotten); moving scoping into SQL
views per tenant (right for Postgres row-level security in Phase 12, premature for SQLite).

**Consequences:** every future `/api` router must be included with `_API_AUTH`; every
appointment-bound endpoint must resolve through the two helpers. Phase 9.1 adds the route/authz
table with a CI test that fails on any new route lacking an entry.

---

## ADR-18: Structured Logging with a Log-Record Factory, Not structlog

**Date:** 2026-09-15 (Phase 0 task 0.5)

**Decision:** stdlib `logging` plus one module, `zenflow/logging.py`. Context (request id,
therapist, patient, appointment, service) lives in a `ContextVar` and is attached to every record
by a **log-record factory** (`ZenLogRecord`, resolving the fields lazily through `__getattr__`).
Two formatters — `ConsoleFormatter` (dev) and `JsonFormatter` (one object per line) — chosen by
`LOG_FORMAT` (`auto` = by `ENV`). Redaction of every known secret shape runs in the factory
(message + args) and again in the formatters (final text, tracebacks). A Starlette middleware
binds the request id and echoes `X-Request-ID`; the scheduler binds a job id per sweep.

**Why a record factory and not a handler filter:** filters attach to handlers or to the logger
that *created* the record, so any handler we do not own (uvicorn's, pytest's `caplog`, a future
CloudWatch handler) would see records without context. The factory runs for every record in the
process. The lazy `__getattr__` exists because `logging.Logger.makeRecord` refuses `extra=` keys
that already exist on the record — pre-setting `duration_ms` would have broken `timed()`.

**Why not structlog:** it would be a new dependency and a second logging API for every module to
learn; the plan allows a stdlib JSON formatter; uvicorn / python-telegram-bot / langchain all log
through stdlib anyway, and the factory approach covers them for free. If we ever want structlog's
processor pipeline (Phase 8 metrics/tracing) the context and redaction functions plug straight
into it.

**Consequences:** `bot/main.py`'s ad-hoc single-line formatter is gone. `logs/botLogs.text` is
still truncated per start and `logs/webLogs.text` appended, as before. The uvicorn access log is
kept; the app adds its own `web.access` line with `request_id`, `therapist_id`, `duration_ms`.
Redaction is pattern-based — a brand-new secret shape needs a new pattern *and* a test in
`tests/unit/test_logging.py`.

---

## ADR-19: One Clock — Canonical UTC Strings, Clinic-Local Calendar Dates

**Date:** 2026-09-15 (Phase 1.1, fixes F2)

**Problem:** `completed_at` and `pending_rec_send_at` were written with `datetime.now().isoformat()`
(host-local, naive), `followup_sent_at` / `created_at` / `updated_at` with SQLite `datetime('now')`
(UTC, space-separated), and the follow-up window compared them as strings against
`datetime.now(UTC).isoformat()` (`+00:00`, microseconds). On a UTC+3 host the 22–26 h window was
really 19–23 h, and the three shapes never sorted consistently.

**Decision:**
1. `zenflow/clock.py` is the only source of "now". Canonical stored form:
   `YYYY-MM-DDTHH:MM:SSZ`. `to_iso()` refuses naive datetimes; `parse_iso()` / `normalize()`
   accept every legacy shape.
2. SQL stamps use `clock.SQL_NOW` (`strftime('%Y-%m-%dT%H:%M:%SZ','now')`); every INSERT sets
   `created_at` explicitly so the table DEFAULTs are dead (SQLite cannot alter a DEFAULT without a
   table rebuild — Phase 12.2.3 Alembic does that).
3. `CLINIC_TZ` (default `Asia/Jerusalem`) defines "today". `clock.today()` replaces every
   `date.today()`: the clinic's schedule must not flip at UTC midnight on a cloud host.
4. Ruff `DTZ` (flake8-datetimez) is enabled repo-wide; the single legitimate naive constructor
   (`_slot_datetime`, a clinic wall-clock slot) carries a commented `noqa`.
5. `python -m zenflow.migrate_timestamps` rewrites existing rows (dry-run, backup, idempotent,
   unparseable rows reported and left alone). Naive Python-written values are interpreted in
   `--local-tz` (default `CLINIC_TZ`) because the old process ran on the clinic's machine.

**Alternatives considered:** storing epoch integers (compact and comparable, but unreadable in
`sqlite3` and in logs, and every existing row is text); `+00:00` suffix instead of `Z` (both are
ISO; `Z` is shorter and is what `strftime` can emit natively); keeping `datetime('now')` and
normalising on read (leaves SQL-side comparisons broken).

**Consequences:** relay timestamps stay epoch floats (`time.time()`, already UTC). Availability
slot strings from FullCalendar remain naive wall-clock strings by design. The freezegun quirk that
`tz_offset` also shifts `datetime.now(UTC)` means tests vary `CLINIC_TZ`, not the host offset;
host offset cannot influence the code any more by construction.

---

## ADR-20: Durable Job Queue — SQLite `jobs` Table Behind a `TaskQueue` Interface

**Date:** 2026-09-15 (Phase 1.2; the plan asked for a researched decision, not an assumption)

**Context.** One small clinic; SQLite + Redis today, AWS later; jobs measured in minutes to
hours (24 h follow-up, N-hour recommendation delivery, the intake → diagnosis → points chain).
Today the follow-up scheduler polls every 30 min inside the bot process and the intake pipeline
is `asyncio.ensure_future` fire-and-forget: a restart loses both, nothing retries, and "already
sent" lives only in a Redis key that a flush erases.

**Options compared for THIS system**

| Option | Durability across restart | Idempotency / retry | New infra | Fit for 24 h delays | Verdict |
|---|---|---|---|---|---|
| (a) in-process asyncio + DB `jobs` table | yes (rows) | built here, tested | none | native (`run_at`) | **chosen now** |
| (b) APScheduler + SQLAlchemy jobstore | yes | weak (no attempts/dead-letter model; misfire handling only) | SQLAlchemy dep | ok | adds a dep for less than (a) |
| (c) Celery + Redis broker | broker-dependent; Redis is not durable here (`allkeys-lru`) | retries yes; long ETAs are unreliable (visibility timeout re-delivery) | worker process + broker semantics | poor for 24 h | Phase 12 option |
| (d) Temporal | excellent (workflow history) | excellent (`sleep(24h)` with retries) | server or Temporal Cloud + SDK | ideal | heavyweight for one clinic; keep as the upgrade path |
| (e) EventBridge Scheduler + SQS + Lambda/ECS | excellent | at-least-once + DLQ | AWS only | native | Phase 12 target when on AWS |

**Decision.** Implement (a): `zenflow/queue.py` (`TaskQueue` ABC + `SqliteTaskQueue`) and
`zenflow/worker.py`. Claim is a single atomic `UPDATE … RETURNING`; `idempotency_key` is
`UNIQUE`; retries back off 60 s × 2^(n−1) up to `max_attempts` then `dead`; a `running` job whose
lock is older than 10 min is reclaimable (crashed worker). The worker runs inside the bot process
when `ZF_QUEUE_BACKEND=inprocess` and standalone via `python -m zenflow.worker`. (c)/(d)/(e) plug
in as further `TaskQueue` implementations in Phase 12; `get_default_queue()` raises
`NotImplementedError` naming that phase for the other flag values, and both paths are tested.

**Why not Redis for the queue:** the Redis here is a cache with `allkeys-lru` eviction; a job store
must survive eviction and restarts. SQLite already holds every clinical record and is backed up.

**Consequences.** One more table in the same database; the worker polls every 5 s when idle
(one indexed query). Exactly-once is achieved as at-least-once + idempotent handlers + the
idempotency key — handlers must be safe to re-run. Phase 1.3 registers the follow-up and
recommendation handlers and replaces the 30-minute poll; Phase 3.1 moves the intake pipeline.

**Addendum (2026-09-17, Phase 5.4): waiting is not failing.** Some jobs cannot proceed until a
person acts, for example an email that needs the therapist to connect Google. Retrying those
would burn the attempt budget and dead-letter a send that would succeed an hour later. The
contract gains three verbs, each covered by the conformance tests:

- **`defer(job_id, run_at, reason)`:** a running job goes back to `pending` until `run_at`. The
  attempt is not charged, and `reason` is kept as `last_error`.
- **`pending(name)`:** lists what is waiting.
- **`run_now(job_id)`:** ends a wait early, when what the job waited for has happened.

A handler signals a wait by raising `zenflow.worker.JobDeferred(run_at, reason)`. The worker then
defers the job and runs no dead-letter hook. The Celery, Temporal and AWS backends of Phase 12
must offer the same behaviour: a delayed re-delivery that does not count as a failure.

---

## ADR-21: Follow-ups and Recommendations Are Enqueued at Completion, Not Polled

**Date:** 2026-09-15 (Phase 1.3; fixes F1)

**Before.** `followup_scheduler` polled every 30 minutes inside the bot process and did two
unrelated jobs: sessions completed 22–26 h ago got step 1, and due queued recommendations were
sent. A restart across the window lost the follow-up; the only "already sent" guard was a Redis
key; a Telegram failure was logged and dropped; the email fallback called
`send_email(to, subject, body)` against the signature `send_email(therapist_id, to, subject,
body)`, so it always raised `TypeError`, was swallowed, and surfaced as a generic failure alert
(F1).

**Decision.**
1. `POST …/complete` enqueues `followup.send_step1` at `completed_at + 24h`
   (key `followup:{appointment_id}`); queuing recommendations enqueues
   `recommendations.dispatch` at `pending_rec_send_at` (key
   `recommendations:{appointment_id}:{send_at}`), from both the auto-queue at completion and the
   explicit "schedule ≥ 24h" path. Enqueue failures never fail the request (`safe_enqueue`).
2. Handlers are idempotent against the database. The follow-up checks `followup_sent_at`, the
   conversation and the rating; a delivery failure now raises so the job retries with backoff.
   A follow-up that would fire more than 48 h after completion is dropped (a "yesterday" message
   two days late is worse than none). Patients with no messaging channel are skipped (Phase 6.4
   raises a therapist alert). Recommendations skip when the queue entry was cleared or its send
   time changed, so rescheduling needs no job cancellation.
3. F1: the email fallback passes the therapist id first. When Gmail is not connected the
   therapist gets one "send failed" alert and the queue entry is kept for "Send Now" — retrying
   cannot help until they connect Google (Phase 5.4 adds retry-after-reconnect). Other failures
   retry; the therapist is alerted only on the final attempt (`zenflow.worker.is_last_attempt()`),
   not on every retry.
4. The 30-minute loop survives only as `reconcile()`: a safety net that enqueues jobs for rows
   written before this change or whose enqueue failed.

**Amendment (PR #6 review, 2026-09-15).** At-least-once hazards closed before merge:
- The follow-up key includes `completed_at` (`followup:{apt}:{completed_at}`); completing again
  schedules from the latest completion and the superseded job skips.
- After a successful send the database stamp is written first. Redis is a secondary guard, and a
  retry that finds the Redis mark without a stamp repairs the stamp instead of re-sending.
- Recommendations are cleared immediately after delivery; "sent" notifications are best-effort
  and can no longer trigger a retry (a duplicate message).
- "Send Now" clears any auto-queued copy, so the T+24h job finds nothing to deliver.
- Dead-letter hooks (`HandlerRegistry.on_dead`) alert the therapist once when retries are
  exhausted, including timeouts; the handler no longer alerts per attempt. A worker that keeps
  crashing is dead-lettered by `claim()` without a hook (Phase 8 surfaces dead letters).
- Reconciliation looks back the full 48 h expiry window and one bad row no longer aborts a sweep.

**Consequences.** Delivery is at-least-once. A crash between the Telegram send and the database
stamp can repeat a message once; the ordering keeps that window to milliseconds. Timestamps must
be canonical for the reconciliation query (run `python -m zenflow.migrate_timestamps` once on an
existing database).

---

## ADR-22: Bot State Persists as Whitelisted JSON in SQLite, Not Pickle

**Date:** 2026-09-16 (Phase 2.3)

**Context.** Conversation state and `user_data` lived only in memory, so every restart dropped
every half-finished booking. The plan named `PicklePersistence`, but `bot_data` holds live
asyncio tasks (the follow-up scheduler and the job worker) that cannot be pickled, a pickle file
would be a second unmanaged copy of patient state next to the database, and loading pickle is
code execution. The plan also says never to persist raw clinical free text.

**Options.** (a) `PicklePersistence` with `bot_data` disabled; (b) a Redis-backed persistence
class; (c) a custom `BasePersistence` writing JSON rows to the existing SQLite database.

**Decision.** (c) — `bot/persistence.py::SqlitePersistence`, table `bot_persistence`.
- Stored: conversation states, and only the `user_data` keys in `PERSISTED_USER_KEYS`
  (therapist choice and the scheduling keys of the current flow). The cancel list keeps just the
  fields `confirm_cancel()` needs. `bot_data`, `chat_data` and callback data are never stored.
- Rows older than `ZF_CONV_TIMEOUT_MINUTES` are not restored, so after a long outage a patient
  lands at the menu, not mid-booking; their therapist choice survives.
- (b) was rejected because Redis in this deployment is a cache with eviction; the database is the
  thing that is backed up (`zenflow/db_backup.py`).

**Consequences.** A new `user_data` key is silently *not* persisted until it is added to the
whitelist; this is intentional, and CLAUDE.md says so. Conversation timeouts are not persisted
by PTB: after a restart, the idle timer restarts on the patient's next update. The acceptance
test drives a real `Application` offline (a fake `BaseRequest`) through book → restart → finish.

---

## ADR-23: The Intake AI Pipeline Runs as Four Queued Jobs

**Date:** 2026-09-16 (Phase 3.1; closes BOT_AUDIT B13)

**Before.** The last intake answer fired `asyncio.ensure_future(_summary_and_tcm(...))`: summary,
diagnosis and two point batches in one background coroutine. A restart lost it, nothing retried
it, a failure anywhere marked the whole session FAILED, and it read the conversation from the
Redis intake history (30-minute TTL, in-process caches). Opening the treatment page could start a
second generation for the same session at the same time.

**Decision.**
1. The conversation is saved to `intake_sessions.history_json` together with the appointment,
   inside the booking transaction. The jobs read only the database.
2. Four jobs on the ADR-20 queue, each enqueuing the next: `intake.finalize`,
   `diagnosis.generate`, `points.generate` (batch 1), `points.generate` (batch 2). Idempotency
   keys are `pipeline:{stage}:{appointment}:{run}[:{batch}]`; `run` is `intake` for the automatic
   run, so later explicit runs (Phase 3.2/3.3) get their own chain.
3. Each handler checks the database first and skips work already done (a summary, a pattern,
   `GENERATING_STAGE_2B`/`COMPLETED`). A batch and its status move are one `UPDATE`.
4. A failed AI call raises `GenerationError`; the queue retries (3 attempts, 60 s/120 s backoff).
   On the last attempt Stage 0 degrades to a placeholder summary and Stages 1-2 mark the session
   `FAILED`; dead-letter hooks do the same for timeouts and crashes. The point selector's own
   inner retry is disabled for jobs so one handler cannot outlast the worker's 300 s timeout.
5. `points_status` only moves forward (`advance_points_status(..., only_from=...)`), so a replayed
   job never drags a finished session back to GENERATING.
6. A per-appointment lease `generation:{id}` (`zenflow/leases.py`, TTL 360 s > handler timeout)
   keeps two generations from overlapping even across worker processes; a busy handler raises
   `PipelineBusy` and is retried later.

**Consequences.** The patient's confirmation never waits for the AI, and a restart mid-pipeline
resumes where it stopped. The in-process worker runs one job at a time, so when several
intakes finish together their generations queue behind each other rather than competing for
the same local model (which serialises them anyway); a second `python -m zenflow.worker`
process adds throughput safely because of the lease. The web endpoints that generate synchronously (`rediagnose`,
`generate-points`, `regenerate-points`) do not take the lease yet — Phase 3.2 adds the 409 guard
and Phase 3.3 moves them onto the same jobs.

**Amendment (Phase 3.2, 2026-09-16).** The three web endpoints now take the same lease and
return 409 with `points_status` while a generation is in progress. `force=true` skips only the
status check — for a status a crashed run left behind — never the lease. The treatment page no
longer generates on load; it offers an explicit button.

**Amendment (Phase 3.3, 2026-09-16).** `regenerate-points` answers 202 and queues
`points.generate` with its own run id (`regen-…`) and the therapist's language; the page follows
the status only. `CANCELLED` is a terminal status: `POST …/cancel-generation` sets it (only
over an in-progress status), cancels the session's pending/running pipeline jobs, and every
stage writes with `expect_status` — the status it started with — so a batch that finishes after
Cancel is discarded and its successor is never queued. The synchronous `generate-points` honours
Cancel the same way.

---

## ADR-24: Live Treatment Updates Use SSE With Pub/Sub Wake-Ups

**Date:** 2026-09-16 (Phase 3.4)

**Before.** The treatment page asked `GET /api/treatment-notes/…` every 2 seconds for as long as a
generation ran — up to 15 minutes, per open tab.

**Decision.** Behind `ZF_SSE_UPDATES` (off by default):
1. `GET …/stream` is a server-sent event stream: `notes` with the current state, `notes` again
   whenever they change, `done` once nothing is generating. It closes on its own after 15 minutes
   or when the page goes away.
2. Every `points_status` write in the repository publishes `changed` on
   `zenflow:treatment:{appointment_id}`. The message is only a wake-up: the stream re-reads the
   database, which stays the single source of truth, and sends the notes only if they differ.
3. Pub/sub has no replay, so the stream also re-checks every 2 s. A missed message costs latency,
   never correctness; without Redis the stream degrades to that timer.
4. The page uses `EventSource` when the server says the flag is on and falls back to the old
   polling on any stream error. Both transports feed one `apply(notes)` function.

**Options rejected.** WebSockets (two-way, more moving parts, nothing to send upstream); pushing
the notes themselves over pub/sub (two sources of truth, and a lost message would lose data).

**Consequences.** The flag's two paths are both tested. Each open stream holds one Redis
connection and one thread hop per re-check; at clinic scale that is negligible. Turn the flag on
once Redis is confirmed on the deployment host (HOSTING_AND_MONITORING.md).

---

## ADR-25: A Design-Token Layer in CSS Custom Properties; Dark Mode Is Opt-In

**Date:** 2026-09-17 (Phase 4.2a)

**Before.** Colours, spacing and type sizes were literals repeated across `style.css`, the
treatment page's classes and JS templates. The point-card palette lived in a JS object. Small text
used `#0D9488` and `#9CA3AF`, which are 3.7:1 and 2.5:1 on white (WCAG AA asks for 4.5:1).

**Decision.**
1. `static/css/tokens.css`, loaded by `base.html` on every page, defines `--zf-*` custom
   properties: a 4 px spacing scale, radii, a type scale, shadows, semantic colours (surface,
   text, accent, caution, danger) and a soft/border/ink triple for each acupuncture channel.
2. The prefix keeps them apart from the older `--primary`/`--border`/… variables in `style.css`,
   which stay until each page moves over.
3. Text tokens are chosen for contrast; `tests/unit/test_design_tokens.py` computes the WCAG ratio
   of every text/background pair in both themes, and fails when a dark override is missing.
4. Dark mode is an override block under `:root[data-theme="dark"]`, not
   `prefers-color-scheme`: only the components built on tokens (the point cards so far) change,
   so following the OS setting automatically would produce half-dark pages.
5. Components map tokens to local variables (`tp-ch-liver` sets `--ch-soft/--ch-border/--ch-ink`),
   so a card or chip says *which* palette it uses and its rules stay the same for every channel.

**Options rejected.** A CSS preprocessor (a build step for a server-rendered app with no bundler);
automatic dark mode now (see 4); keeping colours in JS (the CSP work in Phase 4.1c moved styling
out of script, and variables let one rule serve every channel).

**Consequences.** New UI should use tokens; old rules migrate page by page. Turning dark mode on
for real (a setting, then `data-theme` on `<html>`) waits until the shell and the remaining
components are on tokens.

---

## ADR-26: Media Lives Behind a Storage Interface; Images Enter Only Through a Licence-Checking Ingester

**Date:** 2026-09-17 (Phase 4.3b)

**Context.** Point images are the first binary content the app stores. They will move to S3
when the app runs on AWS (Phase 12), and every image needs a traceable licence (plan 4.3: "no
scraping copyrighted atlases").

**Decision.**
1. **Storage interface.** `zenflow/storage.py` defines `Storage` (put, get, exists, delete, url)
   and `get_storage()` picks the implementation: `LocalStorage` (files under `MEDIA_ROOT`) now,
   and `S3Storage` behind `ZF_STORAGE_S3` in 4.3c. Callers only ever hold keys and links.
2. **Key rules.** Keys are validated in one place before any disk or bucket call: relative,
   lower-case segments, a known image extension, and a content type that matches the extension.
   `LocalStorage` also checks that the resolved path stays under its root.
3. **Serving local files.** They are served by `/media/<key>` to signed-in therapists only.
   S3 will hand out presigned links instead, so the page code does not change.
4. **Ingestion.** The only way in is `python -m zenflow.ingest_images`. It refuses a file without a
   credit and a licence, re-encodes every image (which drops EXIF, GPS and other metadata), and
   names objects by content hash, so re-running updates rows instead of duplicating them.
5. **Visibility flag.** `ZF_POINT_IMAGES` decides whether `/api/acupoints` lists images, so the
   code can ship before any licensed image exists (Q4).

**Options rejected.** Storing images as BLOBs in SQLite (grows the database and its backups, and
has no path to a CDN or S3); public static serving from `web/static` (no access control, and
images would sit in the repo); accepting uploads through the web UI now (it needs a licence
workflow and review that nobody has asked for yet).

**Consequences.** Swapping in S3 touches only `get_storage()` and the new class. Images must be
re-ingested, not copied, into a new store, which re-runs the metadata stripping.

**Addendum — S3 (Phase 4.3c).**
- **Configuration:** `S3Storage` is selected by `ZF_STORAGE_S3=1`. The boot fails without
  `S3_BUCKET`, in every environment.
- **Writes:** every object goes under `S3_PREFIX` with `ServerSideEncryption=aws:kms`, using
  `S3_KMS_KEY_ID` or the bucket's AWS-managed key, plus its content type and a private cache
  header.
- **Links:** SigV4 presigned GETs, valid for 1 hour, which is longer than the 10-minute client
  cache of `/api/acupoints`. `/media` answers 404 under S3.
- **Credentials:** they come only from the standard AWS chain (profile or instance role); the
  settings never hold keys.
- **Custom endpoints:** `S3_ENDPOINT_URL` (e.g. MinIO) must be `https://` outside dev (ADR-14).
- **Tests:** the same contract suite runs on both stores (moto for S3).
- **Follow-up for Phase 9:** the CSP must allow `img-src` for the bucket's host.

---

## ADR-27: One Channel Adapter per Messaging Provider, Proven by a Shared Conformance Suite

**Date:** 2026-09-17 (Phase 7.1)

**Context.** The clinic will add WhatsApp (7.4). Before 7.1, outbound messaging was an
outbound-only `MessagingChannel` used by two jobs. Other code called Telegram directly:
- the web reply path and Send Now;
- the reply echo, the bot-name lookups and the status check, which posted to api.telegram.org themselves;
- the relay, which held raw python-telegram-bot `Bot` objects.

Nothing described inbound messages, so a second provider could not be checked against anything.

**Decision.**
1. **The contract.** `ChannelAdapter` (`bot/interfaces/channel.py`) covers:
   - outbound: text, buttons, media, edits and typing;
   - inbound: webhook parsing and verification.

   It returns `SentMessage`, raises `ChannelError` (permanent / `retry_after` / code), and
   validates content against per-adapter limits before sending. `MessagingChannel` stays as an
   alias.
2. **One module per provider.** Telegram is `TelegramChannel`, the only module that names
   api.telegram.org (a test enforces it). It is reached two ways with identical behaviour:
   - over httpx with a token (web process, jobs);
   - through a running application's own `Bot` (the relay), which keeps B14's managed client
     and rate limiter.
3. **The conformance suite.** `tests/contract/channel_conformance.py` is written against the
   contract only. Each adapter supplies a harness for a fake of its provider. Telegram runs it
   through both ways in, against one offline Bot API.
4. **Tests never touch the network.** The test guard now sits at the adapter's HTTP transport,
   so every call — getMe included — is refused or recorded, and the real encoding runs.
5. **The conversation drivers stay provider-specific.** Replies inside a Telegram conversation
   remain python-telegram-bot calls. A WhatsApp front end gets its own driver over the booking
   API (7.3); everything the system initiates goes through an adapter.

**Options rejected:**
- **Moving the whole conversation engine behind the adapter now.** It would rewrite every
  handler before the booking API exists to share the logic.
- **A generic `do_api_request` passthrough.** It would leak Telegram's method names into callers.
- **Patching the old `_send` seam in tests.** It skipped the encoding and error mapping, and it
  let direct httpx calls reach the network.

**Consequences.**
- WhatsApp is "implement the adapter and make the suite green".
- Error handling can branch on `permanent` / `retry_after` instead of parsing text.
- `web/services/telegram_service.py` no longer sends anything itself: it keeps the echo (through
  `get_staff_channel()`), the bot identity helpers and the Redis relay views.
- Adding a provider method means adding it to the contract and to the suite first.

---

## ADR-28: Patients Have Internal Ids; Channel Identities Live in `patient_channels`

**Date:** 2026-09-17 (Phase 7.2)

**Context.** `appointments.patient_id` was a Telegram user id, and a manual booking was a
negative millisecond stamp. Whether a patient could be messaged was decided in about a dozen
places from the id's sign or from `source = 'manual'`, both in Python and in the page's
JavaScript. A WhatsApp patient (7.4) would have no Telegram id to use at all.

**Decision.**

1. **Internal ids.** `patients` has an internal id. Every table that stores a patient id stores
   that id.
2. **Channel identities.** `patient_channels(channel, external_id)` is unique per identity and
   has one primary per patient. A view, `patient_contacts`, gives each patient's messaging
   contact.
3. **Reachability.** Whether a patient can be messaged is `messaging_contact(patient_id)`, and
   nothing else. It is a property of the patient, not of a booking. A therapist booking a
   Telegram patient by hand no longer makes them unreachable.
4. **Where rows come from.** The bot creates a patient on first contact (`for_channel`); the
   dashboard creates one without a channel. Attaching a booking to an existing patient requires
   an earlier appointment with the same therapist (SF-013).
5. **Telegram-keyed state stays as it is.** Conversation state that is Telegram's own — the
   relay, intake history and pre-6.3 follow-up keys — stays keyed by the Telegram user id until
   the channels are generalised in 7.4.
6. **The migration.**
   - It runs once, is recorded in `schema_migrations`, and is atomic, after a backup.
   - It refuses to start the process on failure.
   - `patients.legacy_id` keeps the old value for one release: pages redirect (308) after
     their tenant check, and API paths are rewritten before routing.

**Options rejected:**

- **A new `appointments.patient_ref` column beside the old one.** Two ids with overlapping
  meanings in every query, forever.
- **Keeping Telegram ids as patient ids and adding a "channel" column to appointments.** A
  patient on two channels would be two patients.
- **Treating a failed migration as a warning.** New bookings would get internal ids while old
  rows kept Telegram ids.

**Consequences:**

- WhatsApp needs a channel row, not a new id scheme.
- A patient's sessions stay grouped across channels.
- Every sender resolves a contact first.
- Telegram-shaped data (`patient_id == telegram id`) no longer works anywhere. Tests create
  patients through `make_patient(telegram_id=…)`, whose internal id deliberately differs from
  the Telegram id.
- The compatibility layer, and the `legacy_id` column, are to be removed one release later.

---

## ADR-29: One Booking Implementation, Reached Through a Versioned API with Idempotency Keys

**Date:** 2026-09-17 (Phase 7.3)

**Context.** Two code paths created appointments — the Telegram flow and the dashboard's manual
booking — and each had its own order of steps, its own patient handling and its own idea of what
"the hour is taken" means. WhatsApp (7.4) would have been a third. Nothing outside the clinic
could book at all, and a client that retried after a timeout had no way to avoid a double
booking.

**Decision.**

1. **One service.** `web/services/booking_service.py` creates and cancels every appointment:
   validate → resolve the patient → check availability → insert the row → update the calendar →
   queue the confirmation. The row claims the hour first; a calendar failure never loses a
   booking (B4).
2. **A versioned API over it.** `/api/v1` exposes booking, listing, cancelling and availability.
   Times are instants; responses also carry the clinic-local date and time the clinic thinks in.
3. **Two kinds of caller.** A machine client sends an API key and may book for any therapist; the
   dashboard keeps its session cookie and may only act for itself. The guard is a declared
   dependency, so the "every /api route authenticates" test can see it.
4. **API keys, not JWT.** A key is `zf_` + 32 random bytes, stored as a SHA-256 hash, revocable
   by name. JWT would add a dependency and a key-rotation story for no gain: these clients are
   servers the clinic runs, not third parties needing delegated, expiring access.
5. **Idempotency keys.** The first request under a key claims it; the answer — success or
   refusal — is stored and replayed. A different body under the same key is refused. Keys are
   per caller and kept for 24 hours.
6. **A published schema.** `docs/api/booking-v1.openapi.json` is committed and compared with what
   the app serves, so an API change is a deliberate export, not a side effect.
7. **A rate limit that fails open.** A one-minute window per caller in Redis; when Redis is
   unavailable the request is allowed.

**Options rejected:**

- **Letting each client keep its own booking code.** That is the bug: three orders of steps and
  three answers to "is this hour free?".
- **Making the bot call the API over HTTP in-process.** A network hop, a second auth path and a
  new failure mode for no benefit; the bot calls the service directly and the API is the seam if
  the processes are ever split.
- **Idempotency by hashing the request alone.** Two different people may legitimately ask for the
  same hour; the client's key is what says "this is my retry".
- **Failing closed when Redis is down.** A rate limiter is not worth refusing bookings over.

**Consequences.**

- Adding a channel means calling the service (or the API), not writing booking logic.
- Availability is enforced for API callers; a therapist booking their own calendar from the
  dashboard may still take any hour (7.3b passes `enforce_availability=False`).
- `message_log` now records confirmations, which needed its `kind` and `channel` CHECKs widened —
  migration `0002_message_log_kinds` rebuilds the table once.
- The exported schema has to be regenerated whenever a model or route changes.

**Addendum — the other two callers (7.3b).**
- The Telegram flow and the dashboard's manual booking now call the service, so `_release_hour`
  and `insert_manual` are gone. A test asserts `INSERT INTO appointments` exists in exactly one
  module, and that `save_appointment` has exactly one caller.
- **Availability** is enforced for the API and the Telegram flow, and not for the dashboard: a
  therapist booking their own calendar may take any hour, published or not.
- **Confirmations** stay off for both: the Telegram flow answers in the conversation, and a
  therapist booking by hand is with the patient. The option is one field away if the clinic wants
  it.
- **`calendar_note`** keeps the calendar's wording apart from the stored clinical summary, which
  the AI pipeline fills in later. Passing the calendar wording as the summary would have stopped
  the pipeline summarising at all — a test caught it.
- **Cancelling from the bot** goes through the service too, so the hour and the calendar event
  are handled the same way everywhere. Clearing the intake cache is now guarded: a Redis outage
  used to break the patient's cancellation (BOT_AUDIT B9's rule, applied where it was missing).

---

## ADR-30: WhatsApp Through Meta's Cloud API, Not Twilio (owner decision Q2)

**Date:** 2026-09-18 (Phase 7.4)

**Context.** Plan 7.4 asks for a WhatsApp channel and for the provider to be decided first. Two
realistic options: Meta's own WhatsApp Cloud API, or Twilio's WhatsApp API in front of it. Both
end at the same place — a WhatsApp Business Account, Meta's approval of every template, and the
same 24-hour rule — so the difference is the layer in between.

**The comparison.**

| | Meta Cloud API | Twilio |
|---|---|---|
| Who hosts | Meta, free of per-message platform fees; you pay Meta's conversation price | Twilio hosts and adds a per-message fee on top of Meta's |
| Onboarding | Meta app + Business verification + a phone number | Twilio account, plus the same Meta verification |
| Trying it out | a test number with a handful of allowed recipients | a shared sandbox number, joined by a keyword |
| API shape | one messages endpoint; JSON that mirrors WhatsApp's own concepts | Twilio's Messages API, with WhatsApp folded into its own model |
| Webhooks | signed with the app secret (HMAC-SHA256) | signed with the Twilio auth token (its own scheme) |
| Interactive messages | reply buttons and lists, first class | supported, with Twilio's own payload shape and some lag behind Meta's features |
| Lock-in | the provider is WhatsApp itself | a second vendor to leave later |
| Other channels | WhatsApp only | SMS and voice from the same account |

**Decision: Meta's Cloud API**, behind `ZF_CHANNEL_WHATSAPP`.

1. **One fewer intermediary** between a clinic and its patients' clinical messages, and one fewer
   contract, price list and status page to reason about.
2. **No per-message platform fee** on top of Meta's conversation charge, which matters for a
   single-clinic budget (Q3 is still open on hosting cost).
3. **The API mirrors WhatsApp's own model** — service window, templates, interactive messages —
   so the adapter's code reads like the rules it has to obey.
4. **Features land there first**, including the interactive messages the check-in needs.
5. **The cost of being wrong is small**: everything provider-specific is one module behind the
   `ChannelAdapter` contract, and the conformance suite is the acceptance test for a replacement.
   Moving to Twilio means writing `TwilioWhatsAppChannel` and making the same suite green.

**What would change the answer.** Twilio becomes the better choice if the clinic also wants SMS
or voice from one vendor, if Meta's Business verification proves impractical, or if someone wants
a single support contract more than they want the lower per-message cost. Because of (5), that
remains a contained change — the owner can still answer Q2 the other way.

**Consequences.**
- `WHATSAPP_*` settings name Meta's concepts (phone number id, app secret, verify token).
- Templates must be approved in the Meta console before the 24-hour check-in can reach anyone
  outside the service window; the template names belong in configuration (7.4b).
- The adapter is the only module that names graph.facebook.com (a test enforces it).
- `ZF_CHANNEL_WHATSAPP` stays off until the clinic has an account, so nothing above affects a
  running clinic today.

---

## ADR-31: The Audit Trail Is Append-Only in the Database, and Names an Actor from Context

**Date:** 2026-09-18 (Phase 8.1)

**Context.** Plan 8.1 asks for one table that answers "what happened to this patient's data,
when, and who did it", written on every clinical mutation. Two questions had to be settled: how
the trail resists being rewritten, and how a repository three calls below a request knows who is
acting.

**Decision.**

1. **Append-only in SQLite.** Two triggers raise on UPDATE and DELETE. The guarantee belongs to
   the database, not to a convention: an application that can rewrite its own trail has no trail.
2. **The actor comes from a context variable**, set where the identity is actually known — the
   request middleware (therapist, with IP and user agent), the booking API's guard (the API
   key's client), the bot (the patient), the pipeline (the AI), the worker (the job). The default
   is `system`, so an unnamed write is honest rather than wrong.
3. **Rows carry the diff, not the record.** `audit.changes()` drops `created_at`, `updated_at`
   and `id`, so a row shows the edit. Secret-looking fields are `<redacted>`, the payload passes
   through the log redactor, and it is capped.
4. **Recording is best effort.** A failed insert is logged and swallowed. Evidence is worth a
   great deal, but not the appointment it describes.
5. **The endpoint list is a test.** `tests/integration/test_audit_log.py` names the endpoints
   that change clinical data; adding one without recording fails the suite.

**Options rejected:**

- **A repository-level decorator on every write.** It would record mechanical writes (a cache
  stamp, a status poll) as clinical events and still not know the actor.
- **Passing the actor down as an argument.** Every service signature would grow one, and the bot
  and jobs would keep forgetting it.
- **Making the write transactional with the change.** A failed audit insert would then roll back
  a patient's booking; the trail must not be able to take the clinic down.
- **Storing before/after in full.** Larger rows, and clinical text duplicated into a table with a
  different retention story.

**Consequences.**

- The trail can be read per entity (8.5 will surface it on the session page) and per actor.
- Anything that needs to delete audit rows (a retention policy, a legal erasure request) has to
  drop the triggers deliberately — which is the point, and is left to plan 9.9 / Q5.
- Actions are named `entity.verb`, so new ones are self-describing.

---

## ADR-32: Every Model Call Goes Through One Meter, and the Prompt Is Stored as a Hash

**Status:** Accepted (Phase 8.2, 2026-09-18)

**Context.** Eight places called a model directly, each with its own `asyncio.wait_for` and its own
fallback. Nobody could say what the AI cost last week, which stage is slow, how often Ollama times
out, or which prompt produced a diagnosis a therapist is questioning. The obvious fix — log the
prompt and the answer — writes the most sensitive text in the system into a table with no
retention story.

**Decision.**

1. **One function calls models.** `web/services/ai_calls.ask(model, messages, stage=…,
   timeout_seconds=…)` applies the timeout, returns what the model returned and re-raises what it
   raised, so every caller keeps the fallback it already had. It writes one `ai_calls` row on
   success, on error and on timeout. A test walks the source tree and fails if `.ainvoke(` appears
   anywhere else.
2. **The prompt is identity, not content.** `prompt_sha256` / `response_sha256` answer "was this
   the same call?", "did this diagnosis come from that summary?" and "do these failures share an
   input?" without storing clinical text. The text itself is kept only with
   `ZF_AI_DEBUG_PROMPTS=1` **and** a dev or test environment; `forget_prompts()` removes those
   copies while the hashes remain.
3. **The appointment comes from context.** `ai_calls.for_appointment(id)` is set by the pipeline's
   `_exclusive()` block and by the treatment page's regeneration, so seven call signatures did not
   have to grow an argument that most callers do not have.
4. **Recording is best effort**, like the audit trail (ADR-31): a failed insert is logged and
   swallowed. A metric is worth less than the patient's answer.
5. **The table is the metrics source.** `summary(hours)` returns calls, failures, failure rate,
   p50/p95 and tokens overall and per stage — the AI half of 8.4's `/api/admin/metrics`.

**Options rejected:**

- **A LangChain callback handler.** Tied to one client library; the project is meant to keep an
  `AIProvider` seam for Bedrock and others (plan 12.1), and a callback cannot see the timeout the
  caller applied.
- **Logging alone.** Log lines answer "what happened just now", not "what did the AI cost this
  month" or "how often does stage 2 fail"; log files also rotate away and are shipped off-box.
- **Storing prompts in full with a retention job.** The retention job is the part that does not
  exist yet; until it does, the table would be an unbounded copy of clinical conversations.
- **`audit_log` instead of a second table.** Audit rows are clinical events with an actor; a model
  call is an operational measurement with tokens and latency. Mixing them would make both queries
  worse and put prompts inside an append-only table that nothing may prune.

**Consequences.**

- Cost and latency are queryable per stage, per model and per appointment, from the day a provider
  changes — which is what makes the `USE_AI=anthropic` switch measurable rather than a leap.
- A failed generation leaves evidence (`status`, `error`, `duration_ms`) even though the caller
  degraded gracefully and the patient saw a fallback question.
- Adding a model call means adding a stage name; the conformance test makes that the only way.

---

## ADR-33: The Message Log Is Metadata in Both Directions, and Records What the System Acted On

**Status:** Accepted (Phase 8.3, 2026-09-18)

**Context.** `message_log` was created in 6.6 for outbound deliveries and widened in 7.3 for
booking confirmations, but its `direction` column only ever held `out`, and the busiest
conversation in the product — the patient/therapist relay — left no trace at all. "Did the patient
ever get an answer?" could only be answered from Redis keys that expire, and a relay failure was
visible only in a log file.

**Decision.**

1. **Both directions, one table.** `direction='out'` is what the clinic sent; `direction='in'` is
   what the patient sent. A received message is recorded with `status='sent'` — it arrived, by
   definition — so the status column keeps one meaning: did this message reach the other side.
2. **Inbound means what the system acted on**: a relay message and a check-in answer. Intake
   answers are not logged — they belong to the intake record, and the clinic is not delivering
   anything. Nor are the check-in's own follow-up questions: the check-in is one delivery.
3. **Metadata only, never the text.** The relay history (Redis) and the check-in transcript hold
   what was said. A test asserts that a distinctive sentence never appears in the row.
4. **The relay's call sites pass a channel identity**, not a patient id, because that is what a bot
   has. `record_relay()` resolves it through `patient_channels` (ADR-28) and writes `patient_id`
   null for somebody who never booked, rather than dropping the row.
5. **Logging is best effort, after the send.** `record_relay()` swallows its own errors: a
   therapist holding the message must never be told the delivery failed because a row was not
   written. Every entry point to a reply — the therapist bot, the dashboard's messages page — logs
   the same way, so the trail does not depend on which door was used.
6. **The therapist's card collapses runs.** A four-question check-in is one line with a count, not
   four lines, because the card answers "did we hear back?" rather than "list every packet".

**Options rejected:**

- **Storing the message text.** It would duplicate clinical conversation into a table with a
  different retention story and a different audience, for no operational gain.
- **A separate `relay_log` table.** The questions are the same questions ("what did this patient
  receive, and when?"), and two tables would have to be merged in every answer.
- **Logging every inbound Telegram update.** Most are conversation steps that already have a
  home; the log would grow without making anything more answerable.
- **Making the log part of the send transaction.** A failed insert would then cost a message, or
  cause a retry and a duplicate — the opposite of what a delivery log is for.

**Consequences.**

- `kind` gained `relay`, which SQLite cannot add to a CHECK in place: `widen_kinds()` now takes a
  migration name and rebuilds once (`0003_message_log_relay`), the same shape as `0002`.
- The delivery card is no longer "Messages sent" but "Messages", and shows both directions.
- Relay rows have no `appointment_id` (a chat is not a session), so they serve operational
  queries without appearing on a session's card.

---

## ADR-34: Liveness, Readiness and Metrics Are Three Different Questions

**Status:** Accepted (Phase 8.4, 2026-09-18)

**Context.** The system had one public probe (`/healthz`, F11) and one authenticated status page
(`/api/status`) that mixed "can I serve traffic" with "is Ollama up" and was shaped for a dashboard
widget rather than for an operator or a deployment. Nothing exposed the numbers the earlier Phase 8
tables were written to produce — queue depth, follow-ups due, AI latency and failure rate, message
deliveries.

**Decision.**

1. **Three endpoints, three audiences.** `/healthz` (public, liveness, says nothing else);
   `/readyz` (readiness, 503 when the app cannot serve); `/api/admin/metrics` (session, the
   operator's numbers). `/api/status` stays as the dashboard's own widget feed.
2. **Readiness counts only required dependencies**: the database and a configuration that loads.
   Redis, Ollama, Telegram and Google are probed and reported, but never take the web app out of a
   load balancer's rotation — a check-in that cannot be sent is a follow-up failure, not a reason
   to stop serving a therapist their schedule.
3. **A stranger learns nothing from `/readyz`**: `{"ok": …}` only. A signed-in therapist also gets
   the per-check detail, redacted and trimmed. The probe is not a topology map (F11), but an
   operator debugging a deployment is exactly who needs the names.
4. **The metrics endpoint degrades rather than fails.** Every section is computed defensively: an
   unreadable section becomes `{}`, an unreachable dependency becomes `"ok": false`, Redis being
   down makes the relay count `known: false`. An endpoint that exists to report outages must not
   fail because of one.
5. **No new source of truth.** Every number is read from a table that already records it — `jobs`,
   `followups`, `ai_calls` (8.2), `message_log` (8.3) — so a metric cannot disagree with the data.
6. **Prometheus is a format, behind a flag**, written by hand (twelve gauge families) rather than
   pulled in as a client library: no dependency, no registry to keep in sync with the snapshot, and
   with `ZF_METRICS_PROMETHEUS=0` the format simply 404s like every other flagged surface.
7. **Tracing is a seam, not a dependency.** `ZF_TRACING=1` without the OpenTelemetry packages logs
   one warning and leaves the app untraced (`status() == "unavailable"`). The seam is written and
   tested now; installing the packages is the owner's call.

**Options rejected:**

- **Extending `/api/status`.** Its shape (`{label, detail}` per service) is a UI contract; a probe
  needs status codes and an operator needs numbers.
- **`prometheus_client`.** A new dependency, a global registry that would drift from the snapshot,
  and a second code path for the same values.
- **An unauthenticated `/metrics`.** Queue depth and failure rates describe a clinic's operations;
  until there is a network boundary to put them behind (Phase 12), a session is the boundary.
- **Failing readiness on Redis.** It would take the dashboard out of rotation for an outage it
  survives, turning a degraded feature into an outage.

**Consequences.**

- A deployment gets a real readiness gate, and `docs/METRICS.md` states what "ready" means.
- The AI and message tables now have a consumer, which is what makes them worth keeping.
- Phase 12 can turn on tracing and scraping with flags rather than code.
