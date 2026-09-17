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
  "title": "<localised heading>",
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
  knows before the click. `GET /api/gmail-status` returns the same pair, for Settings and for
  re-checks.

### How the server decides (implemented in 5.2)

`email_service.google_connection(therapist_id)` never raises. It returns one of:

| State | Returned |
|---|---|
| No token stored | `connected: false`, `reason: not_connected` |
| A "reconnect Google" alert is still open | `connected: false`, `reason: token_invalid` |
| Otherwise | `connected: true` |
| The check itself failed | `connected: null`. Callers do not block on it, because the send still guards itself |

`send_email()` raises:

- **`EmailNotConfigured(reason)`:** there is no token, or the token cannot be loaded or
  refreshed. A refused refresh is `token_invalid`.
- **`EmailSendError(token_invalid=True)`:** Gmail refused the credentials (HTTP 401,
  `invalid_grant`, revoked).
- **Plain `EmailSendError`:** Google was unreachable, the token refresh failed in a way Google marks retryable (`RefreshError.retryable`, e.g. a 5xx), or another error occurred. That is worth a
  retry, never a reconnect prompt. The immediate send answers 502 with a generic message; the
  details stay in the log.

The "reconnect Google" alert (`gmail_token_expired`) behaves like this:

- It is created **once** while it is unresolved.
- It is resolved when the therapist reconnects, or when any send succeeds.

`POST …/send-recommendations` answers this contract in two cases:

- when the email send cannot go out;
- when a **24h send is scheduled for an email-only (manual) patient** while Google is known to be
  disconnected. A send like that would fail a day later. Telegram patients are still queued.

## 3. Client (task 5.3)

Implemented in `static/js/treatment/email-dialog.js` and the `treatment/email_dialog.html`
partial, a native `<dialog>` like the point lightbox.

- **Controls:** for an **email-only** (manual) patient while `google.connected` is `false`:
  - "Send Now" and "Send in 24h" get `aria-disabled="true"`, a `title` with the reason, and
    `aria-describedby` pointing at a visible hint (`#google-hint`) that ends in a "Connect Google"
    link.
  - They stay **clickable**. Pressing one anyway asks the server, which explains. An unknown
    state (`null`) blocks nothing.
  - Telegram patients are never marked, because their sends do not use Google.
- **The address dialog** replaces the old overlay.
  - While Google is not connected, it shows the reason with **Connect Google** and **Copy the
    text instead**, and its Send button is marked disabled in the same way.
- **A refusal** (409 `google_not_connected`, from a stale page or a revoked token) switches the
  dialog to an explanation:
  - the server's title and message;
  - a note that the therapist comes back here;
  - Cancel, **Copy the text instead**, and **Connect Google** or **Reconnect Google**.
- **Copy the text instead** uses the 409's `text` when there is one. Otherwise it asks
  `POST …/recommendations-text`, which only formats the email and sends nothing. The text is
  shown read-only, with a Copy button (Clipboard API, falling back to `execCommand`).
- **Connect Google** is a normal link to `/auth/login?next=<this page>`. Just before navigating,
  the page saves the pending send in `sessionStorage` (key `zf:pending-email`):
  - which control was used;
  - the typed address;
  - the on/off state and edited text of each recommendation.

  It is read once, only on the same page, and only within 30 minutes.
- **Returning** with `?google=connected|cancelled`:
  - the parameter is removed from the URL;
  - the recommendations are restored;
  - for an email send, the address dialog reopens with the address filled in and a status line
    ("Google is connected…" / "…nothing was sent");
  - for "Send in 24h", the status appears under the buttons and focus goes back to the button.
  - Nothing is sent without a new click.
- **Strings:** every string in the dialog comes from `locales/{en,he}.json` through the page's JSON
  island (`email_text`, keys listed in `web/routers/pages.py::EMAIL_DIALOG_KEYS`). The dialog's own
  explanation replaces the server's English `needs_email` detail.

## 4. Background sends (task 5.4)

Implemented in `bot/services/followup_scheduler.dispatch_recommendations` and
`bot/services/followup_jobs.py`, on two new queue verbs (ADR-20 addendum).

- **Google not connected** when a queued email delivery runs:
  - **One** persistent `recommendations_waiting_google` alert per appointment, however often the
    job looks again.
  - The job is **deferred**: it goes back to `pending` for `GOOGLE_RECHECK_HOURS` (6 h), and the
    attempt is **not** charged, so waiting never dead-letters it. The queue entry is kept.
  - **Connecting Google** makes the waiting send due at once:
    - `/auth/callback` calls `resume_after_google_connected(therapist_id)`, which runs `run_now`
      on that therapist's pending recommendation jobs;
    - the next worker pass delivers them.
  - The alert is resolved when the send is delivered. It is also resolved when the queue entry
    disappears (for example, sent by hand), which the job notices on its next run.
- **Token refused mid-flight** (a refused refresh, or Gmail 401 / `invalid_grant`):
  - `send_email` raises **one** reconnect alert (`gmail_token_expired`) per therapist while it is
    unresolved;
  - the job **retries** with backoff and is dead-lettered after its attempts;
  - `recommendations_dead` then raises **one** `send_failed` alert. That alert is now
    deduplicated per appointment, so a second dead job or another failure path adds nothing
    while it is open;
  - the queue entry is kept for "Send Now". Reconnecting also pulls any backoff wait forward.
- **Google unreachable**, or a refresh marked retryable: an ordinary failure. It retries and then
  dead-letters, with no reconnect alert.

## 5. What proves it (task 5.5)

| The plan asks | Covered by |
|---|---|
| No token → 409 with the typed payload | `tests/integration/test_google_connection.py::test_no_google_account_is_a_typed_409_with_the_text_to_copy` (exact body), `…::test_an_email_only_patient_is_not_queued_while_google_is_disconnected` |
| …and the UI shows the modal | `tests/e2e/test_email_flow.py::test_a_refused_send_shows_the_servers_explanation` (Enter → the server's 409 → the explanation and its text, with no extra request), `…::test_email_controls_explain_a_missing_google_account` (preflight, en/he) |
| Token present, Gmail says `invalid_grant` → reconnect alert exactly once | the web path: `test_google_connection.py::test_a_revoked_token_asks_to_reconnect_and_alerts_once` and `…::test_gmail_refusing_the_credentials_is_token_invalid_too`. The queued path: `tests/integration/test_background_email.py::test_a_refused_token_retries_then_dead_letters_with_one_alert_each` (at refresh and at send) |
| Happy path → Gmail called with the right base64 MIME, row stamped | the web path: `test_google_connection.py::test_happy_path_sends_base64_mime_and_clears_a_stale_alert`. The queued path: `tests/integration/test_recommendations_delivered.py::test_an_emailed_queue_is_stamped_and_not_sent_again` (To, `text/plain; charset=utf-8`, body, `recommendations_sent_at`) |

**Bug found while writing these tests:**

- **Before:** a queued delivery (email or Telegram) cleared the queue entry but never stamped
  `recommendations_sent_at`.
- **Consequence:** completing the session again queued the same recommendations again, so the
  patient received them twice. The test reproduced two emails on the old code.
- **Fix:** `treatment_repo.mark_recommendations_delivered()` stamps the row and clears the queue
  in one statement.
