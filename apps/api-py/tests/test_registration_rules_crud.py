"""B11.3 — writing seating rules, which is writing the thing that mints accounts.

WHY THIS IS A SEPARATE MODULE FROM `test_registration_rules.py`. That one is
pure: it constructs a transient `RegistrationRule` and asks `_rule_matches` what
it decides, with no database anywhere. Everything here needs a real session, a
real capability grant and a real spine to hang a rule on, so it is marked
`@requires_db` and kept apart rather than making the pure module conditional.

WHAT THESE TESTS ARE ABOUT. A rule with `auto_approve` and a batch is the one
control in the product that seats a student without a human, and it is now
typed into a form. So the tests are named after the failures that form could
produce: a pattern that takes the public endpoint down, a wildcard that approves
everybody, a rule written into a college the author cannot reach, an edit that
moves one out of reach, and a delete that quietly unlinks the applications it
routed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, func, select

from app.db import SessionLocal
from app.models.redesign import AuditEvent
from app.models.cohort import Cohort
from app.models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from app.models.institution import STATUS_ACTIVE, College, Department
from app.models.job import DegreeLevel
from app.models.registration import Registration, RegistrationRule, RegistrationStatus
from app.models.user import Role
from app.routers.registration import MAX_USN_PATTERN_LENGTH, _rule_matches, _usn_matcher

from conftest import requires_db


# --------------------------------------------------------------- fixtures --


@pytest.fixture
def spine():
    """A college, a department under it and a batch under that — torn down in
    the order the foreign keys demand (no `ondelete` on `departments.college_id`
    is deliberate, so a leaked department would refuse the college's delete and
    the NEXT run would fail on a name collision instead of here)."""
    colleges: list[str] = []
    departments: list[str] = []
    cohorts: list[str] = []

    def _make() -> tuple[str, str, str]:
        tag = uuid.uuid4().hex[:6]
        with SessionLocal() as db:
            college = College(code=f"R{tag.upper()}", name="Rules College", status=STATUS_ACTIVE)
            db.add(college)
            db.flush()
            department = Department(college_id=college.id, code=f"D{tag.upper()}", name="Rules Dept")
            db.add(department)
            db.flush()
            cohort = Cohort(
                code=f"RULES-{tag.upper()}",
                name="Rules Batch",
                batch_label="2026-28",
                department_id=department.id,
                degree_level=DegreeLevel.PG,
                start_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
                end_date=datetime(2028, 7, 31, tzinfo=timezone.utc),
            )
            db.add(cohort)
            db.commit()
            colleges.append(college.id)
            departments.append(department.id)
            cohorts.append(cohort.id)
            return college.id, department.id, cohort.id

    yield _make

    with SessionLocal() as db:
        db.execute(delete(Cohort).where(Cohort.id.in_(cohorts)))
        db.execute(delete(Department).where(Department.id.in_(departments)))
        db.execute(delete(College).where(College.id.in_(colleges)))
        db.commit()


@pytest.fixture
def scoped_grant():
    """One capability, scoped to one rung, taken back afterwards.

    A row rather than `POST /api/admin/governance/grants`, for the reason
    test_registration_hold.py gives: `admin.registrations` is `carries_pii`, so
    a deputy's API-made grant lands `pending_approval` and holds NOTHING while
    the Main Admin's is live at once — a fixture that depended on who granted
    would make every scope test about the approval rule instead.
    """
    made: list[str] = []

    def _grant(user_id: str, key: str, level: ScopeLevel, target_id: str) -> str:
        with SessionLocal() as db:
            row = CapabilityGrant(
                capability=key,
                subject_kind=SubjectKind.USER,
                subject_user_id=user_id,
                scope_level=level,
                scope_id=target_id,
                reason="the rules CRUD tests need a grant that reaches one college only",
            )
            db.add(row)
            db.commit()
            made.append(row.id)
            return row.id

    yield _grant

    with SessionLocal() as db:
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.id.in_(made)))
        db.commit()


@pytest.fixture
def rules():
    """Every rule this module writes, removed afterwards however the test ends.

    A leaked rule is worse here than a leaked row of most kinds: `_pick_rule`
    reads EVERY enabled rule on every public submission, so one left behind with
    a wildcard condition silently changes what the rest of the suite's
    registrations do.
    """
    made: list[str] = []

    def _track(rule_id: str) -> str:
        made.append(rule_id)
        return rule_id

    yield _track

    with SessionLocal() as db:
        db.execute(delete(RegistrationRule).where(RegistrationRule.id.in_(made)))
        db.commit()


def _post(client, admin, body: dict):
    return client.post("/api/register/rules", headers=admin.headers, json=body)


def _audit_rows(entity_id: str) -> list[AuditEvent]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.entity_type == "registration_rule", AuditEvent.entity_id == entity_id)
                .order_by(AuditEvent.occurred_at)
            ).all()
        )


# ------------------------------------------------- rule-write defence #1 --


@requires_db
def test_a_quantified_group_is_refused_before_it_can_reach_the_public_endpoint(
    client, make_user, rules
):
    """`(a+)+$` is the ReDoS shape, and the only person who can fix it is the
    author — so the refusal is theirs, in their own words, and nothing is
    stored."""
    admin = make_user("rule-redos", Role.ADMIN)
    r = _post(client, admin, {"name": "Catastrophic", "usn_pattern": r"^(1BG2[0-9]+)+$"})
    assert r.status_code == 422, r.text
    assert "quantifier" in r.json()["detail"]
    with SessionLocal() as db:
        assert db.scalar(select(RegistrationRule).where(RegistrationRule.name == "Catastrophic")) is None


@requires_db
def test_an_uncompilable_pattern_comes_back_as_the_compiler_saw_it(client, make_user):
    admin = make_user("rule-badregex", Role.ADMIN)
    r = _post(client, admin, {"name": "Broken", "usn_pattern": "^1BG2["})
    assert r.status_code == 422, r.text
    assert "not a valid regular expression" in r.json()["detail"]


@requires_db
def test_a_pattern_longer_than_the_cap_is_refused_with_the_cap_in_the_message(client, make_user):
    admin = make_user("rule-longregex", Role.ADMIN)
    r = _post(client, admin, {"name": "Long", "usn_pattern": "a" * (MAX_USN_PATTERN_LENGTH + 1)})
    assert r.status_code == 422, r.text
    assert str(MAX_USN_PATTERN_LENGTH) in r.json()["detail"]


@requires_db
def test_the_same_refusal_applies_to_an_edit_not_only_to_a_create(client, make_user, rules):
    """PATCH is the path an author actually uses to fix a pattern, so it is the
    path a bad one arrives on."""
    admin = make_user("rule-patchregex", Role.ADMIN)
    created = _post(client, admin, {"name": "Editable", "usn_pattern": r"^1BG2[0-9]MBA[0-9]{3}$"})
    assert created.status_code == 201, created.text
    rule_id = rules(created.json()["id"])

    bad = client.patch(
        f"/api/register/rules/{rule_id}", headers=admin.headers, json={"usn_pattern": r"^(ab+)+$"}
    )
    assert bad.status_code == 422, bad.text
    with SessionLocal() as db:
        assert db.get(RegistrationRule, rule_id).usn_pattern == r"^1BG2[0-9]MBA[0-9]{3}$"


@requires_db
def test_an_edited_pattern_takes_effect_with_no_restart(client, make_user, rules):
    """`_usn_matcher` is keyed on the pattern STRING, so a fix is a new cache key.

    This is the property that makes cache invalidation unnecessary, and the
    reason the cache must never be keyed on the rule id: the edit would then be
    invisible until somebody restarted the worker.
    """
    admin = make_user("rule-recompile", Role.ADMIN)
    created = _post(client, admin, {"name": "Recompiled", "usn_pattern": r"^1BG2[0-9]MBA[0-9]{3}$"})
    rule_id = rules(created.json()["id"])
    with SessionLocal() as db:
        rule = db.get(RegistrationRule, rule_id)
        assert _rule_matches(rule, "a@x.com", "1BG24MBA001", DegreeLevel.PG)
        assert not _rule_matches(rule, "a@x.com", "1BG24MCA001", DegreeLevel.PG)

    r = client.patch(
        f"/api/register/rules/{rule_id}", headers=admin.headers, json={"usn_pattern": r"^1BG2[0-9]MCA[0-9]{3}$"}
    )
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        rule = db.get(RegistrationRule, rule_id)
        assert not _rule_matches(rule, "a@x.com", "1BG24MBA001", DegreeLevel.PG)
        assert _rule_matches(rule, "a@x.com", "1BG24MCA001", DegreeLevel.PG)
    # Both patterns are live in the cache; neither was invalidated, and that is
    # the designed behaviour rather than a leak.
    assert _usn_matcher(r"^1BG2[0-9]MBA[0-9]{3}$") is not None


# ------------------------------------------------------- the open door ----


@requires_db
def test_an_auto_approving_rule_with_no_conditions_is_refused(client, make_user):
    """A wildcard that auto-approves is "provision an account for whoever submits
    the form" — one click away on a screen where all three conditions are
    optional inputs."""
    admin = make_user("rule-wildcard", Role.ADMIN)
    r = _post(client, admin, {"name": "Everyone", "auto_approve": True})
    assert r.status_code == 422, r.text
    assert "every application" in r.json()["detail"]


@requires_db
def test_a_catch_all_that_only_routes_to_review_is_allowed(client, make_user, rules):
    """The same rule WITHOUT auto-approve refuses nobody and is useful: it labels
    every unmatched application and sends it to a human."""
    admin = make_user("rule-catchall", Role.ADMIN)
    r = _post(client, admin, {"name": "Everything to review", "auto_approve": False, "priority": 999})
    assert r.status_code == 201, r.text
    rules(r.json()["id"])


@requires_db
def test_an_edit_cannot_turn_a_conditioned_rule_into_an_open_door(client, make_user, rules):
    """Clearing the last condition on an auto-approving rule is the same act as
    creating a wildcard, arrived at one field at a time."""
    admin = make_user("rule-opendoor", Role.ADMIN)
    created = _post(
        client, admin, {"name": "Domain auto", "email_domain": "bgscet.ac.in", "auto_approve": True}
    )
    assert created.status_code == 201, created.text
    rule_id = rules(created.json()["id"])
    r = client.patch(
        f"/api/register/rules/{rule_id}", headers=admin.headers, json={"email_domain": None}
    )
    assert r.status_code == 422, r.text
    with SessionLocal() as db:
        assert db.get(RegistrationRule, rule_id).email_domain == "bgscet.ac.in"


# ------------------------------------------------------------ the values --


@requires_db
def test_the_domain_is_normalised_at_write_time(client, make_user, rules):
    """`_rule_matches` compares against an already-lowercased domain, so a rule
    stored as "@BGSCET.ac.in " would match nobody and nothing would say why."""
    admin = make_user("rule-domain", Role.ADMIN)
    r = _post(client, admin, {"name": "Domain", "email_domain": "@BGSCET.ac.in "})
    assert r.status_code == 201, r.text
    rules(r.json()["id"])
    assert r.json()["email_domain"] == "bgscet.ac.in"


@requires_db
def test_a_whole_address_in_the_domain_field_is_refused(client, make_user):
    admin = make_user("rule-addr", Role.ADMIN)
    r = _post(client, admin, {"name": "Address", "email_domain": "someone@bgscet.ac.in"})
    assert r.status_code == 422, r.text
    assert "part after the @" in r.json()["detail"]


@requires_db
def test_a_batch_that_does_not_exist_is_a_422_and_not_a_500(client, make_user):
    """Left to the foreign key this is an IntegrityError on commit — a 500 that
    takes the audit row down with it, which is the shape of the USN hole GUARD 3
    closed."""
    admin = make_user("rule-nobatch", Role.ADMIN)
    r = _post(client, admin, {"name": "Ghost batch", "cohort_id": uuid.uuid4().hex})
    assert r.status_code == 422, r.text
    assert "does not exist" in r.json()["detail"]


@requires_db
def test_absent_leaves_a_field_alone_and_an_explicit_null_clears_it(client, make_user, rules):
    admin = make_user("rule-patchnull", Role.ADMIN)
    created = _post(
        client,
        admin,
        {
            "name": "Full",
            "email_domain": "bgscet.ac.in",
            "usn_pattern": r"^1BG2[0-9]MBA[0-9]{3}$",
            "degree_level": "PG",
            "priority": 42,
        },
    )
    assert created.status_code == 201, created.text
    rule_id = rules(created.json()["id"])

    # One field named; everything else untouched.
    r = client.patch(f"/api/register/rules/{rule_id}", headers=admin.headers, json={"priority": 7})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["priority"] == 7
    assert body["email_domain"] == "bgscet.ac.in"
    assert body["usn_pattern"] == r"^1BG2[0-9]MBA[0-9]{3}$"
    assert body["degree_level"] == "PG"

    # An explicit null CLEARS — the only way a rule stops requiring a pattern.
    r = client.patch(
        f"/api/register/rules/{rule_id}", headers=admin.headers, json={"usn_pattern": None}
    )
    assert r.status_code == 200, r.text
    assert r.json()["usn_pattern"] is None
    assert r.json()["email_domain"] == "bgscet.ac.in"


@requires_db
def test_a_column_that_cannot_hold_null_refuses_one_rather_than_ignoring_it(
    client, make_user, rules
):
    admin = make_user("rule-nonull", Role.ADMIN)
    created = _post(client, admin, {"name": "Named", "email_domain": "bgscet.ac.in"})
    rule_id = rules(created.json()["id"])
    r = client.patch(f"/api/register/rules/{rule_id}", headers=admin.headers, json={"name": None})
    assert r.status_code == 422, r.text
    assert "cannot be cleared" in r.json()["detail"]


@requires_db
def test_priority_ties_are_settled_by_age_and_an_edit_does_not_rewrite_it(
    client, make_user, rules
):
    """`_pick_rule` orders on (priority, created_at) "so a rule added later can't
    silently outrank an equal". An edit must not move a rule's age, or editing
    the older of two equals would change which one fires."""
    admin = make_user("rule-ties", Role.ADMIN)
    first = _post(client, admin, {"name": "Tie A", "email_domain": "a.example", "priority": 500})
    second = _post(client, admin, {"name": "Tie B", "email_domain": "b.example", "priority": 500})
    a_id, b_id = rules(first.json()["id"]), rules(second.json()["id"])
    born = first.json()["created_at"]

    listed = client.get("/api/register/rules", headers=admin.headers).json()
    order = [row["id"] for row in listed if row["id"] in (a_id, b_id)]
    assert order == [a_id, b_id]

    r = client.patch(f"/api/register/rules/{a_id}", headers=admin.headers, json={"name": "Tie A (edited)"})
    assert r.status_code == 200, r.text
    assert r.json()["created_at"] == born


# ----------------------------------------------------------------- audit --


@requires_db
def test_every_write_reaches_the_audit_trail_including_the_delete(client, make_user, rules):
    """The evidence of a bad rule is an account that exists and an application
    nobody reviewed, so the rule's own words have to survive its deletion."""
    admin = make_user("rule-audit", Role.ADMIN)
    created = _post(client, admin, {"name": "Audited", "email_domain": "bgscet.ac.in"})
    assert created.status_code == 201, created.text
    rule_id = rules(created.json()["id"])

    client.patch(f"/api/register/rules/{rule_id}", headers=admin.headers, json={"priority": 5})
    assert client.delete(f"/api/register/rules/{rule_id}", headers=admin.headers).status_code == 204

    actions = [row.action for row in _audit_rows(rule_id)]
    assert actions == ["CREATED", "UPDATED", "DELETED"]
    trail = _audit_rows(rule_id)
    assert trail[0].after_json["email_domain"] == "bgscet.ac.in"
    assert trail[-1].before_json["name"] == "Audited"

