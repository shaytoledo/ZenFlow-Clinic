# ZenFlow — Availability Management

> Availability management has two modes: Google Calendar (per-therapist) and local SQLite.
> The system auto-detects which mode to use based on whether a token file exists for the therapist.

---

## Two Modes

| Mode | Condition | Storage | Managed via |
|---|---|---|---|
| Google Calendar | `data/google_tokens/{therapist_id}.json` exists | Google Calendar API | FullCalendar + Google Calendar UI |
| Local (SQLite) | No Google token for therapist | `availability` table in `data/zenflow.db` | FullCalendar drag-to-create on `/schedule` |

Both modes are transparent to the patient — the same booking flow is used regardless.

---

## Mode Detection

```python
# availability.py
def _resolve_token_file(therapist_id: str | None):
    tokens_dir = Path(__file__).parent.parent.parent.parent / "data" / "google_tokens"
    if therapist_id:
        tf = tokens_dir / f"{therapist_id}.json"
        return tf if tf.exists() else None
    return None

def _gcal_service(therapist_id=None):
    tf = _resolve_token_file(therapist_id)
    if tf is None:
        return None   # ← triggers local mode
    # Build and return Google API service object
    ...
```

**No cross-therapist fallback.** If therapist T2 does not have their own token, they use local SQLite — even if therapist T1's token exists. Each therapist is fully independent.

---

## Google Calendar Mode

### Calendar Structure

```
Primary Calendar (calendarId="primary")
    ├── "🌿 ZenFlow — {patient_name}"  ← appointment events (created on booking)
    └── (other personal events)

"ZenFlow Availability" Calendar (separate calendar)
    └── "✅ Available" events           ← availability slots (created by therapist)
```

The therapist creates a separate Google Calendar named "ZenFlow Availability" and adds "✅ Available" events as continuous time blocks (e.g. Monday 09:00–13:00). The bot reads these and breaks them into 1-hour bookable slots.

**Custom calendar name:** The `calendar_name` column in `therapists` table (default `"ZenFlow Availability"`) allows each therapist to name their calendar differently.

### Querying Available Days

```python
get_available_days(week_offset, therapist_id):
    1. Redis GET zenflow:avail:days:{tid}:{week_offset}  → return if hit
    2. Build date range (week_offset=0: this week Mon–Sun, =1: next week)
    3. Find "ZenFlow Availability" calendar id in therapist's calendar list
    4. service.events().list(calendarId=avail_cal_id, timeMin=..., timeMax=...)
    5. Extract unique dates from returned events
    6. Redis SET zenflow:avail:days:{tid}:{week_offset}  TTL 10 min
    7. Return sorted list of date objects
```

### Querying Available Hours

```python
get_available_hours(day, therapist_id):
    1. Redis GET zenflow:avail:hours:{tid}:{date}  → return if hit
    2. service.events().list(calendarId=avail_cal_id, timeMin=day, timeMax=day+1day)
    3. Get booked slots: get_booked_slots(day)  → Redis or SQLite query
    4. For each availability event:
           expand into 1-hour slots (e.g. 09:00–13:00 → [09:00, 10:00, 11:00, 12:00])
           subtract booked slots
    5. Redis SET zenflow:avail:hours:{tid}:{date}  TTL 10 min
    6. Return sorted list of "HH:MM" strings
```

### Booking a Slot (Google Calendar)

```python
book_slot(day, time_slot, patient_name, summary, therapist_id):
    1. Invalidate Redis caches:
       DEL zenflow:slots:{day}
       DEL zenflow:avail:hours:{tid}:{day}
       SCAN+DEL zenflow:avail:days:{tid}:*  (all week offsets)

    2. Find covering "✅ Available" event in availability calendar
    3. _remove_hour_from_event(service, avail_cal_id, covering_event, day, slot)
       → exact match: DELETE the event
       → slot at start: PATCH event start to slot_end (shrink from left)
       → slot at end:   PATCH event end to slot_start (shrink from right)
       → slot in middle: PATCH end to slot_start + INSERT new event from slot_end to old end

    4. Create appointment event in PRIMARY calendar:
       service.events().insert(calendarId="primary", body={
           "summary": "🌿 ZenFlow — {patient_name}",
           "description": clinical_summary,
           "start": {"dateTime": slot_start, "timeZone": "Asia/Jerusalem"},
           "end":   {"dateTime": slot_end,   "timeZone": "Asia/Jerusalem"},
       })

    5. Return event["id"]  ← stored as gcal_apt_event_id in appointments table
```

### Cancellation (Google Calendar)

```python
restore_slot(day, time_slot, gcal_apt_event_id, therapist_id):
    1. Redis DEL zenflow:slots:{day}
    2. service.events().delete(calendarId="primary", eventId=gcal_apt_event_id)
    3. Re-create "✅ Available" event in availability calendar:
       service.events().insert(calendarId=avail_cal_id, body={
           "summary": "✅ Available",
           "start": ..., "end": ..., "colorId": "10"  (green)
       })
```

Note: On cancellation, a new 1-hour availability event is created — there is no merging with adjacent events. Over time this can fragment the availability calendar, but it remains functionally correct.

---

## Local Mode (SQLite `availability` table)

### Table Structure

```sql
CREATE TABLE availability (
    id           TEXT PRIMARY KEY,   -- uuid4().hex
    therapist_id TEXT NOT NULL,
    start_dt     TEXT NOT NULL,      -- "YYYY-MM-DDTHH:MM:SS"
    end_dt       TEXT NOT NULL
);
```

