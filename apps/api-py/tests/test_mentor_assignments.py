"""B9.1 — the assignment history, and the 90-day handover.

Four claims, and the third is the one the whole design turns on.

1. Every move writes a spell, the current pair is the OPEN row, and the same
   pair written twice writes nothing.
2. A release mints a `mentor.mentees` grant scoped to that ONE student, with a
   90-day expiry, and it is written ACTIVE — a handover that landed
   `pending_approval` would hold nothing on the single-admin deployment this
   product is built for.
3. THE GRANT RESTORES THE CAPABILITY, WHICH IS THE HAZARD THE BRANCH ALONE
   COULD NOT FIX. `mentor_functions_for` derives the four `mentor.*` keys live
   from `mentee_count > 0`, so a faculty member whose last mentee was just
   reassigned holds NONE of them and `require_capability` refuses at the door —
   before `assert_student_scope` runs at all. A handover honoured only inside
   rule 2 would be invisible to exactly the person it was built for.
4. THE WINDOW IS READ-ONLY. The same account that can now GET the student's
   notes is still refused the POST, because `allow_handover` is passed True at
   GET call sites only and the gate defaults it to False.

And two that say what the handover must NOT be:

5. A PROGRAMME-WIDE `mentor.mentees` grant does not open rule 2 for anybody.
   `holds_handover_for` matches the scope pair (STUDENT, this student) exactly
   and refuses `reaches_target`, which a programme-wide grant satisfies for
   every student alive. AGENTS.md: "a capability can never relax the student
   filter"; this is the test that keeps that true.
6. Revoking the grant closes the door the same second — which is the reason the
   window reads a grant rather than `mentor_assignments.to_at`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app.db import SessionLocal
from app.models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from app.models.mentor_assignment import HANDOVER_DAYS, MentorAssignment
from app.models.user import Mentor, Role, Student, User

ADMIN_API = "/api/admin"
REASON = {"reason": "Handover while Dr Rao is on sabbatical."}


def _student_id(user_id: str) -> str:
    with SessionLocal() as db:
        return db.scalar(select(Student.id).where(Student.user_id == user_id))


def _group_of(user_id: str) -> str | None:
    with SessionLocal() as db:
        return db.scalar(select(Mentor.id).where(Mentor.user_id == user_id))


def _spells(student_id: str) -> list[MentorAssignment]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(MentorAssignment)
                .where(MentorAssignment.student_id == student_id)
                .order_by(MentorAssignment.created_at)
            ).all()
        )


@pytest.fixture
def swept(make_user):
    """Spell rows and handover grants, removed before `make_user` takes the
    accounts. Neither `mentor_assignments` pointer carries an `ondelete`, on
    purpose — the database refuses to delete a student or a group out from under
    a recorded spell — so this ordering is load-bearing, not tidiness."""
    student_ids: list[str] = []
    faculty_user_ids: list[str] = []
    yield student_ids, faculty_user_ids
    with SessionLocal() as db:
        for sid in student_ids:
            db.execute(delete(MentorAssignment).where(MentorAssignment.student_id == sid))
        for uid in faculty_user_ids:
            db.execute(delete(CapabilityGrant).where(CapabilityGrant.subject_user_id == uid))
            for gid in db.scalars(select(Mentor.id).where(Mentor.user_id == uid)).all():
                db.execute(delete(MentorAssignment).where(MentorAssignment.mentor_id == gid))
        db.commit()
    with SessionLocal() as db:
        for uid in faculty_user_ids:
            for group in db.scalars(select(Mentor).where(Mentor.user_id == uid)).all():
                for st in db.scalars(select(Student).where(Student.mentor_id == group.id)).all():
                    st.mentor_id = None
                db.flush()
                db.delete(group)
        db.commit()


# --------------------------------------------------------------- history --


@requires_db
def test_every_move_writes_a_spell_and_the_open_row_is_the_current_pair(client, make_user, swept):
    """One open row per current pair — the property the migration seeds and
    every writer maintains. A reassignment closes one spell and opens another
    with the SAME act stamped on both ends; a re-save of an unchanged pair
    writes nothing, because the console posts one request per ticked student
    and a history that grows a row per Save is a history nobody reads."""
    student_ids, faculty_ids = swept
    admin = make_user("ma-adm", Role.ADMIN)
    first = make_user("ma-fac1", Role.MENTOR)
    second = make_user("ma-fac2", Role.MENTOR)
    stu = make_user("ma-stu", Role.STUDENT)
    sid = _student_id(stu.user_id)
    student_ids.append(sid)
    faculty_ids += [first.user_id, second.user_id]
    assign = f"{ADMIN_API}/students/{sid}/mentor"

    # Never assigned: NO ROW AT ALL. "Never had a mentor" and "has had one since
    # forever" must not read the same, which is why the migration writes nothing
    # for an unseated student either.
    assert _spells(sid) == []

    r = client.post(assign, headers=admin.headers, json={"mentor_user_id": first.user_id, **REASON})
    assert r.status_code == 204, r.text
    rows = _spells(sid)
    assert len(rows) == 1
    assert rows[0].to_at is None and rows[0].kind == "assign"
    assert rows[0].reason == REASON["reason"]
    assert rows[0].by_user_id == admin.user_id

    # The same pair again: nothing new.
    assert client.post(assign, headers=admin.headers, json={"mentor_user_id": first.user_id, **REASON}).status_code == 204
    assert len(_spells(sid)) == 1, "a re-save of an unchanged pair must not grow the history"

    # Reassignment: the first closes, the second opens, both say `reassign`.
    assert client.post(assign, headers=admin.headers, json={"mentor_user_id": second.user_id, **REASON}).status_code == 204
    rows = _spells(sid)
    assert len(rows) == 2
    closed, opened = rows
    assert closed.to_at is not None and closed.end_kind == "reassign"
    assert closed.ended_by_user_id == admin.user_id and closed.end_reason == REASON["reason"]
    # AND THE OPENING ACT SURVIVED THE CLOSE. `by_user_id`/`reason` describe who
    # opened the spell and are never rewritten; a single-column design would
    # have overwritten "who seated this student with the first mentor" here.
    assert closed.by_user_id == admin.user_id and closed.kind == "assign"
    assert opened.to_at is None and opened.kind == "reassign"

    # And the read endpoint shows them newest first, open row leading.
    body = client.get(f"{ADMIN_API}/students/{sid}/mentor-history", headers=admin.headers).json()
    assert [row["to_at"] is None for row in body] == [True, False]
    assert body[0]["end_kind"] is None and body[1]["end_kind"] == "reassign"


@requires_db
def test_an_assignment_without_a_reason_is_refused(client, make_user, swept):
    """B9.2. 422, and the reason is required on a RELEASE as well.

    `mentor_id` is rule 2's scope key: moving a student changes who may read
    their marks, attendance, USN, mentor notes and interview transcripts. A
    release is the move nothing else on any screen reports — the student simply
    stops appearing in a group — so it is the one that most needs a sentence.
    """
    student_ids, faculty_ids = swept
    admin = make_user("ma-noreason-adm", Role.ADMIN)
    faculty = make_user("ma-noreason-fac", Role.MENTOR)
    stu = make_user("ma-noreason-stu", Role.STUDENT)
    sid = _student_id(stu.user_id)
    student_ids.append(sid)
    faculty_ids.append(faculty.user_id)
    assign = f"{ADMIN_API}/students/{sid}/mentor"

    assert client.post(assign, headers=admin.headers, json={"mentor_user_id": faculty.user_id}).status_code == 422
    assert client.post(assign, headers=admin.headers, json={"mentor_id": None}).status_code == 422
    assert client.post(assign, headers=admin.headers, json={"mentor_user_id": faculty.user_id, "reason": "   "}).status_code == 422
    assert _spells(sid) == [], "a refused assignment wrote no history"


# -------------------------------------------------------------- handover --


@requires_db
def test_a_release_mints_a_live_student_scoped_grant_for_ninety_days(client, make_user, swept):
    """The window is a grant, and it is ACTIVE, expiring, and pinned to ONE
    student. Asserted as behaviour rather than as a flag on the catalogue:
    should `mentor.mentees` ever be marked `carries_pii`, B2.4 would write this
    `pending_approval` and every handover on a one-admin deployment would
    silently hold nothing. This fails there rather than in a support call."""
    student_ids, faculty_ids = swept
    admin = make_user("ma-rel-adm", Role.ADMIN)
    faculty = make_user("ma-rel-fac", Role.MENTOR)
    stu = make_user("ma-rel-stu", Role.STUDENT)
    sid = _student_id(stu.user_id)
    student_ids.append(sid)
    faculty_ids.append(faculty.user_id)
    assign = f"{ADMIN_API}/students/{sid}/mentor"

    assert client.post(assign, headers=admin.headers, json={"mentor_user_id": faculty.user_id, **REASON}).status_code == 204
    # Nothing yet: they still mentor the student.
    with SessionLocal() as db:
        assert db.scalar(
            select(CapabilityGrant.id).where(CapabilityGrant.subject_user_id == faculty.user_id)
        ) is None

    assert client.post(assign, headers=admin.headers, json={"mentor_id": None, **REASON}).status_code == 204
    with SessionLocal() as db:
        grant = db.scalar(
            select(CapabilityGrant).where(CapabilityGrant.subject_user_id == faculty.user_id)
        )
        assert grant is not None, "a release minted no handover grant"
        assert grant.capability == "mentor.mentees"
        assert grant.subject_kind is SubjectKind.USER
        assert grant.scope_level is ScopeLevel.STUDENT and grant.scope_id == sid
        assert grant.approval_state == "active", "a pending handover holds nothing"
        assert grant.reason == "handover"
        assert grant.role_at_grant == "MENTOR"
        window = grant.expires_at - datetime.now(timezone.utc)
        assert timedelta(days=HANDOVER_DAYS - 1) < window <= timedelta(days=HANDOVER_DAYS)

    # Releasing again does not stack a second window.
    assert client.post(assign, headers=admin.headers, json={"mentor_id": None, **REASON}).status_code == 204
    with SessionLocal() as db:
        assert db.scalar(
            select(CapabilityGrant.id).where(
                CapabilityGrant.subject_user_id == faculty.user_id
            ).order_by(CapabilityGrant.id)
        ) is not None
        assert len(db.scalars(
            select(CapabilityGrant.id).where(CapabilityGrant.subject_user_id == faculty.user_id)
        ).all()) == 1


@requires_db
def test_the_handover_restores_the_capability_and_opens_the_read_but_not_the_write(
    client, make_user, swept
):
    """HAZARD 3, AND THE READ-ONLY HALF, IN ONE TEST BECAUSE THEY ARE ONE CLAIM.

    `mentor_functions_for` derives the four `mentor.*` capabilities live from
    `mentee_count > 0`. Release a faculty member's only mentee and they hold
    none of them, so `require_capability` answers 403 at the door and
    `assert_student_scope` never runs — a handover honoured only inside rule 2
    would be invisible to precisely the mentor it exists for. The grant fixes
    that for free, because `capabilities_for` unions grants in. **This is the
    assertion the owner asked to see.**

    And then the window stops at the door of the write: the same account, the
    same student, the same second — GET 200, POST 403 — because
    `allow_handover` is passed True at GET call sites only and
    `assert_student_scope` defaults it to False. Fifteen of that gate's call
    sites are writes; this is what keeps a 90-day read from becoming a 90-day
    right to write about somebody else's student.
    """
    student_ids, faculty_ids = swept
    admin = make_user("ma-hand-adm", Role.ADMIN)
    faculty = make_user("ma-hand-fac", Role.MENTOR)
    successor = make_user("ma-hand-fac2", Role.MENTOR)
    stu = make_user("ma-hand-stu", Role.STUDENT)
    sid = _student_id(stu.user_id)
    student_ids.append(sid)
    faculty_ids += [faculty.user_id, successor.user_id]
    assign = f"{ADMIN_API}/students/{sid}/mentor"
    notes = f"/api/mentor/students/{sid}/notes"

    # Before any assignment: no mentees, so no functions, so 403 at the door.
    assert client.get(notes, headers=faculty.headers).status_code == 403

    assert client.post(assign, headers=admin.headers, json={"mentor_user_id": faculty.user_id, **REASON}).status_code == 204
    assert client.get(notes, headers=faculty.headers).status_code == 200
    wrote = client.post(
        notes, headers=faculty.headers,
        json={"note_text": "Discussed the internship shortlist.", "linked_action": "ONE_ON_ONE_SCHEDULED"},
    )
    assert wrote.status_code in (200, 201), wrote.text

    # Handed over to a colleague. This account now mentors NOBODY.
    assert client.post(assign, headers=admin.headers, json={"mentor_user_id": successor.user_id, **REASON}).status_code == 204
    with SessionLocal() as db:
        from app.mentor_functions import mentee_count

        assert mentee_count(db, faculty.user_id) == 0

    # THE READ STILL WORKS. Without the grant this is a 403 from
    # require_capability and the handover is invisible.
    r = client.get(notes, headers=faculty.headers)
    assert r.status_code == 200, (
        "the handover grant did not restore mentor.mentees — hazard 3: a mentor "
        "with no mentees holds none of the four derived functions, so the 403 "
        "happens before assert_student_scope ever runs"
    )

    # THE WRITE DOES NOT. Same account, same student, same second.
    refused = client.post(
        notes, headers=faculty.headers,
        json={"note_text": "Still writing about a student who is not mine.", "linked_action": "ONE_ON_ONE_SCHEDULED"},
    )
    assert refused.status_code == 404, (
        "the handover window opened a WRITE — allow_handover must be passed at "
        "GET call sites only"
    )

    # And the successor, who actually mentors them, can do both.
    assert client.get(notes, headers=successor.headers).status_code == 200


@requires_db
def test_a_programme_wide_grant_does_not_open_rule_two_for_anybody(client, make_user, swept):
    """THE WIDENING THIS DESIGN MUST NOT CAUSE.

    Read with `governance.reaches_target`, the handover branch would pass for
    any holder of a PROGRAMME-WIDE `mentor.mentees` grant — which is how the
    Main Admin reaches a stuck student's evidence — and that grant would
    silently become universal access to every student's records on the deploy
    that shipped it. `holds_handover_for` matches the scope pair (STUDENT, this
    student) exactly, and only a handover or an administrator naming one student
    on purpose can write that pair.
    """
    student_ids, faculty_ids = swept
    admin = make_user("ma-wide-adm", Role.ADMIN)
    faculty = make_user("ma-wide-fac", Role.MENTOR)
    stu = make_user("ma-wide-stu", Role.STUDENT)
    sid = _student_id(stu.user_id)
    student_ids.append(sid)
    faculty_ids.append(faculty.user_id)

    r = client.post(
        "/api/admin/governance/grants",
        headers=admin.headers,
        json={
            "capability": "mentor.mentees",
            "user_ids": [faculty.user_id],
            "reason": "Standing in for the faculty lead across the programme this term.",
        },
    )
    assert r.status_code == 201, r.text
    # The capability is held — the screen opens — and rule 2 still refuses this
    # student, because the two fences are checked separately and on purpose.
    assert client.get(f"/api/mentor/students/{sid}/notes", headers=faculty.headers).status_code == 404


@requires_db
def test_revoking_the_handover_closes_the_door_the_same_second(client, make_user, swept):
    """The reason the window reads a GRANT and not `mentor_assignments.to_at`.

    A copy of the rule in rule 2 ("to_at > now - 90 days") would keep the door
    open after an administrator revoked the grant in Governance, which is two
    sources of truth for one permission. Liveness is asked of
    `_live_grant_clauses`, the one definition of what makes a grant count.
    """
    student_ids, faculty_ids = swept
    admin = make_user("ma-rev-adm", Role.ADMIN)
    faculty = make_user("ma-rev-fac", Role.MENTOR)
    successor = make_user("ma-rev-fac2", Role.MENTOR)
    stu = make_user("ma-rev-stu", Role.STUDENT)
    sid = _student_id(stu.user_id)
    student_ids.append(sid)
    faculty_ids += [faculty.user_id, successor.user_id]
    assign = f"{ADMIN_API}/students/{sid}/mentor"
    notes = f"/api/mentor/students/{sid}/notes"

    assert client.post(assign, headers=admin.headers, json={"mentor_user_id": faculty.user_id, **REASON}).status_code == 204
    assert client.post(assign, headers=admin.headers, json={"mentor_user_id": successor.user_id, **REASON}).status_code == 204
    assert client.get(notes, headers=faculty.headers).status_code == 200

    with SessionLocal() as db:
        grant_id = db.scalar(
            select(CapabilityGrant.id).where(CapabilityGrant.subject_user_id == faculty.user_id)
        )
    revoked = client.post(
        f"/api/admin/governance/grants/{grant_id}/revoke",
        headers=admin.headers,
        json={"reason": "The handover is finished; the successor has what they need."},
    )
    assert revoked.status_code == 200, revoked.text
    assert client.get(notes, headers=faculty.headers).status_code == 403, (
        "a revoked handover still opened the mentee log"
    )


# ------------------------------------------------- disabling an account --


@requires_db
def test_disabling_a_faculty_account_releases_its_mentees_and_hands_nothing_over(
    client, make_user, swept
):
    """B9.1's `faculty_disabled` half, which `admin_faculty.disable_account`
    deferred here in its own docstring.

    Those students used to sit in a group nobody can open — not a leak, because
    a disabled account cannot make a request, but invisible in the unassigned
    pool, which is where the office looks for students who need somebody. They
    are released and the spell says why.

    AND NO HANDOVER GRANT IS MINTED. An offboarded account cannot sign in to be
    asked about a note it wrote; a live window on the Governance screen for
    somebody who has been offboarded reads as access nobody revoked.
    """
    student_ids, faculty_ids = swept
    admin = make_user("ma-dis-adm", Role.ADMIN)
    faculty = make_user("ma-dis-fac", Role.MENTOR)
    stu = make_user("ma-dis-stu", Role.STUDENT)
    sid = _student_id(stu.user_id)
    student_ids.append(sid)
    faculty_ids.append(faculty.user_id)

    assert client.post(
        f"{ADMIN_API}/students/{sid}/mentor",
        headers=admin.headers, json={"mentor_user_id": faculty.user_id, **REASON},
    ).status_code == 204

    r = client.post(
        f"{ADMIN_API}/users/{faculty.user_id}/disable",
        headers=admin.headers,
        json={"reason": "Left the institution at the end of term."},
    )
    assert r.status_code == 200, r.text
    assert r.json()["mentees_released"] == 1
    with SessionLocal() as db:
        assert db.get(Student, sid).mentor_id is None
        assert db.scalar(
            select(CapabilityGrant.id).where(CapabilityGrant.subject_user_id == faculty.user_id)
        ) is None, "a disabled account was handed a 90-day window it can never use"
    rows = _spells(sid)
    assert len(rows) == 1 and rows[0].end_kind == "faculty_disabled"


# ------------------------------------------------------- the seeded rows --


@requires_db
def test_a_seeded_spell_carries_a_date_that_is_not_the_migration_run(client):
    """The migration's one promise about `from_at`.

    Every pair standing when this table arrived was seated by a writer that kept
    no timestamp, so the date is unknowable. `now()` would have told every
    reader the whole roster was seated on deploy day; the seed takes the
    ACCOUNT's creation time, which is the earliest moment the pair could have
    existed. A NULL is legal and means "since before this was recorded" — what
    is refused is a confident wrong date.
    """
    with SessionLocal() as db:
        seeded = db.scalars(
            select(MentorAssignment).where(
                MentorAssignment.to_at.is_(None), MentorAssignment.by_user_id.is_(None)
            )
        ).all()
        if not seeded:
            pytest.skip("no seeded spell on this database")
        today = datetime.now(timezone.utc).date()
        for row in seeded:
            if row.from_at is None:
                continue  # "since before this was recorded" — the honest NULL
            account_created = db.scalar(
                select(User.created_at)
                .join(Student, Student.user_id == User.id)
                .where(Student.id == row.student_id)
            )
            assert row.from_at.date() != today or (
                account_created is not None and account_created.date() == today
            ), "a seeded spell claims the student was seated on the migration's run date"


@requires_db
def test_the_window_lapses_on_its_own_after_ninety_days(client, make_user, swept):
    """IT ENDS BY ITSELF, WHICH IS THE PROPERTY THE GRANT WAS CHOSEN FOR.

    Nothing runs to close a handover: `_live_grant_clauses` compares
    `expires_at` against `now()` IN SQL on every read, so the door shuts on the
    date whether or not anybody remembers it, whether or not a sweeper ran, and
    whether or not the process that minted it is still alive. A window enforced
    by a job is a window that stays open when the job fails.

    The grant is aged rather than the clock moved, because moving the clock
    would prove something about this test's patching and nothing about the
    query the API runs.
    """
    student_ids, faculty_ids = swept
    admin = make_user("ma-exp-adm", Role.ADMIN)
    faculty = make_user("ma-exp-fac", Role.MENTOR)
    successor = make_user("ma-exp-fac2", Role.MENTOR)
    stu = make_user("ma-exp-stu", Role.STUDENT)
    sid = _student_id(stu.user_id)
    student_ids.append(sid)
    faculty_ids += [faculty.user_id, successor.user_id]
    assign = f"{ADMIN_API}/students/{sid}/mentor"
    notes = f"/api/mentor/students/{sid}/notes"

    assert client.post(assign, headers=admin.headers, json={"mentor_user_id": faculty.user_id, **REASON}).status_code == 204
    assert client.post(assign, headers=admin.headers, json={"mentor_user_id": successor.user_id, **REASON}).status_code == 204
    assert client.get(notes, headers=faculty.headers).status_code == 200

    # Ninety days and a minute later.
    with SessionLocal() as db:
        grant = db.scalar(
            select(CapabilityGrant).where(CapabilityGrant.subject_user_id == faculty.user_id)
        )
        assert grant is not None
        grant.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()

    assert client.get(notes, headers=faculty.headers).status_code == 403, (
        "an expired handover still opened the mentee log — the window must be "
        "filtered in SQL by _live_grant_clauses, not by a sweeper"
    )
    # BOTH LAYERS, SEPARATELY. The 403 above is the capability door closing,
    # which is the first thing a lapsed window shuts; rule 2's branch is asked
    # of `holds_handover_for` directly, because a mentor who is handed a NEW
    # mentee tomorrow holds `mentor.mentees` again on their own account and
    # would then reach this student through a branch that forgot to expire.
    with SessionLocal() as db:
        from app.mentor_history import holds_handover_for

        assert holds_handover_for(db, faculty.user_id, sid) is False, (
            "holds_handover_for ignored expires_at — liveness must be asked of "
            "_live_grant_clauses, the one definition of what makes a grant count"
        )
    # And the spell is still there. The window lapsing does not erase the record
    # that the handover happened: "who mentored this student, and when" outlives
    # "who may read them today".
    assert len(_spells(sid)) == 2


@requires_db
def test_a_lingering_mentor_row_is_not_itself_a_handover(client, make_user, swept):
    """HAZARD 5 FROM THE MAP, WHICH IS A TRAP IN THE SESSION AND NOT IN THE DATA.

    `Mentor` rows are never deleted and `mentorId` is baked into the session
    cookie, so a faculty member who has handed a student over still presents a
    claim naming a live `Mentor` row. Keyed on "this session has a mentorId" —
    or on "this account has a group" — the window would be open for every
    faculty member who has ever had one mentee, for every student they ever
    mentored, and after the grant was revoked or lapsed.
    `holds_handover_for` keys on (user, student) in `capability_grants` instead.

    THE SET-UP IS THE ONE THAT ACTUALLY REACHES THE BRANCH. This faculty member
    keeps a SECOND mentee, so `mentor_functions_for` still derives
    `mentor.mentees` and `require_capability` lets them through the door — which
    is the only way `assert_student_scope` is reached at all. The handed-over
    student's grant is then removed and nothing else is touched: the group row
    stands, the session claim stands, the closed spell stands. The read must be
    refused.
    """
    student_ids, faculty_ids = swept
    admin = make_user("ma-ling-adm", Role.ADMIN)
    faculty = make_user("ma-ling-fac", Role.MENTOR)
    successor = make_user("ma-ling-fac2", Role.MENTOR)
    handed_over = make_user("ma-ling-stu", Role.STUDENT)
    kept = make_user("ma-ling-stu2", Role.STUDENT)
    gone_sid = _student_id(handed_over.user_id)
    kept_sid = _student_id(kept.user_id)
    student_ids += [gone_sid, kept_sid]
    faculty_ids += [faculty.user_id, successor.user_id]
    notes = f"/api/mentor/students/{gone_sid}/notes"

    for sid in (gone_sid, kept_sid):
        assert client.post(
            f"{ADMIN_API}/students/{sid}/mentor",
            headers=admin.headers, json={"mentor_user_id": faculty.user_id, **REASON},
        ).status_code == 204
    assert client.post(
        f"{ADMIN_API}/students/{gone_sid}/mentor",
        headers=admin.headers, json={"mentor_user_id": successor.user_id, **REASON},
    ).status_code == 204
    # With the handover grant, the read is open.
    assert client.get(notes, headers=faculty.headers).status_code == 200

    with SessionLocal() as db:
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.subject_user_id == faculty.user_id))
        db.commit()
        # The group row survives — it is never deleted — and it is not a key.
        assert _group_of(faculty.user_id) is not None
        # The account still mentors somebody, so it still holds the capability:
        # this refusal has to come from rule 2, not from the door.
        from app.mentor_functions import mentee_count

        assert mentee_count(db, faculty.user_id) == 1
        # And the spell that closed is still on the record.
        assert db.scalar(
            select(MentorAssignment.id).where(
                MentorAssignment.student_id == gone_sid,
                MentorAssignment.to_at.is_not(None),
            )
        ) is not None

    assert client.get(notes, headers=faculty.headers).status_code == 404, (
        "a lingering Mentor row and a session claim opened the window by "
        "themselves — the grant pinned to (user, student) is the only key"
    )
    # Their own mentee is unaffected.
    assert client.get(f"/api/mentor/students/{kept_sid}/notes", headers=faculty.headers).status_code == 200


# ------------------------------------------------------------- the reads --


@requires_db
def test_the_history_is_read_through_the_same_fences_the_assignment_takes(
    client, make_user, swept
):
    """`GET /admin/students/{id}/mentor-history` — B9.1's read.

    Reading who has mentored somebody is reading about that student, so it takes
    the fence the write takes: `admin.mentors`, and 404 for a student who does
    not exist. A STUDENT is refused outright — this endpoint names other
    people's faculty members and the reasons they were moved.
    """
    student_ids, faculty_ids = swept
    admin = make_user("ma-read-adm", Role.ADMIN)
    faculty = make_user("ma-read-fac", Role.MENTOR)
    stu = make_user("ma-read-stu", Role.STUDENT)
    sid = _student_id(stu.user_id)
    student_ids.append(sid)
    faculty_ids.append(faculty.user_id)
    history = f"{ADMIN_API}/students/{sid}/mentor-history"

    # Nothing recorded: an EMPTY LIST, not a 404. "No assignment recorded" is an
    # answer; the 404 is reserved for a student who is not there.
    assert client.get(history, headers=admin.headers).json() == []
    assert client.get(f"{ADMIN_API}/students/no-such-student/mentor-history", headers=admin.headers).status_code == 404
    assert client.get(history, headers=stu.headers).status_code == 403
    # A faculty account holds neither `admin.mentors` nor this screen.
    assert client.get(history, headers=faculty.headers).status_code == 403

    assert client.post(
        f"{ADMIN_API}/students/{sid}/mentor",
        headers=admin.headers, json={"mentor_user_id": faculty.user_id, **REASON},
    ).status_code == 204
    rows = client.get(history, headers=admin.headers).json()
    assert len(rows) == 1
    assert rows[0]["mentor_name"] is not None and rows[0]["to_at"] is None
    assert rows[0]["by_name"] is not None and rows[0]["reason"] == REASON["reason"]


@requires_db
def test_student_360_draws_the_spells_and_says_which_empty_it_means(
    client, make_user, swept
):
    """B4.5's mentor panel, which 4a left `available: false` for this task.

    THE TWO EMPTIES ARE DIFFERENT AND THE PANEL SAYS WHICH. Nothing recorded and
    nobody assigned is a student waiting to be seated; a timeline saying
    "no mentor history" over a card naming their current faculty member is the
    screen contradicting itself. `available` therefore means "is there anything
    recorded for THIS student", not "does this deployment have the feature".
    """
    student_ids, faculty_ids = swept
    admin = make_user("ma-360-adm", Role.ADMIN)
    faculty = make_user("ma-360-fac", Role.MENTOR)
    stu = make_user("ma-360-stu", Role.STUDENT)
    sid = _student_id(stu.user_id)
    student_ids.append(sid)
    faculty_ids.append(faculty.user_id)
    three_sixty = f"{ADMIN_API}/students/{sid}/360"

    r = client.get(three_sixty, headers=admin.headers)
    assert r.status_code == 200, r.text
    panel = r.json()["mentor_history"]
    assert panel["available"] is False and panel["entries"] == []
    assert "has ever been assigned" in panel["note"], panel["note"]

    assert client.post(
        f"{ADMIN_API}/students/{sid}/mentor",
        headers=admin.headers, json={"mentor_user_id": faculty.user_id, **REASON},
    ).status_code == 204

    body = client.get(three_sixty, headers=admin.headers).json()
    panel = body["mentor_history"]
    assert panel["available"] is True
    assert panel["note"] == ""
    assert len(panel["entries"]) == 1
    spell = panel["entries"][0]
    # The same shape the history card reads — one definition, two screens.
    assert spell["to_at"] is None and spell["kind"] == "assign"
    assert spell["reason"] == REASON["reason"]
    # And the present-tense card beside it still answers separately.
    assert body["current_mentor"]["mentor_user_id"] == faculty.user_id


# ------------------------------------------------- one open spell, enforced --


@requires_db
def test_a_student_cannot_hold_two_open_spells_at_once(client, make_user, swept):
    """The open-row invariant is the DATABASE's now, not the writer's alone
    (`uq_mentor_assignment_one_open_spell`, migration e1c4b7a209d6).

    The module docstring says `to_at IS NULL` is "what makes 'one open row per
    current pair' a statement a query can check rather than a convention a
    writer has to remember". A query could check it and nothing did, so a second
    open row inserted cleanly. `record_mentor_change` is a read-then-write —
    read the open row, close it, open the next — so two concurrent assignments
    for one student both see no open row and both insert, and the console posts
    one request per ticked student on a batch save.

    The insert below goes around the writer ON PURPOSE. Driving this through the
    API cannot reach it: the writer closes the first spell before opening the
    second, which is exactly the behaviour that is correct and exactly why the
    race is invisible from the endpoint. What is under test is the constraint,
    so the test states the race directly.

    The second half matters as much as the first: the index is PARTIAL, so a
    CLOSED spell beside an open one is still legal. A plain
    `UNIQUE (student_id, to_at)` would have been the obvious thing to reach for
    and would have been wrong twice over — it permits two NULLs (Postgres treats
    them as distinct) and it would forbid the ordinary history this table exists
    to keep.
    """
    from sqlalchemy.exc import IntegrityError

    student_ids, faculty_ids = swept
    admin = make_user("ma-dup-adm", Role.ADMIN)
    first = make_user("ma-dup-fac1", Role.MENTOR)
    second = make_user("ma-dup-fac2", Role.MENTOR)
    stu = make_user("ma-dup-stu", Role.STUDENT)
    sid = _student_id(stu.user_id)
    student_ids.append(sid)
    faculty_ids += [first.user_id, second.user_id]
    assign = f"{ADMIN_API}/students/{sid}/mentor"

    assert client.post(
        assign, headers=admin.headers, json={"mentor_user_id": first.user_id, **REASON}
    ).status_code == 204
    # A reassignment closes one spell and opens another: two rows, one open.
    assert client.post(
        assign, headers=admin.headers, json={"mentor_user_id": second.user_id, **REASON}
    ).status_code == 204
    rows = _spells(sid)
    assert len(rows) == 2, "the closed spell must survive — this is history, not a pointer"
    assert sum(r.to_at is None for r in rows) == 1

    # The race, stated directly. Without the partial unique index this commits.
    with SessionLocal() as db:
        db.add(
            MentorAssignment(
                student_id=sid,
                mentor_id=_group_of(first.user_id),
                from_at=datetime.now(timezone.utc),
                to_at=None,
                kind="assign",
            )
        )
        with pytest.raises(IntegrityError) as caught:
            db.commit()
        db.rollback()
    assert "uq_mentor_assignment_one_open_spell" in str(caught.value)

    # Untouched by the refusal, and still exactly one open spell.
    rows = _spells(sid)
    assert len(rows) == 2
    assert sum(r.to_at is None for r in rows) == 1
