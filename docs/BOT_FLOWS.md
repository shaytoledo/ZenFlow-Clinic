# ZenFlow — Bot Conversation Flows

> All handler functions are `async def (update, context) -> int`.
> The returned integer is the next conversation state constant from `bot/states.py`.

---

## State Constants (`bot/states.py`)

```python
(
    SELECTING,        # 0 — main menu
    THERAPIST_SELECT, # 1 — patient choosing a therapist
    SCHEDULE_WEEK,    # 2 — this week / next week
    SCHEDULE_DAY,     # 3 — day picker
    SCHEDULE_HOUR,    # 4 — hour picker
    INTAKE_CONFIRM,   # 5 — yes/no intake questionnaire
    INTAKE,           # 6 — active AI intake (loops)
    CANCEL_SELECT,    # 7 — appointment cancellation picker
    THERAPIST_INPUT,  # 8 — patient typing message to therapist
    THERAPIST_RELAY,  # 9 — active relay loop
) = range(10)
```

---

## Full State Machine Diagram

```
/start  OR  any unrecognised message
          │
          ▼
    ┌─────────────┐
    │  SELECTING  │  ◄────────────────────────────────────────────────┐
    │  (main menu)│                                                    │
    └──────┬──────┘                                                    │
           │                                                           │
    ┌──────┴────────────────────────────────────┐                     │
    │                                           │                     │
    ▼                                           ▼                     │
[📅 Schedule]                              [❌ Cancel]                │
    │                                           │                     │
    ▼                                           ▼                     │
THERAPIST_SELECT ──(if 1 therapist,        CANCEL_SELECT             │
(choose therapist)   auto-select)          (list appointments)        │
    │                                           │                     │
    ▼                                           │ [confirm cancel]    │
SCHEDULE_WEEK                                   │                     │
(this / next week)                              └────────────────────►┘
    │                                                                  │
    ▼                                                                  │
SCHEDULE_DAY                                                           │
(day picker)                                                           │
    │                                                                  │
    ▼                                                                  │
SCHEDULE_HOUR                                                          │
(hour picker)                                                          │
    │                                                                  │
    ▼                                                                  │
INTAKE_CONFIRM                                                         │
(yes / no)                                                             │
    │                        │                                         │
    │ [Yes]                  │ [No]                                    │
    ▼                        ▼                                         │
  INTAKE              save immediately ──────────────────────────────►┘
  (loops ×5)                                                           │
    │ (5th answer)                                                      │
    └──── save appointment ────────────────────────────────────────────┘
                                                                        │
[💬 Connect]                                                            │
    │                                                                   │
    ▼                                                                   │
THERAPIST_SELECT ──(if 1, auto-select)                                 │
    │                                                                   │
    ▼                                                                   │
THERAPIST_INPUT                                                         │
(type message)                                                          │
    │                                                                   │
    ▼                                                                   │
THERAPIST_RELAY ──────(loop until [End Chat])─────────────────────────►┘
```

---

## Flow 1: Schedule an Appointment

### Handler chain

| Step | Handler | File | Returns |
|---|---|---|---|
| 0 | `show_therapist_choice()` | `schedule.py` | `THERAPIST_SELECT` or auto-advances |
| 1 | `select_therapist_and_continue()` | `schedule.py` | `SCHEDULE_WEEK` |
| 2 | `show_week_choice()` | `schedule.py` | `SCHEDULE_WEEK` |
| 3 | `show_days()` | `schedule.py` | `SCHEDULE_DAY` |
| 4 | `show_hours()` | `schedule.py` | `SCHEDULE_HOUR` |
| 5 | `confirm_appointment()` | `schedule.py` | `INTAKE_CONFIRM` |
| 6a | `start_intake()` | `schedule.py` | `INTAKE` |
| 6b | `skip_intake()` | `schedule.py` | `SELECTING` |
| 7 | `handle_intake_answer()` × 5 | `schedule.py` | `INTAKE` (×4), `SELECTING` (final) |

### Step 0: Therapist Selection (`show_therapist_choice`)

```
Precondition: patient tapped "📅 Schedule"

if active therapists == 1:
    context.user_data["selected_therapist"] = active[0]["id"]
    → advance to show_week_choice() (skip selection screen)

if active therapists > 1:
    render inline keyboard with therapist names
    → return THERAPIST_SELECT
```

### Step 1: Therapist Confirmed (`select_therapist_and_continue`)

```
Triggered by: callback_data = "sel_t_{therapist_id}"

context.user_data["selected_therapist"] = therapist_id
flow = context.user_data.pop("therapist_flow", "schedule")

if flow == "contact" → THERAPIST_INPUT
if flow == "welcome" → SELECTING (show main menu with therapist name)
else                 → show_week_choice()
```

### Steps 2–4: Week → Day → Hour

