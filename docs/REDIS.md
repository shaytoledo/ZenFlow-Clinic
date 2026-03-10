# ZenFlow — Redis Reference

> Redis serves as the **cache and messaging layer** — it is never the source of truth.
> All cached data can be reconstructed from SQLite or Google Calendar on a cache miss.

---

## Configuration

Set on first `get_sync_redis()` call (in `bot/redis_client.py`):

```
maxmemory            1gb
maxmemory-policy     allkeys-lru
```

When memory exceeds 1 GB, Redis evicts the least recently used key — regardless of whether it has a TTL. Because all data is reconstructible, this is safe.

**Connection singletons:**
- `get_async_redis()` → `aioredis.Redis` (for FastAPI + async bot handlers)
- `get_sync_redis()` → `syncredis.Redis` (for LangChain + sync helpers)

Both connect to `REDIS_URL` from `.env` (default `redis://localhost:6379/0`).

---

## Complete Key Schema

### AI Intake History

| Key pattern | `zenflow:intake:{patient_id}:{therapist_id}` |
|---|---|
| **Type** | List (managed by `langchain-redis` `RedisChatMessageHistory`) |
| **TTL** | 1800 seconds (30 minutes) |
| **Format** | LangChain serialised message objects (JSON strings in a Redis List) |
| **Written by** | `ai_intake.initialize_intake()` — clears + writes opening question |
| | `ai_intake.get_next_question()` — appends user answer + AI question |
| | `ai_intake.generate_summary()` — appends final user answer |
| | `ai_intake._maybe_compress()` — LTRIM to last 4 messages after summarising |
| **Read by** | `_get_history(user_id)` — called before every LLM invocation |
| **Deleted by** | `ai_intake.clear_intake()` — explicitly after appointment saved |
| **Auto-expires** | 30 min after last write — handles abandoned sessions |
| **Notes** | In-process `_history_cache` dict caches the `RedisChatMessageHistory` object to avoid LRANGE on every call |

---

### Relay — Message Routing

| Key pattern | `zenflow:relay:msg:{msg_id}` |
|---|---|
| **Type** | String (JSON) |
| **TTL** | 86400 seconds (24 hours) |
| **Format** | `{"patient_id": 918187404, "therapist_id": "t1"}` |
| **Written by** | `patient_bot/services/relay.py` `save_relay_mapping()` — when patient message is forwarded to therapist bot |
| **Read by** | `therapist_bot/services/relay.py` `get_patient_for_msg()` — when therapist replies to a forwarded message |
| **Deleted by** | TTL only (24h) |
| **Notes** | Key = Telegram message ID in the **therapist bot**. The therapist must reply-to the forwarded message for precise routing |

---

### Relay — Active Session Presence

| Key pattern | `zenflow:relay:active:{patient_id}` |
|---|---|
| **Type** | String (JSON) |
| **TTL** | None (no expiry) |
| **Format** | `{"patient_id": 918187404, "therapist_id": "t1", "started_at": "..."}` |
| **Written by** | `patient_bot/services/relay.py` `start_relay()` — when patient initiates chat |
| **Read by** | `patient_bot/therapist.py` — to check if patient is already in a relay session |
| **Deleted by** | `patient_bot/services/relay.py` `end_relay()` — explicit delete when either side ends the chat |
| **Notes** | No TTL means orphaned sessions (from crashes) persist indefinitely. Manual `redis-cli del` needed in that case |

---

### Relay — Chat History

| Key pattern | `zenflow:relay:history:{patient_id}` |
|---|---|
| **Type** | String (JSON) |
| **TTL** | 1800 seconds (30 minutes) |
| **Format** | JSON array: `[{"role": "patient"|"therapist", "text": "...", "ts": "ISO datetime"}]` |
| **Written by** | Relay service `append_relay_message()` |
| **Read by** | `web/app.py` messages page |
| **Deleted by** | TTL only |

---

### Booked Slots Cache

| Key pattern | `zenflow:slots:{date}` |
|---|---|
| **Type** | String (JSON) |
| **TTL** | 300 seconds (5 minutes) |
| **Format** | JSON array of `"HH:MM"` strings: `["09:00", "11:00"]` |
| **Written by** | `availability.get_booked_slots()` — after reading from SQLite on cache miss |
| **Read by** | `availability.get_booked_slots()` — called during available-hours calculation |
| **Deleted (invalidated) by** | `availability.book_slot()` — immediately when a slot is booked |
| | `availability.restore_slot()` — immediately when an appointment is cancelled |
| | TTL (5 min) |
| **Notes** | Used to subtract booked hours from the availability window |

---

### Available Days Cache

| Key pattern | `zenflow:avail:days:{therapist_id}:{week_offset}` |
|---|---|
| **Type** | String (JSON) |
| **TTL** | 600 seconds (10 minutes) |
| **Format** | JSON array of `"YYYY-MM-DD"` strings: `["2026-03-10", "2026-03-12"]` |
| **Written by** | `availability.get_available_days()` — after querying Google Calendar or SQLite |
| **Read by** | `availability.get_available_days()` — first thing on every call |
| **Deleted (invalidated) by** | `availability.book_slot()` — **pattern scan**: `zenflow:avail:days:{tid}:*` (all week offsets for this therapist) |
| | TTL (10 min) |
| **Notes** | `week_offset` is 0 (this week) or 1 (next week). Pattern scan ensures both weeks are invalidated when a slot is booked |

