"""B10.1/B10.2/B10.4/B10.7/B10.8 — who may sign a leave request, what the
submit path refuses, how an applicant withdraws, and what a non-approver sees.

The five things this module holds down, and what comes back if it is deleted:

1. ONE SIGNATURE, THE OFFICE'S (2026-09-16). The two-signature chain — a
   MENTOR or a scoped grantee first, a different approver second — deadlocked
   every staff request on a one-admin deployment at FIRST_APPROVED, and the
   owner's answer was a single decision by the Main Admin. So: the office's
   APPROVE is a sanction, a mentoring colleague is refused the queue and the
   signature outright, and a row signed once under the old chain is completed
   by the office's one decision. Delete these and "a faculty member with a
   mentee may sign leave" comes back through whichever gate is loosened first.
   AND THE OFFICE MAY DELEGATE IT (2026-09-17): a faculty member granted
   `admin.leave_approvals` in Governance sees the office's queue and signs
   the one signature as DELEGATE — the grant, never a mentee, is what admits
   them.
2. THE RETIRED KEY ADMITS NOBODY. `mentor.leave_approve` left the catalogue
   with the door; a grant row still naming it (migration `d8b1f4c2a7e9`
   revokes them, but a row can be written by hand) must open nothing — and
   its successor is a different key so that such a row cannot come back to
   life under a rule it was never made under.
3. THE SUBMIT CHECKS ARE SILENT UNTIL THE OFFICE RECORDS AN ALLOWANCE. Delete
   `test_the_submit_checks_sleep_until_a_balance_exists` and a table nobody has
   filled in starts refusing requests the form accepted yesterday — on the
   college's own paper form, which the owner asked not to change.
4. CANCELLED IS REACHABLE ONLY BY ASKING. Neither queue returned a withdrawn
   request before B10.4; widening `/history`'s default instead would have put
   them into the Approved and Rejected tabs of a console already built against
   it.
5. THE REDUCED PROJECTION IS A SHAPE, NOT A CONVENTION. `LeaveOut.reason` is
   free text and routinely medical. `LeaveBrief` cannot carry one because it has
   no field for one, and `test_the_reduced_projection_cannot_carry_a_reason`
   is what stops the next reader adding it "just for the alternate's screen".
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import delete, select, update

from conftest import TEST_PASSWORD, requires_db

from app.db import SessionLocal
from app.leave_policy import academic_year_for
from app.models.cohort import Cohort
from app.models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from app.models.institution import STATUS_ACTIVE, College, Department
from app.models.job import DegreeLevel
from app.models.leave import SIGNED_AS, LeaveRequest, LeaveStatus
from app.models.leave_policy import AcademicCalendarDay, LeaveBalance
from app.models.user import Mentor, Role, Student, User
from app.seed_roster import SSO_ONLY_PASSWORD_HASH

LEAVES = "/api/leaves"
#: The RETIRED key. Kept as a string so the test that writes a stray grant of
#: it can prove it opens nothing.
RETIRED_LEAVE_KEY = "mentor.leave_approve"


# ----------------------------------------------------------------- fixtures --


@pytest.fixture
def chain(make_user):
    """One college, two departments, a batch and a student in each.

    DEPENDS ON `make_user` so that pytest sets THAT fixture up first and tears
    it down LAST. The order is load-bearing rather than tidy: a `make_user`
    faculty account filed under one of these departments, or a `Mentor` group
    one of these students points at, blocks the delete either way round — and
    the failure surfaces as an IntegrityError inside somebody else's teardown.
    """
    tag = uuid.uuid4().hex[:6]
    made: dict[str, str] = {"tag": tag}
    with SessionLocal() as db:
        college = College(code=f"LC{tag.upper()}", name="Leave Chain College", status=STATUS_ACTIVE)
        db.add(college)
        db.flush()
        here = Department(college_id=college.id, name="Chain Here", code=f"CH{tag}")
        there = Department(college_id=college.id, name="Chain There", code=f"CT{tag}")
        db.add_all([here, there])
        db.flush()

        def batch(code: str, department_id: str) -> Cohort:
            return Cohort(
                code=f"{code}-{tag}", name=f"Batch {code}", batch_label="2026-28",
                degree_level=DegreeLevel.PG, department_id=department_id,
                start_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
                end_date=datetime(2028, 7, 31, tzinfo=timezone.utc),
            )

        b_here, b_there = batch("LH", here.id), batch("LT", there.id)
        db.add_all([b_here, b_there])
        db.flush()

        def student(label: str, cohort: Cohort) -> tuple[User, Student]:
            u = User(
                email=f"{label}-{tag}@chain.test", name=f"{label.title()} {tag}",
                role=Role.STUDENT, password_hash=SSO_ONLY_PASSWORD_HASH,
            )
            db.add(u)
            db.flush()
            s = Student(user_id=u.id, usn=f"{label[:4].upper()}{tag}", cohort_id=cohort.id)
            db.add(s)
            return u, s

        u_here, s_here = student("chere", b_here)
        u_there, s_there = student("cthere", b_there)
        db.flush()

        def leave(user: User) -> LeaveRequest:
            return LeaveRequest(
                requester_user_id=user.id,
                from_date=date(2026, 11, 2), to_date=date(2026, 11, 3),
                reason=f"leave-chain fixture {tag}", status=LeaveStatus.SUBMITTED,
                signed_at=datetime.now(timezone.utc),
            )

        leaves = [leave(u_here), leave(u_there)]
        db.add_all(leaves)
        db.commit()
        made |= {
            "college": college.id, "here": here.id, "there": there.id,
            "batch_here": b_here.id, "batch_there": b_there.id,
            "student_here": s_here.id, "student_there": s_there.id,
            "user_here": u_here.id, "user_there": u_there.id,
            "leave_here": leaves[0].id, "leave_there": leaves[1].id,
        }

    yield made

    with SessionLocal() as db:
        departments = [made["here"], made["there"]]
        users = [made["user_here"], made["user_there"]]
        students = [made["student_here"], made["student_there"]]
        # Unseat and unfile before deleting: `students.mentor_id` and
        # `users.department_id` carry no ON DELETE, so anything still pointing
        # here takes this teardown down with it.
        db.execute(update(Student).where(Student.id.in_(students)).values(mentor_id=None))
        db.execute(
            update(User).where(User.department_id.in_(departments)).values(department_id=None)
        )
        db.execute(
            update(Student)
            .where(Student.department_id.in_(departments))
            .values(department_id=None)
        )
        db.flush()
        db.execute(delete(LeaveBalance).where(LeaveBalance.user_id.in_(users)))
        db.execute(delete(LeaveRequest).where(LeaveRequest.requester_user_id.in_(users)))
        db.execute(
            delete(AcademicCalendarDay).where(
                AcademicCalendarDay.college_id == made["college"]
            )
        )
        db.execute(delete(Student).where(Student.id.in_(students)))
        db.execute(delete(User).where(User.id.in_(users)))
        db.execute(
            delete(Cohort).where(Cohort.id.in_([made["batch_here"], made["batch_there"]]))
        )
        db.execute(delete(Department).where(Department.id.in_(departments)))
        db.execute(delete(College).where(College.id == made["college"]))
        db.commit()


@pytest.fixture
def scoped_grant():
    """One capability, scoped to one rung, written as a ROW.

    Not through `POST /api/admin/governance/grants`, for `test_scoped_lists.py`'s
    reason: a deputy's grant of a `carries_pii` key lands `pending_approval`
    under B2.4 and holds nothing until a different `admin.governance` holder
    approves it, while the Main Admin's is live at once — a fixture that
    depended on who granted would make these tests about the approval rule.
    """
    made: list[str] = []

    def _grant(user_id: str, key: str, level: ScopeLevel | None, target_id: str | None) -> str:
        with SessionLocal() as db:
            row = CapabilityGrant(
                capability=key, subject_kind=SubjectKind.USER, subject_user_id=user_id,
                scope_level=level, scope_id=target_id,
                reason="the leave-chain tests need a scoped grant, twenty characters plus",
            )
            db.add(row)
            db.commit()
            made.append(row.id)
            return row.id

    yield _grant

    with SessionLocal() as db:
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.id.in_(made)))
        db.commit()


def _file_under(user_id: str, department_id: str | None) -> None:
    """Put a staff account in a department — where `ancestry_of_user` reads it."""
    with SessionLocal() as db:
        db.get(User, user_id).department_id = department_id
        db.commit()


def _seat(student_id: str, faculty_user_id: str) -> str:
    """Give a faculty account a mentee, which is what derives the three
    `mentor.*` FUNCTIONS (app/mentor_functions.py). None of them is leave
    approval any more. Returns the group id."""
    with SessionLocal() as db:
        group = Mentor(user_id=faculty_user_id)
        db.add(group)
        db.flush()
        db.get(Student, student_id).mentor_id = group.id
        db.commit()
        return group.id


def _unseat(student_id: str, group_id: str) -> None:
    with SessionLocal() as db:
        student = db.get(Student, student_id)
        if student is not None:
            student.mentor_id = None
        db.flush()
        db.execute(delete(Mentor).where(Mentor.id == group_id))
        db.commit()


def _submit(client, headers, **extra) -> dict:
    body = {
        "from_date": "2026-11-10",
        "to_date": "2026-11-11",
        "reason": "Family function at home.",
        "leave_kind": "CASUAL",
    }
    body.update(extra)
    r = client.post(LEAVES, headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _status(leave_id: str) -> str:
    with SessionLocal() as db:
        return db.get(LeaveRequest, leave_id).status.value


def _drop(leave_id: str) -> None:
    with SessionLocal() as db:
        db.execute(delete(LeaveRequest).where(LeaveRequest.id == leave_id))
        db.commit()


# ------------------------------------------------------------ B10.1 the chain --


@requires_db
def test_the_office_sanctions_with_one_signature_and_nobody_else_can(
    client, make_user, login, chain
):
    """THE RULE, in one run: the Main Admin's APPROVE is the sanction, and a
    mentoring colleague — the account the old chain admitted first — is refused
    the queue and the signature with the same answer an invented id gets."""
    applicant = make_user(f"lc-app-{chain['tag']}", Role.MENTOR)
    colleague = make_user(f"lc-col-{chain['tag']}", Role.MENTOR)
    office = make_user(f"lc-adm-{chain['tag']}", Role.ADMIN)
    _file_under(applicant.user_id, chain["here"])
    _file_under(colleague.user_id, chain["here"])
    group = _seat(chain["student_here"], colleague.user_id)
    leave_id = None
    try:
        colleague_h = login(colleague.email, TEST_PASSWORD)  # mentorId is minted at login
        leave_id = _submit(client, applicant.headers)["id"]
        approve = {"decision": "APPROVE", "note": "Sanctioned."}

        # The colleague mentors somebody and is still refused everything.
        assert client.get(f"{LEAVES}/pending", headers=colleague_h).status_code == 403
        refused = client.post(f"{LEAVES}/{leave_id}/decision", headers=colleague_h, json=approve)
        invented = client.post(f"{LEAVES}/no-such-leave/decision", headers=colleague_h, json=approve)
        assert refused.status_code == 403, refused.text
        assert (invented.status_code, invented.json()) == (refused.status_code, refused.json())
        # The student's own request in the colleague's group: the same.
        assert client.post(
            f"{LEAVES}/{chain['leave_here']}/decision", headers=colleague_h, json=approve
        ).status_code == 403

        # The office sees it and signs ONCE, and once is the sanction.
        pending = client.get(f"{LEAVES}/pending", headers=office.headers)
        assert pending.status_code == 200 and pending.headers["X-Reep-Scope"] == "programme"
        assert leave_id in {row["id"] for row in pending.json()}
        done = client.post(f"{LEAVES}/{leave_id}/decision", headers=office.headers, json=approve)
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "APPROVED"
        assert done.json()["first_signed_as"] == "MAIN_ADMIN"
        assert done.json()["second_signed_as"] is None
        assert done.json()["director_name"] and done.json()["director_decided_at"]
        assert done.json()["director_note"] == "Sanctioned."
        assert _status(leave_id) == "APPROVED"

        # Decided is decided.
        again = client.post(f"{LEAVES}/{leave_id}/decision", headers=office.headers, json=approve)
        assert again.status_code == 409 and "no decision possible" in again.text
        assert leave_id not in {row["id"] for row in client.get(f"{LEAVES}/pending", headers=office.headers).json()}
    finally:
        if leave_id:
            _drop(leave_id)
        _unseat(chain["student_here"], group)


@requires_db
def test_a_faculty_member_granted_the_key_signs_as_the_office_s_delegate(
    client, make_user, login, chain, scoped_grant
):
    """`admin.leave_approvals` is what admits a colleague (2026-09-17): with
    the grant they see the office's whole queue, sign the ONE signature as
    DELEGATE, read the paper, and are still refused their own request; without
    it (the test above) they are refused everything. The grant is programme-
    wide because a leave request hangs on no rung this router narrows by."""
    applicant = make_user(f"lc-dapp-{chain['tag']}", Role.MENTOR)
    delegate = make_user(f"lc-dele-{chain['tag']}", Role.MENTOR)
    _file_under(applicant.user_id, chain["here"])
    _file_under(delegate.user_id, chain["there"])
    scoped_grant(delegate.user_id, "admin.leave_approvals", None, None)
    leave_id = None
    own_id = None
    try:
        delegate_h = login(delegate.email, TEST_PASSWORD)
        leave_id = _submit(client, applicant.headers)["id"]
        # `delegate_h`, not `delegate.headers`: the login above retired the
        # make_user cookie (one device at a time), as every sign-in does.
        own_id = _submit(client, delegate_h, from_date="2026-12-01", to_date="2026-12-02")["id"]
        approve = {"decision": "APPROVE", "note": "Sanctioned by the delegate."}

        pending = client.get(f"{LEAVES}/pending", headers=delegate_h)
        assert pending.status_code == 200, pending.text
        assert pending.headers["X-Reep-Scope"] == "programme"
        ids = {row["id"] for row in pending.json()}
        assert leave_id in ids, "the delegate sees the office's queue"
        assert own_id not in ids, "nobody sees their own request in the queue"
        # The student's request in the OTHER department is there too: the
        # reach is the programme, not the department the delegate is filed in.
        assert chain["leave_here"] in ids

        # Their own request: refused with the same sentence the office gets.
        own = client.post(f"{LEAVES}/{own_id}/decision", headers=delegate_h, json=approve)
        assert own.status_code == 400 and "own leave" in own.text

        done = client.post(f"{LEAVES}/{leave_id}/decision", headers=delegate_h, json=approve)
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "APPROVED"
        assert done.json()["first_signed_as"] == "DELEGATE"
        assert done.json()["second_signed_as"] is None
        assert done.json()["director_note"] == "Sanctioned by the delegate."
        assert _status(leave_id) == "APPROVED"
        # The paper is the decider's to read, and it prints the function.
        paper = client.get(f"{LEAVES}/{leave_id}/paper.pdf", headers=delegate_h)
        assert paper.status_code == 200, paper.text
        assert paper.headers["content-type"].startswith("application/pdf")
    finally:
        for lid in (leave_id, own_id):
            if lid:
                _drop(lid)


@requires_db
def test_the_office_cannot_sanction_its_own_request(client, make_user, chain):
    office = make_user(f"lc-own-{chain['tag']}", Role.ADMIN)
    leave_id = _submit(client, office.headers)["id"]
    try:
        r = client.post(f"{LEAVES}/{leave_id}/decision", headers=office.headers, json={"decision": "APPROVE"})
        assert r.status_code == 400 and "own leave" in r.text
        assert leave_id not in {row["id"] for row in client.get(f"{LEAVES}/pending", headers=office.headers).json()}
    finally:
        _drop(leave_id)


@requires_db
def test_a_row_signed_once_under_the_old_chain_is_completed_by_the_office(
    client, make_user, chain
):
    """A FIRST_APPROVED row from before 2026-09-16 is still in the queue, and
    the office's one decision finishes it — written into the second slot so
    the first signer's stamp is not rewritten, whoever that signer was."""
    applicant = make_user(f"lc-old-{chain['tag']}", Role.MENTOR)
    signer = make_user(f"lc-oldsig-{chain['tag']}", Role.MENTOR)
    office = make_user(f"lc-oldadm-{chain['tag']}", Role.ADMIN)
    leave_id = _submit(client, applicant.headers)["id"]
    try:
        with SessionLocal() as db:
            lr = db.get(LeaveRequest, leave_id)
            lr.status = LeaveStatus.FIRST_APPROVED
            lr.first_approver_user_id = signer.user_id
            lr.first_decided_at = datetime.now(timezone.utc)
            lr.first_signed_as = "MENTOR"
            db.commit()
        assert leave_id in {row["id"] for row in client.get(f"{LEAVES}/pending", headers=office.headers).json()}
        done = client.post(f"{LEAVES}/{leave_id}/decision", headers=office.headers, json={"decision": "APPROVE"})
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "APPROVED"
        assert done.json()["first_signed_as"] == "MENTOR", "the first stamp is not rewritten"
        assert done.json()["second_signed_as"] == "MAIN_ADMIN"
    finally:
        _drop(leave_id)


