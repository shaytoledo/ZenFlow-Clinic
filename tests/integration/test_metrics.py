"""Plan 8.4 — health and metrics: is the system up, is it ready, and how is it doing.

Three endpoints with three audiences. `/healthz` is for a load balancer and says nothing but that
the process is alive. `/readyz` is for a deployment: it fails when the app cannot actually serve
traffic, and tells an anonymous caller nothing about why. `/api/admin/metrics` is for the operator:
jobs, follow-ups, AI cost and latency, messages, relay, and what the system can reach.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

import bot.db as dbmod
from zenflow import clock

pytestmark = pytest.mark.integration


def _job(status: str, name: str = "followup.send", run_at: str | None = None) -> None:
    """One row in the queue, the way the worker leaves it (a finished job is stamped)."""
    now = clock.iso_now()
    dbmod.get_db().execute(
        """INSERT INTO jobs (name, payload_json, run_at, status, created_at, updated_at,
                             completed_at)
           VALUES (?, '{}', ?, ?, ?, ?, ?)""",
        (name, run_at or now, status, now, now, now if status == "done" else None),
    )


# ── /healthz: alive ──
async def test_healthz_says_nothing_but_alive(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200 and resp.json() == {"ok": True}


# ── /readyz: able to serve ──
async def test_readyz_is_public_and_tells_a_stranger_nothing(client: httpx.AsyncClient) -> None:
    resp = await client.get("/readyz")
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    body = resp.text.lower()
    for leak in ("database", "redis", "sqlite", "ollama", "telegram"):
        assert leak not in body, "a readiness probe is not a topology map (F11)"


async def test_readyz_tells_the_therapist_what_it_checked(
    authenticated_client: httpx.AsyncClient,
) -> None:
    resp = await authenticated_client.get("/readyz")
    assert resp.status_code == 200
    checks = {c["name"]: c for c in resp.json()["checks"]}
    assert checks["database"]["ok"] is True and checks["database"]["required"] is True
    assert "redis" in checks and checks["redis"]["required"] is False


async def test_readyz_fails_when_the_database_is_gone(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deployment that cannot reach its database must not be sent traffic."""
    from web.services import health

    def _boom() -> bool:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(health, "_database_ok", _boom)
    resp = await client.get("/readyz")
    assert resp.status_code == 503 and resp.json() == {"ok": False}
    assert "locked" not in resp.text


