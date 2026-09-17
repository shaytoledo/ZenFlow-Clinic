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
`auto`, for sessions the therapist never completed.

Until the owner decides, the behaviour is unchanged: only a completed session gets a follow-up.
After 6.3, the option will be built behind `ZF_AUTO_FOLLOWUP` (off by default). The reconcile
sweep would then enqueue a follow-up for past, active, uncompleted appointments.

## 2. Storage: the `followups` table (task 6.3)

There is one row per appointment (`appointment_id` is UNIQUE and cascades on delete). The
repository is `web/repositories/followup_repo.py`, and its schema is created by
`bot/db.init_db()`.

| Column | Meaning |
|---|---|
| `patient_id`, `therapist_id` | copied from the appointment |
| `channel` | `telegram`, or `none` for a manual booking or a negative patient id |
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
