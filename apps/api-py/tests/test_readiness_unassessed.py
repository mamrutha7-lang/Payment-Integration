"""07 §5's guardrail, on the one screen that was breaking it: absence is not zero.

THE DEFECT THIS MODULE PINS. `routers/student.py::_attendance_pct` returned
`0.0` for a student with no `attendance_records` rows, and
`compose_placement_readiness` rendered that as "Attendance 0.0% vs required
75.0%" with `met=False` — a failed check, in the student's own home card, for a
bar nobody had measured them against. `attendance_records` has exactly one
writer (B8.1's spreadsheet import), so on every deployment where that import has
not been run, that was EVERY student. `_cert_completion_pct` had the same shape;
`_live_backlogs` had the mirror image of it, answering 0 — which PASSES — over
no rows at all, so the same student was shown a confident green chip on a fact
nobody had established.

If these tests are deleted, the thing that comes back is not a crash. It is a
number: a student is told they are failing, or that they are fine, on evidence
that does not exist, and neither of those reads as a bug to anyone looking at
the screen.

Five properties, and each fails without the change:

1. A student with nothing on record has `measured=false` on the four imported
   checks, and the score is NULL rather than a low number.
2. A REAL zero still reads as a real zero. The fix is worthless if it makes
   "0% attendance across 40 recorded sessions" indistinguishable from "no
   sessions recorded" in the other direction.
3. The unmeasured checks are excluded from the DENOMINATOR, not just from the
   chip. Dividing by the full weight would keep the old verdict under a new
   label — "67/100 — On track" for a student who met everything anybody ran.
4. A score is WITHHELD entirely below `MIN_SCORED_WEIGHT_SHARE`. Two of the six
   checks read what the student typed and are always measurable, so scoring
   "over the measured weight" alone would put a brand-new student on 0/100 —
   a harsher verdict than the broken arithmetic it replaced, on the same
   absent evidence.
5. The batch reader (`readiness_inputs_many`, B8.5's roll-up) gives the SAME
   answer as the single reader. Two readers of one rule is how a mentor and a
   student end up looking at different numbers, which is the failure
   `mentee_records.py` already carries a warning about.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from conftest import requires_db

from app.db import SessionLocal
from app.models.academics import SemesterResult
from app.models.attendance import AttendanceRecord
from app.models.user import Role, Student, User
from app.routers.student import (
    BAND_NOT_ASSESSED,
    _ReadinessCriteria,
    build_readiness,
    compose_placement_readiness,
    readiness_criteria,
    readiness_inputs_many,
    _readiness_inputs,
)
from app.seed_roster import SSO_ONLY_PASSWORD_HASH

CRITERIA = _ReadinessCriteria(
    min_cgpa=6.0, max_backlogs=0, min_attendance_pct=75.0, min_cert_completion_pct=50.0
)


@pytest.fixture
def blank_student():
    """A student with an account and NOTHING else — no marks, no attendance, no
    certifications. This is the state of every student on a deployment the
    morning after the roster is seeded and before the first import."""
    tag = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            email=f"blank-{tag}@bgscet.ac.in",
            name=f"Blank {tag}",
            role=Role.STUDENT,
            password_hash=SSO_ONLY_PASSWORD_HASH,
        )
        db.add(user)
        db.flush()
        student = Student(user_id=user.id, usn=f"BLANK{tag.upper()}")
        db.add(student)
        db.commit()
        ids = (student.id, user.id)
    yield ids
    with SessionLocal() as db:
        db.execute(delete(Student).where(Student.id == ids[0]))
        db.execute(delete(User).where(User.id == ids[1]))
        db.commit()


@requires_db
def test_a_student_with_nothing_imported_is_not_scored_at_all(blank_student):
    """The headline. Without the fix this student scores 17/100 and is shown a
    red "Attendance 0.0% vs required 75.0%" beside it."""
    student_id, _ = blank_student
    with SessionLocal() as db:
        out = compose_placement_readiness(db, student_id)

    assert out.score is None, (
        "a student with no marks, attendance or certifications was given a "
        "numeric readiness score — that number is a verdict on an assessment "
        "that has not happened"
    )
    assert out.band == BAND_NOT_ASSESSED
    by_label = {f.label: f for f in out.factors}
    for label in ("CGPA", "Live backlogs", "Attendance", "Certification completion"):
        assert by_label[label].measured is False, f"{label} claims to be measured"
    # The two the STUDENT fills in are still measured: an empty profile is an
    # answer, and calling it unknown would excuse a field they can fill today.
    assert by_label["Placement profile"].measured is True
    assert by_label["Resume profile"].measured is True
    # And the sentence a human reads says so rather than naming a percentage.
    assert "0.0%" not in out.summary
    assert "Not enough is on record" in out.summary


@requires_db
def test_the_unmeasured_checks_are_out_of_the_denominator_too(blank_student):
    """Excluding them from the chip but not from the arithmetic keeps the old
    verdict under a new label.

    The student here has marks on record (so the score is reported at all —
    `MIN_SCORED_WEIGHT_SHARE`) and has filled in their own two checks, but
    nothing has imported their attendance or put them on a certification. Over
    the MEASURED weight that is 8/8; over the full weight it would be 8/12 = 67,
    which would draw "On track" for a student who has met every check anybody
    has actually run.
    """
    student_id, _ = blank_student
    from dataclasses import replace

    with SessionLocal() as db:
        inputs = _readiness_inputs(db, student_id)

    out = build_readiness(
        replace(inputs, cgpa=8.1, backlogs=0, has_contacts=True, resume_pct=100),
        CRITERIA,
    )
    assert out.score == 100, (
        "the score was taken over the full weight, so a student who has met "
        "every check that CAN be scored is still shown as short of Ready"
    )
    assert out.band == "Ready"
    assert "not measured yet" in out.summary


@requires_db
def test_a_score_is_withheld_until_enough_is_on_record(blank_student):
    """`MIN_SCORED_WEIGHT_SHARE`. Two of the six checks read what the STUDENT
    typed and are always measurable, so "score over the measured weight" alone
    would put a brand-new student on 0/100 — a harsher verdict than the broken
    arithmetic it replaced, on the same absent evidence."""
    student_id, _ = blank_student
    from dataclasses import replace

    with SessionLocal() as db:
        inputs = _readiness_inputs(db, student_id)

    nothing = build_readiness(inputs, CRITERIA)
    assert nothing.score is None
    assert nothing.band == BAND_NOT_ASSESSED
    assert "0/100" not in nothing.summary
    # The four checks nobody has data for are NAMED, so a mentor reading the
    # same card knows what to chase.
    assert "attendance" in nothing.summary.lower()

    # Attendance alone is 2 of 12 on top of the 2 that are always measurable —
    # a third of the evidence, still not a score.
    assert build_readiness(replace(inputs, attendance_pct=91.0), CRITERIA).score is None
    # Marks carry 6 and cross it.
    assert build_readiness(replace(inputs, cgpa=7.0, backlogs=0), CRITERIA).score is not None


@requires_db
def test_no_attendance_and_bad_attendance_do_not_render_the_same(blank_student):
    """The two states the old code collapsed into one."""
    student_id, _ = blank_student
    with SessionLocal() as db:
        nothing = compose_placement_readiness(db, student_id)
        at = datetime.now(timezone.utc) - timedelta(days=3)
        db.add_all(
            [
                AttendanceRecord(
                    student_id=student_id,
                    course_code="22MBA11",
                    session_no=n,
                    session_date=at,
                    present=False,
                )
                for n in range(1, 5)
            ]
        )
        db.commit()
        try:
            absent = compose_placement_readiness(db, student_id)
        finally:
            db.execute(
                delete(AttendanceRecord).where(AttendanceRecord.student_id == student_id)
            )
            db.commit()

    none_att = next(f for f in nothing.factors if f.label == "Attendance")
    zero_att = next(f for f in absent.factors if f.label == "Attendance")
    assert none_att.measured is False and zero_att.measured is True
    assert none_att.detail != zero_att.detail
    assert "0.0%" in zero_att.detail, "a real 0% must still say 0%"
    assert zero_att.met is False
    assert "not recorded" not in zero_att.detail.lower()

    # And a real zero DOES pull the score down, which is the whole point of
    # keeping the two apart. Asserted on the rule directly, because attendance
    # alone does not put a student over `MIN_SCORED_WEIGHT_SHARE`.
    from dataclasses import replace

    with SessionLocal() as db:
        inputs = _readiness_inputs(db, student_id)
    scorable = replace(
        inputs, cgpa=9.0, backlogs=0, cert_pct=100.0, has_contacts=True, resume_pct=100
    )
    assert build_readiness(replace(scorable, attendance_pct=None), CRITERIA).score == 100
    assert build_readiness(replace(scorable, attendance_pct=0.0), CRITERIA).score < 100


@requires_db
def test_no_results_is_not_zero_backlogs(blank_student):
    """The mirror image, and the one nobody reports: `sum()` over no rows is 0,
    and 0 backlogs PASSES."""
    student_id, _ = blank_student
    with SessionLocal() as db:
        nothing = compose_placement_readiness(db, student_id)
        db.add(
            SemesterResult(
                student_id=student_id, semester=1, sgpa=7.5, cgpa=7.5, live_backlogs=0
            )
        )
        db.commit()
        try:
            clean = compose_placement_readiness(db, student_id)
        finally:
            db.execute(
                delete(SemesterResult).where(SemesterResult.student_id == student_id)
            )
            db.commit()

    none_back = next(f for f in nothing.factors if f.label == "Live backlogs")
    real_back = next(f for f in clean.factors if f.label == "Live backlogs")
    assert none_back.measured is False
    assert none_back.met is False, (
        "an unmeasured check must not report `met`; a client that has not "
        "learned `measured` would draw a green chip over a fact nobody has"
    )
    assert real_back.measured is True and real_back.met is True
    # CGPA resolves with it — they are the same evidence.
    assert next(f for f in clean.factors if f.label == "CGPA").measured is True


@requires_db
def test_the_batch_reader_agrees_with_the_single_reader(blank_student):
    """B8.5's roll-up must not become a second definition of readiness.

    `compose_readiness_many` exists because six queries per student is 12 000 of
    them for a large deployment. The temptation it replaces is a SQL expression
    that decides readiness itself — a second rule, in a second language, that
    disagrees with the student's own card the first time either is edited.
    """
    student_id, _ = blank_student
    with SessionLocal() as db:
        db.add(
            SemesterResult(
                student_id=student_id, semester=1, sgpa=6.4, cgpa=6.4, live_backlogs=2
            )
        )
        db.add(
            AttendanceRecord(
                student_id=student_id,
                course_code="22MBA11",
                session_no=1,
                session_date=datetime.now(timezone.utc),
                present=True,
            )
        )
        db.commit()
        try:
            one = _readiness_inputs(db, student_id)
            many = readiness_inputs_many(db, [student_id])[student_id]
            criteria = readiness_criteria(db)
            assert one == many, f"the two readers disagree: {one!r} vs {many!r}"
            assert build_readiness(one, criteria) == build_readiness(many, criteria)
        finally:
            db.execute(
                delete(SemesterResult).where(SemesterResult.student_id == student_id)
            )
            db.execute(
                delete(AttendanceRecord).where(AttendanceRecord.student_id == student_id)
            )
            db.commit()


@requires_db
def test_the_batch_reader_agrees_on_a_student_with_nothing(blank_student):
    """The empty case is the one a batch reader gets wrong: a LEFT JOIN that
    yields no row is easy to turn into a 0 while the single reader returns None."""
    student_id, _ = blank_student
    with SessionLocal() as db:
        one = _readiness_inputs(db, student_id)
        many = readiness_inputs_many(db, [student_id])[student_id]
    assert one == many
    assert many.attendance_pct is None and many.cert_pct is None
    assert many.cgpa is None and many.backlogs is None


@requires_db
def test_the_endpoint_carries_the_flag_to_the_client(client, make_user):
    """The whole point is that the CLIENT can tell the two apart, so the flag has
    to survive the response model. A default of `True` on the field means a
    missing `measured` would look like "measured" — which is why this asserts on
    the JSON and not on the Python object."""
    student = make_user("readiness-flag", Role.STUDENT)
    r = client.get("/api/student/placement-readiness", headers=student.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "score" in body
    assert body["score"] is None, "a brand-new student was given a readiness score"
    assert body["band"] == BAND_NOT_ASSESSED
    assert all("measured" in f for f in body["factors"])
    attendance = next(f for f in body["factors"] if f["label"] == "Attendance")
    assert attendance["measured"] is False
    assert "0.0%" not in attendance["detail"]
