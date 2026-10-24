"""The question bank behind the free-style interviewer, and the bulk parser.

Two jobs, both small on purpose:

  question_bank_for(track)   -> the enabled questions of one track, in order,
                                rendered as the "[phase] text" lines that
                                interview_matrix.build_instructions expects
                                (the same shape the voice platform renders)
  with_question_bank(spec)   -> a Specialization carrying that bank, for the
                                interview router to hand the engine

Resolved LIVE on every socket open, never cached: a question an admin adds this
morning is asked this afternoon, and a per-process cache is how a deleted
question keeps being asked on three of five workers until the next deploy. It is
one indexed SELECT; the router runs it off the event loop.

The bulk parser is here rather than in the router so the same rules apply to a
pasted list and to a file, and so they can be tested without a request.
"""

from __future__ import annotations

import dataclasses
import re

from sqlalchemy import select

from .db import SessionLocal
from .interview_matrix import SPECIALIZATIONS, InterviewPhase, Specialization
from .models.interview_bank import InterviewBankQuestion

#: The phases a question can be pinned to. ENDED is terminal and asks nothing.
BANK_PHASES: tuple[str, ...] = tuple(
    p.value for p in InterviewPhase if p is not InterviewPhase.ENDED
)
TRACK_KEYS: tuple[str, ...] = tuple(SPECIALIZATIONS.keys())

#: Where a line goes when the author did not say. PROBING is the interview's
#: long middle - the framework-by-framework follow-ups - and the only phase in
#: which "one more question" is always welcome.
DEFAULT_PHASE = InterviewPhase.PROBING.value

MAX_QUESTION_CHARS = 600


def render(phase: str, text: str) -> str:
    return f"[{phase}] {text}"


def question_bank_for(track: str) -> tuple[str, ...]:
    """This track's enabled questions, in position order, as prompt lines."""
    with SessionLocal() as db:
        rows = db.scalars(
            select(InterviewBankQuestion)
            .where(InterviewBankQuestion.track == track, InterviewBankQuestion.enabled.is_(True))
            .order_by(InterviewBankQuestion.position, InterviewBankQuestion.created_at)
        ).all()
    return tuple(render(q.phase, q.text) for q in rows)


def with_question_bank(spec: Specialization) -> Specialization:
    """`spec` carrying the admin's bank for its track; `spec` itself when there
    is none. Appended after anything the code already put there, so a track
    that ships with questions keeps them first.

    NOT THE LIVE HANDSHAKE PATH ANY MORE (B5.1/B5.2). `/api/interview` goes
    through `app.interview_tracks.resolve_specialization`, which reads the TRACK
    ROW and its bank in one session on one worker thread and narrows the bank by
    `track_id` and by college. This stays because it is the code-only half of
    that answer — it takes a `Specialization` the caller already has and asks
    only "what does the office want covered on this code" — and because
    `tests/test_interview_bank.py` pins the wire through it. If you are adding a
    third reader of the bank, add it to `interview_tracks` instead: two readers
    with different filters is how a question stops being asked on one path and
    keeps being asked on the other, with nothing on any screen to say so.
    """
    bank = question_bank_for(spec.key)
    if not bank:
        return spec
    return dataclasses.replace(spec, question_bank=tuple(spec.question_bank) + bank)


# ------------------------------------------------------------------ bulk --

_BRACKET = re.compile(r"^\[\s*([a-z_ ]+?)\s*\]\s*(.+)$", re.I)
_SEP = re.compile(r"^\s*([a-z_ ]+?)\s*[|,;:]\s*(.+)$", re.I)
_ALIASES = {
    "open": "opening", "opening": "opening", "intro": "opening",
    "probe": "probing", "probing": "probing",
    "deep": "deep_dive", "deep dive": "deep_dive", "deep_dive": "deep_dive", "deepdive": "deep_dive",
    "wrap": "wrap_up", "wrap up": "wrap_up", "wrap_up": "wrap_up", "wrapup": "wrap_up", "close": "wrap_up",
}


def normalise_phase(raw: str | None) -> str | None:
    """A phase the author typed, in any of the spellings people use, or None."""
    if raw is None:
        return None
    key = raw.strip().lower().replace("-", " ")
    return _ALIASES.get(key) or _ALIASES.get(key.replace(" ", "_"))


def parse_bulk(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Lines -> [(phase, question)], plus a reason per line that was skipped.

    Accepted, one question per line:
        [probing] Tell me about a conflict you resolved.
        probing | Tell me about a conflict you resolved.
        probing, Tell me about a conflict you resolved.        (a CSV row)
        Tell me about a conflict you resolved.                  (-> probing)
    Blank lines and lines starting with # are ignored. A line whose phase is not
    one of the four is SKIPPED with a reason, never silently filed under
    probing: a mis-typed phase is the author's intent lost, and the response
    says so line by line so they can fix the three that failed rather than
    re-check the fifty that did not.
    """
    rows: list[tuple[str, str]] = []
    skipped: list[str] = []
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip().strip("﻿")
        if not line or line.startswith("#"):
            continue
        phase: str = DEFAULT_PHASE
        body = line
        bracket = _BRACKET.match(line)
        if bracket:
            # Brackets are EXPLICIT intent: an unknown phase here is the author's
            # meaning lost, so it fails loudly with the line number.
            named = normalise_phase(bracket.group(1))
            if named is None:
                skipped.append(
                    f"line {n}: unknown phase {bracket.group(1).strip()!r} "
                    "(use opening, probing, deep_dive or wrap_up)"
                )
                continue
            phase, body = named, bracket.group(2).strip()
        else:
            # "probing | text" / "probing, text" only counts as a phase prefix
            # when the word before the separator IS a phase. Otherwise the
            # comma is punctuation - "Why HR, and why now?" is one question.
            sep = _SEP.match(line)
            named = normalise_phase(sep.group(1)) if sep else None
            if named is not None:
                phase, body = named, sep.group(2).strip()
        body = body.strip().strip('"').strip()
        if len(body) < 8:
            skipped.append(f"line {n}: too short to be a question")
            continue
        if len(body) > MAX_QUESTION_CHARS:
            skipped.append(f"line {n}: longer than {MAX_QUESTION_CHARS} characters")
            continue
        rows.append((phase, body))
    return rows, skipped
