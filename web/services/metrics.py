"""
web/services/metrics.py
────────────────────────
One snapshot of how the clinic's software is doing (Phase 8.4), served at
`GET /api/admin/metrics` and — behind `ZF_METRICS_PROMETHEUS=1` — in Prometheus' text format.

    {"jobs": …, "followups": …, "ai": …, "messages": …, "relay": …, "dependencies": […]}

It answers the questions an operator actually asks: is work piling up or being lost, are patients
being checked on, what is the AI costing and how often does it fail, did messages reach anyone,
and what can the process reach right now.

Every part is defensive: a dependency that is down becomes a `false` in the snapshot, never a 500
from the endpoint that exists to tell you things are down. Numbers come from the tables that
already record them — `jobs` (1.2), `followups` (6.x), `ai_calls` (8.2), `message_log` (8.3) — so
nothing here is a second source of truth.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from zenflow import clock

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_HOURS = 24.0
JOB_STATUSES = ("pending", "running", "done", "dead", "cancelled")
FOLLOWUP_STATUSES = ("scheduled", "sent", "in_progress", "completed", "expired", "no_channel")


def _conn() -> sqlite3.Connection:
    from bot.db import get_db

    return get_db()


async def snapshot(
    *, hours: float = DEFAULT_WINDOW_HOURS, therapist_id: str | None = None
) -> dict[str, Any]:
    """The whole picture. Never raises — a broken part is reported, not propagated."""
    from web.services import health

    return {
        "generated_at": clock.iso_now(),
        "window_hours": float(hours),
        "jobs": _safe(lambda: jobs(hours), {}),
        "followups": _safe(followups, {}),
        "ai": _safe(lambda: ai(hours), {}),
        "messages": _safe(lambda: messages(hours), {}),
        "relay": await _relay(),
        "dependencies": [check.as_dict() for check in await health.dependencies(therapist_id)],
    }


def jobs(hours: float = DEFAULT_WINDOW_HOURS) -> dict[str, Any]:
    """Queue depth by state, plus how long the oldest waiting job has waited (1.2, ADR-20)."""
    counts = {status: 0 for status in JOB_STATUSES}
    for row in _conn().execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status"):
        counts[str(row["status"])] = int(row["n"])
    done = (
        _conn()
        .execute(
            "SELECT COUNT(*) n FROM jobs WHERE status='done' AND completed_at >= ?",
            (clock.hours_ago(hours),),
        )
        .fetchone()
    )
    oldest = (
        _conn()
        .execute(
            "SELECT MIN(run_at) t FROM jobs WHERE status='pending' AND run_at <= ?",
            (clock.iso_now(),),
        )
        .fetchone()
    )
    return {
        **counts,
        "done": int(done["n"]) if done else 0,
        "done_total": counts["done"],
        "oldest_due_seconds": _age_seconds(oldest["t"] if oldest else None),
    }


def followups(now_iso: str | None = None) -> dict[str, Any]:
    """Where every check-in stands, and how many are overdue to go out (6.1)."""
    now = now_iso or clock.iso_now()
    counts = {status: 0 for status in FOLLOWUP_STATUSES}
    for row in _conn().execute("SELECT status, COUNT(*) n FROM followups GROUP BY status"):
        counts[str(row["status"])] = int(row["n"])
    due = (
        _conn()
        .execute(
            "SELECT COUNT(*) n FROM followups WHERE status='scheduled' AND scheduled_for <= ?",
            (now,),
        )
        .fetchone()
    )
    attention = (
        _conn().execute("SELECT COUNT(*) n FROM followups WHERE needs_attention=1").fetchone()
    )
    return {
        **counts,
        "due": int(due["n"]) if due else 0,
        "needs_attention": int(attention["n"]) if attention else 0,
    }


def ai(hours: float = DEFAULT_WINDOW_HOURS) -> dict[str, Any]:
    """Cost, latency and failure rate of the model calls (8.2)."""
    from web.services import ai_calls

    return ai_calls.summary(hours=hours)


def messages(hours: float = DEFAULT_WINDOW_HOURS) -> dict[str, Any]:
    """What reached patients, what did not, and what came back (8.3)."""
    rows = (
        _conn()
        .execute(
            """SELECT direction, channel, status, COUNT(*) n FROM message_log
           WHERE ts >= ? GROUP BY direction, channel, status""",
            (clock.hours_ago(hours),),
        )
        .fetchall()
    )

    out: dict[str, Any] = {"sent": 0, "failed": 0, "received": 0, "by_channel": {}}
    for row in rows:
        channel = str(row["channel"])
        bucket = out["by_channel"].setdefault(channel, {"sent": 0, "failed": 0, "received": 0})
        if str(row["direction"]) == "in":
            key = "received"
        else:
            key = "failed" if str(row["status"]) == "failed" else "sent"
        out[key] += int(row["n"])
        bucket[key] += int(row["n"])
    return out


async def _relay() -> dict[str, Any]:
    """How many patients are in a live chat right now (Redis, so it may simply be unknown)."""
    try:
        from bot.redis_client import get_async_redis

        keys = await get_async_redis().keys("zenflow:relay:active:*")
        return {"active": len(keys), "known": True}
    except Exception as exc:
        logger.warning("relay metric unavailable: %s", exc)
        return {"active": 0, "known": False}


def _age_seconds(iso: str | None) -> int:
    if not iso:
        return 0
    try:
        delta = clock.now_utc() - clock.parse_iso(str(iso))
    except Exception:
        return 0
    return max(int(delta.total_seconds()), 0)


def _safe(probe: Any, fallback: Any) -> Any:
    try:
        return probe()
    except Exception as exc:
        logger.warning("metric unavailable: %s", exc)
        return fallback


# ── Prometheus text exposition (ZF_METRICS_PROMETHEUS=1) ──
#: (metric, help) for the gauges a scraper sees; labels are added per family below
_FAMILIES = (
    ("zenflow_jobs", "Queued jobs by state"),
    ("zenflow_jobs_oldest_due_seconds", "Age of the oldest job that is due and still pending"),
    ("zenflow_followups", "Check-ins by state"),
    ("zenflow_followups_due", "Check-ins scheduled in the past and not sent yet"),
    ("zenflow_followups_needs_attention", "Check-ins flagged for the therapist"),
    ("zenflow_ai_calls_total", "Model calls in the window"),
    ("zenflow_ai_failures_total", "Model calls that failed or timed out in the window"),
    ("zenflow_ai_duration_ms", "Model call latency in the window, by quantile"),
    ("zenflow_ai_tokens_total", "Tokens reported by the models in the window"),
    ("zenflow_messages_total", "Messages to and from patients in the window"),
    ("zenflow_relay_active", "Patients in a live chat"),
    ("zenflow_dependency_up", "Whether a dependency answered its probe"),
)


def prometheus_text(data: dict[str, Any]) -> str:
    """The snapshot as Prometheus' text exposition format — no client library needed."""
    samples: dict[str, list[tuple[dict[str, str], float]]] = {name: [] for name, _ in _FAMILIES}

    for status in JOB_STATUSES:
        samples["zenflow_jobs"].append(({"status": status}, data["jobs"].get(status, 0)))
    samples["zenflow_jobs_oldest_due_seconds"].append(
        ({}, data["jobs"].get("oldest_due_seconds", 0))
    )

    for status in FOLLOWUP_STATUSES:
        samples["zenflow_followups"].append(({"status": status}, data["followups"].get(status, 0)))
    samples["zenflow_followups_due"].append(({}, data["followups"].get("due", 0)))
    samples["zenflow_followups_needs_attention"].append(
        ({}, data["followups"].get("needs_attention", 0))
    )

    ai_data = data.get("ai") or {}
    samples["zenflow_ai_calls_total"].append(({}, ai_data.get("calls", 0)))
    samples["zenflow_ai_failures_total"].append(({}, ai_data.get("failures", 0)))
    for quantile in ("50", "95"):
        samples["zenflow_ai_duration_ms"].append(
            ({"quantile": f"0.{quantile}"}, ai_data.get(f"p{quantile}_ms", 0))
        )
    for kind in ("prompt", "completion"):
        samples["zenflow_ai_tokens_total"].append(
            ({"kind": kind}, ai_data.get(f"{kind}_tokens", 0))
        )

    for channel, counts in (data.get("messages", {}).get("by_channel") or {}).items():
        for key, value in counts.items():
            samples["zenflow_messages_total"].append(({"channel": channel, "kind": key}, value))

    samples["zenflow_relay_active"].append(({}, (data.get("relay") or {}).get("active", 0)))
    for check in data.get("dependencies", []):
        samples["zenflow_dependency_up"].append(
            ({"name": str(check.get("name", ""))}, 1 if check.get("ok") else 0)
        )

    lines: list[str] = []
    for name, help_text in _FAMILIES:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        for labels, value in samples[name]:
            lines.append(f"{name}{_labels(labels)} {_number(value)}")
    return "\n".join(lines) + "\n"


def _labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{key}="{_escape(value)}"' for key, value in labels.items())
    return "{" + inner + "}"


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0"
    return str(int(number)) if number.is_integer() else repr(number)
