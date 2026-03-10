# ZenFlow Clinic — Start Guide

All startup files live here in `startup/`. Run everything from the **project root**.

---

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Python | 3.11+ | https://python.org |
| Redis | 7+ | `winget install Redis.Redis` (Windows) |
| Ollama | latest | https://ollama.com |

---

## 1. Install dependencies

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Mac / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

---

## 2. Configure `.env`

Create `.env` in the **project root** (not in `startup/`):

```env
# ── Telegram ────────────────────────────────────────────────
TELEGRAM_TOKEN=<patient bot token from @BotFather>
THERAPIST_BOT_TOKEN=<therapist bot token from @BotFather>

# ── AI model ────────────────────────────────────────────────
OLLAMA_MODEL=gemma3:latest
OLLAMA_HOST=http://localhost:11434

# ── Redis ───────────────────────────────────────────────────
REDIS_URL=redis://localhost:6379/0

# ── Web session ─────────────────────────────────────────────
SESSION_SECRET=<any long random string, e.g. 64 random hex chars>

# ── Google Calendar (optional) ──────────────────────────────
GOOGLE_CLIENT_ID=<from Google Cloud Console>
GOOGLE_CLIENT_SECRET=<from Google Cloud Console>
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/callback
GOOGLE_REG_REDIRECT_URI=http://localhost:8000/register/google/callback
```

**Getting the tokens:**
- Create two bots on Telegram via `@BotFather` → `/newbot`
- Therapist IDs are managed via the web dashboard (`/register`) — no manual ID needed in `.env`

**Without Google Calendar:** The bot uses local availability slots managed from `/schedule`. No Google setup needed.

---

## 3. Pull the AI model

```bash
ollama pull gemma3:latest
```

`launch.py` also does this automatically on first run.

---

## 4. Run everything

```bash
# From the project root — starts all services
python launch.py
```

This starts:
- Patient Telegram bot (booking, cancelling, AI intake)
- Therapist Telegram bot (relay, registration)
- Web dashboard at `http://localhost:8000`

Press **Ctrl+C** to stop everything cleanly.

**Run individual services (development):**
```bash
python startup/run_bots.py   # Telegram bots only
python startup/run_web.py    # Web dashboard only
```

> If you see `409 Conflict` errors, an old bot instance is still running.
> Kill it: `taskkill /F /IM python.exe` (Windows) or `pkill -f "python"` (Linux/Mac)

---

## 5. Register as a therapist

1. Open `http://localhost:8000` → you will be redirected to `/register`
2. Click **Register** — fill in name, email, password (or **Continue with Google**)
3. You receive an 8-character activation code (e.g. `ABCD1234`)
4. Send that code as a message to the **therapist bot** on Telegram
5. The bot confirms activation — you can now access the dashboard

To sign in later: visit `http://localhost:8000` → **Sign In** tab.

---

## 6. Set up availability

Open `http://localhost:8000/schedule` → drag on the calendar to create availability slots.

The patient bot reads these slots automatically.

- **Google Calendar connected:** slots are stored in Google Calendar
- **No Google Calendar:** slots are stored in SQLite (`data/zenflow.db → availability table`)

---

## 7. Monitor logs

```bash
# Windows (PowerShell)
Get-Content logs/botLogs.text -Wait
Get-Content logs/webLogs.text -Wait

# Mac / Linux
tail -f logs/botLogs.text
tail -f logs/webLogs.text
```

---

## Database reference

### SQLite — `data/zenflow.db`

Primary data store. WAL mode, autocommit connections, `busy_timeout=30s`.

| Table | Purpose |
|---|---|
| `therapists` | Therapist accounts (id, name, email, password_hash, google_id, telegram_id, active) |
| `appointments` | All patient appointments (active + cancelled — soft delete) |
| `intake_sessions` | AI intake conversation history per appointment |
| `availability` | Local availability slots (used when Google Calendar is not connected) |
| `treatment_notes` | Per-session clinical data: TCM pattern, tongue/pulse findings, points used, notes, AI diagnosis |

**View the database (read-only):**
```bash
sqlite3 data/zenflow.db ".tables"
sqlite3 data/zenflow.db "SELECT * FROM appointments ORDER BY date DESC LIMIT 10;"
sqlite3 data/zenflow.db "SELECT * FROM treatment_notes ORDER BY updated_at DESC LIMIT 5;"
```

**Backup:**
```bash
cp data/zenflow.db data/zenflow.db.backup
```

> **Important:** Do not open `data/zenflow.db` with PyCharm's Database plugin while the app is running — it can hold a file lock that prevents writes.

### Redis — `redis://localhost:6379/0`

Cache and messaging layer. `maxmemory=1gb`, `allkeys-lru` eviction.

| Key pattern | TTL | Purpose |
|---|---|---|
| `zenflow:apts:all` | 30 s | All appointments list cache |
| `zenflow:slots:{date}` | 5 min | Booked time slots per day |
| `zenflow:avail:days:{tid}:{week}` | 5 min | Available days per therapist |
| `zenflow:avail:hours:{tid}:{date}` | 5 min | Available hours per therapist/day |
| `zenflow:gcal:events:{tid}:{start}:{end}` | 10 min | Google Calendar events cache |
| `zenflow:intake:{patient_id}:{therapist_id}` | 30 min | AI intake conversation history |
| `zenflow:relay:msg:{msg_id}` | session | Maps forwarded message → patient/therapist |
| `zenflow:relay:active:{patient_id}` | session | Active relay session metadata |
| `zenflow:relay:history:{patient_id}` | 30 min | Relay chat history |
| `zenflow:reg:{CODE}` | 10 min | Therapist registration activation codes |

**Check Redis:**
```bash
redis-cli ping              # → PONG
redis-cli keys "zenflow:*"  # list all ZenFlow keys
redis-cli flushdb           # clear all keys (use only in development)
```

---

## Web dashboard pages

| URL | Description |
|---|---|
| `/` | Dashboard — today's appointments + stats |
| `/schedule` | FullCalendar availability manager |
| `/patients` | Patient list with session history |
| `/treatment/{id}/{date}/{time}` | Active treatment session (notes, AI diagnosis, points) |
| `/sessions` | All session history, sortable by Name / Date / Last Access |
| `/messages` | Live relay chat + intake conversation history |
| `/settings` | Google Calendar, bot activation code |
| `/register` | Therapist sign-up / sign-in (public) |

---

## How the relay works

1. Patient taps **Connect to Therapist** → types a message → forwarded to therapist bot
2. Therapist **replies to** the forwarded message → patient receives it instantly
3. Either side can end the session with **End Chat**

> The therapist must **reply to** the forwarded message. This is the routing key when
> multiple patients are active simultaneously.

---

## Troubleshooting

**`database is locked` on treatment notes save**
- PyCharm's Database plugin may be holding the file. Close the DB connection in PyCharm.
- Kill stale Python processes: `taskkill /F /IM python.exe`

**`409 Conflict` Telegram errors**
- An old bot instance is still polling. Kill all Python processes and restart.

**Redis `ConnectionRefusedError`**
- Redis is not running. Start it: `redis-cli ping` → if no PONG, run `redis-server` or restart the service.

**Ollama timeout / fallback questions**
- Ollama is slow or not running. `launch.py` starts it automatically; or run `ollama serve` manually.

