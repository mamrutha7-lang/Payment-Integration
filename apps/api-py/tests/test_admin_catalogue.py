"""B13 — the catalogue, per college and per course (`app/routers/admin_catalogue.py`).

WHAT EACH GROUP HOLDS DOWN, and what comes back if it is deleted:

1. THE CATALOGUE OPENS ON THE GRANT. The three approved-certification handlers
   were `require_admin` while the screen that draws them opened on the grantable
   `admin.catalogue`, and `catalogue.component.ts` carries a signal and a
   paragraph of copy apologising for the split. Delete
   `test_a_granted_faculty_member_reads_the_certification_catalogue` and the
   move silently reverts to Main-Admin-only with the screen still claiming
   otherwise.

2. THE LISTS ARE NARROWED, AND PROGRAMME-WIDE ROWS SURVIVE THE NARROWING.
   These two pull in opposite directions and both matter. Delete
   `test_a_scoped_holder_sees_their_own_course_and_not_the_other_college` and a
   college-scoped `admin.catalogue` grant reads another college's catalogue.
   Delete `test_a_programme_wide_row_is_in_every_narrowed_list` and the first
   scoped grant ever made empties the certification grid, because every row
   written before B13 has NULL on both pointers — a screen that goes quiet
   rather than refusing, which nobody reports as a permissions bug.

3. THE WRITES ARE FENCED IN BOTH DIRECTIONS. Delete
   `test_a_narrowed_holder_cannot_write_a_programme_wide_certification` and a
   holder scoped to one college publishes rows every other college sees; delete
   `test_a_narrowed_holder_cannot_edit_a_row_outside_their_reach` and the
   narrowing becomes decoration, because a caller who cannot see a row can still
   move it into their own reach by typing its id.

4. ABSENCE MEANS ENABLED, AND THE TABLE ACTUALLY GOVERNS SOMETHING. Delete
   `test_absence_means_enabled` and the migration has to write 48 rows per
   course and a NEW course starts with no badges at all, silently. Delete
   `test_a_disabled_badge_leaves_the_students_own_dashboard` and
   `badge_course_map` is a table the console writes and nothing reads — a row
   that gates nothing, which B2.1 deleted ten capability keys for being.

5. COPY IS ADDITIVE AND SAYS WHAT IT SKIPPED. Delete
   `test_copy_is_additive_and_counts_what_it_skipped` and "copy the MBA's stage
   rules across" silently discards the MCA's, from a button that says "copy".

6. THE SUBJECT IMPORT DRY-RUNS BY DEFAULT AND REFUSES A SCOPED HOLDER.
   `courses.code` is a GLOBAL primary key: two colleges cannot both have
   22MBA11, so a subject is programme-wide by construction. Delete
   `test_the_subject_import_refuses_a_scoped_holder` and a college-scoped grant
   writes rows the whole deployment then sees, with nothing on screen saying so.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app.db import SessionLocal
from app.models.badge import ApprovedCertification
from app.models.catalogue import BadgeCourseMap, StageRule
from app.models.cohort import Cohort
from app.models.course import Course
from app.models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from app.models.institution import STATUS_ACTIVE, AcademicCourse, College, Department
from app.models.job import DegreeLevel
from app.models.user import Role, Student

pytestmark = requires_db

#: Two real codes off the code-defined catalogue. Real ones, because the API
#: validates every `badge_code` against `BADGE_BY_CODE` at the edge.
BADGE_A = "MGR-BUSINESS-COMMUNICATION"
BADGE_B = "MGR-PRESENTATION-SKILLS"


def _code(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8].upper()}"


# ------------------------------------------------------------------ spine --


@pytest.fixture
def spine():
    """Two colleges, one department and one programme in each, plus a batch.

    The smallest shape in which "only your own college's catalogue" can fail:
    one college cannot show a fence and one course cannot show a copy.

    Three certifications: one programme-wide (NULL on both pointers, which is
    what every row written before B13 is), one pinned to HERE's course and one
    to THERE's.
    """
    tag = uuid.uuid4().hex[:6]
    made: dict[str, str] = {"tag": tag}
    with SessionLocal() as db:
        here_college = College(code=_code("CH"), name=f"Here College {tag}", status=STATUS_ACTIVE)
        there_college = College(code=_code("CT"), name=f"There College {tag}", status=STATUS_ACTIVE)
        db.add_all([here_college, there_college])
        db.flush()

        here_dept = Department(college_id=here_college.id, name="Here Dept", code=_code("DH"))
        there_dept = Department(college_id=there_college.id, name="There Dept", code=_code("DT"))
        db.add_all([here_dept, there_dept])
        db.flush()

        here_course = AcademicCourse(
            department_id=here_dept.id, code=_code("MBA"), name="Here MBA", duration_months=24
        )
        # A SECOND programme in the SAME department, so `copy` can run entirely
        # inside one reach — otherwise every copy test would also be a scope test.
        here_other = AcademicCourse(
            department_id=here_dept.id, code=_code("MCA"), name="Here MCA", duration_months=24
        )
        there_course = AcademicCourse(
            department_id=there_dept.id, code=_code("MBA"), name="There MBA", duration_months=24
        )
        db.add_all([here_course, here_other, there_course])
        db.flush()

        batch = Cohort(
            code=_code("B"), name=f"Batch {tag}", batch_label="2026-28",
            degree_level=DegreeLevel.PG, department_id=here_dept.id, course_id=here_course.id,
            start_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end_date=datetime(2028, 7, 31, tzinfo=timezone.utc),
        )
        db.add(batch)
        db.flush()

        wide = ApprovedCertification(
            name=f"Everywhere {tag}", provider="Coursera", badge_code=BADGE_A
        )
        mine = ApprovedCertification(
            name=f"Here only {tag}", provider="NPTEL", badge_code=BADGE_A,
            college_id=here_college.id, course_id=here_course.id,
        )
        theirs = ApprovedCertification(
            name=f"There only {tag}", provider="edX", badge_code=BADGE_B,
            college_id=there_college.id, course_id=there_course.id,
        )
        db.add_all([wide, mine, theirs])
        db.commit()

        made |= {
            "here_college": here_college.id, "there_college": there_college.id,
            "here_dept": here_dept.id, "there_dept": there_dept.id,
            "here_course": here_course.id, "here_other": here_other.id,
            "there_course": there_course.id, "batch": batch.id,
            "cert_wide": wide.id, "cert_mine": mine.id, "cert_theirs": theirs.id,
        }

    yield made

    with SessionLocal() as db:
        courses = [made["here_course"], made["here_other"], made["there_course"]]
        db.execute(delete(StageRule).where(StageRule.course_id.in_(courses)))
        db.execute(delete(BadgeCourseMap).where(BadgeCourseMap.course_id.in_(courses)))
        db.execute(
            delete(ApprovedCertification).where(
                ApprovedCertification.course_id.in_(courses)
                | ApprovedCertification.id.in_(
                    [made["cert_wide"], made["cert_mine"], made["cert_theirs"]]
                )
            )
        )
        db.execute(
            Student.__table__.update()
            .where(Student.cohort_id == made["batch"])
            .values(cohort_id=None)
        )
        db.execute(delete(Cohort).where(Cohort.id == made["batch"]))
        db.execute(delete(AcademicCourse).where(AcademicCourse.id.in_(courses)))
        db.execute(delete(Department).where(Department.id.in_([made["here_dept"], made["there_dept"]])))
        db.execute(
            delete(College).where(College.id.in_([made["here_college"], made["there_college"]]))
        )
        db.commit()


@pytest.fixture
def scoped_grant():
    """One capability, scoped to one rung, written as a row and taken back.

    A row rather than `POST /api/admin/governance/grants` for the reason
    test_scoped_lists.py gives: a deputy's API-made grant of a `carries_pii`
    key lands `pending_approval` (B2.4) while the Main Admin's is live at once.
    `admin.catalogue` is not one, but the two tests should not differ in how
    they arrange the same thing.
    """
    made: list[str] = []

    def _grant(user_id: str, key: str, level: ScopeLevel | None, target_id: str | None) -> str:
        with SessionLocal() as db:
            row = CapabilityGrant(
                capability=key, subject_kind=SubjectKind.USER, subject_user_id=user_id,
                scope_level=level, scope_id=target_id,
                reason="the catalogue tests need a scoped grant, twenty characters plus",
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
def admin(make_user):
    return make_user("catalogue-admin", Role.ADMIN)


@pytest.fixture
def faculty(make_user):
    return make_user("catalogue-faculty", Role.MENTOR)


# ------------------------------------------------------------ 1 · the gate --


def test_a_granted_faculty_member_reads_the_certification_catalogue(
    client, admin, faculty, scoped_grant, spine
):
    """The gate is `admin.catalogue`, not "are you the Main Admin".

    Both halves in one test on purpose: an UNGRANTED faculty member is still
    refused, so this is a change of which key opens the door and not a change of
    whether there is one.
    """
    before = client.get("/api/admin/approved-certifications", headers=faculty.headers)
    assert before.status_code == 403

    scoped_grant(faculty.user_id, "admin.catalogue", None, None)
    after = client.get("/api/admin/approved-certifications", headers=faculty.headers)
    assert after.status_code == 200
    names = {row["name"] for row in after.json()}
    assert {f"Everywhere {spine['tag']}", f"Here only {spine['tag']}"} <= names

    # And the Main Admin, who holds it by baseline, is not narrowed.
    mine = client.get("/api/admin/approved-certifications", headers=admin.headers)
    assert mine.status_code == 200
    assert mine.headers["X-Reep-Scope"] == "programme"


def test_a_student_is_refused_every_catalogue_route(client, make_user, spine):
    student = make_user("catalogue-student")
    for method, path in [
        ("get", "/api/admin/approved-certifications"),
        ("get", "/api/admin/catalogue/courses"),
        ("get", f"/api/admin/catalogue/badges?course_id={spine['here_course']}"),
        ("get", "/api/admin/catalogue/stage-rules"),
    ]:
        r = getattr(client, method)(path, headers=student.headers)
        assert r.status_code == 403, f"{path} answered {r.status_code}"


# -------------------------------------------------------- 2 · the narrowing --


def test_a_scoped_holder_sees_their_own_course_and_not_the_other_college(
    client, faculty, scoped_grant, spine
):
    scoped_grant(faculty.user_id, "admin.catalogue", ScopeLevel.COLLEGE, spine["here_college"])
    r = client.get("/api/admin/approved-certifications", headers=faculty.headers)
    assert r.status_code == 200
    assert r.headers["X-Reep-Scope"] == "narrowed"
    ids = {row["id"] for row in r.json()}
    assert spine["cert_mine"] in ids
    assert spine["cert_theirs"] not in ids

    courses = client.get("/api/admin/catalogue/courses", headers=faculty.headers)
    assert courses.status_code == 200
    course_ids = {row["id"] for row in courses.json()}
    assert {spine["here_course"], spine["here_other"]} <= course_ids
    assert spine["there_course"] not in course_ids


def test_a_programme_wide_row_is_in_every_narrowed_list(client, faculty, scoped_grant, spine):
    """NULL on both pointers means "every college", so a narrowed holder sees it.

    This is the one that would go quietly: every certification written before
    B13 has NULLs, so a scope clause that read that pair as "reaches nothing"
    would empty the grid on the day the first scoped grant was made.
    """
    scoped_grant(faculty.user_id, "admin.catalogue", ScopeLevel.DEPARTMENT, spine["here_dept"])
    r = client.get("/api/admin/approved-certifications", headers=faculty.headers)
    assert r.status_code == 200
    rows = {row["id"]: row for row in r.json()}
    assert spine["cert_wide"] in rows
    assert rows[spine["cert_wide"]]["scope_label"] == "Programme-wide"
    assert rows[spine["cert_mine"]]["scope_label"].startswith("Course · ")


def test_a_cohort_scoped_grant_reaches_no_course(client, faculty, scoped_grant, spine):
    """A batch sits BELOW a course, so it is not in a course's ancestry.

    `reaches_target` gives the same answer for a single record; this pins that
    the list agrees with it, rather than the list quietly widening to "narrowed
    by nothing".
    """
    scoped_grant(faculty.user_id, "admin.catalogue", ScopeLevel.COHORT, spine["batch"])
    r = client.get("/api/admin/catalogue/courses", headers=faculty.headers)
    assert r.status_code == 200
    assert r.json() == []
    # And the certification list still shows the programme-wide row, because
    # that row applies to everyone — including a holder who reaches no course.
    certs = client.get("/api/admin/approved-certifications", headers=faculty.headers)
    rows = certs.json()
    assert spine["cert_wide"] in {row["id"] for row in rows}
    assert all(row["scope_label"] == "Programme-wide" for row in rows)


# ----------------------------------------------------------- 3 · the writes --


def _certification_body(tag: str, **extra) -> dict:
    return {
        "name": f"New cert {tag}", "provider": "Provider", "badge_code": BADGE_A,
        **extra,
    }


def test_a_narrowed_holder_cannot_write_a_programme_wide_certification(
    client, faculty, scoped_grant, spine
):
    scoped_grant(faculty.user_id, "admin.catalogue", ScopeLevel.COLLEGE, spine["here_college"])
    wide = client.post(
        "/api/admin/approved-certifications",
        headers=faculty.headers,
        json=_certification_body(spine["tag"]),
    )
    assert wide.status_code == 403
    assert "Name the college or the course" in wide.json()["detail"]

    ok = client.post(
        "/api/admin/approved-certifications",
        headers=faculty.headers,
        json=_certification_body(spine["tag"], course_id=spine["here_course"]),
    )
    assert ok.status_code == 201, ok.text
    # The college was DERIVED from the course, not typed.
    assert ok.json()["college_id"] == spine["here_college"]


def test_a_narrowed_holder_cannot_edit_a_row_outside_their_reach(
    client, faculty, scoped_grant, spine
):
    """Both the row as it stands and the row as it would be.

    Checking only the incoming scope would let a holder move somebody else's
    certification into their own college and edit it there — a fence with a gate
    beside it.
    """
    scoped_grant(faculty.user_id, "admin.catalogue", ScopeLevel.COLLEGE, spine["here_college"])
    steal = client.patch(
        f"/api/admin/approved-certifications/{spine['cert_theirs']}",
        headers=faculty.headers,
        json=_certification_body(spine["tag"], course_id=spine["here_course"]),
    )
    assert steal.status_code == 403

    widen = client.patch(
        f"/api/admin/approved-certifications/{spine['cert_wide']}",
        headers=faculty.headers,
        json=_certification_body(spine["tag"], course_id=spine["here_course"]),
    )
    assert widen.status_code == 403
    assert "programme-wide" in widen.json()["detail"]


def test_a_course_settles_its_own_college_and_a_contradiction_is_422(client, admin, spine):
    """`_resolve_ancestry`'s rule, applied to a catalogue row: the deepest level
    wins and a shallower one that disagrees is refused rather than picked
    between."""
    r = client.post(
        "/api/admin/approved-certifications",
        headers=admin.headers,
        json=_certification_body(
            spine["tag"],
            course_id=spine["here_course"],
            college_id=spine["there_college"],
        ),
    )
    assert r.status_code == 422
    assert "contradicts the course" in r.json()["detail"]


# --------------------------------------------------- 4 · absence is enabled --


def test_absence_means_enabled(client, admin, spine):
    r = client.get(
        f"/api/admin/catalogue/badges?course_id={spine['here_course']}", headers=admin.headers
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 48, "all 48, always — the screen's question is which of them apply"
    assert all(row["enabled"] for row in rows)
    assert not any(row["overridden"] for row in rows)


def test_switching_a_badge_back_on_writes_a_row_rather_than_deleting_one(client, admin, spine):
    off = client.put(
        "/api/admin/catalogue/badges",
        headers=admin.headers,
        json={"course_id": spine["here_course"], "badge_code": BADGE_A, "enabled": False},
    )
    assert off.status_code == 200
    assert off.json() == {**off.json(), "enabled": False, "overridden": True}

    on = client.put(
        "/api/admin/catalogue/badges",
        headers=admin.headers,
        json={"course_id": spine["here_course"], "badge_code": BADGE_A, "enabled": True},
    )
    assert on.status_code == 200
    assert on.json()["enabled"] is True
    # ONE row, updated — not a second one, and not a delete that leaves the
    # catalogue looking as though nobody ever decided anything.
    assert on.json()["overridden"] is True
    with SessionLocal() as db:
        rows = db.scalars(
            select(BadgeCourseMap).where(
                BadgeCourseMap.course_id == spine["here_course"],
                BadgeCourseMap.badge_code == BADGE_A,
            )
        ).all()
    assert len(rows) == 1


def test_a_disabled_badge_leaves_the_students_own_dashboard(client, admin, make_user, spine):
    """The table governs something, or it is a row that gates nothing.

    The student is seated in the batch on HERE's course, so `compose_badges`
    resolves the same course the admin just edited. Their tile count drops by
    one and the badge is gone from the categories — which is the whole point of
    "the 48 are enabled per course".
    """
    student = make_user("catalogue-badge-student")
    with SessionLocal() as db:
        row = db.scalar(select(Student).where(Student.user_id == student.user_id))
        row.cohort_id = spine["batch"]
        db.commit()

    before = client.get("/api/student/badges", headers=student.headers)
    assert before.status_code == 200, before.text
    assert before.json()["badge_total"] == 48

    client.put(
        "/api/admin/catalogue/badges",
        headers=admin.headers,
        json={"course_id": spine["here_course"], "badge_code": BADGE_A, "enabled": False},
    )

    after = client.get("/api/student/badges", headers=student.headers)
    assert after.status_code == 200
    assert after.json()["badge_total"] == 47
    codes = {
        badge["code"]
        for category in after.json()["categories"]
        for badge in category["badges"]
    }
    assert BADGE_A not in codes
    assert BADGE_B in codes


def test_a_certification_pinned_to_another_course_is_not_offered_to_this_student(
    client, make_user, spine
):
    """A student on HERE's course must not be sent to buy THERE's certificate.

    The programme-wide row is still offered, because that is what NULL means.
    """
    student = make_user("catalogue-cert-student")
    with SessionLocal() as db:
        row = db.scalar(select(Student).where(Student.user_id == student.user_id))
        row.cohort_id = spine["batch"]
        db.commit()

    dashboard = client.get("/api/student/badges", headers=student.headers).json()
    offered = {
        cert["name"]
        for category in dashboard["categories"]
        for badge in category["badges"]
        for cert in badge["approved_certifications"]
    }
    assert f"Everywhere {spine['tag']}" in offered
    assert f"Here only {spine['tag']}" in offered
    assert f"There only {spine['tag']}" not in offered


# ------------------------------------------------------------ 5 · the copy --


def test_stage_rules_upsert_rather_than_collide(client, admin, spine):
    """`uq_stage_rule_course_semester` makes a second rule for semester 3 an
    IntegrityError. The unique key IS the identity of the thing being edited, so
    a PUT that changes one's mind must be an UPDATE and not a 500."""
    first = client.put(
        "/api/admin/catalogue/stage-rules",
        headers=admin.headers,
        json={"course_id": spine["here_course"], "semester": 3, "stage": "EXCEL"},
    )
    assert first.status_code == 200, first.text
    again = client.put(
        "/api/admin/catalogue/stage-rules",
        headers=admin.headers,
        json={"course_id": spine["here_course"], "semester": 3, "stage": "ELEVATE"},
    )
    assert again.status_code == 200, again.text
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["stage"] == "ELEVATE"

    listed = client.get(
        f"/api/admin/catalogue/stage-rules?course_id={spine['here_course']}",
        headers=admin.headers,
    )
    assert [r["semester"] for r in listed.json()] == [3]

    gone = client.delete(
        f"/api/admin/catalogue/stage-rules/{first.json()['id']}", headers=admin.headers
    )
    assert gone.status_code == 204


