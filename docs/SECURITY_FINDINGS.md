# Security Findings Log

Append-only record of security findings, their status and remediation.
Token / secret **values are never written here** — only identifiers, locations and status.
Phase 10.3 of `docs/MASTER_PLAN_EN.md` extends this file with the full attack-scenario results.

| ID | Date found | Severity | Finding | Status |
|---|---|---|---|---|
| SF-001 | 2026-09-14 | **HIGH** | Three Telegram bot tokens committed to git history of a **public** repo (`shaytoledo/ZenFlow-Clinic`, 0 forks). Bot ids `8325825345`, `8577124266`, `8695231289`. Locations: `.claude/settings.local.json` (all three; present on `origin/master` HEAD until this fix, introduced in commit `8fe0d61`, 2026-04-26) and `logs/botLogs.text` / `botLogs.text` (two of them, from the initial commit `a80c0bc`, 2026-02-26, deleted from the tree in `8fe0d61` but still in history). | **Closed 2026-09-14.** Untracked + gitignored; history rewritten (`git filter-repo`, paths purged + token strings replaced), `master` and `v1.0.0` force-pushed. Owner chose to keep the current tokens; residual risk: GitHub may serve old commits by SHA until its GC runs — rotate tokens if that matters. |
| SF-002 | 2026-09-14 | MEDIUM | `.claude/settings.local.json` also contained a plaintext dev sign-up curl with an email address and password (`/register/signup`). | **Closed 2026-09-14.** Purged from history (path removed + string replaced). Change the password if it was ever used outside local dev. |
| SF-003 | 2026-09-14 | MEDIUM | `dump.rdb` (6.7 KB, RDB v6) tracked in git. Contents (categories only): appointments cache (`zenflow:apts:all`) with one patient name (the developer's own test identity), a Google Calendar event id, an AI clinical intake summary (headache / TCM pattern text); relay keys (`relay:active`, `relay:current:t1`, `relay:history`, `relay:msg`, `relay:lastseen`) with one Telegram user id. **No tokens found inside.** `temp-39072.rdb` is 0 bytes. | **Closed 2026-09-14.** Owner confirmed fake test data. Purged from history; file kept on disk (ignored). |
| SF-004 | 2026-09-14 | LOW | `orphan` branch `main` (not related to `master`) carries an older layout plus an unrelated project (`ContextPrompt`, `PycharmProjects/fainting_image_classifer_`) and three SQLite files (`zenflow.db`, `backend/zenflow.db`, `test_zenflow.db`). | **Closed 2026-09-14.** `origin/main` and local `main` deleted; full pre-rewrite backup in `ZenFlow_Clinic-pre-rewrite-2026-09-14.bundle` next to the repo. A local stash from `main` still exists (`stash@{0}`) — drop it when no longer needed. |

## SF-001 remediation checklist
- [x] Untrack `.claude/settings.local.json`, `dump.rdb`, `temp-39072.rdb`; add ignore rules; regression test `tests/unit/test_repo_hygiene.py`.
- [ ] Revoke all three bot tokens in @BotFather — **deferred by owner decision (2026-09-14): current tokens stay in use.**
- [x] History rewrite done 2026-09-14: `git filter-repo --invert-paths` on `.claude/settings.local.json`, `logs/botLogs.text`, `logs/webLogs.text`, `botLogs.text`, `dump.rdb`, `temp-39072.rdb`, `.claude/worktrees/` + `--replace-text` for the three tokens and the dev password. Verified: 0 token hits in full history, locally and on GitHub. `master` and `v1.0.0` force-pushed; all local branches and worktrees re-pointed.
- [ ] Contact GitHub support to purge cached views of the old commits after the rewrite (optional; tokens are dead once revoked).
