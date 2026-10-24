"""B11.1 — the review queue's `checks[]`, and the guards they speak for.

THE CONTRACT THIS MODULE PINS, in one sentence: a check whose status is
"blocked" is a guard in `_provision_student` that will refuse this exact
application, and there is no guard without a check in front of it. That is the
only property that makes the panel worth drawing — a reviewer who is shown a
clean checklist and then met with a 422 learns not to read the checklist, and a
reviewer shown a blocker that Approve would have sailed through learns to
reject applications the queue handles correctly.

So every "blocked" assertion below is paired with the status code the decision
endpoint actually answers, in the same test, on the same row. Split them and
the pair can drift without either half going red.
"""

import uuid

import pytest
from sqlalchemy import delete, event, select

from app.db import SessionLocal, engine
from app.models.cohort import Cohort
from app.models.job import DegreeLevel
from app.models.registration import Registration, RegistrationStatus
from app.models.student_profile import StudentProfile
from app.models.user import Role, Student, User
from app.routers.registration import (
    CHECK_BLOCKED,
    CHECK_DOMAIN,
    CHECK_DUPLICATE_ACCOUNT,
    CHECK_OK,
    CHECK_RULE,
    CHECK_USN_UNIQUE,
    CHECK_WARN,
)

from conftest import application_files, requires_db


# --------------------------------------------------------------- fixtures --


@pytest.fixture
def applicant():
    """A pending Registration, cleaned up WHATEVER the test does.

    A local twin of `test_institutional_spine.py`'s fixture, and for its stated
    reason: a leaked Registration is not merely untidy, it MASKS the next run's
    regression, because a rerun finds the leftover row (and the user behind it)
    and reuses it instead of inserting. A yield fixture's finaliser runs on a
    failed assertion too, which is the whole point of writing it this way.
    """
    created: list[tuple[str, bool]] = []

    def _make(
        email: str,
        name: str = "Checks Applicant",
        *,
        usn: str | None = None,
        cohort_id: str | None = None,
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
                status=RegistrationStatus.PENDING_REVIEW,
                cohort_id=cohort_id,
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
            db.execute(delete(Registration).where(Registration.email == email))
            if user is not None and not pre_existing:
                db.execute(delete(User).where(User.id == user.id))
        db.commit()


def _checks_for(client, admin, registration_id: str) -> dict[str, dict]:
    """This application's checks off the real queue, keyed by `key`.

    Read through `GET /register/pending` rather than by calling the builder, so
    the wiring — the batching, `_out`, the response model — is on the path.
    """
    r = client.get("/api/register/pending", headers=admin.headers)
    assert r.status_code == 200, r.text
    for row in r.json():
        if row["id"] == registration_id:
            assert row["checks"] is not None, "a queued row must carry its checklist"
            return {check["key"]: check for check in row["checks"]}
    raise AssertionError(f"{registration_id} is not in the queue")


# ------------------------------------------- blocked means Approve refuses --


@requires_db
def test_an_off_domain_address_is_blocked_and_approve_refuses_it(client, make_user, applicant):
    """GUARD 1, read out before the press and enforced on it.

    The two halves are asserted TOGETHER on purpose. The check is computed by
    `domain_verdict`, which GUARD 1 itself calls; written as a second reading of
    the fence they would agree today and disagree the first time
    `provisionable_domains_for`'s fallback rule changes — and the disagreement
    surfaces as a GREEN CHECK on a row Approve then refuses with a 422, which is
    the worst possible way to find out.
    """
    admin = make_user("chk-dom", Role.ADMIN)
    reg_id = applicant("checks.offdomain@gmail.com")

    check = _checks_for(client, admin, reg_id)[CHECK_DOMAIN]
    assert check["status"] == CHECK_BLOCKED
    assert "gmail.com" in check["label"]

    decision = client.post(
        f"/api/register/{reg_id}/decision", headers=admin.headers, json={"decision": "APPROVE"}
    )
    assert decision.status_code == 422, decision.text
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.email == "checks.offdomain@gmail.com")) is None


