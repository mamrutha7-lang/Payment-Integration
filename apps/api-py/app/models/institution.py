"""College and Department — the two levels above a Cohort.

WHY THESE EXIST. The student's profile carries a locked card reading College /
Department / Batch / Entry date / Expected completion, "verified by Main Admin".
Four of those five had no data source at all: there was no College column
anywhere in the schema, and Department was free text on `users` that nothing
ever wrote. This module supplies the two missing levels so all five are reached
through one join from `students.cohort_id`.

THE HIERARCHY, IN FULL:

    College -> Department -> Course -> Specialization -> Cohort -> Student

The middle two were deferred once and are now built. Inserting them cost two
tables and two nullable columns on `cohorts`, and — this is the point — **not
one existing student row changed**, because a student stores only `cohort_id`
and reads everything else through it. Denormalising College or Department onto
`students` would have been faster to write and would have made this insertion a
backfill of every student row instead.

WHY THERE IS NO "ACADEMIC YEAR" LEVEL, though one was drawn in the original
sketch. The batch already IS the year: `cohorts.batch_label` says "2024-26",
and `start_date` / `end_date` bound it. `students.current_semester` and
`courses.semester` carry the same fact again from two other angles. A table
would have been the FIFTH representation of one idea, and a fact stored five
ways is a fact that will eventually disagree with itself. If a year ever needs
a record of its OWN — its own admission open/close dates, courses that run only
in certain years — it inserts then at exactly the cost it would have today,
which is the whole property this shape was built for.

THEY ARE OPTIONAL, AND WHETHER THEY STAY OPTIONAL IS ONE LINE. See
HIERARCHY_LEVELS below. The columns are nullable permanently; requiredness is a
rule about what THIS RELEASE accepts, not a promise about rows already written.

WHY "AcademicCourse" AND NOT "Course". `Course` is already a model — the
22MBA11 curriculum in app/models/course.py, which is what a student ENROLS in
and earns marks for. This level is a different thing: a programme in the
admissions catalogue (MBA, MCA) that a batch belongs to and under which many of
those courses are taught. One name meaning two things is a bug waiting for
whoever assumes it means one, and this repo has a guard test for exactly that.
`CourseOut` (student.py) and `SpecializationOut` (voice_platform/api/admin.py)
are likewise taken, and `Specialization` is booked THREE times already — the
interview matrix's four tracks, `PlatformSpecialization`, and
`interview_sessions.specialization`. Hence the prefix in code. **The UI still
says "Course" and "Specialization"**, which is the Batch/Cohort precedent
applied twice more: the internal name is for correctness, the external one is
for the person filling the form.

BATCH vs COHORT. The interface says "Batch"; this schema says `Cohort`, because
`cohorts` already existed (migration fea4515cdba5) and `students.cohort_id`
already pointed at it. The mapping is deliberate and is documented once in
docs/institutional-spine-build-log.md rather than being discovered from a
confusing diff.

STATUS IS A PLAIN STRING, NOT A PG ENUM. The same reasoning app/models/
voice_platform.py sets out for `degree_level`: a new status value should be a
data change, not a `CREATE TYPE` migration carrying all three of AGENTS.md's
enum gotchas. Archive and restore are therefore an UPDATE, never a DELETE —
which is also what the design's admin console does.
"""

import uuid
from datetime import datetime
from typing import Final, NamedTuple

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from .job import DegreeLevel


def _uuid() -> str:
    return uuid.uuid4().hex


#: Lifecycle for a college or a department. ACTIVE is the working state; DRAFT
#: is a record being prepared that must not yet be selectable when seating a
#: student; ARCHIVED is retired but retained, because a graduated student's
#: record still has to name the college they attended.
class HierarchyLevel(NamedTuple):
    """One optional level between a Department and a Batch."""

    key: str  # wire + form-control key
    label: str  # what the admin console prints, and what the student's card prints
    field: str  # the `cohorts` column it fills
    required: bool  # <- THE SWITCH


