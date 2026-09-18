# 24h follow-up and recommendations (Phase 6)

**Goal:** 24 hours after a completed session, the patient gets a short, structured check-in. The
result appears at the bottom of that session, and a patient who cannot be messaged raises a
therapist alert.

Recommendation delivery shares the same pipeline; its Google/Gmail side is in
`docs/GOOGLE_CONNECTION_UX.md`.

## 1. Why it was broken, and where each cause stands (task 6.1, 2026-09-17)

| Root cause (plan 6.1) | Status | Evidence |
|---|---|---|
| **F1**: the email fallback called `send_email(to, subject, body)` without the therapist id, so every email raised `TypeError` | Fixed in Phase 1.3 | `tests/integration/test_followup_jobs.py::test_manual_patient_email_fallback_passes_the_therapist_id_f1` |
| **F2**: `completed_at` was naive local time, compared with UTC, which shifted the 22–26 h window by the host's offset | Fixed in Phase 1.1. The window no longer exists: the job runs at `completed_at + 24h`, stored in canonical UTC | `tests/integration/test_followup_window.py::test_step1_goes_out_at_24h_in_every_zone` (3 clinic zones, T+23h59 → nothing, T+24h → once). Ruff DTZ bans naive `now()` |
| **F3**: answers were consumed only inside `start()`, so answers typed during intake or a therapist chat were swallowed | Fixed in Phase 2.2b: a handler in group −1 answers first and stops the update | `tests/bot/test_followup_routing.py`, `docs/BOT_AUDIT.md` B3 |
| The scheduler ran only in the bot process's `post_init`, so a restart between completion and T+24h lost the follow-up | Fixed in Phase 1.3. "Complete Session" enqueues a durable job (ADR-20/21); the worker runs in the bot process or as `python -m zenflow.worker`, and a job waits in the database until one runs | `test_followup_jobs.py::test_jobs_fire_at_24h_exactly_once` |
| The Redis "already sent" key was the only dedupe, so a flush caused a re-send | Fixed in Phase 1.3. `treatment_notes.followup_sent_at` is checked inside the job; Redis is a secondary guard | **new** `test_followup_window.py::test_a_redis_flush_neither_loses_nor_repeats_step1` (flush before and after the send, plus a reconcile sweep) |
| Only explicitly completed sessions get a follow-up (`completed_at IS NOT NULL`) | **Owner decision Q7** (below) | — |
| The open conversation lived only in Redis (`zenflow:followup:conv:*`, 48 h), so a flush mid-check-in lost the answers already given | Fixed in 6.3: the conversation lives in the `followups` table | `tests/integration/test_followups_table.py::test_a_checkin_goes_from_scheduled_to_completed_in_the_database` (Redis flushed between two answers) |

Removed as dead code:

- **`_find_due_followups()` and `WINDOW_HOURS_MIN/MAX`:** the polling window that jobs replaced.
  Its F2 test now drives the real job instead.
- **`consume_followup_rating()`:** a wrapper with no callers.

### Q7: sessions that were never marked complete

The plan recommends **yes**: send a follow-up *N* hours after the appointment's end time, marked
`auto`, for sessions the therapist never completed. The option is built behind
**`ZF_AUTO_FOLLOWUP`** and is **off** until the owner decides.

**When it is on:**

- **Enqueue:** the reconcile sweep (every 30 min) finds each active appointment that:
  - ended within the last 48 h (its clinic-local start plus `AUTO_SESSION_MINUTES` = 60);
  - was never completed.

  For each one, `enqueue_auto_followup()` schedules the check-in 24 h after the end, with key
  `followup:{appointment}:auto` and the row marked `auto`. Patients who cannot be messaged get the
  6.4 call alert instead.
- **Send:** the job sends step 1 **unless** the session was completed meanwhile. Completing it
  clears `auto` and schedules the usual job, so the automatic one skips itself.
- **No second check-in:** an automatic check-in that already went out is never followed by a
  second one after a late completion. The regular job now also skips any check-in that is past
  `scheduled`.
- **The card** says "(Automatic: the session was not marked complete.)"

With the flag **off**, nothing changes; both paths are tested (`tests/integration/test_auto_followup.py`).

## 2. Storage: the `followups` table (task 6.3)

There is one row per appointment (`appointment_id` is UNIQUE and cascades on delete). The
repository is `web/repositories/followup_repo.py`, and its schema is created by
`bot/db.init_db()`.

