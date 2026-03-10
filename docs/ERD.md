# ZenFlow — Entity Relationship Diagram

> Render at https://mermaid.live — paste the code block below.

---

```mermaid
erDiagram
    THERAPISTS {
        TEXT    id                PK  "t1, t2, ..."
        TEXT    name
        INTEGER telegram_id           "0 until bot activation"
        TEXT    email                 "nullable"
        TEXT    password_hash         "PBKDF2-SHA256 or NULL (Google-only)"
        TEXT    google_id             "Google OAuth sub or NULL"
        TEXT    calendar_name         "default: ZenFlow Availability"
        INTEGER active                "0=false 1=true"
        TEXT    created_at            "datetime('now')"
    }

    APPOINTMENTS {
        INTEGER id                PK  AUTOINCREMENT
        INTEGER patient_id            "Telegram user_id"
        TEXT    patient_name
        TEXT    therapist_id      FK  "→ therapists.id"
        TEXT    date                  "YYYY-MM-DD"
        TEXT    time                  "HH:MM"
        TEXT    status                "active | cancelled (soft delete)"
        TEXT    gcal_apt_event_id     "Google Calendar event id or local_... or NULL"
        TEXT    summary               "AI-generated clinical summary"
        TEXT    created_at
    }

    INTAKE_SESSIONS {
        INTEGER id                PK  AUTOINCREMENT
        INTEGER appointment_id    FK  "→ appointments.id"
        INTEGER patient_id
        TEXT    therapist_id      FK  "→ therapists.id"
        TEXT    history_json          "JSON: [{role, content}, ...]"
        TEXT    created_at
    }

    AVAILABILITY {
        TEXT    id                PK  "uuid4().hex"
        TEXT    therapist_id      FK  "→ therapists.id"
        TEXT    start_dt              "YYYY-MM-DDTHH:MM:SS"
        TEXT    end_dt                "YYYY-MM-DDTHH:MM:SS"
    }

    TREATMENT_NOTES {
        INTEGER id                PK  AUTOINCREMENT
        INTEGER appointment_id    FK  UNIQUE "→ appointments.id (1:1)"
        INTEGER patient_id
        TEXT    tcm_pattern           "e.g. Liver Qi Stagnation"
        TEXT    treatment_principles
        INTEGER diagnosis_certainty   "0–100 (AI confidence %)"
        TEXT    ai_suggested_points   "JSON: [{code, rationale}]"
        TEXT    ai_recommendations    "JSON: {diet, sleep, exercise, stress}"
        TEXT    tongue_observation    "Therapist-entered"
        TEXT    pulse_observation     "Therapist-entered"
        TEXT    session_notes         "Therapist free text"
        TEXT    used_points           "JSON: [ST36, LR3, ...]"
        TEXT    recommendations_sent_at "ISO datetime or NULL"
        TEXT    completed_at          "Set when Complete Session clicked"
        TEXT    created_at
        TEXT    updated_at
    }

    THERAPISTS    ||--o{ APPOINTMENTS       : "treats"
    THERAPISTS    ||--o{ INTAKE_SESSIONS    : "reviews"
    THERAPISTS    ||--o{ AVAILABILITY       : "has slots"
    APPOINTMENTS  ||--o| INTAKE_SESSIONS    : "has one"
    APPOINTMENTS  ||--o| TREATMENT_NOTES   : "has one"
```

---

## Table Reference

| Table | PK type | Rows expected | Written by |
|---|---|---|---|
| `therapists` | TEXT (`t1`, `t2`, …) | < 20 | `init_db()`, web registration, bot activation |
| `appointments` | INTEGER AUTOINCREMENT | Thousands | `save_appointment()` |
| `intake_sessions` | INTEGER AUTOINCREMENT | 1:1 with appointments | `save_appointment()` |
| `availability` | UUID hex | Tens–hundreds | Web `/api/availability`, `book_slot()`, `restore_slot()` |
| `treatment_notes` | INTEGER AUTOINCREMENT | 1:1 with appointments | `save_treatment_notes()`, web `/complete` |

---

## Key Constraints

```sql
-- Foreign keys enforced via PRAGMA foreign_keys=ON
appointments.therapist_id  → therapists.id
intake_sessions.appointment_id → appointments.id
intake_sessions.therapist_id   → therapists.id
availability.therapist_id      → therapists.id
treatment_notes.appointment_id → appointments.id UNIQUE

-- No hard deletes on clinical tables
-- appointments.status = 'cancelled' (soft delete)
-- intake_sessions: never deleted
-- treatment_notes: UPSERT only, never deleted
```

---

## Relationship Notes

### `appointments` ↔ `intake_sessions` (1:optional-1)

Every appointment has at most one intake session record. If the patient skipped intake, `history_json` is `'[]'` (empty array). The record is created in the same transaction as the appointment.

### `appointments` ↔ `treatment_notes` (1:optional-1)

`treatment_notes.appointment_id` has a UNIQUE constraint — enforced at the database level. Created at the same time as the appointment (with blank fields for therapist-entered data, AI data populated immediately after intake).

### `therapists` ↔ `availability` (1:many)

Each therapist has their own set of availability slots. The `therapist_id` column in `availability` uses `"default"` as a fallback when no specific therapist is identified (legacy, rarely used).
