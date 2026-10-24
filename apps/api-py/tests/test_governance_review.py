"""B2.4 and B2.5: a deputy's grant needs two people and the Main Admin's needs
one; every grant gets a review date and describes a person in a role.

Three properties, and every one of them fails SILENTLY if it regresses — the
console still renders, the endpoint still answers 200, and the damage is a
faculty member holding a student's records on one person's say-so, or holding
them still after they stopped being faculty.

  * A capability the catalogue flags `carries_pii`, granted by a DEPUTY, is
    written `pending_approval` and holds NOTHING until a different holder of
    `admin.governance` approves it. The approver may not be the granter, and
    that one refusal is the only part of a four-eyes rule that does anything.
    The MAIN ADMIN's grants are live the moment they are written, whatever they
    carry (2026-09-16): the office is the one authority on the deployment and
    does not wait for a deputy it appointed itself to agree.
  * Every reader of a grant honours the pending state. There are four of them
    and they are in three files; one forgetting is one door left open.
  * A grant describes a person IN A ROLE. Change the role and it stops counting
    — and `grant_access` also revokes it explicitly, so the office can see that
    it is gone rather than only that it does not work.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from conftest import requires_db
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.governance import (
    granted_capabilities,
    granted_reaches,
    require_capability,
)
from app.grant_access import grant as grant_access
from app.models.governance import (
    APPROVAL_ACTIVE,
    APPROVAL_PENDING,
    CAPABILITIES_BY_KEY,
    REVIEW_AFTER_DAYS,
    REVIEW_HORIZON_DAYS,
    AccessGroup,
    AccessGroupMember,
    CapabilityGrant,
    ScopeLevel,
    SubjectKind,
)
from app.models.redesign import AuditEvent
from app.models.user import Role

GOV = "/api/admin/governance"
REASON = "Needed for the December placement review; presenting cohort readiness."
AGREED = "Agreed at the governance meeting; this is the panel member who needs it."

#: A capability the catalogue marks as showing a student's own record, and one it
#: does not. Read from the catalogue rather than hard-coded so that re-flagging a
#: key moves these tests with it instead of quietly making them test nothing.
PII_KEY = "admin.exports"
PLAIN_KEY = "admin.catalogue"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _the_catalogue_still_says_what_this_module_assumes() -> None:
    """These tests are about a FLAG, so they must fail loudly if the flag moves.

    Without this, re-flagging `admin.exports` would leave every assertion below
    passing against a capability that no longer carries PII — a module that is
    green because it stopped testing anything.
    """
    assert CAPABILITIES_BY_KEY[PII_KEY].carries_pii is True
    assert CAPABILITIES_BY_KEY[PLAIN_KEY].carries_pii is False


@pytest.fixture
def admin(make_user):
    return make_user(f"rev-adm-{uuid.uuid4().hex[:4]}", Role.ADMIN)


@pytest.fixture
def faculty(make_user):
    return make_user(f"rev-fac-{uuid.uuid4().hex[:4]}", Role.MENTOR)


@pytest.fixture
def swept():
    """Grants and groups are keyed on a user id and OUTLIVE `make_user`.

    A leaked grant row silently widens whatever runs next, on an account id that
    has since been handed to nobody — so this is a correctness fixture, not
    tidiness.
    """
    made: dict[str, list[str]] = {"grants": [], "groups": [], "users": []}
    yield made
    with SessionLocal() as db:
        for gid in made["grants"]:
            db.execute(delete(AuditEvent).where(AuditEvent.entity_id == gid))
            db.execute(delete(CapabilityGrant).where(CapabilityGrant.id == gid))
        for uid in made["users"]:
            db.execute(delete(CapabilityGrant).where(CapabilityGrant.subject_user_id == uid))
            db.execute(delete(AccessGroupMember).where(AccessGroupMember.user_id == uid))
        for gid in made["groups"]:
            db.execute(delete(CapabilityGrant).where(CapabilityGrant.subject_group_id == gid))
            db.execute(delete(AccessGroupMember).where(AccessGroupMember.group_id == gid))
            db.execute(delete(AccessGroup).where(AccessGroup.id == gid))
        db.commit()


@pytest.fixture
def deputy(client, make_user, admin, swept):
    """A faculty account the office gave `admin.governance` to.

    THIS FIXTURE IS THE POINT OF B2.6 AND THE SUBJECT OF B2.4. A deputy is the
    account whose `carries_pii` grants WAIT: the Main Admin's are live at once,
    so the two-person rule can only be exercised through somebody who is not
    the office. `admin.governance` is deliberately NOT flagged `carries_pii`
    itself — see the comment on the catalogue entry — so the appointment is
    live at once whoever makes it.
    """
    account = make_user(f"rev-dep-{uuid.uuid4().hex[:4]}", Role.MENTOR)
    swept["users"].append(account.user_id)
    r = client.post(
        f"{GOV}/grants",
        headers=admin.headers,
        json={
            "capability": "admin.governance",
            "user_ids": [account.user_id],
            "reason": "Deputy for governance while the office is away for the audit.",
        },
    )
    assert r.status_code == 201, r.text
    rows = r.json()
    swept["grants"] += [g["id"] for g in rows]
    assert rows[0]["approval_state"] == APPROVAL_ACTIVE, (
        "the deputy's own appointment is pending, so nobody can ever approve anything"
    )
    return account


def _grant_as(client, headers: dict, swept, capability: str, user_id: str, **extra) -> dict:
    """A grant through the real endpoint, made by whoever `headers` signs as."""
    r = client.post(
        f"{GOV}/grants",
        headers=headers,
        json={"capability": capability, "user_ids": [user_id], "reason": REASON, **extra},
    )
    assert r.status_code == 201, r.text
    rows = r.json()
    swept["grants"] += [g["id"] for g in rows]
    return rows[0]


def _grant(client, admin, swept, capability: str, user_id: str, **extra) -> dict:
    """The Main Admin's grant — the common case, and the one that is live at once."""
    return _grant_as(client, admin.headers, swept, capability, user_id, **extra)