async def test_readyz_survives_a_dependency_that_is_merely_optional(
    authenticated_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web.services import health

    async def _no_redis() -> tuple[bool, str]:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(health, "_redis_ok", _no_redis)
    resp = await authenticated_client.get("/readyz")
    assert resp.status_code == 200, "the dashboard still serves without Redis"
    checks = {c["name"]: c for c in resp.json()["checks"]}
    assert checks["redis"]["ok"] is False and resp.json()["ok"] is True


# ── /api/admin/metrics: how it is doing ──
async def test_metrics_needs_a_session(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/admin/metrics")).status_code == 401


async def test_metrics_counts_the_work_waiting_and_lost(
    authenticated_client: httpx.AsyncClient,
) -> None:
    for status in ("pending", "pending", "running", "dead", "done"):
        _job(status)

    body = (await authenticated_client.get("/api/admin/metrics")).json()
    jobs = body["jobs"]
    assert (jobs["pending"], jobs["running"], jobs["dead"]) == (2, 1, 1)
    assert jobs["done"] == 1


async def test_metrics_counts_the_followups_a_clinic_cares_about(
    authenticated_client: httpx.AsyncClient, make_completed_session, make_patient
) -> None:
    from web.repositories import followup_repo

    first = make_completed_session(patient=make_patient("Dana", telegram_id=960_000_001))
    second = make_completed_session(patient=make_patient("Noa", telegram_id=960_000_002))
    followup_repo.schedule(first["id"], clock.hours_ago(1))
    followup_repo.schedule(second["id"], clock.hours_ago(1))
    followup_repo.mark_sent(second["id"], [])

    followups = (await authenticated_client.get("/api/admin/metrics")).json()["followups"]
    assert followups["scheduled"] == 1 and followups["sent"] == 1
    assert followups["due"] == 1, "scheduled, in the past, and not sent yet"
    assert followups["completed"] == 0 and followups["needs_attention"] == 0


async def test_metrics_reports_ai_latency_and_failure_rate(
    authenticated_client: httpx.AsyncClient,
) -> None:
    from web.services import ai_calls

    for ms in (10, 20, 30, 40):
        ai_calls.record("pipeline.points", provider="ollama", model="gemma3", duration_ms=ms)
    ai_calls.record("pipeline.points", duration_ms=900, status="timeout", error="timed out")

    ai = (await authenticated_client.get("/api/admin/metrics")).json()["ai"]
    assert ai["calls"] == 5 and ai["failures"] == 1
    assert ai["failure_rate"] == pytest.approx(0.2)
    assert ai["p50_ms"] == 30 and ai["p95_ms"] == 900
    assert ai["stages"]["pipeline.points"]["calls"] == 5


async def test_metrics_reports_what_reached_patients(
    authenticated_client: httpx.AsyncClient, make_patient
) -> None:
    from web.repositories import message_log_repo

    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    patient = make_patient("Dana", telegram_id=960_000_003)
    common: dict[str, Any] = {
        "therapist_id": tid,
        "patient_id": patient["patient_id"],
        "appointment_id": None,
        "channel": "telegram",
    }
    message_log_repo.record(kind="followup", status="sent", **common)
    message_log_repo.record(kind="recommendations", status="failed", error="boom", **common)
    message_log_repo.record(kind="relay", status="sent", direction="in", **common)

    messages = (await authenticated_client.get("/api/admin/metrics")).json()["messages"]
    assert (messages["sent"], messages["failed"], messages["received"]) == (1, 1, 1)
    assert messages["by_channel"]["telegram"]["failed"] == 1


async def test_metrics_says_what_it_can_reach_without_failing_on_what_it_cannot(
    authenticated_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreachable Ollama is a metric, not a 500."""
    from web.services import health

    async def _down() -> tuple[bool, str]:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(health, "_ollama_ok", _down)
    resp = await authenticated_client.get("/api/admin/metrics")
    assert resp.status_code == 200
    dependencies = {c["name"]: c["ok"] for c in resp.json()["dependencies"]}
    assert dependencies["ollama"] is False and dependencies["database"] is True


async def test_metrics_never_leaks_a_secret(authenticated_client: httpx.AsyncClient) -> None:
    text = (await authenticated_client.get("/api/admin/metrics")).text
    assert "TEST-PATIENT-BOT-TOKEN" not in text
    for forbidden in ("session_secret", "token_encryption_key", "client_secret"):
        assert forbidden not in text.lower()


# ── Prometheus, behind a flag ──
async def test_prometheus_is_absent_until_it_is_turned_on(
    authenticated_client: httpx.AsyncClient,
) -> None:
    resp = await authenticated_client.get("/api/admin/metrics?format=prometheus")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


async def test_prometheus_exposition_is_scrapable(
    authenticated_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web.services import ai_calls
    from zenflow.settings import reset_settings

    monkeypatch.setenv("ZF_METRICS_PROMETHEUS", "1")
    reset_settings()
    ai_calls.record("intake.question", provider="ollama", model="gemma3", duration_ms=42)
    _job("pending")

    resp = await authenticated_client.get("/api/admin/metrics?format=prometheus")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")

    lines = [line for line in resp.text.splitlines() if line and not line.startswith("#")]
    assert any(line.startswith('zenflow_jobs{status="pending"} 1') for line in lines)
    assert any(line.startswith("zenflow_ai_calls_total") for line in lines)
    assert "# HELP zenflow_jobs" in resp.text and "# TYPE zenflow_jobs gauge" in resp.text
    for line in lines:
        float(line.rsplit(" ", 1)[1]), "every sample ends in a number"


# ── OpenTelemetry, behind a flag and without the library ──
def test_tracing_is_off_by_default() -> None:
    from zenflow import tracing

    assert tracing.status() == "off" and tracing.enabled() is False


def test_tracing_asked_for_without_the_library_says_so_and_starts_anyway(
    monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """The dependency is not installed yet: asking for tracing must not stop the app."""
    from zenflow import tracing
    from zenflow.settings import reset_settings

    monkeypatch.setenv("ZF_TRACING", "1")
    reset_settings()
    tracing.reset()
    with caplog.at_level("WARNING"):
        tracing.setup(None)

    assert tracing.status() == "unavailable" and tracing.enabled() is False
    assert any("opentelemetry" in record.message.lower() for record in caplog.records)
