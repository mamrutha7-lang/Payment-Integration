"""A rejected applicant may apply again; a live application still may not be doubled.

THE INCIDENT, as reported: "rejected people not able to reapply the student
registration". `registrations.email` was UNIQUE outright and the guard in
`submit` read every row, so an address the office had rejected - for a
mistyped USN, say - met the duplicate 409 on its corrected second attempt,
with the same opaque words a live duplicate gets, and the rejection mail had
told them to contact the same office. Migration d7e2f9a41c86 made the unique
PARTIAL (`status <> 'REJECTED'`) and the guard reads the same rule.

What these pin:

  * A rejected address applies again and gets a 201; the rejected row STAYS,
    as the record of the decision, and the new row is a new application.
  * The reviewer is told: the new row's checklist carries `prior_applications`
    as a WARN naming the reason given last time. Never a block.
  * A LIVE application on the address still refuses a second one (409), and
    the DATABASE refuses it too, so two submissions racing the guard cannot
    both land - proven against the real schema, inside a rolled-back
    transaction.
  * The rejection mail now says applying again is possible.
"""

import pytest
import uuid
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from conftest import application_files, requires_db

from app import document_store, mail_transport
from app.db import SessionLocal
from app.models.job import DegreeLevel
from app.models.registration import Registration, RegistrationStatus
from app.models.user import Role, Student, User
from app.routers import registration as registration_router


@pytest.fixture(autouse=True)
def _clean_outbox_and_rate_limit():
    """The public form is rate-limited per source address and every TestClient
    request shares one, so a module that submits several times must not be
    charged for the modules before it."""
    mail_transport.outbox.clear()
    registration_router._rate_windows.clear()
    yield
    mail_transport.outbox.clear()
    registration_router._rate_windows.clear()


@pytest.fixture
def address():
    """One applicant address, and EVERY row on it torn down whatever the test
    does - this is the module that deliberately creates two."""
    emails: list[str] = []

    def _use(email: str) -> str:
        emails.append(email)
        with SessionLocal() as db:
            db.execute(delete(Registration).where(Registration.email == email))
            db.commit()
        return email

    yield _use

    with SessionLocal() as db:
        for email in emails:
            user = db.scalar(select(User).where(User.email == email))
            if user is not None:  # nothing here approves, but never leak an account
                db.execute(delete(Student).where(Student.user_id == user.id))
                db.execute(delete(User).where(User.id == user.id))
            db.execute(delete(Registration).where(Registration.email == email))
        db.commit()


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    """Every submission carries both files now (2026-09-22); they land here."""
    monkeypatch.setattr(document_store, "_store_dir", lambda: tmp_path)
    return tmp_path


def _submit(client, email: str, usn: str):
    return client.post(
        "/api/register",
        data={
            "name": "Reapply Test",
            "email": email,
            "usn": usn,
            "phone": "+91 90000 00000",
            "personal_email": f"personal.{uuid.uuid4().hex[:8]}@gmail.com",
            "linkedin_url": "https://www.linkedin.com/in/reapply-test",
            "degree_level": "PG",
        },
        files=application_files(),
    )


@requires_db
def test_a_rejected_applicant_can_apply_again_and_the_reviewer_is_told(client, make_user, address):
    admin = make_user("rg-reapply", Role.ADMIN)
    email = address("rg.reapply@bgscet.ac.in")

    first = _submit(client, email, usn="1BG26RRA01")
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]

    rejected = client.post(
        f"/api/register/{first_id}/decision",
        headers=admin.headers,
        json={"decision": "REJECT", "note": "USN does not match the roster"},
    )
    assert rejected.status_code == 200, rejected.text
    told = [e for e in mail_transport.outbox if e.to == email and "registration" in e.subject.lower()]
    assert told and "apply again" in told[-1].text, (
        "the rejection mail must say a corrected application is possible; it is "
        "the only channel the product has to a rejected applicant"
    )

    # The corrected application. This was the 409.
    second = _submit(client, email, usn="1BG26RRA10")
    assert second.status_code == 201, second.text
    second_id = second.json()["id"]
    assert second_id != first_id
    assert second.json()["status"] == "PENDING_REVIEW"

    with SessionLocal() as db:
        rows = {
            r.id: r.status
            for r in db.scalars(select(Registration).where(Registration.email == email)).all()
        }
    assert rows == {first_id: RegistrationStatus.REJECTED, second_id: RegistrationStatus.PENDING_REVIEW}, (
        "the rejected row is the record of that decision and must stay beside the new one"
    )

    # The reviewer sees the history on the new row, as a warning and not a wall.
    queue = client.get("/api/register/pending", headers=admin.headers)
    assert queue.status_code == 200, queue.text
    row = next(r for r in queue.json() if r["id"] == second_id)
    prior = [c for c in row["checks"] if c["key"] == "prior_applications"]
    assert len(prior) == 1, row["checks"]
    assert prior[0]["status"] == "warn"
    assert "USN does not match the roster" in prior[0]["detail"]
    assert "once" in prior[0]["label"]

    # And a THIRD submission, while the second is still live, is still refused.
    third = _submit(client, email, usn="1BG26RRA10")
    assert third.status_code == 409, third.text


@requires_db
def test_an_address_that_never_applied_before_carries_no_history_line(client, make_user, address):
    """`prior_applications` appears only where there is something to say - a
    line reading "never applied before" on every row is noise in a panel meant
    to be read in two seconds."""
    admin = make_user("rg-first", Role.ADMIN)
    email = address("rg.first@bgscet.ac.in")
    r = _submit(client, email, usn="1BG26RRA02")
    assert r.status_code == 201, r.text
    queue = client.get("/api/register/pending", headers=admin.headers).json()
    row = next(x for x in queue if x["id"] == r.json()["id"])
    assert not [c for c in row["checks"] if c["key"] == "prior_applications"]


@requires_db
def test_the_database_allows_a_second_row_only_beside_a_rejected_one(address):
    """The partial unique index, against the real schema. Two LIVE rows on one
    address are refused by Postgres itself - the guard's read-then-write is not
    the only thing standing between two racing submissions - while a REJECTED
    row beside a live one is exactly what a re-application looks like."""
    email = address("rg.schema@bgscet.ac.in")

    def row(status: RegistrationStatus) -> Registration:
        return Registration(name="Schema Test", email=email, degree_level=DegreeLevel.PG, status=status)

    with SessionLocal() as db:
        db.add(row(RegistrationStatus.REJECTED))
        db.add(row(RegistrationStatus.PENDING_REVIEW))
        db.flush()  # legal: one live, one rejected
        db.add(row(RegistrationStatus.HOLD))
        with pytest.raises(IntegrityError) as refused:
            db.flush()
        assert "uq_registration_live_email" in str(refused.value)
        db.rollback()
