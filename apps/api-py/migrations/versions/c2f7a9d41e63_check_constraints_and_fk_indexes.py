"""CHECK constraints for every documented bound, and an index behind every foreign key.

WHAT WAS WRONG. The models argue, at length and correctly, for invariants the
schema then declined to enforce. `time_ledger_cells.half_hours` is an integer
so that "does this add to 24" is an exact comparison — and the database would
accept -5 or 999 for it, because `SLOT_CAPACITY_HALVES` bounded a cell in the
router and nowhere else. Same for capability scores ("1–10, validated at the
edge"), certification progress, interview scorecards, SGPA/CGPA and the rest
of CHECKS below. Every one of those bounds held only on the request path, so a
seed, an ops task, a manual fix and the next endpoint all walked around it.
Across 87 tables this database carried zero CHECK constraints.

And 40 of its 116 foreign-key columns had no index (39 when first measured
against the live database; auth_tokens.created_by_user_id landed in
a8e5c3f17b92 the same day, and the new guard test caught it). Postgres does not index the
referencing side of a FOREIGN KEY; the 2026-08 audit (b41c9e2d7f05) indexed
students.cohort_id and students.mentor_id and stopped, leaving
mentor_notes.mentor_id — rule 2's scope path — interview_sessions.conversation_id,
placement_offers.job_id and 37 others as sequential scans on every join and on
every DELETE of a parent row.

WHAT THIS DOES NOT CONSTRAIN, ON PURPOSE. `turns_persisted <= turns_emitted`
reads as true from the runbook, but that row is written by the interview's
fire-and-forget finalizer, and a CHECK able to reject it converts a diagnostic
counter into a `running` row that lies. Counters are bounded below only.
Scorecard scores are bounded 0-100 because `_report_score` already clamps to
exactly that, so no write the parser produces can be refused. NULL stays legal
on every nullable score — these bound a value, they never demand one.
subject_marks is non-negative only: "out of 50" is one VTU scheme, and schemes
change without a migration.

THE LEDGER BOUND IS A LITERAL HERE AND DERIVED IN THE MODEL. A migration is a
record of what was applied; importing app.models.time_ledger would make this
file change meaning whenever the constant did. The model builds the same CASE
from SLOT_CAPACITY_HALVES and tests/test_codebase_guards.py pins the two
together. `ELSE -1` is the loud-failure arm: a CASE with no match yields NULL,
NULL is not FALSE, and a CHECK that is not FALSE passes — without it a slot the
CASE does not know would be unbounded, silently.

Every constraint was pre-flighted against the live dev data: zero violating
rows, every table. ADD CONSTRAINT ... CHECK takes ACCESS EXCLUSIVE and scans the
table; CREATE INDEX takes SHARE and blocks writes for its duration. At present
row counts both are sub-second. At scale this wants NOT VALID + VALIDATE
CONSTRAINT, and CREATE INDEX CONCURRENTLY outside the transaction, per
d5a1c8b30f47's note on what the lock actually costs.

OFFLINE-CLEAN and SET LOCAL, as before.

Revision ID: c2f7a9d41e63
Revises: a8e5c3f17b92
Create Date: 2026-09-07

"""

from typing import Sequence, Union

from alembic import op

