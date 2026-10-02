"""Phase 12.2.6 — the Terraform describes an AWS deployment the app accepts, and a safe one.

CI also runs `terraform fmt -check` / `validate` and a misconfiguration scan on infra/terraform.
These read the HCL as text and check what neither of those can: that the task environment boots
the app's own settings validation in prod mode, that secrets are injected (never plain env), and
the security properties a health-data system must keep.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TF = ROOT / "infra" / "terraform"


def _hcl(name: str) -> str:
    return (TF / name).read_text(encoding="utf-8")


def _block(text: str, header: str) -> str:
    """The text between `header {` and its matching closing brace."""
    start = text.index(header)
    depth = 0
    for i in range(text.index("{", start), len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[start : i + 1]
    raise AssertionError(f"unclosed block {header!r}")


def _task_environment() -> dict[str, str]:
    """`app_environment` from app.tf, Terraform expressions replaced by what AWS would hand in."""
    known = {
        "local.app_url": "https://app.clinic.example",
        "local.ollama_domain": "ollama.app.clinic.example",
        "aws_db_instance.main.address": "zenflow-prod.abc123.il-central-1.rds.amazonaws.com",
        "aws_s3_bucket.media.bucket": "zenflow-prod-media-123456789012",
        "aws_kms_key.main.arn": "arn:aws:kms:il-central-1:123456789012:key/abc",
        "var.region": "il-central-1",
        "var.clinic_tz": "Asia/Jerusalem",
        "var.ollama_model": "gemma3:latest",
        "var.google_client_id": "1234.apps.googleusercontent.com",
        'local.is_prod ? "prod" : "staging"': "prod",
        'var.ollama_enabled ? "https://${local.ollama_domain}" : ""': (
            "https://ollama.app.clinic.example"
        ),
    }
    body = _block(_hcl("app.tf"), "app_environment = {")
    env = {}
    for line in body.splitlines()[1:-1]:
        line = line.split(" # ")[0].strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = (part.strip() for part in line.partition("="))
        if value.startswith('"') and value.endswith('"') and value.count('"') == 2:
            value = re.sub(r"\$\{([^}]+)\}", lambda m: known[m.group(1)], value[1:-1])
        else:
            assert value in known, f"{key}: teach this test what {value!r} becomes on AWS"
            value = known[value]
        env[key] = value
    return env


def _secret_keys() -> list[str]:
    block = _block(_hcl("variables.tf"), 'variable "app_secret_keys"')
    return re.findall(r'"([A-Z_]+)"', block.split("default", 1)[1])


def test_the_task_environment_boots_the_app_in_prod_mode(monkeypatch) -> None:
    """The switch to AWS is these variables: the app's own fail-fast validation must accept them."""
    from zenflow import settings as S

    env = _task_environment()
    assert env["ENV"] == "prod"
    secrets = {key: f"{key.lower()}-" + "x" * 40 for key in _secret_keys()}
    secrets["REDIS_URL"] = "rediss://:" + "t" * 48 + "@master.zenflow.cache.amazonaws.com:6379/0"
    secrets["ZF_DB_PASSWORD"] = "rds-managed-password"
    for key in S.ALL_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    for key, value in {**env, **secrets}.items():
        monkeypatch.setenv(key, value)
    S.reset_settings()
    try:
        settings = S.get_settings()  # raises SettingsError on anything prod refuses
        assert settings.flags.storage_s3 and settings.flags.webhook_mode
        assert settings.flags.log_files is False
    finally:
        S.reset_settings()

    import bot.db as dbmod

    assert dbmod.db_url().startswith("postgresql+psycopg://zenflow:rds-managed-password@")


def test_every_task_variable_is_one_the_app_reads() -> None:
    from zenflow.settings import ALL_ENV_VARS

    read_elsewhere = {"FORWARDED_ALLOW_IPS"}  # uvicorn's, in the image's CMD
    unknown = set(_task_environment()) - set(ALL_ENV_VARS) - read_elsewhere
    assert not unknown, f"typo or dead setting in the task environment: {unknown}"