@requires_db
def test_a_save_that_changes_nothing_writes_no_audit_row(client, make_user, rules):
    """A trail that records every no-op save is a trail in which the one real
    edit is on page nine."""
    admin = make_user("rule-noop", Role.ADMIN)
    created = _post(client, admin, {"name": "Steady", "email_domain": "bgscet.ac.in", "priority": 60})
    rule_id = rules(created.json()["id"])
    r = client.patch(f"/api/register/rules/{rule_id}", headers=admin.headers, json={"priority": 60})
    assert r.status_code == 200, r.text
    assert [row.action for row in _audit_rows(rule_id)] == ["CREATED"]

@requires_db
def test_deleting_a_rule_unlinks_the_applications_it_routed_and_counts_them(
    client, make_user, rules
):
    """`registrations.matched_rule_id` is ON DELETE SET NULL, so the queue's
    "Rule" column goes blank on rows a rule certainly did match. The count on
    the audit row is the only place that fact survives."""
    admin = make_user("rule-unlink", Role.ADMIN)
    created = _post(client, admin, {"name": "Routed by me", "email_domain": "bgscet.ac.in"})
    rule_id = rules(created.json()["id"])
    email = f"rule.unlink.{uuid.uuid4().hex[:8]}@bgscet.ac.in"
    with SessionLocal() as db:
        reg = Registration(
            name="Routed Applicant",
            email=email,
            degree_level=DegreeLevel.PG,
            status=RegistrationStatus.PENDING_REVIEW,
            matched_rule_id=rule_id,
        )
        db.add(reg)
        db.commit()
        reg_id = reg.id

    assert client.delete(f"/api/register/rules/{rule_id}", headers=admin.headers).status_code == 204
    with SessionLocal() as db:
        assert db.get(Registration, reg_id).matched_rule_id is None
    assert _audit_rows(rule_id)[-1].action == "DELETED"

    with SessionLocal() as db:
        db.execute(delete(Registration).where(Registration.id == reg_id))
        db.commit()


