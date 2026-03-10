# ZenFlow — Memory Management

> This document covers every memory layer in the system: what data lives where,
> when it enters, when it leaves, and what happens when it expires or is invalidated.

---

## Memory Layers Overview

ZenFlow uses four distinct memory layers, each with a different scope, lifetime, and purpose:

```
┌──────────────────────────────────────────────────────────────────────┐
│  Layer 1: Python in-process memory                                    │
│  Scope: single process, lost on restart                               │
│  Contains: LLM singleton, history cache dicts, therapist registry,   │
│            PTB user_data, Redis client handles                        │
├──────────────────────────────────────────────────────────────────────┤
│  Layer 2: Redis (external, shared)                                    │
│  Scope: shared between bot + web processes, survives bot restart      │
│  Contains: intake history, relay routing, availability cache,         │
│            appointment cache, registration codes                      │
├──────────────────────────────────────────────────────────────────────┤
│  Layer 3: SQLite (persistent file)                                    │
│  Scope: permanent until explicitly deleted                            │
│  Contains: therapists, appointments, intake_sessions,                 │
│            availability slots, treatment_notes                        │
├──────────────────────────────────────────────────────────────────────┤
│  Layer 4: Signed session cookie (web browser)                         │
│  Scope: per-browser, 30-day max-age                                  │
│  Contains: therapist_id (authenticates dashboard access)             │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Layer 1 — Python In-Process Memory

### 1.1 LLM Singleton (`bot/patient_bot/services/ai_intake.py`)

```python
_LLM = ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_HOST)
```

| Property | Value |
|---|---|
| Type | `langchain_ollama.ChatOllama` object |
| Created | At module import time (once per process) |
| Destroyed | When the bot process exits |
| Shared | Single instance used for ALL patients |
| Thread-safety | `ainvoke()` is async — concurrent calls are safe |

**Why a singleton:** Creating a new `ChatOllama` per call adds HTTP connection overhead. One instance reuses the underlying HTTP client pool.

**Health check:** `_check_ollama_health()` runs immediately after the singleton is created. It attempts `GET /api/tags` with a 3-second timeout and logs a clear warning if Ollama is unreachable. The singleton is still created even if Ollama is down — all `ainvoke()` calls will fall back to static questions.

---

### 1.2 Intake History Cache (`ai_intake.py`)

```python
_history_cache: dict[int, RedisChatMessageHistory] = {}
```

| Property | Value |
|---|---|
| Key | `patient_id` (Telegram user ID, `int`) |
| Value | `RedisChatMessageHistory` object (a wrapper around a Redis list) |
| Created | `_get_history(user_id)` on first access per user |
| Destroyed | `clear_intake(user_id)` → `_history_cache.pop(user_id, None)` |
| Also cleared | `initialize_intake(user_id)` pops existing entry before creating fresh one |

**Purpose:** Avoids issuing a Redis `LRANGE` command on every LLM call. The `RedisChatMessageHistory` object holds the Redis key reference and a local cache of retrieved messages.

**Lifecycle:**
```
start_intake()          → initialize_intake()     → pop old + create new entry
handle_intake_answer()  → get_next_question()     → reads _history_cache[user_id]
                         → generate_summary()      → reads _history_cache[user_id]
after save_appointment()→ clear_intake()           → pops _history_cache[user_id]
```

---

### 1.3 Rolling Summary Cache (`ai_intake.py`)

```python
_rolling_summaries: dict[int, str] = {}
```

| Property | Value |
|---|---|
| Key | `patient_id` (int) |
| Value | Plain string — compressed summary of older conversation turns |
| Created | `_maybe_compress()` when history length exceeds `_BUFFER_MAX = 6` messages |
| Destroyed | `clear_intake(user_id)` and `initialize_intake(user_id)` |

**Purpose:** Implements a sliding-window context for the LLM. When a patient sends more than 6 messages, the oldest turns are compressed into a 2–3 sentence summary. This prevents the LLM context from growing unbounded.

**Compression trigger:** `_maybe_compress()` is called inside `get_next_question()` before every LLM call.

```
messages ≤ 6  → no compression, full history sent to LLM
messages > 6  → oldest (N - BUFFER_KEEP=4) messages sent to LLM for summarisation
              → summary stored in _rolling_summaries[user_id]
              → Redis history trimmed to most recent 4 messages
              → next LLM call receives: system prompt + summary + last 4 messages