| Column | Meaning |
|---|---|
| `patient_id`, `therapist_id` | copied from the appointment |
| `channel` | the patient's messaging channel (`telegram`), or `none` when they have none (`patient_channels`, Phase 7.2) |
| `status` | `scheduled` → `sent` → `in_progress` → `completed`. Also `expired` (step 1 never went out in time, or no answer within 48 h) and `no_channel` (cannot be messaged; alert in 6.4) |
| `auto` | 1 for the Q7 option (a session that was never completed); 0 today |
| `scheduled_for`, `sent_at`, `completed_at` | canonical UTC |
| `step` | the question the patient is on while the check-in is open |
| `pain_level` (0–10), `improvement_rating` (1–5) | CHECK-constrained |
| `side_effects` (JSON list), `sleep_quality` (`worse`/`same`/`better`), `adherence` (`yes`/`partly`/`no`) | filled by the extended check-in (6.2) |
| `free_text`, `ai_summary`, `needs_attention` | the patient's words, the 2-line summary and the red flag (6.2) |
| `conversation_json` | the transcript |
| `source` | `patient`, or `therapist_manual` (the therapist entered the outcome) |

**Lifecycle:**

- **Scheduled:** `enqueue_followup()` (at "Complete Session", and in the reconcile sweep) calls
  `schedule()`. Completing the session again moves only a row that has not gone out.
- **Sent:** after step 1 is delivered, `mark_sent()` records it.
- **In progress:** each answer is saved with `save_progress()`. **The open conversation is read
  from this table** (`open_for_patient`: `sent`/`in_progress` rows sent within the last 48 h), so a
  restart or a Redis flush mid-check-in loses nothing.
- **Completed:** the last answer calls `complete()`.
- **Expired:**
  - `reconcile()` → `expire_stale()` expires check-ins left open for 48 h;
  - a job that fires after the send window calls `expire_unsent()`.
- **Manual entry:** "Manual feedback" calls `record_manual()`. The row becomes a completed
  `therapist_manual` row, unless the patient has already answered.

**Back-compat, for one release:** the columns `treatment_notes.followup_conversation`,
`followup_rating`, `followup_sent_at` and `manual_feedback_*` are still written in parallel, and the
pages still read them. A conversation opened in Redis before this change is **adopted** into its
row on the next answer, and the Redis copy is dropped.

**Backfill:** `backfill_from_treatment_notes()` runs at every start-up. It only inserts
(`INSERT OR IGNORE`) and never changes `treatment_notes`. Each completed or followed-up session
gets a row:

| Existing data | New row |
|---|---|
| a patient conversation or rating | `completed` (answers and transcript copied) |
| only a therapist entry | `completed`, `therapist_manual` |
| sent, unanswered, within 48 h | `sent` |
| sent, unanswered, older | `expired` |
| completed, not sent, within 48 h | `scheduled` for `completed_at + 24h`, or `no_channel` for a manual booking |
| completed, not sent, older | `expired` |

A legacy row that breaks a CHECK constraint is skipped, not fatal.

## 3. The check-in (task 6.2)

The script lives in `bot/services/followup_checkin.py`. It is deterministic and does no I/O; the
questions and scores never come from the AI. English and Hebrew wording follow the therapist's
language.

| Step | Question | Answer | Buttons | Typed fallback |
|---|---|---|---|---|
| 1 | Pain or discomfort now | 0–10, required | 0…10 | a number |
| 2 | Change since treatment | 1–5 with labels, required | "1 — Much worse" … "5 — Much better" | a number |
| 3 | Side effects | none / soreness / bruising / dizziness / fatigue / **fainting** / other, several allowed | toggles marked ✓, then **Done**; **None** answers at once | words in either language ("sore, tired", "סחרחורת") or the option numbers |
| 4 | Sleep since treatment | worse / same / better, optional | three choices + **Skip** | words or 1–3 |
| 5 | Followed the lifestyle advice | yes / partly / no | three choices | words or 1–3 |
| 6 | Anything for the therapist | free text, optional | **Skip** | any text; "skip" / "דלג" |

**Buttons** carry `fu:<appointment>:<step>:<value>`, at most 64 bytes.

- A handler in group −1 (`bot/patient_bot/followup.py`) answers the button before any other
  handler sees it.