@requires_db
def test_a_stray_grant_of_the_retired_key_admits_nobody(
    client, make_user, login, chain, scoped_grant
):
    """`mentor.leave_approve` is gone from the catalogue. A grant row naming it
    — the migration revokes them, but a row can be written by hand — opens
    neither the queue nor the signature, and the function set a mentee
    derives no longer carries it."""
    from app.mentor_functions import MENTOR_FUNCTIONS

    assert RETIRED_LEAVE_KEY not in MENTOR_FUNCTIONS
    approver = make_user(f"lc-appr-{chain['tag']}", Role.MENTOR)
    _file_under(approver.user_id, chain["here"])
    scoped_grant(approver.user_id, RETIRED_LEAVE_KEY, ScopeLevel.DEPARTMENT, chain["here"])
    approve = {"decision": "APPROVE", "note": None}
    assert client.get(f"{LEAVES}/pending", headers=approver.headers).status_code == 403
    r = client.post(f"{LEAVES}/{chain['leave_here']}/decision", headers=approver.headers, json=approve)
    assert r.status_code == 403, r.text
    assert _status(chain["leave_here"]) == "SUBMITTED"


# ---------------------------------------------------------- B10.4 the withdraw --


@requires_db
def test_the_applicant_withdraws_their_own_request_and_nobody_else_can(
    client, make_user, chain
):
    """Cancel is the APPLICANT's, it is not an approver's act, and it is not a
    way to rewrite a decision.

    REJECTED is terminal here for the same reason APPROVED is: somebody signed
    it. An applicant who could cancel a rejection would be editing a record
    made about them; the rejection stays on the paper and a fresh request is the
    honest way forward.
    """
    applicant = make_user(f"lc-can-{chain['tag']}", Role.MENTOR)
    stranger = make_user(f"lc-str-{chain['tag']}", Role.MENTOR)
    office = make_user(f"lc-canadm-{chain['tag']}", Role.ADMIN)
    ids: list[str] = []
    try:
        plain = _submit(client, applicant.headers)["id"]
        ids.append(plain)

        # A stranger's refusal is the same 404 as an id that does not exist:
        # this endpoint is open to every signed-in account, so a 403 here would
        # be a membership oracle anybody could query.
        mine = client.post(f"{LEAVES}/{plain}/cancel", headers=stranger.headers)
        nothing = client.post(f"{LEAVES}/no-such-leave/cancel", headers=stranger.headers)
        assert mine.status_code == 404, mine.text
        assert (nothing.status_code, nothing.json()) == (mine.status_code, mine.json())

        r = client.post(f"{LEAVES}/{plain}/cancel", headers=applicant.headers)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "CANCELLED"
        with SessionLocal() as db:
            assert db.get(LeaveRequest, plain).cancelled_at is not None

        # Twice is a 409, not a second cancellation.
        assert client.post(f"{LEAVES}/{plain}/cancel", headers=applicant.headers).status_code == 409

        # A SANCTIONED request is terminal: the office's one signature is the
        # decision, and a day already granted is not taken back by the applicant.
        half = _submit(client, applicant.headers, from_date="2026-11-20", to_date="2026-11-21")["id"]
        ids.append(half)
        assert client.post(
            f"{LEAVES}/{half}/decision", headers=office.headers, json={"decision": "APPROVE"}
        ).status_code == 200
        assert _status(half) == "APPROVED"
        assert client.post(f"{LEAVES}/{half}/cancel", headers=applicant.headers).status_code == 409

        # A REJECTED request is terminal.
        done = _submit(client, applicant.headers, from_date="2026-11-25", to_date="2026-11-26")["id"]
        ids.append(done)
        assert client.post(
            f"{LEAVES}/{done}/decision", headers=office.headers, json={"decision": "REJECT"}
        ).status_code == 200
        refused = client.post(f"{LEAVES}/{done}/cancel", headers=applicant.headers)
        assert refused.status_code == 409, refused.text
        assert "REJECTED" in refused.text
    finally:
        for leave_id in ids:
            _drop(leave_id)