```

---

### 1.4 PTB `context.user_data` (booking state)

```python
context.user_data["selected_therapist"]  # str  — e.g. "t1"
context.user_data["selected_day"]        # str  — ISO date "YYYY-MM-DD"
context.user_data["selected_time"]       # str  — "HH:MM"
context.user_data["intake_count"]        # int  — 0–5
context.user_data["selected_week"]       # int  — 0 (this) or 1 (next)
context.user_data["therapist_flow"]      # str  — "schedule" | "contact" | "welcome"
```

| Property | Value |
|---|---|
| Scope | Per Telegram user, per bot application instance |
| Created | Set by individual handlers during the booking flow |
| Cleared | `context.user_data.clear()` after appointment saved or cancelled |
| Persisted | NOT persisted — lost on bot restart (no `PicklePersistence` yet) |
| Thread-safety | PTB guarantees one handler at a time per user |

**Lifecycle:**

```
show_therapist_choice()    → sets therapist_flow = "schedule"
select_therapist_and_continue() → sets selected_therapist = "t1"
show_days()                → sets selected_week = 0/1
show_hours()               → sets selected_day = "2026-03-20"
confirm_appointment()      → sets selected_time = "11:00"
start_intake()             → sets intake_count = 0
handle_intake_answer()     → increments intake_count each call
skip_intake() / final answer → context.user_data.clear()
                             → re-sets selected_therapist (preserved for next booking)
```

---

### 1.5 Therapist Registry (module-level, `bot/config.py`)

```python
THERAPISTS:      list[dict]       # all active therapists
THERAPIST_MAP:   dict[int, dict]  # by telegram_id → therapist dict
THERAPIST_BY_ID: dict[str, dict]  # by id string ("t1") → therapist dict
```

| Property | Value |
|---|---|
| Created | At `bot/config.py` import time — `SELECT * FROM therapists` |
| Updated | `_register_therapist_to_db()` in `therapist_bot/handlers.py` mutates all three dicts in-place on successful bot activation |
| Destroyed | Never (process lifetime) |
| Cross-process | NOT shared — web process has its own copy via `_load_therapists_fresh()` |

**Why in-process:** Handlers check `THERAPIST_MAP` on every incoming message from the therapist bot. A SQLite query on every message would be wasteful; these maps are small (< 20 entries) and very rarely change.

---

### 1.6 Redis Client Singletons (`bot/redis_client.py`)

```python
_async_client: aioredis.Redis | None  # lazy-created on first get_async_redis() call
_sync_client:  syncredis.Redis | None # lazy-created on first get_sync_redis() call
```

| Property | Value |
|---|---|
| Created | First call to `get_async_redis()` / `get_sync_redis()` |
| Config set | `get_sync_redis()` applies `maxmemory=1gb, allkeys-lru` on creation |
| Reused | Same client object for all calls throughout process lifetime |
| Destroyed | When process exits |

---

### 1.7 SQLite Connection Pool (`bot/db.py`)

```python
_local = threading.local()   # thread-local connection storage
```

| Property | Value |
|---|---|
| Per-thread | One `sqlite3.Connection` per OS thread |
| Created | `get_db()` on first call in a given thread |
| `isolation_level` | `None` (autocommit) — no implicit `BEGIN`, no stale transactions |
| Never closed | Connection lives for the thread's lifetime |

**Why autocommit (`isolation_level=None`):** Python's default `isolation_level=""` auto-issues `BEGIN` before every `INSERT/UPDATE`. When a write fails mid-exception, the connection is left with an open transaction. When the asyncio thread pool reuses that thread, the next `execute()` gets `SQLITE_LOCKED` **immediately** (bypassing `busy_timeout`). Autocommit eliminates all implicit transactions entirely.

**Explicit transactions for multi-statement atomicity:**
```python
# save_appointment() — two INSERTs must be atomic
conn.execute("BEGIN")
try:
    conn.execute("INSERT INTO appointments ...", ...)
    conn.execute("INSERT INTO intake_sessions ...", ...)
    conn.execute("COMMIT")
except:
    conn.execute("ROLLBACK")
    raise
```

---

## Layer 2 — Redis

See `docs/REDIS.md` for the complete key schema and TTL table.

### Lifecycle Summary

```
Key created                         Key destroyed
──────────────────────────────────────────────────────────────────────
zenflow:intake:{pid}:{tid}          clear_intake() OR 30-min TTL expiry
zenflow:relay:msg:{msg_id}          24h TTL (auto) — no explicit delete
zenflow:relay:active:{pid}          end_relay() explicit delete
zenflow:relay:history:{pid}         30-min TTL (auto)
zenflow:slots:{date}                book_slot() OR restore_slot() explicit delete
                                    OR 5-min TTL expiry
zenflow:avail:days:{tid}:{week}     book_slot() pattern-scan delete
                                    OR 5-min TTL expiry
zenflow:avail:hours:{tid}:{date}    book_slot() explicit delete
                                    OR 5-min TTL expiry
zenflow:apts:all                    save_appointment() OR cancel_appointment() delete
                                    OR 30s TTL expiry
zenflow:gcal:events:{tid}:{s}:{e}   auth_disconnect / logout explicit purge
                                    OR 10-min TTL expiry
