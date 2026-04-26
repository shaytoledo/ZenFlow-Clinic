# ZenFlow — Data Layer Living Reference

> **Living document.** Update this file whenever a new Redis key is added, a table column changes,
> a TTL is tuned, or a new breaking point is discovered.
>
> Detailed per-layer references: `docs/DATABASE.md` · `docs/REDIS.md`

---

## Layer Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 1 — In-process Python dicts  (bot process only, lost on restart) │
│  THERAPIST_MAP       dict[int, dict]   telegram_id → therapist          │
│  THERAPIST_BY_ID     dict[str, dict]   "t1"        → therapist          │
│  _history_cache      dict[str, obj]    intake key  → RedisChatHistory   │
│  _rolling_summaries  dict[str, str]    intake key  → compressed text    │
└───────────────────────────┬─────────────────────────────────────────────┘
                            │ rebuilt from ▼ on startup / bot activation
┌───────────────────────────▼─────────────────────────────────────────────┐
│  LAYER 2 — Redis  (shared: bot + web, survives restarts, capped at 1GB) │
│  Cache, relay routing, intake history, registration codes               │
└───────────────────────────┬─────────────────────────────────────────────┘
                            │ cache miss / write-through from ▼
┌───────────────────────────▼─────────────────────────────────────────────┐
│  LAYER 3 — SQLite  data/zenflow.db  (source of truth, never evicted)    │
│  therapists · appointments · intake_sessions · availability ·           │
│  treatment_notes                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

**Rule:** Redis is never the source of truth. Every Redis key can be evicted or expire and the
system will reconstruct the data from SQLite (or Google Calendar) on the next access.

---

## Complete Data Inventory

### What lives in SQLite (permanent)

| Table | Rows grow when | Rows shrink when | Max size concern |
|---|---|---|---|
| `therapists` | New therapist registers | Never | Negligible (< 100 rows ever) |
| `appointments` | Patient books | Never (soft delete only) | Grows forever — clinical record |
| `intake_sessions` | Patient completes intake | Never | 1:1 with appointments |
| `availability` | Therapist drags slot on calendar | Slot is booked or deleted | Small — active slots only |
| `treatment_notes` | Appointment saved (AI data) or session completed (therapist data) | Never | 1:1 with appointments |

### What lives in Redis (temporary)

| Key pattern | What it holds | TTL | Written by | Eviction trigger |
|---|---|---|---|---|
| `zenflow:intake:{patient_id}:{therapist_id}` | AI intake conversation history (LangChain list) | 1800 s | `ai_intake.py` | Explicit `clear_intake()` after booking, or TTL on abandon |
| `zenflow:relay:msg:{msg_id}` | `{patient_id, therapist_id}` routing for a forwarded message | 86400 s | `patient_bot/services/relay.py` | TTL only |
| `zenflow:relay:active:{patient_id}` | Active relay session presence | **None** | `patient_bot/services/relay.py` | Explicit `end_relay()` only — **no TTL** |
| `zenflow:relay:history:{patient_id}` | Full relay chat log for web messages page | 86400 s | `web/services/telegram_service.py` (`append_relay_message`) | TTL only |
| `zenflow:relay:lastseen:{patient_id}` | Therapist's last-seen timestamp for a conversation (drives unread badge) | 86400 s | `web/services/telegram_service.py` (`mark_conversation_read`) | TTL only — refreshed every read |
| `zenflow:followup:sent:{appointment_id}` | Idempotency lock — 24h follow-up was sent for this appointment | 7 d | `bot/services/followup_scheduler.py` | TTL only |
| `zenflow:followup:awaiting:{patient_id}` | Patient `pid` has an outstanding follow-up; next 1–5 reply is captured as the rating. Value = appointment_id | 24 h | `bot/services/followup_scheduler.py` (set on send, deleted on reply) | TTL only or explicit delete |
| `zenflow:slots:{date}` | Booked time slots for a date (`["09:00", "11:00"]`) | 300 s | `availability.py` | `book_slot()` / `restore_slot()` / TTL |
| `zenflow:avail:days:{tid}:{week}` | Available days list for a therapist + week | 600 s | `availability.py` | `book_slot()` pattern scan / TTL |
| `zenflow:avail:hours:{tid}:{date}` | Available hours for a therapist + date | 600 s | `availability.py` | `book_slot()` / TTL |
| `zenflow:apts:all` | Full appointments table dump (web dashboard) | 30 s | `web/services/appointment_service.py` | `save_appointment()` / `cancel_appointment()` / TTL |
| `zenflow:gcal:rolling14d:{tid}` | Rolling 14-day Google Calendar events blob — serves any sub-window FullCalendar requests inside [today, today+14d] without a fresh API call | 600 s | `web/services/cache_service.py prefetch_calendar`, `set_events_cached` | Login (warm) / slot create+delete (purge+rewarm) / logout / disconnect / TTL |
| `zenflow:gcal:events:{tid}:{start}:{end}` | Legacy per-range cache used only when the requested range is outside the rolling 14-day window | 600 s | `set_events_cached` fallback path | Slot create+delete / logout / TTL |
| `zenflow:reg:{CODE}` | Therapist registration activation code | 600 s | `web/routers/auth.py POST /register/signup` | Successful bot or web activation / TTL |