@requires_db
def test_a_cancelled_request_is_reachable_only_by_asking_for_it(client, make_user, chain):
    """B10.4's other half: the console's "Cancelled" tab could not be lit by
    filtering on the client, because NEITHER queue returned a cancelled row.

    The default is unchanged and is asserted as such — widening it would have
    dropped withdrawn requests into the Approved and Rejected tabs of every
    screen already built against this endpoint.
    """
    applicant = make_user(f"lc-hist-{chain['tag']}", Role.MENTOR)
    office = make_user(f"lc-histadm-{chain['tag']}", Role.ADMIN)
    ids: list[str] = []
    try:
        gone = _submit(client, applicant.headers)["id"]
        ids.append(gone)
        assert client.post(f"{LEAVES}/{gone}/cancel", headers=applicant.headers).status_code == 200

        kept = _submit(client, applicant.headers, from_date="2026-11-20", to_date="2026-11-21")["id"]
        ids.append(kept)
        assert client.post(
            f"{LEAVES}/{kept}/decision", headers=office.headers, json={"decision": "APPROVE"}
        ).status_code == 200
        assert client.post(
            f"{LEAVES}/{kept}/decision", headers=office.headers, json={"decision": "APPROVE"}
        ).status_code == 409, "a decided request takes no second decision"

        def ids_at(path: str) -> set[str]:
            r = client.get(path, headers=office.headers)
            assert r.status_code == 200, r.text
            return {row["id"] for row in r.json()}

        assert gone not in ids_at(f"{LEAVES}/pending")
        assert gone not in ids_at(f"{LEAVES}/history"), "the default queue grew a cancelled row"
        assert gone in ids_at(f"{LEAVES}/history?status=CANCELLED")
        assert gone not in ids_at(f"{LEAVES}/history?status=REJECTED")
        assert kept not in ids_at(f"{LEAVES}/history?status=CANCELLED")

        bad = client.get(f"{LEAVES}/history?status=SUBMITTED", headers=office.headers)
        assert bad.status_code == 422, bad.text
        assert "pending queue" in bad.text
    finally:
        for leave_id in ids:
            _drop(leave_id)


