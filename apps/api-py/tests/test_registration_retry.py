"""A retry of the same filled-in form is the same application, and says so.

THE INCIDENT (2026-10-01). A student on a phone pressed Submit and the reply
was lost. Whether the application had landed or not, the honest advice is
"press Submit again" — and when it HAD landed, that press met the duplicate
guard's deliberately opaque 409, "This application could not be accepted…
contact the placement office", which reads as a refusal to somebody who has
just applied.

The register form now mints one random key per filled-in form and sends it
with every submit (`newSubmissionKey`, apps/web/.../register/form-checks.ts).
What these pin:

  * the same key on the same address answers the ORIGINAL 201 — same id, one
    row, the files stored once;
  * no key, a different key, or an older row with no key: the opaque 409,
    exactly as before, so the form is no more of a "has X applied" lookup;
  * the key is stored as its sha256, never as itself;
  * a REJECTED row's key opens nothing: a corrected application is new;
  * two submits racing past the guard: the loser is answered like a retry —
    its own application or the 409 — never the 500 it used to be;
  * a malformed key is a 422 before anything is read.
"""

import hashlib
import uuid

import pytest
from sqlalchemy import delete, select

from conftest import application_files, requires_db

from app import document_store
from app.db import SessionLocal
from app.models.registration import Registration, RegistrationStatus
from app.routers import registration as registration_router


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(document_store, "_store_dir", lambda: tmp_path)
    monkeypatch.setattr(registration_router, "_rate_windows", {})
    return tmp_path


@pytest.fixture
def email():
    address = f"retry.{uuid.uuid4().hex[:10]}@bgscet.ac.in"
    yield address
    with SessionLocal() as db:
        db.execute(delete(Registration).where(Registration.email == address))
        db.commit()


def _key() -> str:
    return uuid.uuid4().hex


def _submit(client, email: str, key: str | None):
    data = {
        "name": "Retry Applicant",
        "email": email,
        "usn": f"1BG26RT{uuid.uuid4().hex[:3].upper()}",
        "phone": "+91 90000 00000",
        "personal_email": f"retry.{uuid.uuid4().hex[:8]}@gmail.com",
        "linkedin_url": "https://www.linkedin.com/in/retry-applicant",
        "degree_level": "PG",
    }
    if key is not None:
        data["submission_key"] = key
    return client.post("/api/register", data=data, files=application_files())


def _rows(email: str) -> list[Registration]:
    with SessionLocal() as db:
        return list(db.scalars(select(Registration).where(Registration.email == email)))


@requires_db
def test_the_same_form_retried_gets_its_own_application_back(client, email, _tmp_store):
    key = _key()
    first = _submit(client, email, key)
    assert first.status_code == 201, first.text
    files_after_first = sorted(p.name for p in _tmp_store.iterdir())

    again = _submit(client, email, key)
    assert again.status_code == 201, (
        "a retry carrying the form's own key must be answered with the application "
        f"it already created, not the opaque 409: {again.status_code} {again.text}"
    )
    assert again.json() == first.json(), "the same reply the lost one would have been"
    assert len(_rows(email)) == 1, "and still exactly one application"
    assert sorted(p.name for p in _tmp_store.iterdir()) == files_after_first, (
        "a retry stores no second copy of the CV and photo"
    )


@requires_db
def test_a_different_key_or_none_still_meets_the_opaque_409(client, email):
    assert _submit(client, email, _key()).status_code == 201
    for key in (_key(), None):
        refused = _submit(client, email, key)
        assert refused.status_code == 409, refused.text
        assert "could not be accepted" in refused.json()["detail"]
        assert "already exists" not in refused.json()["detail"].lower()
    assert len(_rows(email)) == 1


@requires_db
def test_a_row_written_without_a_key_never_matches_one(client, email):
    assert _submit(client, email, None).status_code == 201
    assert _submit(client, email, _key()).status_code == 409


@requires_db
def test_the_key_is_stored_as_its_hash(client, email):
    key = _key()
    assert _submit(client, email, key).status_code == 201
    (row,) = _rows(email)
    assert row.submission_key_hash == hashlib.sha256(key.encode()).hexdigest()
    assert key not in (row.submission_key_hash or "")


@requires_db
def test_a_rejected_rows_key_opens_nothing(client, email):
    key = _key()
    first = _submit(client, email, key)
    assert first.status_code == 201
    with SessionLocal() as db:
        row = db.get(Registration, first.json()["id"])
        row.status = RegistrationStatus.REJECTED
        db.commit()
    second = _submit(client, email, key)
    assert second.status_code == 201
    assert second.json()["id"] != first.json()["id"], (
        "a rejection is not live; the next submit is a new application"
    )


@requires_db
def test_the_loser_of_a_race_is_answered_like_a_retry_not_a_500(client, email, monkeypatch):
    """Both submits pass the guard before either commits; the database's partial
    unique index refuses the second INSERT. Simulated by blinding the FIRST
    guard read of the second request, so its INSERT reaches the index."""
    key = _key()
    assert _submit(client, email, key).status_code == 201

    real = registration_router._duplicate_answer

    def blind_once(calls=[0]):  # noqa: B006 — a counter shared by the closure
        def answer(db, address, key_hash):
            calls[0] += 1
            return None if calls[0] == 1 else real(db, address, key_hash)

        return answer

    monkeypatch.setattr(registration_router, "_duplicate_answer", blind_once([0]))
    same = _submit(client, email, key)
    assert same.status_code == 201, f"a double-tapped Submit must not 500: {same.text}"

    monkeypatch.setattr(registration_router, "_duplicate_answer", blind_once([0]))
    other = _submit(client, email, _key())
    assert other.status_code == 409, f"a racing stranger gets the opaque 409: {other.text}"
    assert len(_rows(email)) == 1


@pytest.mark.parametrize("bad", ["short", "has spaces in it ok", "x" * 65, "ünïcode-key-0000000"])
def test_a_malformed_key_is_refused_before_anything_is_read(client, bad):
    r = _submit(client, f"bad.{uuid.uuid4().hex[:8]}@bgscet.ac.in", bad)
    assert r.status_code == 422, r.text
