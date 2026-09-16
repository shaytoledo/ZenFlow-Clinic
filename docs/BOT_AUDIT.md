# Bot Audit — Phase 2.1 (2026-09-15)

> Status: B1, B2, B5, B7 (interim) and B12 were fixed in Phase 2.2a (§1a); B3, B4 and B9 in
> Phase 2.2b (§1b); B6, B8 and B11 in Phase 2.2c (§1c); B10 and B14 in Phase 2.2d (§1d).

Scope: every handler in `bot/patient_bot/` and `bot/therapist_bot/`, read on `master` @ `140a50f`.
Method: for each handler — reachable states, return value, `user_data` read/written — and its
behaviour under (a) unexpected input type, (b) expired/stale callback, (c) Redis outage,
(d) Ollama timeout, (e) duplicate click, (f) `/start` mid-flow, (g) message after the conversation
state was lost, (h) two devices on one Telegram account.

Per the plan, **nothing is fixed in this PR** — the ranked list below comes back to the owner
first, and the scope of Phase 2.2 is agreed together. Every finding cites file:line and names the
concrete scenario; each becomes a failing test before it is fixed.

Runtime facts that shape the answers:
- One `ConversationHandler` (bot/main.py:130) with `allow_reentry=False`, no
  `conversation_timeout`, no persistence, `per_chat=per_user=True` (defaults), sequential update
  processing (PTB default `concurrent_updates=False`).
- Fallbacks: `CommandHandler("start", start)` and **`MessageHandler(filters.ALL, start)`** — any
  update no state handler accepts is routed to `start()`.
- **No `add_error_handler`** on either application: an exception reaches only PTB's own log line;
  the user gets silence (and an unanswered callback spinner).
- Conversation state and `user_data` live in memory: a bot restart drops every in-flight flow.

---

## 1. Ranked findings (by patient impact)

