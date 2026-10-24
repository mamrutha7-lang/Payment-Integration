"""Skills & Badge framework — catalogue shape, the §12 review workflow, rule-2
scope, growth derivation and the §16 leaderboards."""

import uuid
from datetime import datetime, timezone

from conftest import TEST_PASSWORD, requires_db
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models.badge import (
    ApprovedCertification,
    BADGES,
    BadgeCategory,
    StudentBadge,
    StudentBadgeStatus,
)
from app.models.governance import CapabilityGrant, SubjectKind
from app.models.user import Mentor, Role, Student, User


def test_catalogue_matches_the_framework_document():
    """§4–§8: 12 managerial + 16 sectoral + 10 platform + 6 thinking + 4
    readiness = 48, codes unique, readiness staff-awarded only."""
    by_cat = {c: [b for b in BADGES if b.category == c] for c in BadgeCategory}
    assert len(by_cat[BadgeCategory.MANAGERIAL]) == 12
    assert len(by_cat[BadgeCategory.SECTORAL]) == 16
    assert len(by_cat[BadgeCategory.PLATFORM]) == 10
    assert len(by_cat[BadgeCategory.THINKING]) == 6
    assert len(by_cat[BadgeCategory.READINESS]) == 4
    assert len(BADGES) == 48
    assert len({b.code for b in BADGES}) == 48
    assert all(b.staff_awarded for b in by_cat[BadgeCategory.READINESS])
    assert not any(b.staff_awarded for b in BADGES if b.category != BadgeCategory.READINESS)
    # §5: four tracks of four.
    tracks = {}
    for b in by_cat[BadgeCategory.SECTORAL]:
        tracks.setdefault(b.track, []).append(b)
    assert {len(v) for v in tracks.values()} == {4}
    assert len(tracks) == 4


@requires_db
def test_dashboard_defaults_and_start(client, make_user):
    student = make_user("bdg-start", Role.STUDENT)
    dash = client.get("/api/student/badges", headers=student.headers).json()
    assert dash["badge_total"] == 48
    assert dash["earned_total"] == 0
    assert dash["points_total"] == 0
    all_badges = [b for c in dash["categories"] for b in c["badges"]]
    assert all(b["status"] == "NOT_STARTED" for b in all_badges)

    r = client.post("/api/student/badges/MGR-TEAMWORK/start", headers=student.headers)
    assert r.status_code == 200
    started = next(
        b for c in r.json()["categories"] for b in c["badges"] if b["code"] == "MGR-TEAMWORK"
    )
    assert started["status"] == "IN_PROGRESS"

    # A readiness badge cannot be started or claimed (§8).
    assert (
        client.post("/api/student/badges/RDY-INTERVIEW/start", headers=student.headers).status_code
        == 409
    )
    r = client.post(
        "/api/student/badges/RDY-INTERVIEW/evidence",
        headers=student.headers,
        json={"evidence_type": "EXTERNAL_VERIFIED", "title": "nope"},
    )
    assert r.status_code == 409


