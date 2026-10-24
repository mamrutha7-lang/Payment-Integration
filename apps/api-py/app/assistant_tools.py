# NOTE (2026-08): the student-facing assistant is now the realtime mock
# interviewer in app/routers/interview.py, and it calls NO tool in this
# module — by design. The Realtime session is a REMOTE provider, so no
# student record (marks, attendance, CGPA, USN, resume text) may enter its
# prompt, which is exactly what these tools return. They stay in place for
# the orchestrator behind POST /api/agent/ask (retained for rollback). Any
# future path that does feed a tool result to a model must go through
# complete_chat(..., carries_student_data=True) in app/ai/llm.py.
# See docs/interview-assistant.md.

"""Read-only STUDENT TOOLS for the grounded assistant.

These are the assistant orchestrator's *source of truth* for LIVE student facts.
Instead of guessing a student's numbers, the orchestrator calls a tool here and
gets exactly what the student sees on their own screens — because each tool runs
the SAME code path as the corresponding `/student/...` endpoint.

Design contract
---------------
* Every tool takes a live SQLAlchemy ``Session`` plus the caller's
  ``student_id`` and returns a plain ``dict`` / ``list`` (JSON-ready), never a
  Pydantic model or an ORM row.
* Business logic is NOT reproduced here. Each tool invokes the real endpoint
  function from ``app.routers.student`` (with a minimal synthetic session) or a
  shared helper from that module, then projects the result down to the shape the
  orchestrator wants. If a screen's number changes, these tools change with it.
* These tools are read-only and student-scoped: they only ever return the
  caller's own facts, and they never send anything to a model. Grounding the
  assistant with them keeps live student data on this machine (AGENTS.md).

The one exception is :func:`profile_completion`, for which there is no single
endpoint; it is composed from the placement-profile fields and the resume-profile
completeness helper (``_resume_pct`` / ``_resume_completeness``) that the
readiness and next-actions endpoints already use.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import knowledge
from .models.student_profile import StudentProfile
from .models.user import Role
from .routers import student as student_ep


def _session(student_id: str) -> dict:
    """The minimal session payload the student endpoints read.

    Every endpoint reused below narrows to the caller via ``_require_student``,
    which reads ``studentId`` AND the role: B4.4's graduation keeps a graduate's
    ``students`` row and flips ``users.role`` to ALUMNI, so the claim alone stopped
    being proof that the caller is a current student. The role is stated here
    rather than threaded through from the real session because the caller has
    already resolved ``student_id`` from a verified one — this payload is a
    projection of that session, not a second source of truth for it."""
    return {"studentId": student_id, "role": Role.STUDENT.value}


# --- Tools -------------------------------------------------------------------


def completion_gaps(db: Session, student_id: str) -> list[dict]:
    """The student's 'what to do next' list — identical to GET
    /student/next-actions (top-5 candidate actions, most urgent first)."""
    out = student_ep.next_actions(session=_session(student_id), db=db)
    return [a.model_dump(mode="json") for a in out.actions]


def skill_status(db: Session, student_id: str) -> dict:
    """Held skills, pending/decided skill claims, and a verified-vs-total tally.

    ``held`` mirrors GET /student/skills; ``claims`` mirrors
    GET /student/skill-claims."""
    held_models = student_ep.my_skills(session=_session(student_id), db=db)
    claim_models = student_ep.my_skill_claims(session=_session(student_id), db=db)

    held = [
        {
            "name": s.name,
            "category": s.category,
            "level": s.level,
            "verified": s.verified,
        }
        for s in held_models
    ]
    claims = [
        {"skill_name": c.skill_name, "status": c.status} for c in claim_models
    ]
    return {
        "held": held,
        "claims": claims,
        "verified_count": sum(1 for s in held if s["verified"]),
        "total": len(held),
    }


def eligible_jobs(db: Session, student_id: str) -> list[dict]:
    """The opportunities feed with the per-posting eligibility verdict — the same
    gates GET /student/jobs applies (per-posting CGPA / live-backlog / gap)."""
    rows = student_ep.my_jobs(session=_session(student_id), db=db)
    return [
        {
            "title": j.title,
            "company": j.company,
            "eligible": j.eligible,
            "reasons": j.reasons,
            "match_percent": j.match_percent,
            "applied": j.applied,
        }
        for j in rows
    ]


def deadlines(db: Session, student_id: str) -> dict:
    """The next task per in-flight course — projected from GET /student/courses.

    Until 2026-09-17 this also carried the certification due-dates from
    GET /student/certifications. The Certification Tracker and that endpoint
    were removed with the Elevate card's "Specialization Cert" row, and the
    assistant must not go on reading out a screen the student can no longer
    open — so the deadlines answer is the courses' next tasks now.
    """
    course_models = student_ep.my_courses(session=_session(student_id), db=db)
    return {
        "courses": [
            {"name": c.name, "next_task": c.next_task} for c in course_models
        ],
    }


# The placement-profile essentials a recruiter/CV needs. Kept in step with the
# fields the profile screen edits and the readiness "Placement profile" factor
# weighs (phone + linkedin). (field_name, human label)
_PROFILE_FIELDS: list[tuple[str, str]] = [
    ("phone", "phone number"),
    ("email", "email address"),
    ("linkedin_url", "LinkedIn URL"),
    ("github_url", "GitHub URL"),
    ("portfolio_url", "portfolio URL"),
    ("city", "city"),
    ("career_summary", "career summary"),
]


def profile_completion(db: Session, student_id: str) -> dict:
    """How complete the student's placement profile is, and what is missing.

    Percent is the average of two grounded signals: the share of the
    placement-profile essentials that are filled, and the resume-profile
    completeness (``_resume_pct`` -> ``_resume_completeness``, the same value the
    readiness/next-actions endpoints read). ``missing`` lists the empty essentials
    plus a note when the resume profile is under the 70% target."""
    prof = db.scalar(
        select(StudentProfile).where(StudentProfile.student_id == student_id)
    )

    missing: list[str] = []
    filled = 0
    for field, label in _PROFILE_FIELDS:
        value = getattr(prof, field, None) if prof else None
        if value:
            filled += 1
        else:
            missing.append(label)

    profile_pct = round(100 * filled / len(_PROFILE_FIELDS)) if _PROFILE_FIELDS else 0
    resume_pct = student_ep._resume_pct(db, student_id)
    if resume_pct < 70:
        missing.append(f"resume profile is {resume_pct}% complete (target 70%)")

    percent = round((profile_pct + resume_pct) / 2)
    return {"percent": percent, "missing": missing}


def placement_readiness(db: Session, student_id: str) -> dict:
    """The weighted placement-readiness summary — identical to GET
    /student/placement-readiness (score, band, summary, factors)."""
    out = student_ep.placement_readiness(session=_session(student_id), db=db)
    return out.model_dump(mode="json")


def policy_search(db: Session, query: str, audience: str = "student") -> list[dict]:
    """Retrieve APPROVED policy/FAQ/guidance chunks that ground an answer — the
    'explain the rules' layer. Never returns a live student fact."""
    return knowledge.search(db, query, audience=audience)
