"""
web/repositories/
─────────────────
Per-table SQLite repositories. One module per table — every read, write,
update, or delete for that table goes through here.

Service modules (`web/services/*.py`) call into repositories; routers
(`web/routers/*`) call into services. Routers must NEVER import from
`bot/db.py` directly — go through a repository.

Tables:
  therapists        → therapist_repo
  appointments      → appointment_repo
  intake_sessions   → intake_repo
  availability      → availability_repo
  treatment_notes   → treatment_repo
"""
