"""B1.3 — a college admin is a set of scoped grants, not a second Main Admin.

REEP has ONE office account by rule, and `grant_access._refuse_second_main_admin`
is what keeps it that way. The second college a deployment onboards still needs
somebody to run it, and the answer is deliberately not a role: a FACULTY account
(`users.role` stays MENTOR) holding eleven `admin.*` capabilities scoped to one
college. The function is the grant; the role is the identity.

What these tests hold down:

1. THE SET IS SCOPED, AND SCOPED TO THE COLLEGE NAMED IN THE PATH. Delete
   `test_the_appointment_grants_the_set_scoped_to_that_college` and the
   appointment writes programme-wide grants, which is a second Main Admin with
   extra steps — and eleven of them, in one click, with a reason that says
   "college admin".
2. TWO KEYS ARE NEVER IN IT. Delete `test_the_set_never_hands_on_access` and
   `admin.governance` joins the list, at which point a college admin appoints
   the next one — including themselves, to anything.
3. THE ROLE DOES NOT MOVE. Delete `test_nobody_becomes_a_second_main_admin` and
   the day somebody "simplifies" this into a role change is the day the one
   rule `_refuse_second_main_admin` exists to enforce is routed around by an
   endpoint that never calls it.
4. THE APPOINTMENT FOLLOWS THE GRANTS SCREEN'S RULE. Delete
   `test_the_main_admins_appointment_is_live_at_once_and_a_deputys_waits` and
   the endpoint is free to drift from `POST /governance/grants`: either the
   office's own appointment goes back to waiting on a deputy it appointed
   itself, or a deputy's becomes the way to issue seven `carries_pii` grants
   without the second approval B2.4 requires of every other path.
5. IT IS IDEMPOTENT AND AUDITED. Delete `test_appointing_twice_changes_nothing`
   and a second click leaves two live rows for one decision, which makes
   revocation a question of which one; delete `test_every_grant_lands_on_the_trail`
   and "why does this person hold Exports for BGSCET" has no answer.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app.db import SessionLocal
from app.models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from app.models.institution import STATUS_ACTIVE, College, Department
from app.models.redesign import AuditEvent
from app.models.user import Role, User
from app.routers.admin import COLLEGE_ADMIN_CAPABILITIES

REASON = "runs the Far campus day to day while the office covers the main one"


@pytest.fixture
def colleges():
    """Two colleges, each with a department, so "scoped to THIS one" can fail."""
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        mine = College(code=f"C{tag.upper()}", name="Appointed College", status=STATUS_ACTIVE)
        other = College(code=f"O{tag.upper()}", name="Other College", status=STATUS_ACTIVE)
        db.add_all([mine, other])
        db.flush()
        here = Department(college_id=mine.id, name="Here", code=f"CH{tag}")
        db.add(here)
        db.commit()
        made = {"tag": tag, "mine": mine.id, "other": other.id, "here": here.id}

    yield made

    with SessionLocal() as db:
        db.execute(delete(Department).where(Department.id == made["here"]))
        db.execute(delete(College).where(College.id.in_([made["mine"], made["other"]])))
        db.commit()


@pytest.fixture
def forget_grants():
    """Remove every grant an appointment wrote, for the accounts named.

    A grant is keyed on a user id and OUTLIVES the `make_user` account that
    named it, so a leaked row silently widens whatever test runs next — the
    reason `conftest.granted` cleans up too.
    """
    users: list[str] = []
    yield users
    if users:
        with SessionLocal() as db:
            db.execute(delete(CapabilityGrant).where(CapabilityGrant.subject_user_id.in_(users)))
            db.commit()


def _grants_of(user_id: str) -> list[CapabilityGrant]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(CapabilityGrant).where(
                    CapabilityGrant.subject_user_id == user_id,
                    CapabilityGrant.revoked_at.is_(None),
                )
            ).all()
        )


# ------------------------------------------------------------ the set ------


@requires_db
def test_the_appointment_grants_the_set_scoped_to_that_college(
    client, make_user, colleges, forget_grants
):
    """Eleven grants, every one hung on the college in the path.

    IF THE SCOPE GOES, so does the whole idea: programme-wide grants of
    Registrations, Students, Mentors and Exports are the Main Admin's console
    handed to somebody in eleven rows, made in one click, under a reason that
    says "college admin" — which is the sentence an auditor would read and
    believe.
    """
    admin = make_user(f"ca-admin-{colleges['tag']}", Role.ADMIN)
    faculty = make_user(f"ca-fac-{colleges['tag']}", Role.MENTOR)
    forget_grants.append(faculty.user_id)

    r = client.post(
        f"/api/admin/colleges/{colleges['mine']}/admins",
        headers=admin.headers,
        json={"user_id": faculty.user_id, "reason": REASON},
    )
    assert r.status_code == 201, r.text
    assert r.json()["missing"] == []

    rows = _grants_of(faculty.user_id)
    assert {g.capability for g in rows} == set(COLLEGE_ADMIN_CAPABILITIES)
    for g in rows:
        assert g.scope_level is ScopeLevel.COLLEGE, g.capability
        assert g.scope_id == colleges["mine"], g.capability
        assert g.subject_kind is SubjectKind.USER
        assert g.reason == REASON
        # B2.5. A grant is a decision about a person IN A ROLE; without this
        # the row survives a role change and describes nobody.
        assert g.role_at_grant == Role.MENTOR.value


@requires_db
def test_the_set_never_hands_on_access(client, make_user, colleges, forget_grants):
    """`admin.governance` is not in the set, and neither is interview audio.

    A college admin who could grant would appoint the next one — including
    themselves, to anything, in any college — and the fence would be a fence
    with a gate in it. `admin.interview_audio` is out for a different reason:
    a recording is a named student's voice, and it is the one capability that
    stays a separate decision every time it is made.

    A third line stood here asserting `ui.console_v2` was out of the set. Phase 5
    deleted that key from the catalogue, which makes the assertion vacuously
    true — and a vacuous assertion in a test named "never hands on access" reads
    as one more fence than there is. The rule it stood for survives where it
    bites: `set(COLLEGE_ADMIN_CAPABILITIES) <= set(CAPABILITIES_BY_KEY)` in
    tests/test_imports_schema.py, because a name in this tuple with no catalogue
    entry is a KeyError thrown at whoever is appointing a college admin.
    """
    assert "admin.governance" not in COLLEGE_ADMIN_CAPABILITIES
    assert "admin.interview_audio" not in COLLEGE_ADMIN_CAPABILITIES
    assert not [k for k in COLLEGE_ADMIN_CAPABILITIES if k.startswith("mentor.")], (
        "a college admin is not a faculty member of that college's students"
    )

    admin = make_user(f"ca-gov-{colleges['tag']}", Role.ADMIN)
    faculty = make_user(f"ca-govfac-{colleges['tag']}", Role.MENTOR)
    forget_grants.append(faculty.user_id)
    client.post(
        f"/api/admin/colleges/{colleges['mine']}/admins",
        headers=admin.headers, json={"user_id": faculty.user_id, "reason": REASON},
    )
    held = {g.capability for g in _grants_of(faculty.user_id)}
    assert "admin.governance" not in held
    # And the endpoint itself refuses them, which is the half a constant cannot
    # prove: the grant set is not the gate.
    refused = client.post(
        f"/api/admin/colleges/{colleges['other']}/admins",
        headers=faculty.headers, json={"user_id": faculty.user_id, "reason": REASON},
    )
    assert refused.status_code == 403, refused.text


@requires_db
def test_nobody_becomes_a_second_main_admin(client, make_user, colleges, forget_grants):
    """The role does not move, and `_refuse_second_main_admin` is untouched.

    This is the acceptance criterion B1.3 is written around. A college admin
    that worked by setting `users.role = ADMIN` would be a second office
    account minted by an endpoint that never calls the function that refuses
    one — and the refusal lives in `app/grant_access.py`, which this feature
    must not need to edit.
    """
    admin = make_user(f"ca-role-{colleges['tag']}", Role.ADMIN)
    faculty = make_user(f"ca-rolefac-{colleges['tag']}", Role.MENTOR)
    forget_grants.append(faculty.user_id)
    assert client.post(
        f"/api/admin/colleges/{colleges['mine']}/admins",
        headers=admin.headers, json={"user_id": faculty.user_id, "reason": REASON},
    ).status_code == 201

    with SessionLocal() as db:
        assert db.get(User, faculty.user_id).role is Role.MENTOR

        from app.grant_access import _refuse_second_main_admin

        with pytest.raises(ValueError) as refused:
            _refuse_second_main_admin(db, f"someone-else-{colleges['tag']}@bgscet.ac.in")
        assert "one Main Admin" in str(refused.value)


@requires_db
def test_the_main_admins_appointment_is_live_at_once_and_a_deputys_waits(
    client, make_user, colleges, forget_grants
):
    """WHO appoints decides the state — the same rule as `POST /governance/grants`.

    Appointed by the Main Admin, all thirteen are live the moment they are
    written (2026-09-16): the office is the one authority on the deployment
    and does not wait for a deputy it appointed itself to agree. Appointed by
    a DEPUTY, the seven that carry PII land `pending_approval` (B2.4) and the
    six that do not are live, so that college admin can start on the screens
    that name no student while the office approves the rest.

    Appointing through grants rather than through a role is what makes the
    second half possible at all: a role would have handed over the roster the
    moment it was typed, whoever typed it. Delete this and the appointment
    endpoint becomes the one door in Governance whose rule differs from the
    grants screen's.
    """
    admin = make_user(f"ca-pii-{colleges['tag']}", Role.ADMIN)
    faculty = make_user(f"ca-piifac-{colleges['tag']}", Role.MENTOR)
    forget_grants.append(faculty.user_id)
    r = client.post(
        f"/api/admin/colleges/{colleges['mine']}/admins",
        headers=admin.headers, json={"user_id": faculty.user_id, "reason": REASON},
    )
    assert r.status_code == 201, r.text
    states = {row["capability"]: row["approval_state"] for row in r.json()["capabilities"]}
    assert set(states.values()) == {"active"}, states

    # A deputy: a faculty account the office gave `admin.governance`.
    deputy = make_user(f"ca-dep-{colleges['tag']}", Role.MENTOR)
    other = make_user(f"ca-piifar-{colleges['tag']}", Role.MENTOR)
    forget_grants.extend([deputy.user_id, other.user_id])
    dep = client.post(
        "/api/admin/governance/grants", headers=admin.headers,
        json={
            "capability": "admin.governance", "user_ids": [deputy.user_id],
            "reason": "Deputy for governance while the office covers the main campus.",
        },
    )
    assert dep.status_code == 201, dep.text
    r = client.post(
        f"/api/admin/colleges/{colleges['mine']}/admins",
        headers=deputy.headers, json={"user_id": other.user_id, "reason": REASON},
    )
    assert r.status_code == 201, r.text
    states = {row["capability"]: row["approval_state"] for row in r.json()["capabilities"]}
    assert states["admin.students"] == "pending_approval"
    assert states["admin.exports"] == "pending_approval"
    # And one that does not carry PII is live at once, so a fresh college admin
    # can start work on the screens that do not name a student.
    assert states["admin.analytics"] == "active"


@requires_db
def test_the_live_half_of_the_set_is_already_scoped(
    client, make_user, colleges, forget_grants
):
    """End to end: the appointment produces a console narrowed to one college.

    `admin.analytics` is not `carries_pii`, so it is live the moment it is
    written — which makes it the key that can prove the appointment actually
    reaches an endpoint, and reaches it NARROWED, without a second Main Admin
    having to approve anything first.
    """
    admin = make_user(f"ca-live-{colleges['tag']}", Role.ADMIN)
    faculty = make_user(f"ca-livefac-{colleges['tag']}", Role.MENTOR)
    other_faculty = make_user(f"ca-livefar-{colleges['tag']}", Role.MENTOR)
    forget_grants.append(faculty.user_id)
    with SessionLocal() as db:
        db.get(User, other_faculty.user_id).department_id = colleges["here"]
        db.commit()
    try:
        client.post(
            f"/api/admin/colleges/{colleges['mine']}/admins",
            headers=admin.headers, json={"user_id": faculty.user_id, "reason": REASON},
        )
        r = client.get("/api/admin/mentor-load", headers=faculty.headers)
        assert r.status_code == 200, r.text
        assert r.headers["X-Reep-Scope"] == "narrowed"
        assert r.headers["X-Reep-Scope-Colleges"] == colleges["mine"]
        seen = {row["user_id"] for row in r.json()}
        assert other_faculty.user_id in seen, "a faculty account in the granted college"
        assert faculty.user_id not in seen, (
            "the appointee is unfiled, and an unfiled account hangs under nothing"
        )
    finally:
        with SessionLocal() as db:
            db.get(User, other_faculty.user_id).department_id = None
            db.commit()


@requires_db
def test_appointing_twice_changes_nothing(client, make_user, colleges, forget_grants):
    """Idempotent, by the same rule `POST /governance/grants` uses.

    Two live rows for one pair make revocation a question of which one, and here
    also a question of which reason was the real one. Delete this and the second
    click on a slow screen doubles the set.
    """
    admin = make_user(f"ca-twice-{colleges['tag']}", Role.ADMIN)
    faculty = make_user(f"ca-twicefac-{colleges['tag']}", Role.MENTOR)
    forget_grants.append(faculty.user_id)
    url = f"/api/admin/colleges/{colleges['mine']}/admins"
    client.post(url, headers=admin.headers, json={"user_id": faculty.user_id, "reason": REASON})
    first = {g.id for g in _grants_of(faculty.user_id)}

    again = client.post(
        url, headers=admin.headers,
        json={"user_id": faculty.user_id, "reason": "a second click on a slow screen, no change"},
    )
    assert again.status_code == 201, again.text
    assert {g.id for g in _grants_of(faculty.user_id)} == first
    assert len(first) == len(COLLEGE_ADMIN_CAPABILITIES)


@requires_db
def test_every_grant_lands_on_the_trail(client, make_user, colleges, forget_grants):
    """One audit row per grant, each naming the college it was scoped to.

    One row for the batch would make "why does this person hold Exports for
    BGSCET" answerable only by finding a grant they are not named in — the same
    argument `create_grants` makes for writing one per row.
    """
    admin = make_user(f"ca-audit-{colleges['tag']}", Role.ADMIN)
    faculty = make_user(f"ca-auditfac-{colleges['tag']}", Role.MENTOR)
    forget_grants.append(faculty.user_id)
    client.post(
        f"/api/admin/colleges/{colleges['mine']}/admins",
        headers=admin.headers, json={"user_id": faculty.user_id, "reason": REASON},
    )
    ids = [g.id for g in _grants_of(faculty.user_id)]
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.entity_type == "capability_grant",
                    AuditEvent.entity_id.in_(ids),
                )
            ).all()
        )
    assert len(rows) == len(ids)
    for row in rows:
        assert row.after_json["scope_id"] == colleges["mine"]
        assert row.after_json["reason"] == REASON
        assert row.after_json["appointment"] == "college_admin"


# ----------------------------------------------------------- the reading --


@requires_db
def test_the_list_shows_who_holds_what_and_what_is_missing(
    client, make_user, colleges, forget_grants
):
    """ANY of the set, not ALL, and the gaps are named.

    Somebody holding nine of eleven is a college admin whose appointment is
    incomplete or partly revoked, and this is the one screen that can say so.
    Requiring the full set would render them as nobody and leave nine live
    grants invisible here.
    """
    admin = make_user(f"ca-list-{colleges['tag']}", Role.ADMIN)
    faculty = make_user(f"ca-listfac-{colleges['tag']}", Role.MENTOR)
    forget_grants.append(faculty.user_id)
    client.post(
        f"/api/admin/colleges/{colleges['mine']}/admins",
        headers=admin.headers, json={"user_id": faculty.user_id, "reason": REASON},
    )
    with SessionLocal() as db:
        db.execute(
            delete(CapabilityGrant).where(
                CapabilityGrant.subject_user_id == faculty.user_id,
                CapabilityGrant.capability == "admin.jobs",
            )
        )
        db.commit()

    rows = client.get(
        f"/api/admin/colleges/{colleges['mine']}/admins", headers=admin.headers
    ).json()
    row = next(r for r in rows if r["user_id"] == faculty.user_id)
    assert row["missing"] == ["admin.jobs"]
    assert len(row["capabilities"]) == len(COLLEGE_ADMIN_CAPABILITIES) - 1

    # And the OTHER college lists nobody: the grants are hung on one id.
    assert not [
        r
        for r in client.get(
            f"/api/admin/colleges/{colleges['other']}/admins", headers=admin.headers
        ).json()
        if r["user_id"] == faculty.user_id
    ]


# ------------------------------------------------------------- refusals --


@requires_db
def test_a_reason_shorter_than_the_floor_is_refused(client, make_user, colleges):
    """The same floor `POST /governance/grants` applies, and the same sentence.

    A trail of one-word reasons is indistinguishable from none, and this
    endpoint writes eleven rows carrying whatever is typed here. Delete this and
    "ok" becomes the recorded justification for handing somebody a college.
    """
    admin = make_user(f"ca-reason-{colleges['tag']}", Role.ADMIN)
    faculty = make_user(f"ca-reasonfac-{colleges['tag']}", Role.MENTOR)
    r = client.post(
        f"/api/admin/colleges/{colleges['mine']}/admins",
        headers=admin.headers, json={"user_id": faculty.user_id, "reason": "ok"},
    )
    assert r.status_code == 422, r.text
    assert _grants_of(faculty.user_id) == []


@requires_db
def test_a_student_account_cannot_be_appointed(client, make_user, colleges):
    """Capabilities are granted to staff. A STUDENT gaining `admin.students`
    would be the roster editing itself, which is rule 2 rewritten by a form."""
    admin = make_user(f"ca-stu-{colleges['tag']}", Role.ADMIN)
    student = make_user(f"ca-stufac-{colleges['tag']}", Role.STUDENT)
    r = client.post(
        f"/api/admin/colleges/{colleges['mine']}/admins",
        headers=admin.headers, json={"user_id": student.user_id, "reason": REASON},
    )
    assert r.status_code == 422, r.text
    assert _grants_of(student.user_id) == []


@requires_db
def test_an_unknown_college_is_a_404_not_a_grant(client, make_user, colleges):
    """A typo in the path must not write eleven grants scoped to an id that
    names nothing — which is a reach of `nothing`, and therefore an appointment
    that looks made and does nothing at all."""
    admin = make_user(f"ca-404-{colleges['tag']}", Role.ADMIN)
    faculty = make_user(f"ca-404fac-{colleges['tag']}", Role.MENTOR)
    r = client.post(
        "/api/admin/colleges/not-a-college/admins",
        headers=admin.headers, json={"user_id": faculty.user_id, "reason": REASON},
    )
    assert r.status_code == 404, r.text
    assert _grants_of(faculty.user_id) == []
