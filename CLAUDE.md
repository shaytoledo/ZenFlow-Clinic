# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project
ZenFlow Clinic — Telegram bot for a Traditional Chinese Medicine (TCM) acupuncture clinic.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the bot
python run.py
# or
python -m bot.main

# Ollama must be running locally before starting (for AI intake)
ollama run llama3.2
```

## Architecture

```
bot/
├── main.py            # ConversationHandler wiring + entry point
├── states.py          # Integer state constants (SELECTING, SCHEDULE_DAY, …)
├── config.py          # Env vars via python-dotenv
├── utils.py           # Shared: get_main_keyboard()
├── handlers/
│   ├── start.py       # Entry point + back_to_main callback
│   ├── schedule.py    # show_days → show_hours → confirm_appointment → handle_intake_answer
│   ├── cancel.py      # show_appointments → confirm_cancel
│   └── therapist.py   # ask_therapist_message → forward_to_therapist
└── services/
    ├── availability.py   # get_available_days / get_available_hours (stub → Google Calendar)
    ├── appointments.py   # File-based JSON storage in data/appointments/
    └── ai_intake.py      # Ollama AsyncClient; fallback questions if Ollama is down

data/
├── appointments/         # {patient_id}_{YYYY-MM-DD}_{HH-MM}.json
└── therapist_messages/   # {patient_id}_{timestamp}.json
```

## Conversation state machine
```
Any message / /start → SELECTING (main menu)
  SELECTING → schedule  → SCHEDULE_DAY → SCHEDULE_HOUR → INTAKE (×5) → SELECTING
  SELECTING → cancel    → CANCEL_SELECT → SELECTING
  SELECTING → therapist → THERAPIST_INPUT → SELECTING
```

## Key conventions
- All Telegram handlers are `async def (update, context) -> int` returning the next state constant.
- `context.user_data` holds in-flight booking state (`selected_day`, `selected_time`, `intake_history`, `intake_count`). It is cleared on completion or restart.
- Appointment files use the format `{patient_id}_{YYYY-MM-DD}_{HH-MM}.json`. The `patient_id` is the Telegram user ID.
- `availability.py` imports `appointments.py` (not the other way around) to avoid circular imports.
- AI (Ollama/Anthropic) is always called with `await`; fallback to predefined questions on any exception.

## Environment variables (`.env`)
| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_TOKEN` | — | Bot token from @BotFather |
| `OLLAMA_MODEL` | `llama3.2` | Local LLM model name |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `USE_AI` | `ollama` | `ollama` or `anthropic` (future) |

## Planned next steps
- Google Calendar integration for real availability
- `PicklePersistence` to survive bot restarts
- Forward therapist messages to a Telegram group/chat
- Switch `USE_AI=anthropic` for production
