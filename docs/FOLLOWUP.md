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
| The open conversation lives only in Redis (`zenflow:followup:conv:*`, 48 h), so a flush mid-check-in loses the answers already given | Open; moves into the `followups` table in 6.3 | — |

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
