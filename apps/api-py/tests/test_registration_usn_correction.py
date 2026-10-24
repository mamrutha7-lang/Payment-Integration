"""Correcting a USN on an application, saying who already holds one, and
matching a USN whatever case it was typed in.

THE DEAD END THIS MODULE PINS SHUT (2026-10-16). An application whose USN
another student held showed "USN … already belongs to another student" and
told the reviewer to "correct the USN on this application, or reject it as a
duplicate". Nothing could correct it: there was no endpoint and no control, so
the only button left was Reject, and the line did not say who held the number,
so the reviewer could not tell a mistyped digit from the same person applying
twice. `PATCH /api/register/{id}` is the correction and the checklist names the
holder for a programme-wide reviewer.

Every test that clears a block also presses Approve, because a correction that
the checklist calls fine and `_provision_student` still refuses is the
green-check defect `test_registration_checks.py` exists to prevent.

THE CASE OF A USN (2026-10-16, the same day). The roster stores USNs upper case
and the form stored them as typed, and every comparison was case-sensitive: an
applicant who typed `1mp25ba033` was not flagged against `1MP25BA033`, and
approving them seated a second student with the same number. The last section
pins `app/usn.py`: writers store upper case, and lookups compare upper case on
both sides so a lowercase row written before the fix is still found.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from app.models.institution import STATUS_ACTIVE, College
from app.models.job import DegreeLevel
from app.models.redesign import AuditEvent
from app.models.registration import Registration, RegistrationStatus
from app.models.student_profile import StudentProfile
from app.models.user import Role, Student, User
from app.routers.registration import CHECK_BLOCKED, CHECK_OK, CHECK_USN_UNIQUE

from app.usn import normalise_usn

from conftest import application_files, requires_db


# --------------------------------------------------------------- fixtures --


@pytest.fixture
def applicant():
    """An application in any status, cleaned up whatever the test does (the
    local twin of `test_registration_hold.py`'s, for its reason: a leaked row
    is reused by the next run and hides its regression)."""
    created: list[tuple[str, bool]] = []

    def _make(
        email: str,
        name: str = "Correction Applicant",
        *,
        status: RegistrationStatus = RegistrationStatus.PENDING_REVIEW,
        usn: str | None = None,
        college_id: str | None = None,
    ) -> str:
        with SessionLocal() as db:
            pre_existing = db.scalar(select(User).where(User.email == email)) is not None
            db.execute(delete(Registration).where(Registration.email == email))
            reg = Registration(
                name=name,
                email=email,
                usn=usn,
                degree_level=DegreeLevel.PG,
                status=status,
                college_id=college_id,
            )
            db.add(reg)
            db.commit()
            created.append((email, pre_existing))
            return reg.id

    yield _make

    with SessionLocal() as db:
        for email, pre_existing in created:
            user = db.scalar(select(User).where(User.email == email))
            if user is not None and not pre_existing:
                for stu in db.scalars(select(Student).where(Student.user_id == user.id)).all():
                    db.execute(delete(StudentProfile).where(StudentProfile.student_id == stu.id))
                    db.execute(delete(Student).where(Student.id == stu.id))
            reg_ids = [
                rid for (rid,) in db.execute(
                    select(Registration.id).where(Registration.email == email)
                ).all()
            ]
            if reg_ids:
                db.execute(
                    delete(AuditEvent).where(
                        AuditEvent.entity_type == "registration",
                        AuditEvent.entity_id.in_(reg_ids),
                    )
                )
            db.execute(delete(Registration).where(Registration.email == email))
            if user is not None and not pre_existing:
                db.execute(delete(User).where(User.id == user.id))
        db.commit()


@pytest.fixture
def scoped_grant():
    """`admin.registrations` on one college, written as a row for the reason
    `test_registration_hold.py` gives: an API-made grant of a `carries_pii`
    capability by a deputy lands pending and holds nothing."""
    made: list[str] = []

    def _grant(user_id: str, level: ScopeLevel, target_id: str) -> None:
        with SessionLocal() as db:
            row = CapabilityGrant(
                capability="admin.registrations",
                subject_kind=SubjectKind.USER,
                subject_user_id=user_id,
                scope_level=level,
                scope_id=target_id,
                reason="the USN correction tests need a reviewer scoped to one college",
            )
            db.add(row)
            db.commit()
            made.append(row.id)

    yield _grant

    with SessionLocal() as db:
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.id.in_(made)))
        db.commit()


@pytest.fixture
def college():
    made: list[str] = []

    def _make() -> str:
        tag = uuid.uuid4().hex[:6]
        with SessionLocal() as db:
            row = College(code=f"U{tag.upper()}", name="Correction College", status=STATUS_ACTIVE)
            db.add(row)
            db.commit()
            made.append(row.id)
            return row.id

    yield _make

    with SessionLocal() as db:
        db.execute(delete(College).where(College.id.in_(made)))
        db.commit()


def _new_usn(prefix: str = "1BG26FIX") -> str:
    return f"{prefix}{uuid.uuid4().hex[:4].upper()}"


def _give_usn(user_id: str, usn: str) -> str:
    """Put `usn` on the Student row `make_user` created; returns its id."""
    with SessionLocal() as db:
        student = db.scalar(select(Student).where(Student.user_id == user_id))
        student.usn = usn
        db.commit()
        return student.id


def _usn_check(client, reviewer, registration_id: str) -> dict:
    r = client.get("/api/register/pending", headers=reviewer.headers)
    assert r.status_code == 200, r.text
    for row in r.json():
        if row["id"] == registration_id:
            return {c["key"]: c for c in row["checks"]}[CHECK_USN_UNIQUE]
    raise AssertionError(f"{registration_id} is not in this reviewer's queue")


def _email() -> str:
    return f"usnfix-{uuid.uuid4().hex[:8]}@bgscet.ac.in"


# ------------------------------------------------------- the correction --


@requires_db
def test_correcting_a_mistyped_usn_clears_the_block_and_approve_succeeds(
    client, make_user, applicant
):
    """The whole walk the screenshot could not finish: blocked, corrected,
    approved, with the corrected number on the new student and on the trail."""
    admin = make_user("usnfix-walk", Role.ADMIN)
    holder = make_user("usnfix-walk-holder", Role.STUDENT)
    taken = _new_usn()
    _give_usn(holder.user_id, taken)
    email = _email()
    reg_id = applicant(email, usn=taken)
    assert _usn_check(client, admin, reg_id)["status"] == CHECK_BLOCKED

    right = _new_usn()
    r = client.patch(f"/api/register/{reg_id}", headers=admin.headers, json={"usn": right.lower()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["usn"] == right, "a USN is stored upper case, as the roster stores it"
    returned = {c["key"]: c for c in body["checks"]}
    assert returned[CHECK_USN_UNIQUE]["status"] == CHECK_OK, (
        "the answer carries the new verdict, so the panel needs no second request"
    )

    decision = client.post(
        f"/api/register/{reg_id}/decision", headers=admin.headers, json={"decision": "APPROVE"}
    )
    assert decision.status_code == 200, decision.text
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        student = db.scalar(select(Student).where(Student.user_id == user.id))
        assert student.usn == right
        events = db.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_type == "registration",
                AuditEvent.entity_id == reg_id,
                AuditEvent.action == "CORRECTED",
            )
        ).all()
        assert len(events) == 1
        assert events[0].before_json == {"usn": taken}
        assert events[0].after_json == {"usn": right}


@requires_db
def test_the_same_usn_again_writes_nothing(client, make_user, applicant):
    admin = make_user("usnfix-same", Role.ADMIN)
    usn = _new_usn()
    reg_id = applicant(_email(), usn=usn)

    r = client.patch(f"/api/register/{reg_id}", headers=admin.headers, json={"usn": f"  {usn}  "})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        assert db.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_type == "registration", AuditEvent.entity_id == reg_id
            )
        ).all() == [], "an unchanged USN is not a correction"


@requires_db
def test_a_correction_onto_another_students_usn_is_refused(client, make_user, applicant):
    """Correcting one clash into another helps nobody, so it is refused here
    rather than discovered at Approve."""
    admin = make_user("usnfix-clash", Role.ADMIN)
    holder = make_user("usnfix-clash-holder", Role.STUDENT)
    taken = _new_usn()
    _give_usn(holder.user_id, taken)
    original = _new_usn()
    reg_id = applicant(_email(), usn=original)

    r = client.patch(f"/api/register/{reg_id}", headers=admin.headers, json={"usn": taken})
    assert r.status_code == 409, r.text
    assert holder.email in r.json()["detail"], "the Main Admin is told whose number it is"
    with SessionLocal() as db:
        assert db.get(Registration, reg_id).usn == original


@requires_db
def test_the_applicants_own_record_holding_the_usn_is_not_a_clash(client, make_user, applicant):
    """GUARD 3's control flow: an applicant who already has a Student row keeps
    it, so their own number on the application is fine."""
    admin = make_user("usnfix-own", Role.ADMIN)
    own = make_user("usnfix-own-app", Role.STUDENT)
    usn = _new_usn()
    _give_usn(own.user_id, usn)
    reg_id = applicant(own.email, usn=_new_usn())

    r = client.patch(f"/api/register/{reg_id}", headers=admin.headers, json={"usn": usn})
    assert r.status_code == 200, r.text


@requires_db
def test_a_decided_application_cannot_be_corrected(client, make_user, applicant):
    admin = make_user("usnfix-decided", Role.ADMIN)
    usn = _new_usn()
    reg_id = applicant(_email(), usn=usn, status=RegistrationStatus.REJECTED)

    r = client.patch(f"/api/register/{reg_id}", headers=admin.headers, json={"usn": _new_usn()})
    assert r.status_code == 409, r.text
    with SessionLocal() as db:
        assert db.get(Registration, reg_id).usn == usn


@requires_db
def test_a_held_application_can_be_corrected(client, make_user, applicant):
    """HOLD is decidable, and "held until the USN is checked" is the obvious
    reason to hold one."""
    admin = make_user("usnfix-held", Role.ADMIN)
    reg_id = applicant(_email(), usn=_new_usn(), status=RegistrationStatus.HOLD)

    r = client.patch(f"/api/register/{reg_id}", headers=admin.headers, json={"usn": _new_usn()})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "HOLD"


@requires_db
def test_a_blank_usn_and_an_unknown_field_are_refused(client, make_user, applicant):
    admin = make_user("usnfix-422", Role.ADMIN)
    usn = _new_usn()
    reg_id = applicant(_email(), usn=usn)

    assert client.patch(
        f"/api/register/{reg_id}", headers=admin.headers, json={"usn": "   "}
    ).status_code == 422
    assert client.patch(
        f"/api/register/{reg_id}",
        headers=admin.headers,
        json={"usn": _new_usn(), "email": "someone.else@bgscet.ac.in"},
    ).status_code == 422, "a field this endpoint does not apply must not be silently dropped"
    with SessionLocal() as db:
        assert db.get(Registration, reg_id).usn == usn


@requires_db
def test_a_student_cannot_correct_an_application(client, make_user, applicant):
    student = make_user("usnfix-student", Role.STUDENT)
    reg_id = applicant(_email(), usn=_new_usn())

    r = client.patch(f"/api/register/{reg_id}", headers=student.headers, json={"usn": _new_usn()})
    assert r.status_code == 403, r.text


@requires_db
def test_a_scoped_reviewer_cannot_correct_an_application_out_of_reach(
    client, make_user, applicant, scoped_grant, college
):
    """The same predicate as the queue and every other button on it."""
    reviewer = make_user("usnfix-unreach", Role.MENTOR)
    scoped_grant(reviewer.user_id, ScopeLevel.COLLEGE, college())
    usn = _new_usn()
    reg_id = applicant(_email(), usn=usn)

    r = client.patch(f"/api/register/{reg_id}", headers=reviewer.headers, json={"usn": _new_usn()})
    assert r.status_code == 403, r.text
    with SessionLocal() as db:
        assert db.get(Registration, reg_id).usn == usn


# --------------------------------------------- who holds the USN, and who is told --


@requires_db
def test_the_main_admin_is_told_who_holds_the_usn(client, make_user, applicant):
    """A mistyped USN, the same person under a second address and another
    record with the wrong number all read the same without the holder's name."""
    admin = make_user("usnfix-who", Role.ADMIN)
    holder = make_user("usnfix-who-holder", Role.STUDENT)
    taken = _new_usn()
    holder_student_id = _give_usn(holder.user_id, taken)
    reg_id = applicant(_email(), usn=taken)

    check = _usn_check(client, admin, reg_id)
    assert check["status"] == CHECK_BLOCKED
    assert holder.email in check["label"]
    assert "Voice Test usnfix-who-holder" in check["label"]
    assert check["student_id"] == holder_student_id