# ----------------------------------------------------- B10.2 the submit checks --


def _balance(user_id: str, kind: str, entitled: int, consumed: int = 0) -> None:
    with SessionLocal() as db:
        db.add(
            LeaveBalance(
                user_id=user_id, kind=kind, academic_year=academic_year_for(date(2026, 11, 10)),
                entitled_days=entitled, consumed_days=consumed,
            )
        )
        db.commit()


@requires_db
def test_the_submit_checks_sleep_until_a_balance_exists(client, make_user, chain):
    """A table nobody has filled in must not change what the form accepts.

    `interview_policies`' rule applied to leave: no row is seeded, and the
    absence of one IS the default. `tests/test_leave_dates.py` submits
    `today..today` and then `today..today+3` for one account and asserts both
    are 201 — a deliberately overlapping pair — so an overlap check that fired
    without an allowance would take that module with it, and would refuse
    requests the office has been signing by hand for a year.
    """
    applicant = make_user(f"lc-sleep-{chain['tag']}", Role.MENTOR)
    ids: list[str] = []
    try:
        ids.append(_submit(client, applicant.headers)["id"])
        # Exactly the same days again: accepted, because nothing has been recorded.
        ids.append(_submit(client, applicant.headers)["id"])

        _balance(applicant.user_id, "CASUAL", entitled=10)
        clash = client.post(
            LEAVES,
            headers=applicant.headers,
            json={
                "from_date": "2026-11-11", "to_date": "2026-11-12",
                "reason": "Family function at home.", "leave_kind": "CASUAL",
            },
        )
        assert clash.status_code == 422, clash.text
        assert "overlaps" in clash.text and "2026-11-10" in clash.text

        # A kind the office has recorded nothing for is still not checked.
        ids.append(_submit(client, applicant.headers, leave_kind="OOD")["id"])
    finally:
        for leave_id in ids:
            _drop(leave_id)


