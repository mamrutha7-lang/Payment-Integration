"""The REEP Skills & Badge framework (developer framework doc, 2026-08).

THE BADGE CATALOGUE IS CODE; ONLY STUDENT STATE IS ROWS — the same rule as
`models/milestone.py`, for the same reason: the 48 badges below are the
programme's structure, identical for every student, and a rename must be a code
review, not a migration racing 5 000 rows. The framework document's §18 asks
for admin add/edit of badges; here that IS a code change, deliberately — what
administrators maintain in the database is everything that genuinely varies per
term: the Approved Certification Catalogue (§12), evidence verdicts, manual
awards/revocations, and assessment scores.

Four concepts, kept separate because the document keeps them separate (§10):

  skill/badge   BADGES below — code, name, category, stage, points
  evidence      badge_evidence — a claim with a type (§11) and a review status
                (§12); a student may hold SEVERAL evidence rows per badge, of
                different types ("Negotiation: External ✓, BGSCET ✓, Applied ✓")
  badge earned  student_badges — one row per (student, badge) once they move
                off "not started": IN_PROGRESS (self-marked) or EARNED
                (awarded on approved evidence, an assessment, or manually)
  growth        capability_assessments — the seven §9 capabilities on a 1–10
                scale at checkpoints T0–T4, staff-entered

Display status (§13) is DERIVED, never stored: no rows = Not Started; an
IN_PROGRESS row = In Progress; any pending evidence = Verification Pending; an
EARNED row = Earned, and Earned with fewer than the three evidence types
approved = "advanced evidence available". Deriving it means the word on the
screen cannot contradict the rows it summarises.

Certificate ≠ badge (§10): approving evidence is what mints the EARNED row, and
uploads on their own never do.
"""

import enum
import uuid
from datetime import date, datetime
from typing import Final, NamedTuple