@requires_db
def test_a_scoped_reviewer_is_not_told_who_holds_the_usn(
    client, make_user, applicant, scoped_grant, college
):
    """The holder may be outside a scoped reviewer's grant, so the line says
    the USN is taken and not by whom; the 409 on a correction says the same."""
    reviewer = make_user("usnfix-scoped", Role.MENTOR)
    holder = make_user("usnfix-scoped-holder", Role.STUDENT)
    taken = _new_usn()
    _give_usn(holder.user_id, taken)
    college_id = college()
    scoped_grant(reviewer.user_id, ScopeLevel.COLLEGE, college_id)
    reg_id = applicant(_email(), usn=taken, college_id=college_id)

    check = _usn_check(client, reviewer, reg_id)
    assert check["status"] == CHECK_BLOCKED
    assert holder.email not in check["label"]
    assert check["student_id"] is None

    other = applicant(_email(), usn=_new_usn(), college_id=college_id)
    r = client.patch(f"/api/register/{other}", headers=reviewer.headers, json={"usn": taken})
    assert r.status_code == 409, r.text
    assert holder.email not in r.json()["detail"]


@requires_db
def test_the_refusal_at_approve_never_names_the_holder(client, make_user, applicant):
    """`_apply_rule` writes `_provision_student`'s refusal into the PUBLIC
    `decision_reason` when a rule's auto-approve is refused, so the holder's
    name and address belong in the staff checklist and never in that 409."""
    admin = make_user("usnfix-public", Role.ADMIN)
    holder = make_user("usnfix-public-holder", Role.STUDENT)
    taken = _new_usn()
    _give_usn(holder.user_id, taken)
    reg_id = applicant(_email(), usn=taken)

    decision = client.post(
        f"/api/register/{reg_id}/decision", headers=admin.headers, json={"decision": "APPROVE"}
    )
    assert decision.status_code == 409, decision.text
    assert holder.email not in decision.json()["detail"]
    assert "Voice Test" not in decision.json()["detail"]