# --------------------------------------------------------------------------- #
# B2.4 — two people, unless the one person is the Main Admin
# --------------------------------------------------------------------------- #

@requires_db
def test_the_main_admins_grant_is_live_the_moment_it_is_written(
    client, admin, faculty, swept
):
    """The office does not ask anybody's permission (2026-09-16).

    REEP has ONE Main Admin by rule, every deputy holds Governance on its
    say-so, and so a second signature on the office's own decision could only
    ever come from somebody the office appointed to give it. Before this test
    existed the office's `carries_pii` grant landed `pending_approval` and, on
    the one-admin deployment this product is built for, never activated at all
    until a deputy was appointed to agree with it. The owner's rule is that the
    Main Admin grants access and control without a second approval, and
    `initial_approval_state` is where that is decided.

    DELETE THIS and the next editor makes every `carries_pii` grant pending
    "for safety", and the office is back to appointing a deputy whose only job
    is to agree with it.
    """
    row = _grant(client, admin, swept, PII_KEY, faculty.user_id)
    assert row["approval_state"] == APPROVAL_ACTIVE
    assert row["carries_pii"] is True
    # Nobody else signed it, and the row does not pretend somebody did.
    assert row["approved_by"] is None and row["approved_at"] is None

    with SessionLocal() as db:
        assert PII_KEY in granted_capabilities(db, faculty.user_id), (
            "the Main Admin's grant did not take effect"
        )
    # Same cookie, no re-login: capabilities resolve live, as they always have.
    assert PII_KEY in client.get("/api/auth/me", headers=faculty.headers).json()["capabilities"]
    assert client.get("/api/admin/exports/students.csv", headers=faculty.headers).status_code == 200

    # There is nothing to approve, and nothing waits in the queue.
    again = client.post(
        f"{GOV}/grants/{row['id']}/approve", headers=admin.headers, json={"reason": AGREED}
    )
    assert again.status_code == 409, again.text
    queue = client.get(f"{GOV}/review", headers=admin.headers).json()
    assert row["id"] not in {g["id"] for g in queue["pending"]}


