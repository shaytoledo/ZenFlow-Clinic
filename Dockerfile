# ZenFlow Clinic — one image definition, one target per service (Phase 12.2.1).
#
#   docker build --target web    -t zenflow-web .
#   docker build --target bots   -t zenflow-bots .
#   docker build --target worker -t zenflow-worker .
#   docker compose up            # the whole stack locally (docker-compose.yml)
#
# Multi-stage: dependencies are built into a virtualenv in `builder` and only the venv + source reach
# the runtime image (no compilers, no pip cache). Every service runs as an unprivileged user.
# Configuration comes from the environment at run time (zenflow/settings.py) — no secret is ever
# baked into an image; ENV=dev is the only mode that tolerates missing secrets.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# ── builder: resolve the locked dependencies into /opt/venv ────────────────────────────────────
FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip && pip install -r /tmp/requirements.txt

# ── runtime base: venv + source, non-root ─────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ZF_LOG_FILES=0 \
    ZENFLOW_DB_PATH=/data/zenflow.db \
    MEDIA_ROOT=/data/media
RUN groupadd --system --gid 10001 zenflow \
    && useradd --system --uid 10001 --gid zenflow --home-dir /app --shell /usr/sbin/nologin zenflow \
    && mkdir -p /data && chown zenflow:zenflow /data && chmod 0700 /data
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=zenflow:zenflow bot ./bot
COPY --chown=zenflow:zenflow web ./web
COPY --chown=zenflow:zenflow zenflow ./zenflow
COPY --chown=zenflow:zenflow startup ./startup
COPY --chown=zenflow:zenflow locales ./locales
# Alembic (12.2.3): init_db() stamps/upgrades the schema at start-up, so the revisions ship too
COPY --chown=zenflow:zenflow alembic.ini ./alembic.ini
COPY --chown=zenflow:zenflow migrations ./migrations
USER zenflow
VOLUME ["/data"]

# ── web: the dashboard + booking API ──────────────────────────────────────────────────────────
FROM runtime AS web
EXPOSE 8080
# The ALB (Phase 12) or a local proxy terminates TLS. uvicorn believes X-Forwarded-* only from
# FORWARDED_ALLOW_IPS; the app's own client-IP rule is ZF_TRUSTED_PROXIES (SF-022).
ENV FORWARDED_ALLOW_IPS=127.0.0.1
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"]
CMD ["sh", "-c", "exec uvicorn web.app:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips \"$FORWARDED_ALLOW_IPS\""]

# ── bots: the patient + therapist Telegram bots (and, with ZF_QUEUE_BACKEND=inprocess, the worker) ─
FROM runtime AS bots
CMD ["python", "startup/run_bots.py"]

# ── worker: the background-job worker on its own (any ZF_QUEUE_BACKEND) ──────────────────────
FROM runtime AS worker
CMD ["python", "-m", "zenflow.worker"]
