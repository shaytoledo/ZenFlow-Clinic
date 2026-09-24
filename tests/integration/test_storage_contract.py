"""Phase 4.3c — one contract for every media store, run against LocalStorage and S3Storage.

S3 runs against moto's in-memory AWS (no network, no real account). The S3-only tests check what
a local disk has no equivalent for: SSE-KMS on every object, the key prefix, and presigned links.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from zenflow.storage import LocalStorage, S3Storage, Storage, StorageError, s3_client

pytestmark = pytest.mark.integration

moto = pytest.importorskip("moto")
BUCKET = "zenflow-media-test"
REGION = "eu-central-1"
PW = "pw-Test-123"


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """moto's fake AWS with fake credentials and one bucket."""
    for name, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": REGION,
    }.items():
        monkeypatch.setenv(name, value)
    s3_client.cache_clear()  # a client built with other credentials must not leak in
    with moto.mock_aws():
        client = s3_client(region=REGION)
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION}
        )
        yield client


@pytest.fixture(params=["local", "s3"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Storage]:
    if request.param == "local":
        yield LocalStorage(tmp_path / "media")
    else:
        client = request.getfixturevalue("aws")
        yield S3Storage(client, BUCKET, prefix="media/")


KEY = "acupoints/li4/0123abcd.webp"


# ── the contract ──
def test_put_get_exists_delete(store: Storage) -> None:
    assert not store.exists(KEY)
    store.put(KEY, b"first", "image/webp")
    assert store.exists(KEY) and store.get(KEY) == b"first"
    store.put(KEY, b"second", "image/webp")
    assert store.get(KEY) == b"second", "a put replaces the object"
    store.delete(KEY)
    assert not store.exists(KEY)
    store.delete(KEY)  # deleting what is gone is not an error


@pytest.mark.parametrize("key", ["../x.png", "/abs/x.png", "Acupoints/x.png", "a.png", "a/b.exe"])
def test_unsafe_keys_never_reach_the_store(store: Storage, key: str) -> None:
    for call in (
        lambda: store.put(key, b"x", "image/png"),
        lambda: store.get(key),
        lambda: store.exists(key),
        lambda: store.delete(key),
        lambda: store.url(key),
    ):
        with pytest.raises(StorageError):
            call()


def test_the_content_type_must_match_the_key(store: Storage) -> None:
    with pytest.raises(StorageError, match="image/webp"):
        store.put(KEY, b"x", "image/png")
    assert not store.exists(KEY)


def test_links_point_at_the_object(store: Storage) -> None:
    store.put(KEY, b"x", "image/webp")
    link = store.url(KEY, expires=600)
    assert link.endswith(KEY) or KEY in link


# ── S3 only ──
def test_objects_are_encrypted_typed_and_prefixed(aws: Any) -> None:
    kms = __import__("boto3").client("kms", region_name=REGION)
    key_id = kms.create_key(Description="zenflow media")["KeyMetadata"]["KeyId"]
    store = S3Storage(aws, BUCKET, prefix="/media/", kms_key_id=key_id)
    store.put(KEY, b"x", "image/webp")

    head = aws.head_object(Bucket=BUCKET, Key=f"media/{KEY}")
    assert head["ServerSideEncryption"] == "aws:kms"
    assert key_id in head["SSEKMSKeyId"]
    assert head["ContentType"] == "image/webp"
    assert head["CacheControl"] == "private, max-age=86400"
    listed = [o["Key"] for o in aws.list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert listed == [f"media/{KEY}"]


def test_without_a_key_id_objects_still_use_kms(aws: Any) -> None:
    S3Storage(aws, BUCKET).put(KEY, b"x", "image/webp")
    assert aws.head_object(Bucket=BUCKET, Key=KEY)["ServerSideEncryption"] == "aws:kms"


def test_links_are_signed_expiring_https_urls(aws: Any) -> None:
    import requests

    store = S3Storage(aws, BUCKET, prefix="media")
    store.put(KEY, b"image-bytes", "image/webp")
    link = store.url(KEY, expires=600)
    assert link.startswith("https://") and f"media/{KEY}" in link
    assert "X-Amz-Signature=" in link and "X-Amz-Expires=600" in link
    assert requests.get(link, timeout=10).content == b"image-bytes"


# ── configuration ──
def test_the_flag_selects_s3_and_requires_a_bucket(aws: Any, monkeypatch) -> None:
    from zenflow.settings import SettingsError, reset_settings
    from zenflow.storage import get_storage

    monkeypatch.setenv("ZF_STORAGE_S3", "1")
    monkeypatch.delenv("S3_BUCKET", raising=False)
    reset_settings()
    with pytest.raises(SettingsError, match="S3_BUCKET"):
        get_storage()

    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("S3_PREFIX", "clinic-a/")
    monkeypatch.setenv("S3_REGION", REGION)
    reset_settings()
    store = get_storage()
    assert isinstance(store, S3Storage)
    assert (store.bucket, store.prefix) == (BUCKET, "clinic-a/")
    store.put(KEY, b"x", "image/webp")
    assert aws.head_object(Bucket=BUCKET, Key=f"clinic-a/{KEY}")

    monkeypatch.setenv("ZF_STORAGE_S3", "0")
    reset_settings()
    assert isinstance(get_storage(), LocalStorage)


@pytest.mark.parametrize(
    ("endpoint", "ok"),
    [
        ("https://minio.example.com", True),
        ("http://minio.example.com", False),
        ("http://localhost:9000", True),
    ],
)
def test_a_custom_endpoint_must_use_https_outside_dev(
    endpoint: str, ok: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zenflow.settings import Settings

    env = {
        "ENV": "prod",
        "SESSION_SECRET": "s" * 40,
        "TOKEN_ENCRYPTION_KEY": "t" * 40,
        "OLLAMA_HOST": "https://ollama.example.com",
        "REDIS_URL": "rediss://:redis-secret@redis.example.com:6380/0",
        "GOOGLE_REDIRECT_URI": "https://app.example.com/auth/callback",
        "GOOGLE_REG_REDIRECT_URI": "https://app.example.com/register/google/callback",
        "GOOGLE_GMAIL_REDIRECT_URI": "https://app.example.com/auth/gmail/callback",
        "S3_ENDPOINT_URL": endpoint,
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    if ok:
        Settings(_env_file=None)  # type: ignore[call-arg]
    else:
        with pytest.raises(ValueError, match="S3_ENDPOINT_URL"):
            Settings(_env_file=None)  # type: ignore[call-arg]


# ── end to end: ingest into S3, link from the API ──
async def test_ingested_images_are_linked_from_s3(
    aws: Any, tmp_path: Path, make_therapist, login_as, fake_redis, monkeypatch
) -> None:
    import io

    from PIL import Image

    from bot.db import get_db
    from zenflow import ingest_images
    from zenflow.settings import reset_settings

    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "credits.json").write_text(
        json.dumps({"default": {"credit": "Test", "licence": "CC0 1.0"}}), encoding="utf-8"
    )
    out = io.BytesIO()
    Image.new("RGB", (64, 64), (1, 2, 3)).save(out, "PNG")
    (folder / "LI4.png").write_bytes(out.getvalue())

    for name, value in {
        "ZF_STORAGE_S3": "1",
        "ZF_POINT_IMAGES": "1",
        "S3_BUCKET": BUCKET,
        "S3_REGION": REGION,
    }.items():
        monkeypatch.setenv(name, value)
    reset_settings()
    from zenflow.storage import get_storage

    report = ingest_images.ingest_folder(folder, get_db(), get_storage())
    assert report.added == 1
    keys = [o["Key"] for o in aws.list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert len(keys) == 2 and all(k.startswith("media/acupoints/li4/") for k in keys)

    client = await login_as(make_therapist(email="s3-api@example.com", password=PW))
    [image] = (await client.get("/api/acupoints")).json()["points"]["LI4"]["images"]
    assert image["url"].startswith("https://") and "X-Amz-Signature=" in image["url"]
    assert "-thumb.webp" in image["thumb_url"]
    media_key = keys[0].removeprefix("media/")
    assert (await client.get(f"/media/{media_key}")).status_code == 404, "S3 links, not /media"


def test_the_client_is_built_once_per_region(aws: Any) -> None:
    assert s3_client(region=REGION) is aws
    assert s3_client(region="us-east-1") is not aws
