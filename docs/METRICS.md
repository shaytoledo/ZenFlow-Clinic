# Health and metrics (Phase 8.4)

Three endpoints, three audiences.

| Endpoint | Who asks | Answers |
|---|---|---|
| `GET /healthz` | a load balancer | is the process alive — `{"ok": true}`, nothing else |
| `GET /readyz` | a deployment | may this instance be sent traffic |
| `GET /api/admin/metrics` | the operator | how is the system doing |

Code: `web/services/health.py` (what we can reach), `web/services/metrics.py` (how we are doing),
`zenflow/tracing.py` (the tracing flag). Decision record: ADR-34.

---

## 1. `/healthz` — alive

Public, unchanged since F11: `{"ok": true}` and no names. It proves the process answers HTTP; it
deliberately says nothing that would help someone map the system.

## 2. `/readyz` — able to serve

200 while the app can serve traffic, **503** when it cannot. What counts:

| Check | Required | Why |
|---|---|---|
| `database` | yes | every page and endpoint reads it |
| `configuration` | yes | settings that no longer load mean the process is misconfigured |
| `redis` | no | sessions are signed cookies, caches fall back to SQLite — the dashboard works |

Redis, the AI and the bots being down are **problems**, not reasons to take the web app out of
rotation; a check-in that cannot be sent is a follow-up failure, not a reason to stop serving the
therapist their schedule. They appear in the metrics endpoint instead.

An anonymous caller gets `{"ok": true|false}` and nothing else — a readiness probe is not a
topology map. A signed-in therapist also gets `checks`, because an operator debugging a deployment
is exactly who needs the names:

```json
{"ok": true, "checks": [{"name": "database", "ok": true, "required": true, "detail": "ok"}, …]}
```

Reasons are short and pass through the log redactor, so a connection string with a password in it
cannot arrive in a probe response.

## 3. `/api/admin/metrics` — how it is doing

Session required (any active therapist), like `/api/admin/flags`. `?hours=` sets the window
(default 24, capped at 30 days).

```json
{
  "generated_at": "2026-09-18T09:00:00Z",
  "window_hours": 24.0,
  "jobs":      {"pending": 2, "running": 1, "dead": 0, "done": 37, "oldest_due_seconds": 12},
  "followups": {"scheduled": 4, "sent": 2, "completed": 9, "due": 1, "needs_attention": 0},
  "ai":        {"calls": 41, "failures": 1, "failure_rate": 0.0244, "p50_ms": 820, "p95_ms": 4100,
                "prompt_tokens": 12045, "completion_tokens": 3100, "stages": {…}},
  "messages":  {"sent": 12, "failed": 1, "received": 7, "by_channel": {"telegram": {…}}},
  "relay":     {"active": 1, "known": true},
  "dependencies": [{"name": "ollama", "ok": true, "required": false, "detail": "…"}, …]
}
```

Nothing here is a second source of truth: the numbers come from the tables that already record
them — `jobs` (1.2), `followups` (6.x), `ai_calls` (8.2), `message_log` (8.3) — plus Redis for the
live relay count.

**The endpoint that reports outages must not fail because of one.** Every section is computed
defensively: a section that cannot be read becomes `{}`, an unreachable dependency becomes
`"ok": false`, and Redis being down makes the relay count `{"active": 0, "known": false}` rather
than a 500.

Useful questions it answers directly: *is work piling up* (`jobs.pending`, `oldest_due_seconds`),
*is work being lost* (`jobs.dead`), *are patients being checked on* (`followups.due`),
*what is the AI costing and how often does it fail* (`ai`), *did messages reach anyone*
(`messages.by_channel`).

## 4. Prometheus (`ZF_METRICS_PROMETHEUS=1`)

With the flag on, `GET /api/admin/metrics?format=prometheus` returns the same snapshot in the text
exposition format, written by hand — no client library, so nothing new is installed:

```
# HELP zenflow_jobs Queued jobs by state
# TYPE zenflow_jobs gauge
zenflow_jobs{status="pending"} 2
zenflow_ai_duration_ms{quantile="0.95"} 4100
zenflow_messages_total{channel="telegram",kind="failed"} 1
zenflow_dependency_up{name="ollama"} 1
```

With the flag off the format simply does not exist (404), like every other flagged surface in this
codebase. The endpoint still requires a session, so a scraper needs credentials; wiring a scrape
job into a deployment is a Phase 12 question, not a reason to open the endpoint now.

## 5. Tracing (`ZF_TRACING=1`)

`zenflow/tracing.py` is the seam, written now so Phase 12 does not have to invent it under
pressure. The OpenTelemetry packages are **not installed** in this project:

| `status()` | Means |
|---|---|
| `off` | the flag is not set (the default) |
| `unavailable` | the flag is set and `opentelemetry-instrumentation-fastapi` is missing — one warning is logged, the app runs untraced |
| `on` | the flag is set, the library is there, the app is instrumented |

The application starts in all three cases. Observability that can take the clinic down is not
observability. Installing `opentelemetry-sdk` and `opentelemetry-instrumentation-fastapi` is the
owner's call (it is a new dependency), and until then the flag is honest about doing nothing.

## 6. What is deliberately not here

- **No time series.** These are current values; a scraper (or Phase 12's CloudWatch) keeps history.
- **No per-patient numbers.** Everything is an aggregate; the endpoint never returns a name.
- **No alerting.** Thresholds belong where the history is.
