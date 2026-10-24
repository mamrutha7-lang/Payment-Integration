"""leave attachments, balances, the academic calendar, the signing function and
the withdrawal timestamp

Revision ID: d8a3f16c05be
Revises: c7e91a4b26d3
Create Date: 2026-09-13

B10.1's recording half, B10.2, B10.3 and B10.4 of
docs/redesign-2026-09/04-backend-changes.md, in ONE revision because they are
one schema round behind two screens that are read together — the applicant's
leave form and the office's Leave Approvals board. Four revisions would leave
four windows in which one of them is half migrated.

It chains onto c7e91a4b26d3 (Phase 4d).

==============================================================================
1. `leave_attachments` — B10.3
==============================================================================

The document_store block, spelled exactly as `uploads`, `staff_upskilling_certs`
and `staff_signatures` spell it: `original_name`, `stored_name` (unique, random,
so a crafted filename can never traverse the store), `mime_type` (the SNIFFED
type, never the client's header) and `size_bytes`. One shape for every
file-backed table is what lets `purge_people.FILE_COLUMNS` be a dict of
table -> column, and this table is added to it in the same commit: the bytes
live on the EFS volume, no row delete touches them, and a purge that lost the
pointer first would leave a student's medical certificate on disk with nothing
left to find it by.

`leave_request_id` is NOT NULL / CASCADE and `uploaded_by_user_id` is nullable /
SET NULL, and the asymmetry is the design. An attachment has no life outside the
application it came with, so it cannot outlive the request; but the fact that a
document was attached outlives the ACCOUNT of whoever attached it, which
`purge_people` will delete for everyone but the Main Admin. NULL there therefore
reads as "that account is gone", never as "nobody attached this".

==============================================================================
2. `leave_balances` — B10.2, AND IT IS KEYED ON `users`, NOT `students`
==============================================================================

Both roles apply on this form. `purge_students` already scopes `leave_requests`
by `requester_user_id` for that reason, and a faculty member's casual-leave
allowance is the balance the office is asked about most. A `students` key would
have made staff balances unrepresentable and pushed them into a second table
with the same columns.

`kind` IS A PLAIN `sa.String` WITH A CHECK, NOT A PG ENUM, and the CHECK is
generated from `app/models/leave_policy.py::PRINTED_LEAVE_KINDS` rather than
typed out here — two copies of a vocabulary is how a widened set is accepted on
screen and refused on INSERT. AGENTS.md's gotcha (b) is the other half of the
reason: a `leave_kind` PG type would have to be hand-written as
`postgresql.ENUM(..., create_type=False)` the moment a second table reused it,
and autogenerate would emit a bare `sa.Enum` that fails with "type already
exists". The five values are fixed by the PRINTED FORM — `leave_paper.OPTIONS`
holds a measured strike-through range for each — so a sixth is a new template
from the office, not a migration.

`entitled_days` / `consumed_days` are INTEGERS. A leave request carries two
dates and nothing finer, so the span it consumes is a whole number of days by
construction, and integers make "does this fit in the balance" an exact
comparison — `time_ledger`'s half-hours are the precedent and its reasoning
applies word for word.

NO ROW IS SEEDED, and the absence of one is not a zero. A deployment that never
opens the policy screen has no balances at all, and that must read as "no
allowance recorded" rather than "you have none left"; `interview_policies`
(a4f7d2c80b93) made the same compatibility promise the same way.

==============================================================================
3. `academic_calendar` — B10.2, A COLLEGE CATALOGUE
==============================================================================

KEPT by both destructors, with `colleges` and `departments`, because it names
nobody: a date, a word and a label that the next intake's leave is counted
against. `college_id` carries NO ondelete — the spine's convention, the database
refuses to delete a college that still has rows under it.

`created_by_user_id` IS `ON DELETE SET NULL`, WHICH DEPARTS FROM THE FOUR SPINE
TABLES ON PURPOSE. They declare the same column with no ON DELETE and rely on
`purge_people.CREATED_BY_COLUMNS` nulling it before the accounts go. That
mechanism is correct and its guard is not: the test that would catch a missing
entry runs the real delete against the real database, and it can only fail if a
row actually points at a doomed account — a calendar is empty on every
development machine, so the omission would ship. SET NULL makes the invariant a
property of the schema instead. `mentor_assignments.by_user_id` (c7e91a4b26d3)
is the recent precedent and argues it in the same words.

The column is named `day`, not `date`. 04-backend-changes.md spells it `date`;
it is the same column, renamed because `date` shadows `datetime.date` in the
model module's own annotations and a reader of `.date` cannot tell the field
from the type.

Two kinds and the second is not redundant: `holiday` is a day the college is
closed, `working` is a day it is OPEN that would otherwise be assumed shut. A
college on alternate Saturdays cannot be described without it.

==============================================================================
4. `leave_requests` — THREE COLUMNS, AND NO ENUM WORK AT ALL
==============================================================================

`first_signed_as` / `second_signed_as` (B10.1) record WHICH FUNCTION signed,
beside the approver columns that already record which ACCOUNT did. The same
account can be a mentor on one request and the office on another, and today the
row cannot tell you which.

Both are `sa.String(32)`, not enums, and not merely by house preference: B10.1's
vocabulary is the part still being argued about. There is NO HOD ACCOUNT in this
product — `departments.head` is free text, checked and not assumed — and no
principal concept, so the set of functions must stay a data change.  AGENTS.md's
gotcha (a) is the other half: adding an enum COLUMN to an existing table does
not create the type, and this revision would have had to `CREATE TYPE` first.

Every existing row gets NULL on both, which is the truth: nobody recorded a
function before this column existed. It must render as nothing at all rather
than as a guessed function — the same rule `swoc_entries.semester` follows one
revision back, for the same reason (there is nothing to backfill FROM).

`cancelled_at` (B10.4). `LeaveStatus.CANCELLED` has been in the `leave_status`
PG type since a80068bf03da created it with all five values, and
`leave_paper.SANCTIONED_WORDS["CANCELLED"]` already prints "Cancelled" — so
B10.4 needs NO enum work whatever, and the value must not be "helpfully" added
again. What was missing is WHEN: `updated_at` carries `onupdate=now()` and moves
for any edit, so it cannot answer "when was this withdrawn" on a row anybody
touched afterwards.

==============================================================================
WHAT THE DOWNGRADE DOES NOT BRING BACK
==============================================================================

`downgrade()` drops the three tables and the three columns. Every attachment ROW
goes with them — AND NOT THE BYTES, which sit on the EFS volume under a random
`stored_name` that only those rows knew. A downgrade therefore orphans every
leave attachment on the volume permanently. It is written that way rather than
deleting files from a migration, because a schema step that reaches out and
destroys data on a filesystem cannot be rolled forward again and would run
against whichever volume happened to be mounted. Every entitlement somebody
typed, every calendar day, every recorded signing function and every withdrawal
timestamp is also gone; nothing recreates them on the next upgrade.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d8a3f16c05be"
down_revision: Union[str, None] = "c7e91a4b26d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The two CHECK bodies, written out rather than imported from
# `app/models/leave_policy.py`, which GENERATES exactly these strings from
# `PRINTED_LEAVE_KINDS` and `CALENDAR_KINDS`.
#
# A migration that imports a live model module breaks the day that module is
# renamed, and it is meant to be a snapshot of a moment, not a view onto HEAD —
# c2f7a9d41e63 says the same thing above its own CHECKS tuple ("names and
# conditions match the models' CheckConstraint declarations byte for byte, so
# create_all() and this agree"). So they are duplicated here on purpose, and
# `tests/test_leave_schema_phase4.py` compares these literals against what the
# model generates, which is what keeps the duplication from drifting.
LEAVE_BALANCE_KIND_CHECK = "kind IN ('CASUAL', 'PERMISSION', 'OOD', 'RH', 'LOP')"
ACADEMIC_CALENDAR_KIND_CHECK = "kind IN ('holiday', 'working')"
CALENDAR_HOLIDAY = "holiday"


def upgrade() -> None:
    # ---------------------------------------------------------- B10.3 -- #
    op.create_table(
        "leave_attachments",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("leave_request_id", sa.String(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.String(), nullable=True),
        sa.Column("original_name", sa.String(), nullable=False),
        sa.Column("stored_name", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # CASCADE: an attachment cannot outlive the application it came with.
        sa.ForeignKeyConstraint(
            ["leave_request_id"], ["leave_requests.id"], ondelete="CASCADE"
        ),
        # SET NULL: the fact that a paper was attached outlives the account of
        # whoever attached it. See the docstring.
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        # Unique: two rows pointing at one stored file would make a delete of
        # either orphan the other's bytes.
        sa.UniqueConstraint("stored_name", name="uq_leave_attachment_stored_name"),
    )
    # (leave_request_id, uploaded_at) serves the FK's index requirement on its
    # LEADING column and the only read anybody makes of this table at once.
    op.create_index(
        "ix_leave_attachment_request", "leave_attachments", ["leave_request_id", "uploaded_at"]
    )
    op.create_index(
        "ix_leave_attachment_uploader", "leave_attachments", ["uploaded_by_user_id"]
    )

    # ---------------------------------------------------------- B10.2 -- #
    op.create_table(
        "leave_balances",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("academic_year", sa.String(length=16), nullable=False),
        sa.Column("entitled_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("consumed_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # `user_id` LEADS this, so it is also the table's FK index.
        sa.UniqueConstraint(
            "user_id", "kind", "academic_year", name="uq_leave_balance_user_kind_year"
        ),
        sa.CheckConstraint(LEAVE_BALANCE_KIND_CHECK, name="ck_leave_balance_kind"),
        # `consumed_days > entitled_days` is deliberately NOT refused: leave
        # past an allowance happens and the office signs it.
        sa.CheckConstraint(
            "entitled_days >= 0 AND consumed_days >= 0", name="ck_leave_balance_days"
        ),
    )

    op.create_table(
        "academic_calendar",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("college_id", sa.String(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column(
            "kind", sa.String(length=16), server_default=CALENDAR_HOLIDAY, nullable=False
        ),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # No ondelete: the spine's convention — the database refuses to delete a
        # college that still has a calendar.
        sa.ForeignKeyConstraint(["college_id"], ["colleges.id"]),
        # SET NULL rather than CREATED_BY_COLUMNS. See the docstring.
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        # `college_id` LEADS this, so it is also that FK's index.
        sa.UniqueConstraint("college_id", "day", name="uq_academic_calendar_college_day"),
        sa.CheckConstraint(ACADEMIC_CALENDAR_KIND_CHECK, name="ck_academic_calendar_kind"),
    )
    op.create_index(
        "ix_academic_calendar_created_by", "academic_calendar", ["created_by_user_id"]
    )

    # --------------------------------------------------- B10.1 / B10.4 -- #
    # Plain String columns. NO `CREATE TYPE`, and none is needed — see the
    # docstring's section 4 for why an enum here would be the wrong shape twice
    # over, and why `leave_status` already carries CANCELLED.
    op.add_column(
        "leave_requests", sa.Column("first_signed_as", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "leave_requests", sa.Column("second_signed_as", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "leave_requests", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("leave_requests", "cancelled_at")
    op.drop_column("leave_requests", "second_signed_as")
    op.drop_column("leave_requests", "first_signed_as")

    op.drop_index("ix_academic_calendar_created_by", table_name="academic_calendar")
    op.drop_table("academic_calendar")

    op.drop_table("leave_balances")

    op.drop_index("ix_leave_attachment_uploader", table_name="leave_attachments")
    op.drop_index("ix_leave_attachment_request", table_name="leave_attachments")
    # The ROWS go; the bytes on the volume do not. See the docstring.
    op.drop_table("leave_attachments")