```
show_week_choice()  → inline keyboard: "This week" / "Next week"
                      context.user_data["selected_week"] = 0 or 1
                      → SCHEDULE_WEEK

show_days()         → calls get_available_days(week_offset, therapist_id)
                      → Redis cache check first, then Google Calendar or SQLite
                      → inline keyboard: one button per available date
                      → SCHEDULE_DAY

show_hours()        → calls get_available_hours(day, therapist_id)
                      → Redis cache check first, then calendar minus booked slots
                      → inline keyboard: one button per available hour
                      → SCHEDULE_HOUR
```

**Back navigation:**
- `back_week` callback → `show_week_choice()`
- `back_days` callback → `show_days()` (uses stored `selected_week`)

### Step 5: Intake Prompt (`confirm_appointment`)

```
Triggered by: callback_data = "hour_{HH:MM}"

context.user_data["selected_time"] = "HH:MM"
Show message: "📅 Monday, 20 March at 11:00 — noted! Would you like a few quick questions?"
Inline keyboard: [Yes] / [No]
→ INTAKE_CONFIRM
```

### Step 6a: Skip Intake (`skip_intake`)

```
book_slot(day, time_slot, patient_name, "Patient opted to skip", therapist_id)
    → invalidates Redis caches
    → Google Calendar or SQLite slot removal
    → returns gcal_id or "local_..." sentinel

save_appointment(patient_id, patient_name, day, time, [], "no intake", gcal_id, therapist_id)
    → SQLite BEGIN: INSERT appointments + INSERT intake_sessions (empty history)
    → COMMIT
    → Redis DEL zenflow:apts:all

save_treatment_notes(appointment_id, patient_id, {})
    → SQLite UPSERT treatment_notes (all fields NULL/empty)

clear_intake(user_id)
    → no-op if no intake was started

context.user_data.clear()
→ send confirmation message with date + time
→ SELECTING
```

### Step 6b: Start Intake (`start_intake`)

```
context.user_data["intake_count"] = 0
initialize_intake(user_id, OPENING_QUESTION)
    → clear Redis history key
    → clear in-process caches
    → write opening question to Redis history

Send message: "Great! A few quick questions..."
Send message: OPENING_QUESTION
→ INTAKE
```

### Step 7: Intake Answer Loop (`handle_intake_answer`)

```
Repeat for answers 1–4:
    intake_count += 1
    get_next_question(user_id, answer)
        → add answer to Redis history
        → _maybe_compress() if > 6 messages
        → LLM call (100s timeout) → adaptive question
        → fallback question on error/timeout
    Send next question
    → INTAKE

On 5th answer:
    generate_summary(user_id, final_answer)
        → LLM call: summarise entire conversation
        → fallback: "Intake completed — see history for details."

    book_slot(...)   → invalidate Redis caches, update calendar
    save_appointment(...)   → SQLite + Redis invalidation
    generate_tcm_diagnosis(user_id, summary)
        → LLM call: structured TCM JSON
        → fallback: empty dict with certainty=0
    save_treatment_notes(appointment_id, user_id, tcm)   → SQLite UPSERT
    clear_intake(user_id)   → clear Redis + in-process caches

    Send confirmation:
        "✅ Appointment successfully booked!"
        "📅 Friday, 20 March 2026 at 11:00"
        "📋 Intake summary: [first 3 lines of AI summary]"
        "Your acupuncturist has received your intake details..."
    → SELECTING
```

---

## Flow 2: Cancel an Appointment

### Handler chain

| Step | Handler | File | Returns |
|---|---|---|---|
| 1 | `show_appointments()` | `cancel.py` | `CANCEL_SELECT` |
| 2 | `confirm_cancel()` | `cancel.py` | `SELECTING` |

### Step 1: List Appointments (`show_appointments`)

```
get_patient_appointments(patient_id)
    → SQLite: SELECT * FROM appointments WHERE patient_id=? AND status='active'

if none:
    → "You have no active appointments."
    → SELECTING

else:
    render inline keyboard: one button per appointment
    each button: "{date} at {time} — {therapist_name}"
    callback_data: "cancel_{appointment_id}"
    → CANCEL_SELECT
```

### Step 2: Confirm and Cancel (`confirm_cancel`)

```
appointment_id = int(query.data.replace("cancel_", ""))

cancel_appointment(appointment_id)
    → SQLite: UPDATE appointments SET status='cancelled' WHERE id=?
    → Redis DEL zenflow:apts:all

restore_slot(day, time_slot, gcal_apt_event_id, therapist_id)
    → Redis DEL zenflow:slots:{date}
    → if gcal_id starts with "local_":
        → _add_hour_to_local() → SQLite INSERT availability
    → else (Google Calendar):
        → delete appointment event from primary calendar
        → create "✅ Available" event in availability calendar

→ send "✅ Appointment cancelled."
→ SELECTING
```

---

## Flow 3: Connect to Therapist (Relay)

### Handler chain

| Step | Handler | File | Returns |
|---|---|---|---|
| 1 | `ask_therapist_message()` | `therapist.py` | `THERAPIST_SELECT` or `THERAPIST_INPUT` |
| 2 | `select_therapist_and_continue()` | `schedule.py` | `THERAPIST_INPUT` |
| 3 | `start_relay()` | `therapist.py` | `THERAPIST_RELAY` |
| 4 | `relay_to_therapist()` | `therapist.py` | `THERAPIST_RELAY` (loops) |
| 5 | `end_chat()` | `therapist.py` | `SELECTING` |

