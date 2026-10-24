"""The Specialization Matrix, read from rows instead of from code (B5.1).

`app/interview_matrix.py` holds four `Specialization` rows as a frozen dict and
MUST KEEP DOING SO. Three reasons, and they are all load-bearing:

  * that module is I/O-free by construction — it is imported by the engine, and
    the engine is the one thing in this stack that must never reach a database
    on the hot path;
  * `app/routers/interview.py`'s header and `app/models/interview_track.py`'s
    both say the engine imports no ORM model. `interview_matrix` importing
    `models.interview_track` would make that false in one line;
  * the constant is the FALLBACK. A deployment whose `interview_tracks` table is
    empty — every deployment, between `alembic upgrade head` and the seed inside
    the same migration, and any deployment whose office deleted a row — still
    runs the four interviews it ran yesterday.

So this module is the compile step, and it sits exactly where
`app/interview_bank.py` sits: it holds the ORM imports, it opens its own
session, and the router calls it OFF THE EVENT LOOP.

WHY THAT LAST SENTENCE IS THE WHOLE POINT OF THIS MODULE. Before B5.1 the
handshake did

    specialization = get_specialization(spec_key)                  # in memory
    specialization = await asyncio.to_thread(with_question_bank, …) # a SELECT

and the split was deliberate: the first line is a dict lookup and costs nothing,
the second is a query and was moved to a worker thread on purpose, because
`app/routers/interview.py` runs every live interview's audio on one event loop
and a single slow SELECT there stalls all of them. Turning the first line into a
query without moving it would have put a database round trip back on that loop —
the exact regression `with_question_bank`'s `to_thread` exists to prevent, and
one that shows up as *other people's* interviews stuttering, not as anything
wrong with the interview that caused it.

`resolve_specialization` is therefore the ONE call the handshake makes: one
thread hop, one session, the track row and its bank read together.

RULE 1 IS UNTOUCHED, and the one thing here that reads a student needs saying.
`resolve_specialization` takes a `student_id` to answer "which college's row",
because two colleges may each have an `hr` track and picking the wrong one is a
different interviewer. That id is used to look up a COLLEGE and is then dropped:
what the engine receives is a `Specialization` of staff-authored text — persona,
frameworks, syllabus, sample question, the office's bank — exactly as before. No
student field is composed into an instruction, and nothing student-shaped
crosses `asyncio.to_thread` into the engine.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from typing import Final

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .interview_bank import render
from .interview_matrix import SPECIALIZATIONS, Specialization
from .models.interview_bank import InterviewBankQuestion
from .models.interview_track import InterviewTrack

log = logging.getLogger("app.interview_tracks")

#: A track code is what `?specialization=` carries and what every
#: `interview_sessions.specialization` row already holds, so it is a URL-safe
#: slug and nothing else. Short, because it is typed into a query string by the
#: client and read back on a records grid.
TRACK_CODE_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_-]{1,19}$")

#: Codes the office may not take. "general" is the generic interview that
#: predates the matrix — `?specialization=` absent — and a track claiming it
#: would make the two indistinguishable on the records grid.
RESERVED_TRACK_CODES: Final[frozenset[str]] = frozenset({"general", "none", "default"})


# --------------------------------------------------------------------------- #
# The compile step — pure, no I/O, and therefore testable without Postgres
# --------------------------------------------------------------------------- #


def track_to_specialization(
    row: InterviewTrack, bank: tuple[str, ...] = ()
) -> Specialization:
    """One catalogue row → the dataclass the engine takes.

    An ORM object NEVER crosses into the engine: `NovaSonicSession` is handed an
    `interview_matrix.Specialization`, the same frozen dataclass it took when
    the four rows were a dict, so neither engine nor the caps nor the recorder
    nor the writers learn where the row came from. That is what makes B5.1 a
    data change rather than an engine change.

    `bank` is appended after whatever the row itself carries, the same order
    `with_question_bank` uses — a track that ships with questions keeps them
    first.
    """
    return Specialization(
        key=row.code,
        label=row.label,
        persona=row.persona,
        frameworks=tuple(str(f) for f in (row.frameworks or ())),
        sample_question=row.sample_question,
        nova_voice=row.nova_voice or "",
        syllabus=tuple(str(s) for s in (row.syllabus or ())),
        question_bank=tuple(bank),
    )


# --------------------------------------------------------------------------- #
# Persona shape — the failure nothing downstream can catch
# --------------------------------------------------------------------------- #

#: Words that begin an INSTRUCTION rather than name a person. `build_instructions`
#: writes "you are {persona}", so "Act as a CHRO" composes into "you are Act as a
#: CHRO" and "You are a CHRO" into "you are You are a CHRO". The model is handed
#: broken grammar and simply interviews slightly worse — on every interview, for
#: everyone on that track, with nothing on any screen saying so. That is why
#: these are a REFUSAL and not a warning.
_PERSONA_LEADING_VERBS: Final[frozenset[str]] = frozenset(
    {
        "act", "assume", "be", "behave", "imagine", "interview", "play",
        "pretend", "respond", "roleplay", "speak", "you", "you're", "youre",
    }
)
# "take", "talk" and "your" were in this set and came out again: "Take-charge
# Head of Operations" and "Your college's CHRO" are personas somebody may
# reasonably write, and a refusal list that eats them is a field an admin fights
# rather than uses — which is how the check gets deleted. The list is for
# openings that can only be an INSTRUCTION.


def persona_refusals(persona: str) -> list[str]:
    """Why this persona cannot be stored. Empty means it composes.

    Two signals, both unambiguous and both fatal to the composed prompt:
    a SENTENCE-ENDING mark at the end, and a SECOND-PERSON or IMPERATIVE opening.
    Everything else is a matter of taste and comes back as a warning instead —
    refusing on taste would make the field unusable for a college whose CFO
    persona legitimately reads "a Managing Director (India & SEA)".
    """
    text = (persona or "").strip()
    problems: list[str] = []
    if not text:
        return ["persona is required: build_instructions embeds it as 'you are {persona}'."]
    if text[-1] in ".!?":
        problems.append(
            "persona must be a NOUN PHRASE, not a sentence — drop the "
            f"{text[-1]!r}. It is embedded as \"you are {{persona}}\", so a "
            "full stop composes into \"you are a sharp CFO..\""
        )
    first = re.split(r"[^A-Za-z']+", text.lower(), maxsplit=1)[0]
    if first in _PERSONA_LEADING_VERBS:
        problems.append(
            f"persona must be a NOUN PHRASE, not an instruction: it begins with "
            f"{first!r} and is embedded as \"you are {{persona}}\", which would "
            f"compose into \"you are {text[:40]}…\". Write who the interviewer "
            "IS — \"an empathetic yet compliant Chief Human Resources Officer\"."
        )
    return problems


def persona_warnings(persona: str) -> list[str]:
    """Things worth saying on the form that are not worth refusing over.

    Returned in the response body rather than raised, because every one of these
    is legitimate somewhere and a 422 on a judgement call is a field an admin
    fights instead of uses.
    """
    text = (persona or "").strip()
    notes: list[str] = []
    if not text:
        return notes
    if text[0].isupper() and not text.split(" ", 1)[0].isupper():
        notes.append(
            "The four personas in the matrix start lower case (\"an empathetic "
            "yet compliant Chief Human Resources Officer\") because they are "
            "embedded mid-sentence after \"you are\"."
        )
    if len(text) < 12:
        notes.append(
            "A persona this short gives the interviewer very little to be: the "
            "shipped four name a seniority, a temperament and a function."
        )
    return notes


# --------------------------------------------------------------------------- #
# Reading the catalogue
# --------------------------------------------------------------------------- #


def _visible_tracks(college_id: str | None) -> Select:
    """Rows that apply to one college: its own, plus the programme-wide ones.

    NULL `college_id` IS PROGRAMME-WIDE, the reading `jobs.college_id` already
    has and the reading the four seeded rows depend on — the migration writes
    them NULL on any deployment with more than one college, because pinning them
    to a guessed college would make the mock interview vanish for everybody in
    the other one.
    """
    if college_id is None:
        return select(InterviewTrack).where(InterviewTrack.college_id.is_(None))
    return select(InterviewTrack).where(
        or_(
            InterviewTrack.college_id == college_id,
            InterviewTrack.college_id.is_(None),
        )
    )


def college_of_student(db: Session, student_id: str | None) -> str | None:
    """The college a student hangs under, or None.

    Delegates to `governance.ancestry_of_student` rather than writing the walk
    again: that function reads BOTH department pointers — `cohorts.department_id`
    and `students.department_id` — and the bug it exists to prevent (an override
    hung on a department reaching the seated students and silently missing every
    unseated one) is the same bug a second copy here would reintroduce, in a
    place where the symptom is "this student gets a different interviewer".
    """
    if not student_id:
        return None
    from .governance import ancestry_of_student
    from .models.governance import ScopeLevel

    return dict(ancestry_of_student(db, student_id)).get(ScopeLevel.COLLEGE)


def track_for_code(
    db: Session, code: str, *, college_id: str | None = None
) -> InterviewTrack | None:
    """The track row that applies to this code for this college, or None.

    THE COLLEGE'S OWN ROW WINS over the programme-wide one. That is the whole
    reason the lookup takes a college at all: `uq_interview_track_college_code`
    permits one `hr` per college plus one programme-wide `hr`, and a lookup that
    ignored the college would be a coin toss between two different interviewers
    — which is the failure `uq_interview_track_global_code` was added to prevent
    at the OTHER end, between two programme-wide rows.

    Ordering rather than two queries: `college_id IS NULL` sorts last, so the
    specific row is first whenever there is one.
    """
    key = (code or "").strip().lower()
    if not key:
        return None
    if college_id is not None:
        return db.scalars(
            _visible_tracks(college_id)
            .where(InterviewTrack.code == key)
            .order_by(
                InterviewTrack.college_id.is_(None),
                InterviewTrack.position,
                InterviewTrack.id,
            )
        ).first()

    # NO COLLEGE — a student seated in no batch and filed under no department,
    # which is an ordinary state (the console shows a list of them). Narrowing
    # to `college_id IS NULL` would be wrong on the commonest deployment there
    # is: migration `a4f7d2c80b93` PINS the four seeded rows to the college when
    # there is exactly one, so an unfiled student would miss every persona the
    # office had edited and silently get the frozen constant instead.
    #
    # So: the repository's own `if len(colleges) == 1` precedent
    # (`b2c9e04a7731`, and the seed's `_resolve_spine`). One row for this code
    # anywhere is unambiguous and is used. Several, and the programme-wide one
    # wins if there is one; otherwise nothing is returned, because picking
    # between two colleges' interviewers for a student who belongs to neither is
    # the coin toss `uq_interview_track_global_code` exists to prevent.
    rows = db.scalars(
        select(InterviewTrack)
        .where(InterviewTrack.code == key)
        .order_by(
            InterviewTrack.college_id.is_(None).desc(),
            InterviewTrack.position,
            InterviewTrack.id,
        )
    ).all()
    if len(rows) == 1:
        return rows[0]
    return rows[0] if rows and rows[0].college_id is None else None


def bank_for_track(
    db: Session, *, code: str, track_id: str | None, college_id: str | None
) -> tuple[str, ...]:
    """This track's enabled questions, in order, as the "[phase] text" prompt
    lines `build_instructions` expects.

    FILTERED BY THE TRACK ROW (B5.2) **and** by the code, not by one or the
    other, and the `OR` is the compatibility half. `track_id` was backfilled
    from `track` by migration `a4f7d2c80b93`, so every question written before
    Phase 4c has one — but a question written by a caller that does not set it
    (and the endpoint that wrote questions for a year did not) would silently
    vanish from the interview if this filtered on `track_id` alone. A question
    disappearing from the bank is invisible: the interview still runs, it just
    stops covering what the office said to cover.

    College narrows the same way the track does: NULL is programme-wide.
    """
    conditions = [InterviewBankQuestion.track == code]
    if track_id is not None:
        conditions.append(InterviewBankQuestion.track_id == track_id)
    clause = or_(*conditions) if len(conditions) > 1 else conditions[0]
    stmt = (
        select(InterviewBankQuestion)
        .where(clause, InterviewBankQuestion.enabled.is_(True))
        .order_by(InterviewBankQuestion.position, InterviewBankQuestion.created_at)
    )
    if college_id is not None:
        stmt = stmt.where(
            or_(
                InterviewBankQuestion.college_id == college_id,
                InterviewBankQuestion.college_id.is_(None),
            )
        )
    else:
        stmt = stmt.where(InterviewBankQuestion.college_id.is_(None))
    return tuple(render(q.phase, q.text) for q in db.scalars(stmt).all())


def resolve_specialization(
    code: str | None, *, student_id: str | None = None
) -> Specialization | None:
    """The ONE call the WebSocket handshake makes, off the event loop.

    Returns None for three different situations, and the caller treats all three
    the same way it always has (`app/routers/interview.py`: an absent key is the
    generic interview, a present-but-unresolvable key is close 4010):

      * no code at all — the generic interview that predates the matrix. NO
        QUERY IS RUN, so the handshake of a generic interview costs exactly what
        it did before this module existed;
      * a code that names no row and no constant — a client bug or a hand-rolled
        socket;
      * a code whose row is DISABLED. Deliberately not a fallback to the
        constant: disabling `hr` must actually stop HR interviews, and falling
        back would make the switch a no-op for precisely the four tracks that
        have a constant. The client's picker only offers enabled tracks, so a
        student reaches this only with a stale tab or a typed URL.

    THE CONSTANT IS THE FALLBACK WHEN THERE IS NO ROW, which is what keeps a
    deployment mid-upgrade — and the whole test suite, which has no
    `interview_tracks` rows unless a test writes one — running the four
    interviews it ran yesterday.
    """
    key = (code or "").strip().lower()
    if not key:
        return None
    with SessionLocal() as db:
        college_id = college_of_student(db, student_id)
        row = track_for_code(db, key, college_id=college_id)
        if row is not None and not row.enabled:
            log.info("interview track %r is disabled for college %s", key, college_id)
            return None
        base = track_to_specialization(row) if row is not None else SPECIALIZATIONS.get(key)
        if base is None:
            return None
        # THE BANK FOLLOWS THE TRACK, not the student. A question is stamped
        # with its track's college when it is written (`interview_bank`'s
        # `_track_pointers`), so asking for the student's college here would
        # drop every question of a college-pinned track for a student who is
        # not yet filed under one — the same unfiled student the lookup above
        # already resolves a row for.
        bank = bank_for_track(
            db,
            code=key,
            track_id=row.id if row is not None else None,
            college_id=row.college_id if row is not None else college_id,
        )
    if not bank:
        return base
    return dataclasses.replace(
        base, question_bank=tuple(base.question_bank) + bank
    )


def enabled_tracks(db: Session, *, college_id: str | None = None) -> list[InterviewTrack]:
    """The tracks a student in this college may be offered, in display order.

    The read behind B5.3's "Practise another track" list and behind the admin
    picker. A COLLEGE'S OWN ROW SHADOWS the programme-wide one of the same code,
    for `track_for_code`'s reason: two rows, one code, one interview.
    """
    rows = db.scalars(
        _visible_tracks(college_id)
        .where(InterviewTrack.enabled.is_(True))
        .order_by(InterviewTrack.position, InterviewTrack.code)
    ).all()
    by_code: dict[str, InterviewTrack] = {}
    for row in rows:
        seen = by_code.get(row.code)
        if seen is None or (seen.college_id is None and row.college_id is not None):
            by_code[row.code] = row
    return sorted(by_code.values(), key=lambda r: (r.position, r.code))


def catalogue_codes(db: Session, *, college_id: str | None = None) -> tuple[str, ...]:
    """Every code a student in this college could send as `?specialization=`.

    The table's codes UNIONED WITH the constant's, because the constant is still
    the fallback for a code with no row: a deployment that has added `ops` but
    never seeded the four still answers `?specialization=hr`, and a picker built
    from the table alone would stop offering an interview that still works.
    """
    rows = tuple(t.code for t in enabled_tracks(db, college_id=college_id))
    return rows + tuple(k for k in SPECIALIZATIONS if k not in set(rows))


# --------------------------------------------------------------------------- #
# Who may author on which track (B5.2 — the capability, scoped by B1.2)
# --------------------------------------------------------------------------- #

#: The one key over the whole catalogue: the tracks AND the questions hanging on
#: them. Defined here rather than in either router because both routers need it
#: and "may you author the interview" is one decision — splitting it into two
#: keys would let the office grant a faculty member questions on a track they
#: may not see.
AUTHORING_CAPABILITY: Final[str] = "admin.interview_questions"


def may_write_track(
    db: Session,
    session: dict,
    *,
    college_id: str | None,
    course_id: str | None,
    specialization_id: str | None,
) -> bool:
    """Would `require_capability(..., target=…)` let this session write here?

    ASKED THROUGH THE FUNCTION THAT REFUSES, so a greyed control and a 403 can
    never disagree, and so this never has to learn separately about the role
    baseline, about mentor functions, or about `reaches_target`'s rule that an
    empty ancestry is reached by no scoped grant. It is a projection of the
    fence, never a second copy of it: every write still calls
    `require_capability` itself.

    NOTE WHAT THE EMPTY ANCESTRY MEANS HERE, because it is the whole scope story
    for this area: a PROGRAMME-WIDE track hangs on no rung, so only an unscoped
    holder — the Main Admin, or somebody holding a programme-wide grant — may
    edit one or author questions against it. A college-scoped holder READS the
    programme-wide tracks (their students sit them) and writes their own
    college's row, which shadows it at the handshake.
    """
    from fastapi import HTTPException

    from .governance import ancestry_of_interview_track, require_capability

    try:
        require_capability(
            db,
            session,
            AUTHORING_CAPABILITY,
            target=ancestry_of_interview_track(
                db,
                college_id=college_id,
                course_id=course_id,
                specialization_id=specialization_id,
            ),
        )
    except HTTPException:
        return False
    return True


def writable_tracks(db: Session, session: dict) -> list[InterviewTrack]:
    """Every ENABLED track row this session may write, in display order.

    Disabled rows are excluded on purpose: a track that is switched off is
    refused at the handshake, so a question authored against it would be written
    into a bank nothing reads. Re-enable it first — which is a PATCH on the
    track, on the same screen.
    """
    rows = db.scalars(
        select(InterviewTrack)
        .where(InterviewTrack.enabled.is_(True))
        .order_by(InterviewTrack.position, InterviewTrack.code, InterviewTrack.id)
    ).all()
    return [
        row
        for row in rows
        if may_write_track(
            db,
            session,
            college_id=row.college_id,
            course_id=row.course_id,
            specialization_id=row.specialization_id,
        )
    ]


def authorable_codes(db: Session, session: dict) -> tuple[str, ...]:
    """Codes this session may attach a bank question to.

    The rows it may write, plus — ONLY FOR AN UNSCOPED HOLDER — the
    `interview_matrix` constants, which have no row and are therefore
    programme-wide. That last clause is the scope fence on the question bank: a
    college-scoped holder writing against `hr` when `hr` is the shipped constant
    would be putting a question into every college's HR interview through a
    screen they hold for one college.
    """
    codes = tuple(dict.fromkeys(row.code for row in writable_tracks(db, session)))
    if may_write_track(db, session, college_id=None, course_id=None, specialization_id=None):
        codes += tuple(k for k in SPECIALIZATIONS if k not in set(codes))
    return codes


def writable_track_for_code(
    db: Session, session: dict, code: str
) -> InterviewTrack | None:
    """The row a new question on `code` should hang on, or None for a code that
    has no row (the constants, authorable only by an unscoped holder).

    A COLLEGE'S OWN ROW WINS over the programme-wide one, the same precedence
    `track_for_code` applies at the handshake — so a college admin's question
    lands on their college's track and is asked of their students, not of
    everybody's.
    """
    key = (code or "").strip().lower()
    rows = [row for row in writable_tracks(db, session) if row.code == key]
    if not rows:
        return None
    rows.sort(key=lambda r: (r.college_id is None, r.position, r.id))
    return rows[0]


def readable_codes(db: Session, session: dict) -> tuple[str, ...]:
    """Codes this session may LIST the questions of — wider than `authorable_codes`.

    Reading and writing are two different reaches here and conflating them
    breaks the screen. `scope_views.interview_track_scope_clause` shows a
    college-scoped holder the PROGRAMME-WIDE tracks as well as their own,
    because those are the interviews their students actually sit; refusing to
    show them the questions on one would leave a row on the grid that answers
    422 when clicked.
    """
    from .policies import scope_filter
    from .scope_views import interview_track_scope_clause

    reach = scope_filter(db, session, AUTHORING_CAPABILITY)
    if reach.nothing:
        return ()
    rows = db.scalars(
        select(InterviewTrack)
        .where(interview_track_scope_clause(reach))
        .order_by(InterviewTrack.position, InterviewTrack.code, InterviewTrack.id)
    ).all()
    codes = tuple(dict.fromkeys(row.code for row in rows))
    return codes + tuple(k for k in SPECIALIZATIONS if k not in set(codes))