| # | Severity | Finding | Where | Seed |
|---|---|---|---|---|
| B1 | **Critical** | **Therapist replies can reach the wrong patient.** (1) `end_relay()` deletes `relay:active:{pid}` but not `relay:current:{therapist_id}`, so free-typed text still goes to the patient who ended the chat. (2) When a therapist *replies* to a message whose mapping has expired (24 h TTL) or was never saved, `_handle_relay` silently falls back to `get_current_patient()` — the **last patient who wrote to that therapist**, not the one being replied to. (3) `current:{therapist_id}` is a single slot: patient A writes, patient B writes, the therapist free-types an answer meant for A → delivered to B. Clinical text to the wrong person. | patient_bot/services/relay.py:90; therapist_bot/handlers.py:90-119; relay.py:63-64 | F4 confirmed + extended |
| B2 | **High** | **Markdown breaks the relay.** Patient name and message text are interpolated into `parse_mode="Markdown"` messages (`*New message from {patient_name}*` + raw text); therapist replies likewise. Any unbalanced `_`, `*`, `` ` `` or `[` — an email address, "5*3", a name like `Moshe_K` — makes Telegram return *can't parse entities*: the patient is told "Could not reach the therapist", the therapist reply fails with "Could not deliver". It is also a spoofing vector (formatting injected into therapist-facing text; plan 9.7). | patient_bot/therapist.py:93-100, 135-139; therapist_bot/handlers.py:122-127 | new |
| B3 | **High** | **Follow-up answers are swallowed by other states.** The 24 h follow-up is consumed only inside `start()`. A patient in `INTAKE`, `THERAPIST_INPUT` or `THERAPIST_RELAY` who answers "7" has it fed to the intake LLM or forwarded to the therapist; the follow-up conversation never advances. | patient_bot/start.py:57-80; main.py:171-179 | F3 confirmed |
| B4 | **High** | **Double booking.** Hours are computed when shown (`show_hours`), but `skip_intake` / `handle_intake_answer` save the appointment minutes later (after up to five intake answers) without re-checking the slot. Two patients shown the same free hour both book it; `book_slot` removes an already-removed hour without error. | patient_bot/schedule.py:181-209, 256-296, 450-495 | new |
| B5 | **High** | **Therapists activated on the web are invisible to the bots until restart.** `THERAPIST_MAP` / `THERAPISTS` are loaded at import; only *bot-side* registration mutates them. A therapist who activates through the web portal gets "You're not registered" from the therapist bot and cannot be selected by patients until the bot process restarts (the web runs in another process). | therapist_bot/handlers.py:9, 70; patient_bot/start.py:82; config.py | new (plan 12.2.4 root cause) |
| B6 | **High** | **Bookings silently lost mid-flow.** A photo/voice/sticker/location in `INTAKE` (TEXT-only handler) or `/start` in any state hits the fallback `start()`, which shows the menu and returns `SELECTING`. The half-finished booking is abandoned with no message; `selected_day`/`selected_time` stay in `user_data`. `/start` is not a clean reset. | main.py:171-173, 186-189; start.py:83-96 | new |
| B7 | **High** | **Media is dropped silently.** Both bots accept TEXT only. A patient photo in `THERAPIST_RELAY` exits the relay via the fallback (patient believes it was sent); a therapist photo/voice gets no response at all. Media may be PHI → policy decision required. | therapist_bot/main.py:28; main.py:180-183 | F5 confirmed |
| B8 | Medium | **No error handler → silence.** Any exception (Redis down, DB locked, stale `user_data`) leaves the patient with no reply and a spinning button. Examples: `confirm_cancel` indexes `user_data["apts_to_cancel"][idx]` (IndexError if the list is gone); `_handle_relay` calls Redis unguarded. | cancel.py:60-61; therapist_bot/handlers.py:91, 111 | new |
| B9 | Medium | **Redis outage mis-reports relay delivery.** `start_relay` sends to the therapist, then `save_relay_mapping` raises → caught by the same `except` → the patient is told "Could not reach the therapist" although the therapist received it, and the therapist cannot reply (no mapping). Intake start (`initialize_intake`, Redis-backed history) raises with no handler. | therapist.py:92-112; schedule.py:249 | new |
| B10 | Medium | **Stuck states, no timeout.** No `conversation_timeout`: a patient left in `THERAPIST_RELAY` has every later message ("I want to book Tuesday") forwarded to the therapist days later; `INTAKE` waits forever. | main.py:130-190 | new |
| B11 | Medium | **Stale buttons after a restart or state change hang.** Old inline keyboards send callbacks in a state with no matching handler (entry points have no `CallbackQueryHandler`) → the query is never answered → spinner. The "🔚 End Chat" button on therapist replies does nothing unless the patient is still in `THERAPIST_RELAY`. | main.py:131-134; handlers.py:126 | new |
| B12 | Medium | **Relay can target a different therapist.** `_get_therapist()` falls back to the *first active therapist* when the selected one is missing or deactivated — the patient's message goes to someone they did not choose. | patient_bot/therapist.py:21-27 | new |
| B13 | Medium | **Intake pipeline is fire-and-forget.** `asyncio.ensure_future(_summary_and_tcm(...))`: a restart mid-pipeline leaves `points_status` stuck at `GENERATING_*`; no retry. | schedule.py:484 | known (Phase 3.1) |
| B14 | Low | Module-level `Bot(token=...)` instances in both relay modules are never initialised/closed (httpx pool leak on shutdown; bypass the application's rate limiter). | therapist.py:18; handlers.py:24 | plan 2.2 |
| B15 | Low | Hard-coded English in the relay flow and `change_therapist` (the rest of the bot is localised). | therapist.py:37-75; start.py:31 | new |
| B16 | Low | Ollama waits up to 100 s per intake question with no typing indicator; fallback questions exist, so correctness holds. | ai_intake.py | (d) OK |
| B17 | Low | Registration code: 8 × [A-Z0-9] (36⁸ ≈ 2.8·10¹²) with 10-min TTL — brute force infeasible under Telegram flood limits; no rate limit or lockout. | handlers.py:18, 72 | plan 10 A7 |

Not a defect: (h) two devices on one Telegram account share one `user_data` and one conversation
(PTB keys by chat+user); updates are processed sequentially, so no race. (e) duplicate clicks are
mostly harmless because the state has already moved on — the second click lands in B11 instead.

---

## 1a. Fixed in Phase 2.2a (relay safety)

| # | What changed | Where | Test |
|---|---|---|---|
| B1 | `end_relay()` now reads the session and **compare-and-deletes** `relay:current:{therapist_id}` (a newer chat with another patient is never released). A reply whose 24 h mapping expired is **refused** — the therapist is asked to reply to a newer message — instead of falling back to "whoever wrote last". Free typing is delivered only while **exactly one** patient chat is open; with two or more the bot asks the therapist to reply to the patient's message. | patient_bot/services/relay.py · therapist_bot/handlers.py | `tests/bot/test_relay_safety.py` (6 tests) |
| B2 | Every relay body — patient → therapist and therapist → patient — is sent as **plain text**, no `parse_mode`. Names and message text can contain `_ * ` [` without the send failing, and formatting can no longer be injected into therapist-facing text. | patient_bot/therapist.py · therapist_bot/handlers.py | 2 tests with `a_b@c.com`, `5*3`, `[notes](x)` |
| B5 | `reload_therapists()` mutates the three registry containers **in place**; it used to rebind the module globals, so every module that did `from bot.config import THERAPIST_BY_ID` kept an import-time snapshot. A therapist deactivated in the dashboard is now invisible to the bot on the next reload, without a restart. (Cross-process propagation — the web dashboard and the bots are separate processes — is still open and stays in 2.2b.) | config.py | reload-visibility test |
| B7 | **Interim policy only, pending Q6:** media is refused politely instead of vanishing. A patient photo/voice/file in `THERAPIST_RELAY` gets an explanation and **stays in the chat** (it used to fall through to `start()`, which silently ended the relay while the patient believed the file was sent); a therapist's media gets the same answer. Nothing is forwarded or stored, so the eventual answer to Q6 is unconstrained. | patient_bot/therapist.py · therapist_bot/handlers.py · main.py wiring | 2 tests |
| B12 | `_get_therapist()` no longer substitutes the first active therapist. The patient's chosen therapist is used when they are still active; otherwise the choice is cleared and the patient is asked to choose again. The implicit fallback now applies only when the patient chose nobody **and** the clinic has exactly one active therapist. | patient_bot/therapist.py | deactivated-therapist test |

Still open from the list above: B3, B4, B6, B8–B11, B14 (Phase 2.2b), B13 (Phase 3.1), B15–B17.


## 1b. Fixed in Phase 2.2b (bookings and follow-ups)

| # | What changed | Where | Test |
|---|---|---|---|
| B3 | The follow-up now has its own handler in **group -1**, which runs before the ConversationHandler in every state, answers the message and raises `ApplicationHandlerStop`. The patient's conversation state is left exactly as it was, so a pain score typed during an intake no longer feeds the intake LLM and one typed in a therapist chat is no longer forwarded to the therapist. `start()` no longer owns the follow-up. | patient_bot/followup.py (new) · main.py · patient_bot/start.py | `tests/bot/test_followup_routing.py` (6 tests) |
| B4 | The appointment row is now written **before** the calendar work, inside `BEGIN IMMEDIATE`, after re-checking the hour; a clash raises `SlotTaken` and the patient is told the time was just taken and asked to pick another (`bot_slot_taken`, both languages). A partial unique index `ux_appointments_active_slot` backs it for every writer, and the dashboard's manual booking answers **409** instead of 500. Because the row is saved first, a booking is no longer lost when Google Calendar fails — and the hour is no longer released for a booking that never happened. | services/appointments.py · db.py · schedule.py · web/routers/api/appointments.py | `tests/bot/test_double_booking.py` (8 tests) |
| B9 | A Redis failure after the therapist has already received the message is logged, not reported to the patient as "Could not reach the therapist"; only a real send failure says that. Clearing the intake cache is best-effort too, so a Redis outage cannot swallow a booking confirmation. | patient_bot/therapist.py · schedule.py | `tests/bot/test_relay_failures.py` (2 tests) |

Coverage note: this task also added the first tests for the booking and cancel flows
(`tests/bot/test_booking_flow.py`, `tests/bot/test_cancel_flow.py`). Importing those modules in
tests grew the measured codebase from 4,782 to 5,677 statements — roughly 900 statements of
already-untested code that coverage simply could not see before.

Still open from the list above: B6, B8, B10, B11, B14 (Phase 2.2c), B13 (Phase 3.1), B15-B17.


## 1c. Fixed in Phase 2.2c (never answer with silence)

| # | What changed | Where | Test |
|---|---|---|---|
| B6 | `/start` is a clean reset: `clear_in_flight()` drops the half-finished booking (day, time, week, intake counter, cancel list, flow marker) and the patient is told it was not saved. Their chosen therapist survives, because that outlives any single flow. A patient whose media or stray message fell through to `start()` now gets the same clear answer instead of a silent abandon. | patient_bot/commands.py (new) · patient_bot/start.py | `tests/bot/test_robustness.py` |
| B8 | Both applications install `on_error`: it logs the failure with its traceback, answers a pending callback query so the button stops spinning, and replies with `bot_error` plus a working menu. It is defensive throughout — an error for something that is not an update has nobody to answer, and a reply that itself fails is logged rather than raised. | errors.py (new) · main.py · therapist_bot/main.py | 4 tests |
| B11 | `stale_button` is registered both as a conversation **entry point** (a button pressed with no conversation state — the position every keyboard is in after a restart, since state lives in memory) and as the last **fallback** (a button no state handler claimed). It answers the query, replaces the dead keyboard with the menu and returns `SELECTING`. | errors.py · main.py | 2 tests |
| — | `/cancel` stops any flow and returns to the menu; `/help` lists the commands without touching the conversation state. A test pins `allow_reentry=False`, which CLAUDE.md calls critical. | patient_bot/commands.py · main.py | 4 tests |

New strings in both locales: `bot_error`, `bot_button_expired`, `bot_cancelled_flow`,
`bot_booking_dropped`, `bot_help`.

Still open from the list above: B10 timeout (needs Q9) and B14 Bot lifecycle → Phase 2.2d;
B13 → Phase 3.1; B15-B17.


## 1d. Fixed in Phase 2.2d (idle flows and bot clients)

| # | What changed | Where | Test |
|---|---|---|---|
| B10 | The ConversationHandler now has `conversation_timeout` = `ZF_CONV_TIMEOUT_MINUTES` (default **30**, the Q9 proposal for booking/intake; `0` = never expire). A `TIMEOUT` state handler clears the unfinished flow, tells the patient (`bot_timed_out`, both languages) and ends the conversation. A therapist reply still reaches the patient afterwards, because it is sent by the patient bot directly, not through conversation state. `python-telegram-bot[job-queue]` is now in the lockfile — without APScheduler PTB only warns and nothing ever expires. | patient_bot/timeout.py (new) · main.py · zenflow/settings.py · requirements | `tests/bot/test_timeout_and_lifecycle.py` |
| B14 | Neither relay module builds a `Bot(token=...)` at import time any more. `bot.main.wire_bots()` points them at the two running applications' own clients, which PTB initialises, rate-limits and shuts down. If wiring did not happen, both sides say so instead of failing. | main.py · patient_bot/therapist.py · therapist_bot/handlers.py | 3 tests |

**Q9, partially:** PTB has one timeout per conversation, so the proposed 24 h for an open therapist
chat is not separately configurable — a chat also closes after 30 idle minutes. Nothing is lost
(therapist replies still arrive; the patient can reopen the chat from the menu), but if the clinic
wants chats to stay open longer, raise `ZF_CONV_TIMEOUT_MINUTES` or say so and the relay can get
its own conversation.

Every finding ranked for Phase 2.2 is now closed. Still open: B7's permanent media policy (Q6),
B13 → Phase 3.1, B15-B17.

---

## 2. Handler inventory

| Handler | Reachable from | Returns | user_data reads / writes | Notable behaviour under (a)–(h) |
|---|---|---|---|---|
| `start` | entry points, fallbacks (/start, **any** unmatched update) | SELECTING / THERAPIST_SELECT | r: selected_therapist · w: selected_therapist, therapist_flow | consumes follow-up only here (B3); not a reset (B6); swallows media (B6/B7) |
| `back_to_main` | THERAPIST_SELECT, SCHEDULE_WEEK, CANCEL_SELECT | SELECTING | r: selected_therapist | — |
| `change_therapist` | SELECTING | THERAPIST_SELECT / SELECTING | w: − selected_therapist | lang hard-coded en (B15) |
| `show_therapist_choice` | SELECTING (schedule) | SCHEDULE_WEEK / THERAPIST_SELECT / SELECTING | r/w: selected_therapist, therapist_flow | reads stale in-memory THERAPISTS (B5) |
| `select_therapist_and_continue` | THERAPIST_SELECT | THERAPIST_INPUT / SELECTING / SCHEDULE_WEEK | w: selected_therapist · pop: therapist_flow | trusts callback id without re-checking active |
| `show_week_choice` | SCHEDULE_WEEK (back), internal | SCHEDULE_WEEK | r: selected_therapist | — |
| `show_days` | SCHEDULE_WEEK, SCHEDULE_HOUR (back) | SCHEDULE_DAY / SCHEDULE_WEEK | r/w: selected_week · r: selected_therapist | Redis/GCal errors caught in services |
| `show_hours` | SCHEDULE_DAY | SCHEDULE_HOUR / SCHEDULE_DAY | w: selected_day | slot not reserved (B4) |
| `confirm_appointment` | SCHEDULE_HOUR | INTAKE_CONFIRM | r: selected_day · w: selected_time | no re-check (B4) |
| `start_intake` | INTAKE_CONFIRM | INTAKE | w: intake_count | Redis down → exception, silence (B8/B9) |
| `skip_intake` | INTAKE_CONFIRM | SELECTING | r: selected_day/time/therapist · clear | books without re-check (B4) |
| `handle_intake_answer` | INTAKE (TEXT) | INTAKE / SELECTING | r/w: intake_count · clear at 5 | follow-up answers eaten (B3); media exits intake (B6); pipeline fire-and-forget (B13) |
| `show_appointments` | SELECTING (cancel) | CANCEL_SELECT | w: apts_to_cancel | — |
| `confirm_cancel` | CANCEL_SELECT | SELECTING | r: apts_to_cancel · clear | IndexError on stale list (B8) |
| `show_therapist_for_contact` | SELECTING (therapist) | THERAPIST_INPUT / THERAPIST_SELECT / SELECTING | r/w: selected_therapist, therapist_flow | English only (B15) |
| `start_relay` | THERAPIST_INPUT (TEXT) | THERAPIST_RELAY / SELECTING | r: selected_therapist | Markdown failure (B2); misleading error on Redis failure (B9); wrong-therapist fallback (B12) |
| `relay_to_therapist` | THERAPIST_RELAY (TEXT) | THERAPIST_RELAY | r: selected_therapist | as above; follow-up answers forwarded (B3); media exits relay (B7) |
| `end_chat` | THERAPIST_RELAY | SELECTING | — | leaves `current:{therapist}` (B1) |
| therapist `start_therapist` | /start | — | — | stale THERAPIST_MAP (B5) |
| therapist `handle_therapist_message` | any TEXT | — | — | stale map (B5); TEXT only (B7) |
| therapist `_handle_relay` | known therapist | — | — | wrong-patient routing (B1); Markdown (B2); unguarded Redis (B8) |
| therapist `_handle_registration` | 8-char code | — | — | mutates globals of this process only (B5); no rate limit (B17) |

---

## 3. Proposed Phase 2.2 scope (for agreement)

**Fix first (patient safety), each behind a failing test:**
1. B1 — relay routing: compare-and-delete `current:{therapist_id}` on end; **stop falling back to
   "current patient" when a reply's mapping is missing** (ask the therapist to reply to a newer
   message instead); refuse free-typing when more than one patient is active for that therapist.
   *Behaviour change to the relay → needs owner approval (Q8 below).*
