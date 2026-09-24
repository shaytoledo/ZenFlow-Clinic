"""
zenflow.patient_export
───────────────────────
Gather everything the clinic holds about one patient — the GDPR "right of access" (Phase 9.9).

    python -m zenflow.patient_export <patient_id>            # JSON to stdout
    python -m zenflow.patient_export <patient_id> --out p.json

`export_patient(patient_id)` reads every table that holds patient data (`docs/DATA_LAYER.md`) filtered
to that patient, and returns one JSON-serialisable dict — one section per data class. It is
**read-only** and strictly scoped: each query filters by `patient_id` (the AI-call section by the
patient's own appointment ids), so no other patient's data can leak into the export.

This is the access half of the data-subject procedures. Deletion/erasure and the retention periods
are a separate, owner-gated task (retention lengths and the legal basis are owner decision Q5, and
deletion has to reconcile with the append-only audit trail and clinical-record retention) — see
`docs/DATA_LAYER.md`.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from typing import Any

from zenflow.clock import iso_now


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def export_patient(patient_id: int) -> dict[str, Any]:
    """Everything the clinic holds about `patient_id`, one section per data class."""
    from bot.db import get_db

    conn = get_db()
    patient = _rows(conn, "SELECT * FROM patients WHERE id=? ORDER BY rowid", (patient_id,))
    appointments = _rows(
        conn, "SELECT * FROM appointments WHERE patient_id=? ORDER BY rowid", (patient_id,)
    )
    apt_ids = [a["id"] for a in appointments]
    if apt_ids:
        placeholders = ",".join("?" * len(apt_ids))
        ai_calls = _rows(
            conn,
            f"SELECT * FROM ai_calls WHERE appointment_id IN ({placeholders}) ORDER BY rowid",  # noqa: S608 - ids are ints from our own query
            tuple(apt_ids),
        )
    else:
        ai_calls = []

    def by_patient(table: str) -> list[dict[str, Any]]:
        return _rows(
            conn, f"SELECT * FROM {table} WHERE patient_id=? ORDER BY rowid", (patient_id,)
        )

    return {
        "patient_id": patient_id,
        "exported_at": iso_now(),
        "patient": patient[0] if patient else None,
        "channels": by_patient("patient_channels"),
        "appointments": appointments,
        "intake_sessions": by_patient("intake_sessions"),
        "treatment_notes": by_patient("treatment_notes"),
        "messages": by_patient("message_log"),
        "notifications": by_patient("notifications"),
        "followups": by_patient("followups"),
        "ai_calls": ai_calls,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export everything the clinic holds about one patient (GDPR access)."
    )
    parser.add_argument("patient_id", type=int, help="the internal patients.id")
    parser.add_argument("--out", help="write JSON to this file instead of stdout")
    args = parser.parse_args(argv)

    data = export_patient(args.patient_id)
    if data["patient"] is None:
        print(f"No patient with id {args.patient_id}", file=sys.stderr)
        return 1

    text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
        counts = {k: len(v) for k, v in data.items() if isinstance(v, list)}
        print(f"wrote {args.out} — {counts}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