def test_copy_is_additive_and_counts_what_it_skipped(client, admin, spine):
    """Additive, never overwriting, and the skip count is reported.

    "copied 0" and "copied 0, skipped 12" are different answers and only one of
    them means the copy did nothing.
    """
    client.put(
        "/api/admin/catalogue/stage-rules", headers=admin.headers,
        json={"course_id": spine["here_course"], "semester": 1, "stage": "REBOOT"},
    )
    client.put(
        "/api/admin/catalogue/stage-rules", headers=admin.headers,
        json={"course_id": spine["here_course"], "semester": 2, "stage": "EXCEL"},
    )
    # The destination already holds semester 1, with a DIFFERENT stage.
    client.put(
        "/api/admin/catalogue/stage-rules", headers=admin.headers,
        json={"course_id": spine["here_other"], "semester": 1, "stage": "ELEVATE"},
    )
    client.put(
        "/api/admin/catalogue/badges", headers=admin.headers,
        json={"course_id": spine["here_course"], "badge_code": BADGE_A, "enabled": False},
    )

    body = {
        "from_course": spine["here_course"],
        "to_course": spine["here_other"],
        "parts": ["certifications", "badges", "stage_rules"],
    }
    dry = client.post("/api/admin/catalogue/copy?dry_run=true", headers=admin.headers, json=body)
    assert dry.status_code == 200, dry.text
    counts = {p["part"]: (p["copied"], p["skipped"]) for p in dry.json()["parts"]}
    assert counts == {"certifications": (1, 0), "badges": (1, 0), "stage_rules": (1, 1)}

    # A DRY RUN WRITES NOTHING. Without the rollback the destination would
    # already hold them and the real copy below would report "skipped".
    with SessionLocal() as db:
        assert db.scalar(
            select(StageRule).where(StageRule.course_id == spine["here_other"], StageRule.semester == 2)
        ) is None

    real = client.post("/api/admin/catalogue/copy", headers=admin.headers, json=body)
    assert real.status_code == 200, real.text
    assert {p["part"]: (p["copied"], p["skipped"]) for p in real.json()["parts"]} == counts

    # The destination's own semester-1 rule SURVIVED, with its own stage.
    kept = client.get(
        f"/api/admin/catalogue/stage-rules?course_id={spine['here_other']}", headers=admin.headers
    ).json()
    assert {r["semester"]: r["stage"] for r in kept} == {1: "ELEVATE", 2: "EXCEL"}

    # Running it a second time copies nothing and says so.
    twice = client.post("/api/admin/catalogue/copy", headers=admin.headers, json=body)
    assert {p["copied"] for p in twice.json()["parts"]} == {0}