@requires_db
def test_a_deputys_grant_of_a_students_records_is_not_live_until_someone_agrees(
    client, admin, faculty, deputy, swept
):
    """The grant is written, listed, audited — and holds nothing.

    A deputy holds exactly one key on the office's say-so; handing a student's
    records to a colleague on their own is the decision the four-eyes rule
    exists for, and the second pair of eyes is the Main Admin's.

    DELETE THIS and `approval_state` goes back to what it was before B2.4: a
    column written on every row, carrying a check constraint and a docstring
    about four-eyes approval, read by nobody. A grant awaiting a second person
    was fully live the moment it was inserted, and the screen said so.
    """
    row = _grant_as(client, deputy.headers, swept, PII_KEY, faculty.user_id)
    assert row["approval_state"] == APPROVAL_PENDING
    assert row["carries_pii"] is True

    with SessionLocal() as db:
        assert PII_KEY not in granted_capabilities(db, faculty.user_id), (
            "an unapproved grant resolved as held"
        )
    assert PII_KEY not in client.get("/api/auth/me", headers=faculty.headers).json()["capabilities"]
    assert client.get("/api/admin/exports/students.csv", headers=faculty.headers).status_code == 403

    ok = client.post(
        f"{GOV}/grants/{row['id']}/approve", headers=admin.headers, json={"reason": AGREED}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["approval_state"] == APPROVAL_ACTIVE
    assert ok.json()["approved_by"], "the trail does not say who agreed"

    with SessionLocal() as db:
        assert PII_KEY in granted_capabilities(db, faculty.user_id)
    # Same cookie, no re-login: capabilities resolve live, as they always have.
    assert client.get("/api/admin/exports/students.csv", headers=faculty.headers).status_code == 200


@requires_db
def test_the_person_who_granted_it_cannot_be_the_person_who_agreed(
    client, admin, faculty, deputy, swept
):
    """THE ONE ASSERTION THIS WHOLE FEATURE IS FOR, and it is about a DEPUTY.

    Everything else in B2.4 — the state column, the check constraint, the four
    readers that honour it, the pending badge, the review queue — is bookkeeping
    around one sentence: a deputy who decided cannot be the person who agreed.
    Remove this refusal and the flow still works end to end, still writes two
    audit rows, still shows an approver's name on screen, and protects nobody at
    all: one deputy clicks Grant and then clicks Approve. The Main Admin is the
    exception, deliberately -- see the test above.

    DELETE THIS and four-eyes becomes two-clicks, invisibly — every other test
    in this module still passes.
    """
    row = _grant_as(client, deputy.headers, swept, PII_KEY, faculty.user_id)

    refused = client.post(
        f"{GOV}/grants/{row['id']}/approve", headers=deputy.headers, json={"reason": AGREED}
    )
    assert refused.status_code == 403, refused.text
    assert "cannot also approve" in refused.text
    with SessionLocal() as db:
        assert db.get(CapabilityGrant, row["id"]).approval_state == APPROVAL_PENDING
        assert PII_KEY not in granted_capabilities(db, faculty.user_id)

    # ...and the refusal is about the GRANTER, not about approving in general:
    # the Main Admin is exactly who this is waiting for.
    ok = client.post(
        f"{GOV}/grants/{row['id']}/approve", headers=admin.headers, json={"reason": AGREED}
    )
    assert ok.status_code == 200, ok.text

    # And a second approval is a conflict, not a silent overwrite of who agreed.
    again = client.post(
        f"{GOV}/grants/{row['id']}/approve", headers=admin.headers, json={"reason": AGREED}
    )
    assert again.status_code == 409


@requires_db
def test_the_main_admin_may_approve_a_grant_it_made_itself(
    client, admin, faculty, swept
):
    """A row the office granted BEFORE the rule changed is still on the queue,
    and the office is the one account that can clear it.

    Every `carries_pii` grant the Main Admin made before 2026-09-16 was written
    `pending_approval`, and the self-approval refusal then applied to the office
    too -- so on the day the amendment shipped, each of those rows was stuck
    behind a deputy the office would have had to appoint for the purpose. The
    office's signature is sufficient on the create path now, so it is
    sufficient here: Approve, with a reason, finalises the row and writes the
    audit row that says so.

    DELETE THIS and the refusal below quietly widens back to the office, and
    the first production deployment to upgrade meets exactly the screen this
    test was written from: "You granted this, so you cannot also approve it",
    with nobody else able to.
    """
    with SessionLocal() as db:
        row = CapabilityGrant(
            capability=PII_KEY, subject_kind=SubjectKind.USER, subject_user_id=faculty.user_id,
            reason="Granted by the office under the old rule, and left pending.",
            granted_by_user_id=admin.user_id,
            approval_state=APPROVAL_PENDING, role_at_grant=Role.MENTOR.value,
        )
        db.add(row)
        db.commit()
        swept["grants"].append(row.id)
        grant_id = row.id
        assert PII_KEY not in granted_capabilities(db, faculty.user_id)

    ok = client.post(
        f"{GOV}/grants/{grant_id}/approve", headers=admin.headers, json={"reason": AGREED}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["approval_state"] == APPROVAL_ACTIVE
    assert ok.json()["approved_by"], "the trail does not say who agreed"
    with SessionLocal() as db:
        assert PII_KEY in granted_capabilities(db, faculty.user_id)
        trail = db.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_id == grant_id, AuditEvent.action == "APPROVED"
            )
        ).all()
        assert len(trail) == 1, "approving wrote no audit row"


