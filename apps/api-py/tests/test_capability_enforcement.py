"""B2.1 — every capability in the catalogue is checked, or it is not there.

Fourteen of the twenty-nine keys `CAPABILITIES` declared were checked at zero
call sites. That is not a gap in coverage; it is a governance screen offering
switches connected to nothing. The office grants one, types a reason, the audit
records the act, `/auth/me` reports the key back — and the API refuses exactly
as much as it did before.

This module pins the three decisions B2.1 made, one test each:

  * the ten `student.*` keys were DELETED, because nothing checked them and
    nothing ever had (the student-facing half of those names lives on as
    FeatureOverrides, which is a different instrument with the opposite default);
  * `mentor.notebook` REPLACED a role gate, because composing it with one that
    admits MENTOR only would have left the Main Admin's grant inert — a key that
    is checked but can never be satisfied is the same lie one layer in;
  * the leave queue behind `require_admin` alone (2026-09-16), because it must
    stay staff-only whatever a grant says, and because the SUBMIT path must keep
    working for a faculty member with no mentees;
  * `mentor.agent` sits behind a ROLE BRANCH, because a student reaches the
    agent every day and holds no capability at all.

`tests/test_codebase_guards.py` §34 is the other half: it proves the catalogue
has no unchecked key left, so these behaviours cannot be quietly un-wired.
"""

from __future__ import annotations

import uuid

import pytest
from conftest import requires_db
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models.governance import CAPABILITIES, FEATURES_BY_KEY, CapabilityGrant, SubjectKind
from app.models.user import Role, Student

GOV = "/api/admin/governance"

#: The ten keys B2.1 removed. Named here rather than derived with a prefix match
#: so that re-adding one under the same name fails this module loudly.
DELETED_KEYS = (
    "student.profile",
    "student.records",
    "student.skilling",
    "student.uploads",
    "student.resume",
    "student.interviews",
    "student.english",
    "student.time_log",
    "student.mentor_log",
    "student.jobs",
)


def _student_id(user_id: str) -> str:
    with SessionLocal() as db:
        return db.scalar(select(Student.id).where(Student.user_id == user_id))


# --------------------------------------------------------------------------- #
# The ten keys that gated nothing
# --------------------------------------------------------------------------- #

def test_the_catalogue_carries_no_student_record_capability_any_more() -> None:
    """The ten `student.*` keys are gone from `CAPABILITIES`.

    They were checked nowhere in `app/` and read nowhere in `apps/web/src`, so
    granting one was a decision with a reason and an audit row that changed
    nothing. What actually decides whether a staff member may open a student's
    ledger is rule 2 plus the screen's own capability.

    DELETE THIS and the next person reading the old console screenshots puts
    them back, and the grant that follows is once again a promise nobody keeps.
    """
    keys = {c.key for c in CAPABILITIES}
    for key in DELETED_KEYS:
        assert key not in keys, f"{key} is back in the catalogue and still gates nothing"
    assert not any(k.startswith("student.") for k in keys), (
        "a student.* capability reappeared: student-facing switches are FEATURES "
        "(allow by default, switched off with a message), not capabilities "
        "(deny by default, granted with a reason)"
    )


def test_the_student_facing_names_survived_as_features_not_capabilities() -> None:
    """The names were not lost; they moved to the instrument that fits them.

    A capability is DENY BY DEFAULT and is held by staff. A feature is ALLOW BY
    DEFAULT and belongs to a student. "Can this student use the resume builder"
    was only ever the second question, and asking it with the first instrument
    is why the ten keys could sit in the catalogue unchecked for so long: nobody
    could say what checking one would even mean.

    DELETE THIS and B2.1 reads as ten switches thrown away rather than ten moved.
    """
    for key in ("student.resume", "student.jobs", "student.uploads", "student.english",
                "student.time_log", "student.skilling"):
        assert key in FEATURES_BY_KEY, f"{key} left the catalogue with nowhere to land"


