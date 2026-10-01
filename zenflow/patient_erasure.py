"""
zenflow.patient_erasure
────────────────────────
Erase one patient — the deletion half of the data-subject procedures (Phase 9.9; the access half is
`zenflow.patient_export`).

    python -m zenflow.patient_erasure <patient_id> --reason "request 2026-10-01"            # plan
    python -m zenflow.patient_erasure <patient_id> --reason "..." --apply                   # do it
    python -m zenflow.patient_erasure <patient_id> --mode purge --reason "..." --apply      # all

Nothing changes without `--apply`; without it the command prints what it would do.

**Two modes**, because a clinic's records are not ordinary personal data (`docs/DATA_PROTECTION.md`):

`anonymize` (default) — the patient can no longer be identified or contacted, and the clinical record
stays, de-identified, for as long as the law requires it to:
    - the identity row keeps its id but loses the name, phone, email, notes and legacy id;
    - every channel identity (Telegram/WhatsApp id) is deleted, so nothing can message them and a
      new conversation from the same account starts a new, unrelated patient;
    - appointment rows lose the name, phone and email; message metadata and dashboard
      notifications about them are deleted; any dev-only AI prompt copy is cleared;
    - queued jobs for their appointments (follow-ups, confirmations) are dropped;
    - bot conversation state and Redis keys under their Telegram id are deleted;
    - the identity fields inside `audit_log` before/after values are overwritten (see below).
    Kept: `treatment_notes`, `intake_sessions`, `followups`, the appointments themselves, the
    `ai_calls` meters (hashes and numbers) and the audit rows — the clinical record.

`purge` — every row about the patient is deleted, the clinical record and its audit trail included.
It is for a record whose retention period is over (`ZF_RETENTION_CLINICAL_YEARS`, used by
`zenflow.retention`), so it is refused while that period runs — or always, while the period is
0 = keep forever — unless `--override-retention` says a person has decided otherwise.

**The audit trail.** `audit_log` is append-only by trigger (8.1). Erasure is the one sanctioned
exception: in a single transaction it lifts the guard, rewrites (anonymize) or deletes (purge) the
patient's rows, and puts the guard back — then records `patient.erased` / `patient.purged` with the
mode, reason and counts and nothing that identifies the patient. A failure rolls the whole thing
back, guard included. The trigger protects the trail from the application; anyone able to run this
command already holds the database file.

Not reachable from here, listed in the report for a person to do: events in the therapist's Google
Calendar (their titles carry the patient's name), copies in backups (they age out with the backup
rotation), and anything already delivered to the patient's own devices.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

from zenflow import clock

logger = logging.getLogger(__name__)

Mode = Literal["anonymize", "purge"]
ERASED_NAME = "Erased patient"
#: keys in an audit before/after value that identify a person
IDENTITY_KEYS = frozenset(
    {
        "patient_name",
        "patient_phone",
        "patient_email",
        "full_name",
        "name",
        "phone",
        "email",
        "external_id",
        "legacy_id",
    }
)
#: audit entities recorded under an appointment id (8.5)
_APPOINTMENT_ENTITIES = ("appointment", "treatment_notes", "followup")


class ErasureRefused(RuntimeError):
    """The erasure was not allowed (unknown patient, or a purge inside the retention period)."""


@dataclass
class ErasureReport:
    patient_id: int
    mode: Mode
    applied: bool
    counts: dict[str, int] = field(default_factory=dict)
    manual_steps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "patient_id": self.patient_id,
            "mode": self.mode,
            "applied": self.applied,
            "counts": self.counts,
            "manual_steps": self.manual_steps,
        }


def _ids(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[Any]:
    return [row[0] for row in conn.execute(sql, params).fetchall()]


def _in(values: list[Any]) -> str:
    return ",".join("?" * len(values))


def _last_appointment_date(conn: sqlite3.Connection, patient_id: int) -> str | None:
    row = conn.execute(
        "SELECT MAX(date) FROM appointments WHERE patient_id=?", (patient_id,)
    ).fetchone()
    return row[0] if row and row[0] else None


def retention_allows_purge(conn: sqlite3.Connection, patient_id: int) -> bool:
    """True when the patient's clinical retention period is over (never, while it is 0)."""
    from zenflow.settings import get_settings

    years = get_settings().flags.retention_clinical_years
    if years <= 0:
        return False
    last = _last_appointment_date(conn, patient_id)
    if last is None:
        created = conn.execute(
            "SELECT created_at FROM patients WHERE id=?", (patient_id,)
        ).fetchone()
        last = (created[0] or "")[:10] if created else ""
    cutoff = (clock.today() - timedelta(days=365 * years)).isoformat()
    return bool(last) and last[:10] < cutoff


