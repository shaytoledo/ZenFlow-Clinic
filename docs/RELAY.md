# ZenFlow — Two-Bot Relay Architecture

> The relay connects patients (via the patient bot) to therapists (via the therapist bot)
> in real-time, supporting multiple simultaneous patient sessions.

---

## Why Two Bots

A single Telegram bot can only have one active `getUpdates` connection. Forwarding messages from a patient to a therapist via the same bot would send the message back to the same bot — Telegram does not deliver messages a bot sends to itself.

Using two separate bots with separate tokens solves this:
- **Patient bot** (`TELEGRAM_TOKEN`) — patients interact with this
- **Therapist bot** (`THERAPIST_BOT_TOKEN`) — therapists interact with this

The patient bot forwards messages **to** the therapist bot. The therapist bot routes replies **back** via the patient bot's API client.

Both bots run in the **same Python process**, sharing in-memory state and Redis.

---

## Architecture Diagram

```
PATIENT                    PATIENT BOT                   THERAPIST BOT              THERAPIST
   │                        (TELEGRAM_TOKEN)               (THERAPIST_BOT_TOKEN)        │
   │                                │                              │                    │
   │── "My back hurts" ────────────►│                              │                    │
   │                                │ Bot(THERAPIST_TOKEN)         │                    │
   │                                │──── forward message ────────►│                    │
   │                                │     fwd_msg_id = 92          │───── "Patient says: My back hurts" ──►│
   │                                │                              │                    │
   │                    save_relay_mapping(92, patient_id, "t1")   │                    │
   │                    Redis: zenflow:relay:msg:92 = {patient, t1}│                    │
   │                    Redis: zenflow:relay:active:{patient} = ... │                    │
   │                                │                              │                    │
   │◄── "Sent. [End Chat]" ─────────│                              │                    │
   │                                │                              │◄── reply-to msg 92 ── "Let's discuss..." ──│
   │                                │   get_patient_for_msg(92)    │                    │
   │                                │   → patient_id, therapist_id │                    │
   │                                │   security check: t1 == t1 ✓ │                    │
   │                                │◄─ Bot(TELEGRAM_TOKEN).send_message(patient_id, "Therapist: Let's discuss...") ─│
   │◄── "Therapist: Let's discuss..."│                              │                    │
   │                                │                              │                    │
   │── "End Chat" ─────────────────►│                              │                    │
   │                                │ end_relay(patient_id)        │                    │
   │                                │ Redis DEL zenflow:relay:active:{patient}           │
   │                                │── Bot(THERAPIST_TOKEN).send_message(therapist, "[Patient ended chat]") ──►│
   │◄── "Chat ended." ──────────────│                              │                    │
```

---

## Redis Keys Used by Relay

### `zenflow:relay:msg:{therapist_bot_msg_id}`

```
Key:     zenflow:relay:msg:92
Value:   {"patient_id": 918187404, "therapist_id": "t1"}
TTL:     86400 s (24 hours)
```

**Purpose:** Maps the Telegram message ID in the **therapist bot** to the patient and therapist IDs. This is the primary routing key.

**Written by:** `patient_bot/services/relay.py` `save_relay_mapping()` — called every time the patient bot forwards or relays a message.

**Read by:** `therapist_bot/services/relay.py` `get_patient_for_msg()` — called when therapist replies to a specific forwarded message.

**Deleted by:** TTL only (24h). Therapists can reply to old forwarded messages for up to 24 hours after the relay session ends.

---

### `zenflow:relay:active:{patient_id}`

```
Key:     zenflow:relay:active:918187404
Value:   {"patient_id": 918187404, "therapist_id": "t1", "started_at": "2026-03-09T19:44:16"}
TTL:     None (no expiry — explicit delete required)
```

**Purpose:** Presence key indicating a patient is currently in an active relay session. Also stores which therapist owns the session — used to prevent other therapists from replying to another therapist's patient.

