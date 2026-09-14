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
