# ZenFlow Clinic — Quick Start

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Python | 3.11+ | https://python.org |
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

Create a `.env` file in the project root:

```
TELEGRAM_TOKEN=<patient bot token>
THERAPIST_BOT_TOKEN=<therapist bot token>
THERAPIST_TELEGRAM_ID=<therapist's numeric Telegram user ID>
OLLAMA_MODEL=gemma3:latest
OLLAMA_HOST=http://localhost:11434
```

**Getting the tokens:**
- Create two bots on Telegram via `@BotFather` → `/newbot`
- Get the therapist's user ID: have them message `@userinfobot` on Telegram
- The therapist must send `/start` to **both** bots once before they can receive messages

**Optional — Google Calendar integration:**
```
GOOGLE_CLIENT_ID=<from Google Cloud Console>
GOOGLE_CLIENT_SECRET=<from Google Cloud Console>
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/callback
```
Without this, the bot uses a built-in stub schedule. See `ARCHITECTURE.md` for setup steps.

---

## 3. Pull the AI model

```bash
ollama pull gemma3:latest
```

The bot also tries to start Ollama automatically on startup.

---

## 4. Run everything

```bash
python launch.py
```

This starts all three services at once:
- Patient Telegram bot
- Therapist Telegram bot
- Web dashboard at `http://localhost:8000`

Press **Ctrl+C** to stop everything.

**Run individual services:**
```bash
python run_bots.py   # Telegram bots only
python run_web.py    # Web dashboard only
```

> If you see `409 Conflict` errors, an old instance is still running. Kill it first:
> ```bash
> taskkill /F /IM python.exe   # Windows
> ```

---

## 5. Therapist web dashboard

Open `http://localhost:8000` → sign in with Google → the weekly calendar appears.

- **Click or drag** an empty slot to mark it available (green)
- **Click a green slot** to remove it
- The patient bot reads these slots automatically — no extra steps needed

---

## 6. Monitor logs

```bash
# Windows (PowerShell)
Get-Content botLogs.text -Wait

# Mac / Linux
tail -f botLogs.text
```

---

## How the therapist relay works

1. Patient taps **Connect to Therapist** → types a message → forwarded to the therapist's bot
2. Therapist **replies to** the forwarded message → patient receives it instantly
3. Either side can end the session with the **End Chat** button

> The therapist must **reply to** the message (not type freely). This is how the system
> routes replies to the correct patient when multiple patients are active simultaneously.

---

For a full explanation of every file and how the system works internally, see `ARCHITECTURE.md`.