def test_copy_refuses_a_source_outside_the_reach(client, faculty, scoped_grant, spine):
    """Reading one programme to write another is still a read of somebody's
    catalogue, so the SOURCE is fenced as hard as the destination."""
    scoped_grant(faculty.user_id, "admin.catalogue", ScopeLevel.COLLEGE, spine["here_college"])
    r = client.post(
        "/api/admin/catalogue/copy",
        headers=faculty.headers,
        json={
            "from_course": spine["there_course"],
            "to_course": spine["here_course"],
            "parts": ["stage_rules"],
        },
    )
    assert r.status_code == 403


def test_copy_refuses_a_course_onto_itself(client, admin, spine):
    r = client.post(
        "/api/admin/catalogue/copy",
        headers=admin.headers,
        json={
            "from_course": spine["here_course"],
            "to_course": spine["here_course"],
            "parts": ["stage_rules"],
        },
    )
    assert r.status_code == 422


# -------------------------------------------------- 6 · the subject import --


SUBJECT_CSV = (
    "code,name,stage,dimension,semester,model_type,teaching_hours\n"
    "{code_a},Managerial Economics,EXCEL,PROFESSIONAL,1,INSTRUCTOR_LED,40\n"
    "{code_b},Design Thinking,REBOOT,THINKING,1,SUPERVISED_SELF_LEARN,20\n"
)