# ----------------------------------------------------------------- scope --


@requires_db
def test_a_scoped_author_can_seat_a_rule_in_their_own_college(
    client, make_user, rules, spine, scoped_grant
):
    college_id, _department_id, cohort_id = spine()
    author = make_user("rule-scope-own", Role.MENTOR)
    scoped_grant(author.user_id, "admin.registrations", ScopeLevel.COLLEGE, college_id)
    r = _post(client, author, {"name": "Ours", "email_domain": "bgscet.ac.in", "cohort_id": cohort_id})
    assert r.status_code == 201, r.text
    rules(r.json()["id"])


@requires_db
def test_a_scoped_author_cannot_seat_a_rule_in_another_college_and_nothing_is_written(
    client, make_user, spine, scoped_grant
):
    """403 AND NO ROW. The check runs after the flush — through the list's own
    predicate, so it cannot drift from what the drawer shows — and the session is
    closed without a commit, which rolls the insert back."""
    mine, _dept, _cohort = spine()
    _theirs, _dept2, their_cohort = spine()
    author = make_user("rule-scope-other", Role.MENTOR)
    scoped_grant(author.user_id, "admin.registrations", ScopeLevel.COLLEGE, mine)

    r = _post(client, author, {"name": "Theirs", "email_domain": "x.example", "cohort_id": their_cohort})
    assert r.status_code == 403, r.text
    with SessionLocal() as db:
        assert db.scalar(select(RegistrationRule).where(RegistrationRule.name == "Theirs")) is None