#: THE LEVELS BETWEEN A DEPARTMENT AND A BATCH, AND WHETHER EACH MUST BE FILLED
#: IN TODAY. This tuple is the ONE answer to "must a new batch name a course?" —
#: `AdminCohortIn`'s validator reads it, and the Angular admin console reads it
#: through GET /api/admin/hierarchy/levels. Neither side decides anything by
#: itself, so "both sides agree" is a property of the call graph rather than a
#: promise in a comment. The LABEL is typed once, here, so the word cannot drift
#: between the console, the student's locked card and a future export.
#:
#: TO MAKE A LEVEL MANDATORY: change its `required=False` to `required=True` on
#: the line below, and deploy. THAT IS THE WHOLE CHANGE. No migration, no schema
#: change, no second edit anywhere.
#:
#: THE COLUMNS STAY NULLABLE FOREVER, AND THAT IS NOT AN OVERSIGHT. Nullability
#: is what the DATABASE promises about rows that ALREADY EXIST; `required` is
#: what THIS RELEASE asks of a NEW one. They are different questions, and
#: conflating them turns "make it mandatory" back into a migration — one that
#: aborts on the first legacy batch, halfway through a deploy.
#:
#: WHAT THE FLIP DOES NOT DO is make existing batches compliant. Nothing can:
#: the morning after, every batch created while the level was optional is
#: missing it. So three things ship WITH the switch, not after it —
#: `GET /api/admin/cohorts/incomplete` lists exactly those rows,
#: `AdminCohortOut.missing_levels` flags them, and PATCH still accepts any edit
#: that does not make the gap WORSE. Without those, the morning after the flip
#: the console starts refusing saves on rows nobody can fix, and the fix will be
#: to flip it back.
#:
#: ORDER IS DEPTH, and a required level implies the shallower ones, because
#: ancestors are DERIVED rather than typed (see _resolve_ancestry in
#: app/routers/admin.py). A gap in the middle is incoherent, and
#: test_codebase_guards.py pins that the required levels form a prefix.
HIERARCHY_LEVELS: Final[tuple[HierarchyLevel, ...]] = (
    HierarchyLevel("course", "Course", "course_id", required=False),
    HierarchyLevel("specialization", "Specialization", "specialization_id", required=False),
)


STATUS_ACTIVE = "ACTIVE"
STATUS_DRAFT = "DRAFT"
STATUS_ARCHIVED = "ARCHIVED"