# -------------------------------------------------- the case of a USN --


def test_a_usn_is_normalised_to_the_rosters_spelling():
    assert normalise_usn("  1mp25ba033 ") == "1MP25BA033"
    assert normalise_usn("   ") is None
    assert normalise_usn(None) is None


@requires_db
def test_the_form_stores_a_lowercase_usn_upper_case(client, tmp_document_store):
    email = _email()
    usn = _new_usn("1bg26low").lower()
    try:
        r = client.post(
            "/api/register",
            data={
                "name": "Lowercase Applicant",
                "email": email,
                "usn": f" {usn} ",
                "phone": "+91 90000 00000",
                "personal_email": f"personal.{uuid.uuid4().hex[:8]}@gmail.com",
                "linkedin_url": "https://www.linkedin.com/in/lowercase-applicant",
                "degree_level": "PG",
            },
            files=application_files(),
        )
        assert r.status_code == 201, r.text
        assert r.json()["usn"] == usn.upper()
        with SessionLocal() as db:
            reg = db.scalar(select(Registration).where(Registration.email == email))
            assert reg.usn == usn.upper()
    finally:
        with SessionLocal() as db:
            db.execute(delete(Registration).where(Registration.email == email))
            user = db.scalar(select(User).where(User.email == email))
            if user is not None:
                for stu in db.scalars(select(Student).where(Student.user_id == user.id)).all():
                    db.execute(delete(StudentProfile).where(StudentProfile.student_id == stu.id))
                    db.execute(delete(Student).where(Student.id == stu.id))
                db.execute(delete(User).where(User.id == user.id))
            db.commit()


