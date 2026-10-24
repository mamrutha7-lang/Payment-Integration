"""students.cohort_id + students.mentor_id indexes

Revision ID: b41c9e2d7f05
Revises: c7e91a4d0f36
Create Date: 2026-08-20
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b41c9e2d7f05'
down_revision: Union[str, None] = 'c7e91a4d0f36'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The two hottest scope filters in the app, and (until now) the only ones with
# no index behind them (2026-08 scalability audit). cohort_id is what
# /student/leaderboards ranks a cohort by — at a 5,000-row `students` table
# every board build was a seq scan — and mentor_id is what rule 2's
# _assert_can_access_student narrows a MENTOR's world by, on every staff
# request for a student. Both are selective at target scale (cohorts of
# hundreds, mentor groups of tens) and both columns are written rarely
# (enrolment, mentor reassignment), so the index upkeep is noise.
#
# Plain CREATE INDEX, not CONCURRENTLY: today's tables are thousands of rows
# and this runs inside Alembic's transaction. The day an index lands on a
# table that is already millions of rows (messages, interview_turns), that
# migration should use op.get_bind().execute + CONCURRENTLY outside the
# transaction instead — this comment is the reminder the audit asked for.


def upgrade() -> None:
    op.create_index('ix_students_cohort_id', 'students', ['cohort_id'])
    op.create_index('ix_students_mentor_id', 'students', ['mentor_id'])


def downgrade() -> None:
    op.drop_index('ix_students_mentor_id', table_name='students')
    op.drop_index('ix_students_cohort_id', table_name='students')