@requires_db
def test_a_request_past_the_balance_is_refused_and_the_refusal_names_lop(
    client, make_user, chain
):
    """B10.2's LOP "fallback" is a REFUSAL, never a silent rewrite.

    Rewriting `leave_kind` to LOP would change which of the five printed options
    is STRUCK THROUGH on the college's own form: the applicant asks for Casual
    Leave and collects a PDF saying Loss Of Pay, having agreed to nothing.
    Nobody should find out their leave became unpaid by reading a PDF.
    """
    applicant = make_user(f"lc-bal-{chain['tag']}", Role.MENTOR)
    ids: list[str] = []
    try:
        _balance(applicant.user_id, "CASUAL", entitled=2, consumed=1)
        over = client.post(
            LEAVES,
            headers=applicant.headers,
            json={
                "from_date": "2026-11-10", "to_date": "2026-11-12",
                "reason": "Family function at home.", "leave_kind": "CASUAL",
            },
        )
        assert over.status_code == 422, over.text
        assert "3 working day(s)" in over.text
        assert "1 of 2 day(s) left" in over.text
        assert "LOP (loss of pay)" in over.text

        # And the kind is untouched: nothing was stored at all.
        with SessionLocal() as db:
            assert db.scalar(
                select(LeaveRequest.id).where(
                    LeaveRequest.requester_user_id == applicant.user_id
                )
            ) is None

        # The one day that does fit is accepted, kind intact.
        inside = _submit(client, applicant.headers, to_date="2026-11-10")
        ids.append(inside["id"])
        assert inside["leave_kind"] == "CASUAL"
    finally:
        for leave_id in ids:
            _drop(leave_id)