### What lives in the filesystem (semi-permanent)

| File | What it holds | Written by | Deleted by |
|---|---|---|---|
| `data/zenflow.db` | All structured data | `bot/db.py` | Never (backup before touching) |
| `data/google_tokens/{id}.json` | Per-therapist Google OAuth token (access + refresh) | `web/gcal.py` OAuth callback | `GET /auth/disconnect` |

---

## TTL and Eviction Logic

### Why each TTL is what it is

| Key | TTL | Reasoning |
|---|---|---|
| `zenflow:apts:all` | **30 s** | Dashboard reads this on every page load. Short TTL means stale data is shown for at most 30 s. Explicit invalidation on write ensures correctness for the writing request |
| `zenflow:slots:{date}` | **5 min** | Booked slots change infrequently. Explicit invalidation on booking means no stale double-booking risk. TTL is a safety net only |
| `zenflow:avail:days:{tid}:{week}` | **10 min** | Availability changes are therapist-driven (rare). 10 min stale window is acceptable; explicit invalidation on booking keeps it accurate |
| `zenflow:avail:hours:{tid}:{date}` | **10 min** | Same reasoning as days cache |
| `zenflow:gcal:rolling14d:{tid}` | **10 min** | Single blob covering the whole [today, today+14d] window. One Google Calendar fetch (300–800 ms) serves every FullCalendar sub-range request for the next 10 minutes. Pre-warmed at login → schedule page is instant on first open |
| `zenflow:gcal:events:{tid}:{...}` | **10 min** | Legacy per-range fallback for windows outside the rolling 14-day cache (e.g. browsing 3 weeks ahead) |
| `zenflow:intake:{pid}:{tid}` | **30 min** | Intake sessions last ~5 min. 30 min covers slow patients and network delays. Abandoned sessions auto-clean |
| `zenflow:relay:msg:{msg_id}` | **24 h** | Therapist may reply hours after receiving the forwarded message. 24 h covers any realistic reply window |
| `zenflow:relay:active:{pid}` | **None** | Must survive indefinitely until the session is explicitly ended. No TTL by design |
| `zenflow:relay:history:{pid}` | **24 h** | Chat history shown on the web messages page. 24 h covers a full clinic working day |
| `zenflow:relay:lastseen:{pid}` | **24 h** | Therapist last-seen timestamp; refreshed every time the conversation is opened or a reply is sent |
| `zenflow:followup:sent:{apt_id}` | **7 d** | Idempotency lock so the 24h follow-up scheduler never sends twice for one appointment |
| `zenflow:followup:awaiting:{pid}` | **24 h** | Patient has 24 h to reply with a 1–5 rating; after that the prompt expires silently and the start handler stops intercepting numbers |
| `zenflow:reg:{CODE}` | **10 min** | Activation codes must be short-lived for security. Therapist must complete bot activation promptly |

### Memory eviction (LRU)

```
maxmemory            1gb
maxmemory-policy     allkeys-lru
```

When Redis reaches 1 GB, it evicts the **least recently used key** — regardless of TTL.
This means a `zenflow:relay:active:{pid}` key (no TTL) can be evicted under memory pressure.

