# ZenFlow Clinic Bot — How to Start

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.11+ | https://python.org |
| Ollama | latest | https://ollama.com |
| Git | any | https://git-scm.com |

---

## 1. Install dependencies

```bash
cd Clinic
python -m venv .venv

# Windows
.venv\Scripts\activate

# Mac / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

---

## 2. Configure environment

Create a `.env` file in the project root (already exists — do not commit it):

```
TELEGRAM_TOKEN=<patient bot token from @BotFather>
THERAPIST_BOT_TOKEN=<therapist bot token from @BotFather>
THERAPIST_TELEGRAM_ID=<therapist's numeric Telegram user ID>
OLLAMA_MODEL=gemma3:latest
OLLAMA_HOST=http://localhost:11434
USE_AI=ollama
```

---

## 3. Set up the two bots

### Patient bot
Create via `@BotFather` → `/newbot` → copy the token → paste as `TELEGRAM_TOKEN`.

### Therapist bot
Create a **second** bot via `@BotFather` → `/newbot` → copy the token → paste as `THERAPIST_BOT_TOKEN`.

This bot is the therapist's private workspace. Patients never see or interact with it.

### Therapist Telegram ID
1. Ask the therapist to message `@userinfobot` on Telegram
2. It replies with their numeric ID, e.g. `Your ID: 918187404`
3. Paste that number as `THERAPIST_TELEGRAM_ID`

### Therapist must start both bots once
Telegram does not allow bots to message a user who has never contacted them.
The therapist must open **both** the patient bot and the therapist bot and send any message (e.g. `/start`) **once each**.

---

## 4. Set up Ollama

Install the AI model used for intake questions:

```bash
ollama pull gemma3:latest
```

> The bot will also try to start Ollama automatically on startup.

---

## 5. Run the bot

```bash
python run.py
```

This starts **both** bots concurrently in a single process.

> **Important:** Only one instance should be running at a time.
> If you get `409 Conflict` errors, kill any old instances first:
> ```bash
> # Windows
> taskkill /F /IM python.exe
> ```

---

## 6. Monitor logs

Logs are written to `botLogs.text` in the project root:

```bash
# Windows (PowerShell)
Get-Content botLogs.text -Wait

# Mac / Linux
tail -f botLogs.text
```

---

## How the therapist relay works

- Patient taps **Connect to Therapist** → types a message → forwarded to the therapist's dedicated bot
- Therapist **replies** to that forwarded message → patient receives it instantly
- Patient can keep typing freely — each message is forwarded automatically
- Every message the patient sees has a **🔚 End Chat** button — they can end at any time
- The therapist **must reply** to the forwarded message (not type freely) — this is how the bot knows which patient to route to when multiple patients are active simultaneously

---

## Data storage

| Path | Contents |
|------|----------|
| `data/appointments/{user_id}/` | One JSON file per **active** appointment; deleted on cancel |
| `data/chat_history/{user_id}_intake.json` | Temporary LangChain history for AI intake questions — cleared after each booking |
| `data/relay_sessions.json` | Maps therapist-bot message IDs → patient IDs for reply routing |

---

## Switching AI provider

**Ollama → Anthropic (production)**

1. In `.env`:
   ```
   USE_AI=anthropic
   ANTHROPIC_API_KEY=your-key-here
   ```
2. Swap the LLM in `bot/services/ai_intake.py`.

**Ollama → Redis for history storage**

In `ai_intake.py`, replace `_get_history()`:
```python
from langchain_community.chat_message_histories import RedisChatMessageHistory
return RedisChatMessageHistory(session_id=str(user_id), url="redis://localhost:6379")
```