2. B2 — send relay text as plain text (or MarkdownV2 with every user-supplied fragment escaped).
3. B3 — follow-up consumer as a group(-1) handler that runs before the ConversationHandler in any
   state.
4. B4 — re-validate the slot inside `book_slot`/`save_appointment` (atomic check-and-insert with a
   UNIQUE `(therapist_id, date, time)` on active appointments); tell the patient to pick again.
5. B5 — load therapists from the database at use time (short-TTL cache) instead of import-time
   globals.

**Then (robustness):** B6 `/start` = clean reset + "your booking was not finished" notice;
B8 global error handler with a graceful message; B9 separate send vs. mapping failures; B10
`conversation_timeout` with a friendly message; B11 answer stale callbacks; B12 no silent
therapist fallback; B14 shared initialised Bot client; `/cancel` and `/help`; a test pinning
`allow_reentry=False`.

**Needs a policy decision before any code:** B7 media (Q6) — 2.2a ships the safe interim
behaviour (refuse politely, forward nothing, store nothing); the answer to Q6 replaces it.

**Deferred:** B13 → Phase 3.1; B15 → i18n pass; B16 → typing indicator in 3.x; B17 → Phase 9.5.

---

## 4. Questions for the owner

- **Q6 (media, B7):** when a patient sends a photo/voice/document in a therapist chat — (a) relay
  it to the therapist as-is, (b) relay and also store it with the session (PHI storage), or
  (c) refuse politely ("please describe it in text or bring it to your session")? Same question
  for therapists sending media to patients.
- **Q8 (relay behaviour, B1):** may the therapist keep free-typing without replying to a specific
  message? Proposed: allowed only while exactly one patient chat is active for that therapist;
  otherwise the bot asks them to reply to the patient's message.
- **Q9 (timeout, B10):** how long may a patient sit in a flow before it resets — proposed 30 min
  for booking/intake and 24 h for an open therapist chat.
