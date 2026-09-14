# ZenFlow — Progress Tracker / מעקב התקדמות

The living checklist for `docs/MASTER_PLAN_EN.md` / `docs/MASTER_PLAN_HE.md`.
**Rule:** at the end of every task, tick the box and fill in date + commit SHA + a one-line note.
**כלל:** בסוף כל משימה — סמן, ומלא תאריך + SHA של הקומיט + הערה בשורה אחת.

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Phase 0 — Foundations
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| 0.1 | Repo hygiene (untrack `*.rdb`, secret sweep) | [x] | 2026-09-14 | 941e8c9 | Untracked `*.rdb` + `.claude/settings.local.json`; secret sweep → `docs/SECURITY_FINDINGS.md` SF-001..004; history rewritten with git-filter-repo + force-pushed; orphan `main` deleted. Tokens kept by owner decision |
| 0.2 | pyproject, black, ruff, mypy, pre-commit, Makefile | [ ] | | | |
| 0.3 | `tests/` harness + conftest fixtures + 10 smoke tests | [ ] | | | |
| 0.4 | `zenflow/settings.py` + feature-flag registry | [ ] | | | |
| 0.5 | Structured logging + redaction + request-id | [ ] | | | |

## Phase 0.5 — Critical security triage
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| F6 | Multi-tenant IDOR — `require_appointment_access` on all routes | [ ] | | | **highest severity** |
| F7 | SESSION_SECRET fail-fast + separate TOKEN_ENCRYPTION_KEY | [ ] | | | |
| F8 | Committed Redis snapshots — untrack + content review | [x] | 2026-09-14 | 941e8c9 | Untracked, reviewed (SF-003: fake test data, no tokens — confirmed by owner), purged from history |
| F11 | `/api/status` requires auth; add public `/healthz` | [ ] | | | |
| — | Session cookie flags (https_only / same_site / max_age) | [ ] | | | |

## Phase 1 — Time, jobs & durable scheduling
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| 1.1 | `zenflow/clock.py` + timestamp audit + data migration (F2) | [ ] | | | |
| 1.2 | ADR: queue backend → `TaskQueue` + `jobs` table + worker | [ ] | | | |
| 1.3 | Migrate follow-up + recommendation schedulers to jobs (F1) | [ ] | | | |

## Phase 2 — Bot audit & repair (item 6)
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| 2.1 | `docs/BOT_AUDIT.md` — full handler sweep, ranked findings | [ ] | | | bring list back before fixing |
| 2.2 | Known fixes F3, F4, F5 + error handler + /cancel + timeout | [ ] | | | |
| 2.3 | State persistence across restarts | [ ] | | | |
| 2.4 | Multi-therapist relay isolation tests | [ ] | | | |

## Phase 3 — Pipeline ownership (items 4, 5)
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| 3.1 | Generation moves to enqueued jobs right after intake | [ ] | | | item 5 |
| 3.2 | Treatment page never auto-triggers generation | [ ] | | | item 4 |
| 3.3 | Verify + fix "Regenerate points" (202 + status stream) | [ ] | | | item 1b |
| 3.4 | SSE updates replacing 2s polling | [ ] | | | |

## Phase 4 — Clinical UI (items 1, 3, 9)
| # | Task | Status | Date | Commit | Notes |
|---|---|---|---|---|---|
| 4.1 | Split `treatment.html` into partials + JS modules + CSS | [ ] | | | pure refactor, snapshot-tested |
| 4.2 | Point layout redesign (tokens, cards, states, a11y, RTL) | [ ] | | | item 1a |
| 4.3 | `acupoints` + `acupoint_images` + Storage ABC + ingester | [ ] | | | item 9 |
| 4.4 | Sidebar user card → /settings | [ ] | | | item 3 |

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
| 9.4 | Security headers + CSP with nonces | [ ] | | | needs 4.1 |
| 9.5 | Rate limiting & lockout (web + Telegram) | [ ] | | | |
| 9.6 | Secrets & key rotation, `SecretsProvider` | [ ] | | | |
| 9.7 | Input/output safety (XSS, Markdown injection, SSRF) | [ ] | | | |
| 9.8 | LLM prompt-injection defences | [ ] | | | real attack surface |
| 9.9 | Data protection, retention, export/delete | [ ] | | | |
| 9.10 | Supply-chain scanning in CI | [ ] | | | |

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

## Open questions awaiting the human / שאלות פתוחות
| # | Question | Blocks | Answer |
|---|---|---|---|
| Q1 | Does "track in SDB" mean the DB audit trail? | Phase 8 | |
| Q2 | WhatsApp provider: Twilio or Meta Cloud API? | 7.4 | |
| Q3 | AWS budget/region; Ollama stays or move to Bedrock/Anthropic? | Phase 12 | |
| Q4 | Point-image source + licence approval | 4.3 | |
| Q5 | GDPR / Israeli privacy law posture for patient data | 9.9 | |
| Q6 | Media relay policy (photos/voice may be PHI) | 2.2 | |
| Q7 | Follow-up for sessions never explicitly "completed"? | 6.1 | |
| Q8 | Rewrite git history to purge leaked tokens/logs/rdb (public repo, 0 forks)? Delete orphan `origin/main`? | 0.1 / F8 | 2026-09-14: approved + done. Keep current bot tokens (owner decision). Backup: `ZenFlow_Clinic-pre-rewrite-2026-09-14.bundle` next to the repo |
