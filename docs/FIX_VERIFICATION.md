# Fix Verification — every recorded fix, proven by undoing it

The owner asked for proof that the fixes recorded as done were really made. A fix only counts when
three things hold:

1. **its code is in the codebase** (not just described in a document);
2. **its regression test passes** with the fix in place;
3. **the same test FAILS when the fix is undone.** A test that still passes without the fix guards
   nothing.

`tests/mutation/run.py` checks all three for every fix in `docs/SECURITY_FINDINGS.md`,
`docs/BOT_AUDIT.md`, the review findings and `docs/PROGRESS.md`. Each fix has a spec in
`tests/mutation/specs.py`: the minimal edit that undoes it, and the tests that must catch it. The
runner applies the edit, runs the tests, and restores the file.

```bash
python tasks.py verify-fixes              # all of them (slow: ~170 test runs); needs a clean tree
python tests/mutation/run.py SF-018 B4    # just these
```

CI runs it weekly and on demand (`.github/workflows/verify-fixes.yml`).

## Result (2026-10-02)

**86 of 86 recorded fixes verified.** The audit itself turned up test gaps. The fix code was always
present, but these tests did not fail when it was undone. Each now does:

| Fix | What the test missed | Closed by |
|---|---|---|
| A3 session fixation | The forged session was planted on the `testserver` cookie domain. `http.cookiejar` files cookies for a dotless host under `testserver.local`, so the forged cookie was never sent and the test passed vacuously. | #109 |
| SF-018 OAuth state (registration) | Only the Calendar callback was tested, not the Google sign-up callback. | #109 |
| B7 media handlers | The handlers were tested, but not that the bots route media to them. | #109 |
| B4 double booking | The clash check and the unique index masked each other. | #109 (each layer alone) |
| B2 plain-text relay | The relay loop itself (`relay_to_therapist`) was untested. | #109 |
| F2 completed_at UTC | The service path only checked that a timestamp existed. | #109 |
| 7.4b WhatsApp window | Nothing checked *when* the window was stamped. | #109 |
| R1 notification time | No test at all. | #109 (runs the real JS under Node) |
| R0 `.env` DB path | No test at all. | #109 |
| SF-012 / SF-021 dashboard escaping | A `{# xss-reviewed #}` entry for `a.patient_name` (needed for a text-only sink) also approved the same name in `innerHTML`. Raw-HTML sinks (`insertAdjacentHTML`, `outerHTML`, …) bypassed the `${…}` rule. | this PR |
| SF-017 Markdown (treatment message) | Only the recommendation text was probed, not the AI-written category. | this PR |
| F7 default secret | The default secret is shorter than 32 characters, so the length check refused it and masked the equality check. | this PR |
| B5 therapist registry | Only the by-id map was checked; modules that hold the list never saw a reload. | this PR |
| B12 no reroute | The "no choice, two active therapists" case was untested. | this PR |
| R0 `.env` disabled in tests | A disabled `.env` and a missing file load the same thing, so the test could not tell them apart. | this PR |

## Every fix

