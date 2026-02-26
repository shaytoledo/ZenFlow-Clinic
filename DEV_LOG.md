ehat # ZenFlow Clinic Bot — Dev Log

---

## Session 1 — 2026-02-26

### Goal
Build the initial Telegram bot for ZenFlow Clinic from scratch.

### What was built
- Full project structure under `Clinic/`
- Conversation state machine with 6 states:
  `SELECTING → SCHEDULE_DAY → SCHEDULE_HOUR → INTAKE → (back to SELECTING)`
  `SELECTING → CANCEL_SELECT → (back to SELECTING)`
  `SELECTING → THERAPIST_INPUT → (back to SELECTING)`
- **Main menu**: 3 inline keyboard options (Schedule / Cancel / Connect to Therapist)
- **Schedule flow**: pick day (next 7 workdays, Sun–Fri) → pick hour → AI intake (5 adaptive questions) → save to file
- **Cancel flow**: lookup appointments by Telegram user ID → select → mark as cancelled in file
- **Therapist flow**: collect free-text message → save to `data/therapist_messages/`
- **AI intake**: Ollama (local LLM) with predefined fallback questions if Ollama is unavailable
- **File storage**: `data/appointments/{patient_id}_{date}_{HH-MM}.json`

### Architecture decisions
- `python-telegram-bot` v21 (async) — stable `ConversationHandler` with inline keyboards
- File-based JSON storage chosen over DB for simplicity; Google Calendar / DB planned for next phase
- Ollama for dev (free, local) → Anthropic Claude for production
- Conversation state lives in `context.user_data` (in-memory, resets on bot restart)

---

## Session 2 — 2026-02-26

### Bugs fixed
- **Ollama hanging** (root cause of all 3 bugs): Ollama `chat()` calls had no timeout — if Ollama wasn't warm, the coroutine hung forever, freezing the entire bot and preventing state transitions (back button appeared broken, intake never finished, appointments never saved).
  - Fix: `asyncio.wait_for(..., timeout=30)` on every Ollama call in `ai_intake.py`.
- **Ollama auto-start**: `main.py` now runs `_ensure_ollama()` via `post_init` — checks reachability, starts `ollama serve` if down, pulls model if missing.
- **Cancel not finding appointments**: Changed storage from flat `{id}_{date}_{time}.json` to `data/appointments/{patient_id}/{date}_{time}.json` — one folder per patient, trivial to list.
- **File logging**: Added `FileHandler` writing to `bot.log` in project root.
- **Logging throughout**: All handlers log state transitions with `[patient_id]` prefix.

### Architecture changes
- `appointments.py` now uses `pathlib.Path` throughout (Windows-safe).
- Appointment lookup: `data/appointments/{patient_id}/*.json` — O(n) per patient, no scan needed.
- Availability check: `data/appointments/*/{date}_*.json` — scans all patient dirs for a given day.

### Known limitations / next TODOs
- [ ] State lost on bot restart — needs persistence (e.g. `PicklePersistence`)
- [ ] Therapist messages stored in files, not forwarded to a Telegram chat
- [ ] No double-booking race condition guard
- [ ] Availability is a stub (Sun–Fri 09:00–14:00) → Google Calendar integration next
- [ ] Anthropic integration wired but not activated