@requires_db
def test_a_scoped_author_cannot_write_a_rule_that_names_no_batch(
    client, make_user, spine, scoped_grant
):
    """A rule with no `cohort_id` hangs under nothing and applies to every
    college — the same "named nothing belongs to the Main Admin" answer
    `registration_scope_clause` gives an unfiled application."""
    college_id, _dept, _cohort = spine()
    author = make_user("rule-scope-wide", Role.MENTOR)
    scoped_grant(author.user_id, "admin.registrations", ScopeLevel.COLLEGE, college_id)
    r = _post(client, author, {"name": "Programme wide", "email_domain": "x.example"})
    assert r.status_code == 403, r.text
    with SessionLocal() as db:
        assert db.scalar(select(RegistrationRule).where(RegistrationRule.name == "Programme wide")) is None


@requires_db
def test_an_edit_cannot_move_a_rule_out_of_the_authors_reach(
    client, make_user, rules, spine, scoped_grant
):
    """Without the second reachability check, re-targeting would be the way to
    hand a rule to another college — or to take it programme-wide, out of
    everybody's reach including the author's."""
    mine, _dept, my_cohort = spine()
    _theirs, _dept2, their_cohort = spine()
    author = make_user("rule-scope-move", Role.MENTOR)
    scoped_grant(author.user_id, "admin.registrations", ScopeLevel.COLLEGE, mine)
    created = _post(client, author, {"name": "Stays", "email_domain": "x.example", "cohort_id": my_cohort})
    assert created.status_code == 201, created.text
    rule_id = rules(created.json()["id"])

    moved = client.patch(
        f"/api/register/rules/{rule_id}", headers=author.headers, json={"cohort_id": their_cohort}
    )
    assert moved.status_code == 403, moved.text
    widened = client.patch(
        f"/api/register/rules/{rule_id}", headers=author.headers, json={"cohort_id": None}
    )
    assert widened.status_code == 403, widened.text
    with SessionLocal() as db:
        assert db.get(RegistrationRule, rule_id).cohort_id == my_cohort