@requires_db
def test_approving_demands_a_reason_and_the_subject_may_be_the_approvers_own_account(
    client, admin, deputy, swept
):
    """Agreeing is a decision, so it carries a reason of its own — and the rule
    is about the GRANTER, never the SUBJECT.

    "The office was given exports by its deputy" is an ordinary sentence, and
    refusing it would mean the Main Admin could never be granted anything while
    a deputy exists. What must not happen is one person writing and agreeing to
    the same row, which the test above pins.

    DELETE THIS and the refusal is free to widen to "the subject cannot approve",
    which locks the office out of its own console the first time a deputy hands
    it something.
    """
    row = _grant_as(client, deputy.headers, swept, PII_KEY, admin.user_id)
    assert row["approval_state"] == APPROVAL_PENDING
    thin = client.post(
        f"{GOV}/grants/{row['id']}/approve", headers=admin.headers, json={"reason": "ok"}
    )
    assert thin.status_code == 422 and "20 characters" in thin.text

    ok = client.post(
        f"{GOV}/grants/{row['id']}/approve", headers=admin.headers, json={"reason": AGREED}
    )
    assert ok.status_code == 200, ok.text


@requires_db
def test_a_capability_that_shows_no_student_record_is_live_on_one_persons_say_so(
    client, admin, faculty, deputy, swept
):
    """The rule is NARROW, and that is what keeps it alive.

    A four-eyes rule that applies to everything is a rule the office routes
    around, because the day it blocks "let this colleague see the approved
    certifications list" is the day somebody asks for it to be switched off.
    `carries_pii` is the whole of the distinction — for a deputy, who is the
    only granter it still applies to.

    DELETE THIS and the next editor makes every deputy's grant pending "for
    consistency", and the console grows a queue nobody can clear.
    """
    row = _grant_as(client, deputy.headers, swept, PLAIN_KEY, faculty.user_id)
    assert row["approval_state"] == APPROVAL_ACTIVE
    assert row["carries_pii"] is False
    with SessionLocal() as db:
        assert PLAIN_KEY in granted_capabilities(db, faculty.user_id)