**Impact of LRU eviction per key type:**

| Key | Impact if evicted | Recovery |
|---|---|---|
| `zenflow:apts:all` | Cache miss — SQLite read on next request | Automatic, transparent |
| `zenflow:slots:*` | Cache miss — SQLite read on next booking check | Automatic |
| `zenflow:avail:*` | Cache miss — Google Calendar or SQLite read | Automatic |
| `zenflow:gcal:events:*` | Cache miss — Google API call (slow) | Automatic but adds latency |
| `zenflow:intake:*` | **Intake history lost mid-session** — patient sees fallback questions | Partial recovery: session continues with fallback questions; history lost |
| `zenflow:relay:msg:*` | Therapist reply-to routing fails — falls back to active session lookup | Graceful: routes to active patient if one exists |
| `zenflow:relay:active:*` | **Relay session orphaned** — patient cannot end chat cleanly | Manual: `redis-cli del zenflow:relay:active:{patient_id}` |
| `zenflow:relay:history:*` | Chat history missing from web messages page | TTL rebuild on next message |
| `zenflow:reg:*` | Therapist code expires early | Generate new code via `/api/my/activation-code` |

**In practice:** 1 GB is far more than ZenFlow will ever use at this scale. LRU eviction is a safety net, not an expected event.

---

## Known Breaking Points

### 1. Orphaned Relay Session (`zenflow:relay:active:{pid}` with no TTL)

**When it happens:**
- Bot process crashes mid-relay session
- Patient force-quits Telegram without pressing "End Chat"
- Redis key evicted under LRU memory pressure (rare)

**Symptom:** Patient sends a new message → bot says "You are already in a relay session" → patient cannot escape.

**Fix:**
```bash
redis-cli del "zenflow:relay:active:{patient_telegram_id}"
```

**Long-term fix (not yet implemented):** Add a 4-hour TTL on `zenflow:relay:active` as a safety net. The explicit `end_relay()` delete still happens normally; TTL only catches crashes.

---

### 2. `SQLITE_LOCKED` — Stale Open Transaction

**When it happens:**
- Bot process killed with SIGKILL (Ctrl+C during a write, `taskkill /F`)
- A thread's `execute()` raised an exception inside an implicit `BEGIN` block
- PyCharm Database plugin holds `.db-shm` open

**Symptom:**
```
sqlite3.OperationalError: database is locked
```

**Why `busy_timeout` doesn't help:** The lock is held by an **open transaction on the same connection** — not by another process. SQLite's `busy_timeout` only retries when the lock is from a different connection.

**Fix:**
```bash
# Kill all Python processes first
taskkill /F /IM python.exe   # Windows cmd

# Or find specific PIDs
tasklist | grep python       # Git Bash
taskkill //F //PID <pid>     # Git Bash syntax

# Verify no lock files remain
ls data/zenflow.db-journal   # should not exist
ls data/zenflow.db-shm       # WAL shm file — ok if it exists, not ok if held by dead process
```

**Prevention:** `isolation_level=None` (autocommit) eliminates all implicit `BEGIN` statements. Already implemented in `bot/db.py`. The only explicit `BEGIN` is in `save_appointment()` — if that fails, `ROLLBACK` is always called in the `except` block.

---

### 3. 409 Conflict — Duplicate Bot Instance

**When it happens:**
- `python startup/launch.py` run while a previous instance is still polling
- Previous process killed with `taskkill /F` (subprocess children not cleaned up)
- Uvicorn reload spawns a second bot process

**Symptom:**
```
telegram.error.Conflict: Conflict: terminated by other getUpdates request
```

**Fix:**
```bash
taskkill /F /IM python.exe   # Windows cmd — kills ALL Python processes
```
Then restart cleanly.

---

### 4. Intake History Lost Mid-Session (Redis eviction or restart)

**When it happens:**
- Redis flushed (`redis-cli flushdb`) during an active intake
- LRU eviction under memory pressure removes the intake key
- Redis restarted without persistence (`dump.rdb` disabled)

**Symptom:** Patient is mid-intake, next AI question is a fallback question (non-contextual), conversation loses coherence.