def _upload(text: str) -> dict:
    return {"file": ("subjects.csv", io.BytesIO(text.encode("utf-8")), "text/csv")}


@pytest.fixture
def imported_codes():
    made: list[str] = []
    yield made
    if made:
        with SessionLocal() as db:
            db.execute(delete(Course).where(Course.code.in_(made)))
            db.commit()


def test_the_subject_import_dry_runs_by_default_and_reports_every_row(
    client, admin, imported_codes
):
    """The default is the dry run, unlike every other write here.

    The input is a file somebody exported from a spreadsheet, so the commonest
    mistake is the WRONG FILE rather than the wrong value — and the commonest
    mistake should not be the one that costs a restore.
    """
    code_a, code_b = _code("SUB"), _code("SUB")
    imported_codes += [code_a, code_b]
    csv_text = SUBJECT_CSV.format(code_a=code_a, code_b=code_b)

    dry = client.post(
        "/api/admin/catalogue/subjects/import", headers=admin.headers, files=_upload(csv_text)
    )
    assert dry.status_code == 200, dry.text
    assert dry.json()["dry_run"] is True
    assert (dry.json()["created"], dry.json()["updated"], dry.json()["errors"]) == (2, 0, 0)
    with SessionLocal() as db:
        assert db.get(Course, code_a) is None

    real = client.post(
        "/api/admin/catalogue/subjects/import?dry_run=false",
        headers=admin.headers,
        files=_upload(csv_text),
    )
    assert real.status_code == 200, real.text
    assert (real.json()["created"], real.json()["updated"]) == (2, 0)
    with SessionLocal() as db:
        assert db.get(Course, code_a).name == "Managerial Economics"

    # The same file again is two UPDATES, not two duplicate-key 500s.
    again = client.post(
        "/api/admin/catalogue/subjects/import?dry_run=false",
        headers=admin.headers,
        files=_upload(csv_text),
    )
    assert (again.json()["created"], again.json()["updated"]) == (0, 2)