@requires_db
def test_a_lowercase_application_is_blocked_by_an_upper_case_holder(client, make_user, applicant):
    """The reported shape: the roster's `1MP25BA033`, typed as `1mp25ba033`.
    The row is written directly, as every application stored before the fix
    was, so this also covers the queue as it stands on deploy day."""
    admin = make_user("usnfix-case-up", Role.ADMIN)
    holder = make_user("usnfix-case-up-holder", Role.STUDENT)
    taken = _new_usn()
    _give_usn(holder.user_id, taken)
    reg_id = applicant(_email(), usn=taken.lower())

    check = _usn_check(client, admin, reg_id)
    assert check["status"] == CHECK_BLOCKED
    assert holder.email in check["label"]

    decision = client.post(
        f"/api/register/{reg_id}/decision", headers=admin.headers, json={"decision": "APPROVE"}
    )
    assert decision.status_code == 409, decision.text
    with SessionLocal() as db:
        assert db.scalar(select(Student).where(Student.usn == taken.lower())) is None, (
            "a refused approval must not seat a lowercase twin"
        )


@requires_db
def test_an_upper_case_application_is_blocked_by_a_lowercase_holder(client, make_user, applicant):
    """The other direction: a student approved before the fix with a lowercase
    USN still holds that number."""
    admin = make_user("usnfix-case-low", Role.ADMIN)
    holder = make_user("usnfix-case-low-holder", Role.STUDENT)
    taken = _new_usn()
    _give_usn(holder.user_id, taken.lower())
    reg_id = applicant(_email(), usn=taken)

    assert _usn_check(client, admin, reg_id)["status"] == CHECK_BLOCKED
    decision = client.post(
        f"/api/register/{reg_id}/decision", headers=admin.headers, json={"decision": "APPROVE"}
    )
    assert decision.status_code == 409, decision.text
    assert client.patch(
        f"/api/register/{reg_id}", headers=admin.headers, json={"usn": f"{taken}X"}
    ).status_code == 200
    assert client.patch(
        f"/api/register/{reg_id}", headers=admin.headers, json={"usn": taken.lower()}
    ).status_code == 409, "a correction is refused against a lowercase holder too"


