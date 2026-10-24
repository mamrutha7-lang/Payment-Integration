"""The programme sign-up flow (ported from Prisma): an application to join,
the data-driven rules that may wave it through, and single-use email
confirmation links.

Structural note carried over verbatim: `Registration` has NO foreign key to
`Student`. A Student cannot exist until a cohort is decided — which is the very
thing approval decides — so the applicant lives in its own table until then, and
the created student's id is written back to `approved_student_id` as a plain
string breadcrumb (deleting the student must not resurrect a pending application).

`registration_status` is a new PG enum; `degree_level` is the existing one shared
with Cohort/Job (create_type=False), reused by one instance across both columns
that need it here.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .job import DegreeLevel


def _uuid() -> str:
    return uuid.uuid4().hex


class RegistrationStatus(str, enum.Enum):
    # TWO OF THESE SIX ARE DEAD AND CANNOT BE REMOVED. Nothing has written
    # DRAFT (it is only this column's default, and `submit()` always names
    # PENDING_REVIEW explicitly) and nothing has written PENDING_VERIFICATION
    # since migration 9b2d47f0ce15 retired the confirm-before-review gate.
    # They stay because Postgres cannot drop a value from an enum without
    # recreating the type and rewriting every column that uses it —
    # `Role.DIRECTOR` is the repo's precedent for "a value survives as a value".
    # Treat them as unreachable, not as states to handle.
    DRAFT = "DRAFT"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    PENDING_REVIEW = "PENDING_REVIEW"
    #: B11.2. A reviewer has read this application and parked it with a note —
    #: waiting on a missing document, a USN to confirm, a call to the applicant.
    #: INTERNAL: no mail leaves, `decision_reason` is untouched, and the
    #: applicant's own result card cannot tell HOLD from PENDING_REVIEW (it
    #: branches on AUTO_APPROVED and calls everything else "held for review").
    #: Still decidable and still counted as waiting work — see
    #: PENDING_QUEUE_STATUSES.
    HOLD = "HOLD"
    AUTO_APPROVED = "AUTO_APPROVED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


#: What "an application still waiting on a human" means, in ONE place, because
#: two readers ask it: the Analytics "pending registrations" tile
#: (`routers/console.py`) and the decidable-status guard on
#: `POST /{id}/decision`. A HOLD is in it deliberately — the work is parked, not
#: finished — and a tile that silently dropped the moment somebody held a row
#: would report an office getting through its queue when nothing had happened.
#: The review QUEUE's default page is PENDING_REVIEW alone and that is not a
#: contradiction: the queue has a Held tab to send those rows to, and the tile
#: has no second number.
PENDING_QUEUE_STATUSES: tuple[RegistrationStatus, ...] = (
    RegistrationStatus.PENDING_REVIEW,
    RegistrationStatus.HOLD,
)


# One shared instance so the (already-existing) degree_level type is referenced
# consistently by both the required column here and the nullable one on the rule.
_DEGREE_LEVEL = Enum(DegreeLevel, name="degree_level", create_type=False)


class RegistrationRule(Base):
    """When a registration may be waved through without a human reading it. The
    conditions live in data so the admissions office can change them between
    intakes without a deploy. All populated conditions must match; the lowest
    `priority` among the matches decides."""

    __tablename__ = "registration_rules"
    __table_args__ = (Index("ix_regrule_enabled_priority", "enabled", "priority"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    # Everything after the "@", lowercased. Null means "any domain".
    email_domain: Mapped[str | None] = mapped_column(String, nullable=True)
    # Regex the USN must satisfy, e.g. ^1BG2[0-9]MBA[0-9]{3}$.
    usn_pattern: Mapped[str | None] = mapped_column(String, nullable=True)
    degree_level: Mapped[DegreeLevel | None] = mapped_column(_DEGREE_LEVEL, nullable=True)

    cohort_id: Mapped[str | None] = mapped_column(
        ForeignKey("cohorts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # False means "matched, and still send it to a human" — a rule can route and
    # label an application without approving it.
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default="100")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Registration(Base):
    __tablename__ = "registrations"
    __table_args__ = (
        Index("ix_registration_status_created", "status", "created_at"),
        # ONE LIVE APPLICATION PER ADDRESS, AND A REJECTION IS NOT LIVE
        # (migration d7e2f9a41c86, 2026-09-16). `email` was `unique=True`
        # outright, so an address the office had REJECTED could never apply
        # again: the second submission met the duplicate guard's deliberately
        # opaque 409 ("could not be accepted ... contact the placement office"),
        # and the rejection mail had told the applicant to reply to that same
        # office - so a student who had mistyped a USN was refused forever, by
        # the same words, with nothing on either side saying why. A rejection
        # is a decision about ONE application, and the row stays as the record
        # of it; the address is free to be applied with again.
        #
        # PARTIAL, on `status <> 'REJECTED'`, rather than dropping uniqueness:
        # every other status is either waiting on a decision (PENDING_REVIEW,
        # HOLD) or already an account (AUTO_APPROVED, APPROVED), and a second
        # row beside any of those is the duplicate the guard exists to refuse.
        # The database enforces it so that two submissions racing past the
        # guard's read-then-write cannot both land - the same reason
        # `uq_mentor_assignment_one_open_spell` is a constraint and not a
        # sentence. Declared here AND in the migration, per AGENTS.md: an index
        # that lives only in a migration is one `alembic check` asks to drop
        # every run, and the `postgresql_where` is part of the declaration.
        Index(
            "uq_registration_live_email",
            "email",
            unique=True,
            postgresql_where=text("status <> 'REJECTED'"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String)
    # Not `unique=True` - see `uq_registration_live_email` above. The guard in
    # `routers/registration.py::submit` reads the same rule: an existing row
    # blocks a new one unless it is REJECTED.
    email: Mapped[str] = mapped_column(String)
    usn: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    # A second address and the LinkedIn profile, both REQUIRED by the form and
    # by `RegisterIn` since 2026-09-16 and NULLABLE here on purpose: the rule
    # is about new applications, the column is a promise about the rows
    # already written. `phone` and `usn` above are the same shape for the same
    # reason. Copied onto the student's profile at approval.
    personal_email: Mapped[str | None] = mapped_column(String, nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String, nullable=True)

    # THE FORM'S OWN NAME FOR THIS APPLICATION, HASHED (migration f4a2c9e7b1d3,
    # 2026-10-01). The register form mints one random key per filled-in form
    # and sends it with every submit; a retry after a reply lost on a phone
    # carries the same key, and `submit` answers it with this row's original
    # 201 instead of the duplicate guard's opaque 409, which reads as a
    # refusal to somebody who has just applied. sha256 hex, never the key:
    # it is the bearer for reading this application back, and every other
    # bearer in this schema (`auth_tokens`) is stored the same way. NULL on
    # every row written before the column, and on any client that sends none.
    submission_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    degree_level: Mapped[DegreeLevel] = mapped_column(
        _DEGREE_LEVEL, default=DegreeLevel.PG, server_default="PG"
    )
    # The cohort applied for. Nullable — an applicant may not know; a rule or a
    # reviewer assigns it.
    cohort_id: Mapped[str | None] = mapped_column(
        ForeignKey("cohorts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # THE APPLICANT'S OWN CLAIM of where they belong - College -> Department ->
    # (Course) -> (Specialization) -> (Batch) - picked on the public form from
    # what the admin built. Kept SEPARATE from `cohort_id`, which the rule engine
    # stamps: the rule is policy and wins; the applicant's batch fills the gap
    # when no rule seats them. The client sends the deepest level it knows and
    # the API derives the ancestors (the _resolve_ancestry discipline), so these
    # five never disagree with each other. SET NULL: an application must survive
    # the admin renaming or removing a department.
    college_id: Mapped[str | None] = mapped_column(
        ForeignKey("colleges.id", ondelete="SET NULL"), nullable=True, index=True
    )
    department_id: Mapped[str | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    course_id: Mapped[str | None] = mapped_column(
        ForeignKey("academic_courses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    specialization_id: Mapped[str | None] = mapped_column(
        ForeignKey("academic_specializations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # THE SECOND TICK OF A DUAL SPECIALIZATION (2026-09-22, migration
    # d4c8e1f7a2b9). The form's Specialization box is a CHECKLIST: a student
    # who opted for one specialization ticks one, a student who opted for a
    # dual specialization ticks two, and `_resolve_claim` writes the first into
    # `specialization_id` - which every reader that existed before this column
    # (the scope clause, the queue, the batch settling) goes on reading
    # unchanged - and the other one here. The two are UNORDERED on the form:
    # "first" and "second" are the schema's words, not a rank, and where the
    # requested batch pins a specialization that one is written first so the
    # batch and the column it has always been checked against still agree.
    #
    # A COLUMN AND NOT A TABLE, because a dual specialization is TWO by
    # definition, and a second column keeps its sibling's SET NULL discipline
    # (an application survives the office archiving a specialization) and
    # stays out of the three destructors' table ledgers - a
    # `registration_specializations` table would need a verdict in
    # `purge_people`, `purge_students` and the two deletion walks before it
    # could hold a row. Nothing on `students` copies it: a batch hangs on ONE
    # specialization and the student's locked profile card is read through
    # that join, so the second choice lives on the application, where the
    # office reads it (the queue's Specialization line and the
    # `dual_specialization` check).
    second_specialization_id: Mapped[str | None] = mapped_column(
        ForeignKey("academic_specializations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    requested_cohort_id: Mapped[str | None] = mapped_column(
        ForeignKey("cohorts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[RegistrationStatus] = mapped_column(
        Enum(RegistrationStatus, name="registration_status"),
        default=RegistrationStatus.DRAFT,
        server_default="DRAFT",
    )

    # Which rule decided this, when one did.
    matched_rule_id: Mapped[str | None] = mapped_column(
        ForeignKey("registration_rules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    decision_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Reviewer's user id — a plain column (audit stamp), as on Upload.
    reviewed_by_id: Mapped[str | None] = mapped_column(String, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(String, nullable=True)

    # --- the HOLD stamp (B11.2) -------------------------------------------
    # Separate from the three above, not a reuse of them: `reviewed_*` means
    # DECIDED, and `reopen` clears it precisely because a decision was undone.
    # A hold is not a decision, and a held application that reported itself as
    # reviewed would be a row claiming an outcome nobody reached.
    #
    # `held_by_id` is a PLAIN STRING, the house style `reviewed_by_id` set two
    # lines up ("a plain column (audit stamp), as on Upload") — and there is a
    # second reason here: a real FK to `users` on a table `purge_people` EMPTIES
    # is fine, but the same shape on a KEPT table is what `CREATED_BY_COLUMNS`
    # exists to unpick, and the next person to copy this pattern onto
    # `registration_rules` would not notice the difference.
    #
    # `hold_note` is the reviewer's own words and is REQUIRED by the endpoint:
    # a HOLD carrying no note is indistinguishable from PENDING_REVIEW on every
    # screen, so the note is not decoration, it is the whole feature. It never
    # reaches the applicant — `PublicRegistrationOut` does not declare it, and
    # `_public_out_one` narrows by `model_fields`.
    hold_note: Mapped[str | None] = mapped_column(String, nullable=True)
    held_by_id: Mapped[str | None] = mapped_column(String, nullable=True)
    held_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Set once the application became a Student. A string, not a relation.
    approved_student_id: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EmailVerification(Base):
    """A single-use email confirmation link. Only the hash of the token is
    stored — the token itself exists in the email and nowhere else, so a leaked
    dump of this table cannot confirm anybody's address. `consumed_at` is kept
    rather than the row deleted, so a second click can be told apart from an
    expired link and given the right message."""

    __tablename__ = "email_verifications"
    __table_args__ = (Index("ix_emailverif_registration", "registration_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    registration_id: Mapped[str] = mapped_column(
        ForeignKey("registrations.id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


#: The two things an applicant may attach before anyone has decided on them.
#: Plain strings, not a PG enum — the same choice `auth_tokens.purpose` made:
#: adding a third kind is a deploy, not a type migration.
DOCUMENT_KIND_CV = "CV"
DOCUMENT_KIND_PHOTO = "PHOTO"
DOCUMENT_KINDS: tuple[str, ...] = (DOCUMENT_KIND_CV, DOCUMENT_KIND_PHOTO)


class RegistrationDocument(Base):
    """A file attached to a public application BEFORE it is decided.

    Why not `Upload`: an Upload is keyed on `students.id`, and an applicant has
    no Student row until the Main Admin approves them — that is the whole point of
    the queue. So the file lives here, owned by the application, and on
    APPROVE it is MOVED into `uploads` (same stored_name, no second copy of the
    bytes) as the student's first RESUME / PROFILE_PHOTO. On REJECT, or when a
    never-verified application is swept, it is deleted with the row.

    ONE OF EACH KIND, REPLACED IN PLACE — the unique constraint is what makes
    "re-upload your CV" a replace and not a second file the reviewer has to
    guess between. The bytes go through app/document_store like every other
    upload: magic-sniffed (PDF / PNG / JPEG only), size-capped, no path from
    the client ever reaches the disk.
    """

    __tablename__ = "registration_documents"
    __table_args__ = (
        UniqueConstraint("registration_id", "kind", name="uq_regdoc_registration_kind"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    registration_id: Mapped[str] = mapped_column(
        ForeignKey("registrations.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String)  # DOCUMENT_KIND_CV | DOCUMENT_KIND_PHOTO
    original_name: Mapped[str] = mapped_column(String)
    stored_name: Mapped[str] = mapped_column(String, unique=True)
    mime_type: Mapped[str] = mapped_column(String)
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
