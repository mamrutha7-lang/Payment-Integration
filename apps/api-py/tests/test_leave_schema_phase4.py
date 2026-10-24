"""Phase 4e, stage 1 — the leave schema, and the four things about it that are
invisible at every call site.

Everything here is a guard against a specific way the next person to touch this
area goes wrong, and each one has a reason written on it:

1. THE SIX VERDICTS. Three new tables, two destructors, and the pair that would
   hurt most is the one that looks most alike: `leave_balances` is per-person
   and goes, `academic_calendar` is a college catalogue and stays, and they live
   in ONE model module. `purge_people.check_verdicts()` only insists that every
   table is CLASSIFIED; nothing but this asserts they were classified
   CORRECTLY, and both mistakes are silent — a wrong KEEP leaves a departed
   student's medical certificate on the volume, a wrong EMPTY destroys a
   calendar the office typed in.

2. EVERY FK INDEXED. `tests/test_codebase_guards.py` already sweeps the whole
   schema for this. Pinned again here, per column, because that sweep reports a
   list and this says which ones were meant.

3. NO PG ENUM. `leave_balances.kind`, `academic_calendar.kind`,
   `first_signed_as` and `second_signed_as` are all vocabularies, which is
   exactly the shape somebody "fixes" into an enum. Three of AGENTS.md's
   gotchas fire if they do, and B10.1's function vocabulary is the part still
   being argued about — there is no HOD ACCOUNT in this product at all — so it
   must stay a data change.

4. `leave_status` ALREADY CARRIED `CANCELLED`. Since a80068bf03da created the
   type with all five values, in 2026. B10.4 therefore needs NO enum work, and
   the failure mode this pins is somebody helpfully adding an
   `ALTER TYPE leave_status ADD VALUE 'CANCELLED'` migration — which fails on
   every database that already has it and blocks the deploy.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import text

from conftest import requires_db

from app import document_store, purge_people, purge_students
from app.db import Base, SessionLocal

NEW_TABLES = ("leave_attachments", "leave_balances", "academic_calendar")

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "d8a3f16c05be_leave_attachments_balances_calendar.py"
)


def _registered():
    import app.models  # noqa: F401 — registers every table on Base.metadata

    return Base.metadata.tables


# --------------------------------------------------------------------------- #
# 1. The six verdicts
# --------------------------------------------------------------------------- #


def test_the_three_new_tables_are_registered_on_the_metadata():
    """A model module nobody imported in `app/models/__init__.py` is invisible
    to Alembic autogenerate AND to both destructors' verdict checks — so it
    would pass every test below by not existing."""
    tables = _registered()
    for name in NEW_TABLES:
        assert name in tables, f"{name} is not on Base.metadata — check models/__init__.py"


def test_purge_people_verdicts():
    assert purge_people.VERDICTS["leave_attachments"] == purge_people.EMPTY
    assert purge_people.VERDICTS["leave_balances"] == purge_people.EMPTY
    # The catalogue. Somebody in the office typed next year's holidays in once
    # and every intake after that is counted against them.
    assert purge_people.VERDICTS["academic_calendar"] == purge_people.KEEP


def test_purge_students_verdicts():
    # A file on a leave request belongs to the REQUEST, which is scoped by its
    # requester one line above it. `by_user("uploaded_by_user_id")` is the
    # tempting wrong answer: an approver may attach the office's own paper, and
    # a staff uploader SURVIVES this module — so that scope would leave a
    # departed student's certificate behind.
    assert purge_students.STUDENT_VERDICTS["leave_attachments"] == (
        "via",
        "leave_requests",
        "leave_request_id",
    )
    assert purge_students.STUDENT_VERDICTS["leave_balances"] == ("user", "user_id")
    assert purge_students.STUDENT_VERDICTS["academic_calendar"] == purge_students.KEEP


def test_the_two_destructors_agree_about_the_calendar_and_disagree_about_nothing_else():
    """`academic_calendar` is KEPT by BOTH, which is what
    `test_purge_students.py::test_the_two_destructors_classify_the_same_tables`
    requires: the only tables kept here and not there are a faculty member's own
    shelf and the two opaque queues. A calendar kept by one and emptied by the
    other would silently change how leave is counted after a purge."""
    assert purge_people.VERDICTS["academic_calendar"] == purge_people.KEEP
    assert purge_students.STUDENT_VERDICTS["academic_calendar"] == purge_students.KEEP


def test_leave_attachments_is_a_file_backed_table_and_is_declared_as_one():
    """`FILE_COLUMNS` is what makes the BYTES go before the ROW that points at
    them. Without this entry a purge deletes the row, the stored file stays on
    the EFS volume under a random name nothing knows any more, and that is the
    one outcome the module says cannot be repaired afterwards. Both destructors
    drive off this same dict."""
    assert purge_people.FILE_COLUMNS["leave_attachments"] == "stored_name"
    assert "stored_name" in _registered()["leave_attachments"].c


def test_the_document_store_quota_constants_exist_and_are_module_constants():
    """B10.3's two numbers, and they are counted against DIFFERENT OWNERS: the
    file count is per REQUEST, the byte allowance is per USER. Read the comment
    beside them before writing the caller — a quota built the other way round
    lets one long-running request exhaust an account's whole allowance."""
    assert document_store.MAX_LEAVE_ATTACHMENTS_PER_REQUEST > 0
    assert document_store.MAX_LEAVE_ATTACHMENT_BYTES_PER_USER >= document_store.MAX_BYTES
    # Constants, not settings: `app/config.py` must not have grown a name for
    # either. The comment above MAX_UPLOADS_PER_STUDENT says why.
    from app.config import settings

    assert not hasattr(settings, "max_leave_attachments_per_request")
    assert not hasattr(settings, "max_leave_attachment_bytes_per_user")


