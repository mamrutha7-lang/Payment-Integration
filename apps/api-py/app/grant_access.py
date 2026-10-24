"""Grant one person Google sign-in access.

    python -m app.grant_access <email> --name "Full Name" --role ADMIN

Sign-in is Google-only, and the allowlist is not a config file â it is the
`users` table itself. `google_callback` looks the verified Google address up
case-insensitively and refuses anything it does not find, with no just-in-time
provisioning. So "allow this person to log in" means exactly one thing: give
them a row. THE DOMAIN IS NOT CHECKED â GOOGLE_ALLOWED_DOMAIN is a label, not a
fence (app/google_auth.py says why), so this row is the whole of the access
control and a @gmail.com address granted here signs in exactly like any other.

`app/seed_roster.py` does this in bulk for the student roster. This module is the
single-account counterpart, for the people a roster never contains â the operator
who has to get in before anyone else does, a mentor hired mid-term, a director.

STUDENT REQUIRES --usn, deliberately. A student is not a `User` row alone:
`_payload_for()` in app/routers/auth.py only puts `studentId` in the session
when `user.student` exists, and every /api/student/* route 403s without it â so
a bare student User row would sign in perfectly and then fail on every screen
it can reach. With --usn this tool creates the full User + Student +
StudentProfile trio exactly as `app/seed_roster.py` does; it exists for the
student the roster never contains â the operator's own TEST account, a late
joiner. Use a USN no roster row will ever claim (e.g. TEST01): it is UNIQUE,
and a real USN granted here blocks that student's roster seed. The real batch
stays `app.seed_roster`'s job.

PRODUCTION-SAFE, deliberately. Unlike `app/seed.py` â which refuses when
``ENV=prod`` because it creates demo accounts behind passwords published in
AGENTS.md â this writes no password anyone can use. It follows the `seed_kb.py`
precedent instead: production genuinely needs it, because on a fresh production
database with Google-only sign-in and an empty `users` table, *nobody can log in
at all*, including the person who has to fix that.

Idempotent. Re-running against an existing address updates the name and role
rather than raising on the unique constraint, so it doubles as "promote this
person" without a second tool.
"""

from __future__ import annotations

import argparse
import re
import sys
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from datetime import datetime, timezone
from types import SimpleNamespace

from .architecture_events import record_change
from .db import SessionLocal
from .mentor_history import record_mentor_change
from .models.governance import AccessGroupMember, CapabilityGrant, SubjectKind
from .models.institution import Department
from .models.student_profile import StudentProfile
from .models.user import Mentor, Role, Student, User
from .config import settings
from .seed_roster import db_target
from .usn import usn_is

# The roles this tool may mint on its own. STUDENT is absent on purpose â see the
# module docstring; a User row without its Student row is an account that signs
# in and then 403s everywhere. MENTOR is present but warned about below: a MENTOR
# with no `Mentor` group correctly sees NOBODY (AGENTS.md rule 2), which is safe
# but silent, and the operator should know before the mentor reports it as a bug.
#
# ALUMNI needs nothing but the row. It has no Student and no Mentor row by
# definition (its session claims carry neither studentId nor mentorId), and its
# profile is deliberately NOT created here: `GET /api/alumni/profile` answers
# `created: false` until they save one, and that flag is what makes the client
# show the first-login create form. Minting a blank profile would skip the only
# screen a new alumnus is meant to land on.
#
# DIRECTOR IS NOT GRANTED, and ADMIN is granted ONCE. REEP has one Main Admin:
# the office account that opens Governance and decides what faculty may see.
# Faculty are MENTOR - a mentor with a grant reaches exactly the console screens
# the Main Admin chose, and nothing widens on its own. A DIRECTOR would hold
# every console screen by baseline, which is a second admin under another name,
# so the role stays for the dev seed and the tests and is never minted here.
_GRANTABLE_ROLES = (Role.MENTOR, Role.ADMIN, Role.ALUMNI)
_OFFICE_ROLES = (Role.ADMIN, Role.DIRECTOR)