@requires_db
def test_the_list_is_narrowed_the_same_way_the_writes_are_and_says_so(
    client, make_user, rules, spine, scoped_grant
):
    """Decision 3's whole point: a college admin reading rules they cannot edit,
    with nothing saying which is which, was the worst of the three options."""
    college_id, _dept, cohort_id = spine()
    author = make_user("rule-scope-list", Role.MENTOR)
    scoped_grant(author.user_id, "admin.registrations", ScopeLevel.COLLEGE, college_id)
    mine = _post(client, author, {"name": "Mine", "email_domain": "x.example", "cohort_id": cohort_id})
    rules(mine.json()["id"])

    admin = make_user("rule-scope-admin", Role.ADMIN)
    theirs = _post(client, admin, {"name": "Unseated", "email_domain": "y.example"})
    rules(theirs.json()["id"])

    scoped = client.get("/api/register/rules", headers=author.headers)
    assert scoped.status_code == 200, scoped.text
    assert scoped.headers["X-Reep-Scope"] == "narrowed"
    ids = [row["id"] for row in scoped.json()]
    assert mine.json()["id"] in ids
    assert theirs.json()["id"] not in ids

    unscoped = client.get("/api/register/rules", headers=admin.headers)
    assert unscoped.headers["X-Reep-Scope"] == "programme"
    assert theirs.json()["id"] in [row["id"] for row in unscoped.json()]


