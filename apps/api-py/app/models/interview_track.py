"""The Specialization Matrix as rows the office can edit (B5.1).

`app/interview_matrix.py` holds four `Specialization` rows as a frozen dict, and
that was right while there was one college and one MBA. It is the wrong shape
the moment a second college wants a fifth track, or wants the DM interviewer to
ask about the syllabus IT teaches: the only way to add a track today is a pull
request.

So the catalogue becomes a table, and this model is **the dataclass field for
field**. Anything the dataclass carries that a row does not is a track that
silently interviews differently from the one in code — the DM syllabus is the
example that made this rule, because 04-backend-changes.md's column list omits
it and a row without it downgrades the Digital Marketing interview to the
generic one with nothing on any screen to say so.

WHAT IS *NOT* HERE, AND WHY. `Specialization.question_bank` is filled at
RUNTIME, from `interview_bank_questions` (`app/interview_bank.py`), and must
never be stored on the track: two places holding the office's questions is two
places to edit and one to forget.

RULE 1 IS UNTOUCHED, and it is worth saying where a reader will look for it. A
track row is STAFF-AUTHORED TEXT, exactly like a bank question: a persona, a
framework list, a sample question, a syllabus. No student field is composed into
it, `build_instructions` still assembles a fixed string per (track, phase) pair,
and nothing in `app/interview_nova.py` or `app/routers/interview.py` learns
where the row came from. The engine must not import this model — see
`app/routers/interview.py`'s header — which is why the compile step reads a row
and hands the engine an `interview_matrix.Specialization`, never an ORM object.

NO PG ENUM ON `code`, and this is the table the rule is really about.
`docs/interview-engine-v3.md` §6.1 and `tests/test_interview_records.py`'s
no-enum guard exist so that a fifth track stays a DATA change; making `code` an
enum here would make it a `CREATE TYPE` migration, which is the exact regression
this whole table is meant to end.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, validates

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class InterviewTrack(Base):
    """One row of the Specialization Matrix, editable by the office.

    Mirrors `interview_matrix.Specialization`. Read the column comments before
    changing any of them: three of these columns are load-bearing in a way that
    is invisible at the call site.
    """

    __tablename__ = "interview_tracks"
    __table_args__ = (
        # One track per code per college. NULL college_id is PROGRAMME-WIDE —
        # the same reading `jobs.college_id` and `placement_criteria.college_id`
        # already have, and the reading the four seeded rows rely on, because
        # the migration cannot know which college a deployment belongs to.
        UniqueConstraint("college_id", "code", name="uq_interview_track_college_code"),
        # ...and Postgres treats NULLs as DISTINCT, so the constraint above does
        # not stop a second programme-wide 'hr'. This partial index does. Two
        # programme-wide rows for one code would make `specialization_for('hr')`
        # a coin toss between two different interviewers.
        Index(
            "uq_interview_track_global_code",
            "code",
            unique=True,
            postgresql_where=text("college_id IS NULL"),
        ),
        # One index per foreign key, each LEADING with its own column
        # (test_every_foreign_key_column_is_indexed). `college_id` already leads
        # the unique constraint above and so needs none of its own.
        Index("ix_interview_tracks_course_id", "course_id"),
        Index("ix_interview_tracks_specialization_id", "specialization_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    #: THE `?specialization=` VALUE, and the reason the seeded four keep the
    #: codes hr / dm / ba / fa exactly: a student's bookmarked link, the
    #: client's track picker and every `interview_sessions.specialization` row
    #: already written all carry this string. Renaming one is a data migration
    #: of the history, not a rename.
    code: Mapped[str] = mapped_column(String)
    #: "Human Resources (HR)". Rendered into the §Specialization heading of the
    #: composed instructions, and shown to the student on the track picker.
    label: Mapped[str] = mapped_column(String)
    #: A NOUN PHRASE, NEVER A SENTENCE. `build_instructions` embeds this as
    #: "you are {persona}", so "an empathetic yet compliant Chief Human
    #: Resources Officer (CHRO)" composes and "You are an empathetic CHRO."
    #: composes into "you are You are an empathetic CHRO.". Nothing downstream
    #: can catch that: the model is handed broken grammar and simply interviews
    #: slightly worse, on every interview, for everyone on that track. The form
    #: that edits this column must say so on the field.
    persona: Mapped[str] = mapped_column(Text)
    #: What a PRACTITIONER is assessed on, pitched at the industry bar.
    #: `tests/test_interview_matrix.py` requires at least four for each of the
    #: four seeded codes.
    frameworks: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, server_default=text("'{}'::text[]")
    )
    #: The question PROBING must work in early — rephrased naturally by the
    #: model, never recited.
    sample_question: Mapped[str] = mapped_column(Text)
    #: The voice this role speaks with, frozen onto the session at the handshake
    #: because a model cannot be re-voiced once it has emitted audio.
    #:
    #: VALIDATED, and this is the one column where a bad value is not a
    #: cosmetic problem: Nova answers an unknown `voiceId` with a
    #: ValidationException that kills the stream AT THE HANDSHAKE — an interview
    #: that never starts, with nothing in the UI naming the cause. Empty means
    #: "use the configured generic voice" (`nova_voice_for` falls back), which
    #: is a legal state and how a row that predates the column behaves.
    nova_voice: Mapped[str] = mapped_column(String, default="", server_default="")
    #: WHAT THIS COHORT WAS ACTUALLY TAUGHT, module by module — a different
    #: interview from `frameworks`, and the column 04-backend-changes.md's list
    #: leaves out. Only `dm` has one today and `tests/test_interview_matrix.py`
    #: pins both that fact and the absence of an answer key in it. Empty is the
    #: normal state: `build_instructions` omits the block entirely rather than
    #: composing an empty heading.
    syllabus: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, server_default=text("'{}'::text[]")
    )

    # --- where the track hangs on the spine ---------------------------------
    # All three NULLABLE, all three meaning "wider than that rung". NULL on all
    # of them is the programme-wide track, which is what the migration writes
    # for the seeded four on any deployment with more than one college: pinning
    # them to a guessed college would make the mock interview vanish for every
    # student in the other one. No `ondelete`, the spine's convention: the
    # database refuses to delete a rung that still has rows under it.
    college_id: Mapped[str | None] = mapped_column(
        ForeignKey("colleges.id"), nullable=True
    )
    course_id: Mapped[str | None] = mapped_column(
        ForeignKey("academic_courses.id"), nullable=True
    )
    #: The rung B5.3 reads: a batch carries `cohorts.specialization_id`, and the
    #: track mapped to it is the one the student's picker preselects.
    specialization_id: Mapped[str | None] = mapped_column(
        ForeignKey("academic_specializations.id"), nullable=True
    )

    #: Disabled tracks stay readable — `interview_sessions.specialization`
    #: values already written still resolve to a label — but are not offered and
    #: are refused at the handshake. Deleting a track instead would leave every
    #: past interview on it labelled by a dangling code.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    #: Display order on the picker, the same load-bearing-not-cosmetic role
    #: `interview_bank_questions.position` has.
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: Audit stamp, a plain column and NOT a foreign key — the precedent is
    #: `InterviewBankQuestion.created_by_user_id` next door, for the same
    #: reason: deleting the author must never delete the track, and a KEPT table
    #: holding a live `users` FK is a row `purge_people` has to null before it
    #: can delete an account.
    created_by_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @validates("nova_voice")
    def _validate_nova_voice(self, _key: str, value: str | None) -> str:
        """Refuse a voice Nova will not accept, at the point of writing.

        NOT a CHECK constraint and not a PG enum: AWS adds voices, and a
        deployment that cannot use a new one until somebody ships a migration is
        the same trap §6.1 describes for `code`. This is the same set
        `nova_voice_for` falls back from — but falling back at the handshake
        logs a line nobody reads, while refusing here answers the admin form
        with a 422 naming the voice they typed.

        Imported lazily so `app.models` never pulls the engine's module in at
        import time; `interview_matrix` is I/O-free, but the models package is
        imported by Alembic, by both purge modules and by every CLI, and none of
        them has any business loading the interviewer.
        """
        from ..interview_matrix import KNOWN_NOVA_VOICES

        cleaned = (value or "").strip().lower()
        if cleaned and cleaned not in KNOWN_NOVA_VOICES:
            raise ValueError(
                f"{cleaned!r} is not a voice Amazon Nova 2 Sonic accepts. Nova "
                "answers an unknown voiceId with a ValidationException during "
                "the handshake, so this would be an interview that never "
                "starts. One of: " + ", ".join(sorted(KNOWN_NOVA_VOICES))
            )
        return cleaned
