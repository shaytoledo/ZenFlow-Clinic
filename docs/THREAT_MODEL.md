# ZenFlow — Threat Model (STRIDE)

Phase 10.1. How ZenFlow can be attacked, who would attack it, and where each attack is stopped (with
the test that proves it). Pairs with `docs/SECURITY_FINDINGS.md` (findings) and the Phase 9 hardening.

> **Rules of engagement for the offensive tests (Phase 10):** local and staging only. Never
> production, never third-party services. The attack scenarios below live as automated tests in
> `tests/security/` and run under `python tasks.py security`.

## Assets (what an attacker wants)

| Asset | Where it lives | Why it matters |
|---|---|---|
| Clinical notes, TCM diagnosis | `treatment_notes` (SQLite) | health data |
| Intake transcripts | `intake_sessions` (SQLite), Redis (30 min) | health data, patient's own words |
| Google OAuth tokens | `data/google_tokens/{id}.json` (Fernet-encrypted) | act as the therapist in Google |
| Telegram bot tokens | env / `SecretsProvider` | impersonate the clinic's bots |
| Relay message history | Redis | patient⇄therapist conversation |
| Therapist credentials | `therapists.password_hash` (pbkdf2) | dashboard access |
| Patient identities | `patients`, `patient_channels` | who is a patient, their channel ids |

## Entry points

Public `/register` + sign-in · the `zf_session` cookie · both bots (polling today, webhooks later) ·
the booking API `/api/v1` · Redis · the SQLite file · logs · the Google OAuth callback.

## Trust boundaries

```
Internet ──TLS──> [ web dashboard / OAuth callback / booking API ]
Patient ──Telegram TLS──> [ patient bot ] ──Redis relay──> [ therapist bot ] ──> Therapist
                                    │
                          [ SQLite file · Redis · google_tokens ]   (host / private-network boundary)
                                    │
                          [ Ollama (local) · Google · Anthropic · Telegram ]  (outbound TLS)
```

