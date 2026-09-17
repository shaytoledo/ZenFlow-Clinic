# Google / Gmail connection UX (Phase 5)

**Goal:** trying to email without a connected Google account gives an immediate, clear message
that says what to do. It is never a silent failure or a generic 500.

Email leaves ZenFlow only through the therapist's own Gmail, over OAuth (`web/services/email_service.py`).
There is no SMTP.

## 1. Prior work that was recovered (task 5.1, 2026-09-17)

The plan names two earlier branches.

- **Neither has commits.** Both tips are `12a230d` (2026-05-02), a commit `master` already contains.
- **The work is uncommitted,** in the worktree each branch is checked out in.
- **Nothing can be cherry-picked.** The useful parts are ported by hand onto the current code, in
  tasks 5.2–5.4.
- **Both worktrees are left untouched.** The owner can discard them once Phase 5 is merged.

### `claude/google-account-email-connection-090220`

This work is in `.claude/worktrees/inspiring-edison-1bb306`: 7 files changed (+314 / −40), last
edited 2026-08-20.

| Change | Status on `master` | Decision |
|---|---|---|
| `EmailNotConfigured.reason`: `not_connected` (never linked) or `token_invalid` (token expired or revoked) | missing | **Port** (5.2). The UI needs it to choose between "Connect" and "Reconnect" |
| `google_connected(therapist_id)`: a connection check that never raises | `/api/gmail-status` does the same check inline | **Port the idea.** One helper, used by the endpoint and the page bootstrap |
| `_google_not_connected()`: 409 with `status`, `reason`, `title`, `detail`, `connect_url`, `text` | the send returns **200** with `{"ok": false, "status": "no_smtp"}` | **Port, reshaped** to the plan's contract (§2). `text` is kept for the copy fallback |
| Refuse to queue a 24h send for an email-only patient while Google is not connected | missing: the send is queued and fails a day later | **Port** (5.2) |
| `/auth/login?next=…`, stored in the session, returns to the page with `?google=connected\|cancelled` | missing: the flow always lands on `/settings` | **Port, with a stricter check.** `_safe_next` accepted `/\evil.example`, which browsers treat as `//evil.example`, an open redirect. The port rejects backslashes and control characters too |
| Scheduler: the therapist id passed to `send_email`, `EmailSendError` handled | **superseded.** Phase 1.3 fixed F1 and moved delivery into `bot/services/followup_jobs.py` (retries plus a dead-letter alert) | Drop. Only the message wording differs |
| 9 new `locales/{en,he}.json` keys (title and body for not connected and expired, Connect, Reconnect, copy, redirect hint, queued-send warning) | missing | **Port** the strings (5.3) |
| `treatment.html`: "Not connected to Google" modal, copy fallback, return banner, `handleGoogleGate()` | **superseded structurally.** Phase 4.1 split the template, and inline styles, `onclick` and Jinja inside JS are now forbidden by tests | **Reimplement the behaviour** in `static/js/treatment/`, with `tp-*` classes, `data-action`, and strings from the JSON island |

### `feature/google-auth-email-validation`

This work is in the main checkout, `ZenFlow_Clinic/`: `web/templates/treatment.html` only
(+62 / −3), last edited 2026-05-25.

- **What it did:**
  - showed a bottom toast: "You are not logged in with a Google account, therefore this action
    cannot be performed";
  - added an `assertGoogleAuth()` pre-check (`/api/gmail-status`) before the email pop-up opens
    and again before it submits;
  - fails open on server errors.
- **Decision:** superseded by the modal above and by the plan's preflight (controls disabled
  before the click). Its string is English-only and it uses inline styles. **Kept:** re-checking
  just before a send, since the token can be revoked while a dialog is open. The server-side 409
  covers that case anyway.

Both worktrees also hold an untracked `temp-39072.rdb`, a Redis dump. It is not project code.

## 2. The contract (task 5.2)

Any endpoint that would send email answers like this when Google cannot be used:

```http
HTTP/1.1 409 Conflict
{
  "ok": false,
  "code": "google_not_connected",
  "reason": "not_connected" | "token_invalid",
  "message": "<localised sentence>",
  "action_url": "/settings#google",
  "connect_url": "/auth/login?next=<the page>",
  "text": "<the email subject and body, for copying>"
}
```

- **`code`** is stable, so the UI branches on it and never on the message.
- **`reason`** selects "Connect Google" or "Reconnect Google".
- **`text`** is present only when there was a message to send.
- **The page bootstrap** (`#treatment-config`) carries `google: {connected, reason}`, so the UI
  knows before the click. `GET /api/gmail-status` stays for Settings and for re-checks.

## 3. Client (task 5.3)

- **Controls:** every email-sending control is disabled while Google is not connected. A visible
  hint says why and links to "Connect Google".
- **If a send is refused anyway** (a stale page or a revoked token), a dialog explains:
  - what happened and why;
  - a **Connect Google** button that returns to this session;
  - **Copy the text instead**.
- **Returning** with `?google=connected` restores the pending send: the recipient address and the
  selected items are kept in `sessionStorage` for that session only.
- **Strings:** all of them come from `locales/{en,he}.json`.

## 4. Background sends (task 5.4)

- **Google not connected** during a queued delivery:
  - **one** persistent notification per appointment, never one per attempt;
  - the queue entry is kept;
  - delivery is retried after the therapist reconnects.
- **Token revoked mid-flight:**
  - **one** reconnect notification per therapist while it is unresolved;
  - the job retries and is dead-lettered after its attempt budget.
