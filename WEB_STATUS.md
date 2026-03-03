# ZenFlow Therapist Web App — Project Status

## What Was Built

### Architecture
- **Framework:** FastAPI + Jinja2 templates
- **Run:** `python run_web.py` → `http://localhost:8000`
- **Shared layout:** `web/templates/base.html` — persistent sidebar with navigation, live clock, active-session badge
- **CSS namespace:** all new styles use `zf-` prefix in `web/static/style.css`
- **Design system:** teal `#0D9488` primary, Inter font, white cards with `border-radius: 14px`

### Pages Built

| Route | Template | Status |
|-------|----------|--------|
| `/` | `dashboard.html` | ✅ Done |
| `/schedule` | `schedule.html` | ✅ Done (existing calendar moved here) |
| `/patients` | `patients.html` | ✅ Done |
| `/messages` | `messages.html` | ✅ Done |
| `/settings` | `settings.html` | ✅ Done |
| `/treatment/{id}/{date}/{time}` | `treatment.html` | ✅ Done |

### APIs Added (`web/app.py`)

| Endpoint | Purpose |
|----------|---------|
| `GET /api/appointments/today` | All today's appointments from JSON files |
| `GET /api/patients` | Aggregated patient list (sessions, last visit, intake count) |
| `GET /api/patients/{id}` | Single patient detail + full appointment history |
| `GET /api/appointment/{pid}/{date}/{time}` | Single appointment with full intake history |
| `GET /api/messages/active` | Count of patients in active Telegram relay |

### Data Source
All data is read from `data/appointments/{patient_id}/*.json` — no database.

---

## What Each Page Does