**Written by:** `patient_bot/services/relay.py` `start_relay()` — when patient initiates chat.

**Read by:** `therapist_bot/handlers.py` — when therapist sends a free-text message (not a reply-to), to find their current active patient.

**Deleted by:** `patient_bot/services/relay.py` `end_relay()` — when patient or therapist ends the chat.

**Risk:** No TTL means orphaned sessions from crashes persist. Manual cleanup: `redis-cli del "zenflow:relay:active:{patient_id}"`.

---

### `zenflow:relay:history:{patient_id}`

```
Key:     zenflow:relay:history:918187404
Value:   [{"role": "patient", "text": "My back hurts", "ts": "2026-03-09T19:44:15"},
          {"role": "therapist", "text": "Let's discuss...", "ts": "2026-03-09T19:44:35"}]
TTL:     1800 s (30 minutes)
```

**Purpose:** Recent relay conversation history for display on the web dashboard `/messages` page.

**Written by:** Relay service `append_relay_message()`.

**Read by:** `web/app.py` messages page.

---

## Routing Logic (Therapist Side)

When the therapist bot receives a message from a known therapist, `handle_therapist_message()` decides the routing:

```python
if message.reply_to_message:
    # Precise routing: therapist replied to a specific forwarded message
    msg_id = message.reply_to_message.message_id
    mapping = get_patient_for_msg(msg_id)
    # mapping = {"patient_id": ..., "therapist_id": "t1"}

    if mapping["therapist_id"] != current_therapist_id:
        # Security: therapist A cannot reply to therapist B's patient
        await message.reply_text("⚠️ This message belongs to another therapist.")
        return

    await Bot(TELEGRAM_TOKEN).send_message(
        chat_id=mapping["patient_id"],
        text=f"Therapist: {message.text}"
    )

else:
    # Free-text routing: find therapist's current active patient
    active = get_active_relay_for_therapist(current_therapist_id)
    if active:
        await Bot(TELEGRAM_TOKEN).send_message(
            chat_id=active["patient_id"],
            text=f"Therapist: {message.text}"
        )
    else:
        await message.reply_text("No active relay session.")
```

---

## Security Model

| Threat | Mitigation |
|---|---|
| Therapist A reads Therapist B's patient messages | Relay mapping stores `therapist_id`; reply is rejected if IDs don't match |
| Unregistered user impersonates therapist | `THERAPIST_MAP` checked by `telegram_id`; unknown users get "not registered" |
| Replay attack via old forwarded message IDs | Relay msg keys expire after 24h |
| Patient sends to wrong therapist | `selected_therapist` is chosen before relay starts; can't be changed mid-session |

---

## Multi-Therapist Relay

When multiple therapists are active:
- Each therapist has a separate `telegram_id` in `THERAPIST_MAP`
- The relay key stores `therapist_id` in addition to `patient_id`
- The therapist bot has ONE `MessageHandler` that routes ALL therapists
- No per-therapist polling connections needed

```
Patient A ──→ Therapist T1 (telegram_id=918187404)
Patient B ──→ Therapist T2 (telegram_id=987654321)
Patient C ──→ Therapist T1 (telegram_id=918187404)

T1 can reply to A and C, but NOT to B's sessions.
T2 can reply to B, but NOT to A or C's sessions.
```

---

## Running Both Bots Concurrently

Both bots run in the same asyncio event loop:

```python
# bot/main.py
async def _run(patient_app, therapist_app):
    async with patient_app, therapist_app:
        await patient_app.updater.start_polling()
        await therapist_app.updater.start_polling()
        await asyncio.Event().wait()   # run forever

asyncio.run(_run(patient_app, therapist_app))
```

`asyncio.Event().wait()` blocks indefinitely, keeping both polling loops alive. On `Ctrl+C` or `SIGTERM`, both apps shut down cleanly via the `async with` context managers.