@requires_db
def test_governance_refuses_to_grant_a_capability_the_catalogue_dropped(client, make_user):
    """The grant endpoint validates against the catalogue, so a deleted key 422s.

    This is what makes deleting a key safe to do: the console cannot mint a new
    row naming one, so the population of inert grants can only shrink.

    DELETE THIS and a stale client (or a curl from an old runbook) writes a grant
    for a key nothing checks, and the office believes it handed something over.
    """
    admin = make_user(f"b21-adm-{uuid.uuid4().hex[:4]}", Role.ADMIN)
    r = client.post(
        f"{GOV}/grants",
        headers=admin.headers,
        json={
            "capability": "student.records",
            "user_ids": [admin.user_id],
            "reason": "B2.1 proves the catalogue is the fence on this field.",
        },
    )
    assert r.status_code == 422, f"a deleted capability was accepted as a grant: {r.text}"


@requires_db
def test_a_grant_row_naming_a_deleted_capability_goes_inert_rather_than_breaking(make_user):
    """An existing row for a removed key is IGNORED, not an exception.

    Deployments already carry grants for the ten keys. `granted_capabilities`
    filters on `CAPABILITIES_BY_KEY`, so such a row resolves to nothing and every
    other capability the same user holds still resolves normally. It was already
    inert; now it says so.

    DELETE THIS and the next catalogue deletion is a 500 on /auth/me for anyone
    holding the old grant — which is every screen at once, for a member of staff
    who did nothing wrong.
    """
    from app.governance import granted_capabilities

    faculty = make_user(f"b21-inert-{uuid.uuid4().hex[:4]}", Role.MENTOR)
    with SessionLocal() as db:
        db.add(
            CapabilityGrant(
                capability="student.records",
                subject_kind=SubjectKind.USER,
                subject_user_id=faculty.user_id,
                reason="A row written before B2.1 deleted the key.",
            )
        )
        db.add(
            CapabilityGrant(
                capability="admin.analytics",
                subject_kind=SubjectKind.USER,
                subject_user_id=faculty.user_id,
                reason="A live key held by the same account.",
            )
        )
        db.commit()
    try:
        with SessionLocal() as db:
            held = granted_capabilities(db, faculty.user_id)
        assert "student.records" not in held, "a key the catalogue dropped resolved anyway"
        assert "admin.analytics" in held, (
            "the dead row took a live one with it — the filter is dropping too much"
        )
    finally:
        with SessionLocal() as db:
            db.execute(
                delete(CapabilityGrant).where(CapabilityGrant.subject_user_id == faculty.user_id)
            )
            db.commit()


# --------------------------------------------------------------------------- #
# mentor.notebook — the capability REPLACED the role gate
# --------------------------------------------------------------------------- #

@requires_db
def test_the_notebook_is_reachable_by_a_granted_main_admin_and_by_nobody_ungranted(
    client, make_user, granted
):
    """`mentor.notebook` is now THE gate on the eight notebook routes.

    Three places in this repository promise that the Main Admin can be granted
    this in Governance and stand in for a faculty member: the docstring on
    `policies.require_notebook_staff`, the `_FACULTY_ONLY` note in
    app/governance.py, and AGENTS.md. Until B2.1 the promise was false — the role
    gate admitted MENTOR only and refused the office account before the grant was
    ever consulted. Composing a capability check with that gate would have kept
    it false, which is why the capability REPLACED it over a staff floor.

    DELETE THIS and the grant silently stops working again: Governance still
    offers it, the audit row is still written, and the screen still 403s.
    """
    admin = make_user(f"b21-nb-adm-{uuid.uuid4().hex[:4]}", Role.ADMIN)
    faculty = make_user(f"b21-nb-fac-{uuid.uuid4().hex[:4]}", Role.MENTOR)
    stu = make_user(f"b21-nb-stu-{uuid.uuid4().hex[:4]}", Role.STUDENT)
    entries = f"/api/v1/mentor/notebook/students/{_student_id(stu.user_id)}/entries"

    # The office account holds no faculty instrument by baseline (_FACULTY_ONLY).
    assert client.get(entries, headers=admin.headers).status_code == 403

    # A faculty account with NO mentees does not hold the function either. It
    # used to reach this and be turned away by rule 2 one line later with a 404;
    # a 403 that names the capability is the same outcome told honestly.
    assert client.get(entries, headers=faculty.headers).status_code == 403

    # The student whose notebook it is has no business on the staff route at all:
    # `require_staff` is the floor under the capability, and it must stay.
    assert client.get(entries, headers=stu.headers).status_code == 403

    granted(admin, "mentor.notebook")
    r = client.get(entries, headers=admin.headers)
    assert r.status_code == 200, f"the granted Main Admin still cannot read it: {r.text}"
    assert r.json() == []


