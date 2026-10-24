"""Assistant retention & aged-data redaction (Assistant V2 Phase D, review #9;
interview records added by Interview Engine v3 §6.7 layer 3 and §6.8).

Memory does not live forever. Three jobs keep stored data on a lifecycle instead
of letting it accumulate indefinitely:

* ``purge_expired`` walks conversations AND interview records: one whose
  ``retention_until`` has passed is SOFT-deleted (``deleted_at`` stamped) —
  recoverable-shaped but hidden — and the free text it still carries through the
  grace window is PII-scrubbed with ``app.redaction.redact_pii``. Anything
  soft-deleted for longer than the grace window is HARD-deleted. The two subjects
  share one function on purpose: **one retention story in this codebase, not
  two.** A second module with its own clock and its own grace window is how a
  student's transcript ends up outliving the conversation it was quoted from.
  It also destroys the CV and photograph of an application the office REJECTED
  more than ``REJECTED_REGISTRATION_DOCUMENT_DAYS`` ago — the one subject on
  this path that is not a record with a soft-delete stage, because the decision
  row is kept forever and only its attachments age out. See step 0b.
* ``redact_expired_runs`` walks the ``AgentRun`` audit trail: a run older than the
  window keeps its METRICS (status, intent, resolved, duration_ms, model) but has
  its free text (question, answer, trace, citations) replaced with the redaction
  sentinel — so long-run analytics survive without holding a student's words.
* ``finalize_orphaned_interviews`` is the THIRD and last finalization layer for
  ``interview_sessions`` (§6.7). Layers 1 and 2 — the relay's own finalizer and
  the router's ``finally:`` backstop — both need the process to still be alive.
  This one is the answer to ``kill -9``: a ``running`` row whose heartbeat went
  stale becomes an honest ``abandoned`` instead of a row that reads as still live
  forever. A ``running`` row that is never closed is worse than no row, because
  it is a record that lies.

All three are IDEMPOTENT (a second pass is a no-op) and pure functions of ``now``
so a test can pin the clock.

**What is wired and what is not.** ``finalize_orphaned_interviews`` IS called
once at startup from ``app/main.py``'s lifespan — a worker that just restarted is
exactly the process that knows the previous one died, so a deploy or a
crash-restart sweeps its own orphans with no cron at all. ``purge_expired`` and
``redact_expired_runs`` are still NOT wired: they delete student data on a clock,
which is a deployment decision, and running them from every boot would make the
amount of data destroyed a function of how often someone restarts the API.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.orm import Session

from .account_links import sweep_login_codes
from . import document_store
from .config import settings
from .models.agent_run import AgentRun
from .models.conversation import Conversation, Message
from .models.interview import InterviewEvaluation, InterviewSession, InterviewTurn
from .interview_summary import ensure_summary
from .redaction import REDACTED, redact_pii

log = logging.getLogger("reep.retention")

# A soft-deleted conversation is kept this long (so a mistaken clear is
# recoverable) before it is destroyed for good. Interview records reuse the same
# window — the grace period answers "someone deleted this by mistake", which is
# the same question for both subjects, and two different numbers would only mean
# two different wrong answers to give a student who asks.
SOFT_DELETE_GRACE_DAYS = 30

# How long a REJECTED application's attached files — the CV and the photograph —
# are kept before this sweep destroys them.
#
# Rejection deliberately keeps them, and that is not an oversight to be tidied
# away: `POST /api/register/{id}/reopen` is the queue's Undo, and it is lossless
# only because the papers are still there when somebody presses it. What was
# missing is the other end. Nothing aged them out — not the decision, not
# `purge_expired`, not any console screen — so the CV of an applicant the office
# refused stayed on the volume for the life of the deployment, with nothing
# anywhere saying so. An undo nobody is going to press is not a reason to keep a
# person's documents forever.
#
# 180 days is the interview record's window, chosen for the same reason: it
# covers a placement season and the review that follows it, which is the period
# in which a rejection is still argued about. Past it, a reopen brings back the
# application and not its papers, and the applicant is asked to send them again —
# which the public attach route still accepts, because a reopened row is
# PENDING_REVIEW again. That is the honest reading of "lossless": a promise about
# the window in which an undo actually happens, not one that runs forever.
#
# A CONSTANT HERE AND NOT A SETTING, on purpose. Every knob in config.py is an
# operator's dial, and how long the college keeps the papers of somebody it
# refused a place is not an operator's decision — it is the college's records
# policy, and moving it belongs in a pull request somebody read, the way the
# badge catalogue and HIERARCHY_LEVELS do. There is no "keep forever" value for
# the same reason the interview window has none.
#
# THE `registrations` ROW ITSELF IS NEVER TOUCHED by any of this. It is the
# record that this person applied and was refused, the audit trail hangs off it,
# and `POST /api/register`'s duplicate guard is keyed on the address it carries;
# deleting it would quietly reopen the form to an address the office has already
# ruled on. This sweep destroys the files and leaves the decision standing.
REJECTED_REGISTRATION_DOCUMENT_DAYS = 180


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# The one place interview retention reaches outside Postgres
# ---------------------------------------------------------------------------
def _delete_interview_audio(rows: Sequence[InterviewSession]) -> tuple[int, list[str]]:
    """Sweep the audio store for **every** session in ``rows``, not only the ones
    whose row admits to having audio.

    Returns ``(sessions_whose_audio_was_destroyed, blocked_session_ids)``. A
    session id in the second list is one whose bytes are still on disk, and its
    database row **must not be hard-deleted this pass** — see the reasoning below.

    **DO NOT "OPTIMISE" THIS BACK INTO A FLAG TEST.** It used to select
    ``[r for r in rows if r.audio_recorded or r.audio_path]``, and that filter is
    exactly how a named student's voice outlives its retention window. Those two
    columns record what the RELAY BELIEVED and managed to write down; the whole
    failure mode is that the relay may have died before it could write anything
    down. A session whose Layer 1 finalizer never ran — an exception its
    ``except*`` clauses do not match — still has its files closed by ``run()``'s
    ``finally``, so the bytes are on disk while the row says ``audio_recorded =
    false, audio_path = NULL``. Filtering on the row asks the wrong witness. The
    filesystem is the authority on which files exist.

    The unconditional call costs nothing because ``delete_session_audio`` is a
    no-op for a session that never recorded — see its contract, which states that
    property precisely so this call site can rely on it: files are named after
    the ``interview_sessions.id``, so the session's primary key alone finds them,
    and a missing file (or a store directory that was never created, which is
    every deployment with recording disabled) is success rather than an error.
    That is what makes an unconditional sweep the fix here, rather than the
    reconciliation pass this module and the store both used to sketch: no store
    listing, no second delete path, no new state.

    THIS IS THE SINGLE INTEGRATION POINT for the interview-audio store. Every
    other line of this module speaks only to Postgres, where a delete is
    transactional and either happens or does not. Audio is a file on a disk: the
    delete is not in our transaction, it can half-succeed, and it is a named
    student's recorded voice. Keeping that in one function means the next editor
    changes it in one place, and means "does retention delete the audio?" has one
    answer rather than a grep.

    The import stays guarded even though the store now exists: this module's
    other three jobs — conversations, ``AgentRun`` redaction, the orphan sweeper
    — speak only to Postgres and must keep working if ``app/interview_audio.py``
    is ever moved, renamed or stripped from a slim image. An ``ImportError`` here
    is reported and made safe, never fatal to the whole sweep.

    The contract this expects of the store, stated here because this is its only
    caller::

        # app/interview_audio.py
        def delete_session_audio(interview_session_id: str, audio_path: str | None = None) -> int:
            '''Remove any stored audio for this interview; return files removed.

            IDEMPOTENT: an already-missing file is success, not an error — the
            reaper retries, and a second pass must not start raising.
            A NO-OP for a session that never recorded, store directory or not.
            RAISES only when bytes are believed to still be on disk.
            '''

    **A session whose audio could not be deleted keeps its database row.** That
    is the deliberate direction to fail, and it is the same reasoning §6.7 gives
    for the orphan sweeper: deleting the row would leave a recording of a named
    student on disk with *nothing left pointing at it* — undiscoverable, so
    undeletable by anyone who later has to answer for it. A row that outlives its
    retention window by one sweep is visible and arguable. An orphaned voice file
    is neither.
    """
    if not rows:
        return 0, []

    try:
        from .interview_audio import delete_session_audio
    except ImportError:
        # No store, so no way to delete anything and no way to look. THE ONE
        # PLACE the flags are still read, because with the store gone they are
        # the only evidence left about which sessions had audio — and blocking
        # every row instead would freeze interview retention entirely over a
        # broken import, leaving transcripts past their window to answer for
        # too. ERROR, not a warning: this is student voice data that retention
        # has been asked to destroy and cannot.
        claimed = [r.id for r in rows if r.audio_recorded or r.audio_path]
        if claimed:
            log.error(
                "%d interview session(s) past retention carry stored audio, but "
                "app/interview_audio.py is not importable — their audio cannot be "
                "deleted and their rows are being HELD BACK from hard-delete so the "
                "files stay discoverable. Session ids: %s",
                len(claimed),
                ", ".join(claimed),
            )
        else:
            log.error(
                "app/interview_audio.py is not importable, so %d expiring "
                "interview session(s) are being hard-deleted without their audio "
                "store being swept. No row claims a recording, but a row is not "
                "the authority on what is on disk.",
                len(rows),
            )
        return 0, claimed

    deleted = 0
    blocked: list[str] = []
    for row in rows:
        try:
            # The row's PRIMARY KEY first and its remembered path second: the id
            # is the file's name and is always present, the path is only a
            # cross-check for the day the naming rule changes.
            if delete_session_audio(row.id, row.audio_path):
                deleted += 1
        except Exception:
            # One bad file must not stop the rest of the sweep, and must not be
            # swallowed either.
            log.exception(
                "Failed to delete stored audio for interview session %s "
                "(path=%r); its row is held back from hard-delete.",
                row.id,
                row.audio_path,
            )
            blocked.append(row.id)
    return deleted, blocked


def purge_expired(db: Session, now: datetime | None = None) -> dict[str, int]:
    """Advance every conversation AND every interview record one step along its
    retention lifecycle.

    * retention_until in the past and not yet soft-deleted -> soft-delete now and
      PII-scrub the free text it keeps through the grace window.
    * soft-deleted more than ``SOFT_DELETE_GRACE_DAYS`` ago -> hard-delete it and
      everything hanging off it.

    The two subjects run on **different clocks and the same shape**: a
    conversation is kept 90 days (it is a chat thread), an interview record 180
    (``settings.interview_retention_days`` — it is an assessment artefact a
    mentor reviews across a semester). They are one function because the shape is
    the thing that must not diverge; the numbers were always allowed to differ.

    Returns a summary. The conversation keys — ``soft_deleted``,
    ``messages_redacted``, ``hard_deleted``, ``messages_deleted`` — are unchanged
    and still count conversations only; the interview keys are prefixed
    ``interview*`` so no caller can read one subject's number as the other's.
    Idempotent: a run with nothing due changes nothing.
    """
    now = now or _utcnow()
    grace_cutoff = now - timedelta(days=SOFT_DELETE_GRACE_DAYS)

    summary = {
        "soft_deleted": 0,
        "messages_redacted": 0,
        "hard_deleted": 0,
        "messages_deleted": 0,
        "interviews_soft_deleted": 0,
        "interview_turns_redacted": 0,
        "interview_reports_redacted": 0,
        "interviews_hard_deleted": 0,
        "interview_turns_deleted": 0,
        # Sessions whose stored audio was actually found and destroyed. The
        # sweep runs for every expiring session, so this is normally far smaller
        # than `interviews_hard_deleted` — and a number LARGER than the count of
        # rows that claimed audio is not a bug, it is the hole this closes.
        "interview_audio_deleted": 0,
        # Sessions that were due for hard-delete and were held back because their
        # stored audio could not be destroyed. Non-zero here means retention did
        # NOT complete and someone has to look — see _delete_interview_audio.
        "interviews_hard_delete_blocked": 0,
        # Sign-in codes (auth_tokens, purpose='login_code') dead past their
        # grace: consumed, or expired unused. Issue deletes a user's earlier
        # codes, so this is the last code of anyone who stopped signing in.
        "login_codes_deleted": 0,
        # Stored files destroyed off REJECTED applications more than
        # REJECTED_REGISTRATION_DOCUMENT_DAYS past their decision: a CV, a
        # photograph, or both. The rejected application itself survives — it is
        # the record of a decision — so this number counts FILES, never rows in
        # `registrations`, and the key keeps a name it was given when it counted
        # something else (see step 0b). Zero on a healthy deployment most nights,
        # which is why the key has to stay in the summary whatever it counts: a
        # key that vanishes from a nightly job's output reads as a job that
        # stopped running.
        "registrations_purged": 0,
    }

    # --- 0) Dead sign-in codes. Not a subject with a lifecycle — a six-digit
    # code is spent or expired within minutes and the row then says nothing
    # /login/code will ever read — so a plain delete, on the same run.
    summary["login_codes_deleted"] = sweep_login_codes(db, now=now)

    # --- 0b) The papers of applications the office refused, well past the
    # decision. See REJECTED_REGISTRATION_DOCUMENT_DAYS for why they are kept
    # that long and why the `registrations` row itself is left standing.
    #
    # THE KEY IS OLDER THAN THE SWEEP IT NOW REPORTS. Until 2026-09-10 this step
    # swept PENDING_VERIFICATION applications whole — ones that never clicked a
    # confirmation link — a week past the link's life. Submission stopped writing
    # that status when the mailbox proof moved past approval, migration
    # 9b2d47f0ce15 moved the rows already stuck in it into the review queue, and
    # the step could then only ever delete nothing; `registrations_purged` was
    # left in the summary at 0 rather than removed, because a key that vanishes
    # from a nightly job's output reads as a job that stopped running. It now
    # counts something real again, and it counts FILES rather than applications.
    #
    # THE WORK IS DONE BY THE MODULE THAT OWNS THOSE FILES, not repeated here —
    # `_delete_interview_audio`'s "single integration point" argument, arriving
    # from the other side. `routers/registration.py` already destroys these bytes
    # in two other places (an upload replacing one, and approval moving them into
    # the student's own uploads), and a third delete path written in this module
    # would be a second implementation of the ordering that matters most here:
    # the manifest row is RELEASED, carrying the file's full facts, BEFORE the
    # bytes are unlinked, because doing it the other way round leaves a file in
    # the permanent archive that nothing in the database can ever name. Those
    # facts — kind, original name, mime type, size, recorded_at — hang off a row
    # this module deliberately does not import. The archived copy in S3 is not
    # touched at all, by that archive's own rule.
    #
    # THE HELPER IS ITS OWN MODULE, AND THAT IS THE WHOLE POINT OF ITS ADDRESS.
    # It was first written inside `app/routers/registration.py`, which is the
    # module that PROVISIONS ACCOUNTS on approval and therefore imports all
    # three of the identity models by name. Reaching it there would have pulled
    # every one of them into this nightly process — past the letter of §35,
    # whose AST check reads only this file's own import statements, and straight
    # through the middle of what that guard exists to prevent.
    # `app/registration_documents.py` names `Registration` and
    # `RegistrationDocument` and nothing that is an account, so the import can
    # be read honestly by the guard and by a human.
    #
    # It is still FUNCTION-LOCAL, for `_delete_interview_audio`'s reason: an
    # ImportError costs this one step and is reported loudly, rather than taking
    # conversations, agent runs and interview records down with it at boot.
    #
    # A failure INSIDE the helper is deliberately not caught. At the point of
    # this call everything the pass has done is still uncommitted — step 0's
    # deleted sign-in codes included — so carrying on past a database error
    # would need a rollback that silently discards them while the summary goes
    # on reporting them, and a session left needing a rollback fails every query
    # after it anyway. Letting it propagate loses this pass and nothing else,
    # and the next night runs it again. The per-file catches at step 3b are not
    # a precedent for doing otherwise here: they exist because a filesystem
    # delete is not in the transaction, which is a different problem.
    try:
        from .registration_documents import purge_rejected_documents
    except ImportError:
        log.exception(
            "app/registration_documents.py is not importable, so the documents of "
            "applications rejected more than %d days ago were NOT swept this "
            "pass. Their files are still on the volume; the rest of the sweep "
            "continues.",
            REJECTED_REGISTRATION_DOCUMENT_DAYS,
        )
    else:
        summary["registrations_purged"] = purge_rejected_documents(
            db,
            older_than_days=REJECTED_REGISTRATION_DOCUMENT_DAYS,
            now=now,
        )

    # --- 1) Soft-delete conversations whose retention window has closed. -------
    expired = db.scalars(
        select(Conversation).where(
            Conversation.retention_until.is_not(None),
            Conversation.retention_until < now,
            Conversation.deleted_at.is_(None),
        )
    ).all()
    for conv in expired:
        conv.deleted_at = now
        summary["soft_deleted"] += 1
        # Free text retained through the grace window is scrubbed of obvious PII.
        for msg in db.scalars(
            select(Message).where(Message.conversation_id == conv.id)
        ).all():
            scrubbed = redact_pii(msg.content)
            if scrubbed != msg.content:
                msg.content = scrubbed
                summary["messages_redacted"] += 1

    # --- 2) Hard-delete conversations soft-deleted past the grace window. ------
    doomed_ids = db.scalars(
        select(Conversation.id).where(
            Conversation.deleted_at.is_not(None),
            Conversation.deleted_at < grace_cutoff,
        )
    ).all()
    if doomed_ids:
        summary["messages_deleted"] = (
            db.query(Message)
            .filter(Message.conversation_id.in_(doomed_ids))
            .count()
        )
        db.execute(
            delete(Message).where(Message.conversation_id.in_(doomed_ids))
        )
        db.execute(
            delete(Conversation).where(Conversation.id.in_(doomed_ids))
        )
        summary["hard_deleted"] = len(doomed_ids)

    # --- 3) The same two stages for interview records. -------------------------
    #
    # Deliberately AFTER the conversation stages, and safe in either order: the
    # bulk `delete(Message)` above cannot touch an interview_turns row, because
    # `interview_turns.message_id` carries ON DELETE SET NULL **in the DDL** (a
    # bulk DELETE bypasses ORM cascades entirely), and `interview_sessions.
    # conversation_id` is SET NULL for the same reason. A student clearing their
    # chat, or the 90-day conversation clock closing first, leaves the interview
    # record standing with two NULLs and every turn intact. That is the whole
    # point of keying this record on interview_session_id and never on
    # conversation_id.
    retention_window = timedelta(days=settings.interview_retention_days)

    # --- 3a) Soft-delete + redact interviews past their window. ----------------
    #
    # `retention_until` is STORED at open (started_at + the window) so that
    # changing the setting cannot retroactively re-date an interview a student
    # was already promised 180 days for. The NULL branch is the fallback for a
    # row written before that column was populated, or by a writer that failed to
    # set it: without it such a row is kept FOREVER, which is the one outcome
    # nobody would choose on purpose. An OR of two explicit predicates rather
    # than a COALESCE so the stored branch can still use
    # ix_interview_session_retention.
    expired_sessions = db.scalars(
        select(InterviewSession).where(
            InterviewSession.deleted_at.is_(None),
            or_(
                and_(
                    InterviewSession.retention_until.is_not(None),
                    InterviewSession.retention_until < now,
                ),
                and_(
                    InterviewSession.retention_until.is_(None),
                    InterviewSession.started_at < now - retention_window,
                ),
            ),
        )
    ).all()
    for sess in expired_sessions:
        sess.deleted_at = now
        summary["interviews_soft_deleted"] += 1
        # The student's own words, and the model's raw words about them, are the
        # two free-text fields that survive into the grace window. §6.8 names
        # exactly these two: `interview_turns.content` and
        # `interview_evaluations.raw_response`. `summary`/`drill` are the
        # student-facing coaching prose that the report screen renders, and they
        # are left readable so a record in its final 30 days still means
        # something to whoever opens it.
        for turn in db.scalars(
            select(InterviewTurn).where(
                InterviewTurn.interview_session_id == sess.id
            )
        ).all():
            scrubbed = redact_pii(turn.content)
            if scrubbed != turn.content:
                turn.content = scrubbed
                summary["interview_turns_redacted"] += 1
        for report in db.scalars(
            select(InterviewEvaluation).where(
                InterviewEvaluation.interview_session_id == sess.id
            )
        ).all():
            scrubbed = redact_pii(report.raw_response)
            if scrubbed != report.raw_response:
                report.raw_response = scrubbed
                summary["interview_reports_redacted"] += 1

    # --- 3b) Hard-delete interviews soft-deleted past the grace window. --------
    #
    # Whole rows, not ids: the audio sweep wants the row's remembered
    # `audio_path` as a cross-check, and asking for it after the delete is
    # asking too late. It does NOT need it to decide whether to look — see
    # _delete_interview_audio.
    doomed_sessions = db.scalars(
        select(InterviewSession).where(
            InterviewSession.deleted_at.is_not(None),
            InterviewSession.deleted_at < grace_cutoff,
        )
    ).all()
    if doomed_sessions:
        # Bytes on disk FIRST, rows second. The other order destroys the only
        # pointer to a student's recording before destroying the recording.
        audio_deleted, blocked_ids = _delete_interview_audio(doomed_sessions)
        summary["interview_audio_deleted"] = audio_deleted
        summary["interviews_hard_delete_blocked"] = len(blocked_ids)

        blocked = set(blocked_ids)
        doomed_session_ids = [s.id for s in doomed_sessions if s.id not in blocked]
        if doomed_session_ids:
            summary["interview_turns_deleted"] = (
                db.query(InterviewTurn)
                .filter(
                    InterviewTurn.interview_session_id.in_(doomed_session_ids)
                )
                .count()
            )
            # One statement, and the database does the rest: `interview_turns`
            # and `interview_evaluations` both carry ON DELETE CASCADE in the
            # DDL. Deleting the children by hand first would work too, and would
            # be the second implementation of a rule the schema already states —
            # the shape that goes stale the day a fifth child table is added.
            db.execute(
                delete(InterviewSession).where(
                    InterviewSession.id.in_(doomed_session_ids)
                )
            )
            summary["interviews_hard_deleted"] = len(doomed_session_ids)

    db.commit()
    return summary


def finalize_orphaned_interviews(
    db: Session,
    now: datetime | None = None,
    grace_seconds: int | None = None,
) -> int:
    """Close ``interview_sessions`` rows whose process died mid-call, and return
    how many were closed. **Layer 3 of three** (§6.7).

    Layers 1 and 2 — the relay's own finalizer and the router's ``finally:``
    backstop — both require this process to still be alive to run. Neither
    survives ``kill -9``, an OOM kill, or a container that was cut off
    mid-interview, and every one of those leaves a row that says ``running``
    forever: a record that lies, on a student's history screen, next to real
    interviews. This is the layer that needs nothing of the dead process except
    the last heartbeat it managed to write.

    The predicate is a **stale heartbeat**, never an elapsed ``started_at``. By
    ``started_at`` alone a long healthy interview and a dead one are
    indistinguishable, and the sweeper would end live calls.
    ``settings.interview_orphan_grace_seconds`` (1200 = ``interview_max_seconds``
    900 + 300 s of slack) sits comfortably past the longest legal interview, so a
    healthy session is never a candidate.

    ``ended_at`` is set to ``heartbeat_at``, **not** ``now``. The interview ended
    when the heartbeat stopped; stamping ``now`` would inflate every orphaned
    session's duration by however long the sweeper took to be run — which, since
    the usual trigger is the next deploy, could be days.

    Idempotent by construction: the ``status = 'running'`` predicate is the same
    guard layers 1 and 2 use, so whichever finalizer gets there first wins and
    the others update zero rows. It never overwrites a terminal status, so a
    session that ended honestly is never relabelled ``abandoned``.

    **The failure direction, stated so nobody has to rediscover it.** If the
    relay's heartbeat write fails all session long, ``heartbeat_at`` stays at
    ``started_at`` and this function will mark a perfectly healthy, still-running
    interview ``abandoned``. That is wrong, and it is the correct way to be
    wrong: a session wrongly marked abandoned is visible on a screen and someone
    argues about it, while a session stuck at ``running`` forever is invisible
    and nobody ever does. Do not "fix" this by loosening the predicate.
    """
    now = now or _utcnow()
    grace = (
        settings.interview_orphan_grace_seconds
        if grace_seconds is None
        else grace_seconds
    )
    cutoff = now - timedelta(seconds=grace)

    result = db.execute(
        update(InterviewSession)
        .where(
            InterviewSession.status == "running",
            InterviewSession.heartbeat_at < cutoff,
        )
        .values(
            status="abandoned",
            terminal_reason="orphaned (no heartbeat)",
            # A column reference, not a Python value — see the docstring.
            ended_at=InterviewSession.heartbeat_at,
        )
        # RETURNING, added with B6.2. The ids are needed for one reason: this is
        # the layer that closes the interviews NEITHER of the other two could,
        # so it is the layer whose sessions would otherwise never get a score
        # summary — and those summaries are the only part of the record that
        # survives the 180-day purge. Reading them back costs nothing here
        # (Postgres has the rows in hand) and the alternative is a trend that
        # silently loses every interview whose process was killed.
        .returning(InterviewSession.id)
    )
    swept_ids = [row[0] for row in result.all()]
    swept = len(swept_ids)
    if swept:
        db.commit()
        for session_id in swept_ids:
            # Each in its own transaction, and each swallowing its own failure:
            # the sweep above is already committed, and a bookkeeping copy must
            # never be able to undo a record that has stopped lying about being
            # `running`.
            ensure_summary(db, session_id)
        log.warning(
            "Finalized %d orphaned interview session(s): status='running' with "
            "no heartbeat for over %d s. Each one is a relay process that died "
            "mid-call; their transcripts up to that point are intact.",
            swept,
            grace,
        )
    return swept


def redact_expired_runs(
    db: Session, older_than_days: int = 90, now: datetime | None = None
) -> int:
    """Redact the free text of ``AgentRun`` rows older than the window while
    keeping every metrics field, and return how many rows were redacted.

    Kept: status, intent, resolved, duration_ms, steps, model, timestamps.
    Cleared: question -> sentinel, answer -> sentinel, trace -> [], citations ->
    []. Idempotent — a row already carrying the sentinel question is skipped, so a
    second pass returns 0.
    """
    now = now or _utcnow()
    cutoff = now - timedelta(days=older_than_days)

    stale = db.scalars(
        select(AgentRun).where(
            AgentRun.created_at < cutoff,
            AgentRun.question != REDACTED,  # idempotent: skip already-redacted
        )
    ).all()

    for run in stale:
        run.question = REDACTED
        run.answer = REDACTED
        run.trace = []
        run.citations = []

    if stale:
        db.commit()
    return len(stale)