@requires_db
def test_a_lop_refusal_does_not_advise_applying_for_lop(client, make_user, chain):
    """A hint that tells somebody applying for LOP to apply for LOP is the kind
    of message that makes a product look like it is not reading the request."""
    applicant = make_user(f"lc-lop-{chain['tag']}", Role.MENTOR)
    _balance(applicant.user_id, "LOP", entitled=1)
    r = client.post(
        LEAVES,
        headers=applicant.headers,
        json={
            "from_date": "2026-11-10", "to_date": "2026-11-12",
            "reason": "Unpaid.", "leave_kind": "LOP",
        },
    )
    assert r.status_code == 422, r.text
    assert "LOP (loss of pay)" not in r.text
    assert "adjust your balance" in r.text


@requires_db
def test_the_calendar_stops_a_holiday_eating_a_day_of_the_balance(client, make_user, chain):
    """The college's own closed days are not leave.

    A day counts unless `academic_calendar` carries a `holiday` row for it at
    the applicant's college — no weekday rule is invented here, because nothing
    in this product writes down which days the college works and the Angular
    form computes its own day count from the two dates. See
    `app/leave_policy.py` on why a silent Sunday subtraction would put the
    server's number and the applicant's number quietly out of step.
    """
    applicant = make_user(f"lc-cal-{chain['tag']}", Role.MENTOR)
    _file_under(applicant.user_id, chain["here"])
    _balance(applicant.user_id, "CASUAL", entitled=1)
    ids: list[str] = []
    try:
        body = {
            "from_date": "2026-11-10", "to_date": "2026-11-12",
            "reason": "Family function at home.", "leave_kind": "CASUAL",
        }
        assert client.post(LEAVES, headers=applicant.headers, json=body).status_code == 422

        with SessionLocal() as db:
            db.add_all([
                AcademicCalendarDay(
                    college_id=chain["college"], day=date(2026, 11, 11),
                    kind="holiday", label="Chain holiday",
                ),
                AcademicCalendarDay(
                    college_id=chain["college"], day=date(2026, 11, 12),
                    kind="holiday", label="Chain holiday II",
                ),
            ])
            db.commit()

        ok = client.post(LEAVES, headers=applicant.headers, json=body)
        assert ok.status_code == 201, ok.text
        ids.append(ok.json()["id"])
    finally:
        for leave_id in ids:
            _drop(leave_id)