@requires_db
def test_every_reader_of_a_grant_honours_the_pending_state(
    client, admin, faculty, deputy, swept
):
    """Four readers, three files, and one of them had forgotten.

    `granted_capabilities` and `granted_reaches` share `_live_grant_clauses`, so
    they cannot disagree. The other two are in the router and write their own
    SQL: the group card's capability list (`_group_out`) and the duplicate check
    on the create path. The group card was reporting a pending grant as a
    capability the group HOLDS while it resolved to nothing on every request —
    a console that shows access the API does not give, which is worse than one
    that shows none, because nobody goes looking.

    The pending rows are a DEPUTY's, because only a deputy's are pending now;
    the readers do not care who wrote the row.

    DELETE THIS and the next reader written against `revoked_at IS NULL` alone
    is a door left open with a green badge over it.
    """
    # 1 + 2: the two resolvers, through a scoped row (the reach path is the one
    # `require_capability(target=...)` uses and it does not go via the other).
    with SessionLocal() as db:
        row = CapabilityGrant(
            capability=PII_KEY, subject_kind=SubjectKind.USER, subject_user_id=faculty.user_id,
            reason="A pending grant, inserted at a rung of the spine.",
            scope_level=ScopeLevel.DEPARTMENT, scope_id=uuid.uuid4().hex,
            approval_state=APPROVAL_PENDING, role_at_grant=Role.MENTOR.value,
        )
        db.add(row)
        db.commit()
        swept["grants"].append(row.id)
        scope_id = row.scope_id

        assert PII_KEY not in granted_capabilities(db, faculty.user_id)
        assert granted_reaches(db, faculty.user_id, PII_KEY) == []
        with pytest.raises(Exception) as refused:
            require_capability(
                db, {"userId": faculty.user_id, "role": "MENTOR"}, PII_KEY,
                target=[(ScopeLevel.DEPARTMENT, scope_id)],
            )
        assert getattr(refused.value, "status_code", None) == 403

    # 3: the group card.
    grp = client.post(
        f"{GOV}/groups", headers=admin.headers, json={"name": f"Panel {uuid.uuid4().hex[:6]}"}
    )
    assert grp.status_code == 201, grp.text
    gid = grp.json()["id"]
    swept["groups"].append(gid)
    made = client.post(f"{GOV}/grants", headers=deputy.headers, json={
        "capability": PII_KEY, "group_ids": [gid], "reason": REASON})
    assert made.status_code == 201, made.text
    swept["grants"] += [g["id"] for g in made.json()]
    assert made.json()[0]["approval_state"] == APPROVAL_PENDING
    card = next(g for g in client.get(f"{GOV}/groups", headers=admin.headers).json() if g["id"] == gid)
    assert PII_KEY not in card["capabilities"], (
        "the group card listed a capability nobody in the group actually holds"
    )

    # 4: the duplicate check. A pending row is already a decision about this
    # pair, so re-granting must not write a second one for the approver to
    # choose between — whoever re-grants, the office included.
    for headers in (deputy.headers, admin.headers):
        again = client.post(f"{GOV}/grants", headers=headers, json={
            "capability": PII_KEY, "group_ids": [gid], "reason": REASON})
        assert again.status_code == 201 and again.json() == [], again.text


# --------------------------------------------------------------------------- #
# B2.4 — the review queue
# --------------------------------------------------------------------------- #

@requires_db
def test_a_grant_that_never_expires_still_gets_a_date_somebody_has_to_look_at(
    client, admin, faculty, swept
):
    """A REVIEW IS NOT AN EXPIRY, and the grant with no expiry is the one that
    needs the review most — the access given for one November audit and still
    held in March. Nothing else would ever bring it back to anybody's attention.

    DELETE THIS and `review_at` goes back to being a decorative column, and the
    queue silently becomes an expiry list with a misleading name.
    """
    row = _grant(client, admin, swept, PLAIN_KEY, faculty.user_id)
    assert row["expires_at"] is None
    assert row["review_at"] is not None, "a grant with no expiry got no review date"
    review_at = datetime.fromisoformat(row["review_at"])
    expected = _now() + timedelta(days=REVIEW_AFTER_DAYS)
    assert abs((review_at - expected).total_seconds()) < 120

    # Far out, so it is NOT in the queue yet: a review list that shows every
    # grant is the grants table with a different heading.
    queue = client.get(f"{GOV}/review", headers=admin.headers)
    assert queue.status_code == 200, queue.text
    assert row["id"] not in {g["id"] for g in queue.json()["expiring"]}