revision: str = "c2f7a9d41e63"
down_revision: Union[str, None] = "a8e5c3f17b92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (constraint name, table, condition). Names and conditions match the models'
# CheckConstraint declarations byte for byte, so create_all() and this agree.
CHECKS: tuple[tuple[str, str, str], ...] = (
    (
        "ck_ledger_cell_half_hours",
        "time_ledger_cells",
        "half_hours >= 0 AND half_hours <= (CASE slot WHEN 'DAWN' THEN 8 WHEN 'MORNING' THEN 6 "
        "WHEN 'MIDDAY' THEN 6 WHEN 'AFTERNOON' THEN 6 WHEN 'EVENING' THEN 8 WHEN 'NIGHT' THEN 14 "
        "ELSE -1 END)",
    ),
    ("ck_student_badge_points", "student_badges", "points_awarded >= 0"),
    ("ck_capassess_score_range", "capability_assessments", "score >= 1 AND score <= 10"),
    ("ck_swoc_weight_range", "swoc_entries", "weight >= 1 AND weight <= 5"),
    (
        "ck_interview_session_counters",
        "interview_sessions",
        "turns_emitted >= 0 AND turns_persisted >= 0 AND answers_accepted >= 0",
    ),
    (
        "ck_interview_eval_scores",
        "interview_evaluations",
        "(overall_score IS NULL OR overall_score BETWEEN 0 AND 100) AND "
        "(communication_score IS NULL OR communication_score BETWEEN 0 AND 100) AND "
        "(domain_score IS NULL OR domain_score BETWEEN 0 AND 100) AND "
        "(structure_score IS NULL OR structure_score BETWEEN 0 AND 100)",
    ),
    ("ck_certification_required_hours", "certifications", "required_hours >= 0"),
    (
        "ck_cert_progress_range",
        "certification_progress",
        "progress_pct >= 0 AND progress_pct <= 100 AND hours_logged >= 0",
    ),
    (
        "ck_semester_result_range",
        "semester_results",
        "semester >= 1 AND (sgpa IS NULL OR sgpa BETWEEN 0 AND 10) "
        "AND (cgpa IS NULL OR cgpa BETWEEN 0 AND 10)",
    ),
    (
        "ck_subject_mark_nonnegative",
        "subject_marks",
        "credits >= 0 AND internal >= 0 AND external >= 0 AND total >= 0",
    ),
    (
        "ck_acadqual_marks",
        "academic_qualifications",
        "max_marks > 0 AND marks >= 0 AND marks <= max_marks",
    ),
    (
        "ck_placement_criteria_range",
        "placement_criteria",
        "min_reep_completion_pct BETWEEN 0 AND 100 AND min_attendance_pct BETWEEN 0 AND 100 "
        "AND min_cert_completion_pct BETWEEN 0 AND 100 AND min_cgpa BETWEEN 0 AND 10 "
        "AND max_live_backlogs >= 0 AND max_gap_months >= 0",
    ),
    ("ck_job_min_cgpa", "jobs", "min_cgpa IS NULL OR min_cgpa BETWEEN 0 AND 10"),
    (
        "ck_course_hours",
        "courses",
        "semester >= 1 AND teaching_hours >= 0 AND self_learning_hours_required >= 0",
    ),
    (
        "ck_enrollment_counts",
        "enrollments",
        "lectures_attended >= 0 AND lectures_total >= 0 AND lectures_attended <= lectures_total "
        "AND teaching_hours_attended >= 0 AND self_learning_hours_logged >= 0",
    ),
    (
        "ck_student_semester_target",
        "students",
        "current_semester >= 1 AND weekly_hour_target >= 0",
    ),
    (
        "ck_mock_attempt_score",
        "mock_attempts",
        "(score IS NULL OR score >= 0) AND (max_score IS NULL OR max_score > 0) "
        "AND (score IS NULL OR max_score IS NULL OR score <= max_score)",
    ),
    (
        "ck_english_baseline_score",
        "english_baselines",
        "overall_score IS NULL OR overall_score BETWEEN 0 AND 100",
    ),
    (
        "ck_english_section_score",
        "english_baseline_sections",
        "(score IS NULL OR score BETWEEN 0 AND 100) AND minutes >= 0",
    ),
    ("ck_timesheet_minutes", "time_sheet_entries", "minutes >= 0 AND minutes <= 1440"),
)

