# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project
ZenFlow Clinic — Telegram bot for a Traditional Chinese Medicine (TCM) acupuncture clinic.

## Commands

```bash
# Run directly (venv must be active, .env must exist)
python run.py

# Easiest start — checks everything, installs deps, starts Ollama, runs bot
python setup_and_run.py

# Pull the required AI model (first time only)
ollama pull gemma3:latest
```

## Architecture

```
bot/
├── main.py            # Wires patient ConversationHandler + runs both bots via asyncio
├── states.py          # Integer state constants (SELECTING, SCHEDULE_DAY, …)
├── config.py          # Env vars via python-dotenv
├── utils.py           # Shared: get_main_keyboard()
├── handlers/
│   ├── start.py       # Entry point + back_to_main callback
│   ├── schedule.py    # show_days → show_hours → confirm_appointment → handle_intake_answer
│   ├── cancel.py      # show_appointments → confirm_cancel
│   └── therapist.py   # ask_therapist_message → start_relay → relay_to_therapist → end_chat
├── therapist_bot/
│   ├── __init__.py
│   ├── main.py        # build_therapist_app() — separate bot for therapist
│   └── handlers.py    # handle_therapist_reply — routes therapist replies to patients
└── services/
    ├── availability.py   # get_available_days / get_available_hours (stub → Google Calendar)
    ├── appointments.py   # File-based JSON storage in data/appointments/
    ├── ai_intake.py      # Ollama AsyncClient; fallback questions if Ollama is down
    └── relay.py          # data/relay_sessions.json — maps therapist msg IDs → patient IDs

data/
├── appointments/{patient_id}/   # one JSON per active appointment: {YYYY-MM-DD}_{HH-MM}.json
├── chat_history/                # {user_id}_intake.json — temp LangChain history, cleared after intake
├── therapist_messages/          # legacy save dir (kept for reference)
└── relay_sessions.json          # active relay mappings
```

## Conversation state machine
```
Any message / /start → SELECTING (main menu)
  SELECTING → schedule  → SCHEDULE_DAY → SCHEDULE_HOUR → INTAKE_CONFIRM
                          → Yes → INTAKE (×5 adaptive AI questions) → SELECTING
                          → No  → SELECTING (saved without intake)
  SELECTING → cancel    → CANCEL_SELECT → SELECTING (file deleted on confirm)
  SELECTING → therapist → THERAPIST_INPUT → THERAPIST_RELAY (loop) → SELECTING
                                             patient types → forwarded to therapist bot
                                             therapist replies → delivered to patient
                                             patient presses End Chat → SELECTING
```

## Two-bot relay architecture
- **Patient bot** (`TELEGRAM_TOKEN`): patient-facing; forwards messages to therapist via `Bot(THERAPIST_BOT_TOKEN)`
- **Therapist bot** (`THERAPIST_BOT_TOKEN`): therapist-facing; receives forwarded messages, routes replies back via `Bot(TELEGRAM_TOKEN)`
- Both bots run concurrently in the same process via `asyncio.run(_run(patient_app, therapist_app))`
- Routing key: `relay_sessions.json` maps therapist-bot message ID → patient user ID
- Therapist **must reply** to the forwarded message (not type freely) for routing to work

## Key conventions
- All Telegram handlers are `async def (update, context) -> int` returning the next state constant.
- `context.user_data` holds in-flight booking state (`selected_day`, `selected_time`, `intake_count`). Cleared on completion, skip, or cancellation.
- `allow_reentry=False` is critical — setting it True causes the catch-all entry point `MessageHandler(filters.ALL, start)` to intercept text messages in active states (INTAKE, THERAPIST_INPUT), breaking those flows.
- Cancelled appointments are **deleted** (not marked). `get_patient_appointments()` returns only files that exist and have `status == "active"`.
- All Ollama calls are wrapped in `asyncio.wait_for(..., timeout=100)`. Fallback questions are used if Ollama is unreachable or slow.
- `availability.py` imports `appointments.py` (not the other way around) to avoid circular imports.

## Environment variables (`.env`)
| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_TOKEN` | — | Patient bot token from @BotFather |
| `THERAPIST_BOT_TOKEN` | — | Therapist bot token (separate bot from @BotFather) |
| `THERAPIST_TELEGRAM_ID` | — | Therapist's Telegram user ID (integer) |
| `OLLAMA_MODEL` | `gemma3:latest` | Local LLM model name |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `USE_AI` | `ollama` | `ollama` or `anthropic` (future) |

## Current status — what works
- Schedule appointment: pick day/hour → optional 5-question AI intake → appointment saved with clinical summary
- Cancel appointment: lists active appointments, deletes the file on confirm, clears chat history
- Connect to therapist: two-way relay via dedicated therapist bot; patient sees ✅ Sent + End Chat button after each message; therapist replies are delivered instantly
- All back buttons functional
- Ollama auto-starts on bot startup; fallback questions if model is unavailable

## Planned next steps
- Google Calendar integration for real availability
- `PicklePersistence` to survive bot restarts
- Switch `USE_AI=anthropic` for production
