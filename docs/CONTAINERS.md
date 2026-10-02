# Containers (Phase 12.2.1)

One `Dockerfile` with a target per service, and a `docker-compose.yml` that runs the whole stack on one
machine — dev/prod parity, so a flag that works in compose works on AWS.

```bash
docker build --target web    -t zenflow-web .     # dashboard + booking API, port 8080
docker build --target bots   -t zenflow-bots .    # both Telegram bots (+ the in-process worker)
docker build --target worker -t zenflow-worker .  # the job worker on its own
docker compose up --build                          # web, bots, redis, postgres, minio
docker compose --profile ai up                     # + Ollama (large)
```

## What the images guarantee (and what checks it)

| Property | How | Checked by |
|---|---|---|
| Small runtime, no build tools | multi-stage: dependencies built into `/opt/venv` in `builder`, only the venv + source copied | `tests/unit/test_containers.py` |
| Never runs as root | user `zenflow` (uid 10001), `/data` owned by it, mode 0700 | test + CI (`id -u` in every image) |
| Web is health-checked | `HEALTHCHECK` → `GET /healthz` | test + CI (container started, `/healthz` must answer) |
| No secret in an image | configuration only from the environment at run time (`zenflow/settings.py`) | test (no `ENV`/`ARG` names a secret) |
| No data/secrets in the build context | `.dockerignore`: `.env`, `data/`, `logs/`, `*.db`, `.git/`, `tests/`… | test |
| Logs to the console | `ZF_LOG_FILES=0` — the platform collects stdout/stderr; `/app` is not writable | test (both flag values) |
| Known CVEs surfaced | trivy on the web image (advisory, like pip-audit) | CI |

## Run-time settings that matter in a container

| Variable | Image default | Why |
|---|---|---|
| `ZENFLOW_DB_PATH` | `/data/zenflow.db` | the SQLite file lives on the `/data` volume (until `ZF_DB_URL`, 12.2.2) |
| `MEDIA_ROOT` | `/data/media` | local images, unless `ZF_STORAGE_S3=1` |
| `ZF_LOG_FILES` | `0` | console only |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | which peers uvicorn believes for `X-Forwarded-*` |
| `ZF_TRUSTED_PROXIES` | (empty) | which peers the app believes for the client IP (SF-022) — the ALB subnet on AWS |
| `ENV` | (none) | `dev` relaxes the secret checks; `staging`/`prod` require every secret (fail-fast) |

Secrets (`SESSION_SECRET`, `TOKEN_ENCRYPTION_KEY`, bot tokens, …) come from `.env` in compose, and
from the task definition / AWS Secrets Manager on AWS (`docs/SECRETS.md`) — never from the image.

## Not yet (later 12.2 tasks)

- Postgres is in compose for 12.2.2; the app still uses SQLite until `ZF_DB_URL` exists.
- Run **one** `bots` container, polling or webhook (ADR-49): conversation state lives in that process.
  `ZF_WEBHOOK_MODE=1` + `TELEGRAM_WEBHOOK_URL` + `TELEGRAM_WEBHOOK_SECRET` switch it to webhooks (12.2.5).
- The bots image answers `GET :8081/healthz` (both modes; Docker `HEALTHCHECK`). The worker image has no
  HTTP endpoint: its liveness is the jobs it completes (`/api/admin/metrics`).