# (index name, table, column). Names follow SQLAlchemy's index=True convention,
# `ix_<table>_<column>`, so create_all() and this migration produce one index
# under one name rather than two under two.
FK_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_academic_courses_created_by_user_id", "academic_courses", "created_by_user_id"),
    ("ix_academic_specializations_created_by_user_id", "academic_specializations", "created_by_user_id"),
    ("ix_assistant_feedback_owner_user_id", "assistant_feedback", "owner_user_id"),
    ("ix_auth_tokens_created_by_user_id", "auth_tokens", "created_by_user_id"),
    ("ix_badge_evidence_approved_certification_id", "badge_evidence", "approved_certification_id"),
    ("ix_badge_evidence_upload_id", "badge_evidence", "upload_id"),
    ("ix_certification_progress_cert_code", "certification_progress", "cert_code"),
    ("ix_colleges_created_by_user_id", "colleges", "created_by_user_id"),
    ("ix_departments_created_by_user_id", "departments", "created_by_user_id"),
    ("ix_interview_sessions_consent_id", "interview_sessions", "consent_id"),
    ("ix_interview_sessions_conversation_id", "interview_sessions", "conversation_id"),
    ("ix_interview_turns_message_id", "interview_turns", "message_id"),
    ("ix_jobs_import_run_id", "jobs", "import_run_id"),
    ("ix_leave_requests_first_approver_user_id", "leave_requests", "first_approver_user_id"),
    ("ix_leave_requests_second_approver_user_id", "leave_requests", "second_approver_user_id"),
    ("ix_mentor_notes_mentor_id", "mentor_notes", "mentor_id"),
    ("ix_mock_attempts_evaluator_user_id", "mock_attempts", "evaluator_user_id"),
    ("ix_placement_offers_approved_by_id", "placement_offers", "approved_by_id"),
    ("ix_placement_offers_job_id", "placement_offers", "job_id"),
    ("ix_platform_call_sessions_candidate_id", "platform_call_sessions", "candidate_id"),
    ("ix_platform_call_sessions_interview_session_id", "platform_call_sessions", "interview_session_id"),
    ("ix_platform_candidates_user_id", "platform_candidates", "user_id"),
    ("ix_platform_recording_policies_updated_by", "platform_recording_policies", "updated_by"),
    ("ix_platform_time_limits_specialization_id", "platform_time_limits", "specialization_id"),
    ("ix_redesign_audit_events_actor_user_id", "redesign_audit_events", "actor_user_id"),
    ("ix_redesign_domain_jobs_tenant_id", "redesign_domain_jobs", "tenant_id"),
    ("ix_redesign_knowledge_document_versions_namespace_id", "redesign_knowledge_document_versions", "namespace_id"),
    ("ix_redesign_mentor_notebook_actions_entry_id", "redesign_mentor_notebook_actions", "entry_id"),
    ("ix_redesign_mentor_notebook_actions_owner_user_id", "redesign_mentor_notebook_actions", "owner_user_id"),
    ("ix_redesign_mentor_notebook_attachments_uploaded_by_user_id", "redesign_mentor_notebook_attachments", "uploaded_by_user_id"),
    ("ix_redesign_mentor_notebook_entries_author_user_id", "redesign_mentor_notebook_entries", "author_user_id"),
    ("ix_redesign_mentor_notebook_entries_mentor_id", "redesign_mentor_notebook_entries", "mentor_id"),
    ("ix_redesign_mentor_notebook_entry_revisions_author_user_id", "redesign_mentor_notebook_entry_revisions", "author_user_id"),
    ("ix_redesign_outbox_events_tenant_id", "redesign_outbox_events", "tenant_id"),
    ("ix_registration_rules_cohort_id", "registration_rules", "cohort_id"),
    ("ix_registrations_cohort_id", "registrations", "cohort_id"),
    ("ix_registrations_matched_rule_id", "registrations", "matched_rule_id"),
    ("ix_resumes_job_id", "resumes", "job_id"),
    ("ix_skill_claims_upload_id", "skill_claims", "upload_id"),
    ("ix_swoc_entries_author_user_id", "swoc_entries", "author_user_id"),
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    for name, table, condition in CHECKS:
        op.create_check_constraint(name, table, condition)
    for name, table, column in FK_INDEXES:
        op.create_index(name, table, [column])


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    for name, table, _column in reversed(FK_INDEXES):
        op.drop_index(name, table_name=table)
    for name, table, _condition in reversed(CHECKS):
        op.drop_constraint(name, table, type_="check")
