"""Phase 12.2.1 — the container images and the compose parity stack.

These read the files; CI's `container` job additionally builds the web image, runs it and checks
/healthz answers (that is the proof the image works, these keep its properties from regressing).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
IGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()


def _stages() -> dict[str, str]:
    """`FROM … AS name` → that stage's text."""
    parts = re.split(r"(?m)^FROM ", DOCKERFILE)[1:]
    out = {}
    for part in parts:
        name = re.match(r"\S+\s+AS\s+(\w+)", part)
        assert name, f"every stage is named: {part[:60]!r}"
        out[name.group(1)] = part
    return out


def test_one_target_per_service_built_in_stages() -> None:
    stages = _stages()
    assert {"builder", "runtime", "web", "bots", "worker"} <= set(stages)
    assert "pip install" in stages["builder"]
    assert "pip install" not in stages["runtime"], "no build tooling in the runtime image"


def test_services_never_run_as_root() -> None:
    runtime = _stages()["runtime"]
    assert re.search(r"(?m)^USER zenflow\s*$", runtime)
    for name in ("web", "bots", "worker"):
        assert "USER root" not in _stages()[name], name


def test_the_web_image_checks_its_own_health() -> None:
    web = _stages()["web"]
    assert "HEALTHCHECK" in web and "/healthz" in web


def test_no_secret_is_baked_into_an_image() -> None:
    from zenflow.secrets import SECRET_NAMES

    for name in (*SECRET_NAMES, "BACKUP_ENCRYPTION_KEY"):
        assert not re.search(rf"(?m)^\s*(ENV|ARG)\s+.*\b{name}\b", DOCKERFILE), name


def test_containers_log_to_the_console_not_files() -> None:
    assert "ZF_LOG_FILES=0" in _stages()["runtime"]


@pytest.mark.parametrize("entry", [".env", "data/", "logs/", ".git/", "*.db", "tests/"])
def test_the_build_context_excludes_secrets_and_data(entry: str) -> None:
    assert entry in IGNORE


def test_the_compose_stack_has_the_parity_services() -> None:
    services = COMPOSE["services"]
    assert {"web", "bots", "redis", "postgres", "minio"} <= set(services)
    assert services["web"]["build"]["target"] == "web"
    assert services["bots"]["build"]["target"] == "bots"
    assert "ai" in services["ollama"].get("profiles", []), "Ollama is opt-in (it is large)"


def test_compose_never_carries_a_real_secret() -> None:
    from zenflow.secrets import SECRET_NAMES

    for name, service in COMPOSE["services"].items():
        env = service.get("environment") or {}
        for key in env:
            assert key not in SECRET_NAMES, f"{name} sets {key} — secrets come from .env only"


def test_the_image_ships_everything_the_app_reads_at_start() -> None:
    """init_db() stamps/upgrades through Alembic (12.2.3) — its config and revisions must ship."""
    runtime = _stages()["runtime"]
    for path in ("bot", "web", "zenflow", "startup", "locales", "alembic.ini", "migrations"):
        assert re.search(rf"(?m)^COPY .*{re.escape(path)}", runtime), path
