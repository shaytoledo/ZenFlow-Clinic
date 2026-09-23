# ZenFlow — Authentication & Registration

> Only the web dashboard requires authentication.
> The Telegram bots are secured by Telegram user ID matching (THERAPIST_MAP).

---

## Web Auth Overview

Authentication uses **signed session cookies** (not JWTs, not database tokens):

```
Browser                          FastAPI (web/app.py)
  │                                     │
  │── POST /register/signin ───────────►│
  │   {email, password}                 │
  │                                     │── verify password hash
  │                                     │── Set-Cookie: zf_session=<signed>
  │◄── 302 redirect to / ──────────────│
  │                                     │
  │── GET / ────────────────────────────►│
  │   Cookie: zf_session=<signed>        │
  │                                     │── decode cookie → therapist_id = "t1"
  │                                     │── SELECT * FROM therapists WHERE id="t1"
  │◄── 200 dashboard HTML ─────────────│
```

---

## Session Cookie

| Property | Value |
|---|---|
| Cookie name | `zf_session` |
| Signing | HMAC-SHA256 via `itsdangerous` (Starlette `SessionMiddleware`) |
| Secret | `SESSION_SECRET` from `.env` |
| Max-age | 30 days |
| Contents | `{"therapist_id": "t1"}` |
| HttpOnly | Yes (set by Starlette) |
| SameSite | Lax |

**Middleware configuration:**
```python
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="zf_session",
    max_age=30 * 24 * 3600,   # 30 days in seconds
)
```

**Reading the session:**
```python
def _get_session_therapist_id(request: Request) -> str | None:
    return request.session.get("therapist_id")
```

Every dashboard route calls this. If it returns `None`, the route redirects to `/register`.

---

## Password Hashing

```python
def _hash_password(password: str) -> str:
    salt = secrets.token_hex(32)     # 64 hex chars
    key  = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return f"{salt}:{key.hex()}"

def _verify_password(password: str, stored: str) -> bool:
    salt, hex_hash = stored.split(":", 1)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return hmac.compare_digest(key.hex(), hex_hash)
```

- **Algorithm:** PBKDF2-SHA256
- **Iterations:** 260,000 (recommended minimum as of 2024)
- **Salt:** 32-byte random, stored in the hash string
- **Timing-safe comparison:** `hmac.compare_digest`
- **Google-only accounts:** `password_hash = NULL` — "use Google sign-in" error shown if password sign-in attempted

---

## Registration Flow

### Web Registration (Email + Password)

```
1. GET /register  → two-tab card (Register | Sign In)

2. POST /register/signup
   body: {name, email, password}

   a. Check email not already registered (_find_by_email)
   b. Hash password (_hash_password)
   c. _register_web_therapist(name, email, password_hash)
      → INSERT therapists (telegram_id=0, active=0)
      → new therapist id = "t{max_id + 1}"
   d. Generate 8-char activation code [A-Z0-9]
   e. Redis SET zenflow:reg:{CODE} = {name, email, google_id=null}  TTL 600s
   f. request.session["therapist_id"] = new_therapist_id
   g. redirect → /register/done?code={CODE}

3. GET /register/done?code={CODE}
   → show code with copy button + bot links
   → verify Redis key still exists (if expired: show warning)

4. GET /register/activate
   → "Waiting for bot activation..."
   → JavaScript polls /api/my/activation-status

5. Therapist sends CODE to therapist bot
   → _register_therapist_to_db(code)
      → Redis GET zenflow:reg:{CODE} → {name, email}
      → find therapist row by email (upsert telegram_id, active=1)
      → Redis DEL zenflow:reg:{CODE}
      → mutate THERAPIST_MAP, THERAPIST_BY_ID in-memory
      → bot replies: "✅ Activation successful! You can now access the dashboard."
```

### Web Registration (Google OAuth)

```
1. GET /register/google
   → build Google OAuth URL (openid + email + profile scopes)
   → redirect to Google

2. Google redirects to GET /register/google/callback?code=...
   → exchange code for id_token
   → extract: google_id (sub), email, name

   a. If google_id exists in DB (_find_by_google_id):
      → sign in: request.session["therapist_id"] = existing_id
      → redirect to /
   b. If email exists in DB (_find_by_email):
      → link google_id to existing account
      → request.session["therapist_id"] = existing_id
      → redirect to /
   c. Else: new account
      → _register_web_therapist(name, email, password_hash=None, google_id=google_id)
      → generate activation code → Redis
      → redirect to /register/done?code={CODE}
```

