# Retrospective — Phases 0–12 (Phase 13.4)

The plan asks for a retro at the end of every phase: **what broke, what the plan got wrong, what to
reorder.** This is written after Phase 12, so it collects them all, then the lessons that became
rules. The sources are PROGRESS's review logs, `docs/SECURITY_FINDINGS.md`,
`docs/FIX_VERIFICATION.md` and the PRs (#1–#124).

## Per phase

| Phase | What broke or was found | What the plan got wrong / missed | Lesson |
|---|---|---|---|
| 0 Foundations | Secrets and tokens committed in history (history rewritten, Q8); the developer's `.env` leaked into tests; a comment-as-value in `.env.example` (SF-009) | — | Pin the test environment before any import (conftest §1) |
| 0.5 Triage | Cross-tenant reads on every appointment route (IDOR, F6); a default `SESSION_SECRET` accepted | Good call: done before features | Authz checks belong in one helper (`resolve_owned_appointment`) |
| 1 Time & jobs | 6 + 8 + 8 review findings: 'ZZ' timestamps, dates shifted a day, a job queue that could resurrect cancelled jobs | The plan's one-liner "migrate the schedulers" hid most of the work | Review every queue/clock change adversarially; it found what tests did not |
| 2 Bot audit | 17 defects (B1–B17), the worst a therapist's reply reaching **the wrong patient** (B1) | — | Never guess a recipient; refuse instead |
| 3 Pipeline | "Regenerate points" raced the page's own poller | — | One source of truth for job status (the database) |
| 4 Clinical UI | A 1,967-line template with inline handlers made CSP impossible | The CSP could only be enforced in 9.4/SF-016, after the pages were rewritten | Inline-free markup should be a Phase 0 rule |
| 5–6 Google, follow-up | Follow-ups lost on restart (F1), wrong timezone window (F2), routed to the wrong chat (F3) | — | Durable jobs + idempotency keys, never fire-and-forget |
| 7 Channels & identity | `patient_id` was a Telegram id everywhere, negative for manual bookings | **Should have been earlier**: Phases 2–6 built on the old identity and needed a migration (7.2) | Model identity before building flows on it |
| 8 Observability | — | — | The audit trail paid off in 9.9 (erasure) and 10 (forensics) |
| 9 Security | XSS across dashboard pages (SF-021), OAuth state (SF-018), Redis without AUTH (SF-019), world-readable DB (SF-020) | 9.7 fixed XSS on the treatment page only, and missed the rest until 10 | A security fix searches for **every** instance, not the reported one |
| 10 Offensive | Spoofable `X-Forwarded-For` defeating the lockout (SF-022) | Proxy trust belonged in 9.5 (rate limits) | Attack your own rate limits from behind a proxy |
| 11 Tests & CI | **CI hung for 6 hours on every run from #67 to #104** (a frozen monotonic clock under freezegun on Linux); no branch protection until Phase 12 | CI arrived in 9.10, but was never seen green; nobody noticed | A CI job counts only after one green run; make it required the same day |
| V.1 Verification | The owner's own testing showed fixes "not really fixed": **15 tests passed with the fix removed** | The plan's DoD said "a failing test first" but never checked the test fails *without* the fix | Every fix gets a mutation spec (`tests/mutation/`); VERIFIED or it is not done |
| 12 AWS readiness | Postgres found ~12 SQLite-only bugs (incl. erasure lifting the audit guard the SQLite way; patient search on SQLite's bare columns); a test that dropped the double-booking index silently disabled the protection for later tests; `g4dn` is not offered in Tel Aviv; the scanner found that **no alarm email could ever have arrived** (SNS on the AWS-managed key) | "Webhooks enable horizontal scaling" is false for python-telegram-bot conversations (one bots replica, ADR-49); a "short-TTL cache" for the registry was unnecessary (ADR-47) | Run the suite on the target database early; scan IaC before trusting it |

## What the plan should reorder (for the next plan, or a rewrite)

1. **CI that is green and required, plus the mutation check, in Phase 0.** Every later phase relied
   on CI that, for 37 PRs, never finished.
2. **Patient identity (7.2) before the flows that store patients (2, 5, 6).**
3. **Markup without inline code (4.1b/c) before the CSP (9.4)**, and the CSP enforcing from day one.
4. **The Postgres suite (12.2.2) as soon as the schema is data (12.2.3).** It was the single largest
   source of real bugs in Phase 12.
5. **Proxy and IP trust inside rate limiting (9.5)**, not found by the attack phase.

## Lessons that became rules (all in CLAUDE.md "Key conventions")

- A fix is proven by undoing it: a mutation spec per fix (`python tasks.py verify-fixes`).
- SQL must run on SQLite and Postgres; CI runs every test on both.
- No module-level registries and no `while True` loops in services: the database, and `zenflow.periodic`.
- New docs go in CLAUDE.md's index, and quoted commands must exist (`test_docs_current.py`).
- Secrets only through the `SecretsProvider`; never in logs, argv, URLs printed by tools, or Terraform.
- Nothing is provisioned or paid for without the owner (Phase 12 rule); open decisions live in
  `docs/QUESTIONS_FOR_SHAI.md`.
- Tooling note, for whoever works on this next: in this environment, shell heredocs collapse `\\` to
  `\`, which broke several generated files. Write scripts with the editor, or build backslashes
  with `chr(92)`.

## Phase 13 itself

Documentation is now enforced, not hoped for. The open items are the owner's decisions; there is no
unfinished engineering task in the plan. Ongoing work after this point follows the same loop:
a branch, a PR, a mutation spec for every fix, a self-review comment, merged on green.
