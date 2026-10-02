"""
zenflow.retention
──────────────────
The retention policy for every class of patient data, and the sweep that applies it (Phase 9.9).

    python -m zenflow.retention            # what the policy would remove today
    python -m zenflow.retention --apply    # remove it (run daily from cron / the scheduler)

`POLICY` below is the single source for how long each class is kept; `docs/DATA_PROTECTION.md`
renders it for people. Two knobs, both `ZF_*` settings:

- `ZF_RETENTION_OPERATIONAL_DAYS` (default 730) — operational rows about patients: message metadata,
  AI-call meters, notifications already read or resolved, finished jobs. 0 = keep forever.
- `ZF_RETENTION_CLINICAL_YEARS` (default **0 = keep forever**) — years after a patient's last
  appointment before their whole record is purged (`zenflow.patient_erasure`, mode `purge`). The
  legal minimum for clinical records is owner decision Q5; until it is set, nothing clinical is
  ever removed automatically.

Everything is dated in canonical UTC (`YYYY-MM-DDTHH:MM:SSZ`, ADR-19), so a cutoff is a string
comparison. Rows another component already expires are listed as such and left to it.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from zenflow import clock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DataClass:
    name: str
    tables: str
    kind: str  # identity | clinical | operational | audit | transient
    kept: str


POLICY: tuple[DataClass, ...] = (
    DataClass(
        "Identity",
        "patients",
        "identity",
        "with the clinical record; anonymized on an erasure request",
    ),
    DataClass(
        "Channel identities",
        "patient_channels",
        "identity",
        "with the clinical record; deleted on an erasure request",
    ),
    DataClass(
        "Clinical record",
        "appointments, intake_sessions, treatment_notes, followups",
        "clinical",
        "ZF_RETENTION_CLINICAL_YEARS after the last appointment (0 = forever, Q5)",
    ),
    DataClass(
        "Audit trail",
        "audit_log",
        "audit",
        "as long as the clinical record it describes; purged with it",
    ),
    DataClass(
        "Message metadata",
        "message_log",
        "operational",
        "ZF_RETENTION_OPERATIONAL_DAYS",
    ),
    DataClass("AI-call meters", "ai_calls", "operational", "ZF_RETENTION_OPERATIONAL_DAYS"),
    DataClass(
        "Dashboard notifications",
        "notifications",
        "operational",
        "ZF_RETENTION_OPERATIONAL_DAYS once read or resolved; open alerts stay",
    ),
    DataClass(
        "Background jobs",
        "jobs",
        "operational",
        "ZF_RETENTION_OPERATIONAL_DAYS once done or dead",
    ),
    DataClass(
        "Bot conversation state",
        "bot_persistence",
        "transient",
        "until the flow ends or times out (ZF_CONV_TIMEOUT_MINUTES)",
    ),
    DataClass(
        "Redis (intake history, relay, caches)",
        "Redis",
        "transient",
        "its key TTL — 30 min to 7 days (docs/DATA_LAYER.md)",
    ),
    DataClass(
        "API idempotency records",
        "api_idempotency",
        "transient",
        "24 hours (web/services/idempotency.py prunes them)",
    ),
    DataClass(
        "Revoked sessions",
        "revoked_sessions",
        "transient",
        "until the session would have expired (web/session_policy.py prunes them)",
    ),
)

#: (table, timestamp column, extra condition) swept by ZF_RETENTION_OPERATIONAL_DAYS
OPERATIONAL_RULES: tuple[tuple[str, str, str], ...] = (
    ("message_log", "ts", ""),
    ("ai_calls", "ts", ""),
    (
        "notifications",
        "created_at",
        " AND (resolved_at IS NOT NULL OR (read_at IS NOT NULL AND persistent=0))",
    ),
    ("jobs", "updated_at", " AND status IN ('done','dead')"),
)


def expired_patients() -> list[int]:
    """Patients whose clinical retention period is over (none while the period is 0)."""
    from bot.db import get_db
    from zenflow.patient_erasure import retention_allows_purge
    from zenflow.settings import get_settings

    if get_settings().flags.retention_clinical_years <= 0:
        return []
    conn = get_db()
    ids = [row[0] for row in conn.execute("SELECT id FROM patients ORDER BY id").fetchall()]
    return [pid for pid in ids if retention_allows_purge(conn, pid)]


def sweep(*, apply: bool = False, keep: Collection[int] = ()) -> dict[str, Any]:
    """Apply (or, by default, preview) the policy. Returns what was — or would be — removed.

    `keep` = patient ids the operator holds back from the clinical purge after reading the preview
    (e.g. a minor, whose record must outlive their 25th birthday — docs/DATA_PROTECTION.md §1).
    """
    from bot.db import get_db
    from zenflow.patient_erasure import erase_patient
    from zenflow.settings import get_settings

    flags = get_settings().flags
    conn = get_db()
    result: dict[str, Any] = {"applied": apply, "operational": {}, "clinical_purged": []}

    days = flags.retention_operational_days
    if days > 0:
        cutoff = clock.hours_ago(days * 24)
        result["operational_cutoff"] = cutoff
        for table, column, extra in OPERATIONAL_RULES:
            where = f"{column} < ?{extra}"
            n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", (cutoff,)).fetchone()[0]
            if apply and n:
                conn.execute(f"DELETE FROM {table} WHERE {where}", (cutoff,))
            result["operational"][table] = n

    held = {int(pid) for pid in keep}
    expired = [pid for pid in expired_patients() if pid not in held]
    result["clinical_kept"] = sorted(held)
    for pid in expired:
        if apply:
            erase_patient(
                pid,
                mode="purge",
                reason=f"retention: clinical period of {flags.retention_clinical_years}y over",
                apply=True,
            )
        result["clinical_purged"].append(pid)
    if apply:
        logger.info(f"retention sweep: {result}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply the data-retention policy (Phase 9.9).")
    parser.add_argument("--apply", action="store_true", help="remove rows (default: preview)")
    parser.add_argument("--policy", action="store_true", help="print the policy table and exit")
    parser.add_argument(
        "--keep",
        default="",
        help="comma-separated patient ids to hold back from the clinical purge (e.g. minors)",
    )
    args = parser.parse_args(argv)
    if args.policy:
        for c in POLICY:
            print(f"{c.name:40} {c.kind:12} {c.tables:55} {c.kept}")
        return 0
    try:
        keep = [int(x) for x in args.keep.split(",") if x.strip()]
    except ValueError:
        parser.error("--keep takes patient ids, e.g. --keep 12,34")
    print(json.dumps(sweep(apply=args.apply, keep=keep), indent=2))
    if not args.apply:
        print("preview only — nothing changed; re-run with --apply", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
