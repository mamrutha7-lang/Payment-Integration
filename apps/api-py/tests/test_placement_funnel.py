"""B12.3 — the funnel, the yearly KPIs and the by-track split.

What each of these holds down:

1. A DASH IS NOT A ZERO. Delete `test_the_stage_nothing_records_is_null_and_says_why`
   and the obvious edit — count `interview_sessions` into `interviewed` — puts a
   REHEARSAL figure in a hiring funnel: a student can sit four mock interviews in
   an afternoon without a recruiter existing, and "38 interviewed, 4 offered"
   would be read by the office as a conversion problem. The same rule protects
   `placement_rate_pct`, which is NULL and not 0.0 when nobody is eligible.
2. A FUNNEL CAPTIONED "DISTINCT STUDENTS" COUNTS STUDENTS. Delete
   `test_the_funnel_counts_students_and_the_offer_counts_stay_beside_it` and
   `offers` (a count of OFFERS) goes back into the funnel, where one student with
   three offers is three people.
3. A MEDIAN IS A STATEMENT ABOUT A SET. Delete
   `test_the_ctc_figures_are_over_approved_priced_offers` and the blanks come
   back in — `ctc_inr` defaults to 0 and the student's offer form does not demand
   it, so the median lands on the floor and the screen reports a programme
   placing people for nothing.
4. THE BATCH FILTER CANNOT WIDEN. Delete `test_cohort_id_narrows_within_the_reach`
   and `?cohort_id=` becomes a way to read a batch outside the caller's grant.
5. THE COLUMNS SUM TO THE FUNNEL. Delete `test_the_by_track_split_carries_the_unfiled`
   and students whose batch names no specialization vanish from the split,
   which reads as a smaller programme rather than as unfiled students.
6. THE EXPORT IS ONE EXPORT. Delete `test_the_placement_export_takes_the_batch_filter`
   and B12.3's `offers.csv` gets built as a second route that has to re-implement
   B14's scope, PII drop and receipt.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select, update

from conftest import requires_db

from app.db import SessionLocal
from app.models.cohort import Cohort
from app.models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from app.models.institution import (
    STATUS_ACTIVE,
    AcademicCourse,
    AcademicSpecialization,
    College,
    Department,
)
from app.models.job import DegreeLevel
from app.models.offer import OfferRoleType, OfferStatus, PlacementOffer
from app.models.user import Role, Student, User


@pytest.fixture
def cohort_with_tracks():
    """One college, one course, one specialization and TWO batches under it.

    Two batches because `?cohort_id=` has to be shown narrowing to one of them;
    one specialization plus an unfiled batch because the split has to be shown
    carrying the students nobody has filed.
    """
    tag = uuid.uuid4().hex[:6]
    made: dict[str, str] = {"tag": tag}
    with SessionLocal() as db:
        college = College(code=f"PC{tag.upper()}", name="Placement College", status=STATUS_ACTIVE)
        db.add(college)
        db.flush()
        department = Department(college_id=college.id, name="Placement Dept", code=f"PD{tag}")
        db.add(department)
        db.flush()
        course = AcademicCourse(department_id=department.id, code=f"PM{tag}", name="MBA Placement")
        db.add(course)
        db.flush()
        fin = AcademicSpecialization(course_id=course.id, code="FIN", name="Finance")
        db.add(fin)
        db.flush()

        def batch(code: str, specialization_id: str | None) -> Cohort:
            return Cohort(
                code=f"{code}-{tag}", name=f"Batch {code}", batch_label="2026-28",
                degree_level=DegreeLevel.PG, department_id=department.id,
                course_id=course.id, specialization_id=specialization_id,
                start_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
                end_date=datetime(2028, 7, 31, tzinfo=timezone.utc),
            )

        filed = batch("FIN", fin.id)
        unfiled = batch("GEN", None)
        db.add_all([filed, unfiled])
        db.commit()
        made |= {
            "college": college.id, "department": department.id, "course": course.id,
            "spec": fin.id, "batch_filed": filed.id, "batch_unfiled": unfiled.id,
        }

    yield made

    with SessionLocal() as db:
        batches = [made["batch_filed"], made["batch_unfiled"]]
        db.execute(update(Student).where(Student.cohort_id.in_(batches)).values(cohort_id=None))
        db.execute(delete(Cohort).where(Cohort.id.in_(batches)))
        db.execute(delete(AcademicSpecialization).where(AcademicSpecialization.id == made["spec"]))
        db.execute(delete(AcademicCourse).where(AcademicCourse.id == made["course"]))
        db.execute(delete(Department).where(Department.id == made["department"]))
        db.execute(delete(College).where(College.id == made["college"]))
        db.commit()


@pytest.fixture
def offers():
    """Write placement offers straight to the table and take them away again."""
    made: list[str] = []

    def _offer(student_id: str, *, status: OfferStatus, ctc: int = 0, organisation="Acme",
               created_at: datetime | None = None) -> str:
        with SessionLocal() as db:
            row = PlacementOffer(
                student_id=student_id,
                role_type=OfferRoleType.FULL_TIME,
                job_title="Analyst",
                organisation=organisation,
                ctc_inr=ctc,
                status=status,
            )
            db.add(row)
            db.flush()
            if created_at is not None:
                row.created_at = created_at
            db.commit()
            made.append(row.id)
            return row.id

    yield _offer

    with SessionLocal() as db:
        db.execute(delete(PlacementOffer).where(PlacementOffer.id.in_(made)))
        db.commit()


def _student_id(user_id: str) -> str:
    with SessionLocal() as db:
        return db.scalar(select(Student.id).where(Student.user_id == user_id))


def _seat(student_id: str, cohort_id: str) -> None:
    with SessionLocal() as db:
        db.get(Student, student_id).cohort_id = cohort_id
        db.commit()


@pytest.fixture
def scoped_grant():
    made: list[str] = []

    def _grant(user_id: str, key: str, level: ScopeLevel, target_id: str) -> str:
        with SessionLocal() as db:
            row = CapabilityGrant(
                capability=key, subject_kind=SubjectKind.USER, subject_user_id=user_id,
                scope_level=level, scope_id=target_id,
                reason="the placement funnel test needs a scoped grant, twenty plus",
            )
            db.add(row)
            db.commit()
            made.append(row.id)
            return row.id

    yield _grant

    with SessionLocal() as db:
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.id.in_(made)))
        db.commit()


def _placement(client, headers, **params) -> dict:
    r = client.get("/api/admin/placement", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


@requires_db
def test_the_stage_nothing_records_is_null_and_says_why(client, make_user):
    admin = make_user("funnel-gap-admin", Role.ADMIN)
    body = _placement(client, admin.headers)

    assert body["interviewed"] is None
    gaps = {gap["stage"]: gap["reason"] for gap in body["unavailable"]}
    assert "interviewed" in gaps
    # The reason is in words, on the payload, so the screen can print it rather
    # than the client inventing an explanation for a dash.
    assert "rehearsal" in gaps["interviewed"].lower()
    # "Shortlisted" is not even named: a stage nothing will ever count is not a
    # gap, it is a feature nobody asked for.
    assert "shortlisted" not in gaps


@requires_db
def test_the_funnel_counts_students_and_the_offer_counts_stay_beside_it(
    client, make_user, cohort_with_tracks, offers
):
    spine = cohort_with_tracks
    admin = make_user("funnel-count-admin", Role.ADMIN)
    student = make_user("funnel-count-stud", Role.STUDENT)
    sid = _student_id(student.user_id)
    _seat(sid, spine["batch_filed"])

    # ONE student, THREE approved offers. The funnel must say one.
    for n in range(3):
        offers(sid, status=OfferStatus.APPROVED, ctc=600000 + n, organisation=f"Acme {n}")

    body = _placement(client, admin.headers, cohort_id=spine["batch_filed"])
    assert body["eligible"] == 1
    assert body["offered_students"] == 1
    assert body["approved_students"] == 1
    # ...while the offer counts, which the status donut is arithmetic over, are
    # still three.
    assert body["offers"] == 3
    assert body["approved"] == 3
    assert body["multiple_offer_students"] == 1
    assert body["placement_rate_pct"] == 100.0


@requires_db
def test_a_rate_over_nobody_is_null_not_zero(client, make_user, cohort_with_tracks):
    admin = make_user("funnel-empty-admin", Role.ADMIN)
    # A batch with nobody in it: "0% placed" and "there is nobody to place" are
    # opposite facts.
    body = _placement(client, admin.headers, cohort_id=cohort_with_tracks["batch_unfiled"])
    assert body["eligible"] == 0
    assert body["placement_rate_pct"] is None
    assert body["median_ctc_inr"] is None
    assert body["highest_ctc_inr"] is None
    assert body["ctc_offers_counted"] == 0


@requires_db
def test_the_ctc_figures_are_over_approved_priced_offers(
    client, make_user, cohort_with_tracks, offers
):
    spine = cohort_with_tracks
    admin = make_user("funnel-ctc-admin", Role.ADMIN)
    a = make_user("funnel-ctc-a", Role.STUDENT)
    b = make_user("funnel-ctc-b", Role.STUDENT)
    c = make_user("funnel-ctc-c", Role.STUDENT)
    for user in (a, b, c):
        _seat(_student_id(user.user_id), spine["batch_filed"])

    offers(_student_id(a.user_id), status=OfferStatus.APPROVED, ctc=400000)
    offers(_student_id(b.user_id), status=OfferStatus.APPROVED, ctc=1200000)
    # A blank CTC — the column's default, and what the student's own form leaves
    # when they do not type one. It must not drag the median to the floor.
    offers(_student_id(c.user_id), status=OfferStatus.APPROVED, ctc=0)
    # A pending offer of a wild number: not approved, so not in the figures.
    offers(_student_id(c.user_id), status=OfferStatus.PENDING_APPROVAL, ctc=9_000_000)

    body = _placement(client, admin.headers, cohort_id=spine["batch_filed"])
    assert body["ctc_offers_counted"] == 2
    assert body["median_ctc_inr"] == 800000
    assert body["highest_ctc_inr"] == 1200000
    # The pending one still counts as an offer HELD, which is the funnel's
    # question, and c holds two.
    assert body["offered_students"] == 3
    assert body["multiple_offer_students"] == 1


@requires_db
def test_the_year_filter_narrows_the_offers_and_lists_the_years(
    client, make_user, cohort_with_tracks, offers
):
    spine = cohort_with_tracks
    admin = make_user("funnel-year-admin", Role.ADMIN)
    student = make_user("funnel-year-stud", Role.STUDENT)
    sid = _student_id(student.user_id)
    _seat(sid, spine["batch_filed"])

    offers(sid, status=OfferStatus.APPROVED, ctc=500000,
           created_at=datetime(2024, 6, 1, tzinfo=timezone.utc))
    offers(sid, status=OfferStatus.APPROVED, ctc=900000,
           created_at=datetime(2026, 6, 1, tzinfo=timezone.utc))

    whole = _placement(client, admin.headers, cohort_id=spine["batch_filed"])
    assert whole["year"] is None
    assert whole["approved"] == 2
    # Newest first, and NOT narrowed by the selection — a Period filter that
    # only offers the year already chosen cannot be used to leave it.
    assert whole["years"][:2] == [2026, 2024]

    one = _placement(client, admin.headers, cohort_id=spine["batch_filed"], year=2024)
    assert one["year"] == 2024
    assert one["approved"] == 1
    assert one["highest_ctc_inr"] == 500000
    assert one["years"][:2] == [2026, 2024]


@requires_db
def test_the_by_track_split_carries_the_unfiled(client, make_user, cohort_with_tracks, offers):
    spine = cohort_with_tracks
    admin = make_user("funnel-track-admin", Role.ADMIN)
    filed = make_user("funnel-track-filed", Role.STUDENT)
    unfiled = make_user("funnel-track-unfiled", Role.STUDENT)
    filed_id = _student_id(filed.user_id)
    _seat(filed_id, spine["batch_filed"])
    _seat(_student_id(unfiled.user_id), spine["batch_unfiled"])
    offers(filed_id, status=OfferStatus.APPROVED, ctc=700000)

    for cohort_id in (spine["batch_filed"], spine["batch_unfiled"]):
        body = _placement(client, admin.headers, cohort_id=cohort_id)
        split = {row["code"]: row for row in body["by_track"]}
        # The split's eligible column sums to the funnel's eligible, whichever
        # batch is asked for — that is what makes it a split rather than a
        # second, smaller programme.
        assert sum(row["eligible"] for row in body["by_track"]) == body["eligible"]
        assert sum(row["placed"] for row in body["by_track"]) == body["approved_students"]

    filed_body = _placement(client, admin.headers, cohort_id=spine["batch_filed"])
    fin = {row["code"]: row for row in filed_body["by_track"]}["FIN"]
    assert fin["name"] == "Finance"
    assert (fin["eligible"], fin["placed"]) == (1, 1)

    unfiled_body = _placement(client, admin.headers, cohort_id=spine["batch_unfiled"])
    nameless = {row["code"]: row for row in unfiled_body["by_track"]}[None]
    assert nameless["name"] == "Not filed"
    assert (nameless["eligible"], nameless["placed"]) == (1, 0)


@requires_db
def test_cohort_id_narrows_within_the_reach(
    client, make_user, cohort_with_tracks, offers, scoped_grant
):
    spine = cohort_with_tracks
    faculty = make_user("funnel-scoped-mentor", Role.MENTOR)
    inside = make_user("funnel-scoped-in", Role.STUDENT)
    outside = make_user("funnel-scoped-out", Role.STUDENT)
    _seat(_student_id(inside.user_id), spine["batch_filed"])
    _seat(_student_id(outside.user_id), spine["batch_unfiled"])

    scoped_grant(faculty.user_id, "admin.placement", ScopeLevel.COHORT, spine["batch_filed"])

    mine = _placement(client, faculty.headers, cohort_id=spine["batch_filed"])
    assert mine["eligible"] == 1

    # A batch the holder does not hold: zeros, never somebody else's figures.
    theirs = _placement(client, faculty.headers, cohort_id=spine["batch_unfiled"])
    assert theirs["eligible"] == 0
    assert theirs["by_track"] == []


@requires_db
def test_the_placement_export_takes_the_batch_filter(
    client, make_user, cohort_with_tracks, offers
):
    """B12.3's `offers.csv` is this file with a parameter, not a second route."""
    spine = cohort_with_tracks
    admin = make_user("funnel-export-admin", Role.ADMIN)
    inside = make_user("funnel-export-in", Role.STUDENT)
    outside = make_user("funnel-export-out", Role.STUDENT)
    in_id, out_id = _student_id(inside.user_id), _student_id(outside.user_id)
    _seat(in_id, spine["batch_filed"])
    _seat(out_id, spine["batch_unfiled"])
    offers(in_id, status=OfferStatus.APPROVED, ctc=500000, organisation="Inside Ltd")
    offers(out_id, status=OfferStatus.APPROVED, ctc=500000, organisation="Outside Ltd")

    # A second endpoint under /admin/placement/ would be a second copy of B14's
    # three rules. There is none, and that is the point.
    assert client.get("/api/admin/placement/offers.csv", headers=admin.headers).status_code == 404

    whole = client.get("/api/admin/exports/placement.csv", headers=admin.headers)
    assert whole.status_code == 200
    assert "Inside Ltd" in whole.text and "Outside Ltd" in whole.text

    narrowed = client.get(
        "/api/admin/exports/placement.csv",
        headers=admin.headers,
        params={"cohort_id": spine["batch_filed"]},
    )
    assert narrowed.status_code == 200
    assert "Inside Ltd" in narrowed.text
    assert "Outside Ltd" not in narrowed.text