@requires_db
def test_the_queue_separates_what_is_waiting_on_a_person_from_what_is_running_out(
    client, admin, faculty, deputy, swept
):
    """Two lists, because they need opposite actions.

    A pending grant holds nothing and somebody is waiting on a decision; an
    expiring grant is working now and will stop. Merged into one "needs
    attention" list, the urgent one queues behind the routine one on the day
    they collide.

    DELETE THIS and the queue can quietly become a single list, or start reading
    only `expires_at` — which would drop every never-expiring grant, the exact
    population the review date exists for.
    """
    soon = _now() + timedelta(days=REVIEW_HORIZON_DAYS - 5)
    expiring = _grant(
        client, admin, swept, PLAIN_KEY, faculty.user_id, expires_at=soon.isoformat()
    )
    # A deputy's, because only a deputy's `carries_pii` grant waits.
    waiting = _grant_as(client, deputy.headers, swept, PII_KEY, faculty.user_id)

    # A grant with NO expiry, whose review date is inside the horizon. This is
    # the population the queue exists for and the one an expiry-only query drops
    # silently: it never lapses, so nothing else will ever raise it.
    forever = _grant(client, admin, swept, "admin.jobs", faculty.user_id)
    assert forever["expires_at"] is None
    assert client.post(
        f"{GOV}/grants/{forever['id']}/extend", headers=admin.headers,
        json={
            "reason": "Brought forward for the term's governance review meeting.",
            "review_at": (_now() + timedelta(days=REVIEW_HORIZON_DAYS - 10)).isoformat(),
        },
    ).status_code == 200

    body = client.get(f"{GOV}/review", headers=admin.headers).json()
    assert body["horizon_days"] == REVIEW_HORIZON_DAYS
    assert forever["id"] in {g["id"] for g in body["expiring"]}, (
        "a grant that never lapses is invisible to the queue — which is the one "
        "grant the review date exists for"
    )
    assert waiting["id"] in {g["id"] for g in body["pending"]}
    assert waiting["id"] not in {g["id"] for g in body["expiring"]}, (
        "a grant that holds nothing was filed under 'running out'"
    )
    assert expiring["id"] in {g["id"] for g in body["expiring"]}
    assert expiring["id"] not in {g["id"] for g in body["pending"]}

    # Approved, it leaves the pending list — and its own expiry is far out, so
    # it does not simply move across.
    client.post(
        f"{GOV}/grants/{waiting['id']}/approve", headers=admin.headers, json={"reason": AGREED}
    )
    body = client.get(f"{GOV}/review", headers=admin.headers).json()
    assert waiting["id"] not in {g["id"] for g in body["pending"]}


@requires_db
def test_extending_pushes_the_dates_out_and_makes_somebody_type_why_again(
    client, admin, faculty, swept
):
    """"Still needed" is a different decision from "needed".

    A review that copies the original reason forward produces a trail in which
    every grant looks as though it was justified once and never questioned —
    which is the failure the review queue exists to break, arriving through the
    button that is supposed to break it.

    DELETE THIS and `/extend` is free to become a one-click "+180 days" with no
    reason, no audit row worth reading, and no date it refuses.
    """
    soon = _now() + timedelta(days=3)
    row = _grant(client, admin, swept, PLAIN_KEY, faculty.user_id, expires_at=soon.isoformat())

    assert client.post(
        f"{GOV}/grants/{row['id']}/extend", headers=admin.headers, json={"reason": REASON}
    ).status_code == 422, "an extension that names no new date changed something"

    stale = (_now() - timedelta(days=1)).isoformat()
    past = client.post(
        f"{GOV}/grants/{row['id']}/extend", headers=admin.headers,
        json={"reason": REASON, "expires_at": stale},
    )
    assert past.status_code == 422 and "in the past" in past.text

    thin = client.post(
        f"{GOV}/grants/{row['id']}/extend", headers=admin.headers,
        json={"reason": "ok", "expires_at": (_now() + timedelta(days=90)).isoformat()},
    )
    assert thin.status_code == 422 and "20 characters" in thin.text

    later = _now() + timedelta(days=120)
    ok = client.post(
        f"{GOV}/grants/{row['id']}/extend", headers=admin.headers,
        json={
            "reason": "Reviewed at the governance meeting; the audit runs to March.",
            "expires_at": later.isoformat(),
            "review_at": (_now() + timedelta(days=100)).isoformat(),
        },
    )
    assert ok.status_code == 200, ok.text
    assert abs((datetime.fromisoformat(ok.json()["expires_at"]) - later).total_seconds()) < 120

    with SessionLocal() as db:
        trail = db.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_id == row["id"], AuditEvent.action == "EXTENDED"
            )
        ).all()
        assert len(trail) == 1, "extending wrote no audit row"
        assert trail[0].before_json["expires_at"] != trail[0].after_json["expires_at"]