@requires_db
def test_a_student_reaches_none_of_the_four_verbs(client, make_user, rules):
    """The capability, not the role, is the gate — but a STUDENT holds neither,
    and every verb must say so rather than only the ones somebody remembered."""
    admin = make_user("rule-rbac-admin", Role.ADMIN)
    created = _post(client, admin, {"name": "Guarded", "email_domain": "bgscet.ac.in"})
    rule_id = rules(created.json()["id"])

    student = make_user("rule-rbac-student", Role.STUDENT)
    assert client.get("/api/register/rules", headers=student.headers).status_code == 403
    assert _post(client, student, {"name": "Sneaky"}).status_code == 403
    assert (
        client.patch(
            f"/api/register/rules/{rule_id}", headers=student.headers, json={"priority": 1}
        ).status_code
        == 403
    )
    assert client.delete(f"/api/register/rules/{rule_id}", headers=student.headers).status_code == 403
    with SessionLocal() as db:
        assert db.get(RegistrationRule, rule_id).priority != 1


@requires_db
def test_a_rule_that_does_not_exist_is_404_on_both_write_verbs(client, make_user):
    admin = make_user("rule-missing", Role.ADMIN)
    missing = uuid.uuid4().hex
    assert (
        client.patch(f"/api/register/rules/{missing}", headers=admin.headers, json={"priority": 1}).status_code
        == 404
    )
    assert client.delete(f"/api/register/rules/{missing}", headers=admin.headers).status_code == 404


@requires_db
def test_the_rule_the_engine_picks_is_the_one_the_screen_wrote(client, make_user, rules, spine):
    """End to end in one test: a rule typed through the API is a rule
    `_pick_rule` evaluates, with the conditions it was given.

    This is the assertion that would fail if a write path stored an
    un-normalised domain or dropped a condition — the failure that otherwise
    shows up as an application quietly falling through to manual review with no
    rule to blame.
    """
    _college, _dept, cohort_id = spine()
    admin = make_user("rule-endtoend", Role.ADMIN)
    created = _post(
        client,
        admin,
        {
            "name": "Typed here",
            "email_domain": "@TYPED.example",
            "usn_pattern": r"^1BG2[0-9]MBA[0-9]{3}$",
            "degree_level": "PG",
            "cohort_id": cohort_id,
            "auto_approve": False,
            "priority": 3,
        },
    )
    assert created.status_code == 201, created.text
    rule_id = rules(created.json()["id"])
    with SessionLocal() as db:
        rule = db.get(RegistrationRule, rule_id)
        assert rule.cohort_id == cohort_id
        assert _rule_matches(rule, "a@typed.example", "1BG24MBA001", DegreeLevel.PG)
        assert not _rule_matches(rule, "a@other.example", "1BG24MBA001", DegreeLevel.PG)
        assert not _rule_matches(rule, "a@typed.example", "1BG24MBA001", DegreeLevel.UG)
        assert (
            db.scalar(
                select(func.count()).select_from(RegistrationRule).where(RegistrationRule.id == rule_id)
            )
            == 1
        )