def test_secrets_are_injected_never_plain_environment() -> None:
    from zenflow.secrets import SECRET_NAMES

    plain = set(_task_environment())
    assert not plain & set(SECRET_NAMES), "a secret in the plain task environment"
    assert set(_secret_keys()) <= set(SECRET_NAMES)
    app = _hcl("app.tf")
    for injected in ("REDIS_URL", "ZF_DB_PASSWORD"):
        assert f'name      = "{injected}"' in app
    assert "master_user_secret[0].secret_arn}:password::" in app
    assert "manage_master_user_password   = true" in _hcl("data_stores.tf")


def test_one_bots_replica_stopped_before_its_replacement_starts() -> None:
    """ADR-49: two bots processes would split a patient's conversation between two memories."""
    bots = _block(_hcl("app.tf"), 'resource "aws_ecs_service" "bots"')
    assert re.search(r"desired_count\s+= 1\n", bots)
    assert re.search(r"deployment_minimum_healthy_percent = 0\n", bots)
    assert re.search(r"deployment_maximum_percent\s+= 100\n", bots)


def test_patient_data_at_rest_is_encrypted_and_private() -> None:
    data = _hcl("data_stores.tf")
    rds = _block(data, 'resource "aws_db_instance" "main"')
    assert re.search(r"storage_encrypted\s+= true", rds)
    assert re.search(r"publicly_accessible\s+= false", rds)
    assert re.search(r"deletion_protection\s+= local.is_prod", rds)
    assert '"rds.force_ssl"' in data
    redis = _block(data, 'resource "aws_elasticache_replication_group" "main"')
    for setting in ("at_rest_encryption_enabled", "transit_encryption_enabled"):
        assert re.search(rf"{setting}\s+= true", redis), setting
    pab = _block(data, 'resource "aws_s3_bucket_public_access_block" "media"')
    assert pab.count("= true") == 4
    assert '"aws:SecureTransport"' in data
    assert "enable_key_rotation     = true" in data


def test_only_the_load_balancer_is_open_to_the_internet() -> None:
    sg = _hcl("security_groups.tf")
    open_ingress = re.findall(
        r'resource "aws_vpc_security_group_ingress_rule" "(\w+)" \{[^}]*?cidr_ipv4\s+= "0\.0\.0\.0/0"',
        sg,
    )
    assert sorted(open_ingress) == ["alb_http", "alb_https"]
    https = _hcl("edge.tf")
    assert 'ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"' in https
    assert 'protocol    = "HTTPS"' in https, "port 80 only redirects to HTTPS"


def test_the_ollama_install_is_pinned_and_verified() -> None:
    script = _hcl("ollama_user_data.sh.tftpl")
    assert "sha256sum --check" in script
    assert not re.search(r"curl[^\n|]*\|\s*(ba)?sh", script), "no remote script piped into a shell"
    assert re.search(
        r'variable "ollama_version"[^}]*default\s+= "\d+\.\d+\.\d+"', _hcl("variables.tf")
    )


@pytest.mark.parametrize("env_file", ["staging.tfvars", "prod.tfvars"])
def test_each_environment_names_its_owner_inputs(env_file: str) -> None:
    text = (TF / "envs" / env_file).read_text(encoding="utf-8")
    for required in ("environment", "domain_name", "route53_zone_id", "alert_email", "image_tag"):
        assert re.search(rf"^{required}\s+=", text, re.M), required


def test_nothing_leaves_the_vpc_except_https() -> None:
    """Egress: 443 to the internet (APIs without fixed addresses); data stores by security group."""
    sg = _hcl("security_groups.tf")
    assert not re.search(r'ip_protocol\s+= "-1"', sg), "no all-protocol rules"
    to_internet = re.findall(
        r'resource "aws_vpc_security_group_egress_rule" "(\w+)" \{[^}]*?from_port\s+= (\d+)'
        r'[^}]*?cidr_ipv4\s+= "0\.0\.0\.0/0"',
        sg,
    )
    assert to_internet and all(port == "443" for _, port in to_internet), to_internet


def test_alarms_can_actually_reach_the_owner() -> None:
    """CloudWatch cannot publish to an SNS topic under the AWS-managed key: it needs its own."""
    monitoring = _hcl("monitoring.tf")
    assert "alias/aws/sns" not in monitoring
    assert "kms_master_key_id = aws_kms_key.alerts.arn" in monitoring
    assert '"cloudwatch.amazonaws.com"' in monitoring