zenflow:reg:{CODE}                  bot activation explicit delete
                                    OR 10-min TTL expiry
```

### Eviction Policy

Redis is configured with `maxmemory=1gb, allkeys-lru`. If total memory exceeds 1 GB, Redis evicts the **least recently used** key regardless of whether it has a TTL. All cached data is reconstructible from SQLite — no data loss on eviction.

### Cache Miss Behaviour

Every Redis read is wrapped in a try/except. If Redis is unavailable:
- **Availability queries:** fall through to Google Calendar or SQLite
- **Booked slots:** fall through to SQLite query
- **Appointment list:** fall through to SQLite query
- **Relay routing:** message cannot be routed → logged as error (relay session fails gracefully)

---

## Layer 3 — SQLite

See `docs/DATABASE.md` for the complete schema.

### Write Points

| Table | Written by | Trigger |
|---|---|---|
| `therapists` | `init_db()` | On startup (INSERT OR IGNORE from JSON) |
| `therapists` | `_register_therapist_to_db()` | Bot activation code sent |
| `therapists` | `_register_web_therapist()` | Web registration form |
| `appointments` | `save_appointment()` | After intake or skip-intake |
| `intake_sessions` | `save_appointment()` | Same transaction as appointment |
| `availability` | Web `POST /api/availability` | Therapist drags on FullCalendar |
| `availability` | `_remove_hour_from_local()` | Patient books (local mode) |
| `availability` | `_add_hour_to_local()` | Patient cancels (local mode) |
| `treatment_notes` | `save_treatment_notes()` | AI diagnosis after intake, OR therapist saves treatment page |

### Read Points

| Table | Read by | Trigger |
|---|---|---|
| `therapists` | `config.py` | Bot startup |
| `therapists` | `web/app.py` | Every auth-gated web request |
| `appointments` | `get_patient_appointments()` | Patient cancellation flow |
| `appointments` | `get_booked_slots()` | Availability check (cache miss) |
| `appointments` | `web/app.py` | Dashboard, patients list, sessions page |
| `intake_sessions` | `web/app.py` | Messages page |
| `availability` | `_read_local_avail()` | Bot availability query (local mode) |
| `availability` | `web/app.py` | FullCalendar events API |
| `treatment_notes` | `get_treatment_notes()` | Treatment page load |

### Data is Never Hard-Deleted From These Tables

- `appointments`: set `status='cancelled'`, row preserved forever
- `intake_sessions`: never deleted
- `treatment_notes`: updated in-place via UPSERT
- `availability`: rows deleted and re-inserted on booking/cancellation (slot management, not clinical data)
- `therapists`: never deleted from the app (manual SQL only)

---

## Layer 4 — Session Cookie

```
Cookie name:    zf_session
Signing:        HMAC-SHA256 via itsdangerous (SESSION_SECRET from .env)
Max-age:        30 days
Content:        {"therapist_id": "t1"}
Transport:      HTTPS (or HTTP for localhost dev)
```

### Lifecycle

| Event | Action |
|---|---|
| Successful `POST /register/signin` | `request.session["therapist_id"] = therapist["id"]` |
| Successful `POST /register/signup` | Same, after inserting therapist row |
| Google OAuth callback | Same, after matching google_id or inserting new row |
| `GET /logout` | `request.session.clear()` |
| Cookie expires (30 days) | Browser discards cookie; next request redirects to `/register` |
| Invalid/tampered cookie | `itsdangerous` raises `BadSignature`; session returns empty dict; redirect to `/register` |

### How Dashboard Routes Check Authentication

```python
def _get_session_therapist_id(request: Request) -> str | None:
    return request.session.get("therapist_id")

# In every dashboard route:
therapist_id = _get_session_therapist_id(request)
if not therapist_id:
    return RedirectResponse("/register")
```

---

## Full Data Flow: Patient Books an Appointment

```
1. Patient taps "📅 Schedule"
   └─ context.user_data["therapist_flow"] = "schedule"

2. Patient selects therapist (or auto-skipped if 1)
   └─ context.user_data["selected_therapist"] = "t1"

3. Patient selects week
   └─ context.user_data["selected_week"] = 0

4. Bot calls get_available_days()
   └─ Redis GET zenflow:avail:days:t1:0
      HIT  → return cached list
      MISS → query Google Calendar or SQLite availability
           → Redis SET zenflow:avail:days:t1:0 (TTL 5 min)

5. Patient selects day
   └─ context.user_data["selected_day"] = "2026-03-20"

6. Bot calls get_available_hours()
   └─ Redis GET zenflow:avail:hours:t1:2026-03-20
      HIT  → return cached list
      MISS → query calendar, subtract get_booked_slots()
           → Redis SET zenflow:avail:hours:t1:2026-03-20 (TTL 5 min)

