"""Knowledge Base seed — the ONLY seed that is safe to run in production.

    python -m app.seed_kb

Split out of `app.seed` deliberately. That module creates demo accounts with
published passwords (director@bgscet.ac.in / director123 among them) and is now
refused outright when ENV=prod. But the KB is not demo data: it is the grounded
assistant's entire source of truth, and without it every "how do I verify a
skill?" falls back to ungrounded generation. Production needs this content and
must never need the accounts, so they no longer travel together.

Contains NO student facts and NO credentials — approved, student-audience
policy/FAQ/guidance only, the "explain the rules" layer. A student's own numbers
still come from the authenticated records view.

Idempotent: keyed on document title, so re-running adds nothing.
"""

from datetime import datetime, timezone

from sqlalchemy import select

from .db import SessionLocal
from .models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeStatus


# Each entry: (title, source_type, [(section_title, anchor, chunk_text), ...]).
# Realistic REEP policy/FAQ/guidance covering the assistant's target use cases.
KNOWLEDGE: list[tuple[str, str, list[tuple[str, str, str]]]] = [
    (
        "Verifying a skill (e.g. Power BI)",
        "faq",
        [
            (
                "How skill verification works",
                "verify-skill",
                "To get a skill like Power BI verified, upload a certificate or "
                "proof of completion as evidence and raise a skill claim with the "
                "level you are claiming. Your mentor reviews the proof and either "
                "verifies or rejects the claim. A verified skill is trusted across "
                "REEP: verified skills raise your job-match percentage because "
                "employers weight confirmed skills far more than self-reported ones.",
            ),
            (
                "Self-reported vs verified",
                "verify-skill-levels",
                "A self-reported skill counts for less than a verified one. Until a "
                "mentor confirms your claim the skill shows as unverified and "
                "contributes only partially to job matching. Attach the strongest "
                "evidence you have — a provider completion certificate is best.",
            ),
        ],
    ),
    (
        "Documents needed before placements",
        "placement_guide",
        [
            (
                "Placement-ready checklist",
                "placement-docs",
                "Before you appear for placements your profile should be at least "
                "70% complete. The core documents are: a clear profile photo, an "
                "up-to-date resume or CV, and certificate proofs for the skills and "
                "certifications you list. Missing any of these lowers your profile "
                "completeness and can block eligibility for some drives.",
            ),
        ],
    ),
    (
        "What placement clearance means",
        "policy",
        [
            (
                "Eligibility criteria",
                "clearance",
                "Placement clearance means you meet the eligibility criteria set for "
                "a drive. The usual gates are: a minimum CGPA, no (or a capped "
                "number of) live backlogs, an attendance floor, and completion of "
                "the required certifications. Each job can set its own thresholds — "
                "the higher of the drive's rule and the programme default applies.",
            ),
            (
                "Why you might not be cleared",
                "clearance-blocked",
                "Common reasons for being blocked: CGPA below the drive's minimum, "
                "one or more live backlogs when the drive allows none, attendance "
                "under the required percentage, or a mandatory certification still "
                "pending. Clearing the backlog, raising attendance, or completing "
                "the certification restores eligibility for the next drive.",
            ),
        ],
    ),
    (
        "How leaderboards are calculated",
        "policy",
        [
            (
                "Per-board metrics",
                "leaderboards",
                "Each leaderboard ranks students on a single metric so the ranking "
                "is transparent: completed certifications, verified skills, latest "
                "CGPA, active days (login/check-in streak), or mock-assessment "
                "scores. Boards are cohort-scoped — you are compared only with your "
                "own batch, never across programmes.",
            ),
            (
                "Opting out",
                "leaderboards-optout",
                "Leaderboards are opt-out. If you prefer not to appear you can hide "
                "yourself from the public ranking; your metrics are still tracked "
                "for your mentor and your own dashboard, just not shown to peers.",
            ),
        ],
    ),
    (
        "What to upload for a certification",
        "course_guide",
        [
            (
                "Accepted proof",
                "cert-upload",
                "For a certification, upload the completion certificate issued by "
                "the provider as a PDF, PNG or JPEG. The file is magic-byte "
                "validated on upload, so the real file type must match its "
                "extension — a renamed file is rejected. Once uploaded the proof is "
                "marked pending mentor review.",
            ),
            (
                "After you upload",
                "cert-review",
                "A mentor reviews the certificate proof and either verifies it or "
                "sends it back with a note. A verified certification counts toward "
                "your completed-certs metric, your profile completeness, and any "
                "drive that requires it.",
            ),
        ],
    ),
    (
        "Steps to apply for a job",
        "placement_guide",
        [
            (
                "Applying to a posting",
                "apply-steps",
                "To apply for a job: open the posting and check the eligibility "
                "gates and your match percentage. If you are eligible, use Apply to "
                "record your intent — this logs your application for the placement "
                "team. Then build a tailored CV for that role in the Resume Builder "
                "and keep your certificate proofs current.",
            ),
            (
                "Match percentage",
                "apply-match",
                "Your match percentage reflects how well your verified skills and "
                "profile line up with the job's required skills. Verifying more of "
                "your claimed skills is the fastest way to raise it.",
            ),
        ],
    ),
    (
        "Placement process overview",
        "placement_guide",
        [
            (
                "End-to-end flow",
                "placement-overview",
                "The placement journey runs: complete your profile and documents, "
                "get your skills verified, meet the eligibility criteria, apply to "
                "matching drives, sit the selection rounds, and record any offer. "
                "The placement cell publishes drives; your mentor helps you stay "
                "clearance-ready throughout.",
            ),
        ],
    ),
    (
        "Time-sheet and attendance rules",
        "policy",
        [
            (
                "Logging your time",
                "timesheet",
                "The daily time-sheet records how you spend your day across "
                "activities such as lectures, coursework, skilling, and rest. Log "
                "entries honestly — mentors use the time-sheet alongside lab "
                "check-ins to gauge effort, not just outcomes.",
            ),
            (
                "Attendance floor",
                "attendance",
                "Attendance is tracked per course from session records. Falling "
                "below the required percentage (commonly 75%) triggers an alert to "
                "you and your mentor and can affect placement clearance. Supervised "
                "lab check-ins and confirmed sessions count toward your active-days "
                "metric.",
            ),
        ],
    ),
    (
        "Using the Resume Builder",
        "course_guide",
        [
            (
                "Building a CV",
                "resume-builder",
                "The Resume Builder assembles a CV from your profile: education, "
                "verified skills, certifications, and a career summary. Tailor it "
                "per job by highlighting the skills that match the posting. Keep a "
                "verified certificate behind each skill so the CV holds up in "
                "review.",
            ),
            (
                "Keeping it current",
                "resume-current",
                "Update your resume whenever you complete a certification or get a "
                "new skill verified. A current resume plus proofs is what makes your "
                "profile placement-ready.",
            ),
        ],
    ),
    (
        "Mentor meetings and leave basics",
        "faq",
        [
            (
                "Working with your mentor",
                "mentor-basics",
                "Every student is assigned a mentor who reviews your uploads, "
                "verifies skill and certificate claims, and tracks your placement "
                "readiness. Mentor notes and one-on-one meetings are logged so your "
                "progress has a paper trail.",
            ),
            (
                "Applying for leave",
                "leave-basics",
                "To take leave, raise a leave request with the dates and reason; "
                "your mentor approves or declines it. Approved leave is accounted "
                "for when attendance is computed, so record it rather than simply "
                "missing sessions.",
            ),
        ],
    ),
]


