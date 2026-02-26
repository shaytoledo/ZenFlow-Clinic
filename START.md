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
TELEGRAM_TOKEN=<your token from @BotFather>
OLLAMA_MODEL=gemma3:latest
OLLAMA_HOST=http://localhost:11434
USE_AI=ollama
```

---

## 3. Set up Ollama

Install the AI model the bot uses for intake questions:

```bash
ollama pull gemma3:latest
```

> The bot will also try to start Ollama automatically on startup, but it's faster if Ollama is already running.

---

## 4. Run the bot

```bash
python run.py
```

That's it. The bot is now live on Telegram.

---

## 5. Monitor logs

Logs are written to `botLogs.text` in the project root:

```bash
# Windows (PowerShell)
Get-Content botLogs.text -Wait

# Mac / Linux
tail -f botLogs.text
```

---

## Data storage

| Path | Contents |
|------|----------|
| `data/appointments/{user_id}/` | One JSON file per appointment |
| `data/chat_history/{user_id}_intake.json` | LangChain conversation history (cleared after each intake) |
| `data/therapist_messages/` | Messages sent to the therapist |

---

## Switching AI provider

**Dev → Production (Ollama → Anthropic)**

1. In `.env`, set:
   ```
   USE_AI=anthropic
   ANTHROPIC_API_KEY=your-key-here
   ```
2. In `bot/services/ai_intake.py`, the comment at `_get_history()` shows where to swap in Redis.

**Ollama → Redis for history storage**

In `ai_intake.py`, replace `_get_history()`:
```python
from langchain_community.chat_message_histories import RedisChatMessageHistory
return RedisChatMessageHistory(session_id=str(user_id), url="redis://localhost:6379")
```
