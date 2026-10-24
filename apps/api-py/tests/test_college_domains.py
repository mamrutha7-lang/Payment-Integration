"""B1.1 — the domain fence belongs to the college, not to the deployment.

Approving a registration mints a `users` row, and the roster IS the access
control (`app/google_auth.py` refuses a Google account with no matching row).
The fence on that door used to be one environment list applied to every
application whatever college it named. With one college those are the same
sentence; with two, an applicant to college B is admitted on college A's
domain, and nothing in the console would ever show it.

These tests pin the three things that make the change safe rather than merely
correct: a college's own list fences it, a college without one falls back to the
deployment list exactly as before, and the fallback is a fallback and not a
floor — a college that names its domains is not silently widened by the
environment's.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import String, bindparam, delete, select, text
from sqlalchemy.dialects.postgresql import ARRAY

from conftest import requires_db

from app.config import settings
from app.db import SessionLocal
from app.institution_domains import (
    college_id_for_cohort,
    domain_of,
    normalise_domain,
    provisionable_domains_for,
)
from app.models.cohort import Cohort
from app.models.job import DegreeLevel
from app.models.institution import STATUS_ACTIVE, College, Department


def test_a_domain_is_normalised_the_same_way_wherever_it_is_typed():
    """`@BGSCET.ac.in ` and `bgscet.ac.in` are one fence.

    The comparison downstream is exact set membership, so a stray `@` or a
    capital does not loosen the fence — it silently admits NOBODY on that
    domain, and the only symptom is a 422 on somebody's application days later.
    """
    for typed in ("@BGSCET.ac.in", " bgscet.ac.in ", "BGSCET.AC.IN", "@bgscet.ac.in "):
        assert normalise_domain(typed) == "bgscet.ac.in"


def test_the_domain_of_an_address_is_read_the_same_way():
    assert domain_of("Student@BGSCET.ac.in") == "bgscet.ac.in"
    assert domain_of("no-at-sign") == ""
    assert domain_of("") == ""


@pytest.fixture
def college(request):
    """A throwaway college, optionally with its own domain list."""
    made: list[str] = []

    def _make(domains: list[str] | None = None) -> str:
        with SessionLocal() as db:
            row = College(
                code=f"T{uuid.uuid4().hex[:6].upper()}",
                name="Test College",
                status=STATUS_ACTIVE,
                email_domains=domains or [],
            )
            db.add(row)
            db.commit()
            made.append(row.id)
            return row.id

    yield _make

    with SessionLocal() as db:
        db.execute(delete(College).where(College.id.in_(made)))
        db.commit()


@requires_db
def test_a_college_with_no_domains_falls_back_to_the_deployment_list(college):
    """Day one is unchanged. This is the guardrail the whole task hangs on:
    every college that existed before B1.1 is fenced exactly as it was."""
    college_id = college(None)
    with SessionLocal() as db:
        assert provisionable_domains_for(db, college_id) == settings.provisionable_email_domains


@requires_db
def test_a_college_with_its_own_domains_is_fenced_by_those_alone(college):
    """THE FALLBACK IS A FALLBACK, NOT A FLOOR.

    A college that has named its domains is not widened by the deployment's:
    a per-tenant fence the environment can quietly reopen is not a fence. So
    the deployment's own domain must NOT be admitted here unless this college
    also named it.
    """
    college_id = college(["partner.edu"])
    with SessionLocal() as db:
        allowed = provisionable_domains_for(db, college_id)
    assert allowed == frozenset({"partner.edu"})
    for env_domain in settings.provisionable_email_domains:
        if env_domain != "partner.edu":
            assert env_domain not in allowed, (
                "the environment widened a college that had declared its own fence"
            )


@requires_db
def test_an_application_naming_no_college_still_has_a_fence(college):
    """The pre-spine case — an application that named no college, a batch with
    no department — answers the deployment list, which is what fenced that
    address before colleges had domains at all. Never the empty set: an
    unfenced door here is a stranger's address becoming a roster row."""
    with SessionLocal() as db:
        assert provisionable_domains_for(db, None) == settings.provisionable_email_domains
        assert provisionable_domains_for(db, "no-such-college") == settings.provisionable_email_domains


@requires_db
def test_a_batch_resolves_its_college_through_its_department(college):
    """`cohorts` carries `department_id` and the department carries
    `college_id`; neither is copied onto the cohort, which is the spine's whole
    point. A batch not yet filed under a department answers None rather than
    raising — it is an ordinary state on the incomplete-batches screen."""
    college_id = college(["batch.edu"])
    with SessionLocal() as db:
        dept = Department(college_id=college_id, name="Dept", code=f"D{uuid.uuid4().hex[:4]}")
        db.add(dept)
        db.flush()
        tag = uuid.uuid4().hex[:6]
        seated = Cohort(
            code=f"SEAT-{tag}", name="Seated", batch_label="2026-28",
            degree_level=DegreeLevel.PG, department_id=dept.id,
            start_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end_date=datetime(2028, 7, 31, tzinfo=timezone.utc),
        )
        unfiled = Cohort(
            code=f"UNFI-{tag}", name="Unfiled", batch_label="2026-28",
            degree_level=DegreeLevel.PG,
            start_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end_date=datetime(2028, 7, 31, tzinfo=timezone.utc),
        )
        db.add_all([seated, unfiled])
        db.commit()
        seated_id, unfiled_id, dept_id = seated.id, unfiled.id, dept.id

    try:
        with SessionLocal() as db:
            assert college_id_for_cohort(db, seated_id) == college_id
            assert college_id_for_cohort(db, unfiled_id) is None
            assert college_id_for_cohort(db, None) is None
            # ...and the fence follows the join, which is the point of resolving
            # it this way rather than storing a college on the batch.
            assert provisionable_domains_for(db, college_id_for_cohort(db, seated_id)) == frozenset(
                {"batch.edu"}
            )
    finally:
        with SessionLocal() as db:
            db.execute(delete(Cohort).where(Cohort.id.in_([seated_id, unfiled_id])))
            db.execute(delete(Department).where(Department.id == dept_id))
            db.commit()