def test_the_academic_year_label_is_the_conventional_spelling() -> None:
    """It exists to FIND a row the office typed, and the balance screen should
    pre-fill the same function's output so the two agree by construction. A miss
    switches the check off — see `app/leave_policy.py` on why that is the safe
    failure and consulting "whichever row this person has" is not."""
    assert academic_year_for(date(2026, 9, 13)) == "2026-27"
    assert academic_year_for(date(2026, 6, 1)) == "2026-27"
    assert academic_year_for(date(2026, 5, 31)) == "2025-26"
    assert academic_year_for(date(2027, 1, 4)) == "2026-27"


# ------------------------------------------------ B10.7 the reduced projection --


def test_the_reduced_projection_cannot_carry_a_reason() -> None:
    """`LeaveOut.reason` is the form's Purpose cell: free text, routinely
    medical. B10.6's alternate is a colleague who is neither the applicant nor
    an approver, and handing them `_leave_out` hands them a diagnosis.

    Asserted on the SHAPE rather than on a call site, because "the alternate
    endpoint happens to use the other function" is a property one careless
    import undoes. Pinned to an exact field set so a field ADDED to `LeaveBrief`
    has to be added here too, on purpose, by somebody reading this docstring.
    """
    from app.routers.leave import BRIEF_FIELDS, LeaveBrief, LeaveOut

    assert tuple(LeaveBrief.model_fields) == BRIEF_FIELDS
    forbidden = {"reason", "credit", "alt_name", "alt_rows", "director_note",
                 "first_note", "second_note", "requester_designation", "requester_department"}
    assert not (set(LeaveBrief.model_fields) & forbidden), (
        "the reduced projection grew a field a third party has no business reading"
    )
    # And the full one still carries everything it always did — this is a split,
    # not a trim: `/mine`, the two queues and the paper all read it.
    assert forbidden - set(LeaveOut.model_fields) == {"first_note", "second_note"}