@requires_db
def test_the_check_names_the_same_domains_the_refusal_does(client, make_user, applicant):
    """One writer, observably: the panel and the 422 read out one list.

    Not a restatement of the test above — that one pins the VERDICT, this one
    pins the list of domains carried with it. A second copy of the fence that
    happened to agree on yes/no while naming a different set would still send a
    reviewer to correct an address to a domain this college does not admit.
    """
    admin = make_user("chk-dom2", Role.ADMIN)
    reg_id = applicant("checks.samelist@gmail.com")

    detail = _checks_for(client, admin, reg_id)[CHECK_DOMAIN]["detail"]
    refusal = client.post(
        f"/api/register/{reg_id}/decision", headers=admin.headers, json={"decision": "APPROVE"}
    ).json()["detail"]

    from app.config import settings

    for domain in settings.provisionable_email_domains:
        assert domain in detail, f"the check does not name {domain}"
        assert domain in refusal, f"the refusal does not name {domain}"


@requires_db
def test_a_staff_address_is_blocked_and_approve_answers_409(client, make_user, applicant):
    """GUARD 2. Attaching a Student row to a MENTOR account mints a `studentId`
    into their session, so this is rule 2 being edited by a public form."""
    admin = make_user("chk-role", Role.ADMIN)
    victim = make_user("chk-victim", Role.MENTOR)
    reg_id = applicant(victim.email, "Impersonator")

    check = _checks_for(client, admin, reg_id)[CHECK_DUPLICATE_ACCOUNT]
    assert check["status"] == CHECK_BLOCKED
    assert "MENTOR" in check["label"]

    decision = client.post(
        f"/api/register/{reg_id}/decision", headers=admin.headers, json={"decision": "APPROVE"}
    )
    assert decision.status_code == 409, decision.text
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == victim.email))
        assert user.role is Role.MENTOR
        assert db.scalar(select(Student).where(Student.user_id == user.id)) is None


@requires_db
def test_a_usn_another_student_holds_is_blocked_and_approve_answers_409(
    client, make_user, applicant
):
    """GUARD 3 — the hole this check would otherwise be drawn over.

    `students.usn` is `unique=True` and `_provision_student` wrote it with no
    check, so the SECOND application carrying a USN raised IntegrityError on
    COMMIT: a 500, not the 409 the endpoint promises, landing after `decide` had
    already added its `record_change` row — so the rollback took the audit row
    with it and the trail did not even show that anybody tried. A check drawn
    over that hole is advisory over a 500, which is why the guard ships here.
    """
    admin = make_user("chk-usn", Role.ADMIN)
    incumbent = make_user("chk-usn-holder", Role.STUDENT)
    usn = f"1BG26CHK{uuid.uuid4().hex[:3].upper()}"
    with SessionLocal() as db:
        student = db.scalar(select(Student).where(Student.user_id == incumbent.user_id))
        student.usn = usn
        db.commit()

    reg_id = applicant(f"checks.usn-{uuid.uuid4().hex[:8]}@bgscet.ac.in", usn=usn)

    check = _checks_for(client, admin, reg_id)[CHECK_USN_UNIQUE]
    assert check["status"] == CHECK_BLOCKED
    assert usn in check["label"]

    decision = client.post(
        f"/api/register/{reg_id}/decision", headers=admin.headers, json={"decision": "APPROVE"}
    )
    assert decision.status_code == 409, decision.text
    assert usn in decision.json()["detail"]
    with SessionLocal() as db:
        assert (
            len(db.scalars(select(Student).where(Student.usn == usn)).all()) == 1
        ), "a refused approval must leave one holder of the USN, not two"
        assert (
            db.get(Registration, reg_id).status is RegistrationStatus.PENDING_REVIEW
        ), "a refused approval must leave the application decidable"


# ------------------------------------------------------- the middle verdict --


