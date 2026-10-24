"""07 §5 — what must still be true after Phase 3, for people who were already here.

Phase 3 changed how access is decided, in four places at once: the MENTOR role
baseline shrank to two keys (B2.3), eleven list endpoints started narrowing
themselves to the caller's reach (B1.4), grants gained a rung of the spine to
hang on (B1.2), and provisioning gained a domain fence (B1.1). Every one of
those is a change to who can see what, made on a deployment that already has
faculty signing in, grants in the table and students on the roster.

The acceptance checklist calls these the COMPATIBILITY GUARDRAILS, and they are
a different kind of test from the ones beside them. `test_grant_scope.py` proves
the new rule works. These prove the OLD behaviour survived it — which is the
half that nobody notices is broken until a faculty member cannot open a screen
they opened yesterday, and by then the deploy is three days old and the change
that did it is one of forty.

Each test names the guardrail it holds. Guardrails whose subject is a Phase 4
task (batch promotion, graduation, the daily interview cap, summary backfill)
are NOT here and are not silently missing: there is a written note at the foot
of this module saying which, and why they cannot be pinned yet.

WRITTEN AGAINST THE REAL SEED, because that is the population these guarantees
are about. A guardrail proven only against rows the test itself invented is a
guardrail about the test.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select

from conftest import TEST_PASSWORD, requires_db

# Fixtures by name, the way test_admin_promotion and test_admin_students already
# borrow them: the College -> Department -> Batch chain is built THROUGH THE
# API, so a guardrail about batch operations runs against a batch the console
# could actually have produced.
from test_admin_institution import (  # noqa: F401 - fixtures by name
    _code,
    chain,
    director,
    tracker,
)
from test_admin_promotion import (  # noqa: F401 - fixtures and helpers by name
    _seeded_student,
    swept,
)

from app.db import SessionLocal
from app.governance import ROLE_BASELINE, capabilities_for, granted_reaches
from app.mentor_functions import MENTOR_FUNCTIONS, mentee_count, mentor_functions_for
from app.models.academics import SemesterResult
from app.models.governance import CapabilityGrant, ScopeLevel, SubjectKind
from app.models.institution import Department
from app.models.user import Mentor, Role, Student, User
from app.policies import scope_filter
from app.seed_roster import SSO_ONLY_PASSWORD_HASH

ADMIN = ("admin@bgscet.ac.in", "admin123")
MENTOR = ("mentor@bgscet.ac.in", "mentor123")
STUDENT = ("student@bgscet.ac.in", "student123")


@pytest.fixture
def unmentored_faculty():
    """A MENTOR-role account with no `Mentor` row and no students.

    THE POPULATION B2.3 IS ABOUT. AGENTS.md has said "a faculty account is not a
    mentor by existing" since the Main Admin console landed, and every one of
    the four endpoints already behaved that way; what B2.3 changed is that the
    CAPABILITY now says so too. This account is how the two are compared.
    """
    tag = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            email=f"unmentored-{tag}@bgscet.ac.in",
            name="Unmentored Faculty",
            role=Role.MENTOR,
            password_hash=SSO_ONLY_PASSWORD_HASH,
        )
        db.add(user)
        db.commit()
        uid = user.id
    yield uid
    with SessionLocal() as db:
        # Signing this account in — which two of these tests do — writes a
        # `login_days` row and, since B15, a `login_events` one, and both hold a
        # real FK to `users` with no ON DELETE. Deleting the user without them
        # is a ForeignKeyViolation that fails the NEXT test to run, in a
        # teardown, which is the least legible place a failure can appear.
        from app.models.account_events import LoginEvent
        from app.models.user import LoginDay

        db.execute(delete(LoginEvent).where(LoginEvent.user_id == uid))
        db.execute(delete(LoginDay).where(LoginDay.user_id == uid))
        db.execute(delete(CapabilityGrant).where(CapabilityGrant.subject_user_id == uid))
        db.execute(delete(User).where(User.id == uid))
        db.commit()


# ----------------------------------------------------------- the role itself --


@requires_db
def test_existing_faculty_still_sign_in_and_their_role_is_unchanged(client, login):
    """GUARDRAIL: "Existing faculty sign in; `users.role` unchanged."

    B2.3 shrank what MENTOR *means*, and the failure mode worth naming is the
    one where somebody "simplifies" that by changing the role of the accounts
    instead. A role migration is invisible in a diff of governance.py and
    catastrophic: every `require_mentor` gate in the product reads the role.
    """
    headers = login(*MENTOR)
    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "MENTOR"

    with SessionLocal() as db:
        row = db.scalar(select(User).where(User.email == MENTOR[0]))
        assert row is not None and row.role == Role.MENTOR


@requires_db
def test_faculty_with_mentees_keep_the_four_group_functions(client, login):
    """GUARDRAIL: "Faculty with mentees keep Mentee Log / Verifications / Leave queue."

    The seeded mentor has one mentee, so they hold the four. Asserted through
    `/api/auth/me`, which is the list the client actually renders its nav from —
    not through `mentor_functions_for` alone, which would prove the helper works
    and not that the session sees it.
    """
    headers = login(*MENTOR)
    capabilities = set(client.get("/api/auth/me", headers=headers).json()["capabilities"])

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == MENTOR[0]))
        assert mentee_count(db, user.id) > 0, "the seed's mentor lost their mentee"

    assert MENTOR_FUNCTIONS <= capabilities, (
        "a faculty member with mentees lost a function they held before Phase 3"
    )
    # And the personal two, which never depended on the group.
    assert {"mentor.agent", "mentor.upskilling"} <= capabilities


@requires_db
def test_faculty_without_mentees_keep_their_own_things_and_nothing_else(unmentored_faculty):
    """GUARDRAIL: "Faculty without see Notebook, Leave (own), Signature, Agent."

    The four GROUP functions go; the two PERSONAL ones stay. Those two are in
    the baseline deliberately — the assistant and one's own certificate shelf
    belong to the person, and taking them away between assignments would be
    removing their own things.

    Signature and one's own leave are NOT capabilities at all and never were:
    `/api/staff/signature` and `POST /api/leaves` gate on the ROLE. That is why
    this test asserts the capability set is exactly the baseline rather than
    looking for keys named after those screens — a reader who expects to find
    `mentor.signature` here should find this sentence instead.
    """
    with SessionLocal() as db:
        session = {"role": "MENTOR", "userId": unmentored_faculty}
        assert mentor_functions_for(db, unmentored_faculty) == frozenset()
        assert capabilities_for(db, session) == ROLE_BASELINE["MENTOR"]
        assert capabilities_for(db, session) == frozenset(
            {"mentor.agent", "mentor.upskilling"}
        )


@requires_db
def test_the_capability_and_the_endpoint_agree_about_an_unmentored_faculty(
    client, login, unmentored_faculty
):
    """The two halves that a role removal is famous for splitting.

    `test_no_director_privilege.py` exists because the DIRECTOR removal reached
    the role gates and not the capability baseline, and a session refused by
    every `require_*` was admitted by every `require_capability`. B2.3 is the
    same shape of change in the other direction, so the same pairing is checked:
    the capability list and the endpoint must give the SAME answer.
    """
    with SessionLocal() as db:
        user = db.get(User, unmentored_faculty)
        email, password = user.email, "compat-guardrail-pw-2026"
        from app.security import hash_password

        user.password_hash = hash_password(password)
        db.commit()

    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    headers = {"Cookie": r.headers.get("set-cookie", "")}
    client.cookies.clear()

    capabilities = set(client.get("/api/auth/me", headers=headers).json()["capabilities"])
    assert "mentor.mentees" not in capabilities

    # ...and the endpoint says the same thing. A mentor with no group sees
    # NOBODY — rule 2's words — which is an empty list, not a refusal, because
    # they are staff and the question is legitimate.
    mentees = client.get("/api/mentor/mentees", headers=headers)
    assert mentees.status_code in (200, 403), mentees.text
    if mentees.status_code == 200:
        assert mentees.json() == [], (
            "the capability says no mentees and the endpoint returned some"
        )


# ------------------------------------------------------------------- grants --


@requires_db
def test_a_grant_made_before_b1_2_still_opens_its_screen(unmentored_faculty):
    """GUARDRAIL: "Every pre-existing grant still opens its screen (scope = BGSCET)."

    Every row written before B1.2 has NULL in both scope columns, because there
    was nothing to put there. `granted_reaches` must read that pair as
    programme-wide and `scope_filter` must answer `everything` — if either ever
    reads NULL as "reaches nothing", every grant in the product silently stops
    working on deploy, and the screen it opens goes empty rather than refusing,
    which is the version nobody reports as a permissions bug.

    Written as a RAW ROW with NULLs rather than through the endpoint, because
    the endpoint is the new code and the row is what is already in the database.
    """
    with SessionLocal() as db:
        grant = CapabilityGrant(
            capability="admin.analytics",
            subject_kind=SubjectKind.USER,
            subject_user_id=unmentored_faculty,
            scope_level=None,
            scope_id=None,
            reason="A grant made before scope targets existed at all.",
        )
        db.add(grant)
        db.commit()

        assert granted_reaches(db, unmentored_faculty, "admin.analytics") == [(None, None)]
        reach = scope_filter(
            db, {"role": "MENTOR", "userId": unmentored_faculty}, "admin.analytics"
        )
        assert reach.everything, "a pre-existing grant stopped reaching the programme"
        assert not reach.nothing


@requires_db
def test_the_main_admin_is_never_narrowed_by_scoping(client, login):
    """GUARDRAIL, and the one that covers several others at once.

    "pending leave rows are decidable by the Main Admin immediately" and
    "SWOC entries show their author without re-entry" are both really this: the
    office account holds its keys through ROLE_BASELINE, and a baseline
    capability is UNSCOPED by design (`require_capability` says why). So B1.4
    narrowed nothing for the one account that must see everything.

    Proven on the queues B1.4 actually rewrote, through HTTP, because that is
    where a scope projection that defaulted the wrong way would show up.
    """
    headers = login(*ADMIN)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN[0]))
        session = {"role": "ADMIN", "userId": admin.id}
        for key in ("admin.analytics", "admin.exports", "admin.swoc"):
            if key in ROLE_BASELINE["ADMIN"]:
                assert scope_filter(db, session, key).everything, (
                    f"the Main Admin was narrowed on {key}"
                )

    for path in ("/api/leaves/pending", "/api/admin/swoc"):
        r = client.get(path, headers=headers)
        assert r.status_code == 200, f"{path}: {r.text}"


@requires_db
def test_swoc_entries_still_carry_their_author(client, login):
    """GUARDRAIL: "SWOC entries show their author without re-entry."

    B1.4 added a scope projection to this list. The author is a JOIN onto
    `swoc_entries.author_user_id`, and a rewritten query that dropped it would
    leave every entry reading as written by nobody — which on a screen whose
    whole purpose is a named judgement about a student is worse than an error.

    WRITTEN HERE RATHER THAN READ FROM THE SEED, and that distinction cost a
    false failure before it was understood: `app/seed.py` writes its four SWOC
    entries with NO `author_user_id`, so a seeded entry answers `author: null`
    CORRECTLY — the schema's own comment allows null for an account that is
    gone, and a demo row nobody wrote is the same shape of fact. Asserting
    against those rows tests the seed, not the join. So this test creates one
    through the real endpoint, as a real person, and reads it back.
    """
    from app.models.swoc import SwocEntry

    headers = login(*ADMIN)
    listing = client.get("/api/admin/swoc", headers=headers)
    assert listing.status_code == 200, listing.text
    students = listing.json()
    if not students:
        pytest.skip("no students on this deployment to write a SWOC entry about")
    student_id = students[0]["student_id"]

    created = client.post(
        f"/api/admin/swoc/{student_id}",
        headers=headers,
        json={
            "kind": "STRENGTH",
            "text": "Compatibility guardrail: this entry must name its author.",
            "weight": 3,
        },
    )
    assert created.status_code in (200, 201), created.text
    entry_id = created.json().get("id")
    assert entry_id

    try:
        again = client.get("/api/admin/swoc", headers=headers)
        assert again.status_code == 200, again.text
        mine = [
            e
            for row in again.json()
            for e in (row.get("entries") or [])
            if e.get("id") == entry_id
        ]
        assert mine, "the entry that was just written is not in the list"
        assert mine[0].get("author"), (
            "an entry written by a named admin came back with no author — "
            "the join was dropped"
        )
    finally:
        with SessionLocal() as db:
            db.execute(delete(SwocEntry).where(SwocEntry.id == entry_id))
            db.commit()


# ------------------------------------------------------------- the student --


@requires_db
def test_student_routes_are_untouched_by_scoping(client, login):
    """GUARDRAIL: "Student routes and APIs unchanged by scoping."

    B1.4 edited eight routers. `policies.scope_filter` answers a question about
    a STAFF member's reach and has no meaning for a student reading their own
    record — a student's fence is rule 2 and their own session, which is a
    different and stricter gate. If a scope projection ever leaks onto one of
    these, a student starts seeing an empty dashboard with a 200 beside it.
    """
    headers = login(*STUDENT)
    for path in (
        "/api/student/profile",
        "/api/student/dashboard",
        "/api/student/jobs",
        "/api/student/programme",
        "/api/student/ledger",
    ):
        r = client.get(path, headers=headers)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:200]}"
        assert "X-Reep-Scope" not in r.headers, (
            f"{path} grew a staff scope header; a student has no scope"
        )


@requires_db
def test_a_student_is_still_refused_every_console_surface(client, login):
    """The fence that all of Phase 3's rewriting had to leave standing.

    Named separately from the capability tests because it is a different claim:
    not "a student holds no capability" but "no Phase 3 endpoint answers one".
    """
    headers = login(*STUDENT)
    for path in (
        "/api/admin/audit",
        "/api/admin/faculty",
        "/api/admin/governance/grants",
        "/api/admin/exports/history",
    ):
        r = client.get(path, headers=headers)
        assert r.status_code in (401, 403), f"{path} answered {r.status_code} to a student"


# -------------------------------------------------------- faculty lifecycle --


@requires_db
def test_disabling_one_account_leaves_every_other_untouched(client, login, unmentored_faculty):
    """GUARDRAIL: "Disabled faculty only: active accounts untouched."

    The obvious failure is an UPDATE with no WHERE. The quieter one is a
    `token_version` bump that reaches further than the row it was about — which
    under one-device-at-a-time signs a colleague out mid-task with no
    explanation, and reads to them as the app logging them out at random.
    """
    headers = login(*ADMIN)
    with SessionLocal() as db:
        others = {
            u.id: (u.token_version or 0, u.disabled_at)
            for u in db.scalars(
                select(User).where(User.role.in_([Role.MENTOR, Role.ADMIN]))
            ).all()
            if u.id != unmentored_faculty
        }
    assert others, "no other staff accounts to compare against"

    r = client.post(
        f"/api/admin/users/{unmentored_faculty}/disable",
        headers=headers,
        json={"reason": "Compatibility guardrail: disabling exactly one account."},
    )
    assert r.status_code == 200, r.text
    assert r.json()["disabled"] is True

    with SessionLocal() as db:
        for uid, (version, disabled_at) in others.items():
            now = db.get(User, uid)
            assert (now.token_version or 0) == version, (
                f"disabling one account bumped {now.email}'s token version"
            )
            assert now.disabled_at == disabled_at, (
                f"disabling one account changed {now.email}'s disabled state"
            )


@requires_db
def test_an_activation_link_is_not_revoked_by_somebody_elses(client, login, unmentored_faculty):
    """GUARDRAIL: "Activation links already sent stay valid to expiry."

    Issuing a link SUPERSEDES every older live one — for that user. The rule is
    per account, and a superseding query written without its user predicate
    would invalidate every outstanding link in the product every time an admin
    minted one, which presents as "the link you were sent does not work" for
    people who were never mentioned.
    """
    headers = login(*ADMIN)
    tag = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        other = User(
            email=f"bystander-{tag}@bgscet.ac.in",
            name="Bystander Faculty",
            role=Role.MENTOR,
            password_hash=SSO_ONLY_PASSWORD_HASH,
        )
        db.add(other)
        db.commit()
        other_id = other.id

    try:
        first = client.post(f"/api/admin/users/{other_id}/activation-link", headers=headers)
        assert first.status_code == 200, first.text
        from app.models.auth_token import AuthToken

        with SessionLocal() as db:
            live_before = db.scalar(
                select(AuthToken).where(
                    AuthToken.user_id == other_id, AuthToken.consumed_at.is_(None)
                )
            )
            assert live_before is not None
            token_id = live_before.id

        # A link minted for a DIFFERENT account.
        second = client.post(
            f"/api/admin/users/{unmentored_faculty}/activation-link", headers=headers
        )
        assert second.status_code in (200, 422), second.text

        with SessionLocal() as db:
            still = db.get(AuthToken, token_id)
            assert still is not None, "the bystander's link row was deleted"
            assert still.consumed_at is None, (
                "minting one account's activation link consumed another's"
            )
    finally:
        with SessionLocal() as db:
            from app.models.auth_token import AuthToken

            db.execute(delete(AuthToken).where(AuthToken.user_id == other_id))
            db.execute(delete(User).where(User.id == other_id))
            db.commit()


# --------------------------------------------------------- the office's data --


@requires_db
def test_placement_criteria_defaults_still_answer(client, login):
    """GUARDRAIL: "Criteria defaults remain the fallback; screens say 'no import
    yet' instead of zeros."

    The API half is what is pinnable here: the endpoint either serves the active
    criteria or answers 404 saying none is set. What it must NEVER do is return
    a row of zeros, because a zero threshold reads on the analytics screen as
    "every student qualifies" — the opposite of "nobody has set this yet".
    """
    r = client.get("/api/admin/criteria", headers=login(*ADMIN))
    assert r.status_code in (200, 404), r.text
    if r.status_code == 200:
        body = r.json()
        assert not all(
            (body.get(k) in (0, 0.0, None))
            for k in ("min_cgpa", "min_attendance_pct", "min_reep_completion_pct")
        ), "the criteria endpoint served a row of zeros, which reads as 'everyone qualifies'"



# ------------------------------------------------ Phase 4's own guardrails --
#
# Everything below this line was listed at the foot of this module as "cannot be
# pinned yet — its subject is Phase 4". Phase 4 landed (4a semesters and
# graduation, 4b imports and analytics, 4c interview policy and tracks, 4d
# mentor history and SWOC, 4e leave, 4f registrations), so the notes became
# tests. The two that are still notes are still at the foot, with their reasons.


@requires_db
def test_promotion_and_graduation_keep_the_record_they_move(
    client, login, make_user, chain, tracker, swept
):
    """GUARDRAIL: "Records after promotion keep their semester numbers;
    graduation keeps login and USN."

    Two halves of one worry — that a batch operation which walks a student
    FORWARD also rewrites what is already behind them.

    The first half has a deeper test next door
    (`test_admin_promotion.py::test_promote_moves_the_semester_and_rewrites_nothing`
    snapshots all four semester-bearing tables); what is here is the same claim
    at compatibility altitude, on a result row that existed before the
    promotion, because that is the row a student looks at.

    The SECOND half is not held anywhere else and is the reason this test is
    written rather than cross-referenced. Graduation flips `users.role` and
    `students.status` and bumps `token_version`, and it would be entirely
    natural for it to also clear the USN — the student is no longer on the
    roster. It must not: the USN is how every mark, every attendance record and
    every uploaded marksheet in this database is identified with the person, and
    an alumnus asking for a transcript in 2031 is asking about that string. The
    account must also still be able to SIGN IN afterwards, through the ordinary
    front door, or "graduation" is indistinguishable from deletion.
    """
    h = chain["headers"]
    cid = chain["cohort"]["id"]
    account, sid = _seeded_student(client, make_user, swept, chain, "compat", semester=2)
    swept["emails"].append(account.email)

    usn = f"1MP25COMPAT{uuid.uuid4().hex[:4].upper()}"
    with SessionLocal() as db:
        student = db.get(Student, sid)
        student.usn = usn
        db.add(SemesterResult(student_id=sid, semester=2, sgpa=8.1, cgpa=7.9))
        db.commit()

    promoted = client.post(
        f"/api/admin/cohorts/{cid}/promote", headers=h, json={"effective_on": "2026-08-01"}
    )
    assert promoted.status_code == 200, promoted.text

    with SessionLocal() as db:
        assert db.get(Student, sid).current_semester == 3
        result = db.scalar(select(SemesterResult).where(SemesterResult.student_id == sid))
        assert result.semester == 2, (
            "the promotion walked the RESULT forward too, which turns "
            "'scored 8.1 in semester 2' into a claim about semester 3"
        )
        assert result.sgpa == pytest.approx(8.1)

    graduated = client.post(
        f"/api/admin/cohorts/{cid}/graduate",
        headers=h,
        json={"effective_on": "2026-07-31", "reason": "Course complete."},
    )
    assert graduated.status_code == 200, graduated.text

    with SessionLocal() as db:
        student = db.get(Student, sid)
        assert student.usn == usn, "graduation cleared the USN the whole record hangs on"
        assert db.scalar(
            select(SemesterResult).where(SemesterResult.student_id == sid)
        ).semester == 2, "graduation rewrote a semester number"

    # And the account still opens. `token_version` was bumped, so the cookie
    # they were holding is dead — that is the single-device rule doing its job,
    # not a lockout — and a fresh sign-in is what proves the difference.
    client.cookies.clear()
    fresh = login(account.email, TEST_PASSWORD)
    me = client.get("/api/auth/me", headers=fresh)
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "ALUMNI"


@requires_db
def test_the_four_original_track_codes_still_open_an_interview():
    """GUARDRAIL: "Old interview track codes still open sessions."

    B5.1 moved the Specialization Matrix into `interview_tracks`, a table an
    admin edits. The four codes a student's bookmarked URL and every deployed
    Angular bundle already carry — `hr`, `dm`, `ba`, `fa` — must keep resolving
    on a deployment where nobody has opened the new screen and the table is
    therefore EMPTY. That is what `SPECIALIZATIONS` is still doing in
    `interview_matrix.py`: it is the fallback when there is no row, not a
    leftover.

    A voice is asserted alongside, because an unknown one is not a degraded
    interview — it is a Bedrock `ValidationException` at the handshake, i.e. an
    interview that never starts, reported by the student as "it just closed".
    """
    from app.interview_matrix import KNOWN_NOVA_VOICES
    from app.interview_tracks import resolve_specialization

    for code in ("hr", "dm", "ba", "fa"):
        spec = resolve_specialization(code)
        assert spec is not None, f"the bookmarked code {code!r} no longer opens an interview"
        assert spec.persona and not spec.persona.endswith("."), (
            "persona is a NOUN PHRASE — build_instructions embeds it as "
            "'you are {persona}' and a sentence there is broken grammar nothing catches"
        )
        assert spec.nova_voice in KNOWN_NOVA_VOICES, spec.nova_voice
        # Case and whitespace are what a hand-typed URL actually carries.
        assert resolve_specialization(f"  {code.upper()} ") is not None

    # The two NOTs, which are the same as they were before the table existed.
    assert resolve_specialization(None) is None, "no code is still the generic interview"
    assert resolve_specialization("not-a-track") is None, "an unknown key is still 4010"


@requires_db
def test_a_consent_row_stays_live_until_the_version_bumps(client, make_user, monkeypatch):
    """GUARDRAIL: "Existing consent rows still valid until the version bumps."

    B6.1 changed what a consent row MEANS — the two storage scopes are now
    copied from the college's policy rather than ticked by the student — without
    changing the version string. A grant written by the old three-tick panel is
    therefore still this version's grant, and must still open an interview.

    What must ALSO be true is the other direction: the moment
    `INTERVIEW_CONSENT_VERSION` moves, that row stops being reported as consent
    — and is NOT revoked. Both halves are here because they look like the same
    fact and are opposite ones. Reporting a stale grant as live is consent to
    copy the student never read; stamping `revoked_at` on it to express that
    would rewrite the answer to "what was this student consented to when
    interview X ran", which is the one question `interview_sessions.consent_id`
    exists to keep answerable.
    """
    from app.config import settings
    from app.models.interview import InterviewConsent

    student = make_user("compat-consent")
    version = settings.interview_consent_version

    granted = client.post(
        "/api/interview/consent", headers=student.headers, json={"version": version}
    )
    assert granted.status_code == 201, granted.text

    live = client.get("/api/interview/consent", headers=student.headers).json()
    assert live["version"] == version
    assert live["consent"] is not None
    # The three booleans survive B6.1 — they are what the policy is copied ONTO.
    assert live["consent"]["scope_live_ai"] is True
    for key in ("scope_store_transcript", "scope_store_audio"):
        assert key in live["consent"], f"{key} disappeared from the grant"

    with SessionLocal() as db:
        row = db.scalar(
            select(InterviewConsent).where(InterviewConsent.user_id == student.user_id)
        )
        assert row is not None and row.revoked_at is None
        row_id = row.id

    # The terms change under the row.
    monkeypatch.setattr(settings, "interview_consent_version", f"{version}-next")
    after = client.get("/api/interview/consent", headers=student.headers).json()
    assert after["version"] == f"{version}-next"
    assert after["consent"] is None, "a grant for last term's copy was reported as consent"

    with SessionLocal() as db:
        assert db.get(InterviewConsent, row_id).revoked_at is None, (
            "bumping the version REVOKED the old grant, which rewrites what every "
            "interview pinned to it was consented under"
        )

    with SessionLocal() as db:
        db.execute(delete(InterviewConsent).where(InterviewConsent.user_id == student.user_id))
        db.commit()


@requires_db
def test_the_daily_cap_is_still_eight_and_a_reset_only_moves_the_window_forward(make_user):
    """GUARDRAIL: "Daily cap value 8; counted attempts never lost mid-day."

    The number first: 8 is what students were told and what a deployment with no
    `interview_policies` row still gets, because the ABSENCE of a policy row is
    the default and no row is seeded.

    The second half is the one with a mechanism behind it.
    `cap_window_start` is `GREATEST(now - 24 h, the latest reset)` — the reset is
    an EXTRA lower bound on the rolling window, never a replacement for it.
    Written the other way round an old reset row would widen the window back
    open and a student would find themselves counted against attempts from last
    week; written this way the bound can only ever move forward, which is the
    only direction "give this student their attempts back" is allowed to move.
    """
    from datetime import datetime as _dt

    from app.config import settings
    from app.interview_policy import cap_window_start, default_policy
    from app.models.interview import InterviewCapReset

    assert settings.interview_max_per_student_per_day == 8
    policy = default_policy()
    assert policy.configured is False and policy.daily_cap == 8

    student = make_user("compat-cap")
    with SessionLocal() as db:
        sid = db.scalar(select(Student.id).where(Student.user_id == student.user_id))

    now = _dt.now(timezone.utc)
    rolling = now - timedelta(hours=24)

    with SessionLocal() as db:
        assert cap_window_start(db, sid, now) == rolling, "no reset: the plain 24 h window"

    try:
        # A reset from LAST WEEK must not widen the window back open.
        with SessionLocal() as db:
            db.add(InterviewCapReset(
                student_id=sid, by_user_id=student.user_id,
                reason="Bedrock outage.", at=now - timedelta(days=7),
            ))
            db.commit()
        with SessionLocal() as db:
            assert cap_window_start(db, sid, now) == rolling, (
                "a stale reset widened the window and handed back attempts nobody granted"
            )

        # A reset an hour ago moves it FORWARD, and only forward.
        recent = now - timedelta(hours=1)
        with SessionLocal() as db:
            db.add(InterviewCapReset(
                student_id=sid, by_user_id=student.user_id,
                reason="Browser kept dropping.", at=recent,
            ))
            db.commit()
        with SessionLocal() as db:
            assert cap_window_start(db, sid, now) == recent
            assert cap_window_start(db, sid, now) > rolling
    finally:
        with SessionLocal() as db:
            db.execute(delete(InterviewCapReset).where(InterviewCapReset.student_id == sid))
            db.commit()


@requires_db
def test_the_summary_backfill_exists_and_its_dry_run_writes_nothing():
    """GUARDRAIL: "Interview summaries backfilled before the first purge after
    deploy."

    This is an OPERATIONAL guarantee and it has a clock on it. B6.2's summary is
    written at finalization from now on; every interview held before that code
    shipped has none, and `retention.purge_expired` is deleting those interviews
    on a rolling 180-day window every night. Each night the backfill does not
    run, some student's earliest attempts stop being recoverable — silently, and
    their progress trend simply starts later than their first interview did.

    So what is pinned is that the tool is there and that it is SAFE TO POINT AT
    PRODUCTION, which is the property that decides whether anybody runs it in
    the window where it still matters: a dry run reports and writes nothing.
    Deliberately NOT guarded on `ENV=prod` the way `app.seed` is — it mints no
    account and writes no student-authored text, only four integers copied from
    rows already in this database, and production is exactly where it belongs.
    """
    from app import backfill_interview_summaries as backfill
    from app.models.interview import InterviewScoreSummary

    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(InterviewScoreSummary))
        counts = backfill.backfill(db, dry_run=True)

    assert set(counts) == {
        "candidates", "written", "with_scores", "without_scores",
        "soft_deleted_rescued", "skipped_conflict",
    }
    assert counts["written"] == 0, "a dry run wrote rows"

    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(InterviewScoreSummary)) == before

    assert backfill.main(["--dry-run"]) == 0
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(InterviewScoreSummary)) == before


@requires_db
def test_a_leave_written_before_phase_4e_still_prints_its_paper(client, make_user, tmp_path, monkeypatch):
    """GUARDRAIL: "Old leave PDFs untouched."

    No leave PDF is ever STORED — `GET /api/leaves/{id}/paper.pdf` re-renders
    from the row every time — so "untouched" cannot mean an archive of files. It
    can only mean one thing: a request row written before 4e existed, whose new
    columns are all NULL, still renders on the college's own form.

    That is not a formality. 4e added leave kinds, credits, an alternate
    arrangement, balances and attachments, and the overlay's field coordinates
    are measured against `app/assets/leave_form_template.pdf`. A renderer that
    assumed any of the new values is present would raise on every historical
    row, and the first person to find out would be a faculty member printing a
    leave they took last term.
    """
    from app import document_store

    monkeypatch.setattr(document_store, "_store_dir", lambda: tmp_path)
    faculty = make_user("compat-leave", Role.MENTOR)

    # The PRE-4e request body, exactly: three fields and nothing else.
    created = client.post(
        "/api/leaves",
        headers=faculty.headers,
        json={
            "from_date": "2026-03-02",
            "to_date": "2026-03-03",
            "reason": "Family function at home.",
        },
    )
    assert created.status_code == 201, created.text
    leave = created.json()
    assert leave["leave_kind"] is None and leave["credit"] is None

    paper = client.get(f"/api/leaves/{leave['id']}/paper.pdf", headers=faculty.headers)
    assert paper.status_code == 200, paper.text
    assert paper.content.startswith(b"%PDF")
    assert paper.headers["content-type"].startswith("application/pdf")


# --------------------------------------------------------------------------- #
# WHAT IS DELIBERATELY NOT HERE
#
# Two of 07 §5's guardrails are still notes rather than tests, and they are
# written down for the reason the rest of this module exists: a checklist with
# quiet gaps is one somebody signs off as complete. The other four were notes
# here until Phase 4 landed and are now the tests above.
#
#   - "Registration auto-approval behaves as before on day one" — held, in
#     full, by `tests/test_college_domains.py`, which fences B1.1's
#     PROVISIONING from the other side and includes the fact that most needs
#     pinning (the deployment's `ENV` domain list is a FALLBACK and not a
#     floor). A copy here would be a second statement of one claim, and two
#     statements of a rule are how the rule ends up with two meanings.
#     4f changed the queue AROUND that rule rather than the rule — `?status=`,
#     paging, HOLD — and `POST /api/register`'s default listing is required to
#     answer byte-identically to what it answered before; that belongs with the
#     registration module's own tests, beside the endpoint.
#
#   - "Interview summaries backfilled before the first purge after deploy" is
#     pinned above only as far as a test CAN pin it: that the tool exists, that
#     its dry run writes nothing, and that nothing stops it running on
#     production. Whether somebody actually ran it inside the 180-day window is
#     not a property of this repository and no test can assert it. It is a
#     deploy step, and it is written down in
#     `app/backfill_interview_summaries.py`'s own docstring, which is where the
#     person doing the deploy will be.
# --------------------------------------------------------------------------- #