@requires_db
def test_a_granted_admin_can_read_the_notebook_but_never_author_in_a_mentors_name(
    client, make_user, granted
):
    """Standing in is READING. Authoring stays with the assigned mentor.

    `create_entry` and `create_action` carry their own "only an assigned mentor"
    checks, and B2.1 left them exactly as they were. A notebook entry is signed
    work — it carries `mentor_id` and an author — and the office account has no
    group to sign as.

    DELETE THIS and widening the gate to admit a granted admin quietly widens the
    write path too, and a mentor's private record grows an entry they did not
    write.
    """
    admin = make_user(f"b21-nbw-adm-{uuid.uuid4().hex[:4]}", Role.ADMIN)
    stu = make_user(f"b21-nbw-stu-{uuid.uuid4().hex[:4]}", Role.STUDENT)
    sid = _student_id(stu.user_id)
    granted(admin, "mentor.notebook")

    r = client.post(
        f"/api/v1/mentor/notebook/students/{sid}/entries",
        headers=admin.headers,
        json={"body": "Standing in for a colleague."},
    )
    assert r.status_code == 403, f"a granted admin authored an entry: {r.text}"
    assert "assigned mentor" in r.text


# --------------------------------------------------------------------------- #
# the leave queue — the Main Admin's alone since 2026-09-16
# --------------------------------------------------------------------------- #

@requires_db
def test_a_faculty_member_with_no_mentees_can_still_ask_for_leave(client, make_user):
    """THE HALF THAT MUST NOT BREAK. Submitting is not an approver's act.

    Approving leave is the Main Admin's (2026-09-16). Putting that gate on
    `POST /api/leaves` — one plausible reading of "leave is the office's" — is
    how a new lecturer discovers they cannot ask for a day off.

    DELETE THIS and the gate creeps onto the submit path the next time someone
    tidies the four handlers into one dependency.
    """
    faculty = make_user(f"b21-lv-fac-{uuid.uuid4().hex[:4]}", Role.MENTOR)
    from app.models.leave import LeaveRequest

    r = client.post(
        "/api/leaves",
        headers=faculty.headers,
        json={"from_date": "2026-10-01", "to_date": "2026-10-02", "reason": "B2.1 probe"},
    )
    assert r.status_code == 201, f"a faculty member could not submit their own leave: {r.text}"
    leave_id = r.json()["id"]
    try:
        mine = client.get("/api/leaves/mine", headers=faculty.headers)
        assert mine.status_code == 200
        assert leave_id in [row["id"] for row in mine.json()]
    finally:
        with SessionLocal() as db:
            db.execute(delete(LeaveRequest).where(LeaveRequest.id == leave_id))
            db.commit()


@requires_db
def test_the_approver_queue_needs_the_capability_and_refuses_the_same_way_for_every_id(
    client, make_user
):
    """A faculty account with no mentees is refused the queue, the history AND
    the signature — and the refusal cannot be used to probe which leave ids exist.

    The 403 is decided BEFORE any id is looked up, so an invented id and a real
    one get the identical answer. `_assert_can_decide` flattens its own refusals
    to a single 404 for exactly this reason; a capability check placed after the
    row load would have undone that work by answering 404 for unknown ids and 403
    for real ones.

    DELETE THIS and the decision endpoint becomes a membership oracle over the
    whole programme for anybody holding a faculty account: guess ids, read the
    error, learn who has leave pending.
    """
    from app.models.leave import LeaveRequest

    faculty = make_user(f"b21-lvq-fac-{uuid.uuid4().hex[:4]}", Role.MENTOR)
    applicant = make_user(f"b21-lvq-stu-{uuid.uuid4().hex[:4]}", Role.STUDENT)
    r = client.post(
        "/api/leaves",
        headers=applicant.headers,
        json={"from_date": "2026-10-05", "to_date": "2026-10-06", "reason": "oracle probe"},
    )
    assert r.status_code == 201, r.text
    leave_id = r.json()["id"]
    try:
        assert client.get("/api/leaves/pending", headers=faculty.headers).status_code == 403
        assert client.get("/api/leaves/history", headers=faculty.headers).status_code == 403

        real = client.post(
            f"/api/leaves/{leave_id}/decision",
            headers=faculty.headers,
            json={"decision": "APPROVE"},
        )
        invented = client.post(
            "/api/leaves/no-such-leave-id/decision",
            headers=faculty.headers,
            json={"decision": "APPROVE"},
        )
        assert real.status_code == 403, real.text
        assert invented.status_code == real.status_code, (
            "a real leave id and an invented one answer differently — the refusal "
            "is an oracle"
        )
        assert invented.json() == real.json()
    finally:
        with SessionLocal() as db:
            db.execute(delete(LeaveRequest).where(LeaveRequest.id == leave_id))
            db.commit()


