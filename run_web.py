"""
ZenFlow — Web dashboard only (development).

    python run_web.py

Opens at: http://localhost:8000

Routes:
    /               Dashboard — today's schedule + stats
    /schedule       FullCalendar availability manager (Google Calendar)
    /patients       Patient list + session history
    /messages       Active relay conversations
    /settings       Google Calendar auth + therapist registration link
    /register       Therapist self-registration (public, no login needed)
    /treatment/...  Per-session treatment notes

Requires Redis to be running:
    redis-server          (Windows: start Redis service or use WSL)
    redis-cli ping        → should return PONG
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("web.app:app", host="0.0.0.0", port=8000, reload=True)
