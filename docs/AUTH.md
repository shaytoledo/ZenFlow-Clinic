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

a. _find_by_email(email) → therapist dict or None
b. if not found: "No account with that email"
c. if password_hash is NULL: "This account uses Google sign-in"
d. _verify_password(password, therapist["password_hash"])
   → if False: "Incorrect password"
e. if not active: "Account not yet activated — send the code to the bot"
f. request.session["therapist_id"] = therapist["id"]
g. redirect to /
```

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

Required `.env` variables:
```
GOOGLE_CLIENT_ID=<from Google Cloud Console>
GOOGLE_CLIENT_SECRET=<from Google Cloud Console>
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/callback
GOOGLE_REG_REDIRECT_URI=http://localhost:8000/register/google/callback
```

Both URIs must be added to **Authorised redirect URIs** in Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client ID.

Two OAuth flows share the same `GOOGLE_CLIENT_ID`:
- **Calendar OAuth** (`/auth/callback`) — connects therapist's Google Calendar
- **Registration OAuth** (`/register/google/callback`) — sign up / sign in with Google

---

## Authentication State Summary

| State | `telegram_id` | `active` | Can use bot? | Can use dashboard? |
|---|---|---|---|---|
| Just registered (web) | 0 | 0 | No | No (redirected to /register/activate) |
| Code sent, not activated | 0 | 0 | No | No |
| Activated via bot | non-zero | 1 | Yes | Yes (if session cookie valid) |
| Signed out | — | 1 | Yes | No (no session cookie) |
