"""Volume quotas: the arithmetic, and the fact that every writer brings one.

MAX_BYTES bounds ONE file. On its own it bounds nothing: an authenticated
caller can post 10 MB as many times as they like, the disk fills, and every
other upload starts failing on a machine whose health checks all pass.

The quota that stops that used to live in the CALLER, with a comment naming
"the single caller of save_bytes". Three callers later, routers/alumni.py had
no check at all. These tests pin the fix from both ends: the arithmetic is
correct (no database needed -- four integers), and no writer can store bytes
without declaring who is counting.
"""

import ast
from pathlib import Path

import pytest

from app.document_store import (
    MAX_BYTES,
    AllowanceExceeded,
    QuotaRejected,
    ShelfFull,
    UploadRejected,
    VolumeQuota,
    save_bytes,
)

# A one-pixel PNG: real magic bytes, so _sniff accepts it.
PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"\x00" * 32


class TestTheArithmetic:
    """No database, no fixtures -- which is the point of keeping the ORM out."""

    def test_a_full_shelf_refuses_before_the_body_is_read(self):
        q = VolumeQuota(max_files=3, max_bytes=10_000, used_files=3, used_bytes=0)
        with pytest.raises(ShelfFull) as exc:
            q.check_slot()
        # 409, never 413: the file is fine, the shelf is full. And it must say
        # what to DO -- a limit with no way out reads as a dead end.
        assert exc.value.status_code == 409
        assert "Delete one you no longer need" in str(exc.value)

    def test_room_for_one_more_passes(self):
        VolumeQuota(max_files=3, max_bytes=10_000, used_files=2, used_bytes=0).check_slot()

    def test_the_limit_is_a_ceiling_not_a_wall(self):
        """used == max refuses; used == max - 1 admits. Off by one here is a
        student told they are full one file early, every time."""
        VolumeQuota(max_files=1, max_bytes=99, used_files=0, used_bytes=0).check_slot()
        with pytest.raises(ShelfFull):
            VolumeQuota(max_files=1, max_bytes=99, used_files=1, used_bytes=0).check_slot()

    def test_bytes_are_counted_against_what_is_already_stored(self):
        q = VolumeQuota(max_files=99, max_bytes=1_000, used_files=1, used_bytes=900)
        q.check_bytes(100)  # exactly to the line is allowed
        with pytest.raises(AllowanceExceeded) as exc:
            q.check_bytes(101)
        assert exc.value.status_code == 413

    def test_the_message_names_what_the_owner_calls_these(self):
        """A staff member uploads certificates, not "files". The person reading
        this hit a limit; the words are theirs, not the schema's."""
        q = VolumeQuota(max_files=1, max_bytes=10, used_files=1, used_bytes=0, noun="certificate")
        with pytest.raises(ShelfFull) as exc:
            q.check_slot()
        assert "certificates" in str(exc.value)

    def test_single_slot_holds_exactly_one(self):
        q = VolumeQuota.single_slot("resume")
        q.check_slot()
        assert VolumeQuota(1, MAX_BYTES, 1, 0, "resume").max_files == 1

    def test_a_quota_refusal_is_not_an_upload_refusal(self):
        """QuotaRejected must NOT subclass UploadRejected. Routers answer that
        one with 422 -- so a shelf-full refusal inheriting from it would tell a
        student their perfectly valid PDF was malformed."""
        assert not issubclass(QuotaRejected, UploadRejected)
        assert issubclass(ShelfFull, QuotaRejected)
        assert issubclass(AllowanceExceeded, QuotaRejected)


class TestTheStoreRefusesASilentCaller:
    def test_save_bytes_will_not_store_without_a_quota(self):
        """The whole fix in one assertion. A default would let the next writer
        store bytes for an owner nobody counts -- which is exactly how
        routers/alumni.py came to have no quota while the module comment still
        claimed a single writer."""
        with pytest.raises(TypeError):
            save_bytes(PNG)  # type: ignore[call-arg]

    def test_save_bytes_enforces_the_allowance_as_a_backstop(self, tmp_path, monkeypatch):
        """Repeated inside the store even though callers check first: by that
        line the size is known for certain, and a backstop beats trusting three
        call sites."""
        from app import document_store

        monkeypatch.setattr(document_store, "_store_dir", lambda: tmp_path)
        full = VolumeQuota(max_files=9, max_bytes=10, used_files=0, used_bytes=10)
        with pytest.raises(AllowanceExceeded):
            document_store.save_bytes(PNG, quota=full)
        assert list(tmp_path.iterdir()) == [], "refused bytes must not reach disk"

    def test_a_permitted_file_is_stored(self, tmp_path, monkeypatch):
        from app import document_store

        monkeypatch.setattr(document_store, "_store_dir", lambda: tmp_path)
        name, mime, size = document_store.save_bytes(
            PNG, quota=VolumeQuota.single_slot("resume")
        )
        assert mime == "image/png"
        assert size == len(PNG)
        assert (tmp_path / name).read_bytes() == PNG


def test_every_save_bytes_call_site_passes_a_quota():
    """The guard that survives me.

    A future endpoint that stores a file is exactly where this regresses -- the
    author copies a nearby call, and nothing on screen says a quota is owed.
    The keyword-only argument makes it a TypeError at runtime; this makes it a
    test failure at review time, naming the file.
    """
    routers = Path(__file__).resolve().parents[1] / "app" / "routers"
    offenders = []
    for path in routers.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if name != "save_bytes":
                continue
            if not any(kw.arg == "quota" for kw in node.keywords):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        "save_bytes called without quota= at: "
        + ", ".join(offenders)
        + " — every writer must declare who is counting, or the quota is decoration"
    )