@requires_db
def test_an_existing_student_account_warns_and_approve_still_succeeds(
    client, make_user, applicant
):
    """THE VERDICT THAT MUST NOT BE COLLAPSED INTO "duplicate".

    An applicant who already holds a STUDENT account is the ORDINARY
    re-application path — it is also the state `python -m app.seed_roster`
    leaves behind for every enrolled student, so it is the common case rather
    than an edge one. Approve reuses that account. Rendering it as a blocker
    alongside the staff-address case would send reviewers to reject
    applications the queue handles correctly, and nothing would ever go red.
    """
    admin = make_user("chk-reapply", Role.ADMIN)
    email = f"checks.reapply-{uuid.uuid4().hex[:8]}@bgscet.ac.in"
    with SessionLocal() as db:
        user = User(email=email, name="Re Applicant", role=Role.STUDENT, password_hash="google-only")
        db.add(user)
        db.commit()
    reg_id = applicant(email, "Re Applicant")

    check = _checks_for(client, admin, reg_id)[CHECK_DUPLICATE_ACCOUNT]
    assert check["status"] == CHECK_WARN, "an existing STUDENT account is not a blocker"
    assert check["status"] != CHECK_BLOCKED

    decision = client.post(
        f"/api/register/{reg_id}/decision", headers=admin.headers, json={"decision": "APPROVE"}
    )
    assert decision.status_code == 200, decision.text
    with SessionLocal() as db:
        users = db.scalars(select(User).where(User.email == email)).all()
        assert len(users) == 1, "the warning path must reuse the account, not mint a second"


@requires_db
def test_a_usn_clash_is_a_remark_when_the_applicant_already_has_a_record(
    client, make_user, applicant
):
    """THE CHECK FOLLOWS THE GUARD'S CONTROL FLOW, NOT JUST ITS RULE.

    GUARD 3 runs only where a Student row is about to be INSERTED. An applicant
    who already has one keeps it — USN included — and the number typed on the
    application is never written, so a clash cannot refuse the approval. Reading
    the rule without the branch would report "Approve refuses this (409)" above a
    button that answers 200, which is the green-check defect in the other
    direction and just as bad: a reviewer learns the panel is guesswork.
    """
    admin = make_user("chk-usn-own", Role.ADMIN)
    incumbent = make_user("chk-usn-own-holder", Role.STUDENT)
    applicant_account = make_user("chk-usn-own-app", Role.STUDENT)
    usn = f"1BG26OWN{uuid.uuid4().hex[:3].upper()}"
    with SessionLocal() as db:
        db.scalar(select(Student).where(Student.user_id == incumbent.user_id)).usn = usn
        db.commit()

    reg_id = applicant(applicant_account.email, "Has A Record", usn=usn)

    check = _checks_for(client, admin, reg_id)[CHECK_USN_UNIQUE]
    assert check["status"] == CHECK_WARN, (
        "a clash on an applicant who already has a record cannot refuse the approval"
    )
    decision = client.post(
        f"/api/register/{reg_id}/decision", headers=admin.headers, json={"decision": "APPROVE"}
    )
    assert decision.status_code == 200, decision.text


@requires_db
def test_an_application_with_no_usn_warns_rather_than_blocks(client, make_user, applicant):
    """A missing USN is a fact worth seeing and not a refusal. Approving seats
    them without one; Students & batches is where it gets added."""
    admin = make_user("chk-nousn", Role.ADMIN)
    reg_id = applicant(f"checks.nousn-{uuid.uuid4().hex[:8]}@bgscet.ac.in", usn=None)

    check = _checks_for(client, admin, reg_id)[CHECK_USN_UNIQUE]
    assert check["status"] == CHECK_WARN
    decision = client.post(
        f"/api/register/{reg_id}/decision", headers=admin.headers, json={"decision": "APPROVE"}
    )
    assert decision.status_code == 200, decision.text


@requires_db
def test_a_clean_application_carries_no_blocker(client, make_user, applicant):
    """The green path, asserted as a WHOLE-LIST property rather than per check.

    Without this, every assertion above would still pass if `blocked` were
    returned for everything.
    """
    admin = make_user("chk-clean", Role.ADMIN)
    reg_id = applicant(
        f"checks.clean-{uuid.uuid4().hex[:8]}@bgscet.ac.in", usn=f"1BG26CLN{uuid.uuid4().hex[:3].upper()}"
    )
    checks = _checks_for(client, admin, reg_id)
    assert {c["status"] for c in checks.values()} <= {CHECK_OK, CHECK_WARN}
    assert checks[CHECK_DOMAIN]["status"] == CHECK_OK
    assert checks[CHECK_DUPLICATE_ACCOUNT]["status"] == CHECK_OK
    assert checks[CHECK_USN_UNIQUE]["status"] == CHECK_OK
    # No rule seeded against a random address: the engine's own verdict is the
    # one check that is always present, whatever happened.
    assert CHECK_RULE in checks


