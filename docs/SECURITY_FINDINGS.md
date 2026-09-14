# Security Findings Log

Append-only record of security findings, their status and remediation.
Token / secret **values are never written here** — only identifiers, locations and status.
Phase 10.3 of `docs/MASTER_PLAN_EN.md` extends this file with the full attack-scenario results.

| ID | Date found | Severity | Finding | Status |
|---|---|---|---|---|
| SF-001 | 2026-09-14 | **HIGH** | Three Telegram bot tokens committed to git history of a **public** repo (`shaytoledo/ZenFlow-Clinic`, 0 forks). Bot ids `8325825345`, `8577124266`, `8695231289`. Locations: `.claude/settings.local.json` (all three; present on `origin/master` HEAD until this fix, introduced in commit `8fe0d61`, 2026-04-26) and `logs/botLogs.text` / `botLogs.text` (two of them, from the initial commit `a80c0bc`, 2026-02-26, deleted from the tree in `8fe0d61` but still in history). | Untracked + gitignored (Phase 0.1). **Tokens must be revoked and re-issued via @BotFather** — history rewrite pending human decision. |
| SF-002 | 2026-09-14 | MEDIUM | `.claude/settings.local.json` also contained a plaintext dev sign-up curl with an email address and password (`/register/signup`). | Untracked + gitignored (Phase 0.1). Change that password if it was ever used outside local dev. History rewrite pending. |
| SF-003 | 2026-09-14 | MEDIUM | `dump.rdb` (6.7 KB, RDB v6) tracked in git. Contents (categories only): appointments cache (`zenflow:apts:all`) with one patient name (the developer's own test identity), a Google Calendar event id, an AI clinical intake summary (headache / TCM pattern text); relay keys (`relay:active`, `relay:current:t1`, `relay:history`, `relay:msg`, `relay:lastseen`) with one Telegram user id. **No tokens found inside.** `temp-39072.rdb` is 0 bytes. | Untracked + gitignored (Phase 0.1). Appears to be test data, not real patients — confirm. |
| SF-004 | 2026-09-14 | LOW | `orphan` branch `main` (not related to `master`) carries an older layout plus an unrelated project (`ContextPrompt`, `PycharmProjects/fainting_image_classifer_`) and three SQLite files (`zenflow.db`, `backend/zenflow.db`, `test_zenflow.db`). | Not touched. Decide whether to delete `origin/main` (it is not the default branch). |

## SF-001 remediation checklist
- [x] Untrack `.claude/settings.local.json`, `dump.rdb`, `temp-39072.rdb`; add ignore rules; regression test `tests/unit/test_repo_hygiene.py`.
- [ ] Revoke all three bot tokens in @BotFather (`/revoke`) and update `.env` (`TELEGRAM_TOKEN`, `THERAPIST_BOT_TOKEN`). **Human action.**
- [ ] Decide on history rewrite (`git filter-repo` on `.claude/settings.local.json`, `logs/botLogs.text`, `botLogs.text`, `*.rdb`) + force-push + re-clone. Safe to do: public repo, 0 forks, single developer. **Human approval required.**
- [ ] Contact GitHub support to purge cached views of the old commits after the rewrite (optional; tokens are dead once revoked).