def _refuse_second_main_admin(db: Session, email: str) -> None:
    """One Main Admin. Re-running for the SAME address is the idempotent update
    the module promises; a DIFFERENT address while an office account exists is
    refused, and the message says how a handover is done instead: demote the
    current one first (re-run it as MENTOR), then grant the new."""
    current = db.scalar(select(User).where(func.lower(User.email) == email))
    if current is not None and current.role is Role.ADMIN:
        return  # the Main Admin's own address: the idempotent update, never a refusal
    others = db.scalars(
        select(User).where(User.role.in_(_OFFICE_ROLES), func.lower(User.email) != email)
    ).all()
    if others:
        held = ", ".join(sorted(u.email for u in others))
        raise ValueError(
            f"REEP has one Main Admin, and that account exists: {held}. Faculty are "
            "granted as MENTOR and given screens by the Main Admin in Governance. To "
            f"hand the console to {email}, first re-run this for the current Main Admin "
            "with --role MENTOR (or ALUMNI), then grant ADMIN."
        )

# Unusable-password sentinel, identical to seed_roster.SSO_ONLY_PASSWORD_HASH and
# for the same reason: `users.password_hash` is NOT NULL, but these accounts must
# never be able to authenticate with a password. `verify_password` splits on ":"
# and rejects anything that is not exactly scrypt:<salt>:<digest>, so this value
# returns False for every password ever tried â POST /api/auth/login answers with
# its ordinary 401 and reveals nothing about which accounts are SSO-only.
SSO_ONLY_PASSWORD_HASH = "google-only"

# Exactly what app/security.py:hash_password emits: `scrypt:<salt>:<digest>`
# with a 16-byte salt (32 hex chars) and dklen=64 (128 hex chars). Anchored and
# exact, so the only thing this accepts is a hash `verify_password` can actually
# check against — a truncated paste, a sentinel, or a hash from a different KDF
# is refused here rather than written as a key nobody can use.
_PASSWORD_HASH_RE = re.compile(r"^scrypt:[0-9a-f]{32}:[0-9a-f]{128}$")


def _validate_password_hash(value: str) -> str:
    """Accept a precomputed scrypt hash for `--password-hash`, or raise.

    WHY A HASH IS ALLOWED IN ARGV WHEN A PASSWORD IS NOT. app.set_password
    refuses a `--password` flag because argv lands in shell history, in `ps`,
    and in the CloudTrail record of an ECS RunTask's overrides. A scrypt hash
    with a random 16-byte salt is not a secret in that sense: recovering a
    12+-character password from it is the attack scrypt with N=16384 exists to
    make impractical, and the plaintext never leaves the machine that hashed it.
    That is what lets the ops-task workflow provision a password-holding account
    from the browser without the password crossing the RunTask API.

    The length floor and the published-password denylist live where the
    PLAINTEXT is — `python -m app.set_password --print-hash` applies both before
    it prints anything. This path trusts that the operator hashed there; it
    cannot check a password it never sees.
    """
    candidate = value.strip()
    if not _PASSWORD_HASH_RE.fullmatch(candidate):
        raise ValueError(
            "--password-hash must be exactly what `python -m app.set_password "
            "--print-hash` prints (scrypt:<32 hex>:<128 hex>); a password itself "
            "is never accepted here"
        )
    return candidate



def _cli_actor() -> tuple[dict, SimpleNamespace]:
    """A session and a request shaped for `record_change`, from a command line.

    THE AUDIT WRITER IS NOT REIMPLEMENTED HERE, and that is the point of the
    shim. `record_change` writes the `redesign_audit_events` row AND the outbox
    event, with the same field names the console's own mutations use, so a role
    change made from a terminal appears in `GET /api/admin/audit` beside the ones
    made on screen. A hand-rolled `AuditEvent(...)` in this module would be the
    second copy that stops matching the first — and the trail that only records
    what somebody did through a browser is the trail that misses the interesting
    half.

    `actor_user_id` is NULL because there is no signed-in person: whoever ran
    this held a shell on the deployment, which is a stronger fact than any user
    id and one this process cannot name. The route records the command instead,
    so the row says how it happened.
    """
    return (
        {"userId": None},
        SimpleNamespace(
            headers={},
            url=SimpleNamespace(path="python -m app.grant_access"),
            state=SimpleNamespace(request_id=None),
        ),
    )