# --------------------------------------------------------------------------- #
# 2. Every foreign key indexed
# --------------------------------------------------------------------------- #


def _leading(table) -> set[str]:
    from sqlalchemy import PrimaryKeyConstraint, UniqueConstraint

    leading: set[str] = set()
    for idx in table.indexes:
        cols = list(idx.columns)
        if cols:
            leading.add(cols[0].name)
    for con in table.constraints:
        if isinstance(con, (PrimaryKeyConstraint, UniqueConstraint)):
            cols = list(con.columns)
            if cols:
                leading.add(cols[0].name)
    for col in table.columns:
        if col.index or col.unique or col.primary_key:
            leading.add(col.name)
    return leading


def test_every_foreign_key_on_the_new_tables_leads_an_index():
    """Postgres does not index the referencing side of a FOREIGN KEY, and a
    composite that carries the column SECOND is no help. Two of these are served
    by a composite whose leading column IS the FK (`ix_leave_attachment_request`,
    and `user_id` leading `uq_leave_balance_user_kind_year`), which is the point
    of declaring them that way rather than adding a second index to vacuum."""
    tables = _registered()
    expected = {
        "leave_attachments": {"leave_request_id", "uploaded_by_user_id"},
        "leave_balances": {"user_id"},
        "academic_calendar": {"college_id", "created_by_user_id"},
    }
    for name, fks in expected.items():
        table = tables[name]
        found = {c.name for c in table.columns if c.foreign_keys}
        assert found == fks, f"{name}'s foreign keys changed: {found}"
        leading = _leading(table)
        for column in fks:
            assert column in leading, f"{name}.{column} leads no index"


# --------------------------------------------------------------------------- #
# 3. No PG enum anywhere in this area
# --------------------------------------------------------------------------- #


def test_the_new_vocabulary_columns_are_plain_strings_in_python():
    """`leave_balances.kind`, `academic_calendar.kind` and the two
    `*_signed_as` columns are all vocabularies. A String with a CHECK keeps a
    sixth value a data change; an `sa.Enum` makes it an `ALTER TYPE` migration,
    and AGENTS.md's gotcha (a) means adding one to `leave_requests` would not
    even have created the type."""
    from sqlalchemy import Enum as SAEnum
    from sqlalchemy import String

    tables = _registered()
    for table_name, column in (
        ("leave_balances", "kind"),
        ("academic_calendar", "kind"),
        ("leave_requests", "first_signed_as"),
        ("leave_requests", "second_signed_as"),
    ):
        col = tables[table_name].c[column]
        assert isinstance(col.type, String) and not isinstance(col.type, SAEnum), (
            f"{table_name}.{column} is {col.type!r} — it must stay a plain String; "
            "see app/models/leave_policy.py"
        )


def test_the_migration_creates_no_type_and_alters_none():
    """The literal tripwire. This revision must contain no `CREATE TYPE`, no
    `sa.Enum`, and above all no `ALTER TYPE leave_status ADD VALUE` — that last
    one fails on every database that already has the value, which is all of
    them, and it blocks the deploy rather than the pull request."""
    source = MIGRATION.read_text(encoding="utf-8")
    # Only the CODE. The module docstring and the comments discuss enums at
    # length — explaining why there is none is the opposite of adding one.
    body = source.split('"""', 2)[2]
    code = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("#")
    )
    for forbidden in ("CREATE TYPE", "ALTER TYPE", "sa.Enum", "postgresql.ENUM"):
        assert forbidden not in code, f"{forbidden} appears in {MIGRATION.name}"


