# The audit trail (Phase 8.1)

"What happened to this patient's data, when, and who did it" — answerable from SQL.

Table: `audit_log`. Code: `web/services/audit.py`. Decision record: ADR-31.

---

## 1. What a row says

| Column | Holds |
|---|---|
| `ts` | when, as a canonical UTC instant |
| `actor_type` / `actor_id` | `therapist` / `patient` / `api` / `ai` / `system`, and which one |
| `action` | `appointment.created`, `session.completed`, `followup.answered`, … |
| `entity_type` / `entity_id` | what it happened to |
| `before_json` / `after_json` | **only the fields that changed**, redacted |
| `ip` / `user_agent` | for a request that came from a browser |
| `request_id` | ties the row to the access log line and to every log record of that request |

```sql
-- everything that ever happened to one appointment
SELECT ts, actor_type, actor_id, action, after_json
FROM audit_log WHERE entity_type='appointment' AND entity_id='42' ORDER BY id;

-- everything one therapist did last week
SELECT * FROM audit_log WHERE actor_type='therapist' AND actor_id='t1' AND ts >= '2026-03-01';
```

## 2. Append-only, by the database

```sql
CREATE TRIGGER audit_log_is_append_only_update BEFORE UPDATE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
-- and the same for DELETE
```

An UPDATE or DELETE raises. A trail its own application can rewrite is not evidence, so this is a
property of the database rather than a rule in a review checklist.

## 3. Who is acting

The actor lives in a context variable, so a repository three calls down does not have to be told:

| Actor | Set by |
|---|---|
| `therapist` | `web/app.py`'s request middleware, from the session (with IP and user agent) |
| `api` | the booking API's guard, from the API key's client name |
| `patient` | the bot when a patient books (`schedule._book`) or answers a check-in |
| `ai` | the generation pipeline, around its own writes |
| `system` | the default — jobs, sweeps, start-up backfills. The worker names the job |

```python
from web.services import audit

with audit.acting_as("ai", "gemma3:latest"):
    save_treatment_notes(apt_id, patient_id, diagnosis)
```

## 4. Recording

```python
audit.record("appointment.cancelled", "appointment", apt_id, before=row, after=updated)
```

- **`audit.changes(before, after)`** returns only what really changed, ignoring `created_at`,
  `updated_at` and `id` — a row shows the edit, not the whole record.
- **Secrets never land in it.** Any field whose name looks like a secret (`password`, `token`,
  `secret`, `key_hash`, `encrypted`) is stored as `<redacted>`, and the whole payload goes through
  `zenflow.logging.redact` as well. Payloads are capped at 4000 characters.
- **Recording never fails a change.** A failed insert is logged and swallowed: losing the note of
  an appointment must not lose the appointment.

## 5. What is recorded today

| Action | Written when | Actor |
|---|---|---|
| `appointment.created` | any booking — bot, dashboard or API | the one who booked |
| `appointment.cancelled` | any cancellation | the one who cancelled |
| `treatment_notes.updated` | notes saved, by a therapist or the AI pipeline | therapist / ai |
| `session.completed` | "Complete Session" (its final edits are in the same row) | therapist |
| `followup.answered` | each answer a patient gives the check-in | patient |
| `followup.recorded` | the therapist enters the outcome of a phone call | therapist |

`tests/integration/test_audit_log.py` keeps a list of the endpoints that change clinical data and
fails if one of them stops recording — so a new endpoint has to say where it belongs.

## 5a. Where a therapist sees it (8.5)

`audit.for_appointment(id)` is one session's whole story: every entity a session owns —
`appointment`, `treatment_notes`, `followup` — is recorded under the appointment's own id, so
the trail is one query. `web/services/history_view.py` turns it into the **Record history**
card (`partials/session_history.html`), shown on the live treatment page and the read-only
archive, with the session's model calls (8.2) summarised underneath.

A line says *who* (you, another therapist, the patient, ZenFlow AI, a named API client, the
system), *what* ("Notes updated", "Session completed"), *when*, and **which fields moved** —
never their values. The clinical text is already on the page; repeating it in a history card
would only spread the same sensitive words across more of the DOM and the print view.

## 6. Retention

Audit rows are part of the clinical record and are kept as long as it is. Nothing prunes them
today. A retention and erasure policy (including what a patient's right to erasure means for a
trail that exists to be unforgeable) belongs with the patient-data review — plan 9.9, owner
question Q5.
