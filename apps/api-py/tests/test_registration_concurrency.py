"""A registration being saved must not stop the API answering anybody else.

INCIDENT (2026-09-29): students on results day were shown 504 by CloudFront.
`submit` was an `async def` doing blocking work - the database, the file write
to EFS, the archive PUT to S3 - so while one application's files were saved,
the single event loop of that API process served nothing. The requests queued
behind it passed CloudFront's 60 s origin timeout. As a plain `def` it runs on
the threadpool and the loop stays free; this pins that by making the save slow
and asking `/health` for an answer while it runs.
"""

from __future__ import annotations

import threading
import time
import uuid

import pytest
from sqlalchemy import delete

from app import document_store
from app.db import SessionLocal
from app.models.registration import Registration
from app.routers import registration as registration_router
from conftest import application_files, requires_db

#: How long each stored file takes in this test. Long enough that a blocked
#: loop is unmistakable, short enough that the test stays quick.
SLOW_SAVE_SECONDS = 1.5


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(document_store, "_store_dir", lambda: tmp_path)
    monkeypatch.setattr(registration_router, "_rate_windows", {})


@requires_db
def test_a_slow_save_does_not_freeze_other_requests(client, monkeypatch):
    real_save = registration_router.save_and_record
    saving = threading.Event()

    def slow_save(*args, **kwargs):
        saving.set()
        time.sleep(SLOW_SAVE_SECONDS)  # blocking, as the EFS write and S3 PUT are
        return real_save(*args, **kwargs)

    monkeypatch.setattr(registration_router, "save_and_record", slow_save)

    email = f"conc.{uuid.uuid4().hex[:8]}@bgscet.ac.in"
    result: dict[str, int] = {}

    def apply() -> None:
        r = client.post(
            "/api/register",
            data={
                "name": "Concurrent Applicant",
                "email": email,
                "usn": f"1BG26CON{uuid.uuid4().hex[:3].upper()}",
                "phone": "+91 98765 43210",
                "personal_email": f"conc.{uuid.uuid4().hex[:8]}@gmail.com",
                "linkedin_url": "linkedin.com/in/concurrent-applicant",
                "degree_level": "PG",
            },
            files=application_files(),
        )
        result["status"] = r.status_code

    worker = threading.Thread(target=apply)
    try:
        worker.start()
        assert saving.wait(10), "the application never reached the file save"
        started = time.monotonic()
        health = client.get("/health")
        waited = time.monotonic() - started
        worker.join(30)

        assert health.status_code == 200
        assert waited < SLOW_SAVE_SECONDS / 2, (
            f"/health waited {waited:.2f}s behind a registration's file save: "
            "the handler is blocking the event loop again"
        )
        assert result.get("status") == 201, result
    finally:
        worker.join(30)
        with SessionLocal() as db:
            db.execute(delete(Registration).where(Registration.email == email))
            db.commit()


def test_the_rate_limiter_counts_every_attempt_under_contention(monkeypatch):
    """The handlers share `_rate_windows` from several threads now; without the
    lock two attempts can read the same count and both write count + 1."""
    monkeypatch.setattr(registration_router, "_rate_windows", {})
    monkeypatch.setattr(registration_router, "_RATE_MAX_PER_WINDOW", 10_000)
    barrier = threading.Barrier(8)

    def hammer() -> None:
        barrier.wait()
        for _ in range(500):
            registration_router._rate_limit_retry_after("10.0.0.1")

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert registration_router._rate_windows["10.0.0.1"][1] == 8 * 500