@requires_db
def test_the_brief_of_a_real_request_holds_no_free_text(client, make_user, chain):
    """The shape check above, exercised against a row rather than a class."""
    from app.routers.leave import AltRow, _leave_brief

    applicant = make_user(f"lc-brief-{chain['tag']}", Role.MENTOR)
    leave_id = _submit(client, applicant.headers, reason="Post-operative review.")["id"]
    try:
        with SessionLocal() as db:
            brief = _leave_brief(
                db.get(LeaveRequest, leave_id), db, alt_row=AltRow(staff_name="Kavya N")
            )
        dumped = brief.model_dump()
        assert "Post-operative" not in str(dumped)
        assert dumped["status"] == "SUBMITTED"
        assert dumped["requester_name"]
        assert dumped["alt_row"]["staff_name"] == "Kavya N"
    finally:
        _drop(leave_id)


# ------------------------------------------------------ B10.8 the attestation --


def test_every_signing_function_has_a_printed_label() -> None:
    """A row stamped with a function the paper has no word for prints a raw
    database token at somebody's signature on the college's own form."""
    from app.leave_paper import SIGNED_AS_LABELS

    assert set(SIGNED_AS_LABELS) == set(SIGNED_AS)


def test_the_paper_prints_the_function_beside_the_signature_and_nothing_when_there_is_none() -> None:
    """B10.8, and the two things it is NOT.

    The words "PROGRAM DIRECTOR" are printed on the college's own PDF and are
    still there: the function goes on the muted attestation line beneath, which
    is overlay text in blank margin. And a request decided before these columns
    existed — NULL on both — prints exactly what it printed before, never a
    function guessed from who the signer happens to be today.
    """
    import io
    from types import SimpleNamespace

    from pypdf import PdfReader

    from app import leave_paper

    def paper(**extra) -> str:
        leave = SimpleNamespace(
            requester_name="Asha Rao", requester_designation="Assistant Professor",
            requester_department="MBA", from_date=date(2026, 9, 15), to_date=date(2026, 9, 16),
            reason="Family function.", status="APPROVED", leave_kind="CASUAL", credit="2",
            alt_name="Kavya N", alt_rows=[],
            signed_at=datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc),
            director_name="Ravi Kumar",
            director_decided_at=datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc),
            director_note=None,
        )
        for key, value in extra.items():
            setattr(leave, key, value)
        pdf = leave_paper.render_leave_paper_pdf(leave)
        return PdfReader(io.BytesIO(pdf)).pages[0].extract_text()

    # A historical row: no columns at all on the object, no function printed.
    before = paper()
    assert "PROGRAM" in before, "the form's own label is not ours to rename"
    assert "Ravi Kumar" in before
    for word in ("Mentor", "Delegated approver", "Main Admin"):
        assert word not in before, f"a function was invented for an unstamped row: {word}"

    # The second signature carries the function, and it is the one printed —
    # second-then-first, as `director_name` itself is chosen.
    stamped = paper(first_signed_as="MENTOR", second_signed_as="MAIN_ADMIN")
    assert "Main Admin" in stamped
    assert "PROGRAM" in stamped

    # Rejected at the first step: only one signature exists and it is that one's
    # function that belongs against the name.
    first_only = paper(first_signed_as="DELEGATE", second_signed_as=None)
    assert "Delegated approver" in first_only