from sqlalchemy import (
    CheckConstraint,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .user import Stage


def _uuid() -> str:
    return uuid.uuid4().hex


# --- vocabulary --------------------------------------------------------------


class BadgeCategory(str, enum.Enum):
    """§3 — the five skill categories. Catalogue-only (never a DB column)."""

    MANAGERIAL = "MANAGERIAL"
    SECTORAL = "SECTORAL"
    PLATFORM = "PLATFORM"
    THINKING = "THINKING"
    READINESS = "READINESS"


class SectoralTrack(str, enum.Enum):
    """§5 — the four specialisation tracks sectoral badges belong to."""

    FINANCE = "FINANCE"
    HR = "HR"
    MARKETING = "MARKETING"
    BUSINESS_ANALYTICS = "BUSINESS_ANALYTICS"


class EvidenceType(str, enum.Enum):
    """§11. Three DIFFERENT ways a skill is demonstrated — never collapsed."""

    EXTERNAL_VERIFIED = "EXTERNAL_VERIFIED"
    BGSCET_ASSESSED = "BGSCET_ASSESSED"
    APPLIED = "APPLIED"


class EvidenceStatus(str, enum.Enum):
    """§12 — the certification approval workflow, all four states."""

    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MORE_INFO_REQUIRED = "MORE_INFO_REQUIRED"


class StudentBadgeStatus(str, enum.Enum):
    """The two STORED statuses. Everything else in §13 is derived (see module
    docstring) — storing "Verification Pending" would let it outlive the
    evidence row that resolves it, the English-baseline lesson again."""

    IN_PROGRESS = "IN_PROGRESS"
    EARNED = "EARNED"


class CapabilityKind(str, enum.Enum):
    """§9 — the seven longitudinally tracked capabilities."""

    READING = "READING"
    WRITING = "WRITING"
    LISTENING = "LISTENING"
    SPEAKING = "SPEAKING"
    QUANTITATIVE = "QUANTITATIVE"
    DATA_INTERPRETATION = "DATA_INTERPRETATION"
    LOGICAL_REASONING = "LOGICAL_REASONING"


class AssessmentCheckpoint(str, enum.Enum):
    """§9 — T0 (REBOOT entry) through T4 (graduation)."""

    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    T4 = "T4"


CAPABILITY_LABEL: Final[dict[CapabilityKind, str]] = {
    CapabilityKind.READING: "Reading Comprehension",
    CapabilityKind.WRITING: "Writing",
    CapabilityKind.LISTENING: "Listening Comprehension",
    CapabilityKind.SPEAKING: "Speaking Fluency & Accuracy",
    CapabilityKind.QUANTITATIVE: "Quantitative Ability",
    CapabilityKind.DATA_INTERPRETATION: "Data Interpretation",
    CapabilityKind.LOGICAL_REASONING: "Logical / Analytical Reasoning",
}


# --- the catalogue (§4–§8) ---------------------------------------------------


class BadgeDefinition(NamedTuple):
    code: str
    name: str
    category: BadgeCategory
    stage: Stage  # REEP stage (§2). NOT Microsoft Excel — see §6's terminology note.
    points: int
    description: str
    requirement: str
    track: SectoralTrack | None = None
    # §8: readiness badges are awarded on BGSCET assessment thresholds, never
    # on a certificate upload — the claim form is closed for these.
    staff_awarded: bool = False


def _b(code, name, category, stage, points, description, requirement, **kw) -> BadgeDefinition:
    return BadgeDefinition(code, name, category, stage, points, description, requirement, **kw)


_CERT_REQ = "Approved evidence — an external certification, a BGSCET workshop/assessment, or applied work — verified by staff."
_APPLIED_REQ = "Applied evidence (project, internship, case competition, simulation or live industry problem) verified by staff — a certificate alone is not sufficient."
_ASSESS_REQ = "Awarded by BGSCET MBA when your assessment scores cross the programme threshold — no certificate upload."

BADGES: Final[tuple[BadgeDefinition, ...]] = (
    # §4 Managerial (12)
    _b("MGR-BUSINESS-COMMUNICATION", "Business Communication", BadgeCategory.MANAGERIAL, Stage.EXCEL, 10,
       "Clear, professional written and spoken business communication.", _CERT_REQ),
    _b("MGR-PRESENTATION-SKILLS", "Presentation Skills", BadgeCategory.MANAGERIAL, Stage.EXCEL, 10,
       "Structuring and delivering persuasive presentations.", _CERT_REQ),
    _b("MGR-EMOTIONAL-INTELLIGENCE", "Emotional Intelligence", BadgeCategory.MANAGERIAL, Stage.EXCEL, 10,
       "Self-awareness, empathy and relationship management at work.", _CERT_REQ),
    _b("MGR-TEAMWORK", "Teamwork", BadgeCategory.MANAGERIAL, Stage.EXCEL, 10,
       "Contributing effectively inside a team.", _CERT_REQ),
    _b("MGR-TEAM-MANAGEMENT", "Team Management", BadgeCategory.MANAGERIAL, Stage.ELEVATE, 15,
       "Organising, delegating and running a team.", _CERT_REQ),
    _b("MGR-LEADERSHIP", "Leadership", BadgeCategory.MANAGERIAL, Stage.ELEVATE, 15,
       "Setting direction and taking people with you.", _CERT_REQ),
    _b("MGR-DECISION-MAKING", "Decision-Making", BadgeCategory.MANAGERIAL, Stage.EXCEL, 10,
       "Structured decisions under uncertainty.", _CERT_REQ),
    _b("MGR-NEGOTIATION", "Negotiation", BadgeCategory.MANAGERIAL, Stage.ELEVATE, 15,
       "Preparing for and conducting principled negotiations.", _CERT_REQ),
    _b("MGR-CONFLICT-MANAGEMENT", "Conflict Management", BadgeCategory.MANAGERIAL, Stage.ELEVATE, 15,
       "Surfacing and resolving conflict productively.", _CERT_REQ),
    _b("MGR-INFLUENCE-PERSUASION", "Influence & Persuasion", BadgeCategory.MANAGERIAL, Stage.ELEVATE, 15,
       "Building a case and moving stakeholders without authority.", _CERT_REQ),
    _b("MGR-STAKEHOLDER-MANAGEMENT", "Stakeholder Management", BadgeCategory.MANAGERIAL, Stage.ELEVATE, 15,
       "Mapping, prioritising and managing stakeholders.", _CERT_REQ),
    _b("MGR-CHANGE-MANAGEMENT", "Change Management", BadgeCategory.MANAGERIAL, Stage.ELEVATE, 15,
       "Planning and landing organisational change.", _CERT_REQ),
    # §5 Sectoral / Functional (16 across 4 tracks)
    _b("SEC-FIN-STATEMENT-ANALYSIS", "Financial Statement Analysis", BadgeCategory.SECTORAL, Stage.EXCEL, 15,
       "Reading and interpreting P&L, balance sheet and cash flows.", _CERT_REQ, track=SectoralTrack.FINANCE),
    _b("SEC-FIN-BANKING-BFSI", "Banking & BFSI Fundamentals", BadgeCategory.SECTORAL, Stage.EXCEL, 15,
       "How banks, insurance and financial services work.", _CERT_REQ, track=SectoralTrack.FINANCE),
    _b("SEC-FIN-INVESTMENT-MARKETS", "Investment & Capital Markets", BadgeCategory.SECTORAL, Stage.ELEVATE, 20,
       "Instruments, markets and portfolio basics.", _CERT_REQ, track=SectoralTrack.FINANCE),
    _b("SEC-FIN-MODELLING-VALUATION", "Financial Modelling & Valuation", BadgeCategory.SECTORAL, Stage.ELEVATE, 20,
       "Building models and valuing businesses.", _CERT_REQ, track=SectoralTrack.FINANCE),
    _b("SEC-HR-TALENT-ACQUISITION", "Talent Acquisition", BadgeCategory.SECTORAL, Stage.EXCEL, 15,
       "Sourcing, assessing and hiring talent.", _CERT_REQ, track=SectoralTrack.HR),
    _b("SEC-HR-LEARNING-PERFORMANCE", "Learning & Performance Management", BadgeCategory.SECTORAL, Stage.EXCEL, 15,
       "L&D design and performance systems.", _CERT_REQ, track=SectoralTrack.HR),
    _b("SEC-HR-ANALYTICS", "HR Analytics", BadgeCategory.SECTORAL, Stage.ELEVATE, 20,
       "People data: attrition, engagement, workforce planning.", _CERT_REQ, track=SectoralTrack.HR),
    _b("SEC-HR-RELATIONS-COMPLIANCE", "Employee Relations & HR Compliance", BadgeCategory.SECTORAL, Stage.ELEVATE, 20,
       "Labour law basics, ER and compliant HR practice.", _CERT_REQ, track=SectoralTrack.HR),
    _b("SEC-MKT-SALES-CUSTOMER", "Sales & Customer Management", BadgeCategory.SECTORAL, Stage.EXCEL, 15,
       "Pipeline, selling and account management.", _CERT_REQ, track=SectoralTrack.MARKETING),
    _b("SEC-MKT-DIGITAL-MARKETING", "Digital Marketing", BadgeCategory.SECTORAL, Stage.EXCEL, 15,
       "Search, social, content and paid channels.", _CERT_REQ, track=SectoralTrack.MARKETING),
    _b("SEC-MKT-ANALYTICS", "Marketing Analytics", BadgeCategory.SECTORAL, Stage.ELEVATE, 20,
       "Campaign measurement, attribution and funnels.", _CERT_REQ, track=SectoralTrack.MARKETING),
    _b("SEC-MKT-BRAND-GROWTH", "Brand & Growth Marketing", BadgeCategory.SECTORAL, Stage.ELEVATE, 20,
       "Positioning, brand building and growth loops.", _CERT_REQ, track=SectoralTrack.MARKETING),
    _b("SEC-BA-FUNDAMENTALS", "Business Analytics Fundamentals", BadgeCategory.SECTORAL, Stage.EXCEL, 15,
       "The analytics workflow: question, data, model, decision.", _CERT_REQ, track=SectoralTrack.BUSINESS_ANALYTICS),
    _b("SEC-BA-DATA-VISUALISATION", "Data Visualisation", BadgeCategory.SECTORAL, Stage.EXCEL, 15,
       "Charts that answer a business question honestly.", _CERT_REQ, track=SectoralTrack.BUSINESS_ANALYTICS),
    _b("SEC-BA-PREDICTIVE-DECISION", "Predictive & Decision Analytics", BadgeCategory.SECTORAL, Stage.ELEVATE, 20,
       "Forecasting and decision models.", _CERT_REQ, track=SectoralTrack.BUSINESS_ANALYTICS),
    _b("SEC-BA-DATA-STORYTELLING", "Data Storytelling", BadgeCategory.SECTORAL, Stage.ELEVATE, 20,
       "Narrating analysis for an executive audience.", _CERT_REQ, track=SectoralTrack.BUSINESS_ANALYTICS),
    # §6 Platform / Technical (10). "Microsoft Excel" the tool, not EXCEL the stage.
    _b("TECH-EXCEL-FOUNDATION", "Microsoft Excel – Foundation", BadgeCategory.PLATFORM, Stage.REBOOT, 10,
       "Core spreadsheet skills: formulas, references, tables.", _CERT_REQ),
    _b("TECH-EXCEL-ADVANCED", "Microsoft Excel – Advanced Business Applications", BadgeCategory.PLATFORM, Stage.EXCEL, 15,
       "Lookups, pivots, what-if and business modelling in Excel.", _CERT_REQ),
    _b("TECH-POWERPOINT", "PowerPoint for Business", BadgeCategory.PLATFORM, Stage.REBOOT, 10,
       "Building clear, professional decks.", _CERT_REQ),
    _b("TECH-POWER-BI", "Power BI", BadgeCategory.PLATFORM, Stage.EXCEL, 15,
       "Data models, DAX basics and interactive dashboards.", _CERT_REQ),
    _b("TECH-SQL", "SQL", BadgeCategory.PLATFORM, Stage.EXCEL, 15,
       "Querying relational data confidently.", _CERT_REQ),
    _b("TECH-PYTHON", "Python for Business", BadgeCategory.PLATFORM, Stage.ELEVATE, 20,
       "Scripting analysis and automation for business problems.", _CERT_REQ),
    _b("TECH-CRM", "CRM Platforms", BadgeCategory.PLATFORM, Stage.ELEVATE, 15,
       "Working a CRM (e.g. Salesforce) end to end.", _CERT_REQ),
    # §6: AI badges are CAPABILITIES, not tools — a ChatGPT/Claude/Gemini course
    # certificate is recorded as EVIDENCE under one of these three, never as a
    # badge of its own.
    _b("TECH-AI-LITERACY", "AI Literacy", BadgeCategory.PLATFORM, Stage.REBOOT, 10,
       "What AI systems can and cannot do, and how to use them responsibly.", _CERT_REQ),
    _b("TECH-AI-PRODUCTIVITY", "AI for Business Productivity", BadgeCategory.PLATFORM, Stage.EXCEL, 15,
       "Using AI tools to work faster on real business tasks.", _CERT_REQ),
    _b("TECH-AI-ANALYSIS", "AI for Analysis & Decision-Making", BadgeCategory.PLATFORM, Stage.ELEVATE, 20,
       "Applying AI to analysis, forecasting and decisions.", _CERT_REQ),
    # §7 Thinking (6)
    _b("THK-CRITICAL-THINKING", "Critical Thinking", BadgeCategory.THINKING, Stage.EXCEL, 15,
       "Evaluating arguments and evidence before concluding.", _CERT_REQ),
    _b("THK-STRUCTURED-PROBLEM-SOLVING", "Structured Problem-Solving", BadgeCategory.THINKING, Stage.EXCEL, 15,
       "Decomposing problems MECE-style and working hypotheses.", _CERT_REQ),
    _b("THK-ANALYTICAL-THINKING", "Analytical Thinking", BadgeCategory.THINKING, Stage.EXCEL, 15,
       "Quantitative reasoning about messy situations.", _CERT_REQ),
    _b("THK-DESIGN-THINKING", "Design Thinking", BadgeCategory.THINKING, Stage.EXCEL, 15,
       "User-centred problem framing and iteration.", _CERT_REQ),
    _b("THK-STRATEGIC-THINKING", "Strategic Thinking", BadgeCategory.THINKING, Stage.ELEVATE, 20,
       "Positioning, trade-offs and long-horizon choices.", _CERT_REQ),
    # §7: advanced thinking skills need APPLIED evidence, not only a certificate.
    _b("THK-BUSINESS-PROBLEM-SOLVING", "Business Problem-Solving", BadgeCategory.THINKING, Stage.ELEVATE, 25,
       "Solving a real business problem end to end.", _APPLIED_REQ),
    # §8 Interview & Placement Readiness (4) — assessment-threshold awards.
    _b("RDY-COMMUNICATION", "Communication Ready", BadgeCategory.READINESS, Stage.ELEVATE, 25,
       "Reading, writing, listening and speaking at placement standard.", _ASSESS_REQ, staff_awarded=True),
    _b("RDY-APTITUDE", "Aptitude Ready", BadgeCategory.READINESS, Stage.ELEVATE, 25,
       "Quantitative and logical reasoning at placement standard.", _ASSESS_REQ, staff_awarded=True),
    _b("RDY-DATA-INTERPRETATION", "Data Interpretation Ready", BadgeCategory.READINESS, Stage.ELEVATE, 25,
       "Data interpretation at placement standard.", _ASSESS_REQ, staff_awarded=True),
    _b("RDY-INTERVIEW", "Interview Ready", BadgeCategory.READINESS, Stage.ELEVATE, 25,
       "Resume, introduction, GD and interview performance at placement standard.", _ASSESS_REQ, staff_awarded=True),
)

BADGE_BY_CODE: Final[dict[str, BadgeDefinition]] = {b.code: b for b in BADGES}

CATEGORY_LABEL: Final[dict[BadgeCategory, str]] = {
    BadgeCategory.MANAGERIAL: "Managerial Skills",
    BadgeCategory.SECTORAL: "Sectoral Skills",
    BadgeCategory.PLATFORM: "Platform / Technical Skills",
    BadgeCategory.THINKING: "Thinking Skills",
    BadgeCategory.READINESS: "Interview Readiness",
}

TRACK_LABEL: Final[dict[SectoralTrack, str]] = {
    SectoralTrack.FINANCE: "Finance",
    SectoralTrack.HR: "Human Resources",
    SectoralTrack.MARKETING: "Marketing",
    SectoralTrack.BUSINESS_ANALYTICS: "Business Analytics",
}


# --- rows --------------------------------------------------------------------


class ApprovedCertification(Base):
    """§12 — the Approved Certification Catalogue, maintained by administration.

    THIS one is data, not code: providers and courses genuinely change term to
    term, and the office curates it. A student picking a row from here gets the
    simpler verification path; an off-catalogue upload is still allowed and
    simply reviewed the long way.
    """

    __tablename__ = "approved_certifications"
    __table_args__ = (
        Index("ix_approvedcert_badge", "badge_code"),
        # Each FK LEADS its own index, which is what makes it usable for the
        # lookup (test_every_foreign_key_column_is_indexed).
        Index("ix_approvedcert_college", "college_id"),
        Index("ix_approvedcert_course", "course_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    # Validated against BADGE_BY_CODE at the API edge, like milestone keys.
    badge_code: Mapped[str] = mapped_column(String)
    evidence_type: Mapped[EvidenceType] = mapped_column(
        Enum(EvidenceType, name="badge_evidence_type"),
        default=EvidenceType.EXTERNAL_VERIFIED,
        server_default="EXTERNAL_VERIFIED",
    )
    stage: Mapped[Stage] = mapped_column(
        Enum(Stage, name="stage", create_type=False), default=Stage.EXCEL, server_default="EXCEL"
    )
    duration_text: Mapped[str | None] = mapped_column(String, nullable=True)  # "≈10 hours"
    is_free: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # ------------------------------------------------------------------ #
    # WHO THIS ROW IS FOR (B13). Both NULLABLE, and **NULL MEANS
    # PROGRAMME-WIDE** — the row is visible to every college and every course.
    #
    # That is the meaning every row written before B13 already has, and it is
    # why neither column is NOT NULL with a fabricated default: a default
    # pointing at the first college on the deployment is how a SECOND college
    # silently inherits the first's catalogue, which is the exact failure this
    # scoping exists to prevent.
    #
    # No `ondelete` on either, like every other pointer into the spine: the
    # database refuses to delete a college or a course that still has
    # catalogue rows filed under it. The console archives; it does not delete.
    # ------------------------------------------------------------------ #
    college_id: Mapped[str | None] = mapped_column(
        ForeignKey("colleges.id"), nullable=True
    )
    course_id: Mapped[str | None] = mapped_column(
        ForeignKey("academic_courses.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BadgeEvidence(Base):
    """One piece of evidence a student attaches to a badge (§11/§12).

    Several rows per (student, badge) are legal and expected — the document's
    own example is Negotiation holding all three evidence types at once.
    """

    __tablename__ = "badge_evidence"
    __table_args__ = (
        Index("ix_badgeevidence_student_badge", "student_id", "badge_code"),
        Index("ix_badgeevidence_status", "status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    badge_code: Mapped[str] = mapped_column(String)

    evidence_type: Mapped[EvidenceType] = mapped_column(
        Enum(EvidenceType, name="badge_evidence_type", create_type=False)
    )
    status: Mapped[EvidenceStatus] = mapped_column(
        Enum(EvidenceStatus, name="badge_evidence_status"),
        default=EvidenceStatus.PENDING_VERIFICATION,
        server_default="PENDING_VERIFICATION",
    )

    # The certificate/document backing this claim — one of the student's own
    # uploads. SET NULL so deleting the upload leaves the claim (and its
    # verdict) as an honest audit line rather than vanishing history.
    upload_id: Mapped[str | None] = mapped_column(
        ForeignKey("uploads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Set when the student picked a catalogue row (§12's simpler path).
    approved_certification_id: Mapped[str | None] = mapped_column(
        ForeignKey("approved_certifications.id", ondelete="SET NULL"), nullable=True, index=True
    )

    title: Mapped[str] = mapped_column(String)  # what the evidence is
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    completed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    student_note: Mapped[str | None] = mapped_column(String, nullable=True)

    review_note: Mapped[str | None] = mapped_column(String, nullable=True)
    reviewed_by_id: Mapped[str | None] = mapped_column(String, nullable=True)  # users.id, audit stamp
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StudentBadge(Base):
    """A student's stored position on one badge. Absent = Not Started (§13)."""

    __tablename__ = "student_badges"
    __table_args__ = (
        UniqueConstraint("student_id", "badge_code", name="uq_student_badge"),
        CheckConstraint("points_awarded >= 0", name="ck_student_badge_points"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    badge_code: Mapped[str] = mapped_column(String)
    status: Mapped[StudentBadgeStatus] = mapped_column(
        Enum(StudentBadgeStatus, name="student_badge_status"),
        default=StudentBadgeStatus.IN_PROGRESS,
        server_default="IN_PROGRESS",
    )
    # Points are STAMPED at award time from the catalogue, so a later points
    # rebalance never silently rewrites history (§18 "assign badge points").
    points_awarded: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    earned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    awarded_by_id: Mapped[str | None] = mapped_column(String, nullable=True)  # users.id, audit stamp
    award_note: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CapabilityAssessment(Base):
    """§9/§15 — one capability score at one checkpoint, staff-entered.

    Unique per (student, capability, checkpoint): re-entering a score is an
    upsert, so a typo is corrected in place rather than growing a second T1.
    """

    __tablename__ = "capability_assessments"
    __table_args__ = (
        UniqueConstraint(
            "student_id", "capability", "checkpoint", name="uq_capability_checkpoint"
        ),
        # §9's 1–10, enforced where "validated at the edge" cannot be bypassed.
        CheckConstraint("score >= 1 AND score <= 10", name="ck_capassess_score_range"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    capability: Mapped[CapabilityKind] = mapped_column(
        Enum(CapabilityKind, name="capability_kind")
    )
    checkpoint: Mapped[AssessmentCheckpoint] = mapped_column(
        Enum(AssessmentCheckpoint, name="assessment_checkpoint")
    )
    score: Mapped[float] = mapped_column(Float)  # 1–10 (§9), validated at the edge
    recorded_by_id: Mapped[str | None] = mapped_column(String, nullable=True)  # users.id
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