### Dashboard (`/`)
- 4 stat cards: today's appointments, intake forms, total patients, total sessions
- Today's schedule list with "Open Session" button per appointment
- Intake alerts panel (today's appointments that have AI intake notes)
- Recent appointments table (last 10 across all patients)

### Schedule (`/schedule`)
- FullCalendar week view (existing availability manager)
- Drag to create availability slots
- Click slot to delete
- Synced with Google Calendar "ZenFlow Availability" calendar

### Patients (`/patients`)
- Searchable table (by name or Telegram ID)
- Filter chips: All / Active / New
- Patient row shows: name, last appointment, session count, intake forms, status
- Click "View History" → slide-in drawer with full appointment history
- Click "Last Session" → opens treatment page for that appointment

### Treatment Session (`/treatment/{id}/{date}/{time}`)
- Patient header bar (name, date, session badge)
- **Left column:**
  - AI Intake Summary (collapsible, shows full Q&A from Telegram bot)
  - Clinical Summary (AI-generated summary text)
  - Tongue & Pulse input fields
- **Right column:**
  - Suggested acupuncture points (detected from summary keywords)
  - Points Used Today (tag-style input — type code + Enter)
  - Click any suggested point → info panel slides in from right (location, channel, actions)
  - Session Notes (auto-save indicator)
  - AI Lifestyle Advice (4 toggleable items with emoji)
  - "Send Approved Advice via Telegram" button
  - "Complete Session" button
- Built-in point reference: ST36, LI4, PC6, LR3, SP6, GV20, HT7, KD3

### Messages (`/messages`)
- Left panel: patient list with conversation indicators
- Right panel: intake conversation thread (Q&A from Telegram bot)
- Shows all intake sessions per patient in chronological order

### Settings (`/settings`)
- Google Calendar connection status + reconnect button
- Bot configuration overview (bot names, AI model)
- Clinic info form (name, timezone, therapist name, session duration)
- Data stats (total patients, sessions, intake forms)

---

## Known Issues / Bugs

1. **Corrupted appointment file:**
   `data/appointments/5501111146/2026-03-02_11-00.json` — trailing comma in JSON (Ollama generation bug).
   Fix: open the file, remove the trailing comma before `}`.

2. **`menu-btn` removed from schedule page:**
   Old schedule had a hamburger toggle for its sidebar. Now the schedule is inside the new layout, sidebar toggle was removed. The CSS class `.collapsed` is no longer triggered. Cosmetic only.

3. **Treatment page: session count is always "Session —":**
   Need to count how many appointments this patient has had before this date to show "Session 3 of 7" etc. Not yet implemented.

4. **`data/therapist_messages/` folder still exists:**
   Old therapist message logs. No longer written to (was removed in a previous session). The folder and files are orphaned and can be deleted.

---

## Open Features To Implement

### High Priority

- [ ] **Complete Session button** — save tongue/pulse notes + points used + session notes to a `session_notes` field in the appointment JSON (currently nothing is persisted from the treatment page)
- [ ] **Real "Send via Telegram" button** — actually call `Bot(TELEGRAM_TOKEN).send_message(patient_id, ...)` from the web app (need async Telegram client in FastAPI)
- [ ] **Today's schedule accuracy** — if no appointments were booked today, the dashboard shows empty. Works correctly once patients book. Test by checking `data/appointments/` for today's date.
- [ ] **Patient name persists in drawer** — currently reads `patient_name` from first appointment file. If the patient never used the bot, there's no file. Add a manual "Add Patient" flow.

### Medium Priority

- [ ] **Improvement chart on dashboard** — The Figma design shows a line chart of avg improvement scores over weeks. Needs a `score` field in appointment JSON + a chart library (Chart.js is lightweight).
- [ ] **Add Patient button** — Patients page has a search bar but no way to add a patient manually (without them booking through Telegram).
- [ ] **Patient profile page** — Full dedicated page per patient with photo, notes, treatment history timeline, not just the drawer.
- [ ] **TCM Syndrome suggestion** — Treatment page shows "Liver Qi Stagnation" as a hardcoded example. Should call Ollama to generate a real TCM diagnosis from the intake conversation.
- [ ] **More acupuncture points in reference** — Only 8 points are in the JS reference dict. Should have ST, LI, LR, SP, PC, GV, CV, HT, KD, BL, GB, TE series.
- [ ] **Messages page: real-time** — Currently shows only intake Q&A. Should show actual Telegram relay messages in real time (requires WebSocket or polling).
- [ ] **Search across all intake notes** — Let the therapist search "lower back pain" and find all patients with that complaint.

### Low Priority / Polish

- [ ] **Settings: actually save to `.env`** — Clinic info form is cosmetic. Wire to write back to `.env` or a `config.json`.
- [ ] **Dark mode** — Toggle in settings.
- [ ] **Mobile responsive** — Sidebar collapses on small screens. Currently not handled for the new layout.
- [ ] **Export to PDF** — "Print / Export" button on treatment page to generate a PDF session report.
- [ ] **Appointment status** — Add `status: done | in_progress | upcoming` to appointment JSON so the dashboard shows color-coded status badges.
- [ ] **Pagination on patients page** — Works fine for small lists but needs pagination if > 50 patients.
- [ ] **`data/therapist_messages/` cleanup** — Delete the old orphaned folder and its files.

---

## File Map

```
web/
├── app.py                  ← FastAPI routes + data helpers
├── gcal.py                 ← Google Calendar OAuth + API wrapper
├── templates/
│   ├── base.html           ← Shared sidebar layout (zf- namespace)
│   ├── dashboard.html      ← / route
│   ├── schedule.html       ← /schedule route (wraps FullCalendar)
│   ├── patients.html       ← /patients route
│   ├── treatment.html      ← /treatment/{id}/{date}/{time}
│   ├── messages.html       ← /messages route
│   └── settings.html       ← /settings route
└── static/
    ├── style.css           ← Old calendar styles + new zf- styles appended at bottom
    └── app.js              ← FullCalendar JS (schedule page only)

data/
├── appointments/{patient_id}/{date}_{time}.json   ← One file per appointment
├── relay_sessions.json                             ← Active Telegram relay sessions
└── google_token.json                               ← Google OAuth token (do not commit)
```

---

## Environment Variables Needed

| Variable | Purpose |
|----------|---------|
| `TELEGRAM_TOKEN` | Patient bot |
| `THERAPIST_BOT_TOKEN` | Therapist bot |
| `THERAPIST_TELEGRAM_ID` | Therapist's Telegram user ID |
| `OLLAMA_MODEL` | Default: `gemma3:latest` |
| `OLLAMA_HOST` | Default: `http://localhost:11434` |
| `GOOGLE_CLIENT_ID` | For Google Calendar OAuth |
| `GOOGLE_CLIENT_SECRET` | For Google Calendar OAuth |
| `GOOGLE_REDIRECT_URI` | Default: `http://localhost:8000/auth/callback` |

---

## Quick Start

```bash
# Start everything (setup + bots + web dashboard)
python launch.py

# Or individually (development)
python run_bots.py   # Telegram bots only
python run_web.py    # Web dashboard only  →  http://localhost:8000

# First time: connect Google Calendar
# → http://localhost:8000/auth/login
```