### Querying Available Days (Local)

```python
get_available_days(week_offset, therapist_id):
    1. Redis GET zenflow:avail:days:{tid}:{week_offset}  → return if hit
    2. _read_local_avail(therapist_id)
       → SELECT id, start_dt, end_dt FROM availability WHERE therapist_id=?
    3. Filter to dates in the target week range
    4. Extract unique dates
    5. Redis SET ... TTL 10 min
    6. Return sorted list
```

### Querying Available Hours (Local)

```python
get_available_hours(day, therapist_id):
    1. Redis GET zenflow:avail:hours:{tid}:{date}  → return if hit
    2. _read_local_avail(therapist_id)  → all slots for therapist
    3. _local_hours(day, slots)
       → filter to slots on target day
       → expand each slot into 1-hour blocks
       → subtract get_booked_slots(day)
    4. Redis SET ... TTL 10 min
    5. Return sorted list
```

### Booking a Slot (Local)

```python
book_slot(day, time_slot, ..., therapist_id):
    1. Invalidate Redis caches (same as Google Calendar mode)
    2. _remove_hour_from_local(therapist_id, day, time_slot)
       → SELECT covering slot from availability
       → DELETE the row
       → re-insert remainder(s):
           exact match     → just delete, no insert
           slot at start   → insert [slot_end, ev_end]
           slot at end     → insert [ev_start, slot_start]
           slot in middle  → insert [ev_start, slot_start] + [slot_end, ev_end]
    3. Return sentinel: "local_{therapist_id}_{date}_{time}"
       (stored as gcal_apt_event_id — triggers local restore path on cancellation)
```

### Cancellation (Local)

```python
restore_slot(day, time_slot, gcal_apt_event_id, therapist_id):
    1. Redis DEL zenflow:slots:{day}
    2. if gcal_apt_event_id.startswith("local_"):
       → _add_hour_to_local(therapist_id, day, time_slot)
          INSERT INTO availability (id=uuid, therapist_id, start_dt, end_dt)
```

### Managing Local Slots via Web Dashboard

The therapist uses the FullCalendar interface at `/schedule`:
- **Drag to create** a slot → `POST /api/availability` (`web/routers/api/availability.py`) → `INSERT INTO availability`
- **Click to delete** a slot → `DELETE /api/availability/{id}` → `DELETE FROM availability WHERE id=?`
- Web reads: `GET /api/events` → `web/services/availability_service.py` reads availability table when no Google token

---

## Google Calendar Token Management

### Token Files

```
data/google_tokens/t1.json    ← therapist with id "t1"
data/google_tokens/t2.json    ← therapist with id "t2"
```

Each token is a JSON credential file from Google OAuth 2.0, containing:
- `access_token` — short-lived (1 hour)
- `refresh_token` — long-lived, used to get new access tokens
- `token_uri`, `client_id`, `client_secret`, `scopes`

### Token Refresh

```python
creds = Credentials.from_authorized_user_file(str(tf), SCOPES)
if creds.expired and creds.refresh_token:
    creds.refresh(Request())      # exchange refresh_token for new access_token
    tf.write_text(creds.to_json())   # save updated token back to file
```

Auto-refresh happens every time `_gcal_service()` is called and the token is expired.

### OAuth Flow (Connecting Calendar)

```
1. GET /auth/start → build Google OAuth URL (calendar scope)
2. Google redirects to GET /auth/callback?code=...
3. Exchange code for credentials
4. Save to data/google_tokens/{therapist_id}.json
5. Redirect to /settings — calendar is now connected
```

### Disconnecting Calendar

```
GET /auth/disconnect
→ delete data/google_tokens/{therapist_id}.json
→ purge Redis: zenflow:gcal:events:{tid}:*
→ redirect to /settings — availability falls back to local SQLite
```

---

## Cache Invalidation Summary

| Event | Caches invalidated |
|---|---|
| Patient books slot | `zenflow:slots:{date}`, `zenflow:avail:hours:{tid}:{date}`, `zenflow:avail:days:{tid}:*` |
| Patient cancels | `zenflow:slots:{date}` |
| Therapist adds availability (web) | None (5-min TTL handles staleness) |
| Therapist deletes availability (web) | None (5-min TTL handles staleness) |
| Google Calendar disconnect | `zenflow:gcal:events:{tid}:*` |

---

## asyncio.to_thread() Wrapping

All Google Calendar API calls — including service construction — use the synchronous `google-api-python-client`. To avoid blocking the asyncio event loop, **all** synchronous operations are wrapped:

### Service construction

```python
# _gcal_service() reads a token file and may call creds.refresh(Request())
# which is a synchronous HTTP call — must not run on the event loop
service = await asyncio.to_thread(_gcal_service, therapist_id)
```

This matters because token refresh is a real network call (exchanging the OAuth refresh token for a new access token). Before this wrapping, it would stall the event loop during every cache miss on day/hour queries.

### API calls

```python
events = await asyncio.to_thread(
    service.events().list(
        calendarId=cal_id,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
    ).execute   # ← .execute, not .execute() — passed as callable, not result
)
```

The `.execute` (not `.execute()`) is passed as a callable to `to_thread`, which runs it in a thread pool worker. This releases the event loop during the blocking HTTP call.