def test_the_check_bodies_in_the_migration_match_the_ones_the_model_generates():
    """The migration deliberately does NOT import the model — it is a snapshot,
    and an Alembic revision that reads HEAD breaks the day a module is renamed
    (c2f7a9d41e63 makes the same choice above its own CHECKS tuple). The cost of
    that choice is two copies of each condition, and this is what stops them
    drifting: the model GENERATES its string from the vocabulary tuple, so
    adding a sixth leave kind there fails here until the migration agrees."""
    import importlib.util

    from app.models.leave_policy import (
        ACADEMIC_CALENDAR_KIND_CHECK,
        LEAVE_BALANCE_KIND_CHECK,
    )

    spec = importlib.util.spec_from_file_location("_leave_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.LEAVE_BALANCE_KIND_CHECK == LEAVE_BALANCE_KIND_CHECK
    assert module.ACADEMIC_CALENDAR_KIND_CHECK == ACADEMIC_CALENDAR_KIND_CHECK


@requires_db
def test_no_pg_enum_type_backs_any_column_on_the_new_tables():
    """The database's own answer, not the model's. `USER-DEFINED` is how
    `information_schema` reports a PG enum."""
    with SessionLocal() as db:
        rows = db.execute(
            text(
                """
                select table_name, column_name, data_type
                  from information_schema.columns
                 where table_name in (
                       'leave_attachments', 'leave_balances', 'academic_calendar')
                """
            )
        ).all()
    assert rows, "the three tables are not in the database — run alembic upgrade head"
    offenders = [f"{t}.{c}" for t, c, d in rows if d == "USER-DEFINED"]
    assert not offenders, f"a PG enum backs {offenders}"

    # And the two columns added to leave_requests, which is the table an enum
    # column would have been added to WITHOUT a CREATE TYPE (gotcha (a)).
    with SessionLocal() as db:
        added = dict(
            db.execute(
                text(
                    """
                    select column_name, data_type
                      from information_schema.columns
                     where table_name = 'leave_requests'
                       and column_name in
                           ('first_signed_as', 'second_signed_as', 'cancelled_at')
                    """
                )
            ).all()
        )
    assert added["first_signed_as"] == "character varying"
    assert added["second_signed_as"] == "character varying"
    assert added["cancelled_at"] == "timestamp with time zone"


# --------------------------------------------------------------------------- #
# 4. leave_status already carried CANCELLED
# --------------------------------------------------------------------------- #


def test_cancelled_was_in_the_leave_status_enum_before_this_migration():
    """B10.4 needs NO enum work, and this says so twice over so that nobody
    writes the migration that would prove it by failing.

    a80068bf03da created `leave_status` with all five values in 2026;
    `LeaveStatus` has carried the member ever since; and
    `leave_paper.SANCTIONED_WORDS` already prints "Cancelled" on the college's
    own form. What B10.4 actually needed was `cancelled_at`, because
    `updated_at` carries `onupdate=now()` and moves for any edit at all.
    """
    from app.leave_paper import SANCTIONED_WORDS
    from app.models.leave import LeaveStatus

    assert LeaveStatus.CANCELLED.value == "CANCELLED"
    assert SANCTIONED_WORDS["CANCELLED"] == "Cancelled"

    versions = MIGRATION.parent
    creator = next(versions.glob("a80068bf03da_*.py"))
    source = creator.read_text(encoding="utf-8")
    assert "CANCELLED" in source, (
        "a80068bf03da no longer creates leave_status with CANCELLED — if the "
        "type is now built elsewhere, re-point this test at that revision "
        "rather than adding an ALTER TYPE"
    )
    # Nobody has since added the value a second time. (daaeb23b750e alters
    # the type for REVOKED, a different value, which is why the pattern names
    # CANCELLED rather than any ALTER at all.)
    adders = [
        p.name
        for p in versions.glob("*.py")
        if re.search(
            r"ALTER\s+TYPE\s+leave_status\s+ADD\s+VALUE[^'\n]*'CANCELLED'",
            p.read_text(encoding="utf-8"),
            re.I,
        )
    ]
    assert not adders, f"CANCELLED is added to leave_status again by {adders}"


@requires_db
def test_the_database_agrees_that_leave_status_has_all_six_values():
    with SessionLocal() as db:
        labels = set(
            db.execute(
                text(
                    """
                    select e.enumlabel
                      from pg_enum e
                      join pg_type t on t.oid = e.enumtypid
                     where t.typname = 'leave_status'
                    """
                )
            ).scalars()
        )
    assert labels == {
        "SUBMITTED",
        "FIRST_APPROVED",
        "APPROVED",
        "REJECTED",
        "CANCELLED",
        # daaeb23b750e: the office took back a sanction it had given.
        "REVOKED",
    }


# --------------------------------------------------------------------------- #
# 5. The columns later stages are building against
# --------------------------------------------------------------------------- #


def test_the_column_names_later_stages_depend_on():
    """This stage owns the schema and nobody else touches it, so these names are
    a CONTRACT. Renaming one is a migration plus every endpoint built on it, and
    this test is where that decision gets made deliberately instead of by
    autocomplete."""
    tables = _registered()
    assert {c.name for c in tables["leave_attachments"].columns} == {
        "id",
        "leave_request_id",
        "uploaded_by_user_id",
        "original_name",
        "stored_name",
        "mime_type",
        "size_bytes",
        "uploaded_at",
    }
    assert {c.name for c in tables["leave_balances"].columns} == {
        "id",
        "user_id",
        "kind",
        "academic_year",
        "entitled_days",
        "consumed_days",
        "created_at",
        "updated_at",
    }
    # `day`, not `date`: 04-backend-changes.md spells it `date`, which would
    # shadow `datetime.date` in the model module's own annotations.
    assert {c.name for c in tables["academic_calendar"].columns} == {
        "id",
        "college_id",
        "day",
        "kind",
        "label",
        "created_by_user_id",
        "created_at",
        "updated_at",
    }
    for column in ("first_signed_as", "second_signed_as", "cancelled_at"):
        assert column in tables["leave_requests"].c


def test_nothing_else_was_added_to_leave_requests():
    """The owner's standing instruction is that the leave form and its buttons
    do not change. Three nullable columns nothing is required to write is the
    whole schema footprint B10 gets on this table; a fourth would mean the form
    grew a field."""
    tables = _registered()
    assert {c.name for c in tables["leave_requests"].columns} == {
        "id",
        "requester_user_id",
        "from_date",
        "to_date",
        "reason",
        "status",
        "first_approver_user_id",
        "first_decision",
        "first_decided_at",
        "first_note",
        "first_signed_as",
        "second_approver_user_id",
        "second_decision",
        "second_decided_at",
        "second_note",
        "second_signed_as",
        "leave_kind",
        "credit",
        "alt_name",
        "alt_rows",
        "created_at",
        "signed_at",
        "cancelled_at",
        # daaeb23b750e: the stamps of a decision changed after it was made.
        # The approver writes these from the console; the applicant's form
        # did not grow a field.
        "decision_change_kind",
        "decision_changed_at",
        "decision_changed_by_user_id",
        "decision_change_reason",
        "decision_revision",
        "updated_at",
    }


def test_alt_rows_is_still_jsonb_and_b10_6_needs_no_migration():
    """B10.6 adds `user_id` and `accepted_at` KEYS to the stored objects. JSONB
    takes them silently, which is the trap: `_leave_out` does `AltRow(**r)` over
    rows STORED BEFORE the field existed, so any new `AltRow` field must carry a
    default or every pre-existing request 500s on read. Pinned here so a stage
    that promotes `alt_rows` to a table has to come past this comment first."""
    from sqlalchemy.dialects.postgresql import JSONB

    col = _registered()["leave_requests"].c["alt_rows"]
    assert isinstance(col.type, JSONB)

    from app.routers.leave import AltRow

    # Every field defaulted: an old row has none of the new keys.
    assert AltRow() is not None
    for name, field in AltRow.model_fields.items():
        assert not field.is_required(), (
            f"AltRow.{name} has no default — every leave request stored before "
            "it existed will 500 in _leave_out's AltRow(**r)"
        )


def test_the_printed_leave_kinds_are_the_five_the_paper_strikes_through():
    """The set is fixed BY THE TEMPLATE. `leave_paper.OPTIONS` holds a measured
    x-range for each key so the four that do not apply can be struck out; a
    sixth kind has nowhere to be struck and needs a new form from the office,
    not a migration. `LeaveIn.leave_kind`'s pattern is the same set at the API
    edge, and this is the one assertion that ties all three together."""
    from app.leave_paper import OPTIONS
    from app.models.leave_policy import PRINTED_LEAVE_KINDS
    from app.routers.leave import LeaveIn

    assert set(PRINTED_LEAVE_KINDS) == set(OPTIONS)
    pattern = LeaveIn.model_fields["leave_kind"].metadata
    rendered = "".join(str(m) for m in pattern)
    for kind in PRINTED_LEAVE_KINDS:
        assert kind in rendered, f"{kind} is not in LeaveIn.leave_kind's pattern"