---

### Available Hours Cache

| Key pattern | `zenflow:avail:hours:{therapist_id}:{date}` |
|---|---|
| **Type** | String (JSON) |
| **TTL** | 600 seconds (10 minutes) |
| **Format** | JSON array of `"HH:MM"` strings: `["09:00", "10:00", "14:00"]` |
| **Written by** | `availability.get_available_hours()` — after querying calendar/SQLite and subtracting booked slots |
| **Read by** | `availability.get_available_hours()` — first thing on every call |
| **Deleted (invalidated) by** | `availability.book_slot()` — `DEL zenflow:avail:hours:{tid}:{date}` |
| | TTL (10 min) |

---

### All-Appointments Cache

| Key pattern | `zenflow:apts:all` |
|---|---|
| **Type** | String (JSON) |
| **TTL** | 30 seconds |
| **Format** | JSON array of all appointment row dicts |
| **Written by** | `web/app.py` `_load_all_appointments()` — on cache miss |
| **Read by** | `web/app.py` dashboard, patients page, sessions page |
| **Deleted (invalidated) by** | `appointments.save_appointment()` — immediately on new booking |
| | `appointments.cancel_appointment()` — immediately on cancellation |
| | TTL (30 sec) |
| **Notes** | Very short TTL (30s) because the web dashboard reads this on every page load. Cache exists mainly to absorb rapid refreshes, not for long-term caching |

---

### Google Calendar Events Cache

| Key pattern | `zenflow:gcal:events:{therapist_id}:{start_date}:{end_date}` |
|---|---|
| **Type** | String (JSON) |
| **TTL** | 600 seconds (10 minutes) |
| **Format** | JSON array of FullCalendar event dicts `[{"id":"...", "title":"...", "start":"...", "end":"..."}]` |
| **Written by** | `web/app.py` `GET /api/events` — after fetching from Google Calendar API |
| **Read by** | `web/app.py` `GET /api/events` — on cache hit |
| **Deleted (invalidated) by** | `GET /auth/disconnect` — when therapist disconnects Google Calendar |
| | `GET /logout` — purge all `zenflow:gcal:events:{tid}:*` keys |
| | TTL (10 min) |
| **Notes** | Key encodes the date window to allow different windows to be cached independently |

---

### Therapist Registration Codes

| Key pattern | `zenflow:reg:{CODE}` |
|---|---|
| **Type** | String (JSON) |
| **TTL** | 600 seconds (10 minutes) |
| **Format** | `{"name": "...", "email": "...", "google_id": null}` |
| **Written by** | `web/app.py` `POST /register/signup` — generates 8-char `[A-Z0-9]` code |
| | `web/app.py` `GET /api/my/activation-code` — generates fresh code on demand |
| **Read by** | `web/app.py` `GET /register/done` — to verify code still exists |
| | `therapist_bot/handlers.py` `_handle_registration()` — validates code from Telegram message |
| **Deleted by** | `therapist_bot/handlers.py` `_register_therapist_to_db()` — immediately after successful activation |
| | TTL (10 min) |
| **Notes** | One-time use — deleted on activation. Therapist must complete bot activation within 10 minutes of registering |

---

## Cache Invalidation Rules

### On Booking

```python
book_slot(day, time_slot, therapist_id):
    DEL  zenflow:slots:{day}
    DEL  zenflow:avail:hours:{tid}:{day}
    SCAN zenflow:avail:days:{tid}:*  → DEL each
```

### On Cancellation

```python
restore_slot(day, time_slot, therapist_id):
    DEL  zenflow:slots:{day}
    # avail:days and avail:hours also stale — will be rebuilt on next access (TTL)
```

### On Save / Cancel Appointment

```python
save_appointment() or cancel_appointment():
    DEL  zenflow:apts:all
```

### On Intake Complete

```python
clear_intake(user_id):
    DEL  zenflow:intake:{uid}:{tid}   (via RedisChatMessageHistory.clear())
```

---

## Operations Reference

```bash
# Check Redis is running
redis-cli ping                          # → PONG

# List all ZenFlow keys
redis-cli keys "zenflow:*"

# Inspect a specific key
redis-cli get "zenflow:apts:all"
redis-cli lrange "zenflow:intake:918187404:t1" 0 -1
redis-cli ttl "zenflow:reg:ABCD1234"

# Manually clear caches (development)
redis-cli del "zenflow:apts:all"
redis-cli del "zenflow:avail:days:t1:0"

# Clear ALL ZenFlow keys (development only)
redis-cli --eval - <<'EOF'
local keys = redis.call('keys', 'zenflow:*')
for i, k in ipairs(keys) do redis.call('del', k) end
return #keys
EOF

# Check memory
redis-cli info memory | grep used_memory_human
```
