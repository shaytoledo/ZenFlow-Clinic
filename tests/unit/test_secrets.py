"""Phase 9.6 — zenflow.secrets: where secret values come from.

A `SecretsProvider` seam sits under settings: `EnvSecrets` (the default) reads the process
environment, and `AwsSecretsManagerSecrets` (Phase 12) reads a JSON bundle from AWS Secrets Manager.
The provider is the *lowest*-precedence settings source, so the environment always wins — the default
changes nothing, and a cloud provider only fills secrets the environment does not set.
"""

from __future__ import annotations

import sys
import types

import pytest

from zenflow import secrets as sec
from zenflow import settings as S


class _FakeProvider(sec.SecretsProvider):
    def __init__(self, bundle: dict[str, str]) -> None:
        self._bundle = bundle

    def get(self, name: str) -> str | None:
        return self._bundle.get(name) or None


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    sec.set_secrets_provider(None)


# ── EnvSecrets ──
def test_env_secrets_reads_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("SESSION_SECRET", "abc")
    assert sec.EnvSecrets().get("SESSION_SECRET") == "abc"
    monkeypatch.delenv("DEFINITELY_UNSET_XYZ", raising=False)
    assert sec.EnvSecrets().get("DEFINITELY_UNSET_XYZ") is None


# ── selection ──
def test_build_provider_defaults_to_env(monkeypatch) -> None:
    monkeypatch.delenv("ZF_CLOUD", raising=False)
    assert isinstance(sec.build_provider(), sec.EnvSecrets)


def test_build_provider_selects_aws_under_cloud(monkeypatch) -> None:
    monkeypatch.setenv("ZF_CLOUD", "1")
    monkeypatch.setenv("AWS_SECRETS_ID", "zenflow/prod")
    assert isinstance(sec.build_provider(), sec.AwsSecretsManagerSecrets)


def test_cloud_without_a_secret_id_stays_on_env(monkeypatch) -> None:
    monkeypatch.setenv("ZF_CLOUD", "1")
    monkeypatch.delenv("AWS_SECRETS_ID", raising=False)
    assert isinstance(sec.build_provider(), sec.EnvSecrets)


# ── AWS provider (offline, fake boto3) ──
def test_aws_provider_reads_a_bundle(monkeypatch) -> None:
    seen: dict[str, str] = {}

    class FakeClient:
        def get_secret_value(self, SecretId):  # noqa: N803 - boto3's parameter name
            seen["id"] = SecretId
            return {"SecretString": '{"SESSION_SECRET": "s3cr3t", "GOOGLE_CLIENT_SECRET": ""}'}

    fake_boto3 = types.SimpleNamespace(client=lambda *a, **k: FakeClient())
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    provider = sec.AwsSecretsManagerSecrets("zenflow/prod", region="il-central-1")
    assert provider.get("SESSION_SECRET") == "s3cr3t"
    assert provider.get("GOOGLE_CLIENT_SECRET") is None, "an empty value reads as unset"
    assert provider.get("MISSING") is None
    assert seen["id"] == "zenflow/prod"


# ── never log a secret ──
def test_repr_never_leaks_values(monkeypatch) -> None:
    monkeypatch.setenv("SESSION_SECRET", "topsecret-value")
    assert "topsecret-value" not in repr(sec.EnvSecrets())
    assert "topsecret-value" not in repr(sec.get_secrets_provider())


# ── the settings seam ──
def _clean_env(monkeypatch) -> None:
    for key in list(S.ALL_ENV_VARS):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ENV", "dev")


def test_provider_fills_a_secret_the_environment_lacks(monkeypatch) -> None:
    _clean_env(monkeypatch)
    sec.set_secrets_provider(
        _FakeProvider({"SESSION_SECRET": "from-provider-xyz", "GOOGLE_CLIENT_SECRET": "gcs"})
    )
    s = S.Settings()
    assert s.session_secret == "from-provider-xyz"
    assert s.google_client_secret == "gcs"


def test_the_environment_overrides_the_provider(monkeypatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("SESSION_SECRET", "from-env-wins")
    sec.set_secrets_provider(_FakeProvider({"SESSION_SECRET": "from-provider-loses"}))
    assert S.Settings().session_secret == "from-env-wins"
