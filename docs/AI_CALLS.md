# The AI meter (Phase 8.2)

What the AI cost, how long it took, and what it did when it failed — answerable from SQL, without
keeping a copy of what the patient said.

Table: `ai_calls`. Code: `web/services/ai_calls.py`. Decision record: ADR-32.

---

## 1. One row per model call

| Column | Holds |
|---|---|
| `ts` | when, as a canonical UTC instant |
| `appointment_id` | which appointment the call served (null for a call outside one) |
| `stage` | `intake.question`, `pipeline.diagnosis`, `followup.summary`, … (§3) |
| `provider` / `model` | `ollama` / `gemma3:latest`, `anthropic` / `claude-haiku-4-5-…` |
| `prompt_tokens` / `completion_tokens` | what the model said it spent, when it says so |
| `duration_ms` | wall time, including the wait |
| `status` | `ok`, `error`, `timeout` |
| `error` | the exception, redacted and capped |
| `prompt_sha256` / `response_sha256` | the text, as identity only |
| `prompt_debug` / `response_debug` | the text itself — dev only, behind a flag (§4) |

```sql
-- the slowest stages of the last day
SELECT stage, COUNT(*), AVG(duration_ms), MAX(duration_ms)
FROM ai_calls WHERE ts >= datetime('now','-1 day') GROUP BY stage ORDER BY 3 DESC;

-- every model call made for one appointment
SELECT ts, stage, model, duration_ms, status, error FROM ai_calls WHERE appointment_id=42;

-- is the same prompt failing repeatedly?
SELECT prompt_sha256, COUNT(*) FROM ai_calls WHERE status<>'ok' GROUP BY 1 HAVING COUNT(*) > 1;
```

## 2. One way to call a model

```python
from web.services import ai_calls

resp = await ai_calls.ask(_LLM_LONG, messages, stage="pipeline.summary",
                          timeout_seconds=OLLAMA_TIMEOUT)
```

`ask()` applies the timeout, returns what the model returned, and raises exactly what the model
raised — every caller keeps the fallback it already had. It writes the row on all three paths.

`tests/integration/test_ai_calls.py::test_every_model_call_goes_through_the_meter` walks `bot/`,
`web/` and `zenflow/` and fails if `.ainvoke(` appears anywhere else. A second call site would be
a call nobody can cost, time or explain, which is the thing this table exists to prevent.

**Recording never fails a call.** A failed insert is logged and swallowed: losing a metric must
not lose the patient's answer.

## 3. The stages

| Stage | The call |
|---|---|
| `intake.question` | the next adaptive question during a live intake |
| `intake.compress` | summarising old turns when a conversation grows long |
| `intake.summary` | the clinical summary at the end of a live intake |
| `intake.diagnosis` | the one-shot diagnosis (`generate_tcm_diagnosis`) |
| `pipeline.summary` | stage 0 of the queued pipeline, from the stored conversation |
| `pipeline.diagnosis` | stage 1 — pattern, principles, certainty, recommendations |
| `pipeline.points` | stage 2 — a batch of acupuncture points |
| `rediagnose` | the therapist's "re-diagnose" on the treatment page |
| `followup.summary` | the AI's wording of a 24 h check-in |

The pipeline's `_exclusive(appointment_id)` block sets both the appointment for the meter and the
`ai` actor for the audit trail (ADR-31), so a generation is attributed on both sides at once.

## 4. The prompt is a hash

A clinical prompt is patient data. `ai_calls` stores it as SHA-256 — enough to see that two calls
were the same call, that a diagnosis came from the summary on screen, or that a run of failures
shares one input — and not as text.

The exception is a developer's own machine: with `ZF_AI_DEBUG_PROMPTS=1` **and** `ENV=dev` (or a
test run), the prompt and the answer are also kept in `prompt_debug` / `response_debug`. On a
staging or production database the flag changes nothing. `ai_calls.forget_prompts(days)` drops the
debug copies again; the hashes and the metrics outlive them.

Errors go through the same redactor as the logs, so an API key in a provider's error message does
not land in the table.

## 5. Reading it

```python
ai_calls.summary(hours=24)   # calls, failures, failure_rate, p50_ms, p95_ms, tokens, per stage
ai_calls.history(apt_id)     # every call for one appointment, newest first
```

`summary()` is the shape `/api/admin/metrics` will serve in 8.4 (AI p50/p95 latency and LLM failure
rate come from here); `history()` is what 8.5 shows next to an appointment's audit trail.
Percentiles are nearest-rank over the window, computed in Python — at clinic volumes the window is
a few thousand rows.

## 6. Retention

Metric rows are small and are kept: they are the cost and reliability history of the system.
The debug copies are not — `forget_prompts()` exists for them, and nothing schedules it yet
because nothing writes them outside dev. A sweep belongs with the retention policy in plan 9.9
(owner question Q5).