@requires_db
def test_approving_a_lowercase_application_seats_the_upper_case_usn(client, make_user, applicant):
    admin = make_user("usnfix-case-seat", Role.ADMIN)
    usn = _new_usn()
    email = _email()
    reg_id = applicant(email, usn=usn.lower())

    check = _usn_check(client, admin, reg_id)
    assert check["status"] == CHECK_OK
    assert usn in check["label"]
    decision = client.post(
        f"/api/register/{reg_id}/decision", headers=admin.headers, json={"decision": "APPROVE"}
    )
    assert decision.status_code == 200, decision.text
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert db.scalar(select(Student).where(Student.user_id == user.id)).usn == usn


@requires_db
def test_the_students_editor_refuses_a_usn_held_in_another_case(client, make_user):
    admin = make_user("usnfix-case-edit", Role.ADMIN)
    holder = make_user("usnfix-case-edit-holder", Role.STUDENT)
    other = make_user("usnfix-case-edit-other", Role.STUDENT)
    taken = _new_usn()
    _give_usn(holder.user_id, taken.lower())
    other_student_id = _give_usn(other.user_id, _new_usn())

    r = client.patch(
        f"/api/admin/students/{other_student_id}", headers=admin.headers, json={"usn": taken}
    )
    assert r.status_code == 409, r.text