### Step 1: Therapist Selection for Contact

```
if active therapists == 1:
    context.user_data["selected_therapist"] = active[0]["id"]
    → show THERAPIST_INPUT prompt directly

if active therapists > 1:
    context.user_data["therapist_flow"] = "contact"
    → show therapist selection keyboard
    → THERAPIST_SELECT
```

### Step 3: Start Relay (`start_relay`)

```
Triggered by: patient sends first message in THERAPIST_INPUT state

therapist = THERAPIST_BY_ID[selected_therapist]
fwd_msg = await Bot(THERAPIST_BOT_TOKEN).send_message(
    chat_id=therapist["telegram_id"],
    text=f"[{patient_name}]: {patient_message}"
)
save_relay_mapping(fwd_msg.message_id, patient_id, therapist_id)
    → Redis SET zenflow:relay:msg:{fwd_msg.id} = {patient_id, therapist_id}  TTL 24h
    → Redis SET zenflow:relay:active:{patient_id} = {patient_id, therapist_id, started_at}

send to patient: "✅ Message sent. [End Chat]"
→ THERAPIST_RELAY
```

### Step 4: Relay Loop (`relay_to_therapist`)

```
Triggered by: any text message in THERAPIST_RELAY state

fwd_msg = await Bot(THERAPIST_BOT_TOKEN).send_message(therapist, text)
save_relay_mapping(fwd_msg.message_id, patient_id, therapist_id)
    → refreshes Redis key

→ THERAPIST_RELAY (loop continues)
```

### Step 5: End Chat (`end_chat`)

```
Triggered by: "End Chat" inline button

end_relay(patient_id)
    → Redis DEL zenflow:relay:active:{patient_id}

Send patient: "Chat ended. Back to main menu."
Send therapist (if relay was active): "[Patient ended the chat]"
→ SELECTING
```

### Therapist Replies (therapist bot side)

```
handle_therapist_message() in therapist_bot/handlers.py:

1. Check sender in THERAPIST_MAP (by telegram_id)
   → if unknown: check for 8-char registration code
   → if valid code: register → "Activated ✅"
   → if no match: "You are not registered as a therapist."

2. If known therapist:
   if update.message.reply_to_message:
       → get_patient_for_msg(reply_to.message_id)
         → Redis GET zenflow:relay:msg:{reply_msg_id}
         → returns {patient_id, therapist_id}
       → security check: replying therapist must match stored therapist_id
       → Bot(TELEGRAM_TOKEN).send_message(patient_id, f"Therapist: {text}")
   else:
       → get current active patient from zenflow:relay:active:*
       → route to that patient if active session exists
```

---

## ConversationHandler Configuration

```python
# bot/main.py
ConversationHandler(
    entry_points=[
        CommandHandler("start", start),
        MessageHandler(filters.ALL, start),
        CallbackQueryHandler(show_therapist_choice, pattern="^schedule$"),
        CallbackQueryHandler(show_appointments, pattern="^cancel$"),
        CallbackQueryHandler(ask_therapist_message, pattern="^therapist$"),
    ],
    states={
        SELECTING:        [...],
        THERAPIST_SELECT: [CallbackQueryHandler(select_therapist_and_continue, "^sel_t_")],
        SCHEDULE_WEEK:    [CallbackQueryHandler(show_days, "^week_"),
                           CallbackQueryHandler(show_week_choice, "^back_week$")],
        SCHEDULE_DAY:     [CallbackQueryHandler(show_hours, "^day_"),
                           CallbackQueryHandler(show_days, "^back_days$")],
        SCHEDULE_HOUR:    [CallbackQueryHandler(confirm_appointment, "^hour_"),
                           CallbackQueryHandler(show_days, "^back_days$")],
        INTAKE_CONFIRM:   [CallbackQueryHandler(start_intake, "^intake_yes$"),
                           CallbackQueryHandler(skip_intake, "^intake_no$")],
        INTAKE:           [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_intake_answer)],
        CANCEL_SELECT:    [CallbackQueryHandler(confirm_cancel, "^cancel_")],
        THERAPIST_INPUT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, start_relay)],
        THERAPIST_RELAY:  [MessageHandler(filters.TEXT & ~filters.COMMAND, relay_to_therapist),
                           CallbackQueryHandler(end_chat, "^end_chat$")],
    },
    fallbacks=[CommandHandler("start", start)],
    allow_reentry=False,   # ← CRITICAL: must be False
)
```

**Why `allow_reentry=False` is critical:**
The catch-all entry point `MessageHandler(filters.ALL, start)` matches ALL text messages. If `allow_reentry=True`, any message in `INTAKE` or `THERAPIST_INPUT` state would re-enter via the `start` handler instead of the state handler — completely breaking those flows.