- It removes an answered question's buttons, and redraws them after a toggle.
- A button from an older message, or from another check-in, only gets a toast: "That question
  was already answered." It changes nothing.
- A typed answer that doesn't fit gets a short correction together with the same buttons, and
  the step does not move.

**Red-flag rule** (safety first):

- **Triggers:** pain **≥ 8**, change **= 1** (much worse), or **fainting** reported.
- **Evaluated on each answer, not at the end.** The first match:
  - sets `followups.needs_attention`;
  - creates **one** persistent `followup_red_flag` notification (severity `error`, "<patient>
    needs attention after their treatment", with the reasons), deduplicated per appointment.
- Later matches add nothing.
- `needs_attention` is never cleared by later answers.

**Summary:** `followup_checkin.summary()` writes two fixed lines from the answers, for example:

```
Pain 3/10 · Much better (5/5) · advice followed: Partly
side effects: Soreness · no note
```

At the end, `summarize_checkin()` asks the AI to reword those two lines and nothing else. The
prompt holds only the fixed summary, with "use only the facts below". The reply is kept only if it
arrives within 20 s and is at most two non-empty lines and 300 characters. Anything else (a
timeout, an error, a longer reply) keeps the fixed wording. The result is stored in
`followups.ai_summary`.

**Delivery:**

- Step 1 goes out through the channel interface with `extra={"buttons": …}`.
- `TelegramChannel` turns that into `reply_markup.inline_keyboard`, and `send_to_patient` accepts
  `reply_markup`.
- The parallel `treatment_notes.followup_conversation` JSON also carries `side_effects`,
  `sleep_quality`, `adherence`, `summary` and `needs_attention`.

**Owner check (Q11):**

- Please confirm the questions.
- Please confirm the red-flag thresholds.
- "Fainting" was added as its own option so that the rule stays deterministic. The plan said
  "severe dizziness/fainting", and "dizziness" alone does not raise the flag.

## 4. When the patient cannot be messaged (task 6.4)

**Unreachable** means the patient has no messaging channel (`patient_repo.messaging_contact()`
is `None`; since Phase 7.2 this is a property of the patient, not of a manual booking or the id).
Their check-in becomes a phone call.

- **Row:** at enqueue time ("Complete Session", or the reconcile sweep), `followup_repo.schedule()`
  stores the row as `channel = none`, `status = no_channel`. At T+24h the job skips the send, so
  no message is attempted.
- **Alert:** `followup_jobs._alert_if_unreachable()` creates **one** persistent
  `followup_no_channel` notification (severity `warning`): "Follow-up due for <patient> — no
  messaging channel", with the due time.
  - It is raised **once per appointment, ever** (`notification_repo.ever_raised`). Completing the
    session again, or a sweep, never brings back an alert the therapist already resolved.
- **Links:** `/api/notifications` gives every alert tied to one of the therapist's sessions a
  `link` to that session (`notification_service.session_link`, each path part percent-encoded).
  A no-channel alert links straight to the form (`#manual-feedback-card`). The bell renders the
  link only when it starts with `/treatment/`, and escapes it for the attribute.
- **In the session:** the Manual Patient Feedback card is always on the page. For a `no_channel`
  check-in it also shows "This patient cannot be messaged, so the 24h follow-up is a phone
  call… Due: <time>" (`#mf-due`). `GET /api/treatment-notes/…` returns
  `followup: {status, channel, scheduled_for, source}`, and the time is filled in with
  `textContent`.
- **Recording the outcome** (a rating or text):
  - the row becomes `completed` / `therapist_manual` (6.3);
  - the `followup_no_channel` alert is resolved.

  An empty form records nothing.
- **Dashboard banner:** "Manual Follow-Up Required" (`GET /api/my/alerts`) now comes from the
  database: this therapist's `no_channel` check-ins that are due, each with a message and a link.
  The Redis list it used to read (`zenflow:alerts:*`) had no writer left.
  - The banner is built from DOM nodes: patient names come from Telegram profiles and are never
    parsed as markup. The old code wrote them into `innerHTML`.
  - Dismissing the banner hides it for this visit only. It returns while calls are still due.

## 5. The follow-up card (task 6.5)

**One renderer, two pages.**

- `web/services/followup_view.py` turns a `followups` row into translated text values.
- `templates/partials/followup_card.html` renders them, styled by `static/css/followup.css`
  (`fu-*` classes, design tokens, logical properties).
- The **treatment page** (at the bottom, above the Manual Patient Feedback form) and the
  **session archive** both include the partial.
- It is rendered on the server and escaped by Jinja. The old client renderer
  (`renderFollowupResults`, which read the `treatment_notes` JSON) is gone.

| State (row status) | Badge | What the card says |
|---|---|---|
| `scheduled` | Scheduled | "The check-in goes out <clinic time>." |
| `sent` / `in_progress` | Awaiting reply | "Sent <time> — waiting…", or "— N of 6 questions answered so far", with the answers given so far |
| `completed` | Completed | "Answered <time>" (or "Recorded by the therapist <time>"), then the answers, summary, note and transcript |
| `expired` | No reply | "Sent <time>; the patient did not finish within 48 hours" (or "was not sent in time"), with any partial answers |
| `no_channel` | Phone follow-up | "This patient cannot be messaged — call them and record the outcome. Due <time>", plus a **Record the outcome** link to the form |

**Contents:**

- **Pain:** a native `<meter>` (0–10, low 4, high 7, optimum 0) with the value next to it.
- **Chips:** change, side effects, sleep and advice followed. Each has a tone: good / fair /
  poor / neutral.
- **Summary** (6.2) and the **note**, labelled "Therapist's note" for a manual entry.
- **Transcript:** a collapsed `<details>`. The bot's Telegram Markdown markers are dropped; the
  patient's words are shown as typed.
- **Red flag:** when `needs_attention` is set, a `role="alert"` banner comes first, with the
  reasons spelled out ("pain 9/10", "much worse since the treatment", "reported fainting").

Times are shown in the clinic's time zone. Every string is in `locales/{en,he}.json` (`fu_*`),
and Hebrew mirrors from `dir="rtl"` alone.

## 6. Recommendation delivery and the delivery log (task 6.6)

**The chain, end to end.**

1. "Complete Session" queues the enabled recommendations for T+24h
   (`recommendations.dispatch`, idempotency key `recommendations:{appointment}:{send_at}`).
2. The job routes the delivery:
   - Telegram → the patient bot;
   - email → the therapist's Gmail (Phase 5: it waits for Google, or retries a refused token);
   - neither → a persistent "missing contact" alert.
3. On delivery, `mark_recommendations_delivered()` stamps `recommendations_sent_at` and clears the
   queue entry in one statement (5.5).

**Why it is idempotent:**

- the job only acts while the queue entry exists;
- "Send Now" clears the queued copy;
- completing the session again queues nothing once the recommendations were sent.

F1 (email without the therapist id) was fixed in Phase 1.3.

**`message_log`** (started here, finished in 8.3 — full description in `docs/MESSAGE_LOG.md`;
`web/repositories/message_log_repo.py`) holds one append-only row per message between the clinic
and a patient. Columns: `ts`, `direction` (`out` / `in`), `channel` (`telegram` / `whatsapp` /
`email`), `patient_id`, `therapist_id`, `appointment_id`, `kind` (`confirmation` /
`recommendations` / `followup` / `relay`), `status` (`sent` / `failed`), `provider_message_id`
(the Telegram message id or the Gmail id), and `error`.

**What it records:**

- **Sends:** queued recommendations, "Send Now" (Telegram and email), and the follow-up's
  step 1.
- **Failures:** each failed attempt gets its own row, with the error **redacted**
  (`zenflow.logging.redact`) and shortened to 300 characters, because therapists read it.
- **Not recorded:** the recipient address (the appointment holds it); a send that is only
  waiting for Google (the "waiting for Google" alert covers that).
- **Never blocks delivery:** after a successful send, the row is written best-effort, so a
  logging failure cannot cause a retry, and so no second message.

**What the therapist sees:** a "Messages" list (`partials/delivery_log.html`, `fu-log-*`
classes) under the follow-up card, on the treatment page and in the session archive. Each line
shows the time (clinic zone), what it was, the channel, the status — or "Received" for what the
patient sent back (8.3) — and the error if there was one. A run of identical entries is one line
with a count, so a four-question check-in does not become four lines. It lists only the
therapist's own rows and is hidden when there is nothing to show.

The rest of plan 8.3 (booking confirmations, relay messages, an inbound direction) comes with
Phase 8.