7. Patient selects time
   └─ context.user_data["selected_time"] = "11:00"

8. Patient confirms intake → start_intake()
   └─ initialize_intake(user_id)
      → _history_cache.pop(user_id)           [clears old in-process cache]
      → _rolling_summaries.pop(user_id)
      → RedisChatMessageHistory.clear()       [clears old Redis key]
      → RedisChatMessageHistory.add_ai_message(OPENING_QUESTION)
      └─ Redis: RPUSH zenflow:intake:{uid}:{tid} [first message]
              + EXPIRE 1800

9. Patient answers 5 questions
   Each answer → get_next_question(user_id, answer)
   └─ _history_cache[user_id].add_user_message(answer)
      → Redis RPUSH + EXPIRE refresh
   └─ _maybe_compress() if messages > 6
      → LLM call to compress (async, 30s timeout)
      → _rolling_summaries[user_id] = compressed text
      → Redis LTRIM to last 4 messages
   └─ _LLM.ainvoke(context_messages) [100s timeout]
      → Redis RPUSH AI question

10. After 5th answer → generate_summary()
    └─ _LLM.ainvoke() [100s timeout] → clinical summary string

11. book_slot(day, time_slot, ...)
    └─ Redis DEL zenflow:slots:2026-03-20
    └─ Redis DEL zenflow:avail:days:t1:* (all week offsets)
    └─ Redis DEL zenflow:avail:hours:t1:2026-03-20
    └─ Google Calendar: remove hour from availability event
    └─ Google Calendar: create appointment event → gcal_event_id

12. save_appointment(...)
    └─ SQLite BEGIN
       INSERT INTO appointments ...
       INSERT INTO intake_sessions (history_json = get_history_dicts())
       COMMIT
    └─ Redis DEL zenflow:apts:all   [invalidate appointment list cache]
    └─ Returns appointment_id (int)

13. generate_tcm_diagnosis()
    └─ _LLM.ainvoke() [100s timeout] → TCM JSON

14. save_treatment_notes(appointment_id, ...)
    └─ SQLite UPSERT treatment_notes ON CONFLICT DO UPDATE

15. clear_intake(user_id)
    └─ RedisChatMessageHistory.clear()   [DEL zenflow:intake:{uid}:{tid}]
    └─ _history_cache.pop(user_id)
    └─ _rolling_summaries.pop(user_id)

16. context.user_data.clear()
    └─ selected_day, selected_time, intake_count, selected_week cleared
    └─ selected_therapist re-set for next booking
```

---

## Full Data Flow: Patient Cancels an Appointment

```
1. show_appointments(patient_id)
   └─ SQLite: SELECT * FROM appointments WHERE patient_id=? AND status='active'

2. Patient confirms cancel
   └─ cancel_appointment(apt["id"])
      → SQLite: UPDATE appointments SET status='cancelled' WHERE id=?
      → Redis DEL zenflow:apts:all

3. restore_slot(day, time_slot, gcal_apt_event_id, therapist_id)
   └─ Redis DEL zenflow:slots:{date}
   └─ If gcal_event_id starts with "local_":
      → _add_hour_to_local() → SQLite INSERT INTO availability
   └─ Else (Google Calendar):
      → service.events().delete(calendarId="primary", eventId=gcal_event_id)
      → service.events().insert(calendarId=avail_cal_id, ...) ["✅ Available"]
```

---

## Full Data Flow: AI Intake (Ollama unavailable)

When Ollama is down or times out, every LLM call falls through to a static fallback:

```
get_next_question(user_id, answer)
   └─ _history_cache[user_id].add_user_message(answer)
   └─ _LLM.ainvoke() raises ConnectionError or TimeoutError
   └─ except block:
      answered = count HumanMessages in history
      fallback = FALLBACK_QUESTIONS[min(answered, 4)]
      hist.add_ai_message(fallback)
      logger.info(f"fallback question {answered+1} sent")
      return fallback

generate_summary() → "Intake completed — see conversation history for details."
generate_tcm_diagnosis() → empty fallback dict (all fields blank, certainty=0)
```

Appointment is still saved correctly. The `summary` field contains the fallback text. Treatment notes row is created with blank TCM fields.

---

## Memory Leak Prevention

| Risk | Mitigation |
|---|---|
| `_history_cache` grows unbounded | `clear_intake()` is called in EVERY completion path (intake complete, skip, cancel) |
| `_rolling_summaries` grows unbounded | Same — cleared in every `clear_intake()` call |
| Redis `zenflow:intake:*` keys accumulate | 30-min TTL auto-expires abandoned sessions |
| Redis relay keys accumulate | Relay keys have explicit TTLs (24h / 30min) |
| SQLite thread-local connections never closed | Acceptable for long-running servers; threads in asyncio pool are stable |
| PTB `user_data` grows | `context.user_data.clear()` called on all terminal states |