@requires_db
def test_the_backfill_records_the_deployments_fence_onto_a_pre_existing_college():
    """The backfill records a fact rather than inventing one: every college that
    existed when B1.1 landed was fenced by the environment list, because that
    was the only fence there had ever been. Writing it down is what makes the
    Colleges screen show the real fence instead of "none recorded", which reads
    as "no fence at all".

    THE PRECONDITION IS RECONSTRUCTED, NOT OBSERVED, AND THAT IS THE WHOLE
    LESSON OF THIS TEST. It was first written to read whichever colleges happen
    to be on the database and assert that at least one carried the fence. That
    passed on a developer's machine — whose database HAD colleges when
    `a1f4c7d92e08` ran — and failed on CI forever, because CI builds a database
    from nothing: `alembic upgrade head` runs against an EMPTY `colleges` table,
    so the backfill's UPDATE matches zero rows, and every college the seed then
    creates takes the column's `'{}'` server default. The assertion was about a
    population CI does not have and cannot be given, and no amount of re-running
    would have changed it.

    So this creates a college in exactly the state a pre-B1.1 row was in when the
    migration reached it — the column at its server default — and runs the
    migration's own statement against it. Deterministic on any database, and it
    exercises the SQL rather than an after-effect of it.
    """
    domains = sorted(settings.provisionable_email_domains)
    if not domains:
        pytest.skip(
            "this environment names no provisionable domain, so the migration's "
            "own `if domains:` guard skips the UPDATE too — there is no backfill "
            "to check, which is correct rather than untested"
        )

    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        college = College(code=f"BF{tag.upper()}", name="Backfill College", status=STATUS_ACTIVE)
        db.add(college)
        db.commit()
        college_id = college.id

    try:
        with SessionLocal() as db:
            fresh = db.get(College, college_id)
            assert fresh.email_domains == [], (
                "a new college must start with an empty fence and fall back to "
                "the environment — this is the state the backfill acted on"
            )

        # The migration's statement, in the shape `a1f4c7d92e08` runs it. Scoped
        # to this row by id ONLY because the suite shares a database with other
        # tests; the migration itself deliberately carries no WHERE, and its
        # docstring says why — a migration whose only failure mode is doing
        # nothing is the worst kind to write.
        with SessionLocal() as db:
            db.execute(
                text("UPDATE colleges SET email_domains = :domains WHERE id = :id").bindparams(
                    bindparam("domains", value=domains, type_=ARRAY(String())),
                    bindparam("id", value=college_id),
                )
            )
            db.commit()

        with SessionLocal() as db:
            after = db.get(College, college_id)
            assert set(after.email_domains) == set(domains), (
                "the backfill did not write the environment's fence onto the row"
            )
            assert all(d == normalise_domain(d) for d in after.email_domains), (
                f"the backfill stored an unnormalised domain: {after.email_domains}"
            )
    finally:
        with SessionLocal() as db:
            db.execute(delete(College).where(College.id == college_id))
            db.commit()


@requires_db
def test_no_college_anywhere_holds_a_null_or_unnormalised_fence():
    """The invariant that DOES hold on every database, including a fresh one.

    `email_domains` is NOT NULL with a `'{}'` server default, so "this college
    names no domains of its own" is an empty list and never a NULL — which is
    what lets `provisionable_domains_for` tell that state apart from a fence of
    its own without a second column saying which. A NULL here would make the
    fallback unreachable for exactly the rows that need it.

    THE NOT NULL IS ASSERTED AGAINST THE SCHEMA, NOT AGAINST THE ROWS, and the
    difference came out of mutation-testing this module: deleting a
    `row.email_domains is not None` check left the suite GREEN, because while the
    constraint stands no row can violate it and the assertion can never fail. A
    check that cannot fail is not a check — it reads like one in a diff, which is
    worse than its absence. What this guard is actually for is a future migration
    that relaxes the column, so it asks the catalogue whether the constraint is
    still there. That assertion CAN fail, and the day it does is the day the
    fallback silently stops being reachable.
    """
    with SessionLocal() as db:
        nullable = db.scalar(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'colleges' AND column_name = 'email_domains'"
            )
        )
        assert nullable == "NO", (
            "colleges.email_domains became nullable: an empty fence and an "
            "unrecorded one are now indistinguishable, and the environment "
            "fallback is unreachable for the rows that need it"
        )

        rows = db.scalars(select(College)).all()
        if not rows:
            pytest.skip("no colleges on this database to check normalisation on")
        for c in rows:
            assert all(d == normalise_domain(d) for d in c.email_domains), (
                f"{c.code} holds an unnormalised domain: {c.email_domains}"
            )
