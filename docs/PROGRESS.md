# ZenFlow — Progress Tracker / מעקב התקדמות

The living checklist for `docs/MASTER_PLAN_EN.md` / `docs/MASTER_PLAN_HE.md`.
**Rule:** at the end of every task, tick the box and fill in date + commit SHA + a one-line note **+ the PR link**
(every task is delivered as a Pull Request into `master`; the human merges — see Master Plan §1.1 DELIVER).
**כלל:** בסוף כל משימה — סמן, ומלא תאריך + SHA של הקומיט + הערה בשורה אחת **+ קישור ל-PR**
(כל משימה נמסרת כ-Pull Request אל `master`; האדם ממזג — ראה תוכנית-אב §1.1 DELIVER).

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Phase 0 — Foundations
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| 0.1 | Repo hygiene (untrack `*.rdb`, secret sweep) | [x] | 2026-09-14 | 941e8c9 | Untracked `*.rdb` + `.claude/settings.local.json`; secret sweep → `docs/SECURITY_FINDINGS.md` SF-001..004; history rewritten with git-filter-repo + force-pushed; orphan `main` deleted. Tokens kept by owner decision — [PR #1](https://github.com/shaytoledo/ZenFlow-Clinic/pull/1) |
| 0.2 | pyproject, black, ruff, mypy, pre-commit, Makefile | [x] | 2026-09-14 | 3a43bc2 | + black 4abcc89, strict-island types a428572, ruff fixes e3c02fb/6033fb9. Baselines: ruff ignore list, mypy 38 modules ignored, coverage 0% (fail_under=0), bandit 5 medium / 51 low. ADR-13/14. HTTPS rule added to plan 0.4 (owner request) — [PR #1](https://github.com/shaytoledo/ZenFlow-Clinic/pull/1) |
| 0.3 | `tests/` harness + conftest fixtures + 10 smoke tests | [x] | 2026-09-14 | a8a2a09 | conftest: per-test SQLite (`ZENFLOW_DB_PATH`), fakeredis, ASGI client + real sign-in, freezegun, fake Telegram, fake LLM, 5 factories. Smoke test found **SF-005**: 13 API routes open without a session (strict xfail until 0.5) — [PR #1](https://github.com/shaytoledo/ZenFlow-Clinic/pull/1) |
| 0.4 | `zenflow/settings.py` + feature-flag registry | [x] | 2026-09-15 | 7fb2490 | pydantic-settings; fail-fast on default secret / missing TOKEN_ENCRYPTION_KEY / non-local http (ADR-14); 8 typed ZF_* flags both paths tested; `/api/admin/flags`; `python -m zenflow.rotate_token_key`. ADR-16 — [PR #1](https://github.com/shaytoledo/ZenFlow-Clinic/pull/1) |
| 0.5 | Structured logging + redaction + request-id | [x] | 2026-09-15 | 1b5f224 | `zenflow/logging.py` (stdlib, ADR-18): context via ContextVar + record factory, console/JSON by `LOG_FORMAT`, secret redaction (11 shapes tested), `X-Request-ID` middleware + access log, scheduler job ids — [PR #3](https://github.com/shaytoledo/ZenFlow-Clinic/pull/3) |

**Review log — Phase 0 (PRs #1–#3), 2026-09-15.** Eight-angle review over the combined diff; 27 candidates,
13 acted on before merge: `.env.example` comment-as-value (SF-009), unscoped re-query in send-recommendations
(SF-010), legacy Google-token decrypt fallback, `ZENFLOW_DB_PATH` from `.env` ignored, Google redirect defaults
back to the real dev port 8080, scheduler `appointment_id` log key, Redis outage / corrupt blob on message
endpoints, JSON 404 on the HTML treatment page, one auth helper instead of four copies, keyed therapist lookup
instead of a full-table scan per auth check, redaction fast path, developer `.env` leaking into tests, fragile
frozen-clock ordering, default-deny route test. Deferred with tickets: relay ownership by patient only (SF-008 →
2.4), cross-tenant `list_all` cache + Python filtering (→ 7.2/9.1), `availability_service` raw SQL vs repo (→ 9.1),
optional `therapist_id=None` fail-open defaults on repository reads (→ 9.1: make required).

**Review log — PR #4 (Phase 1.1), 2026-09-15.** Correctness review found 6 issues, all fixed before merge with
regressions in `tests/unit/test_clock_review.py`: notification time 'ZZ' → Invalid Date; archive page sliced a UTC
instant as the clinic date (new `clinic_date` / `clinic_datetime` template filters); two therapist INSERTs and both
treatment_notes upserts still used the legacy `created_at` DEFAULT (static test now enforces explicit `created_at`);
date-only strings were shifted a day by the migration (now classified `date` and kept; client-supplied
`recommendations_sent_at` validated + canonicalised, 400 otherwise); rolling calendar window stamped clinic days as UTC
midnight (`clock.day_bounds_utc`).

**Review log — PR #5 (Phase 1.2), 2026-09-15.** 8 findings, all fixed before merge (`tests/unit/test_task_queue_review.py`):
complete/fail now require `status='running'` + owning worker (no resurrecting cancelled/reclaimed jobs); crash path
enforces the attempt budget (expired-lock jobs at the budget dead-letter); explicit `retry_at` no longer bypasses the
budget; empty idempotency key = none; standalone worker runs `init_db()`; `handler_timeout` must be shorter than the
lock timeout; a cancelled worker releases its job (attempt not charged) and the bot cancels background tasks in
`post_shutdown`; stats test covers every status.

**Review log — PR #6 (Phase 1.3), 2026-09-15.** 8 findings; 7 fixed with regressions in
`tests/integration/test_followup_jobs_review.py` (reschedule on re-completion, DB stamp first + repair on retry,
Redis failure after send, notification failure after send, Send Now clears the queue, dead-letter alert on
timeout, 48h reconciliation + bad-row isolation) and the weak assertions strengthened. Known limit: a job whose
worker keeps crashing dead-letters in `claim()` without an alert → Phase 8.

## Phase 0.5 — Critical security triage
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| F6 | Multi-tenant IDOR — `require_appointment_access` on all routes | [x] | 2026-09-15 | 61835ff | `web/deps.py` authz helpers + tenant filters in repos/services; every appointment/patient/relay/slot path scoped; SF-005/006/007 closed; `tests/security/test_tenant_isolation.py` — [PR #2](https://github.com/shaytoledo/ZenFlow-Clinic/pull/2) |
| F7 | SESSION_SECRET fail-fast + separate TOKEN_ENCRYPTION_KEY | [x] | 2026-09-15 | 7fb2490 | settings + rotation in 0.4; boot refusal verified in a real subprocess (`test_app_refuses_to_boot_with_default_secret_in_prod`). Owner still to set `TOKEN_ENCRYPTION_KEY` on the running install (runbook in ARCHITECTURE.md) — [PR #2](https://github.com/shaytoledo/ZenFlow-Clinic/pull/2) |
| F8 | Committed Redis snapshots — untrack + content review | [x] | 2026-09-14 | 941e8c9 | Untracked, reviewed (SF-003: fake test data, no tokens — confirmed by owner), purged from history — [PR #1](https://github.com/shaytoledo/ZenFlow-Clinic/pull/1) |
| F11 | `/api/status` requires auth; add public `/healthz` | [x] | 2026-09-15 | 61835ff | router-level auth covers `/api/status` and `/api/smtp-status`; `GET /healthz` → `{"ok": true}` only — [PR #2](https://github.com/shaytoledo/ZenFlow-Clinic/pull/2) |
| — | Session cookie flags (https_only / same_site / max_age) | [x] | 2026-09-15 | 61835ff | `web/app.py::session_cookie_kwargs`: Secure outside dev, SameSite=lax, max_age 30 d, HttpOnly — [PR #2](https://github.com/shaytoledo/ZenFlow-Clinic/pull/2) |

## Phase 1 — Time, jobs & durable scheduling
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| 1.1 | `zenflow/clock.py` + timestamp audit + data migration (F2) | [x] | 2026-09-15 | d3e0cfa | canonical `…Z` strings; `SQL_NOW`; explicit created_at on every INSERT; `clock.today()` = CLINIC_TZ date; ruff DTZ; `python -m zenflow.migrate_timestamps`; window test at 23h/2h/48h across three clinic zones. ADR-19 — [PR #4](https://github.com/shaytoledo/ZenFlow-Clinic/pull/4) |
| 1.2 | ADR: queue backend → `TaskQueue` + `jobs` table + worker | [x] | 2026-09-15 | a5264f4 | ADR-20 (5 options compared, (a) chosen); `zenflow/queue.py` + `zenflow/worker.py`; jobs table; in-process worker at bot post_init; conformance suite: not-early, idempotent, backoff→dead-letter, crash recovery exactly-once, both flag paths — [PR #5](https://github.com/shaytoledo/ZenFlow-Clinic/pull/5) |
| 1.3 | Migrate follow-up + recommendation schedulers to jobs (F1) | [x] | 2026-09-15 | 780ba6c | enqueued at Complete Session (T+24h) + on queued recommendations; DB-idempotent handlers; Telegram failure retries; 48h expiry; F1 fixed (therapist id passed; Gmail-not-connected → one alert, entry kept); 30-min poll → `reconcile()` safety net. ADR-21. Phase 1 gate green — [PR #6](https://github.com/shaytoledo/ZenFlow-Clinic/pull/6) |

## Phase 2 — Bot audit & repair (item 6)
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| 2.1 | `docs/BOT_AUDIT.md` — full handler sweep, ranked findings | [x] | 2026-09-16 | a993baf | 17 ranked findings B1–B17 (F3/F4/F5 confirmed; new: wrong-patient relay routing, Markdown breaks relay, double booking, stale therapist registry, silent booking loss). **Awaiting owner scope agreement + Q6/Q8/Q9 before 2.2** — [PR #7](https://github.com/shaytoledo/ZenFlow-Clinic/pull/7) |
| 2.2a | Relay safety: B1 wrong-patient routing, B2 Markdown, B5 stale registry, B7 media (interim), B12 therapist substitution | [x] | 2026-09-16 | ed80de1 | 13 tests in `tests/bot/test_relay_safety.py`; no "last patient who wrote" fallback; relay bodies sent as plain text; media refused, never dropped. See `docs/BOT_AUDIT.md` §1a — [PR #8](https://github.com/shaytoledo/ZenFlow-Clinic/pull/8) |
| 2.2b | B3 follow-up routing (F3), B4 double booking, B9 misreported relay failures | [x] | 2026-09-16 | ee77c73 | 24 new tests; partial unique index `ux_appointments_active_slot`; first booking/cancel flow tests. See `docs/BOT_AUDIT.md` §1b — [PR #9](https://github.com/shaytoledo/ZenFlow-Clinic/pull/9) |
| 2.2c | B6 /start reset, B8 error handler, B11 stale callbacks, /cancel + /help | [x] | 2026-09-16 | c91854f | 13 tests in `tests/bot/test_robustness.py`; `allow_reentry=False` pinned. See `docs/BOT_AUDIT.md` §1c — [PR #10](https://github.com/shaytoledo/ZenFlow-Clinic/pull/10) |
| 2.2d | B10 conversation timeout, B14 shared Bot lifecycle | [x] | 2026-09-16 | e056aba | `ZF_CONV_TIMEOUT_MINUTES` (default 30, 0 = off); `wire_bots()`; `python-telegram-bot[job-queue]` locked. See `docs/BOT_AUDIT.md` §1d — [PR #11](https://github.com/shaytoledo/ZenFlow-Clinic/pull/11) |
| 2.3 | State persistence across restarts | [x] | 2026-09-16 | a2a2f7a | `bot/persistence.py::SqlitePersistence` (JSON rows in `bot_persistence`, not pickle); whitelisted scheduling keys only; flows idle past the timeout are not resumed; acceptance test drives a real `Application` offline through book → restart → finish. ADR-22 — [PR #12](https://github.com/shaytoledo/ZenFlow-Clinic/pull/12) |
| 2.4 | Multi-therapist relay isolation tests | [x] | 2026-09-16 | a80f818 | 7 tests in `tests/security/test_relay_isolation.py`. **SF-008 closed**: relay history/unread keyed per therapist–patient, routing per therapist–message (Telegram ids are per chat — a real collision); appointment heuristic removed; dashboard replies sent as plain text — [PR #13](https://github.com/shaytoledo/ZenFlow-Clinic/pull/13) |

## Phase 3 — Pipeline ownership (items 4, 5)
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| 3.1 | Generation moves to enqueued jobs right after intake | [x] | 2026-09-16 | ddf519f | item 5. `bot/services/pipeline_jobs.py`: intake.finalize → diagnosis.generate → points.generate ×2; reads the conversation from the DB (saved with the booking), idempotent per stage, 3 attempts with backoff, dead letter → FAILED, per-appointment lease (`zenflow/leases.py`). Closes BOT_AUDIT B13. ADR-23 — [PR #14](https://github.com/shaytoledo/ZenFlow-Clinic/pull/14) |
| 3.2 | Treatment page never auto-triggers generation | [x] | 2026-09-16 | 7285a74 | item 4. Page load only reads; no points + idle/FAILED → explicit "Generate diagnosis & points" button. `rediagnose`/`generate-points`/`regenerate-points` return 409 + `points_status` while generating (`force=true` overrides a stale status, never the per-appointment lease). Verified in the browser on a scratch DB: 0 generation requests on load, 409 followed as progress — [PR #15](https://github.com/shaytoledo/ZenFlow-Clinic/pull/15) |
| 3.3 | Verify + fix "Regenerate points" (202 + status stream) | [x] | 2026-09-16 | 9c23637 | item 1b. Verdict: the old endpoint worked but raced the page's own poller (two sources of truth, request held for up to four AI calls). Now 202 + the `points.generate` jobs with a fresh run id and the therapist's language; the page follows the status only after the 202. New `POST …/cancel-generation` (status CANCELLED, queued jobs cancelled, an in-flight batch discarded by a compare-and-set write) and a Cancel button; failures end in FAILED. The top points card is now cleared too. Browser-verified on a scratch DB — [PR #16](https://github.com/shaytoledo/ZenFlow-Clinic/pull/16) |
| 3.4 | SSE updates replacing 2s polling | [x] | 2026-09-16 | f5dcbf8 | Behind `ZF_SSE_UPDATES` (off by default). `GET …/stream` sends the notes, then again on every status write (Redis pub/sub wake-up on `zenflow:treatment:{id}`, published by the repository), with a 2 s re-check as the safety net; ends with `done`. The page falls back to polling on any stream error. Subscriber receives a transition within 1 s (test). Browser-verified with the flag on (no local Redis → timer path). Phase 3 complete. ADR-24 — [PR #17](https://github.com/shaytoledo/ZenFlow-Clinic/pull/17) |

## Phase 4 — Clinical UI (items 1, 3, 9)
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| 4.1a | Split `treatment.html` into partials + ordered scripts + one stylesheet | [x] | 2026-09-17 | e5894b4 | pure move: 1,967 → 66 lines; 10 partials, 10 classic scripts (shared globals, main.js last), `static/css/treatment.css`; the one Jinja value inside JS became a JSON island. Before/after DOM snapshot of 4 sessions (tag, id, class, style, attributes, text, 60 computed styles per element): identical — [PR #18](https://github.com/shaytoledo/ZenFlow-Clinic/pull/18) |
| 4.1b | Inline `onclick` → event delegation; escape AI/patient data in `innerHTML` (F10) | [x] | 2026-09-17 | 5eb7ce2 | 26 inline handlers → `data-action` + `events.js` (click/input/focus/hover delegation). **SF-011 (HIGH) found & closed**: AI advice text rendered raw — a planted `<img onerror>` executed (reproduced), point codes broke out of `onclick`. `escHtml` escapes quotes. DOM snapshot of 4 sessions identical except the removed `on*` attributes; injection probes inert — [PR #19](https://github.com/shaytoledo/ZenFlow-Clinic/pull/19) |
| 4.1c | Inline styles → classes + CSS custom properties | [x] | 2026-09-17 | 87254dd | 174 static `style="…"` (140 distinct) → page-scoped `tp-*` classes in `static/css/treatment.css` under a `.tp` root (`.tp .x` outranks the shared `zf-*` rules and their `:hover`, as inline styles did); the 13 data-driven ones → `tp-ch-*` channel themes and `tp-tone-*` status tones (CSS variables) plus one CSSOM width. JS hover/focus handlers → CSS `:hover`/`:focus`; the injected `<style>` and the overlay `cssText` are gone. DOM snapshot of the 4 sessions (tag, id, attributes, text, 60 computed styles): identical; hover, focus, email dialog, progress bar and point panel checked by hand. Tests: no style attribute, every `tp-*` class (incl. computed tone/channel names) is defined, balanced stylesheet — [PR #20](https://github.com/shaytoledo/ZenFlow-Clinic/pull/20) |
| 4.2a | Point cards: research, design tokens, card anatomy, selection model, a11y, RTL | [x] | 2026-09-17 | f0af69e | item 1a. Research + design in `docs/POINT_CARD_DESIGN.md` (WHO naming, Deadman entry layout, pregnancy cautions kept on the card face). `css/tokens.css` (ADR-25): `--zf-*` scales, semantic colours, channel palette, opt-in dark theme; every text pair ≥ 4.5:1 in both themes (test). New card: code badge → name → toggle → channel chip (name always shown) → caution → one-line location → "for this patient" → `<details>`. Cards and chips toggle a point (`aria-pressed`, "Added" text, running count, undo for any removal); tag label opens the point panel (was dead code; now a labelled dialog, Escape closes, focus returns). Logical properties only (test). Fixed on the way: Hebrew cards were all grey (theme looked up by the translated channel), WHO `KI3` found no data (table uses `KD3`), small text below AA. Browser-checked: interactions, tablet width, RTL, dark tokens — [PR #21](https://github.com/shaytoledo/ZenFlow-Clinic/pull/21) |
| 4.2b | Point states: empty, loading (real stage + skeletons), partial, failed, cancelled, stalled | [x] | 2026-09-17 | 2249986 | item 1a. `point-states.js` owns the AI points area: every path calls `showPointState()`; `stateOfNotes()` maps each `points_status` (tested for all of them). The timed progress bar is gone: a step list follows the real stage, with a live "Step n of 4" line, `aria-busy` and skeleton cards; failures are `role=alert` panels with Retry; cancel keeps what batch A saved. The follower shows loaded notes at once, re-renders only on change and drops responses that land after Cancel. `pipeline.js` 571 → 332 lines. Browser-checked: loading, partial, real Cancel, Generate → failed without an LLM, idle with and without intake, ready, stalled, Hebrew RTL, dark tokens — [PR #22](https://github.com/shaytoledo/ZenFlow-Clinic/pull/22) |
| 4.2d | Compact/detailed point density toggle, saved per therapist | [x] | 2026-09-17 | c6f4d27 | item 1a. `therapists.ui_prefs` (JSON, migration) behind `therapist_repo.get_ui_prefs/update_ui_prefs` with a whitelist (`UI_PREF_CHOICES`; corrupt or unknown stored values read as defaults); `GET`/`PATCH /api/my/preferences` (401 without a session, 422 for anything off the whitelist, nothing saved). The page gets the value in its config island; a segmented toggle switches and saves. Compact keeps code, name, toggle, channel and caution (tested), hides location/rationale/details, 180px auto-fill grid. Browser-checked: toggle, saved across reload, card 231→108px tall, caution still shown — [PR #23](https://github.com/shaytoledo/ZenFlow-Clinic/pull/23) |
| 4.2c | Responsive shell + print stylesheet (patient handout) | [x] | 2026-09-17 | ba4eaa0 | item 1a. Below 900px the sidebar is a drawer on every page (`css/shell.css`, `js/shell.js`): labelled menu button with `aria-expanded`, scrim, Escape/scrim/link close, focus moves in and back, slides from the reading side (RTL from the right), the page behind the open drawer is `inert`, reduced-motion safe; topbar drops clock/actions on narrow screens. Print drops the chrome and the fixed-height layout that clipped printing to one page. The treatment page prints only a patient handout (`handout.js`, rebuilt before every print, "Print handout" button): patient/date/therapist, today's points with reference names + locations, switched-on advice; codes only in, so no AI rationale or notes can leak. Browser-checked at 375px LTR/RTL and 1280px; print checked by applying the print rules on screen — [PR #24](https://github.com/shaytoledo/ZenFlow-Clinic/pull/24) |
| 4.2e | Playwright visual snapshots (3 widths × LTR/RTL × light/dark) | [ ] | | | item 1a. Needs the `playwright` package; use the installed Edge/Chrome channel to avoid the browser download |
| 4.3 | `acupoints` + `acupoint_images` + Storage ABC + ingester | [ ] | | | item 9 |
| 4.4 | Sidebar user card → /settings | [x] | 2026-09-17 | _pending_ | item 3. The card is a real `<a href="/settings">` (screen-reader text "Settings", chevron), with hover, keyboard focus ring and the nav items' current-page highlight (`aria-current="page"`); role label raised to AA. Tested at the HTTP level (link, active state, target 200) and in the browser (click lands on /settings; Tab reaches it with a visible ring). The Settings / Language / Sign out dropdown is Q10 |

## Phase 5 — Google / Gmail connection UX (item 2)
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| 5.1 | Recover prior branches, summarise, decide cherry-picks | [ ] | | | do this first |
| 5.2 | Typed 409 `google_not_connected` error contract | [ ] | | | |
| 5.3 | Preflight UI: disabled controls + modal + copy fallback | [ ] | | | |
| 5.4 | Background sends: one notification, keep job queued | [ ] | | | |
| 5.5 | Tests (no token / revoked token / happy path) | [ ] | | | |

## Phase 6 — 24h follow-up & recommendations (items 7, 8)
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| 6.1 | Root-cause fixes + failing tests with frozen clock | [ ] | | | item 8 |
| 6.2 | Extended conversation format + red-flag rule | [ ] | | | item 7 |
| 6.3 | `followups` table + migration + backfill | [ ] | | | |
| 6.4 | No-channel → persistent therapist alert + manual form | [ ] | | | item 7 |
| 6.5 | Follow-up card at the bottom of every session view | [ ] | | | item 7 |
| 6.6 | Recommendation delivery chain, idempotent + logged | [ ] | | | item 8 |

## Phase 7 — Channel & booking API
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| 7.1 | Full `ChannelAdapter` (inbound + outbound) + conformance suite | [ ] | | | |
| 7.2 | `patients` + `patient_channels`; kill `patient_id < 0` | [ ] | | | highest-value refactor |
| 7.3 | `POST /api/v1/appointments` + idempotency + OpenAPI | [ ] | | | |
| 7.4 | WhatsApp adapter behind `ZF_CHANNEL_WHATSAPP` | [ ] | | | provider ADR first |

## Phase 8 — Observability & audit
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| 8.1 | `audit_log` | [ ] | | | confirm "SDB" reading |
| 8.2 | `ai_calls` (cost/latency/failures) | [ ] | | | |
| 8.3 | `message_log` | [ ] | | | |
| 8.4 | `/healthz`, `/readyz`, `/api/admin/metrics` | [ ] | | | |
| 8.5 | Audit trail surfaced per appointment | [ ] | | | |

## Phase 9 — Security hardening
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| 9.1 | Route/authz table + CI test for new routes | [ ] | | | |
| 9.2 | Session & transport hardening | [ ] | | | |
| 9.3 | CSRF | [ ] | | | |
| 9.4 | Security headers + CSP with nonces | [ ] | | | needs 4.1 (done). The treatment page has no inline script, handler or style left; `base.html` and 12 other templates still use `style="…"` — move them too, or ship `style-src 'unsafe-inline'` (styles cannot run script) and tighten later |
| 9.5 | Rate limiting & lockout (web + Telegram) | [ ] | | | |
| 9.6 | Secrets & key rotation, `SecretsProvider` | [ ] | | | |
| 9.7 | Input/output safety (XSS, Markdown injection, SSRF) | [ ] | | | |
| 9.8 | LLM prompt-injection defences | [ ] | | | real attack surface |
| 9.9 | Data protection, retention, export/delete | [ ] | | | |
| 9.10 | Supply-chain scanning in CI | [ ] | | | |
| 9.11 | Data in transit: HTTPS/TLS everywhere, cert verification, Redis TLS, no secrets in URLs | [ ] | | | owner requirement 2026-09-14 (ADR-14) |

## Phase 10 — Offensive testing
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| 10.1 | `docs/THREAT_MODEL.md` (STRIDE) | [ ] | | | |
| 10.2 | A1–A14 attack scenarios as failing tests | [ ] | | | local/staging only |
| 10.3 | `docs/SECURITY_FINDINGS.md` + fixes + regressions | [ ] | | | |
| 10.4 | `make security` target | [ ] | | | |

## Phase 11 — Test pyramid
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| 11.1 | Unit suite (incl. AI-output parser fuzzing) | [ ] | | | |
| 11.2 | Integration suite (all routes, repos, jobs, migrations) | [ ] | | | |
| 11.3 | Bot state-machine suite | [ ] | | | |
| 11.4 | E2E journeys J1–J5 (Playwright) | [ ] | | | |
| 11.5 | Contract + adapter conformance | [ ] | | | |
| 11.6 | Load (Locust) + chaos (Redis/Ollama/Telegram/Google down) | [ ] | | | |
| 11.7 | CI pipeline with required checks | [ ] | | | |

## Phase 12 — AWS readiness
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| 12.1 | Gap-analysis table | [ ] | | | |
| 12.2.1 | Dockerfiles + docker-compose parity stack | [ ] | | | |
| 12.2.2 | Full suite green against Postgres in CI | [ ] | | | the key proof |
| 12.2.3 | Alembic migrations (0001 = current schema) | [ ] | | | |
| 12.2.4 | Remove mutable in-process globals | [ ] | | | blocks >1 instance |
| 12.2.5 | Webhook mode for both bots | [ ] | | | biggest arch change |
| 12.2.6 | IaC skeleton (Terraform/CDK) | [ ] | | | no provisioning without approval |
| 12.2.7 | Backups & tested restore drill | [ ] | | | |
| 12.2.8 | Cost estimate + minimal-viable alternative | [ ] | | | |
| 12.2.9 | Migration runbook + rollback | [ ] | | | |

## Phase 13 — Documentation & maintenance
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| 13.1 | Keep docs/ current per PR | [ ] | | | ongoing |
| 13.2 | ADRs in TECHNICAL_DECISIONS.md | [ ] | | | ongoing |
| 13.3 | `docs/RUNBOOK.md` | [ ] | | | |
| 13.4 | Per-phase retro | [ ] | | | ongoing |

---

## Known tooling defects
| # | Defect | Impact | Plan |
|---|---|---|---|
| T1 | `python tasks.py all` can never exit 0: `bandit -q` exits 1 on the 66 pre-existing Low/Medium findings (try/except/pass, `f"…{SQL_NOW}"` in SQL strings, …), so the run stops before `pytest tests/security`. Discovered 2026-09-16 on task 2.2a; present on `master` too. | The gate must be read, not trusted — lint/type/test/security were run separately for 2.2a (all green: 285 + 33 tests). | Phase 0.2 follow-up: adopt a bandit baseline file that only ever shrinks (same ratchet rule as ADR-13) and let the step fail on *new* findings only. |

## Open questions awaiting the human / שאלות פתוחות
| # | Question | Blocks | Answer |
|---|---|---|---|
| Q1 | Does "track in SDB" mean the DB audit trail? | Phase 8 | |
| Q2 | WhatsApp provider: Twilio or Meta Cloud API? | 7.4 | |
| Q3 | AWS budget/region; Ollama stays or move to Bedrock/Anthropic? | Phase 12 | |
| Q4 | Point-image source + licence approval | 4.3 | |
| Q5 | GDPR / Israeli privacy law posture for patient data | 9.9 | |
| Q6 | Media relay policy (photos/voice may be PHI) | 2.2c | Still open. 2.2a's interim behaviour forwards and stores nothing — it refuses politely and keeps the patient in the chat — so any answer is still available |
| Q7 | Follow-up for sessions never explicitly "completed"? | 6.1 | |
| Q8b | Relay: may therapists free-type without replying, when >1 patient chat is active? (BOT_AUDIT B1) | 2.2 | 2.2a ships the proposed rule: free typing delivered only while exactly one chat is open, otherwise the therapist is asked to reply to the patient's message. Say the word to change it |
| Q9 | Conversation timeout: proposed 30 min booking/intake, 24 h therapist chat (BOT_AUDIT B10) | 2.2d | 2.2d ships 30 min for every flow (`ZF_CONV_TIMEOUT_MINUTES`). A separate 24 h for therapist chats needs its own conversation — say if you want it |
| Q10 | User card: keep it a plain link to Settings, or open a small menu (Settings / Language / Sign out)? | 4.4 | 4.4 ships the plain link (the plan's requirement); language and sign-out stay where they are |
| Q8 | Rewrite git history to purge leaked tokens/logs/rdb (public repo, 0 forks)? Delete orphan `origin/main`? | 0.1 / F8 | 2026-09-14: approved + done. Keep current bot tokens (owner decision). Backup: `ZenFlow_Clinic-pre-rewrite-2026-09-14.bundle` next to the repo |
