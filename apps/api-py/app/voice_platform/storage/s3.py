"""The S3 Recording Bucket: upload the finished dual-channel file and mint the
presigned `recording_s3_url`.

The presigned URL is DERIVED on every read and never stored. A stored URL is a
stored expiry: the Postgres row carries the object key, and whoever asks for
the recording gets a fresh link bounded by the degree level's policy TTL.

`make_presigned_url` is a pure function of a client, a bucket, a key and a TTL
so tests can run it against a boto3 client built with static dummy credentials
— presigning is a local signature, it never talks to AWS — and assert on the
exact query the browser will send.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ...config import settings

log = logging.getLogger("app.voice_platform.storage.s3")

#: S3's own ceiling for a SigV4 presigned URL (7 days). Asking for more is
#: refused by the service, so refuse it here with a sentence.
MAX_PRESIGN_SECONDS = 7 * 24 * 3600

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

CONTENT_TYPES = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "json": "application/json",
}


class RecordingStoreError(RuntimeError):
    """The upload or the presign could not be done. Callers keep the local
    copy and record the failure; the interview never depends on this."""


@dataclass(frozen=True)
class StoredObject:
    bucket: str
    key: str
    size: int
    content_type: str

    @property
    def s3_uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}"


def safe_segment(value: str) -> str:
    """One path segment of an object key: letters, digits, `_`, `-`. Anything
    else — a slash, a dot-dot, a space — is refused, because the key is built
    from a session id and a degree level and neither should be able to name
    another session's object."""
    if not isinstance(value, str) or not _SEGMENT_RE.match(value):
        raise ValueError(f"unsafe key segment: {value!r}")
    return value


def content_type_for(ext: str) -> str:
    return CONTENT_TYPES.get(ext.lower().lstrip("."), "application/octet-stream")


def make_presigned_url(client: Any, bucket: str, key: str, ttl_seconds: int) -> str:
    """A GET link for `key`, valid `ttl_seconds`. Pure: signs locally."""
    ttl = int(ttl_seconds)
    if ttl < 1 or ttl > MAX_PRESIGN_SECONDS:
        raise ValueError(
            f"presign TTL must be between 1 and {MAX_PRESIGN_SECONDS} seconds, got {ttl}"
        )
    if not bucket or not key:
        raise ValueError("bucket and key are required")
    return client.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=ttl
    )


class RecordingStore:
    """The recording bucket, with the key layout in one place."""

    def __init__(
        self,
        client: Any,
        bucket: str,
        *,
        prefix: str = "recordings",
        presign_ttl_seconds: int = 3600,
    ) -> None:
        if not bucket:
            raise ValueError("a bucket name is required")
        self._client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.presign_ttl_seconds = int(presign_ttl_seconds)

    def key_for(
        self, degree_level: str, session_id: str, ext: str, *, when: datetime | None = None
    ) -> str:
        """`<prefix>/<UG|PG>/<yyyy>/<mm>/<session_id>.<ext>` — partitioned by
        month so a lifecycle rule and a human listing both work."""
        stamp = when or datetime.now(timezone.utc)
        degree = safe_segment(degree_level.upper())
        sid = safe_segment(session_id)
        ext = ext.lower().lstrip(".")
        if ext not in CONTENT_TYPES:
            raise ValueError(f"unsupported recording extension: {ext!r}")
        parts = [p for p in (self.prefix, degree, f"{stamp:%Y}", f"{stamp:%m}") if p]
        return "/".join(parts) + f"/{sid}.{ext}"

    def upload_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> StoredObject:
        ctype = content_type or content_type_for(key.rsplit(".", 1)[-1])
        try:
            self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=ctype,
                Metadata=metadata or {},
            )
        except Exception as exc:  # noqa: BLE001 - wrapped for the caller
            raise RecordingStoreError(f"S3 put_object failed for {key}: {exc}") from exc
        return StoredObject(self.bucket, key, len(data), ctype)

    def upload_file(self, key: str, path: Path, *, content_type: str | None = None) -> StoredObject:
        ctype = content_type or content_type_for(path.suffix)
        try:
            self._client.upload_file(
                str(path), self.bucket, key, ExtraArgs={"ContentType": ctype}
            )
        except Exception as exc:  # noqa: BLE001
            raise RecordingStoreError(f"S3 upload_file failed for {key}: {exc}") from exc
        return StoredObject(self.bucket, key, path.stat().st_size, ctype)

    def presigned_url(self, key: str, ttl_seconds: int | None = None) -> str:
        ttl = self.presign_ttl_seconds if ttl_seconds is None else int(ttl_seconds)
        return make_presigned_url(self._client, self.bucket, key, ttl)

    def presign_expiry(self, ttl_seconds: int | None = None) -> datetime:
        ttl = self.presign_ttl_seconds if ttl_seconds is None else int(ttl_seconds)
        return datetime.now(timezone.utc) + timedelta(seconds=ttl)

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:  # noqa: BLE001
            raise RecordingStoreError(f"S3 delete_object failed for {key}: {exc}") from exc


def recording_store(client: Any | None = None) -> RecordingStore | None:
    """The configured store, or None — and None is the answer wherever
    PLATFORM_RECORDINGS_BUCKET is blank. The boto3 import is lazy for the same
    reason app/ai/llm.py's is: a deployment that never uploads should not pay
    for the client at boot."""
    bucket = settings.platform_recordings_bucket.strip()
    if not bucket:
        return None
    if client is None:
        import boto3

        region = settings.platform_region or None
        client = boto3.client("s3", region_name=region)
    return RecordingStore(
        client,
        bucket,
        prefix=settings.platform_recordings_prefix,
        presign_ttl_seconds=settings.platform_presign_ttl_seconds,
    )