**Impact:** Clinical — the AI summary at the end will be lower quality. The appointment is still saved correctly.

**Recovery:** No automatic recovery. Patient experiences generic fallback questions for the rest of the session. Appointment saves correctly with whatever summary was generated.

**Prevention:** Redis `dump.rdb` (RDB snapshot) is enabled by default — this protects against Redis restart. LRU eviction is the real risk; keep `maxmemory=1gb` high enough to never be hit in practice.

---

### 5. Google Calendar Token Expired (No Refresh Token)

**When it happens:**
- `data/google_tokens/{id}.json` was created without `offline` access mode
- `access_type="offline"` missing from the OAuth URL → no `refresh_token` issued

**Symptom:**
```
google.auth.exceptions.RefreshError: Token has been expired or revoked.
```
Therapist's availability shows as empty. New availability slots cannot be fetched.

**Fix:** Therapist must disconnect and reconnect Google Calendar via `/settings`. The OAuth flow uses `access_type="offline"` which forces a new `refresh_token`.

---

### 6. `zenflow:avail:days` Shows Stale Data After Booking

**When it happens:**
- `book_slot()` does a pattern scan `SCAN zenflow:avail:days:{tid}:*` to invalidate all week offsets
- If Redis is under load, `SCAN` may return keys in batches and miss some on the first pass

**Symptom:** Patient books a slot, goes back to the week view, the booked day still appears available.

**Likelihood:** Very low. `SCAN` with a small keyspace (< 1000 keys) is effectively instant and returns all results in one batch.

**Fix:** TTL (10 min) self-heals. Or manually: `redis-cli del "zenflow:avail:days:t1:0" "zenflow:avail:days:t1:1"`

---

### 7. `database is locked` from PyCharm Database Plugin

**When it happens:**
- PyCharm's Database tool is connected to `data/zenflow.db` while the app is running
- PyCharm holds `.db-shm` open, blocking all writes

**Symptom:**
```
sqlite3.OperationalError: database is locked
```
On treatment notes save or appointment write.

**Fix:** In PyCharm → Database tool window → right-click connection → Disconnect. Or close PyCharm entirely.

---

## Data Flow: Full Booking Lifecycle

Shows exactly what gets written where at each step:

```
1. Patient selects therapist
   └─ READS:  THERAPIST_BY_ID (in-process dict)

2. Patient selects week / day
   └─ READS:  zenflow:avail:days:{tid}:{week}  (Redis, 10 min TTL)
              └─ MISS → SQLite availability table or Google Calendar API
                        └─ WRITES: zenflow:avail:days:{tid}:{week}

3. Patient selects hour
   └─ READS:  zenflow:avail:hours:{tid}:{date}  (Redis, 10 min TTL)
              └─ MISS → SQLite availability + zenflow:slots:{date}
                        └─ WRITES: zenflow:avail:hours:{tid}:{date}
                                   zenflow:slots:{date}  (if also missed)

4. Patient completes AI intake (5 questions)
   └─ READS/WRITES: zenflow:intake:{pid}:{tid}  (Redis, 30 min TTL)
                    _history_cache[key]          (in-process dict)

5. Intake complete → appointment saved
   └─ WRITES: SQLite appointments (INSERT, explicit transaction)
              SQLite intake_sessions (INSERT, same transaction)
              SQLite treatment_notes (UPSERT, AI TCM data)
   └─ DELETES: zenflow:apts:all           (explicit invalidation)
               zenflow:slots:{date}        (explicit invalidation)
               zenflow:avail:hours:{tid}:{date}  (explicit)
               zenflow:avail:days:{tid}:*  (pattern scan)
               zenflow:intake:{pid}:{tid}  (clear_intake())

6. Slot removed from availability
   GOOGLE mode: Google Calendar event deleted
   LOCAL mode:  SQLite availability row split/deleted
```

---

## Data Flow: Cancellation Lifecycle

