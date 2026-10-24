"""Three indexes the query plans asked for, and thirteen that were paying rent

Found by reading `pg_indexes` against the running database rather than the
models, which is the only way the two kinds of mistake here become visible.

ADDED — each one is a query that could not use any existing index:

  * `ix_users_email_lower`. NINE call sites ask `WHERE lower(email) = :x` — the
    Google SSO callback, registration provisioning, `grant_access`, and the
    admin faculty/student create-and-edit paths. `ix_users_email` is a plain
    unique btree on `email`, and Postgres cannot use it for that predicate:
    `lower(email)` is an expression, not the indexed value. Every one of those
    was a sequential scan of `users`, which is invisible with six dev rows and
    is every single sign-in once a roster is seeded.

  * `ix_maillog_sent_at`. The ops mail screen with no `kind` filter is
    `ORDER BY sent_at DESC LIMIT 100` over the whole table. `sent_at` is the
    SECOND column of both existing composites, and an index is only walked in
    sort order from its first column, so this was a scan of every row ever
    mailed plus a sort, to show a hundred. 4,689 rows on a dev box that has
    never had a real user.

  * `ix_interview_session_started`. Same shape: the staff list sorts every
    interview in the programme by `started_at`, and the composite that exists
    leads with `student_id`.

DROPPED — thirteen indexes that are a strict prefix of, or identical to, a
UNIQUE index on the same table. Postgres answers those lookups from the unique
index; the duplicate only adds a write on every insert and update to the same
row, and a second thing for the planner to consider. Two of them
(`ix_feature_override_lookup`, `ix_ledger_day_student_day`) are EXACT duplicates
of their unique twin, same columns in the same order.

Dropping them is safe for the foreign-key rule
(`tests/test_codebase_guards.py::test_every_foreign_key_column_is_indexed`)
precisely because the covering index leads with the same column — which is what
that guard checks, and why it stayed green through this change.

`tests/test_codebase_guards.py::test_no_index_duplicates_the_prefix_of_another`
now computes this from the model metadata, so the next one fails in CI instead
of being found by reading a catalogue a year later.

Revision ID: a91f3c5d80e4
Revises: 7c4e0b21d9aa
Create Date: 2026-09-10
"""

from __future__ import annotations

from alembic import op

revision: str = "a91f3c5d80e4"
down_revision: str | None = "7c4e0b21d9aa"
branch_labels: str | None = None
depends_on: str | None = None


#: (index name, table, the CREATE INDEX body) — written out rather than passed
#: through `op.create_index`, because one of them is a FUNCTIONAL index and
#: `create_index` wants a column list.
_ADDED: tuple[tuple[str, str, str], ...] = (
    ("ix_users_email_lower", "users", "(lower(email))"),
    ("ix_maillog_sent_at", "mail_logs", "(sent_at)"),
    ("ix_interview_session_started", "interview_sessions", "(started_at)"),
)

#: (redundant index, its table, the unique index that already covers it). The
#: third element is not used by the migration — it is the evidence, kept here so
#: the reason a name was dropped survives with the name.
_DROPPED: tuple[tuple[str, str, str], ...] = (
    ("ix_feedback_run", "assistant_feedback", "uq_feedback_run_owner"),
    ("ix_capassess_student", "capability_assessments", "uq_capability_checkpoint"),
    ("ix_english_section_baseline", "english_baseline_sections", "uq_english_section"),
    ("ix_english_baseline_student", "english_baselines", "uq_english_baseline_semester"),
    ("ix_feature_override_lookup", "feature_overrides", "uq_feature_override_target"),
    ("ix_redesign_knowledge_chunk_version", "redesign_knowledge_chunks", "uq_redesign_knowledge_chunk_ordinal"),
    ("ix_regdoc_registration", "registration_documents", "uq_regdoc_registration_kind"),
    ("ix_semester_results_student_id", "semester_results", "uq_semester_result"),
    ("ix_studentbadge_student", "student_badges", "uq_student_badge"),
    ("ix_student_milestone_student", "student_milestones", "uq_student_milestone"),
    ("ix_ledger_cell_day", "time_ledger_cells", "uq_ledger_cell"),
    ("ix_ledger_day_student_day", "time_ledger_days", "uq_ledger_day"),
    ("ix_timesheet_student_day", "time_sheet_entries", "uq_timesheet"),
)

#: What each dropped index was, so `downgrade()` can put it back exactly.
_DROPPED_DEFINITIONS: dict[str, str] = {
    "ix_feedback_run": "(run_id)",
    "ix_capassess_student": "(student_id)",
    "ix_english_section_baseline": "(baseline_id)",
    "ix_english_baseline_student": "(student_id)",
    "ix_feature_override_lookup": "(feature, scope, target_id)",
    "ix_redesign_knowledge_chunk_version": "(document_version_id)",
    "ix_regdoc_registration": "(registration_id)",
    "ix_semester_results_student_id": "(student_id)",
    "ix_studentbadge_student": "(student_id)",
    "ix_student_milestone_student": "(student_id)",
    "ix_ledger_cell_day": "(ledger_day_id)",
    "ix_ledger_day_student_day": "(student_id, day)",
    "ix_timesheet_student_day": "(student_id, day)",
}


def upgrade() -> None:
    for name, table, body in _ADDED:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {body}")
    for name, table, _covered_by in _DROPPED:
        op.execute(f"DROP INDEX IF EXISTS {name}")


def downgrade() -> None:
    """Exactly reversible, unlike 7c4e0b21d9aa.

    An index carries no information of its own, so putting these back loses
    nothing and drops nothing — which is what makes a real downgrade the honest
    choice here and a refusal the honest one there.
    """
    for name, table, _covered_by in _DROPPED:
        body = _DROPPED_DEFINITIONS[name]
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {body}")
    for name, _table, _body in _ADDED:
        op.execute(f"DROP INDEX IF EXISTS {name}")
