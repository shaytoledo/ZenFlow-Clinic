"""zenflow.storage — where uploaded media lives (Phase 4.3b; the AWS hook for Phase 12).

    from zenflow.storage import get_storage
    store = get_storage()
    store.put("acupoints/li4/ab12.webp", data, "image/webp")
    store.url("acupoints/li4/ab12.webp")      # what the page links to

Keys are relative, lower-case paths (``[a-z0-9._-]`` segments separated by ``/``); anything
else — ``..``, absolute paths, backslashes — is refused before touching a disk or a bucket.
`LocalStorage` (the default) keeps files under ``MEDIA_ROOT`` and links to ``/media/<key>``,
which the web app serves to signed-in therapists. With ``ZF_STORAGE_S3=1``, `S3Storage` writes to
``S3_BUCKET`` with server-side KMS encryption and links with presigned, expiring GET URLs
(Phase 4.3c). Both pass the same test suite (tests/integration/test_storage_contract.py).
"""

from __future__ import annotations

import os
import re
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

#: media the store accepts, by file extension
CONTENT_TYPES: dict[str, str] = {
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
_KEY = re.compile(r"[a-z0-9][a-z0-9_-]*(/[a-z0-9][a-z0-9._-]*)+")


class StorageError(ValueError):
    """A key or content type the store refuses."""


def check_key(key: str, content_type: str | None = None) -> str:
    """Return `key` if it is a safe media key (and matches `content_type`); raise otherwise."""
    if not isinstance(key, str) or not _KEY.fullmatch(key) or ".." in key:
        raise StorageError(f"invalid media key: {key!r}")
    expected = CONTENT_TYPES.get(Path(key).suffix)
    if expected is None:
        raise StorageError(f"unsupported media type: {key!r}")
    if content_type is not None and content_type != expected:
        raise StorageError(f"{key!r} must be stored as {expected}, not {content_type}")
    return key


def content_type_of(key: str) -> str:
    return CONTENT_TYPES[Path(check_key(key)).suffix]


class Storage(ABC):
    """A flat key → bytes store with links the browser can load."""

    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def url(self, key: str, expires: int = 3600) -> str:
        """A link to the object; remote stores sign it for `expires` seconds."""


class LocalStorage(Storage):
    """Files under `root`, linked as `<base_url>/<key>` (served by web/routers/media.py)."""

    def __init__(self, root: Path, base_url: str = "/media") -> None:
        self.root = Path(root).resolve()
        self.base_url = base_url.rstrip("/")

    def path(self, key: str) -> Path:
        target = (self.root / check_key(key)).resolve()
        if not target.is_relative_to(self.root):
            raise StorageError(f"invalid media key: {key!r}")
        return target

    def put(self, key: str, data: bytes, content_type: str) -> None:
        check_key(key, content_type)
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write next to the target, then rename: a reader never sees half a file.
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def get(self, key: str) -> bytes:
        return self.path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self.path(key).is_file()

    def delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)

    def url(self, key: str, expires: int = 3600) -> str:
        return f"{self.base_url}/{check_key(key)}"


class S3Storage(Storage):
    """Objects in an S3 bucket under `prefix`, encrypted with SSE-KMS, linked by presigned GETs."""

    def __init__(self, client: Any, bucket: str, prefix: str = "", kms_key_id: str = "") -> None:
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self.kms_key_id = kms_key_id

    def _object(self, key: str) -> str:
        return self.prefix + check_key(key)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        check_key(key, content_type)
        extra: dict[str, str] = {"ServerSideEncryption": "aws:kms"}
        if self.kms_key_id:
            extra["SSEKMSKeyId"] = self.kms_key_id
        self.client.put_object(
            Bucket=self.bucket,
            Key=self._object(key),
            Body=data,
            ContentType=content_type,
            CacheControl="private, max-age=86400",
            **extra,
        )

    def get(self, key: str) -> bytes:
        body = self.client.get_object(Bucket=self.bucket, Key=self._object(key))["Body"]
        data: bytes = body.read()
        return data

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=self._object(key))
        except ClientError as exc:
            if str(exc.response.get("Error", {}).get("Code")) in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._object(key))

    def url(self, key: str, expires: int = 3600) -> str:
        link: str = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": self._object(key)},
            ExpiresIn=int(expires),
        )
        return link


def s3_client(region: str = "", endpoint_url: str = "") -> Any:
    """A boto3 S3 client (SigV4, so presigned links work with KMS-encrypted objects)."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        region_name=region or None,
        endpoint_url=endpoint_url or None,
        config=Config(signature_version="s3v4"),
    )


def get_storage() -> Storage:
    """The configured store: S3 when ZF_STORAGE_S3 is on, local files otherwise."""
    from zenflow.settings import ROOT, get_settings

    settings = get_settings()
    if settings.flags.storage_s3:
        return S3Storage(
            s3_client(settings.s3_region, settings.s3_endpoint_url),
            bucket=settings.s3_bucket,
            prefix=settings.s3_prefix,
            kms_key_id=settings.s3_kms_key_id,
        )
    return LocalStorage(
        Path(settings.media_root) if settings.media_root else ROOT / "data" / "media"
    )