### Sign-In Flow

```
POST /register/signin
body: {email, password}

a. login_guard.check_locked(request, email)     # brute-force lockout (9.5)
   → if the account OR the source IP is locked: 429 + Retry-After, no password check
b. _find_by_email(email) → therapist dict or None
c. if found with password_hash NULL: "This account uses Google sign-in" (not a failure)
d. if not found, or _verify_password() is False:
      login_guard.on_failure(...)               # count the failure on both scopes
      → "Invalid email or password."            # one message; never says which of the two
e. login_guard.on_success(...)                   # clear both counters
f. request.session["therapist_id"] = therapist["id"]
g. redirect to / (or /onboarding if not active)
```

The failure message is deliberately uniform (`Invalid email or password.`) so it never reveals
whether the email exists; see the brute-force section below.

---

## Bot Registration (Activation Code)

The therapist bot handles ALL incoming messages for ALL therapists. On receiving a message:

```python
# therapist_bot/handlers.py
def handle_therapist_message(update, context):
    user_id = update.effective_user.id
    text    = update.message.text.strip()

    if user_id in THERAPIST_MAP:
        # Known therapist — relay or forward
        _handle_relay(update, context)

    elif re.fullmatch(r"[A-Z0-9]{8}", text):
        # Looks like an activation code
        _handle_registration(update, context, text)

    else:
        # Unknown user, not a valid code
        await update.message.reply_text(
            "You are not registered as a therapist.\n"
            "Register at http://localhost:8000/register"
        )
```

**`_register_therapist_to_db(user_id, code)`:**

```
1. Redis GET zenflow:reg:{code}
   → if not found: "Code expired or invalid. Generate a new one."
   → data = {name, email, google_id}

2. Find matching therapist row:
   a. Match by email (web-registered user)
   b. Match by telegram_id (already linked)
   c. Create new row if no match

3. UPDATE therapists SET telegram_id=user_id, active=1

4. Redis DEL zenflow:reg:{code}

5. Mutate in-memory maps (immediate effect, no restart needed):
   THERAPIST_MAP[user_id] = therapist_dict
   THERAPIST_BY_ID[therapist_id] = therapist_dict

6. Reply: "✅ Activation successful! Welcome, {name}."
```

---

## Web Activation (Alternative to Bot Activation)

The registration flow also supports entering the activation code directly in the web form:

```
GET /register/activate
→ register_activate.html — shows an input field for the activation code

POST /register/activate
body: {code: "ABCD1234"}

a. Redis GET zenflow:reg:{code}
   → if not found: "Code invalid or expired"
   → data = {name, email, google_id}
b. Find therapist row by email (or telegram_id)
c. UPDATE therapists SET active=1
d. Redis DEL zenflow:reg:{code}
e. Mutate in-memory maps (THERAPIST_MAP, THERAPIST_BY_ID) for immediate effect
f. Return success — therapist can now log in
```

Both the bot activation and the web activation call the same underlying logic. The bot path sets `telegram_id`; the web path does not.

---

## Generate a New Activation Code

```
GET /api/my/activation-code
→ Requires active session
→ Generates a new 8-char code and stores it in Redis (TTL 600s)
→ Returns: {"code": "ABCD1234"}
```

Exposed in `/settings` as a button ("Generate Code"). Useful when the original code expired before the therapist could activate.

---

## Logout

```
GET /logout
→ request.session.clear()
→ redirect to /register
```

Cookie is cleared from the browser by setting its max-age to 0.

---

## Activation Code Generation

```python
import secrets, string

def _generate_code() -> str:
    chars = string.ascii_uppercase + string.digits   # [A-Z0-9]
    return "".join(secrets.choice(chars) for _ in range(8))
```

- 8 characters from `[A-Z0-9]` = 36^8 ≈ 2.8 trillion possibilities
- `secrets.choice` uses a cryptographically secure RNG
- Code expires after **10 minutes** (Redis TTL = 600s)
- Code is **one-time use** — deleted immediately on successful activation

---

## Google OAuth Configuration

