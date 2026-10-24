"""interview tracks, the college's policy, the retained score and the cap reset

Revision ID: a4f7d2c80b93
Revises: f1a7c93d5e26
Create Date: 2026-09-13

B5.1, B5.2, B6.1, B6.2, B6.4 and B6.6 of
docs/redesign-2026-09/04-backend-changes.md, in ONE revision because they are
one schema round behind one screen set — the admin Interviews board and the
student assistant read these tables together, and four revisions would leave
four windows in which the console is half migrated.

It chains onto f1a7c93d5e26 (Phase 4b).

------------------------------------------------------------------------------
THE SEEDED TRACKS ARE A SNAPSHOT, AND THEY CARRY `syllabus`
------------------------------------------------------------------------------

`_SEEDED_TRACKS` below is `app.interview_matrix.SPECIALIZATIONS` copied out as a
literal rather than imported, which is this repository's convention for every
migration: a migration must produce the same rows in two years' time, and an
import makes its output a function of whatever the application module says then.

The copy is field for field and was verified equal to that dict at the moment
it was taken, INCLUDING `syllabus` — which 04's column list
leaves out. Only `dm` has one (five modules plus the metric arithmetic the
student is expected to do out loud), `tests/test_interview_matrix.py` pins both
that fact and the absence of an answer key in it, and a track row without it
would quietly run the Digital Marketing interview as a generic CMO chat. There
is no screen on which that failure is visible.

The codes are `hr` / `dm` / `ba` / `fa`, UNCHANGED. That string is what
`?specialization=` carries, what every `interview_sessions.specialization` row
ever written holds, and what `question_bank_for()` keys on.

`nova_voice` is copied verbatim too (hr `kiara`, dm `tiffany`, ba `arjun`, fa
`matthew`), and those four are pinned by name in the matrix tests. Nova answers
an unknown voiceId with a ValidationException during the handshake, so a typo
here would be an interview that never starts.

------------------------------------------------------------------------------
NO HARDCODED COLLEGE
------------------------------------------------------------------------------

04 says "mapped to BGSCET's specializations". There is no BGSCET on a production
database unless somebody typed one in — `app.seed` creates it and refuses to run
on `ENV=prod` — so a `WHERE code = 'BGSCET'` would either crash or silently
mis-map. Following b2c9e04a7731 and c3a9f1e7d2b4: the migration resolves a
target only when exactly one candidate makes it unambiguous.

  * exactly one college  -> the four tracks are pinned to it;
  * more than one, or none -> `college_id` stays NULL, which reads as
    PROGRAMME-WIDE and is what those rows behave as today. Widening is
    recoverable from the console; silently narrowing is the bug nobody reports,
    because the mock interview simply stops being offered.
  * a specialization (and its course) is attached only when exactly one
    `academic_specializations` row in that one college has a `code` equal to the
    track code. That is a genuine match when it hits — an "HR" specialization
    and the `hr` track — and NULL every other time. The console maps the rest,
    which is what B5.3's picker needs and what no migration can guess.

`interview_bank_questions.college_id` is deliberately NOT backfilled, for
f1a7c93d5e26's reason: a NULL there means "every college", which is exactly what
those rows do today, and attaching them would empty somebody's question bank.
`track_id` IS backfilled, from the code, because it is a new join to a row that
did not exist and there is exactly one track per code after the seed.

------------------------------------------------------------------------------
NO NEW POSTGRES TYPE, AND NONE REUSED
------------------------------------------------------------------------------

`interview_tracks.code` and `interview_score_summaries.status` are plain
`sa.String`. docs/interview-engine-v3.md §6.1 and
`tests/test_interview_records.py`'s no-enum guard: a fifth track must stay a
DATA change, which is the whole reason this table exists. So none of AGENTS.md's
three enum gotchas arises anywhere in this revision.

------------------------------------------------------------------------------
TWO COLUMNS THAT LAND EMPTY ON PURPOSE
------------------------------------------------------------------------------

`interview_turns.question_id` (B6.6) has NO WRITER and will not get one in this
round. Nothing in either engine selects a bank question — the bank is rendered
into the instructions once, with the model told to rephrase rather than recite —
so there is no "the question it injected" to record. The column is added now
because it is free on a nullable column and expensive later on a table with
hundreds of thousands of rows. B5.5's `asked_count` / `avg_score` therefore
cannot be built, and must render as dashes rather than as zeros.

`interview_sessions.transcript_suppressed` is the flag B6.1's
`store_transcript=false` needs: with the transcript off, `turns_emitted` >
`turns_persisted` is the normal state, and that pair is AGENTS.md's runbook
signal for dropped writes. Without the flag the runbook fires on every interview
at such a college and stops being read.

------------------------------------------------------------------------------
WHAT THE DOWNGRADE DOES NOT BRING BACK
------------------------------------------------------------------------------

`downgrade()` drops all four tables. Every track an admin edited or added, every
college's policy, every retained score summary (including the ones the backfill
recovered for interviews whose evaluations are already gone) and every cap reset
go with them, and no backup of them exists inside this migration. The four
seeded tracks come back on the next upgrade; nothing else does.
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4f7d2c80b93"
down_revision: Union[str, None] = "f1a7c93d5e26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --------------------------------------------------------------------------- #
# The snapshot. See the docstring: copied out of interview_matrix.SPECIALIZATIONS
# field for field, syllabus included, and deliberately NOT imported from it.
# --------------------------------------------------------------------------- #

_SEEDED_TRACKS = [{'code': 'hr',
  'label': 'Human Resources (HR)',
  'persona': 'an empathetic yet compliant Chief Human Resources Officer '
             '(CHRO)',
  'frameworks': ['the STAR method',
                 'behavioral competencies',
                 'labor laws',
                 'conflict resolution',
                 'talent acquisition'],
  'sample_question': 'Walk me through how you would handle a sexual '
                     'harassment claim involving a top-performing executive.',
  'nova_voice': 'kiara',
  'syllabus': [],
  'position': 0},
 {'code': 'dm',
  'label': 'Digital Marketing (DM)',
  'persona': 'a growth-oriented, data-driven Chief Marketing Officer (CMO)',
  'frameworks': ['CAC/LTV ratios',
                 'ROAS',
                 'SEO/SEM strategies',
                 'A/B testing',
                 'funnel optimization',
                 'brand positioning'],
  'sample_question': 'Our CAC has increased by 40% on Meta ads this quarter. '
                     'What is your step-by-step diagnostic framework?',
  'nova_voice': 'tiffany',
  'syllabus': ['Module 1 - Foundations: digital versus traditional '
               'marketing, its advantages and limitations, the P-O-E-M '
               'framework (paid, owned and earned media), segmentation, '
               'target marketing, message customization, personalization, '
               'and the structure of a digital marketing plan.',
               'Module 2 - Display advertising: banner, video, rich-media '
               'and responsive formats; buying models; contextual, '
               'placement, interest, geographic, language, demographic and '
               'mobile targeting; remarketing; programmatic; YouTube '
               'advertising.',
               'Module 3 - Search advertising: ad placement and Ad Rank, '
               'keywords and keyword research, campaign construction, '
               'landing pages and landing-page relevance, performance '
               'reporting.',
               'Module 4 - Social and mobile: organic versus paid social, '
               'platform selection by audience and objective, engagement, '
               'influencer marketing, user-generated content, the mobile '
               'marketing toolkit, location-based services, QR codes, '
               'augmented reality, gamification, mobile analytics.',
               'Module 5 - SEO: crawling, indexing and ranking; on-page '
               'versus off-page SEO; long-tail keywords; backlinks; SEO '
               'maintenance; organic traffic; web analytics metrics.',
               'Metric arithmetic the student is expected to do out loud: '
               'CTR = clicks / impressions x 100; CPC = cost / clicks; CPM = '
               'cost / impressions x 1000; cost per conversion = cost / '
               'conversions; conversion rate = conversions / interactions x '
               '100; ROAS = attributed revenue / cost.'],
  'position': 1},
 {'code': 'ba',
  'label': 'Business Analytics (BA)',
  'persona': 'a highly technical, problem-solving Director of Analytics',
  'frameworks': ['SQL/Python logic',
                 'data modeling',
                 'predictive analytics',
                 'A/B test statistical significance',
                 'data visualization'],
  'sample_question': 'How would you design a machine learning pipeline to '
                     'predict customer churn using messy e-commerce logs?',
  'nova_voice': 'arjun',
  'syllabus': [],
  'position': 2},
 {'code': 'fa',
  'label': 'Financial Analytics (FA)',
  'persona': 'a sharp, risk-conscious Managing Director / CFO',
  'frameworks': ['DCF modeling',
                 'financial ratios',
                 'risk mitigation',
                 'valuation techniques',
                 'M&A frameworks'],
  'sample_question': 'Walk me through how a $10 depreciation expense flows '
                     'through the three financial statements.',
  'nova_voice': 'matthew',
  'syllabus': [],
  'position': 3}]


def _tracks_table() -> sa.TableClause:
    """The columns bulk_insert writes. The two ARRAY types must be declared or
    psycopg is handed a Python list with no idea what to make of it."""
    return sa.table(
        "interview_tracks",
        sa.column("id", sa.String),
        sa.column("code", sa.String),
        sa.column("label", sa.String),
        sa.column("persona", sa.Text),
        sa.column("frameworks", postgresql.ARRAY(sa.String)),
        sa.column("sample_question", sa.Text),
        sa.column("nova_voice", sa.String),
        sa.column("syllabus", postgresql.ARRAY(sa.String)),
        sa.column("college_id", sa.String),
        sa.column("course_id", sa.String),
        sa.column("specialization_id", sa.String),
        sa.column("enabled", sa.Boolean),
        sa.column("position", sa.Integer),
    )


def _resolve_spine(bind) -> tuple[str | None, dict[str, tuple[str, str]]]:
    """(the one college, or None) and {track code -> (specialization id, course
    id)} for the codes that match exactly one specialization in it.

    Both halves refuse to guess — see the module docstring. A deployment with
    two colleges gets (None, {}) and four programme-wide tracks.
    """
    colleges = bind.execute(sa.text("SELECT id FROM colleges")).scalars().all()
    if len(colleges) != 1:
        return None, {}
    college = colleges[0]

    rows = bind.execute(
        sa.text(
            "SELECT s.id, s.course_id, lower(btrim(s.code)) AS code"
            "  FROM academic_specializations s"
            "  JOIN academic_courses c ON c.id = s.course_id"
            "  JOIN departments d ON d.id = c.department_id"
            " WHERE d.college_id = :college"
        ).bindparams(college=college)
    ).all()

    by_code: dict[str, list[tuple[str, str]]] = {}
    for spec_id, course_id, code in rows:
        by_code.setdefault(code, []).append((spec_id, course_id))
    # Exactly one, or nothing. Two specializations sharing a code is a data
    # problem in the console; picking one of them here would hide it.
    return college, {
        code: pair[0] for code, pair in by_code.items() if len(pair) == 1
    }


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------ #
    # 1. The Specialization Matrix becomes a table (B5.1)
    # ------------------------------------------------------------------ #
    op.create_table(
        "interview_tracks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("persona", sa.Text(), nullable=False),
        sa.Column(
            "frameworks",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("sample_question", sa.Text(), nullable=False),
        sa.Column("nova_voice", sa.String(), nullable=False, server_default=""),
        sa.Column(
            "syllabus",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("college_id", sa.String(), nullable=True),
        sa.Column("course_id", sa.String(), nullable=True),
        sa.Column("specialization_id", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
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
        # No ondelete on any of the three: the spine's convention is that the
        # database refuses to delete a rung that still has rows under it.
        sa.ForeignKeyConstraint(["college_id"], ["colleges.id"]),
        sa.ForeignKeyConstraint(["course_id"], ["academic_courses.id"]),
        sa.ForeignKeyConstraint(
            ["specialization_id"], ["academic_specializations.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("college_id", "code", name="uq_interview_track_college_code"),
    )
    # NULLs are DISTINCT in Postgres, so the constraint above does not stop a
    # second programme-wide row for the same code. This does — and two of them
    # would make `specialization_for('hr')` a coin toss between two interviewers.
    op.create_index(
        "uq_interview_track_global_code",
        "interview_tracks",
        ["code"],
        unique=True,
        postgresql_where=sa.text("college_id IS NULL"),
    )
    # One index per foreign key, each LEADING with its own column
    # (test_every_foreign_key_column_is_indexed). `college_id` leads the unique
    # constraint above and needs none of its own.
    op.create_index("ix_interview_tracks_course_id", "interview_tracks", ["course_id"])
    op.create_index(
        "ix_interview_tracks_specialization_id",
        "interview_tracks",
        ["specialization_id"],
    )

    college, specs = _resolve_spine(bind)
    op.bulk_insert(
        _tracks_table(),
        [
            {
                "id": uuid4().hex,
                "code": track["code"],
                "label": track["label"],
                "persona": track["persona"],
                "frameworks": track["frameworks"],
                "sample_question": track["sample_question"],
                "nova_voice": track["nova_voice"],
                "syllabus": track["syllabus"],
                "college_id": college,
                "course_id": specs.get(track["code"], (None, None))[1],
                "specialization_id": specs.get(track["code"], (None, None))[0],
                "enabled": True,
                "position": track["position"],
            }
            for track in _SEEDED_TRACKS
        ],
    )

    # ------------------------------------------------------------------ #
    # 2. Bank questions belong to a track (B5.2)
    # ------------------------------------------------------------------ #
    op.add_column(
        "interview_bank_questions", sa.Column("track_id", sa.String(), nullable=True)
    )
    op.add_column(
        "interview_bank_questions", sa.Column("college_id", sa.String(), nullable=True)
    )
    op.create_foreign_key(
        "fk_interview_bank_track",
        "interview_bank_questions",
        "interview_tracks",
        ["track_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_interview_bank_college",
        "interview_bank_questions",
        "colleges",
        ["college_id"],
        ["id"],
    )
    op.create_index(
        "ix_interview_bank_questions_track_id", "interview_bank_questions", ["track_id"]
    )
    op.create_index(
        "ix_interview_bank_questions_college_id",
        "interview_bank_questions",
        ["college_id"],
    )
    # From the code, which is what every existing row already carries. Exactly
    # one track per code exists at this point, so the join cannot be ambiguous.
    # `college_id` stays NULL — see the docstring.
    op.execute(
        "UPDATE interview_bank_questions q"
        "   SET track_id = t.id"
        "  FROM interview_tracks t"
        " WHERE t.code = lower(btrim(q.track)) AND q.track_id IS NULL"
    )

    # ------------------------------------------------------------------ #
    # 3. The college's interview policy (B6.1, B6.4)
    # ------------------------------------------------------------------ #
    op.create_table(
        "interview_policies",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("college_id", sa.String(), nullable=False),
        sa.Column("course_id", sa.String(), nullable=True),
        sa.Column(
            "store_transcript", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column("store_audio", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default="180"),
        sa.Column("daily_cap", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("attempt_cap", sa.Integer(), nullable=False, server_default="20"),
        sa.Column(
            "time_limit_seconds", sa.Integer(), nullable=False, server_default="480"
        ),
        sa.Column("updated_by", sa.String(), nullable=True),
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
        # attempt_cap >= daily_cap or the student's practice allowance silently
        # becomes the spend ceiling. The rest are bounds that refuse a value
        # which would switch the feature off through a number that reads like a
        # limit.
        sa.CheckConstraint(
            "retention_days >= 1 AND daily_cap >= 1 AND attempt_cap >= daily_cap "
            "AND time_limit_seconds BETWEEN 60 AND 3600",
            name="ck_interview_policy_bounds",
        ),
        sa.ForeignKeyConstraint(["college_id"], ["colleges.id"]),
        sa.ForeignKeyConstraint(["course_id"], ["academic_courses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("college_id", "course_id", name="uq_interview_policy_scope"),
    )
    # The college's DEFAULT row, for the NULLs-are-distinct reason again: without
    # this a college could hold two defaults and the resolver would read
    # whichever the planner returned first.
    op.create_index(
        "uq_interview_policy_college_default",
        "interview_policies",
        ["college_id"],
        unique=True,
        postgresql_where=sa.text("course_id IS NULL"),
    )
    op.create_index(
        "ix_interview_policies_course_id", "interview_policies", ["course_id"]
    )
    # NO ROW IS SEEDED. The absence of a policy means `app/config.py`'s defaults,
    # which is exactly the behaviour every deployment has today; writing a row
    # per college here would put the same answer in two places and make the
    # console's "not configured" state unreachable.

    # ------------------------------------------------------------------ #
    # 4. The score summary that outlives the interview (B6.2)
    # ------------------------------------------------------------------ #
    op.create_table(
        "interview_score_summaries",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("student_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("track_code", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("overall_score", sa.Integer(), nullable=True),
        sa.Column("communication_score", sa.Integer(), nullable=True),
        sa.Column("domain_score", sa.Integer(), nullable=True),
        sa.Column("structure_score", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(overall_score IS NULL OR overall_score BETWEEN 0 AND 100) AND "
            "(communication_score IS NULL OR communication_score BETWEEN 0 AND 100) AND "
            "(domain_score IS NULL OR domain_score BETWEEN 0 AND 100) AND "
            "(structure_score IS NULL OR structure_score BETWEEN 0 AND 100)",
            name="ck_interview_summary_scores",
        ),
        # CASCADE on the student: the summary is their record and names nobody
        # without them.
        sa.ForeignKeyConstraint(
            ["student_id"], ["students.id"], ondelete="CASCADE"
        ),
        # SET NULL ON THE SESSION, AND THIS IS THE WHOLE POINT OF THE TABLE.
        # `retention.purge_expired` hard-deletes `interview_sessions` and lets
        # the database cascade to the turns and the evaluation. With CASCADE (or
        # the default) here the summary would go in that same statement and B6.2
        # would silently do nothing — correct in every test that does not run a
        # purge, and empty in production after six months.
        sa.ForeignKeyConstraint(
            ["session_id"], ["interview_sessions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", name="uq_interview_summary_session"),
    )
    op.create_index(
        "ix_interview_summary_student_started",
        "interview_score_summaries",
        ["student_id", "started_at"],
    )

    # ------------------------------------------------------------------ #
    # 5. The cap reset (B6.4)
    # ------------------------------------------------------------------ #
    op.create_table(
        "interview_cap_resets",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("student_id", sa.String(), nullable=False),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("by_user_id", sa.String(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        # B3.1's rule, applied to the other action whose effect is invisible a
        # day later: an empty box must not satisfy "say why".
        sa.CheckConstraint(
            "length(btrim(reason)) > 0", name="ck_interview_cap_reset_reason"
        ),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_interview_cap_reset_student_at",
        "interview_cap_resets",
        ["student_id", "at"],
    )
    op.create_index(
        "ix_interview_cap_resets_by_user_id", "interview_cap_resets", ["by_user_id"]
    )

    # ------------------------------------------------------------------ #
    # 6. Two columns that land empty on purpose (B6.6, B6.1)
    # ------------------------------------------------------------------ #
    op.add_column(
        "interview_turns", sa.Column("question_id", sa.String(), nullable=True)
    )
    # SET NULL: retiring a question must never delete the record of an interview
    # in which it was asked.
    op.create_foreign_key(
        "fk_interview_turn_question",
        "interview_turns",
        "interview_bank_questions",
        ["question_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_interview_turns_question_id", "interview_turns", ["question_id"]
    )
    op.add_column(
        "interview_sessions",
        sa.Column(
            "transcript_suppressed",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("interview_sessions", "transcript_suppressed")
    op.drop_index("ix_interview_turns_question_id", table_name="interview_turns")
    op.drop_constraint("fk_interview_turn_question", "interview_turns", type_="foreignkey")
    op.drop_column("interview_turns", "question_id")

    op.drop_index("ix_interview_cap_resets_by_user_id", table_name="interview_cap_resets")
    op.drop_index("ix_interview_cap_reset_student_at", table_name="interview_cap_resets")
    op.drop_table("interview_cap_resets")

    op.drop_index(
        "ix_interview_summary_student_started", table_name="interview_score_summaries"
    )
    op.drop_table("interview_score_summaries")

    op.drop_index("ix_interview_policies_course_id", table_name="interview_policies")
    op.drop_index(
        "uq_interview_policy_college_default", table_name="interview_policies"
    )
    op.drop_table("interview_policies")

    op.drop_index(
        "ix_interview_bank_questions_college_id", table_name="interview_bank_questions"
    )
    op.drop_index(
        "ix_interview_bank_questions_track_id", table_name="interview_bank_questions"
    )
    op.drop_constraint(
        "fk_interview_bank_college", "interview_bank_questions", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_interview_bank_track", "interview_bank_questions", type_="foreignkey"
    )
    op.drop_column("interview_bank_questions", "college_id")
    op.drop_column("interview_bank_questions", "track_id")

    op.drop_index("ix_interview_tracks_specialization_id", table_name="interview_tracks")
    op.drop_index("ix_interview_tracks_course_id", table_name="interview_tracks")
    op.drop_index("uq_interview_track_global_code", table_name="interview_tracks")
    op.drop_table("interview_tracks")
