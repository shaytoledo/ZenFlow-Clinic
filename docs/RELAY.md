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
   │                    Redis: zenflow:relay:msg:t1:92 = {patient, t1}│                    │
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

## Isolation between therapists (Phase 2.4)

Everything a therapist can read or reply to is keyed by that therapist:

- **Message routing** is `zenflow:relay:msg:{therapist_id}:{msg_id}`. Telegram numbers messages
  *per chat*, so the therapist bot's message 50 to Dr A and its message 50 to Dr B are different
  messages; with the old global key the second mapping overwrote the first.
- **History and unread markers** are per therapist–patient pair. A patient who moves from Dr A to
  Dr B starts a new conversation in Dr B's view; Dr A's stays with Dr A.
- **The dashboard** lets a therapist into a conversation only when the live session is theirs or
  they have stored history of their own. There is no "has an appointment with this patient" rule
  any more (SF-008), and when Redis is unreachable access is refused, never guessed.

`tests/security/test_relay_isolation.py` pins each path: reply-to, free typing, a stale
`current:{therapist}` key, a reused message id, and the dashboard after a chat has ended.

## Routing rules (Phase 2.2a)

A therapist message is delivered only when the intended patient is unambiguous:

| Therapist action | Mapping | Delivered to |
|---|---|---|
| Replies to a forwarded message | found, owned by them | that message's patient |
| Replies to a forwarded message | found, owned by **another** therapist | nobody — "not authorised" |
| Replies to a forwarded message | **missing or expired** (24 h TTL) | nobody — asked to reply to a newer message |
| Types freely | exactly **one** open chat | that patient |
| Types freely | **two or more** open chats | nobody — asked to reply to the patient's message |
| Types freely | no open chat | nobody — "no active patient chat" |

There is deliberately no "last patient who wrote" fallback: it delivered clinical text to the
wrong patient once a mapping expired or a second patient wrote in (BOT_AUDIT B1).

`end_relay(patient_id)` clears `relay:active:{patient}` and releases
`relay:current:{therapist}` **only when it still points at that patient**, so a patient leaving
does not close a chat the therapist has since opened with someone else.

Message bodies cross the relay as **plain text** (no `parse_mode`): a name or message containing
`_ * ` [` used to make Telegram reject the whole send, and Markdown in patient text could forge
formatting in the therapist's view (BOT_AUDIT B2).

Non-text messages (photo, voice, document, sticker, location) are **not forwarded** in either
direction. The sender is told so, and a patient stays in the chat instead of being dropped back to
the main menu. Whether media should be relayed — and stored, since it may be PHI — is open
question Q6 in `docs/BOT_AUDIT.md`.

---

## Redis Keys Used by Relay

### `zenflow:relay:msg:{therapist_id}:{therapist_bot_msg_id}`

```
Key:     zenflow:relay:msg:t1:92
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
Value:   {"patient_id": 918187404, "patient_name": "Moshe Levi", "therapist_id": "t1",
          "started_at": 1772134656.0, "last_msg_id": 92}
TTL:     86400 s (24h), refreshed on every patient message
```

**Purpose:** Presence key indicating a patient is currently in an active relay session. Also stores which therapist owns the session — used to prevent other therapists from replying to another therapist's patient.

**Written by:** `patient_bot/services/relay.py` `save_relay_mapping()` — on every patient message.

**Read by:** `list_active_patients(therapist_id)` (a `SCAN` over `relay:active:*`) — this is what decides whether a therapist's free-typed message has exactly one possible recipient.

**Deleted by:** `patient_bot/services/relay.py` `end_relay()` — when the patient ends the chat; otherwise by TTL.

---

### `zenflow:relay:history:{therapist_id}:{patient_id}`

```
Key:     zenflow:relay:history:t1:918187404
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

    if mapping is None:
        # Mapping expired (24h) — the intended patient is unknown. Never guess (B1).
        await message.reply_text("That conversation has expired — reply to a newer message.")
        return

    if mapping["therapist_id"] != current_therapist_id:
        # Security: therapist A cannot reply to therapist B's patient
        await message.reply_text("⚠️ This message belongs to another therapist.")
        return

    # _patient_channel: a TelegramChannel over the patient application's client (7.1, B14)
    await _patient_channel.send_buttons(
        mapping["patient_id"],
        f"Therapist: {message.text}",        # plain text, no parse_mode (B2)
        [[("🔚 End Chat", "therapist_end")]],
    )

else:
    # Free typing: allowed only while exactly one chat is open for this therapist
    active = list_active_patients(current_therapist_id)
    if len(active) > 1:
        await message.reply_text("You have several open chats — reply to the patient's message.")
    elif not active:
        await message.reply_text("No active relay session.")
    else:
        await _patient_channel.send_buttons(
            active[0], f"Therapist: {message.text}", [[("🔚 End Chat", "therapist_end")]]
        )
```

---

> Since Phase 7.1 the `Bot(...)` sends in these diagrams are `TelegramChannel` calls over the
> running applications' own clients (`_therapist_channel`, `_patient_channel`, wired by
> `bot.main.wire_bots()`); see `docs/CHANNELS.md`.

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