# ------------------------------------------------------- shape and cost ----


@requires_db
def test_the_applicant_never_sees_the_reviewers_checklist(client, tmp_document_store):
    """`_public_out_one` narrows by `model_fields`, so a field added to the staff
    model is private BY DEFAULT — the direction the mistake should fall in, and
    the reason the map says do not "fix" it. A check naming somebody else's
    account, or a domain fence, is the reviewer's side of the record.

    Asserted through a REAL public submission rather than by reading the model,
    because the narrowing happens in `_public_out_one` and a model assertion
    would stay green if that call were replaced with `_out_one`.
    """
    email = f"checks.public-{uuid.uuid4().hex[:8]}@bgscet.ac.in"
    try:
        r = client.post(
            "/api/register",
            data={
                "name": "Public Applicant",
                "email": email,
                "usn": f"1BG26PUB{uuid.uuid4().hex[:3].upper()}",
                "phone": "+91 90000 00000",
                "personal_email": f"personal.{uuid.uuid4().hex[:8]}@gmail.com",
                "linkedin_url": "https://www.linkedin.com/in/public-applicant",
                "degree_level": "PG",
            },
            files=application_files(),
        )
        assert r.status_code == 201, r.text
        assert "checks" not in r.json(), (
            "the applicant's own result card must never carry the reviewer's checklist"
        )
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
def test_a_decision_leaves_checks_null_rather_than_empty(client, make_user, applicant):
    """NULL MEANS NOT COMPUTED. An empty list would read as "nothing to report"
    — a clean bill of health for an application nobody checked — which is the
    same mistake `GET /auth/me` avoids by answering `None` for a field it was
    not asked about."""
    admin = make_user("chk-null", Role.ADMIN)
    reg_id = applicant(f"checks.null-{uuid.uuid4().hex[:8]}@bgscet.ac.in")
    r = client.post(
        f"/api/register/{reg_id}/decision",
        headers=admin.headers,
        json={"decision": "REJECT", "note": "Testing the null contract."},
    )
    assert r.status_code == 200, r.text
    assert r.json()["checks"] is None, "a single-row response must not answer []"

    reopened = client.post(f"/api/register/{reg_id}/reopen", headers=admin.headers)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["checks"] is None


@requires_db
def test_the_queue_costs_the_same_whether_it_holds_three_rows_or_six(
    client, make_user, applicant
):
    """THE N+1 THIS AREA'S MAP FLAGS AS "the failure here".

    `GET /register/pending` has no LIMIT, and the domain verdict alone is a
    three-table join plus a `db.get(College, ...)`. Per row that is two more
    queries per application on an unpaginated queue; batched, it is a constant.
    Counting statements rather than timing is what makes this test mean
    something on a fast laptop.
    """
    admin = make_user("chk-n1", Role.ADMIN)
    with SessionLocal() as db:
        cohort = db.scalar(select(Cohort))
        cohort_id = cohort.id if cohort is not None else None
    if cohort_id is None:
        pytest.skip("no cohort seeded: nothing to resolve a college through")

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    def _count() -> int:
        statements.clear()
        event.listen(engine, "before_cursor_execute", _record)
        try:
            assert client.get("/api/register/pending", headers=admin.headers).status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", _record)
        return len(statements)

    for n in range(3):
        applicant(f"checks.n1-a{n}-{uuid.uuid4().hex[:6]}@bgscet.ac.in", cohort_id=cohort_id)
    with_three = _count()
    for n in range(3):
        applicant(f"checks.n1-b{n}-{uuid.uuid4().hex[:6]}@bgscet.ac.in", cohort_id=cohort_id)
    with_six = _count()

    assert with_six == with_three, (
        f"the queue fired {with_six - with_three} extra statements for three extra "
        "applications — something in the checks builder is running per row"
    )