@requires_db
def test_a_grant_that_has_already_lapsed_is_granted_again_rather_than_extended(
    client, admin, faculty, swept
):
    """Reviving a lapsed row restores access on a decision that already ended.

    The original reason would be doing work on a day nobody chose, and the trail
    would show a grant that never stopped. Re-granting costs one request and
    writes a row that says what it is.

    DELETE THIS and `/extend` becomes an undo for expiry, which is how an access
    control with dates on it ends up with no dates that mean anything.
    """
    with SessionLocal() as db:
        row = CapabilityGrant(
            capability=PLAIN_KEY, subject_kind=SubjectKind.USER, subject_user_id=faculty.user_id,
            reason="A grant that lapsed a fortnight ago, left on the table.",
            expires_at=_now() - timedelta(days=14), role_at_grant=Role.MENTOR.value,
        )
        db.add(row)
        db.commit()
        swept["grants"].append(row.id)
        gid = row.id

    r = client.post(
        f"{GOV}/grants/{gid}/extend", headers=admin.headers,
        json={"reason": REASON, "expires_at": (_now() + timedelta(days=30)).isoformat()},
    )
    assert r.status_code == 409, r.text
    assert "lapsed" in r.text
    with SessionLocal() as db:
        assert db.get(CapabilityGrant, gid).expires_at < _now()


# --------------------------------------------------------------------------- #
# B2.5 — a grant describes a person in a role
# --------------------------------------------------------------------------- #

@requires_db
def test_a_grant_stops_counting_when_the_account_stops_holding_that_role(faculty, swept):
    """"This MENTOR may read the registrations queue" stops describing anybody
    the moment the account is something else.

    Pinned against the ROW's role, not the session's claim: a signed cookie is a
    snapshot, and answering from the claim would let the stale claim vouch for
    the stale grant on every request made before the cookie is rejected.

    DELETE THIS and a demoted account keeps its console screens, with a live
    audit row saying it was given them and nothing saying it should have stopped.
    """
    with SessionLocal() as db:
        row = CapabilityGrant(
            capability=PLAIN_KEY, subject_kind=SubjectKind.USER, subject_user_id=faculty.user_id,
            reason="Granted while this account was a member of faculty.",
            role_at_grant=Role.MENTOR.value,
        )
        db.add(row)
        db.commit()
        swept["grants"].append(row.id)
        assert PLAIN_KEY in granted_capabilities(db, faculty.user_id)

    # The role changes by any route at all — here, straight on the row, which is
    # the case the explicit revocation in `grant_access` cannot cover.
    with SessionLocal() as db:
        from app.models.user import User

        db.get(User, faculty.user_id).role = Role.ALUMNI
        db.commit()
    try:
        with SessionLocal() as db:
            assert PLAIN_KEY not in granted_capabilities(db, faculty.user_id), (
                "a grant made to a MENTOR survived the account becoming an alumnus"
            )
            assert granted_reaches(db, faculty.user_id, PLAIN_KEY) == [], (
                "the reach path forgot the role while the capability path remembered"
            )
    finally:
        with SessionLocal() as db:
            from app.models.user import User

            db.get(User, faculty.user_id).role = Role.MENTOR
            db.commit()