class College(Base):
    """One institution. `code` is what appears beside a student's name (BGSCET)."""

    __tablename__ = "colleges"

    # NAMED, not `unique=True` on the column. An unnamed unique constraint is
    # named by whoever builds the database: Base.metadata.create_all() (what the
    # dev seed path uses) calls it "colleges_code_key", while the migration
    # names it "uq_college_code" — the same declaration producing two different
    # names depending on how the database was made, so a future
    # op.drop_constraint("uq_college_code") fails on half of them. `cohorts`
    # already has this problem; Department got it right. This matches Department.
    __table_args__ = (UniqueConstraint("code", name="uq_college_code"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    # The code is the human handle used on screen and in exports; two colleges
    # sharing one makes every such reference ambiguous.
    code: Mapped[str] = mapped_column(String)  # e.g. BGSCET
    name: Mapped[str] = mapped_column(String)
    campus: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. Bengaluru
    # Nullable throughout: the admin creates a college with a name and a code
    # and fills the rest in later. A required field here would be a required
    # field on the create form, and the form would then invent values.
    contact: Mapped[str | None] = mapped_column(String, nullable=True)
    #: The email domains an application may be APPROVED INTO for this college.
    #:
    #: THE FENCE IS THE TENANT'S, NOT THE DEPLOYMENT'S. Until now the only
    #: fence was `Settings.provisionable_email_domains`, one list read from the
    #: environment and applied to every application regardless of which college
    #: it named. With one college that is the same thing; with two it is a hole,
    #: because an applicant to college B is admitted on college A's domain. Read
    #: `provisionable_domains_for` in app/institution_domains.py for how the two
    #: compose: this list wins where a college has one, and the environment is
    #: the fallback where it does not.
    #:
    #: EMPTY IS A REAL STATE AND MEANS "fall back", not "admit nobody". A
    #: college created on the console starts here, and a fence that refused
    #: everything until somebody filled the field in would make "Add college"
    #: a trap whose only symptom is a 422 on an application days later.
    email_domains: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default=sql_text("'{}'::text[]")
    )
    # WHO created it, for the console's audit view. Nullable: `python -m app.seed`
    # and the CLIs have no user. Set by the admin router from the session; never
    # by the client.
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String, default=STATUS_ACTIVE, server_default=STATUS_ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # passive_deletes="all" is what makes the "the database refuses" comment on
    # Department.college_id TRUE. Without it SQLAlchemy helpfully nulls the
    # children first on a delete, and because college_id is NOT NULL you get a
    # NotNullViolation naming departments.college_id while trying to delete a
    # College — an error that reads like a bug in the wrong table. With it the
    # DELETE reaches the database and the FK raises the legible
    # ForeignKeyViolation the comment promises. There is no delete endpoint
    # today; this is here so the first person to add one is not misled.
    departments: Mapped[list["Department"]] = relationship(
        back_populates="college", order_by="Department.name", passive_deletes="all"
    )


class Department(Base):
    """A department within one college. Cohorts hang off this, not off College."""

    __tablename__ = "departments"
    __table_args__ = (
        # Scoped to the college, not global: two colleges may each have a "CSE",
        # and forcing them to differ would push the college name into the code.
        UniqueConstraint("college_id", "code", name="uq_department_college_code"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    # No ondelete: the database REFUSES to delete a college that still has
    # departments. Archiving is the supported retirement path, and a cascade
    # here would silently take cohorts and student links with it.
    # No index=True: uq_department_college_code is (college_id, code), and a
    # composite index serves a predicate on its LEADING column — verified with
    # EXPLAIN, which picks it for `WHERE college_id = ?` once the standalone
    # index is gone. A second index here would be one more thing to write,
    # vacuum and keep correct for no read it enables.
    college_id: Mapped[str] = mapped_column(ForeignKey("colleges.id"))
    code: Mapped[str] = mapped_column(String)  # e.g. MGMT, CSE — the unit, not the programme it offers
    name: Mapped[str] = mapped_column(String)
    # Head of department, as printed on the leave form's department line.
    head: Mapped[str | None] = mapped_column(String, nullable=True)
    # B9.2. How many students one faculty member in THIS department is expected
    # to carry. NULL falls back to `settings.mentor_capacity`.
    #
    # ADVISORY, AND DELIBERATELY SO. 04 asks for "capacity from a governance
    # setting", and the temptation that comes with a number is to enforce it.
    # Two places in this repository argue against that in writing —
    # `routers/admin_mentoring.py`'s MentorLoadOut ("programme policy, not a per-mentor
    # fact ... nothing enforces it") and the Angular screen's own header ("an
    # admin who chooses to overload one faculty member in a thin year should not
    # have to edit .env first. The rail says 'At capacity' in the risk colour
    # and lets them") — and both are still right. What was wrong was only that
    # the number lived in an environment variable, so tuning it for one
    # department meant a deploy. This column is that fix and nothing more: the
    # assignment endpoints do not read it, and no request is refused by it.
    #
    # There is no `governance_settings` table and this does not invent one. A
    # capacity is a fact about a department, and departments have a row.
    mentor_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # WHO created it, for the console's audit view. Nullable: `python -m app.seed`
    # and the CLIs have no user. Set by the admin router from the session; never
    # by the client.
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String, default=STATUS_ACTIVE, server_default=STATUS_ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    college: Mapped[College] = relationship(back_populates="departments")


# --------------------------------------------------------------------------- #
# The two levels between a Department and a Batch.
#
# One shape, twice: <parent>_id + code + name + status, uniqueness scoped to the
# parent, no ondelete. That regularity is deliberate — a reader who understands
# Department understands both, and every admin endpoint is the same
# GET /{parent}/{id}/{children}.
#
# UNIQUENESS IS SCOPED TO THE PARENT, for the reason Department's is: two
# departments may each run an "MBA", two courses may each have a "FIN" stream,
# and forcing them to differ pushes the parent's name into the child's code.
# --------------------------------------------------------------------------- #


class AcademicCourse(Base):
    """A programme within a department. e.g. MBA, MCA.

    NOT app/models/course.py's `Course`, which is a taught subject a student
    enrols in and earns marks for (22MBA11). This is the admissions catalogue's
    programme, under which many of those subjects are taught. The two are
    genuinely different things and the prefix keeps them apart; the UI calls
    this one "Course", because that is what an admin calls it.
    """

    __tablename__ = "academic_courses"
    __table_args__ = (
        UniqueConstraint("department_id", "code", name="uq_academic_course_department_code"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    # No ondelete, like departments -> colleges: the database REFUSES to delete
    # a department that still has courses. Archiving is the retirement path, and
    # a cascade here would silently take specializations and every cohort
    # pointer into them.
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"))
    code: Mapped[str] = mapped_column(String)  # MBA
    name: Mapped[str] = mapped_column(String)  # Master of Business Administration
    # Nullable throughout: an admin creates the course with a code and a name and
    # fills the rest in later. A required field here would be a required field on
    # the create form, and the form would then invent a value.
    duration_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ------------------------------------------------------------------ #
    # THE SHAPE OF THE PROGRAMME (B4.1). What a batch inherits and what a
    # student's semester is bounded by.
    #
    # THERE IS NO `duration_years`, though 04-backend-changes.md asks for one.
    # `duration_months` above already exists, is already on the admin form and
    # is already printed; a second duration column is one fact stored twice,
    # and 18-month programmes round wrong in years. Years are derived where a
    # screen wants them.
    #
    # BOTH NULLABLE, like `duration_months` and for the same reason: an admin
    # creates the course with a code and a name and fills the rest in later.
    # A course with no `total_semesters` keeps the old flat semester bound
    # (admin_students.MAX_SEMESTER) rather than refusing every edit, and
    # `GET /api/admin/cohorts/incomplete` is where an unfilled one surfaces.
    #
    # `degree_level` REUSES THE EXISTING `degree_level` PG TYPE — the one
    # `cohorts.degree_level` and `jobs` already use. Gotchas (a) and (b)
    # together: adding an enum COLUMN does not CREATE TYPE, and the type is
    # already there, so the migration hand-writes
    # postgresql.ENUM(..., create_type=False) exactly as fea4515cdba5 does.
    # A second `academic_course_degree_level` type holding the same two members
    # would be the same mistake twice.
    #
    # It does not replace `cohorts.degree_level`, which stays written: the
    # batch is where admissions records the fact, and a course that names a
    # level is what a batch is CHECKED against, the way _resolve_ancestry
    # checks a shallower level the client also sent.
    # ------------------------------------------------------------------ #
    degree_level: Mapped[DegreeLevel | None] = mapped_column(
        Enum(DegreeLevel, name="degree_level", create_type=False), nullable=True
    )
    total_semesters: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # WHO created it, for the console's audit view. Nullable: `python -m app.seed`
    # and the CLIs have no user. Set by the admin router from the session; never
    # by the client.
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String, default=STATUS_ACTIVE, server_default=STATUS_ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AcademicSpecialization(Base):
    """A stream within a course. e.g. Finance, HR, Marketing.

    Distinct from `interview_matrix.Specialization` (the four mock-interview
    tracks, catalogue-in-code) and from `PlatformSpecialization` (the voice
    platform's per-degree catalogue rows). Same word, three other domains —
    which is why this one carries the prefix and why the router schema is
    `AcademicSpecializationOut`.
    """

    __tablename__ = "academic_specializations"
    __table_args__ = (
        UniqueConstraint("course_id", "code", name="uq_academic_specialization_course_code"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    course_id: Mapped[str] = mapped_column(ForeignKey("academic_courses.id"))
    code: Mapped[str] = mapped_column(String)  # FIN
    name: Mapped[str] = mapped_column(String)  # Finance
    # WHO created it, for the console's audit view. Nullable: `python -m app.seed`
    # and the CLIs have no user. Set by the admin router from the session; never
    # by the client.
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String, default=STATUS_ACTIVE, server_default=STATUS_ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
