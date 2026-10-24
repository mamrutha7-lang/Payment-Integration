"""The recording bucket: key layout, uploads through an injected client, and
the presigned `recording_s3_url` — signed locally by a real boto3 client with
dummy static credentials, so the exact query the browser sends is asserted."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from botocore.config import Config

from app.voice_platform.storage import s3 as s3mod
from app.voice_platform.storage.s3 import (
    MAX_PRESIGN_SECONDS,
    RecordingStore,
    RecordingStoreError,
    make_presigned_url,
    recording_store,
    safe_segment,
)


@pytest.fixture
def signer():
    return boto3.client(
        "s3",
        region_name="ap-south-1",
        aws_access_key_id="AKIATESTTESTTESTTEST",
        aws_secret_access_key="secret",
        config=Config(signature_version="s3v4"),
    )


class FakeS3Client:
    def __init__(self) -> None:
        self.puts: list[dict] = []
        self.uploads: list[tuple] = []
        self.fail = False

    def put_object(self, **kwargs) -> None:
        if self.fail:
            raise RuntimeError("AccessDenied")
        self.puts.append(kwargs)

    def upload_file(self, path, bucket, key, ExtraArgs=None) -> None:
        self.uploads.append((path, bucket, key, ExtraArgs))

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://fake/{Params['Bucket']}/{Params['Key']}?X-Amz-Expires={ExpiresIn}"

    def delete_object(self, **kwargs) -> None:
        self.puts.append({"deleted": kwargs})


def test_presigned_url_names_the_object_and_carries_the_ttl(signer) -> None:
    url = make_presigned_url(signer, "reep-recordings", "recordings/UG/2026/09/abc.wav", 3600)
    parsed = urlparse(url)
    assert parsed.scheme == "https" and "reep-recordings" in parsed.netloc + parsed.path
    assert parsed.path.endswith("/recordings/UG/2026/09/abc.wav")
    query = parse_qs(parsed.query)
    assert query["X-Amz-Expires"] == ["3600"]
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert "X-Amz-Signature" in query and "X-Amz-Credential" in query


@pytest.mark.parametrize("ttl", [0, -1, MAX_PRESIGN_SECONDS + 1])
def test_presign_ttl_is_bounded_by_s3s_own_limit(signer, ttl: int) -> None:
    with pytest.raises(ValueError):
        make_presigned_url(signer, "b", "k", ttl)


def test_presign_requires_a_bucket_and_a_key(signer) -> None:
    with pytest.raises(ValueError):
        make_presigned_url(signer, "", "k", 60)
    with pytest.raises(ValueError):
        make_presigned_url(signer, "b", "", 60)


def test_key_layout_is_partitioned_by_degree_and_month() -> None:
    store = RecordingStore(FakeS3Client(), "bucket", prefix="/recordings/")
    when = datetime(2026, 9, 5, tzinfo=timezone.utc)
    assert store.key_for("ug", "abc123", "wav", when=when) == "recordings/UG/2026/09/abc123.wav"
    assert store.key_for("PG", "s-1", ".MP3", when=when) == "recordings/PG/2026/09/s-1.mp3"


@pytest.mark.parametrize("bad", ["../x", "a/b", "a b", "", "x" * 65])
def test_unsafe_key_segments_are_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        safe_segment(bad)


def test_unsupported_extension_is_refused() -> None:
    store = RecordingStore(FakeS3Client(), "bucket")
    with pytest.raises(ValueError):
        store.key_for("UG", "abc", "exe")


def test_upload_bytes_sets_content_type_and_wraps_failures() -> None:
    client = FakeS3Client()
    store = RecordingStore(client, "bucket", presign_ttl_seconds=900)
    stored = store.upload_bytes("recordings/UG/2026/09/a.wav", b"RIFF....", metadata={"session_id": "a"})
    assert stored.s3_uri == "s3://bucket/recordings/UG/2026/09/a.wav" and stored.size == 8
    assert client.puts[0]["ContentType"] == "audio/wav" and client.puts[0]["Metadata"] == {"session_id": "a"}
    assert store.presigned_url(stored.key).endswith("X-Amz-Expires=900")
    assert store.presigned_url(stored.key, 60).endswith("X-Amz-Expires=60")
    client.fail = True
    with pytest.raises(RecordingStoreError):
        store.upload_bytes("k.wav", b"x")


def test_recording_store_is_none_until_a_bucket_is_configured(monkeypatch) -> None:
    monkeypatch.setattr(s3mod.settings, "platform_recordings_bucket", "")
    assert recording_store() is None
    monkeypatch.setattr(s3mod.settings, "platform_recordings_bucket", "reep-recordings")
    monkeypatch.setattr(s3mod.settings, "platform_recordings_prefix", "rec")
    store = recording_store(client=FakeS3Client())
    assert store is not None and store.bucket == "reep-recordings" and store.prefix == "rec"