The sharp boundaries: **patient → clinic** (a patient is untrusted), **therapist A → therapist B**
(one tenant must never reach another's data), **network → host**, and **repo/host access → secrets**.

## Attacker profiles

| Profile | Capability | Primary defences |
|---|---|---|
| **Curious patient** | a normal Telegram user poking the bot | conversation state machine, no cross-patient data in replies |
| **Malicious patient** | crafts intake/names to inject | output escaping (SF-011/017), LLM output bounds (9.8), Markdown escaping |
| **Rogue therapist tenant** | a real, signed-in therapist reaching for another's data | per-tenant scoping on every route (F6, 9.1), object-level checks |
| **Network attacker** | on-path between browser/bot and server | TLS everywhere + cert verification (9.11), HSTS, CSRF, session flags |
| **Repo/host insider** | can read the repo or the box | secrets never in git (gitleaks) or logs (redaction); tokens encrypted at rest |

## STRIDE by boundary (summary)

- **Spoofing** — session fixation/forgery (9.2), CSRF (9.3), bot activation-code guessing (9.5), API-key auth for `/api/v1` (7.3).
- **Tampering** — parameterised SQL (no injection), input length caps (9.7), append-only `audit_log` (8.1).
- **Repudiation** — `audit_log` records who changed what (8.1); `message_log` records deliveries (8.3).
- **Information disclosure** — tenant scoping (F6), output escaping (F10), TLS in transit (9.11), secrets redaction/encryption (9.6).
- **Denial of service** — rate limits + lockout + flood control (9.5), bounded free text (9.7) and LLM output (9.8).
- **Elevation of privilege** — activated-session requirement, `require_active_therapist`, no anonymous state-changing routes (SF-015).

## Attack scenarios A1–A14

Each row is (or becomes) a test in `tests/security/`. **Covered** = a regression test exists;
**Deployment** = an operational/Phase-12 control; **Follow-up** = a gap tracked for a later 10.2 pass.

| # | Scenario | Attacker | Status |
|---|---|---|---|
| **A1** | Cross-tenant IDOR on every `/api/**` object (F6) | rogue tenant | **Covered** — `test_tenant_isolation.py`; object-level checks (9.1) |
| **A2** | Unauthenticated access to any route (missing-decorator sweep) | anyone | **Covered** — `test_route_inventory.py` drives every route anonymously (generated from the route table) |
| **A3** | Session: fixation, no rotation, cookie without flags, cookie forged with the default `SESSION_SECRET` (F7) | network / anon | **Covered** — `test_session_policy.py`; default secret refused outside dev (settings) |
| **A4** | CSRF on complete-session / send-recommendations / disconnect-google | network | **Covered** — `test_csrf.py` (double-submit, walk-the-routes guard) |
| **A5** | Stored XSS: patient name / intake / notes via `innerHTML` (F10) | malicious patient | **Covered** — `test_treatment_page_xss.py`; `escHtml`, no inline handlers (SF-011) |
| **A6** | Telegram relay cross-tenant: reply to a recycled/guessed message id (F4) | rogue tenant | **Covered** — `test_relay_isolation.py`; mapping stores `therapist_id`, 24h expiry |
| **A7** | Registration-code brute force (8 chars) | malicious patient | **Covered** — per-user flood on code entry (9.5, `test_abuse_limits.py`); search-space `[A-Z0-9]{8}` ≈ 2.8×10¹² + single-use code (see below) |
| **A8** | Prompt injection through intake → poisoned/oversized diagnosis, JSON breakout (9.8) | malicious patient | **Covered** — `test_llm_injection.py` (bounds) + `test_ai_parser_fuzz.py` (8000 malformed/truncated/injected/deep-nested inputs prove the parsers never raise → no 500); output bounds (ADR-42) |
| **A9** | Redis reachable without auth → read relay history, forge mappings, flush the "already sent" guard | network | **Covered** — settings refuse a non-local `REDIS_URL` that lacks TLS **or** a password/AUTH (`test_transit.py`); on a private box Redis is localhost-only (exempt) |
| **A10** | SQLite file perms / path traversal / world-readable WAL | host insider | Path traversal **Covered** (`storage.check_key` + `is_relative_to`); **Deployment** — file permissions are an OS/host control (Phase 12) |
| **A11** | Resource exhaustion: unbounded intake → Ollama pinned; concurrent regenerate; poller amplification | malicious patient / tenant | **Covered** — AI per-therapist rate limit + input caps (`test_abuse_limits.py`, `test_input_limits.py`); regenerate serialised by the per-appointment lease |
| **A12** | OAuth: open redirect on `redirect_uri`, missing state, scope creep, token replay after disconnect | network | **Covered** — `state` is now generated and verified on both callbacks (SF-018, `test_oauth_state.py`); the `next` redirect target is validated (`test_google_oauth_next.py`); `redirect_uri` is a fixed server value; disconnect deletes the token |
| **A13** | Secrets in git history and logs (F8) | repo insider | **Covered** — gitleaks over history in CI (9.10, `.github/workflows/ci.yml`); redaction (`zenflow/logging.py`); tokens gitignored |
| **A14** | Availability/booking race: double-book one slot from two channels | two patients | **Covered** — `save_appointment` raises `SlotTaken` before the calendar write (`tests/bot/test_double_booking.py`, ADR B4) |

### A7 — registration-code feasibility

The code is `[A-Z0-9]{8}` → 36⁸ ≈ **2.8×10¹²** values, single-use (deleted on activation), and now
per-Telegram-user flood-limited (9.5). At the flood cap an attacker gets a handful of guesses/minute
against a 2.8-trillion space with a short-lived, one-shot target — **not feasible** online. Timing
enumeration is bounded by the same flood limit. (A distributed attacker across many Telegram accounts
is possible in theory but costly; a shorter code TTL would tighten it further — noted for the owner.)

## Follow-ups (a later pass)

- **A10** — document/enforce the DB file mode (`0600`) in the deployment runbook (Phase 12).

_(A9 — non-local Redis TLS+AUTH — and A12 — OAuth `state` verification (SF-018) — closed 2026-09-24.)_

Run everything: `python tasks.py security` (bandit at HIGH + `tests/security/`). CI additionally runs
pip-audit and gitleaks (9.10).