@requires_db
def test_a_grant_from_before_the_column_existed_is_honoured_not_refused(faculty, swept):
    """NULL `role_at_grant` means "we do not know", and guessing revokes real
    access on the deploy that ships the guess.

    It is also what every GROUP grant carries, correctly: a grant to "the
    placement coordinators" is a decision about the group, not about a role.

    DELETE THIS and the safe reading (ignore what you cannot verify) is free to
    flip to the unsafe-for-the-user one (refuse what you cannot verify), which
    on the morning of the deploy looks exactly like every staff screen breaking
    at once.
    """
    with SessionLocal() as db:
        row = CapabilityGrant(
            capability=PLAIN_KEY, subject_kind=SubjectKind.USER, subject_user_id=faculty.user_id,
            reason="A row written before role_at_grant existed on this table.",
        )
        db.add(row)
        db.commit()
        swept["grants"].append(row.id)
        assert row.role_at_grant is None
        assert PLAIN_KEY in granted_capabilities(db, faculty.user_id), (
            "a pre-existing grant was revoked by a column the migration could not fill"
        )


@requires_db
def test_a_grant_records_the_role_it_was_made_about(client, admin, faculty, swept):
    """The column is written by the ONE writer of grants, at the moment the
    decision is made, from the subject's row.

    DELETE THIS and new grants land with a NULL — which the reader honours, by
    design — so the whole of B2.5 silently stops applying to anything granted
    from today onward, and the test above goes on passing.
    """
    row = _grant(client, admin, swept, PLAIN_KEY, faculty.user_id)
    with SessionLocal() as db:
        assert db.get(CapabilityGrant, row["id"]).role_at_grant == Role.MENTOR.value


@requires_db
def test_changing_a_role_through_grant_access_revokes_the_grants_and_the_memberships(
    client, admin, faculty, swept
):
    """Inert is not the same as gone, and the office needs to see gone.

    `role_at_grant` makes a stale grant stop working with no write at all — the
    fail-safe that holds even when the role was changed by hand in SQL. This is
    the other half: `grant_access` revokes the rows and removes the memberships,
    so the Governance screen stops listing access the person does not have.
    An office that can see access somebody no longer holds will eventually act
    on it.

    DELETE THIS and a demotion leaves a tidy-looking list of grants that all
    resolve to nothing — and the next person to read it re-grants them.
    """
    row = _grant(client, admin, swept, PLAIN_KEY, faculty.user_id)
    grp = client.post(
        f"{GOV}/groups", headers=admin.headers, json={"name": f"Cell {uuid.uuid4().hex[:6]}"}
    )
    gid = grp.json()["id"]
    swept["groups"].append(gid)
    swept["users"].append(faculty.user_id)
    assert client.post(
        f"{GOV}/groups/{gid}/members", headers=admin.headers,
        json={"user_ids": [faculty.user_id], "reason": REASON},
    ).status_code == 200

    with SessionLocal() as db:
        # No --name: changing a role must not ask anybody to retype a colleague's
        # name, which is the behaviour tests/test_grant_access_name.py pins.
        grant_access(db, faculty.email, None, Role.ALUMNI)

    try:
        with SessionLocal() as db:
            stamped = db.get(CapabilityGrant, row["id"])
            assert stamped.revoked_at is not None, "the grant survived the role change"
            assert "Role changed from MENTOR to ALUMNI" in (stamped.revoke_reason or "")
            assert db.scalar(
                select(AccessGroupMember.id).where(
                    AccessGroupMember.group_id == gid,
                    AccessGroupMember.user_id == faculty.user_id,
                )
            ) is None, "the account kept inheriting the group's capabilities"
            # Audited, through the writer the console uses, so the change appears
            # in the audit API beside the ones made on screen.
            actions = set(db.scalars(
                select(AuditEvent.action).where(AuditEvent.entity_id.in_([row["id"], gid]))
            ).all())
            assert "REVOKED" in actions and "MEMBER_REMOVED" in actions
    finally:
        with SessionLocal() as db:
            from app.models.user import User

            db.get(User, faculty.user_id).role = Role.MENTOR
            db.commit()