| Fix | File | Without the fix |
|---|---|---|
| SF-005 | `web/app.py` | 12 failed, 46 passed, 1 warning in 20.38s |
| SF-006 (403 owner check) | `web/deps.py` | 1 failed, 15 passed, 3 warnings in 12.61s |
| SF-007 | `web/routers/api/treatment.py` | 1 failed, 7 passed, 1 warning in 6.65s |
| SF-013 | `web/routers/api/appointments.py` | 1 failed, 1 warning in 2.31s |
| SF-014 | `web/app.py` | 1 failed, 1 warning in 2.53s |
| SF-015 | `web/routers/auth.py` | 1 failed, 1 warning in 3.38s |
| F11 | `web/app.py` | 2 failed, 1 warning in 5.46s |
| A4 CSRF | `web/csrf.py` | 2 failed, 1 warning in 3.07s |
| A3 fixation | `web/session_policy.py` | 1 failed, 1 warning in 2.93s |
| A3 revocation | `web/session_policy.py` | 1 failed, 1 warning in 3.06s |
| F9a cookie Secure | `web/app.py` | 1 failed, 1 warning in 3.69s |
| F9c sign-in lockout | `web/routers/auth.py` | 1 failed, 6 warnings in 2.60s |
| F9c sign-up cap | `web/routers/auth.py` | 1 failed, 1 warning in 2.96s |
| A11 AI rate limit | `web/routers/api/treatment.py` | 1 failed, 1 warning in 2.25s |
| A11 message length | `web/routers/api/messages.py` | 1 failed, 1 warning in 2.72s |
| 5.1 open redirect | `web/routers/auth.py` | 1 failed, 9 passed, 1 warning in 6.59s |
| SF-018 calendar callback | `web/routers/auth.py` | 1 failed, 5 passed, 1 warning in 3.88s |
| SF-018 registration callback | `web/deps.py` | 2 failed, 4 passed, 1 warning in 4.07s |
| SF-011 escHtml quote | `web/static/js/treatment/main.js` | 1 failed, 4 passed in 1.50s |
| SF-011 advice text | `web/static/js/treatment/advice.js` | 1 failed, 4 passed in 1.66s |
| SF-012 alert banner | `web/templates/dashboard.html` | **test gap found → test added/fixed in this PR** (now fails as it should) |
| SF-021 dashboard names | `web/templates/dashboard.html` | **test gap found → test added/fixed in this PR** (now fails as it should) |
| SF-021 relay message text | `web/templates/messages.html` | 1 failed, 4 passed in 2.26s |
| SF-016 inline handler | `web/templates/settings.html` | 1 failed in 0.76s |
| SF-016 CSP enforce default | `zenflow/settings.py` | 1 failed, 2 warnings in 2.71s |
| SF-017 follow-up Markdown | `bot/services/followup_scheduler.py` | 1 failed, 1 passed, 1 warning in 1.04s |
| SF-017 treatment Markdown | `web/routers/api/treatment.py` | **test gap found → test added/fixed in this PR** (now fails as it should) |
| A8 point cap | `bot/patient_bot/services/ai_intake.py` | 2 failed, 3 passed, 1 warning in 5.69s |
| A8 certainty clamp | `web/routers/api/treatment.py` | 1 failed, 4 passed, 1 warning in 4.40s |
| A13 token redaction | `zenflow/logging.py` | 1 failed, 11 passed, 1 warning in 3.60s |
| T2 email redaction | `zenflow/logging.py` | 1 failed in 0.94s |
| A7 activation flood | `bot/therapist_bot/handlers.py` | 1 failed in 1.61s |
| SF-019 Redis AUTH | `zenflow/settings.py` | 1 failed in 0.91s |
| F7 default secret | `zenflow/settings.py` | **test gap found → test added/fixed in this PR** (now fails as it should) |
| F7 separate key | `zenflow/settings.py` | 1 failed in 1.09s |
| F9b X-Frame-Options | `web/csp.py` | 1 failed, 2 warnings in 2.54s |
| R0 legacy token | `web/gcal.py` | 1 failed in 1.93s |
| F12 crypto pin | `requirements.txt` | 1 failed in 0.63s |
| SF-009 .env.example | `.env.example` | 1 failed in 0.86s |
| Tenant scope required | `web/repositories/appointment_repo.py` | 8 failed, 16 passed, 3 warnings in 19.53s |
| B1a end_relay | `bot/patient_bot/services/relay.py` | 1 failed in 0.65s |
| B1c ambiguous free typing | `bot/therapist_bot/handlers.py` | 1 failed, 1 passed, 1 warning in 1.57s |
| B2 relay loop plain text | `bot/patient_bot/therapist.py` | 1 failed, 1 warning in 1.12s |
| B3 gate group | `bot/main.py` | 1 failed, 4 passed, 2 warnings in 6.90s |
| B3 gate stops | `bot/patient_bot/followup.py` | 7 failed, 6 passed, 2 warnings in 9.01s |
| B4 clash check (index dropped by test) | `bot/patient_bot/services/appointments.py` | 1 failed, 1 warning in 0.86s |
| B4 unique index | `bot/db.py` | 1 failed, 1 warning in 0.92s |
| B5 reload in place | `bot/config.py` | **test gap found → test added/fixed in this PR** (now fails as it should) |
| B6 /start says so | `bot/patient_bot/start.py` | 1 failed, 1 warning in 1.67s |
| B7 media wiring | `bot/main.py` | 1 failed, 1 warning in 4.68s |
| B8 error handler | `bot/main.py` | 1 failed, 1 warning in 3.95s |
| B10 timeout | `bot/main.py` | 1 failed, 1 warning in 3.56s |
| B11 stale buttons | `bot/main.py` | 1 failed, 1 passed, 2 warnings in 4.97s |
| B12 no reroute | `bot/patient_bot/therapist.py` | **test gap found → test added/fixed in this PR** (now fails as it should) |
| B13 queued pipeline | `bot/patient_bot/schedule.py` | 1 failed in 4.05s |
| F1 send_email therapist_id | `bot/services/followup_scheduler.py` | 1 failed, 1 warning in 2.79s |
| F2 completed_at UTC (endpoint) | `web/routers/api/treatment.py` | 3 failed, 1 warning in 4.09s |
| F2 completed_at UTC (service) | `web/services/treatment_service.py` | 1 failed, 9 passed, 1 warning in 3.73s |
| R3 re-completion | `bot/services/followup_jobs.py` | 1 failed, 1 warning in 3.55s |
| R3 stamp repair | `bot/services/followup_scheduler.py` | 1 failed, 1 warning in 1.40s |
| R3 Send Now clears queue | `web/routers/api/treatment.py` | 1 failed, 1 warning in 3.63s |
| R3 dead-letter alert | `bot/services/followup_jobs.py` | 1 failed, 1 warning in 3.23s |
| R3 reconcile window | `bot/services/followup_scheduler.py` | 1 failed, 1 warning in 1.37s |
| 7.4b label fallback | `bot/interfaces/whatsapp_channel.py` | 1 failed in 0.77s |
| 7.4b window on receipt | `web/routers/api/whatsapp.py` | 1 failed, 1 warning in 3.00s |
| 8.2 ai actor | `bot/services/pipeline_jobs.py` | 1 failed in 2.61s |
| 11.1 empty name | `web/services/appointment_service.py` | 2 failed, 1 passed in 1.00s |
| 11.1 RecursionError | `bot/patient_bot/services/ai_intake.py` | 1 failed, 1 passed, 1 warning in 3.84s |
| 11.1 diagnosis dict | `web/routers/api/treatment.py` | 1 failed, 1 warning in 3.71s |
| Bot Google token from DB | `bot/patient_bot/services/availability.py` | 2 failed, 2 passed (verified on master, after #108) |
| R0 .env off in tests | `zenflow/settings.py` | **test gap found → test added/fixed in this PR** (now fails as it should) |
| R0 db path from .env | `bot/db.py` | 1 failed in 0.52s |
| R1 clinic date | `zenflow/clock.py` | 1 failed in 0.55s |
| R1 date-only left alone | `zenflow/clock.py` | 1 failed, 1 warning in 0.88s |
| R1 notification time Z | `web/templates/base.html` | 1 failed, 1 passed in 0.92s |
| R2 job owner clause | `zenflow/queue.py` | 1 failed in 0.66s |
| R2 retry budget | `zenflow/queue.py` | 1 failed in 0.91s |
| R2 empty idempotency key | `zenflow/queue.py` | 1 failed in 0.62s |
| R2 worker schema | `zenflow/worker.py` | 1 failed in 0.85s |
| R2 timeout vs lock | `zenflow/worker.py` | 1 failed in 0.64s |
| R2 cancel releases | `zenflow/worker.py` | 1 failed in 0.69s |
| 4.2a Hebrew colours | `web/static/js/treatment/point-info.js` | 1 failed in 0.61s |
| 4.2a KI3 alias | `web/static/js/treatment/point-info.js` | 1 failed in 1.21s |
| F8 .rdb ignored | `.gitignore` | 1 failed in 0.65s |
| 9.9 erasure deletes channels | `zenflow/patient_erasure.py` | 3 failed, 11 passed, 1 warning in 5.12s |
| 9.9 backup encryption | `zenflow/db_backup.py` | 2 failed, 6 passed in 2.61s |

When you fix a bug, add its spec to `tests/mutation/specs.py`. If undoing your fix doesn't make a
test fail, the fix is unproven.