def _channel_ids(conn: sqlite3.Connection, patient_id: int) -> list[str]:
    """Every channel identity of the patient — Telegram-keyed state is keyed by these."""
    ids = _ids(conn, "SELECT external_id FROM patient_channels WHERE patient_id=?", (patient_id,))
    legacy = conn.execute("SELECT legacy_id FROM patients WHERE id=?", (patient_id,)).fetchone()
    if legacy and legacy[0] not in (None, ""):
        ids.append(legacy[0])
    return sorted({str(i) for i in ids if str(i).strip()})


def _pending_jobs(conn: sqlite3.Connection, appointment_ids: list[int]) -> list[int]:
    wanted = set(appointment_ids)
    out = []
    for job_id, payload in conn.execute(
        "SELECT id, payload_json FROM jobs WHERE status='pending'"
    ).fetchall():
        try:
            apt = json.loads(payload or "{}").get("appointment_id")
        except (ValueError, AttributeError):
            continue
        if isinstance(apt, int) and apt in wanted:
            out.append(job_id)
    return out


def _persistence_keys(
    conn: sqlite3.Connection, channel_ids: list[str]
) -> list[tuple[str, str, str]]:
    """bot_persistence rows keyed by one of the patient's Telegram ids (user/chat data, states)."""
    wanted = set(channel_ids)
    out = []
    for kind, name, key in conn.execute("SELECT kind, name, key FROM bot_persistence").fetchall():
        if kind == "conversation":
            try:
                parts = [str(p) for p in json.loads(key)]
            except ValueError:
                continue
            if wanted.intersection(parts):
                out.append((kind, name, key))
        elif str(key) in wanted:
            out.append((kind, name, key))
    return out


def _audit_rows(
    conn: sqlite3.Connection, patient_id: int, appointment_ids: list[int]
) -> list[sqlite3.Row]:
    sql = "SELECT id, before_json, after_json FROM audit_log WHERE (entity_type='patient' AND entity_id=?)"
    params: list[Any] = [str(patient_id)]
    if appointment_ids:
        sql += (
            f" OR (entity_type IN ({_in(list(_APPOINTMENT_ENTITIES))})"
            f" AND entity_id IN ({_in(appointment_ids)}))"
        )
        params += [*_APPOINTMENT_ENTITIES, *[str(a) for a in appointment_ids]]
    return conn.execute(sql, tuple(params)).fetchall()


def _scrub(value: str | None) -> str | None:
    """An audit before/after value with every identity field overwritten."""
    if not value:
        return value
    try:
        data = json.loads(value)
    except ValueError:
        return value
    if not isinstance(data, dict):
        return value
    changed = {
        k: ("[erased]" if k in IDENTITY_KEYS and v not in (None, "") else v)
        for k, v in data.items()
    }
    return json.dumps(changed, ensure_ascii=False)


def _redis_patterns(channel_ids: list[str]) -> list[str]:
    patterns = []
    for cid in channel_ids:
        patterns += [
            f"*zenflow:intake:{cid}*",  # intake history (LangChain prefixes "message_store:")
            f"zenflow:relay:active:{cid}",
            f"zenflow:relay:history:*:{cid}",
            f"zenflow:relay:lastseen:*:{cid}",
            f"zenflow:followup:conv:{cid}",
            f"zenflow:followup:awaiting:{cid}",
        ]
    return patterns