@requires_db
def test_evidence_review_mints_the_badge(client, make_user, granted):
    """§10/§12: upload → pending; APPROVE mints EARNED with catalogue points;
    a certificate alone never awards anything."""
    student = make_user("bdg-flow", Role.STUDENT)
    verifier = make_user("bdg-dir", Role.ADMIN)
    # The office account holds no verification queue by role — it is a faculty
    # instrument. It grants itself one, which is the design's own answer for
    # "a student's evidence is stuck and nobody else will look".
    granted(verifier, "mentor.verifications")

    r = client.post(
        "/api/student/badges/MGR-NEGOTIATION/evidence",
        headers=student.headers,
        json={"evidence_type": "EXTERNAL_VERIFIED", "title": "Negotiation certificate", "provider": "Coursera"},
    )
    assert r.status_code == 201
    badge = next(
        b for c in r.json()["categories"] for b in c["badges"] if b["code"] == "MGR-NEGOTIATION"
    )
    assert badge["status"] == "VERIFICATION_PENDING"
    assert badge["points_earned"] == 0  # uploaded, not earned

    queue = client.get("/api/mentor/badge-evidence/pending", headers=verifier.headers).json()
    mine = [
        q for q in queue
        if q["student_id"] == student.user_id or q["title"] == "Negotiation certificate"
    ]
    ev = next(q for q in queue if q["title"] == "Negotiation certificate")
    assert ev["badge_name"] == "Negotiation"

    r = client.post(
        f"/api/mentor/badge-evidence/{ev['id']}/review",
        headers=verifier.headers,
        json={"decision": "APPROVE", "note": "Verified against provider"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "APPROVED"

    dash = client.get("/api/student/badges", headers=student.headers).json()
    badge = next(
        b for c in dash["categories"] for b in c["badges"] if b["code"] == "MGR-NEGOTIATION"
    )
    assert badge["status"] == "EARNED"
    assert badge["points_earned"] == 15  # stamped from the catalogue
    assert badge["advanced_evidence_available"] is True  # two §11 types still open
    assert dash["points_total"] == 15
    assert mine  # sanity: the queue really carried it


@requires_db
def test_reject_and_more_info_mint_nothing(client, make_user, granted):
    student = make_user("bdg-rej", Role.STUDENT)
    verifier = make_user("bdg-rej-dir", Role.ADMIN)
    granted(verifier, "mentor.verifications")
    for decision, expected in (("REJECT", "REJECTED"), ("MORE_INFO", "MORE_INFO_REQUIRED")):
        client.post(
            "/api/student/badges/THK-DESIGN-THINKING/evidence",
            headers=student.headers,
            json={"evidence_type": "APPLIED", "title": f"attempt {decision}"},
        )
        queue = client.get("/api/mentor/badge-evidence/pending", headers=verifier.headers).json()
        ev = next(q for q in queue if q["title"] == f"attempt {decision}")
        r = client.post(
            f"/api/mentor/badge-evidence/{ev['id']}/review",
            headers=verifier.headers,
            json={"decision": decision, "note": "see note"},
        )
        assert r.json()["status"] == expected
    dash = client.get("/api/student/badges", headers=student.headers).json()
    badge = next(
        b for c in dash["categories"] for b in c["badges"] if b["code"] == "THK-DESIGN-THINKING"
    )
    assert badge["status"] == "IN_PROGRESS"  # a row exists; nothing earned
    assert dash["points_total"] == 0


@requires_db
def test_groupless_mentor_sees_no_queue_and_students_see_no_admin(client, make_user):
    mentor = make_user("bdg-scope", Role.MENTOR)  # no Mentor row => no group
    # 403 since B2.3, where it was `200 []`. A faculty account holds
    # `mentor.verifications` only as a grant from the office (2026-10-16; it
    # was derived from mentoring until then), so the capability gate refuses
    # before the queue is narrowed.
    assert client.get("/api/mentor/badge-evidence/pending", headers=mentor.headers).status_code == 403

    student = make_user("bdg-scope-stud", Role.STUDENT)
    assert (
        client.get("/api/mentor/badge-evidence/pending", headers=student.headers).status_code == 403
    )
    # Cohort views and the export are DIRECTOR/ADMIN, not any staff.
    assert client.get("/api/admin/badges/cohort", headers=mentor.headers).status_code == 403
    assert client.get("/api/admin/badges/export.csv", headers=mentor.headers).status_code == 403


def _grant_row(user_id: str, key: str) -> None:
    """A live programme-wide grant, written as a row (test_scoped_lists.py's
    reason: a MENTOR cannot call the Governance endpoint for itself). The
    `make_user` teardown deletes grants by subject, so nothing leaks."""
    with SessionLocal() as db:
        db.add(
            CapabilityGrant(
                capability=key,
                subject_kind=SubjectKind.USER,
                subject_user_id=user_id,
                reason="badge tests: a granted verifier who mentors nobody",
            )
        )
        db.commit()


@requires_db
def test_a_granted_verifier_reviews_students_they_do_not_mentor(client, make_user):
    """2026-10-13: verification no longer depends on mentoring.

    On production 128 certificates from 26 students sat pending because none of
    those students had a mentor, so no faculty member could see them. A faculty
    member GRANTED "Verify skills & evidence" now sees and decides every student
    the grant covers — with no Mentor group at all — through the queue, the
    reviewed strip and the review endpoint alike. Without the grant the same
    account is refused. Since 2026-10-16 the grant is the only path: mentoring
    brings none (test_a_mentor_without_the_grant_cannot_decide_any_student).
    """
    student = make_user("bdg-unmentored", Role.STUDENT)  # nobody mentors them
    verifier = make_user("bdg-granted-fac", Role.MENTOR)  # mentors nobody
    r = client.post(
        "/api/student/badges/TECH-CRM/evidence",
        headers=student.headers,
        json={"evidence_type": "EXTERNAL_VERIFIED", "title": "Unmentored CRM certificate"},
    )
    assert r.status_code == 201, r.text

    # No grant, no group: refused at the capability gate, exactly as before.
    assert (
        client.get("/api/mentor/badge-evidence/pending", headers=verifier.headers).status_code
        == 403
    )

    _grant_row(verifier.user_id, "mentor.verifications")
    queue = client.get("/api/mentor/badge-evidence/pending", headers=verifier.headers)
    assert queue.status_code == 200, queue.text
    ev = next(q for q in queue.json() if q["title"] == "Unmentored CRM certificate")

    r = client.post(
        f"/api/mentor/badge-evidence/{ev['id']}/review",
        headers=verifier.headers,
        json={"decision": "APPROVE", "note": "Checked with the issuer"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "APPROVED"

    reviewed = client.get("/api/mentor/badge-evidence/reviewed", headers=verifier.headers)
    assert reviewed.status_code == 200, reviewed.text
    row = next(x for x in reviewed.json() if x["id"] == ev["id"])
    assert row["review_note"] == "Checked with the issuer"

    dash = client.get("/api/student/badges", headers=student.headers).json()
    crm = next(b for c in dash["categories"] for b in c["badges"] if b["code"] == "TECH-CRM")
    assert crm["status"] == "EARNED"


@requires_db
def test_a_mentor_without_the_grant_cannot_decide_any_student(client, make_user, login):
    """The GRANT is the only door, and mentoring is not one (2026-10-16, the
    owner's decision). A faculty member who mentors somebody but holds no
    grant is refused the queue at the capability gate -- for their OWN mentee's
    claim as much as for a stranger's -- and a decision by id is refused too.
    Until this date they held `mentor.verifications` as a function of
    mentoring and decided their own group's claims."""
    student = make_user("bdg-not-yours", Role.STUDENT)
    granted_admin = make_user("bdg-not-yours-adm", Role.ADMIN)
    _grant_row(granted_admin.user_id, "mentor.verifications")
    mentee = make_user("bdg-own-mentee", Role.STUDENT)
    faculty = make_user("bdg-own-fac", Role.MENTOR)
    with SessionLocal() as db:
        group = Mentor(user_id=faculty.user_id)
        db.add(group)
        db.flush()
        db.scalar(select(Student).where(Student.user_id == mentee.user_id)).mentor_id = group.id
        db.commit()
    headers = login(faculty.email, TEST_PASSWORD)  # mentorId is minted at login

    for who, title in ((student, "Not your mentee's leadership"), (mentee, "Your mentee's leadership")):
        client.post(
            "/api/student/badges/MGR-LEADERSHIP/evidence",
            headers=who.headers,
            json={"evidence_type": "APPLIED", "title": title},
        )
    queue = client.get("/api/mentor/badge-evidence/pending", headers=granted_admin.headers).json()
    for title in ("Not your mentee's leadership", "Your mentee's leadership"):
        ev = next(q for q in queue if q["title"] == title)
        r = client.post(
            f"/api/mentor/badge-evidence/{ev['id']}/review",
            headers=headers,
            json={"decision": "APPROVE"},
        )
        assert r.status_code == 403, (title, r.text)
    assert client.get("/api/mentor/badge-evidence/pending", headers=headers).status_code == 403


@requires_db
def test_manual_award_and_revoke(client, make_user):
    student = make_user("bdg-award", Role.STUDENT)
    director = make_user("bdg-award-dir", Role.ADMIN)

    r = client.post(
        f"/api/mentor/students/{_student_id(client, student)}/badges/RDY-APTITUDE/award",
        headers=director.headers,
        json={"note": "Cleared the aptitude threshold"},
    )
    assert r.status_code == 200
    badge = next(
        b for c in r.json()["categories"] for b in c["badges"] if b["code"] == "RDY-APTITUDE"
    )
    assert badge["status"] == "EARNED"
    assert badge["points_earned"] == 25

    r = client.post(
        f"/api/mentor/students/{_student_id(client, student)}/badges/RDY-APTITUDE/revoke",
        headers=director.headers,
        json={"note": "entered against the wrong student"},
    )
    assert r.status_code == 200
    badge = next(
        b for c in r.json()["categories"] for b in c["badges"] if b["code"] == "RDY-APTITUDE"
    )
    assert badge["status"] == "NOT_STARTED"


@requires_db
def test_growth_derivation_and_assessment_upsert(client, make_user):
    """§9/§15: dashes until assessed, growth only once T0 AND a later score
    exist, and re-entering a score corrects in place."""
    student = make_user("bdg-growth", Role.STUDENT)
    director = make_user("bdg-growth-dir", Role.ADMIN)
    sid = _student_id(client, student)

    g = client.get("/api/student/growth", headers=student.headers).json()
    assert all(r["current"] is None and r["growth"] is None for r in g["rows"])

    r = client.post(
        f"/api/mentor/students/{sid}/assessments",
        headers=director.headers,
        json={"checkpoint": "T0", "scores": {"SPEAKING": 3.4, "WRITING": 4.0}},
    )
    assert r.status_code == 200
    r = client.post(
        f"/api/mentor/students/{sid}/assessments",
        headers=director.headers,
        json={"checkpoint": "T1", "scores": {"SPEAKING": 4.8}},
    )
    g = r.json()
    speaking = next(row for row in g["rows"] if row["capability"] == "SPEAKING")
    assert speaking["scores"]["T0"] == 3.4 and speaking["scores"]["T1"] == 4.8
    assert speaking["current"] == 4.8
    assert abs(speaking["growth"] - 1.4) < 1e-6
    writing = next(row for row in g["rows"] if row["capability"] == "WRITING")
    assert writing["growth"] is None  # only T0 — no later score, no growth claim

    # Upsert: correcting T1 replaces it, never duplicates.
    r = client.post(
        f"/api/mentor/students/{sid}/assessments",
        headers=director.headers,
        json={"checkpoint": "T1", "scores": {"SPEAKING": 5.0}},
    )
    speaking = next(row for row in r.json()["rows"] if row["capability"] == "SPEAKING")
    assert speaking["scores"]["T1"] == 5.0

    # Out-of-scale refused.
    r = client.post(
        f"/api/mentor/students/{sid}/assessments",
        headers=director.headers,
        json={"checkpoint": "T2", "scores": {"SPEAKING": 11}},
    )
    assert r.status_code == 422


@requires_db
def test_leaderboards_views(client, make_user):
    student = make_user("bdg-lb", Role.STUDENT)
    r = client.get("/api/student/badges/leaderboards?view=overall", headers=student.headers)
    assert r.status_code == 200 and r.json()["unit"] == "points"
    r = client.get("/api/student/badges/leaderboards?view=most_improved", headers=student.headers)
    assert r.status_code == 200 and r.json()["unit"] == "growth"
    r = client.get(
        "/api/student/badges/leaderboards?view=sectoral&track=FINANCE", headers=student.headers
    )
    assert r.status_code == 200
    assert (
        client.get("/api/student/badges/leaderboards?view=nope", headers=student.headers).status_code
        == 422
    )


@requires_db
def test_a_removed_student_is_on_no_badge_board(client, make_user):
    """REMOVE answers "… is off every screen", and these boards are screens
    too: the roster behind them left out nobody. The removed student holds
    every badge, so nobody can outrank them off the top fifty instead."""
    viewer = make_user("bdg-lb-view", Role.STUDENT)
    removed = make_user(f"bdg-lb-gone-{uuid.uuid4().hex[:6]}", Role.STUDENT)
    admin = make_user("bdg-lb-office", Role.ADMIN)
    with SessionLocal() as db:
        sid = db.scalar(select(Student.id).where(Student.user_id == removed.user_id))
        for badge in BADGES:
            db.add(
                StudentBadge(
                    student_id=sid,
                    badge_code=badge.code,
                    status=StudentBadgeStatus.EARNED,
                    points_awarded=badge.points,
                    earned_at=datetime.now(timezone.utc),
                )
            )
        db.commit()
        name = db.scalar(select(User.name).where(User.id == removed.user_id))

    def names_on_overall() -> set[str]:
        r = client.get("/api/student/badges/leaderboards?view=overall", headers=viewer.headers)
        assert r.status_code == 200, r.text
        return {row["name"] for row in r.json()["rows"]}

    assert name in names_on_overall()
    r = client.post(
        f"/api/admin/students/{sid}/remove",
        headers=admin.headers,
        json={"reason": "Withdrew from the programme"},
    )
    assert r.status_code == 200, r.text
    assert name not in names_on_overall()


@requires_db
def test_approved_certification_catalogue_and_the_simpler_path(client, make_user):
    """§12: directors curate the catalogue; a student picking a row gets
    title/provider/type from it, and a wrong-badge pick is refused."""
    student = make_user("bdg-cat", Role.STUDENT)
    director = make_user("bdg-cat-dir", Role.ADMIN)

    r = client.post(
        "/api/admin/approved-certifications",
        headers=director.headers,
        json={
            "name": "Test Catalogue Cert",
            "provider": "Test Provider",
            "badge_code": "TECH-SQL",
        },
    )
    assert r.status_code == 201, r.text
    cert = r.json()
    try:
        # Students see it on the badge; picking it fills the claim.
        dash = client.get("/api/student/badges", headers=student.headers).json()
        sql_badge = next(
            b for c in dash["categories"] for b in c["badges"] if b["code"] == "TECH-SQL"
        )
        assert any(c["id"] == cert["id"] for c in sql_badge["approved_certifications"])

        r = client.post(
            "/api/student/badges/TECH-SQL/evidence",
            headers=student.headers,
            json={"approved_certification_id": cert["id"]},
        )
        assert r.status_code == 201
        badge = next(
            b for c in r.json()["categories"] for b in c["badges"] if b["code"] == "TECH-SQL"
        )
        ev = badge["evidence"][0]
        assert ev["title"] == "Test Catalogue Cert"
        assert ev["from_catalogue"] is True

        # The same catalogue row cannot claim a different badge.
        r = client.post(
            "/api/student/badges/TECH-PYTHON/evidence",
            headers=student.headers,
            json={"approved_certification_id": cert["id"]},
        )
        assert r.status_code == 422

        # Mentors cannot curate the catalogue.
        mentor = make_user("bdg-cat-mentor", Role.MENTOR)
        assert (
            client.get("/api/admin/approved-certifications", headers=mentor.headers).status_code
            == 403
        )
    finally:
        with SessionLocal() as db:
            db.execute(delete(ApprovedCertification).where(ApprovedCertification.id == cert["id"]))
            db.commit()


def _student_id(client, user) -> str:
    me = client.get("/api/auth/me", headers=user.headers).json()
    return me["studentId"]