### Why Google credentials are in `.env`

The clinic application **acts as an OAuth client** to Google. To call Google APIs (Calendar, Userinfo) on behalf of a therapist, the app must identify itself with a `client_id` + `client_secret` pair issued by Google Cloud Console. These are the *application's* credentials — not any one therapist's — and they let the app trade an authorisation `code` (returned to the redirect URI) for an access + refresh token belonging to the user who just signed in.

Per-therapist tokens (the actual permission to read that therapist's calendar) are NOT in `.env`. They are stored in `data/google_tokens/{id}.json`, created on first OAuth callback, and deleted on `/auth/disconnect`.

If `.env` is shared with anyone outside the clinic, rotate the secret in Google Cloud Console → Credentials → click the OAuth client → "Reset secret".

Required `.env` variables:
```
GOOGLE_CLIENT_ID=<from Google Cloud Console>
GOOGLE_CLIENT_SECRET=<from Google Cloud Console>
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/callback
GOOGLE_REG_REDIRECT_URI=http://localhost:8000/register/google/callback
```

Both URIs must be added to **Authorised redirect URIs** in Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client ID.

Two OAuth flows share the same `GOOGLE_CLIENT_ID`:
- **Calendar OAuth** (`/auth/callback`) — connects therapist's Google Calendar and Gmail sending.
  `/auth/login?next=/treatment/…` returns to that page with `?google=connected|cancelled`.
  Only a same-site path is accepted: no scheme or host, no `//`, no backslash, no control
  characters, at most 512 characters. Anything else falls back to `/settings`. Reconnecting
  resolves the "reconnect Google" alert. See `docs/GOOGLE_CONNECTION_UX.md`.
- **Registration OAuth** (`/register/google/callback`) — sign up / sign in with Google

---

## Authentication State Summary

| State | `telegram_id` | `active` | Can use bot? | Can use dashboard? |
|---|---|---|---|---|
| Just registered (web) | 0 | 0 | No | No (redirected to /register/activate) |
| Code sent, not activated | 0 | 0 | No | No |
| Activated via bot | non-zero | 1 | Yes | Yes (if session cookie valid) |
| Signed out | — | 1 | Yes | No (no session cookie) |

---

## Session policy (Phase 9.2)

The dashboard session is a **signed cookie** (`zf_session`, HttpOnly, SameSite=lax, Secure outside
dev). The server keeps no server-side copy — that is what lets the app restart and scale without
shared state — so the three properties below are built in `web/session_policy.py` and applied in
one place, `_get_session_therapist` in `web/deps.py`.

### It begins clean — no fixation

`start()` (called by `_set_session` on every sign-in, password or Google) does `session.clear()`
before writing the therapist id, a fresh random `sid`, and the timestamps. Nothing an anonymous
visitor planted — an OAuth `next`, a registration marker, a chosen `sid` — survives into a
signed-in session.

### It ends by time

Every authenticated request checks two limits and clears the session when either passes:

| Limit | Flag | Default |
|---|---|---|
| idle — since the last request | `ZF_SESSION_IDLE_MINUTES` | 720 (12 h) |
| absolute — since sign-in | `ZF_SESSION_MAX_HOURS` | 168 (7 days) |

`seen_at` is refreshed at most once a minute, so a busy session stays alive without a `Set-Cookie`
on every response. The cookie's own `max_age` is set to the absolute limit, so the browser and the
server agree on when it dies. A cookie written before 9.2 has no timestamps; it is adopted as if it
began now, so a deploy signs nobody out.

### It ends on logout, for real

A cookie cannot be recalled: a copy captured before logout still verifies. `revoke()` therefore
writes the session's `sid` to `revoked_sessions` (see `docs/DATABASE.md`), and `is_usable()`
refuses any session whose `sid` is on that list. The row is kept only until the session's absolute
limit would have passed anyway; `prune()` clears the expired ones.

### Transport

`security_headers()` sends `Strict-Transport-Security` (one year, `includeSubDomains`) outside dev
only — on a developer's `http://localhost` it would pin the browser to https and break local work.
No CORS middleware is installed, so a browser's default (refuse cross-origin reads of credentialed
responses) stands; cross-origin *writes* are the CSRF token's job (plan 9.3).

Tests: `tests/security/test_session_policy.py`.