@requires_db
def test_only_the_main_admin_gets_the_leave_queue(client, login, make_user):
    """Leave approval is the Main Admin's alone (2026-09-16). The seeded mentor
    HAS a mentee and used to get the queue as a derived function; the function
    and the capability are gone, and the queue answers 403 to every faculty
    account.

    DELETE THIS and a faculty approver can come back through whichever gate is
    loosened first, with every medical `reason` in the queue behind it.
    """
    admin = make_user(f"b21-lvo-adm-{uuid.uuid4().hex[:4]}", Role.ADMIN)
    assert client.get("/api/leaves/pending", headers=admin.headers).status_code == 200
    assert client.get("/api/leaves/history", headers=admin.headers).status_code == 200

    mentor_h = login("mentor@bgscet.ac.in", "mentor123")
    client.cookies.clear()  # explicit Cookie headers only; the jar would override them
    assert client.get("/api/leaves/pending", headers=mentor_h).status_code == 403
    assert client.get("/api/leaves/history", headers=mentor_h).status_code == 403


# --------------------------------------------------------------------------- #
# mentor.agent — the capability behind a ROLE BRANCH
# --------------------------------------------------------------------------- #

@requires_db
def test_the_agent_capability_never_touches_a_student(client, make_user, monkeypatch):
    """A STUDENT holds no capability at all and still reaches POST /api/agent/ask.

    `ROLE_BASELINE["STUDENT"]` is `frozenset()`, and students use this endpoint
    every day from /student/agent and the orb's "Type instead". An unconditional
    `require_capability(db, session, "mentor.agent")` would 403 every student in
    the deployment on the deploy that shipped it. Whether a student has the agent
    is a FeatureOverride question (`student.agent`), which is allow-by-default.

    DELETE THIS and the role branch looks like a redundant `if` to the next
    person simplifying the helper.
    """
    import app.ai.orchestrator as orch

    # No provider => the deterministic builders answer; nothing leaves the box.
    monkeypatch.setattr(orch, "llm_config", lambda: None)

    stu = make_user(f"b21-ag-stu-{uuid.uuid4().hex[:4]}", Role.STUDENT)
    r = client.post("/api/agent/ask", headers=stu.headers, json={"message": "Am I ready?"})
    assert r.status_code == 200, f"the capability branch caught a student: {r.text}"


@requires_db
def test_staff_reach_the_agent_through_the_capability_they_hold(client, make_user, monkeypatch):
    """Both staff roles carry `mentor.agent` in their baseline, so the check is
    satisfied rather than skipped — which is the difference between a key that is
    wired up and a key that is not.

    DELETE THIS and "enforce mentor.agent" could be satisfied by a call site that
    is never reached, and nobody would notice until the baseline moved.
    """
    from app.governance import ROLE_BASELINE

    import app.ai.orchestrator as orch

    monkeypatch.setattr(orch, "llm_config", lambda: None)

    assert "mentor.agent" in ROLE_BASELINE["MENTOR"]
    assert "mentor.agent" in ROLE_BASELINE["ADMIN"]

    for role in (Role.MENTOR, Role.ADMIN):
        account = make_user(f"b21-ag-{role.value.lower()}-{uuid.uuid4().hex[:4]}", role)
        r = client.post(
            "/api/agent/ask", headers=account.headers, json={"message": "What can you see?"}
        )
        assert r.status_code == 200, f"{role.value} was refused the agent: {r.text}"
