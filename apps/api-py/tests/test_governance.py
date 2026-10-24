"""Governance: capability grants, access groups and student feature overrides.

The properties pinned here are the ones whose failure is SILENT — the endpoint
still returns 200, the console still renders, and the damage is somebody seeing a
student they should not, or a feature nobody can explain being off.

  * A capability never widens rule 2. It decides WHICH SCREENS; rule 2 decides
    WHICH STUDENTS. Both must pass, and no grant relaxes the second.
  * The baseline takes nothing away. Introducing deny-by-default grants must not
    remove a mentor's own mentee log on the deploy that ships them.
  * The reason floor is enforced in the API, not only in the form.
  * The most specific feature override wins, so an exception for one student does
    not require deleting the rule covering their whole specialization.
  * A STUDENT reaches none of it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from conftest import TEST_PASSWORD, requires_db

from app.db import SessionLocal
from app.mentor_functions import MENTOR_FUNCTIONS
from app.governance import (
    ROLE_BASELINE,
    _FACULTY_ONLY,
    capabilities_for,
    feature_enabled,
    granted_capabilities,
)
from app.models.governance import (
    CAPABILITIES,
    AccessGroup,
    AccessGroupMember,
    CapabilityGrant,
    CapabilityScope,
    FeatureOverride,
    ScopeLevel,
    SubjectKind,
)
from app.models.user import Role

GOV = "/api/admin/governance"
REASON = "Needed for the December placement review; presenting cohort readiness."


@pytest.fixture
def admin(make_user):
    """The Main Admin - the only account Governance admits."""
    return make_user(f"gov-adm-{uuid.uuid4().hex[:4]}", Role.ADMIN)


@pytest.fixture
def mentor(make_user):
    return make_user(f"gov-men-{uuid.uuid4().hex[:4]}", Role.MENTOR)


@pytest.fixture
def cleanup():
    """Governance rows are not owned by make_user, so they outlive it."""
    made: dict[str, list[str]] = {"grants": [], "groups": [], "overrides": []}
    yield made
    with SessionLocal() as db:
        for gid in made["grants"]:
            row = db.get(CapabilityGrant, gid)
            if row is not None:
                db.delete(row)
        for oid in made["overrides"]:
            row = db.get(FeatureOverride, oid)
            if row is not None:
                db.delete(row)
        for gid in made["groups"]:
            db.query(AccessGroupMember).filter(AccessGroupMember.group_id == gid).delete()
            row = db.get(AccessGroup, gid)
            if row is not None:
                db.delete(row)
        db.commit()


# --------------------------------------------------------------------------- #

def test_the_catalogue_is_code_and_every_key_is_unique() -> None:
    """No database needed: the catalogue is a constant, and a duplicate key would
    make `CAPABILITIES_BY_KEY` silently shorter than the tuple it came from —
    one capability shadowing another with a different scope."""
    keys = [c.key for c in CAPABILITIES]
    assert len(keys) == len(set(keys)), "duplicate capability key"
    programme = {c.key for c in CAPABILITIES if c.scope is CapabilityScope.PROGRAMME}
    # These four cannot be narrowed by a mentor group — they are cross-cohort by
    # nature, and mislabelling one SCOPED would hand over the programme while the
    # console painted it green.
    for key in ("admin.analytics", "admin.exports", "admin.institution", "admin.mentors"):
        assert key in programme, f"{key} must be PROGRAMME scope"


def test_the_baseline_takes_nothing_away_from_a_mentor() -> None:
    """A deny-by-default rollout would have removed every mentor's own mentee log
    on the deploy that shipped it. The baseline is what stops that."""
    mentor_caps = ROLE_BASELINE["MENTOR"]
    # B2.3: THE BASELINE IS WHAT BELONGS TO THE PERSON, NOT THE GROUP. It was
    # every SCOPED key, which made "is this person staff" and "may this person
    # read a mentee's ledger" one question with one answer. The ones that belong
    # to a group are derived from the mentee count now
    # (app/mentor_functions.py), and the ten student.* keys gate nothing.
    assert mentor_caps == {"mentor.agent", "mentor.upskilling"}
    assert "mentor.mentees" not in mentor_caps, (
        "a faculty account with no mentees is holding the mentee log again"
    )
    # ...and the programme-wide set is NOT inherited: that is what a grant is for.
    assert "admin.analytics" not in mentor_caps
    assert "admin.exports" not in mentor_caps
    # Was ROLE_BASELINE["DIRECTOR"]. DIRECTOR holds nothing now
    # (tests/test_no_director_privilege.py), and the office account is ADMIN.
    #
    # THE MAIN ADMIN IS NOT A SUPERSET OF A MENTOR, AND THAT IS DELIBERATE.
    # `ROLE_BASELINE["ADMIN"] >= mentor_caps` is the assertion this used to make,
    # and `_FACULTY_ONLY` in app/governance.py is what makes it false on purpose:
    # a mentee log, a private notebook, an evidence queue and an upskilling shelf
    # are a FACULTY MEMBER's instruments, and the Main Admin has no mentees,
    # nobody's evidence to verify and no shelf. Those four screens would render
    # empty for it, which is not access, it is a broken page.
    #
    # What must still hold is the thing the original assertion was protecting:
    # the office account holds every PROGRAMME capability, so introducing grants
    # took nothing away from it either. And the four exceptions stay GRANTABLE,
    # so "the Main Admin can never look" is not what this arrangement says —
    # it says the Main Admin has to be given the instrument, with a reason, on
    # the audit trail.
    admin_caps = ROLE_BASELINE["ADMIN"]
    assert admin_caps >= (mentor_caps - _FACULTY_ONLY)
    assert _FACULTY_ONLY and not (admin_caps & _FACULTY_ONLY), (
        "the Main Admin picked up a faculty instrument by baseline; a grant is the way in"
    )
    # A FACULTY MEMBER CAN STILL HOLD ALL FOUR — two by being staff, two by
    # mentoring somebody (B2.3), and one ONLY as a grant: skill verification
    # left the mentoring functions on 2026-10-16 (the owner's decision), so the
    # office chooses who verifies, in Who can do what. The original assertion
    # was `_FACULTY_ONLY <= mentor_caps` and it protected a real thing: a
    # faculty instrument nobody on the faculty can reach is a broken screen.
    # A grant-only instrument is reachable -- by the grant -- and it is named
    # here so that a SECOND one is a decision somebody writes down, not a
    # function that quietly fell out of the derived set.
    GRANT_ONLY = {"mentor.verifications"}
    assert _FACULTY_ONLY - GRANT_ONLY <= (mentor_caps | MENTOR_FUNCTIONS), (
        "a faculty instrument no faculty member can hold, by baseline or by mentees"
    )
    assert GRANT_ONLY.isdisjoint(mentor_caps | MENTOR_FUNCTIONS), (
        "skill verification came back with mentoring; the office chooses who verifies"
    )
    assert MENTOR_FUNCTIONS.isdisjoint(mentor_caps), (
        "a function is in the baseline as well, so it is held without any mentees"
    )
    assert _FACULTY_ONLY <= {c.key for c in CAPABILITIES}, (
        "a faculty instrument left the catalogue, so it can no longer be granted at all"
    )
    assert ROLE_BASELINE["STUDENT"] == frozenset()


def test_the_console_preview_switch_is_gone_from_every_reader() -> None:
    """`ui.console_v2` is DELETED (Phase 5), and it has to be gone from all four
    baselines as well as from the catalogue.

    It gated the 2026-09 console's new screens while the owner reviewed them on
    the production deployment — a capability rather than an environment flag
    because there was no flag to set there and no second build to serve. The
    review is over and the new screens are the console, so the switch is one
    that can only ever refuse.

    THE BASELINES ARE ASSERTED SEPARATELY FROM THE CATALOGUE ON PURPOSE. The
    baselines are built by set arithmetic over `_ALL`, `_SCOPED` and
    `_FACULTY_ONLY`, so dropping the `Capability(...)` line empties them by
    construction — today. A later hand-written baseline that names the string
    directly would reintroduce a key `require_capability` then raises
    ValueError on, which is a 500 in front of a member of staff rather than a
    403. This asserts the absence in both places so that stays impossible.

    Deployments may still hold GRANT ROWS naming it, and those stay: they are
    audit history, `granted_capabilities` filters them out against
    `CAPABILITIES_BY_KEY`, and `tests/test_capability_enforcement.py` pins that
    a row for a deleted key goes inert rather than breaking `/auth/me`.
    """
    key = "ui.console_v2"
    assert key not in {c.key for c in CAPABILITIES}, (
        "the preview switch is back in the catalogue. It gates nothing on the "
        "server (tools/ci/check_capability_enforcement.py has no exemptions now) "
        "and the console it previewed is the only console."
    )
    for role in ("ADMIN", "MENTOR", "STUDENT", "ALUMNI"):
        assert key not in ROLE_BASELINE[role], (
            f"{role}'s baseline still names the deleted preview switch, which "
            "makes require_capability raise ValueError — a 500, not a 403"
        )


@requires_db
def test_a_student_reaches_none_of_it(client, make_user) -> None:
    stu = make_user(f"gov-stu-{uuid.uuid4().hex[:4]}")
    for method, path in (
        ("get", f"{GOV}/catalogue"),
        ("get", f"{GOV}/hierarchy"),
        ("get", f"{GOV}/staff"),
        ("get", f"{GOV}/grants"),
        ("get", f"{GOV}/groups"),
        ("get", f"{GOV}/features"),
    ):
        r = getattr(client, method)(path, headers=stu.headers)
        assert r.status_code == 403, f"{path} let a STUDENT in: {r.status_code}"
    r = client.post(f"{GOV}/grants", headers=stu.headers,
                    json={"capability": "admin.analytics", "user_ids": [], "reason": REASON})
    assert r.status_code == 403


@requires_db
def test_a_reason_below_the_floor_is_refused_by_the_api(client, admin, mentor, cleanup) -> None:
    """The floor is in the API, not only the form. A second client, a script or a
    curl must not be able to write an unauditable grant."""
    r = client.post(f"{GOV}/grants", headers=admin.headers,
                    json={"capability": "admin.analytics", "user_ids": [mentor.user_id], "reason": "ok"})
    assert r.status_code == 422, r.text
    assert "20 characters" in r.text

    r = client.post(f"{GOV}/grants", headers=admin.headers,
                    json={"capability": "admin.analytics", "user_ids": [mentor.user_id], "reason": REASON})
    assert r.status_code == 201, r.text
    cleanup["grants"] += [g["id"] for g in r.json()]


@requires_db
def test_one_request_grants_to_several_people(client, admin, make_user, cleanup) -> None:
    """The console's multi-select, and one audit row per person rather than one
    for the batch — "why does Dr. Rao hold this" must be answerable from a row
    she is named in."""
    a = make_user(f"gov-a-{uuid.uuid4().hex[:4]}", Role.MENTOR)
    b = make_user(f"gov-b-{uuid.uuid4().hex[:4]}", Role.MENTOR)
    r = client.post(f"{GOV}/grants", headers=admin.headers, json={
        "capability": "admin.analytics", "user_ids": [a.user_id, b.user_id], "reason": REASON})
    assert r.status_code == 201, r.text
    rows = r.json()
    cleanup["grants"] += [g["id"] for g in rows]
    assert len(rows) == 2
    assert {g["subject_id"] for g in rows} == {a.user_id, b.user_id}
    assert all(g["reason"] == REASON for g in rows), "the batch reason is copied onto every row"
    assert all(g["scope"] == "PROGRAMME" for g in rows)

    # Re-granting is a no-op, not a duplicate: two live rows for one pair make
    # revocation a question of which one.
    again = client.post(f"{GOV}/grants", headers=admin.headers, json={
        "capability": "admin.analytics", "user_ids": [a.user_id], "reason": REASON})
    assert again.status_code == 201 and again.json() == []


@requires_db
def test_a_grant_adds_a_screen_and_a_group_hands_it_to_its_members(
    client, admin, mentor, make_user, cleanup
) -> None:
    with SessionLocal() as db:
        assert "admin.analytics" not in granted_capabilities(db, mentor.user_id)

    r = client.post(f"{GOV}/grants", headers=admin.headers, json={
        "capability": "admin.analytics", "user_ids": [mentor.user_id], "reason": REASON})
    cleanup["grants"] += [g["id"] for g in r.json()]

    with SessionLocal() as db:
        held = capabilities_for(db, {"userId": mentor.user_id, "role": "MENTOR"})
        assert "admin.analytics" in held, "the grant did not take effect"
        assert "mentor.agent" in held, "the baseline was lost when a grant appeared"

    # Now the group path: a second mentor inherits by joining, with no grant of
    # their own. That is the reason groups exist.
    grp = client.post(f"{GOV}/groups", headers=admin.headers,
                      json={"name": f"Coordinators {uuid.uuid4().hex[:6]}"})
    assert grp.status_code == 201, grp.text
    gid = grp.json()["id"]
    cleanup["groups"].append(gid)

    # THE GROUP'S CAPABILITY CHANGED FROM `admin.exports`, THE PROPERTY DID NOT
    # (B2.4). Exports is flagged `carries_pii`; when this was written the
    # office's grant of it landed `pending_approval` and held nothing until a
    # SECOND holder of `admin.governance` approved it — which would have made
    # this test about the four-eyes rule rather than about groups. The Main
    # Admin's grants are live at once now (2026-09-16), but the swap stays: a
    # deputy's still waits, and a group test has no business turning on who
    # happened to be granting.
    # `admin.catalogue` is the nearest live equivalent: PROGRAMME, not held by a
    # mentor's baseline, and not personal, so joining the group is the only thing
    # that can hand it over. The approval rule has its own module
    # (tests/test_governance_review.py) where it is the subject.
    r = client.post(f"{GOV}/grants", headers=admin.headers, json={
        "capability": "admin.catalogue", "group_ids": [gid], "reason": REASON})
    cleanup["grants"] += [g["id"] for g in r.json()]

    joiner = make_user(f"gov-join-{uuid.uuid4().hex[:4]}", Role.MENTOR)
    with SessionLocal() as db:
        assert "admin.catalogue" not in granted_capabilities(db, joiner.user_id)

    add = client.post(f"{GOV}/groups/{gid}/members", headers=admin.headers,
                      json={"user_ids": [joiner.user_id], "reason": REASON})
    assert add.status_code == 200, add.text

    with SessionLocal() as db:
        assert "admin.catalogue" in granted_capabilities(db, joiner.user_id), \
            "joining the group did not hand over its capability"


@requires_db
def test_a_grant_never_widens_which_students_a_mentor_reaches(
    client, admin, mentor, make_user, cleanup
) -> None:
    """THE SAFETY PROPERTY. A capability decides which SCREENS; rule 2 decides
    which STUDENTS. `mentor` here has no Mentor group at all — the account rule 2
    exists to exclude — and holding a capability that reaches a student's own
    record must not give them a single student.

    THE CAPABILITY GRANTED HERE CHANGED, THE PROPERTY DID NOT (B2.1). It was
    `student.records`, one of ten `student.*` keys that were checked at zero call
    sites anywhere in `app/`; B2.1 deleted them from the catalogue, so granting
    one is now a 422 and this test could no longer set its scene. `mentor.
    verifications` is the nearest live equivalent — SCOPED, and it reaches a
    named student's evidence — and it makes the same point more sharply, because
    unlike the deleted key it is a capability that really does something.
    """
    stu = make_user(f"gov-out-{uuid.uuid4().hex[:4]}")
    r = client.post(f"{GOV}/grants", headers=admin.headers, json={
        "capability": "mentor.verifications", "user_ids": [mentor.user_id], "reason": REASON})
    cleanup["grants"] += [g["id"] for g in r.json()]

    with SessionLocal() as db:
        assert "mentor.verifications" in capabilities_for(
            db, {"userId": mentor.user_id, "role": "MENTOR"}
        )

    # Rule 2 is unmoved: the groupless mentor still reaches nobody. Asserted
    # through the real endpoint, because that is where the gate actually runs.
    # THE REFUSAL MOVED ONE GATE EARLIER; THE PROPERTY IS THE SAME. Before B2.3
    # this answered 200 with an empty list: the account held mentor.mentees
    # through the baseline and rule 2 filtered every student out. A faculty
    # account with no mentees does not hold the function at all now, so the
    # capability gate refuses first. Either way it reaches nobody — and 403
    # tells them the true reason, which "200 []" never did.
    listing = client.get("/api/mentor/mentees", headers=mentor.headers)
    assert listing.status_code == 403, "a capability handed a groupless mentor a mentee"

    sid = None
    with SessionLocal() as db:
        from app.models.user import Student
        row = db.query(Student).filter(Student.user_id == stu.user_id).one_or_none()
        sid = row.id if row else None
    if sid:
        notes = client.get(f"/api/mentor/students/{sid}/notes", headers=mentor.headers)
        assert notes.status_code in (403, 404), (
            "a capability reached a student outside the mentor's group"
        )


@requires_db
def test_a_revoked_grant_stops_working_and_the_row_survives(
    client, admin, mentor, cleanup
) -> None:
    r = client.post(f"{GOV}/grants", headers=admin.headers, json={
        "capability": "admin.placement", "user_ids": [mentor.user_id], "reason": REASON})
    gid = r.json()[0]["id"]
    cleanup["grants"].append(gid)

    with SessionLocal() as db:
        assert "admin.placement" in granted_capabilities(db, mentor.user_id)

    rev = client.post(f"{GOV}/grants/{gid}/revoke", headers=admin.headers,
                      json={"reason": "Moved off the placement cell to full-time teaching."})
    assert rev.status_code == 200, rev.text

    with SessionLocal() as db:
        assert "admin.placement" not in granted_capabilities(db, mentor.user_id)
        row = db.get(CapabilityGrant, gid)
        assert row is not None, "the grant was deleted; who held it in March is now unanswerable"
        assert row.revoked_at is not None and row.revoke_reason

    # Revoking twice is a conflict, not a second silent success.
    again = client.post(f"{GOV}/grants/{gid}/revoke", headers=admin.headers,
                        json={"reason": "Moved off the placement cell to full-time teaching."})
    assert again.status_code == 409


@requires_db
def test_the_most_specific_feature_override_wins(make_user, cleanup) -> None:
    """Switch the voice interviewer off for a cohort, back on for one student in
    it. Both rules stay; the student-level one wins. Deleting the broader rule to
    make the exception would turn the feature on for everyone else in the cohort.
    """
    from app.models.cohort import Cohort
    from app.models.job import DegreeLevel
    from app.models.user import Student

    stu = make_user(f"gov-feat-{uuid.uuid4().hex[:4]}")
    with SessionLocal() as db:
        student = db.query(Student).filter(Student.user_id == stu.user_id).one()
        tag = uuid.uuid4().hex[:6]
        cohort = Cohort(
            code=f"GOV-{tag}", name=f"gov-batch-{tag}", batch_label="2024-26",
            degree_level=DegreeLevel.PG,
            start_date=datetime(2024, 8, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 6, 30, tzinfo=timezone.utc),
        )
        db.add(cohort)
        db.flush()
        student.cohort_id = cohort.id
        db.commit()
        sid, cid = student.id, cohort.id

    with SessionLocal() as db:
        assert feature_enabled(db, sid, "student.assistant") is True, "features are on by default"

        off = FeatureOverride(feature="student.assistant", scope=ScopeLevel.COHORT,
                              target_id=cid, enabled=False, reason=REASON)
        db.add(off)
        db.commit()
        cleanup["overrides"].append(off.id)

    with SessionLocal() as db:
        assert feature_enabled(db, sid, "student.assistant") is False, "the cohort rule did not apply"

        back = FeatureOverride(feature="student.assistant", scope=ScopeLevel.STUDENT,
                               target_id=sid, enabled=True, reason=REASON)
        db.add(back)
        db.commit()
        cleanup["overrides"].append(back.id)

    with SessionLocal() as db:
        assert feature_enabled(db, sid, "student.assistant") is True, \
            "the student-level exception lost to the cohort rule"
        # And the broader rule is still there for everyone else.
        assert db.get(FeatureOverride, cleanup["overrides"][0]) is not None

    with SessionLocal() as db:
        for oid in cleanup["overrides"]:
            row = db.get(FeatureOverride, oid)
            if row:
                db.delete(row)
        db.commit()
        student = db.get(Student, sid)
        if student:
            student.cohort_id = None
        db.commit()
        c = db.get(Cohort, cid)
        if c:
            db.delete(c)
        db.commit()
    cleanup["overrides"].clear()


@requires_db
def test_a_granted_capability_opens_the_analytics_endpoints(client, admin, mentor, cleanup) -> None:
    """THE WIRING. Until this, a grant was recorded and nothing checked it — a
    mentor granted Analytics was still stopped by role at the API, at the route
    guard and in the nav. This pins the API half end to end, and the fact that
    it takes effect WITHOUT a new sign-in: capabilities are resolved live, not
    carried in the cookie, so the session minted before the grant sees it.
    """
    # Before: a mentor is refused, and /me says they hold no admin capability.
    r = client.get("/api/admin/analytics-summary", headers=mentor.headers)
    assert r.status_code == 403, r.text
    me = client.get("/api/auth/me", headers=mentor.headers).json()
    assert "admin.analytics" not in me["capabilities"]
    assert "mentor.agent" in me["capabilities"], "the baseline must be reported too"

    r = client.post(f"{GOV}/grants", headers=admin.headers, json={
        "capability": "admin.analytics", "user_ids": [mentor.user_id], "reason": REASON})
    assert r.status_code == 201, r.text
    cleanup["grants"] += [g["id"] for g in r.json()]

    # After, on the SAME cookie: the endpoint opens and /me reports it.
    r = client.get("/api/admin/analytics-summary", headers=mentor.headers)
    assert r.status_code == 200, r.text
    me = client.get("/api/auth/me", headers=mentor.headers).json()
    assert "admin.analytics" in me["capabilities"]

    # An admin never needed a grant — the baseline carries it.
    assert client.get("/api/admin/analytics-summary", headers=admin.headers).status_code == 200


@requires_db
def test_interview_audio_is_a_capability_faculty_must_be_granted(client, admin, make_user, cleanup) -> None:
    """The recording gate: ADMIN by baseline, everyone else only by grant.

    interview_records.py had `_DEVELOPERS = {"ADMIN"}` and a docstring refusing
    to widen to require_admin - every placement account would then hold the
    most sensitive bytes REEP stores. That asymmetry must SURVIVE the move to a
    capability, and the capability is what makes it grantable to one person, for
    one reason, on the trail.

    WRITTEN AGAINST A FACULTY MEMBER because DIRECTOR no longer exists
    (tests/test_no_director_privilege.py). It used to be the subject here, as
    the role whose baseline excluded exactly this one capability; now no role
    but ADMIN carries it by baseline and MENTOR is the account that has to ask.

    404, not 200, is the pass signal past the gate: the session id here is
    invented, and the route answers 404 for "no such recording" identically to
    "not a real id", by design. What matters is that 403 becomes 404 - the gate
    opened.

    THE STUDENT IS IN THIS MENTOR'S GROUP DELIBERATELY. Without that, rule 2
    would 404 them for scope and the test would pass on the wrong 404 - green
    whether or not the capability gate ever opened.

    THE MAIN ADMIN'S GRANT IS LIVE AT ONCE (2026-09-16). `admin.interview_audio`
    is flagged `carries_pii` — it is a named student's recorded voice, the most
    sensitive bytes REEP stores — and between B2.4 and this date the office's
    own grant of it landed `pending_approval`, waiting for a deputy the office
    had appointed to agree with it. The office is the one authority on this
    deployment and does not ask its own appointee's permission; a DEPUTY's
    grant of this key still waits, and `tests/test_governance_review.py` is
    where that is pinned. Asserted here rather than assumed, because the old
    rule made this exact grant hold nothing.
    """
    from app.models.user import Mentor, Student

    faculty = make_user(f"gov-fac-{uuid.uuid4().hex[:4]}", Role.MENTOR)
    stu = make_user(f"gov-aud-{uuid.uuid4().hex[:4]}")
    with SessionLocal() as db:
        group = Mentor(user_id=faculty.user_id)
        db.add(group)
        db.flush()
        student = db.query(Student).filter(Student.user_id == stu.user_id).one()
        student.mentor_id = group.id
        sid = student.id
        db.commit()
        mentor_row_id = group.id

    # Re-authenticate: the session is a signed snapshot, so the mentorId claim
    # only appears in a cookie minted after the group existed.
    login = client.post(
        "/api/auth/login", json={"email": faculty.email, "password": TEST_PASSWORD}
    )
    assert login.status_code == 200, login.text
    headers = {"Cookie": login.headers.get("set-cookie", "")}
    client.cookies.clear()

    url = f"/api/mentor/students/{sid}/interviews/{uuid.uuid4().hex}/audio"
    try:
        # Faculty, ungranted: refused - and told what to ask for.
        r = client.get(url, headers=headers)
        assert r.status_code == 403, r.text
        assert "Interview audio" in r.text and "Governance" in r.text

        # The Main Admin, no grant: through on the baseline.
        assert client.get(url, headers=admin.headers).status_code == 404

        # Faculty cannot grant it to themselves - Governance is a capability
        # they do not hold. The refusal used to read "Main Admin only"; B2.6
        # replaced that role gate with `admin.governance`, so it now names the
        # capability to ask for, which is the same refusal said usefully.
        body = {
            "capability": "admin.interview_audio",
            "user_ids": [faculty.user_id],
            "reason": REASON,
        }
        r = client.post(f"{GOV}/grants", headers=headers, json=body)
        assert r.status_code == 403 and "Governance" in r.text, r.text
        r = client.post(f"{GOV}/grants", headers=admin.headers, json=body)
        assert r.status_code == 201, r.text
        rows = r.json()
        cleanup["grants"] += [g["id"] for g in rows]
        assert rows[0]["approval_state"] == "active", (
            "the Main Admin's grant of a student's recorded voice waited for a "
            "second signature that only an appointee of the office could give"
        )

        # Same cookie, no re-login: the gate now opens for them too.
        assert client.get(url, headers=headers).status_code == 404
        assert (
            "admin.interview_audio"
            in client.get("/api/auth/me", headers=headers).json()["capabilities"]
        )
    finally:
        with SessionLocal() as db:
            student = db.query(Student).filter(Student.user_id == stu.user_id).one_or_none()
            if student is not None:
                student.mentor_id = None
            db.commit()
            row = db.get(Mentor, mentor_row_id)
            if row is not None:
                db.delete(row)
                db.commit()


@requires_db
def test_governance_is_nobodys_until_it_is_granted(client, admin, mentor, make_user, cleanup) -> None:
    """Nobody but the Main Admin reaches Governance without being given it.

    WAS `test_governance_is_the_main_admins_alone`, AND THE REFUSAL MOVED ONE
    GATE (B2.6). The router asked `require_admin` and answered "Main Admin
    only"; it now asks for the `admin.governance` capability, which the Main
    Admin holds by baseline and nobody else holds at all. The DEFAULT is
    therefore unchanged — that is what these assertions still pin — and what is
    new is that the office can appoint a deputy instead of sharing its mailbox
    when it is unreachable. The old assertion described a gate that no longer
    exists; deleting it would leave nothing pinning that faculty start with
    none of this.

    The two refusals differ on purpose and both are asserted. A MENTOR is staff,
    so it passes the role floor and is turned away by the capability, named, so
    they know what to ask for. A DIRECTOR is not staff at all any more
    (tests/test_no_director_privilege.py) and is refused by the floor before a
    database read.

    tests/test_governance_delegation.py is where the deputy path is proven.
    """
    director = make_user(f"gov-dir-{uuid.uuid4().hex[:4]}", Role.DIRECTOR)
    body = {"capability": "admin.analytics", "user_ids": [mentor.user_id], "reason": REASON}

    r = client.post(f"{GOV}/grants", headers=mentor.headers, json=body)
    assert r.status_code == 403 and "Governance" in r.text, r.text
    assert client.get(f"{GOV}/catalogue", headers=mentor.headers).status_code == 403

    r = client.post(f"{GOV}/grants", headers=director.headers, json=body)
    assert r.status_code == 403, r.text
    assert client.get(f"{GOV}/catalogue", headers=director.headers).status_code == 403

    r = client.post(f"{GOV}/grants", headers=admin.headers, json=body)
    assert r.status_code == 201, r.text
    cleanup["grants"] += [g["id"] for g in r.json()]