---

## CSRF protection (Phase 9.3)

The dashboard authenticates with an ambient cookie, so before this a page on another origin could
make a signed-in therapist's browser send a state-changing request and have it accepted. The
**double-submit-cookie** pattern closes that (`web/csrf.py`).

### The two copies

A random token is set as a **readable** cookie, `zf_csrf` (not HttpOnly — same-origin script has to
read it; SameSite=lax, Secure outside dev). Every unsafe request must echo that same value back:

- **fetch / XHR** — the `X-CSRF-Token` header. `static/js/csrf.js` (loaded on every page) wraps
  `window.fetch` once so the header rides along automatically on same-origin POST/PUT/PATCH/DELETE.
  None of the ~30 existing `fetch(...)` call sites had to change, and new ones are covered for free.
- **a native `<form>` post** — a hidden `csrf_token` field. The same script fills it from the cookie
  on load and before submit, so the three server-rendered forms (sign in, sign up, Google
  disconnect) carry the token.

A cross-site page can cause the cookie to ride along but cannot **read** it (same-origin policy) to
copy it into the header or field, and cannot run our script. So it cannot produce a matching token,
and `secrets.compare_digest` refuses the request with **403**.

### What is exempt, and why it is still safe

`csrf.protect` is a dependency on every cookie-authenticated router. It returns early — no token
needed — in exactly three cases:

| Exempt | Why it is not a CSRF risk |
|---|---|
| GET / HEAD / OPTIONS | change nothing |
| a request carrying `Authorization` | the booking API's key; a browser never attaches that header on its own, so the request was not driven by an ambient cookie |
| `/api/webhooks/*` | no cookie at all — Meta signs the WhatsApp webhook |

The check keys on the **`Authorization` header, not the path**, which is what makes it correct for
`/api/v1`: that router accepts *either* an API key *or* a dashboard session, and a
session-authenticated call to it needs the token like any other cookie request. A test pins both
sides — an API-key booking POST passes without a token, a session booking POST is refused without
one.

`tests/security/test_csrf.py` includes a check that walks every route and fails if a new unsafe,
cookie-authenticated `/api`, `/auth` or `/register` route is not behind the guard.

## Sign-in brute-force lockout (Phase 9.5)

`web/services/login_guard.py` (ADR-38) slows password guessing. It counts consecutive failed
sign-ins in Redis and, once `ZF_LOGIN_MAX_ATTEMPTS` (default 5; `0` disables) is reached, **locks**
the target for a cooldown that starts at 60 s and doubles with each further failure, capped at
30 min.

**Two scopes, checked before the password:**

| scope | what it stops |
|---|---|
| `account` (the email) | hammering one account to guess its password |
| `ip` (the source address) | credential stuffing spread thin across many accounts |

A locked sign-in returns **`429` with a `Retry-After` header** and re-renders the form with a wait
message — the password is never checked while locked. A **correct** password clears both counters.

**Design points:**

- Account failures are counted **whether or not the account exists**, so lockout timing does not
  reveal which emails are real, and the failure message is always the uniform
  `Invalid email or password.`
- When an **existing** account crosses the threshold, its owner gets one `security` notification in
  the bell icon (de-duplicated per window with a Redis `SET NX`), advising a password change.
- **Fail-open:** every Redis call is guarded; if Redis is unavailable the guard allows the request,
  because locking the whole clinic out over a cache blip is worse than briefly losing the throttle.

Tested in `tests/security/test_login_guard.py` (the cooldown math, the endpoint `429`/`Retry-After`,
per-IP vs per-account, the owner notification, and the disabled path).

**Volume limits (9.5 part 2, ADR-39).** Two more surfaces are capped per minute by the booking
API's fixed-window limiter (`web/services/rate_limit.hit`): the synchronous AI endpoints
(`rediagnose` / `generate-points`) per therapist (`ZF_AI_RATE_PER_MINUTE`, default 20, to protect
the shared Ollama box) and `POST /register/signup` per source IP (`ZF_SIGNUP_PER_MINUTE`, default
10). Over budget returns `429` + `Retry-After`, checked before any model call or account creation.
Tested in `tests/security/test_abuse_limits.py`.

> Still open under 9.5: activation-code entry and Telegram-side flood control (both bot-side).