```
1. Patient cancels appointment
   └─ READS:  SQLite appointments WHERE patient_id=? AND status='active'

2. Confirmation → cancel
   └─ WRITES: SQLite appointments SET status='cancelled'  (soft delete)
              SQLite availability INSERT 1-hour row  (restore slot, local mode)
              Google Calendar event deleted           (Google mode)
   └─ DELETES: zenflow:apts:all      (explicit)
               zenflow:slots:{date}   (explicit)
              (avail:days + avail:hours left to expire via TTL)
```

---

## Data Flow: Relay Lifecycle

```
1. Patient starts relay
   └─ WRITES: zenflow:relay:active:{pid}  (no TTL — must be explicitly deleted)

2. Patient sends message
   └─ Bot(THERAPIST_BOT_TOKEN).forward_message() → msg_id
   └─ WRITES: zenflow:relay:msg:{msg_id}      (24h TTL)
              zenflow:relay:history:{pid}      (30 min TTL, append)

3. Therapist replies-to forwarded message
   └─ READS:  zenflow:relay:msg:{msg_id}  → {patient_id, therapist_id}
              (security check: reply therapist_id must match stored therapist_id)
   └─ Bot(TELEGRAM_TOKEN).send_message(patient_id, ...)
   └─ WRITES: zenflow:relay:history:{pid}  (append)

4. Either side ends chat
   └─ DELETES: zenflow:relay:active:{pid}  (explicit delete)
   (relay:msg keys expire via TTL; relay:history expires via TTL)
```

---

## Operational Runbook

### Inspect current state

```bash
# All ZenFlow keys in Redis
redis-cli keys "zenflow:*"

# Check a specific appointment cache
redis-cli get "zenflow:apts:all" | python -m json.tool

# Check active relay sessions
redis-cli keys "zenflow:relay:active:*"

# Check intake sessions in progress
redis-cli keys "zenflow:intake:*"

# Check pending registration codes
redis-cli keys "zenflow:reg:*"
redis-cli ttl "zenflow:reg:ABCD1234"

# Redis memory usage
redis-cli info memory | grep used_memory_human
```

### Safe cache flush (development only)

```bash
# Clear only cache keys (preserves relay + intake + registration)
redis-cli del "zenflow:apts:all"
redis-cli del "zenflow:slots:2026-03-10"
redis-cli del "zenflow:avail:days:t1:0" "zenflow:avail:days:t1:1"
redis-cli del "zenflow:avail:hours:t1:2026-03-10"

# Nuclear option — wipe everything (NEVER in production with active sessions)
redis-cli --eval - <<'EOF'
local keys = redis.call('keys', 'zenflow:*')
for i, k in ipairs(keys) do redis.call('del', k) end
return #keys
EOF
```

### Fix orphaned relay session

```bash
redis-cli del "zenflow:relay:active:918187404"
# Replace 918187404 with the patient's Telegram user ID
```

### SQLite inspection

```bash
sqlite3 data/zenflow.db ".tables"
sqlite3 data/zenflow.db "SELECT id, patient_name, therapist_id, date, time, status FROM appointments ORDER BY date DESC LIMIT 10;"
sqlite3 data/zenflow.db "SELECT id, therapist_id, start_dt, end_dt FROM availability ORDER BY start_dt;"
sqlite3 data/zenflow.db "SELECT id, name, telegram_id, active FROM therapists;"
sqlite3 data/zenflow.db "SELECT appointment_id, tcm_pattern, completed_at FROM treatment_notes ORDER BY updated_at DESC LIMIT 5;"
```

### Backup

```bash
cp data/zenflow.db data/zenflow.db.backup.$(date +%Y%m%d)
```

---

## Adding New Data (Checklist)

When adding a new Redis key:
- [ ] Define the key pattern and document it in this file AND `docs/REDIS.md`
- [ ] Set an appropriate TTL — no key should be `None` unless it represents an active session that must be explicitly ended
- [ ] Define explicit invalidation triggers — TTL alone is not enough for correctness-critical data
- [ ] Document the LRU impact in the "Known Breaking Points" section if eviction would cause data loss

When adding a new SQLite column:
- [ ] Add an `ALTER TABLE` migration to `_migrations` in `bot/db.py`
- [ ] Update the schema in `docs/DATABASE.md` AND this file
- [ ] Update the UPSERT / INSERT query in the relevant service file
- [ ] If the column stores JSON, document the exact format here