def _clear_redis(channel_ids: list[str], apply: bool) -> int:
    """Delete the patient's Redis keys; best effort — Redis is never the source of truth."""
    try:
        from bot.redis_client import get_sync_redis

        r = get_sync_redis()
        keys = {k for pattern in _redis_patterns(channel_ids) for k in r.scan_iter(match=pattern)}
        if apply:
            for key in keys:
                r.delete(key)
            r.delete("zenflow:apts:all")
        return len(keys)
    except Exception as exc:  # noqa: BLE001 - the SQLite erasure stands without Redis
        logger.warning(f"patient erasure: Redis cleanup skipped ({type(exc).__name__})")
        return 0


def _lift_audit_guard(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TRIGGER IF EXISTS audit_log_is_append_only_update")
    conn.execute("DROP TRIGGER IF EXISTS audit_log_is_append_only_delete")


def _restore_audit_guard(conn: sqlite3.Connection) -> None:
    from web.services.audit import CREATE_AUDIT_GUARDS

    for ddl in CREATE_AUDIT_GUARDS:
        conn.execute(ddl)


def erase_patient(
    patient_id: int,
    *,
    mode: Mode = "anonymize",
    reason: str,
    apply: bool = False,
    override_retention: bool = False,
) -> ErasureReport:
    """Erase `patient_id` (see the module docstring). Returns what was — or would be — done."""
    from bot.db import get_db
    from web.services import audit

    if mode not in ("anonymize", "purge"):
        raise ValueError(f"unknown mode {mode!r}")
    if not reason.strip():
        raise ErasureRefused("a reason is required — it is recorded in the audit trail")

    conn = get_db()
    if conn.execute("SELECT 1 FROM patients WHERE id=?", (patient_id,)).fetchone() is None:
        raise ErasureRefused(f"no patient with id {patient_id}")
    if mode == "purge" and not override_retention and not retention_allows_purge(conn, patient_id):
        raise ErasureRefused(
            "the clinical retention period is not over (ZF_RETENTION_CLINICAL_YEARS); "
            "anonymize instead, or pass --override-retention after a documented decision"
        )

    apt_ids = _ids(conn, "SELECT id FROM appointments WHERE patient_id=?", (patient_id,))
    channel_ids = _channel_ids(conn, patient_id)
    report = ErasureReport(patient_id, mode, apply)
    gcal = _ids(
        conn,
        "SELECT gcal_apt_event_id FROM appointments WHERE patient_id=? "
        "AND gcal_apt_event_id IS NOT NULL AND gcal_apt_event_id != ''",
        (patient_id,),
    )
    if gcal:
        report.manual_steps.append(
            f"delete or rename {len(gcal)} Google Calendar event(s) — their titles carry the "
            "patient's name (appointments.gcal_apt_event_id, listed in the plan output)"
        )
    report.manual_steps.append(
        "existing database backups still hold the patient until they age out of the rotation"
    )

    jobs = _pending_jobs(conn, apt_ids)
    persistence = _persistence_keys(conn, channel_ids)
    audit_rows = _audit_rows(conn, patient_id, apt_ids)
    by_patient = ("message_log", "notifications", "patient_channels")
    clinical = ("treatment_notes", "intake_sessions", "followups")

    def count(table: str) -> int:
        sql = f"SELECT COUNT(*) FROM {table} WHERE patient_id=?"  # table names are constants
        return int(conn.execute(sql, (patient_id,)).fetchone()[0])

    report.counts = {t: count(t) for t in (*by_patient, *clinical)}
    report.counts["appointments"] = len(apt_ids)
    report.counts["queued_jobs"] = len(jobs)
    report.counts["bot_state_rows"] = len(persistence)
    report.counts["audit_rows"] = len(audit_rows)
    if apt_ids:
        report.counts["ai_calls"] = conn.execute(
            f"SELECT COUNT(*) FROM ai_calls WHERE appointment_id IN ({_in(apt_ids)})",
            tuple(apt_ids),
        ).fetchone()[0]
    else:
        report.counts["ai_calls"] = 0
    report.counts["redis_keys"] = _clear_redis(channel_ids, apply=False)
    if not apply:
        if gcal:
            report.manual_steps[0] += f": {', '.join(gcal)}"
        return report

    apt_in = _in(apt_ids)
    conn.execute("BEGIN IMMEDIATE")
    try:
        for table in ("message_log", "notifications", "patient_channels"):
            conn.execute(f"DELETE FROM {table} WHERE patient_id=?", (patient_id,))
        if jobs:
            conn.execute(f"DELETE FROM jobs WHERE id IN ({_in(jobs)})", tuple(jobs))
        for kind, name, key in persistence:
            conn.execute(
                "DELETE FROM bot_persistence WHERE kind=? AND name=? AND key=?", (kind, name, key)
            )
        _lift_audit_guard(conn)
        if mode == "anonymize":
            now = clock.iso_now()
            conn.execute(
                """UPDATE patients SET full_name=?, phone=NULL, email=NULL, notes=NULL,
                   legacy_id=NULL, updated_at=? WHERE id=?""",
                (ERASED_NAME, now, patient_id),
            )
            conn.execute(
                """UPDATE appointments SET patient_name=?, patient_phone=NULL, patient_email=NULL
                   WHERE patient_id=?""",
                (ERASED_NAME, patient_id),
            )
            if apt_ids:
                conn.execute(
                    f"UPDATE ai_calls SET prompt_debug=NULL, response_debug=NULL "
                    f"WHERE appointment_id IN ({apt_in})",
                    tuple(apt_ids),
                )
            for row in audit_rows:
                conn.execute(
                    "UPDATE audit_log SET before_json=?, after_json=? WHERE id=?",
                    (_scrub(row["before_json"]), _scrub(row["after_json"]), row["id"]),
                )
        else:
            for table in clinical:
                conn.execute(f"DELETE FROM {table} WHERE patient_id=?", (patient_id,))
            if apt_ids:
                conn.execute(
                    f"DELETE FROM ai_calls WHERE appointment_id IN ({apt_in})",
                    tuple(apt_ids),
                )
            conn.execute("DELETE FROM appointments WHERE patient_id=?", (patient_id,))
            conn.execute("DELETE FROM patients WHERE id=?", (patient_id,))
            if audit_rows:
                ids = [row["id"] for row in audit_rows]
                conn.execute(f"DELETE FROM audit_log WHERE id IN ({_in(ids)})", tuple(ids))
        _restore_audit_guard(conn)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    report.counts["redis_keys"] = _clear_redis(channel_ids, apply=True)
    with audit.acting_as("system", "patient_erasure"):
        audit.record(
            "patient.erased" if mode == "anonymize" else "patient.purged",
            "patient",
            patient_id,
            after={"mode": mode, "reason": reason.strip()[:500], "counts": report.counts},
        )
    logger.info(f"patient {patient_id} {mode}d: {report.counts}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Erase one patient's data (Phase 9.9).")
    parser.add_argument("patient_id", type=int, help="the internal patients.id")
    parser.add_argument("--mode", choices=("anonymize", "purge"), default="anonymize")
    parser.add_argument("--reason", required=True, help="why — recorded in the audit trail")
    parser.add_argument("--apply", action="store_true", help="make the change (default: plan only)")
    parser.add_argument(
        "--override-retention",
        action="store_true",
        help="purge inside the clinical retention period (a documented, human decision)",
    )
    args = parser.parse_args(argv)
    try:
        report = erase_patient(
            args.patient_id,
            mode=args.mode,
            reason=args.reason,
            apply=args.apply,
            override_retention=args.override_retention,
        )
    except ErasureRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
    if not args.apply:
        print("plan only — nothing changed; re-run with --apply", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