def _release_governance_on_role_change(
    db: Session, user: User, *, previous: Role, new: Role
) -> tuple[int, int]:
    """A role change ends every grant and every group membership (B2.5).

    A GRANT IS A DECISION ABOUT A PERSON IN A ROLE — "this MENTOR may read the
    registrations queue". Demote or promote the account and the sentence stops
    describing anybody, so the grant must stop counting. Two mechanisms do that
    here and they are not redundant:

      * `role_at_grant` makes the row INERT the moment the role differs, with no
        write at all. That is the fail-safe: it holds on a deployment where this
        function never ran, where the role was changed by hand in SQL, or where
        this process died between the role write and the revocations.
      * These explicit revocations make it VISIBLE. An inert row still reads as
        a live grant on the Governance screen and in `GET /grants`, and an office
        that can see access somebody no longer has will eventually act on it.

    Memberships are DELETED rather than stamped, unlike grants. A membership is
    not a decision with a reason — `AccessGroupMember` has no `revoked_at` and no
    `reason` column, because the decision is the GROUP'S grant and that row stays
    exactly where it is. Removing the person is how they stop inheriting it, and
    the audit row below is what records that it happened.
    """
    now = datetime.now(timezone.utc)
    session, request = _cli_actor()
    note = (
        f"Role changed from {previous.value} to {new.value} by "
        "`python -m app.grant_access`; a grant describes a person in a role."
    )

    grants = db.scalars(
        select(CapabilityGrant).where(
            CapabilityGrant.subject_kind == SubjectKind.USER,
            CapabilityGrant.subject_user_id == user.id,
            CapabilityGrant.revoked_at.is_(None),
        )
    ).all()
    for g in grants:
        g.revoked_at = now
        g.revoke_reason = note
        record_change(
            db, session=session, request=request, tenant_id=None,
            entity_type="capability_grant", entity_id=g.id, action="REVOKED",
            before={"capability": g.capability, "role_at_grant": g.role_at_grant},
            after={"capability": g.capability, "revoke_reason": note},
            event_type="governance.capability.revoked",
            payload={"capability": g.capability, "user_id": user.id},
        )

    memberships = db.scalars(
        select(AccessGroupMember).where(AccessGroupMember.user_id == user.id)
    ).all()
    for m in memberships:
        group_id = m.group_id
        db.delete(m)
        record_change(
            db, session=session, request=request, tenant_id=None,
            entity_type="access_group", entity_id=group_id, action="MEMBER_REMOVED",
            before={"user_id": user.id, "reason": note}, after=None,
            event_type="governance.group.member_removed",
            payload={"user_id": user.id},
        )
    return len(grants), len(memberships)


