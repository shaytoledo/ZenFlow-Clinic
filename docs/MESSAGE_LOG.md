# The message log (Phase 8.3)

Every message between the clinic and a patient, in both directions — what it was, on which channel,
and whether it arrived. Metadata only: the words live in the conversation, never in this table.

Table: `message_log`. Code: `web/repositories/message_log_repo.py`. Started in 6.6 (deliveries),
widened in 7.3 (booking confirmations) and finished in 8.3 (the relay, and `direction='in'`).

---

## 1. What a row says

| Column | Holds |
|---|---|
| `ts` | when, as a canonical UTC instant |
| `direction` | `out` — the clinic sent it; `in` — the patient sent it |
| `channel` | `telegram`, `whatsapp`, `email` |
| `patient_id` / `therapist_id` | who it was between (`patients.id`, never a Telegram id) |
| `appointment_id` | the session it belongs to, when it has one |
| `kind` | `confirmation`, `recommendations`, `followup`, `relay` |
| `status` | `sent` (it reached the provider — a received message arrived by definition) or `failed` |
| `provider_message_id` | the Telegram message id, the Gmail id, the Cloud API id |
| `error` | why it failed — redacted, at most 300 characters, because therapists read it |

```sql
-- the conversation around one session
SELECT ts, direction, kind, channel, status FROM message_log
WHERE appointment_id=42 ORDER BY ts;

-- what failed to reach patients yesterday, and on which channel
SELECT channel, kind, COUNT(*) FROM message_log
WHERE status='failed' AND ts >= datetime('now','-1 day') GROUP BY 1, 2;
```

## 2. What is recorded

| Kind | Direction | Written when |
|---|---|---|
| `confirmation` | out | the booking confirmation job sends (or fails to send) it — 7.3 |
| `recommendations` | out | the queued delivery, and "Send Now" (Telegram or email) — 6.6 |
| `followup` | out | the 24h check-in's first message — 6.6 |
| `followup` | in | each answer the patient gives the check-in — 8.3 |
| `relay` | in | a patient's message to their therapist — 8.3 |
| `relay` | out | the therapist's reply — 8.3 |

**Not recorded:** intake answers (they belong to the intake record — the clinic is not delivering
anything), the check-in's follow-up questions (one delivery, already recorded), the recipient's
address (the appointment holds it), and a send that is only waiting for Google (the "waiting for
Google" alert covers that).

**Never the message text.** A delivery trail says that a message happened; the relay history and
the check-in transcript hold what was said. `test_the_log_never_holds_what_was_said` pins it.

## 3. Never at the cost of a message

Every write is best effort and happens **after** the send:

- an outbound row is written once the provider accepted the message, so a logging failure can
  never trigger a retry and a second message;
- `message_log_repo.record_relay()` swallows its own errors — the therapist already has the
  message;
- a failed send gets its own row per attempt, with the error redacted.

The relay call sites take the patient's **channel identity** (the Telegram user id the bot has) and
`record_relay` resolves it to `patients.id` through `patient_channels` (ADR-28). Somebody who never
booked still leaves a row, with `patient_id` null.

## 4. What the therapist sees

`partials/delivery_log.html` (`fu-log-*` classes, shared by the treatment page and the session
archive) lists the session's messages in both directions, newest last, own rows only. A run of
identical entries is one line with a count — a four-question check-in reads as
"24h check-in · Telegram · Received ×4", not as four lines. Received messages are rendered in the
quieter tone (`fu-dir-in`).

Relay messages have no `appointment_id`, so they are part of the trail and of any operational
query, but they do not appear on a session's card.

## 5. Widening the enumerations

`kind` and `channel` are CHECK constraints, and SQLite cannot alter one. `widen_kinds(conn,
migration, kind)` rebuilds the table once, copying every row, and records the migration:
`0002_message_log_kinds` (7.3, confirmations and WhatsApp) and `0003_message_log_relay` (8.3). A
second start-up does nothing. Adding a kind in future is one call in `create_schema`.

## 6. Retention

Rows are small and are kept with the clinical record; nothing prunes them today. A retention policy
belongs with the patient-data review (plan 9.9, owner question Q5) — with the observation that
this table holds no clinical content, only the fact that a message existed.