def test_a_bad_row_is_reported_and_does_not_stop_the_others(client, admin, imported_codes):
    """An import that gives up on line 3 of 300 makes the office fix one typo
    per round trip. Every row is reported, including the ones that failed."""
    good, bad = _code("SUB"), _code("SUB")
    imported_codes.append(good)
    csv_text = (
        "code,name,stage,dimension,semester,model_type\n"
        f"{bad},Nonsense,NOT_A_STAGE,THINKING,1,INSTRUCTOR_LED\n"
        f"{good},Fine,EXCEL,THINKING,2,INSTRUCTOR_LED\n"
        f"{good},Duplicate of the row above,EXCEL,THINKING,2,INSTRUCTOR_LED\n"
    )
    r = client.post(
        "/api/admin/catalogue/subjects/import?dry_run=false",
        headers=admin.headers,
        files=_upload(csv_text),
    )
    assert r.status_code == 200, r.text
    assert (r.json()["created"], r.json()["errors"]) == (1, 2)
    outcomes = {row["line"]: row["outcome"] for row in r.json()["rows"]}
    assert outcomes == {2: "error", 3: "create", 4: "error"}
    detail = next(row["detail"] for row in r.json()["rows"] if row["line"] == 2)
    assert "stage" in detail and "NOT_A_STAGE" in detail
    with SessionLocal() as db:
        assert db.get(Course, bad) is None
        assert db.get(Course, good).name == "Fine"


def test_a_missing_column_is_named(client, admin):
    r = client.post(
        "/api/admin/catalogue/subjects/import",
        headers=admin.headers,
        files=_upload("code,name\nX,Y\n"),
    )
    assert r.status_code == 422
    assert "dimension" in r.json()["detail"]


def test_the_subject_import_refuses_a_scoped_holder(client, faculty, scoped_grant, spine):
    """`courses.code` is a GLOBAL primary key, so a subject is programme-wide by
    construction. A college-scoped holder writing one would publish a row the
    whole deployment sees, with nothing on screen saying so — so the refusal
    names the reason rather than the schema quietly deciding it."""
    scoped_grant(faculty.user_id, "admin.catalogue", ScopeLevel.COLLEGE, spine["here_college"])
    r = client.post(
        "/api/admin/catalogue/subjects/import",
        headers=faculty.headers,
        files=_upload("code,name,stage,dimension,semester,model_type\nZZ,Z,EXCEL,THINKING,1,INSTRUCTOR_LED\n"),
    )
    assert r.status_code == 403
    assert "global" in r.json()["detail"]