def grant(
    db: Session,
    email: str,
    name: str | None,
    role: Role,
    usn: str | None = None,
    with_group: bool = False,
    password_hash: str | None = None,
    mentor_email: str | None = None,
    department_id: str | None = None,
    require_department: bool = False,
) -> tuple[User, bool]:
    """Create or update the user row that permits `email` to sign in.

    Takes an existing session, the shape `seed_kb.seed_knowledge` and
    `seed_roster.seed_roster` both use, so a caller can run this inside its own
    scope. Returns the row and whether it was newly created, so the caller can
    print an honest summary instead of claiming to have created something it
    updated.

    `with_group` applies to MENTOR only and creates the `Mentor` row that makes
    the account a real mentor. Without it the grant is exactly what it was: a
    MENTOR who signs in and sees nobody. It is opt-in rather than automatic
    because tests/test_auth_rbac.py builds a group-less mentor on purpose to
    prove rule 2 refuses, and a tool that always created the group would leave
    no way to reach that state.

    STUDENT is granted only when `usn` is supplied, and then it creates the
    FULL trio (User + Student + StudentProfile) exactly as seed_roster does â
    the module refuses a bare student User row for the reason the docstring
    gives, not students as such. This path exists for the account the roster
    never contains: the operator's own test student, a late joiner. The USN is
    the caller's to choose and UNIQUE â a test account should use one that can
    never collide with a real roster row (e.g. TEST01), because a real USN
    granted here would block that student's roster seed forever.
    """
    normalised = email.strip().lower()
    if "@" not in normalised:
        raise ValueError(f"not an email address: {email!r}")
    if role is Role.STUDENT and not (usn or "").strip():
        raise ValueError(
            "STUDENT needs --usn (a User row alone gives no studentId, and every "
            "/api/student/* route 403s without it) â or use `python -m "
            "app.seed_roster` for the real batch"
        )
    if role is Role.DIRECTOR:
        raise ValueError(
            "DIRECTOR is not granted: REEP has one Main Admin (ADMIN), and faculty are "
            "MENTOR - the Main Admin gives them console screens in Governance"
        )
    # WHERE THIS PERSON BELONGS, REQUIRED ON A NEW ACCOUNT AND ONLY ON A NEW ONE.
    #
    # This tool was the last door into the roster that placed nobody. The console
    # already refuses: `AdminFacultyIn.department_id` is `str` with no default,
    # and the public registration form requires College and Department. Only this
    # path could mint an account belonging to no institution at all -- and it
    # silently did, which is not a tidiness problem. A student with no department
    # resolves to no college, no batch and therefore no `default_track`, so the
    # interview picker stays on "General interview", and a general interview has
    # no wrap-up phase and can NEVER be scored. The student takes five mock
    # interviews, every one comes back with a dash where the score goes, and
    # nothing anywhere says why.
    #
    # ONE LEVEL AND NOT TWO, because `departments.college_id` is NOT NULL: naming
    # a department names a college, and asking for both invites them to disagree.
    # It is the same single-pointer shape `users.department_id` and
    # `students.cohort_id` already use -- the ancestors are reached through the
    # join and stored on nothing.
    #
    # NOT required on an update. Re-running with a different --role is the
    # supported way to promote somebody, and demanding a department to do it
    # would either block the promotion or make the operator retype a value that
    # is already on the row -- which is how `--name` renames people who only
    # needed a role change.
    department: Department | None = None
    if department_id is not None and str(department_id).strip():
        department = db.get(Department, str(department_id).strip())
        if department is None:
            raise ValueError(
                f"no department with id {department_id!r}. List them with "
                "`GET /api/admin/departments`, or on the Catalogue screen."
            )
    if role is not Role.STUDENT and role not in _GRANTABLE_ROLES:
        raise ValueError(f"{role.value} cannot be granted here")
    if role is Role.ADMIN:
        _refuse_second_main_admin(db, normalised)
    if with_group and role is not Role.MENTOR:
        raise ValueError("--with-group applies to MENTOR only")
    if password_hash is not None:
        password_hash = _validate_password_hash(password_hash)
    mentor_group: Mentor | None = None
    if mentor_email is not None:
        if role is not Role.STUDENT:
            raise ValueError("--mentor applies to STUDENT only")
        wanted = mentor_email.strip().lower()
        mentor_user = db.scalar(select(User).where(func.lower(User.email) == wanted))
        if mentor_user is None or mentor_user.role is not Role.MENTOR:
            raise ValueError(f"{wanted} is not a MENTOR account; grant it first")
        mentor_group = db.scalar(select(Mentor).where(Mentor.user_id == mentor_user.id))
        if mentor_group is None:
            # Rule 2: the `Mentor` row IS the group. Without it there is nothing
            # to point the student at, and silently skipping would leave a
            # mentor who "has" a student they cannot see.
            raise ValueError(
                f"{wanted} has no Mentor group yet; re-grant it with --with-group first"
            )

    # Case-insensitive, matching the callback's lookup: granting a second row
    # that differs only in case would create an account nobody can sign into,
    # because `users.email` is UNIQUE on the exact string and the callback
    # lowercases before it compares.
    user = db.scalar(select(User).where(func.lower(User.email) == normalised))
    created = user is None
    released_grants = released_memberships = 0

    # A NEW account cannot have a blank name; an EXISTING one keeps the name it
    # has. `--name` is therefore required here and only here. `user.name = name`
    # used to be unconditional, so the only way to change a role was to
    # overwrite the name -- and on production, demoting a second Main Admin to
    # faculty meant either retyping a colleague's name from memory or renaming
    # them. Neither is a thing a role change should ask for.
    if created and not (name or "").strip():
        raise ValueError(
            f"{normalised} does not exist yet, so --name is needed to create it. "
            "Omit --name only to update an account that is already there."
        )

    # REQUIRED AT THE OPERATOR BOUNDARY, NOT IN THE LIBRARY, and the line is
    # drawn here on purpose. `main()` passes require_department=True, so every
    # human-facing door now demands a filing: this tool, the console's
    # `AdminFacultyIn.department_id` (`str`, no default) and the public
    # registration form. What stays permissive is the in-process caller --
    # `seed_roster` files a whole batch by USN through its own path, and the
    # suite builds fixture accounts that never reach a screen.
    #
    # The honest cost: a future in-process caller could still mint an unfiled
    # account. That is a narrower hole than the one this closes, and closing it
    # here instead would have meant a blind refactor of a dozen fixtures in four
    # test modules to invent Department rows they do not otherwise need.
    if require_department and user is None and department is None:
        raise ValueError(
            "a new account needs --department-id: an account filed under no "
            "department resolves to no college and no batch, and for a STUDENT "
            "that means the mock interview never preselects a track and can "
            "never be scored. List them with `GET /api/admin/departments`. "
            "Omit it only to update an account that is already there."
        )

    if user is None:
        user = User(
            id=uuid.uuid4().hex,
            email=normalised,
            name=name,
            role=role,
            # The sentinel unless the operator brought a key. Google sign-in
            # works either way; the hash only adds a second door.
            password_hash=password_hash or SSO_ONLY_PASSWORD_HASH,
            department_id=department.id if department else None,
            # The free-text line the leave form prints, filled from the chosen
            # department's NAME so the two can never disagree on paper. Exactly
            # what routers/admin_faculty.py::create_faculty does.
            department=department.name if department else None,
        )
        db.add(user)
    else:
        # Update rather than raise. Re-running with a different --role is the
        # supported way to promote someone, and re-running identically is a
        # no-op the operator can do safely when unsure whether it took.
        # A role is copied into every session JWT. Advance token_version on a
        # real role transition so old privilege-bearing cookies are rejected
        # once the other workers' revocation caches converge. Repeating the
        # same grant remains idempotent.
        role_changed = user.role is not role
        previous_role = user.role
        # Only when the operator actually supplied one. Omitting --name is how a
        # role is changed without touching the person's name.
        if (name or "").strip():
            user.name = name
        user.role = role
        if role_changed:
            user.token_version = (user.token_version or 0) + 1
            # B2.5. Every capability grant and every access-group membership this
            # account held described the role it no longer has. Both end here,
            # and both are audited — see `_release_governance_on_role_change`.
            released_grants, released_memberships = _release_governance_on_role_change(
                db, user, previous=previous_role, new=role
            )
        if password_hash is not None:
            # Re-running with a hash SETS or ROTATES the key; re-running without
            # one leaves whatever key exists alone, so "promote this person"
            # never silently revokes their password.
            user.password_hash = password_hash
        if department is not None:
            # Same contract as the hash above: supplied, it MOVES them; omitted,
            # it leaves the filing alone. A role change must not clear a
            # department the operator never mentioned.
            user.department_id = department.id
            user.department = department.name

    if role is Role.STUDENT:
        # The trio, exactly as seed_roster builds it. flush() before each
        # dependent row so the ids exist; idempotent on re-run, filling in only
        # what is missing â the same contract seed_roster keeps for accounts a
        # student has already touched.
        db.flush()
        wanted_usn = (usn or "").strip().upper()
        other = db.scalar(select(Student).where(usn_is(Student.usn, wanted_usn)))
        if other is not None and other.user_id != user.id:
            db.rollback()
            raise ValueError(
                f"USN {wanted_usn} already belongs to another account â a test "
                "student must use a USN no roster row will ever claim"
            )
        stu = db.scalar(select(Student).where(Student.user_id == user.id))
        if stu is None:
            # `students.department_id` as well as `users.department_id`, and it is
            # not redundant: 31f7a4c60b12 added it because a student with no batch
            # had NO institutional pointer at all, and Batch is optional at
            # registration while Department is not. This is the pointer the
            # student's own profile card and `governance.ancestry_of_student`
            # read when there is no cohort yet, which for a granted test account
            # is always.
            stu = Student(
                user_id=user.id,
                usn=wanted_usn,
                department_id=department.id if department else None,
            )
            db.add(stu)
        elif not stu.usn:
            stu.usn = wanted_usn
        if department is not None and stu.department_id is None:
            # Fill a gap, never overwrite a seating somebody already chose: the
            # batch wins over this pointer wherever it has one
            # (student_placement.resolve_student_department), so moving a seated
            # student from here would put the two into a disagreement this tool
            # is not the right place to settle.
            stu.department_id = department.id
        db.flush()
        if db.scalar(select(StudentProfile).where(StudentProfile.student_id == stu.id)) is None:
            db.add(StudentProfile(student_id=stu.id))
        if mentor_group is not None:
            # Points the student at the mentor's group, which is the only thing
            # that makes them visible to that mentor (rule 2). Idempotent.
            previous_mentor_id = stu.mentor_id
            stu.mentor_id = mentor_group.id
            # B9.1. The fifth writer of this pointer, through the same one
            # function as the other four. `record_mentor_change` does nothing
            # when the pair is unchanged, which is what keeps this CLI idempotent
            # in the history as well as on the row.
            record_mentor_change(
                db,
                student_id=stu.id,
                previous_mentor_id=previous_mentor_id,
                new_mentor_id=mentor_group.id,
                by_user_id=None,
                reason="granted by app.grant_access",
            )

    if role is Role.MENTOR and with_group:
        # The `Mentor` row IS the group: `_assert_can_access_student` narrows a
        # MENTOR to the students whose `mentor_id` points at it, and
        # `_payload_for` only puts `mentorId` in the session when this row
        # exists. Idempotent -- a second run finds the existing row.
        db.flush()
        if db.scalar(select(Mentor).where(Mentor.user_id == user.id)) is None:
            db.add(Mentor(user_id=user.id))

    db.commit()
    db.refresh(user)
    # Said out loud, because "your Analytics screen is gone" is otherwise a
    # mystery to the person whose role was changed and to whoever changed it.
    if released_grants or released_memberships:
        print(
            f"  Role change released {released_grants} capability grant(s) and "
            f"{released_memberships} access-group membership(s). They are revoked, "
            "not deleted, and re-granted in Governance if they are still wanted."
        )
    return user, created


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.grant_access",
        description="Grant one person Google sign-in access (the allowlist is the users table).",
    )
    parser.add_argument("email", help="the Google address, e.g. someone@bgscet.ac.in")
    parser.add_argument(
        "--name",
        default=None,
        help=(
            'display name, e.g. "Darshan B". Required only when the account does not '
            "exist yet. OMIT IT to change an existing account's role without "
            "rewriting their name - which is what a demotion is."
        ),
    )
    parser.add_argument(
        "--role",
        # REQUIRED, never defaulted. This used to default to ADMIN â the highest
        # privilege in the system, which by AGENTS.md rule 2 reads every
        # student's marks, attendance and USN. A forgotten flag must be an
        # argparse error, not a silent grant of everything.
        required=True,
        choices=[r.value for r in _GRANTABLE_ROLES] + [Role.STUDENT.value],
        help="role to grant â required, because there is no safe default",
    )
    parser.add_argument(
        "--with-group",
        action="store_true",
        help="MENTOR only: also create the Mentor group row. Without it the "
        "mentor signs in and sees no students at all (AGENTS.md rule 2).",
    )
    parser.add_argument(
        "--usn",
        default=None,
        help="STUDENT only, and then required: the USN for the Student row. "
        "Use one no roster row will ever claim (e.g. TEST01) â it is UNIQUE, "
        "and a real USN granted here blocks that student's roster seed.",
    )
    parser.add_argument(
        "--password-hash",
        default=None,
        metavar="scrypt:SALT:DIGEST",
        help="also give the account a password, as the HASH printed by "
        "`python -m app.set_password --print-hash`. A password itself is never "
        "accepted here (it would land in shell history and CloudTrail); the hash "
        "is safe to pass, which is what lets the ops-task workflow do this from "
        "the browser. Re-running with a hash rotates the key; without one, "
        "leaves it alone.",
    )
    parser.add_argument(
        "--department-id",
        default=None,
        metavar="ID",
        help="REQUIRED to create an account; optional to update one. The "
        "department this person belongs to, which names their college too "
        "(departments.college_id is NOT NULL). An account filed nowhere "
        "resolves to no college and no batch, and for a STUDENT that means the "
        "mock interview can never preselect a track or produce a score. List "
        "ids with `GET /api/admin/departments`.",
    )
    parser.add_argument(
        "--mentor",
        default=None,
        metavar="EMAIL",
        help="STUDENT only: put the student in this mentor's group (the mentor "
        "must already be granted --with-group), so the mentor can actually see "
        "them (AGENTS.md rule 2).",
    )
    args = parser.parse_args()

    # Said out loud BEFORE writing, for the same reason app/seed_roster.py says
    # it: this module is prod-runnable, and the operator's only defence against
    # granting an ADMIN on the wrong database is being told which one it is.
    print(f"granting {args.role} to {args.email.strip().lower()} on database {db_target()}")

    with SessionLocal() as db:
        try:
            user, created = grant(
                db,
                args.email,
                args.name,
                Role(args.role),
                usn=args.usn,
                with_group=args.with_group,
                password_hash=args.password_hash,
                mentor_email=args.mentor,
                department_id=args.department_id,
                # The operator boundary. See grant()'s refusal.
                require_department=True,
            )
        except ValueError as exc:
            # Reachable from a library caller; argparse's `choices` catches the
            # STUDENT case first on the command line, with its own message.
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        email, role, display_name = user.email, user.role, user.name
        # A STAFF account with no password yet gets an activation link, printed
        # here and emailed if a transport is configured. Students never do —
        # they sign in with Google (option B). --password-hash means the
        # operator already issued a key, so no link.
        activation_link = None
        if role is not Role.STUDENT and not args.password_hash and not (
            user.password_hash or ""
        ).startswith("scrypt:"):
            from . import account_links

            activation_link, _emailed = account_links.issue_activation(
                db, user, created_by_user_id=None
            )

    verb = "created" if created else "updated"
    print(f"{verb}: {email}  role={role.value}  name={display_name}")
    if activation_link:
        print(
            "  Activation link (works once, expires in "
            f"{settings.activation_link_hours // 24} days):"
        )
        print(f"    {activation_link}")
        if settings.mail_configured:
            print(f"  Also emailed to {email}.")
        else:
            print("  No mail transport is configured (SES_FROM_ADDRESS is blank), so hand")
            print("  this link to them directly.")
    if args.password_hash:
        print("  Password set from the supplied hash. This account can now sign in with")
        print("  email + password as well as Google; the password door opens on its own")
        print("  once any account holds a key (app/routers/auth.py:password_door_open).")
    if args.mentor:
        print(f"  Placed in the Mentor group of {args.mentor.strip().lower()}.")
    if not created:
        print("  (address already present â name and role were updated in place)")
    if role is Role.ALUMNI:
        # Says the quiet part out loud: the empty profile is the point, not an
        # oversight, and the first screen they land on is the one that fills it.
        print("  No alumni profile row was created, deliberately - the first-login")
        print("  create-profile form is what this account is meant to land on.")
    if role is Role.MENTOR and args.with_group:
        print("  Mentor group created. Students still need their mentor_id pointed at it")
        print("  before this mentor can see anybody (AGENTS.md rule 2).")
    if role is Role.MENTOR and not args.with_group:
        # Not a failure, but it will be reported as one. `_assert_can_access_student`
        # narrows a MENTOR to their own `Mentor` group, and no group means no
        # students â correctly, per AGENTS.md rule 2, but with nothing on screen
        # to explain it.
        print(
            "  NOTE: this mentor has no Mentor group yet, so they will sign in "
            "and see no students at all. Re-run with --with-group to create it.",
            file=sys.stderr,
        )
    print()
    print("This address may now complete Google sign-in, once GOOGLE_CLIENT_ID and")
    print("GOOGLE_CLIENT_SECRET are set. The DOMAIN IS NOT CHECKED â this row is")
    print("the whole allowlist (GOOGLE_ALLOWED_DOMAIN is a label, not a fence).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