def seed_knowledge(db) -> int:
    """Idempotently insert the APPROVED student-audience Knowledge Base.

    Returns the number of documents added. Takes an existing session so
    `app.seed` can call it inside its own transaction scope.
    """
    now = datetime.now(timezone.utc)
    added = 0
    for title, source_type, chunks in KNOWLEDGE:
        if db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.title == title)):
            continue
        doc = KnowledgeDocument(
            title=title,
            source_type=source_type,
            version="1",
            status=KnowledgeStatus.APPROVED,
            audience="student",
            published_at=now,
            owner_role="ADMIN",
        )
        doc.chunks = [
            KnowledgeChunk(
                chunk_text=chunk_text,
                section_title=section_title,
                anchor=anchor,
                metadata_json={"source_type": source_type},
            )
            for section_title, anchor, chunk_text in chunks
        ]
        db.add(doc)
        added += 1
    if added:
        db.commit()
        print(f"added knowledge documents ({added}, APPROVED, student audience)")

    # Backfill pgvector embeddings when a provider is configured (idempotent —
    # overwrites with fresh vectors). No-op without a provider: retrieval then
    # runs on Postgres full-text alone. Import here so seeding never hard-depends
    # on the embeddings module.
    from .ai.embeddings import embedder_configured, reembed_all

    if embedder_configured():
        n = reembed_all(db)
        if n:
            print(f"embedded knowledge chunks ({n})")

    return added


def main() -> None:
    db = SessionLocal()
    try:
        seed_knowledge(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
