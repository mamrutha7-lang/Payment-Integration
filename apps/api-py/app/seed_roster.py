"""Roster seed — the real MBA batch, and the allowlist Google sign-in checks.

    python -m alembic upgrade head
    python -m app.seed_roster                      # writes
    python -m app.seed_roster --dry-run            # shows what it would write
    python -m app.seed_roster --domain example.edu # override ROSTER_EMAIL_DOMAIN
    python -m app.seed_roster --cohort MBA-2027-A  # attach to an EXISTING cohort
    python -m app.seed_roster --rekey-domain old.edu   # the domain was wrong; move

Separate from `app.seed` for the same reason `app.seed_kb` is, and it is
deliberately NOT guarded on ENV=prod. `app.seed` refuses in production because
it creates demo accounts whose passwords are published in AGENTS.md — including
a DIRECTOR who reads every student's records. Nothing of that kind is here:
these are real students, there is no DIRECTOR, and no account created by this
module has a usable password. Production is exactly where this belongs. Under
Google-only sign-in the rows below ARE the access control — a Google account
with no `users` row is refused at the callback — so a production host without
this seed has 33 students who cannot sign in at all.

Because it is prod-runnable it says out loud, before writing, which database it
is about to write to and how many rows it is about to touch. Running a roster
against the wrong database is the failure this replaces a refusal with.

Creates, per roster row: a User (role STUDENT, no usable password) + its Student
row carrying the USN + an empty StudentProfile. The Student row is not optional
garnish: `_payload_for()` in app/routers/auth.py only puts `studentId` in the
session JWT when `user.student` exists, and every /api/student/* route 403s
without it. The USN lands here so it is ALREADY FILLED IN on the profile screen
(read-only, "Synced" — a student never types it).

Data only — Alembic owns the schema.

Idempotent, and safe to re-run after a student has edited their profile:
  * keyed on the derived email (UNIQUE) and cross-checked on USN (also UNIQUE);
  * an existing account is never overwritten — only genuinely EMPTY things are
    filled in (a missing Student row, a NULL usn, a missing StudentProfile);
  * a name or USN that disagrees with the roster is reported, never corrected,
    because "the roster is newer" is an assumption and the office is the
    authority for identity changes.
"""

import argparse
import sys
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models.cohort import Cohort
from .models.student_profile import StudentProfile
from .models.user import Role, Student, User
from .usn import usn_is

# Last-resort fallback for the email domain. The configured value is
# `settings.roster_domain` (ROSTER_EMAIL_DOMAIN, alias COLLEGE_EMAIL_DOMAIN,
# else the first GOOGLE_ALLOWED_DOMAIN entry). It lives in .env so that a wrong
# convention is an env edit, not a code change: the local part is DERIVED, never
# supplied by the roster, so if the institution's convention differs from "USN,
# lowercased" then EVERY student on this list is locked out of a Google-only
# sign-in at once, and the fix has to ship in minutes.
DEFAULT_COLLEGE_DOMAIN = "bgscet.ac.in"

# Unusable-password sentinel. These are Google-only accounts, but `users
# .password_hash` is NOT NULL in the schema (models/user.py:48 and the original
# migration), so a value must be supplied and it must be one that can never
# authenticate. `verify_password` (app/security.py) splits on ":" and rejects
# anything that is not exactly scrypt:<salt>:<digest>, so this string returns
# False for every password ever tried and POST /api/auth/login answers with its
# ordinary 401 — no crash, no oracle telling an attacker which accounts are
# SSO-only. Nothing else in the codebase writes a non-scrypt password_hash;
# this is new ground, and it is why no migration is needed to ship the roster.
SSO_ONLY_PASSWORD_HASH = "google-only"

# Source document: the MBA 1MP25MDM batch roster (33 students) supplied by the
# department with the Google-only sign-in brief, 2026-08. Transcribed verbatim,
# USN then name as printed. Sequence numbers 11, 23 and 30 are ABSENT from the
# roster as issued — that is the roster, not a transcription slip, and a future
# reader who "fixes" the gap invents three students who do not exist.
ROSTER: list[tuple[str, str]] = [
    ("1MP25MDM01", "A Sazia Sulthana"),
    ("1MP25MDM02", "Akshay R"),
    ("1MP25MDM03", "Bharath Kumar PN"),
    ("1MP25MDM04", "BS Sinchana"),
    ("1MP25MDM05", "Chaithanya R"),
    ("1MP25MDM06", "Chithrashree J"),
    ("1MP25MDM07", "Dhanush V Yadav"),
    ("1MP25MDM08", "Dharshan KS"),
    ("1MP25MDM09", "Harshitha N"),
    ("1MP25MDM10", "KS Koushik"),
    ("1MP25MDM12", "Kavya N"),
    ("1MP25MDM13", "Lekhana S"),
    ("1MP25MDM14", "Likith Kumar"),
    ("1MP25MDM15", "Likitha S"),
    ("1MP25MDM16", "Lingraj S"),
    ("1MP25MDM17", "Madhan KV"),
    ("1MP25MDM18", "Manasa H"),
    ("1MP25MDM19", "Monish Gowda J"),
    ("1MP25MDM20", "Nagaraja R"),
    ("1MP25MDM21", "Niteesh K Bhonsley"),
    ("1MP25MDM22", "Pavan TN"),
    ("1MP25MDM24", "Rachana HD"),
    ("1MP25MDM25", "Rahul K"),
    ("1MP25MDM26", "Rakshitha T"),
    ("1MP25MDM27", "Ranjitha G"),
    ("1MP25MDM28", "Rohan Thammaiah MJ"),
    ("1MP25MDM29", "S V Sai Pranav"),
    ("1MP25MDM31", "Sushma RP"),
    ("1MP25MDM32", "Uday R"),
    ("1MP25MDM33", "Valentina MB"),
    ("1MP25MDM34", "Vedha G"),
    ("1MP25MDM35", "Vishnav Sai G"),
    ("1MP25MDM36", "Yogananda M"),
]


def email_for(usn: str, domain: str) -> str:
    """Derive the institutional email from a USN.

    1MP25MDM01 -> 1mp25mdm01@bgscet.ac.in. The precedent is already in the
    repo: app/seed.py seeds Registration(email="1bg24mba045@bgscet.ac.in",
    usn="1BG24MBA045"). USNs are stored UPPERCASE (the registration rules'
    patterns are uppercase-anchored) and the email local part is the lowercased
    form, so the two are not interchangeable — always go through here.
    """
    return f"{usn.strip().lower()}@{domain}"


def _normalise_domain(value: str) -> str:
    return value.strip().lstrip("@").lower()


def _email_domain(override: str = "") -> tuple[str, str]:
    """Resolve the email domain. Returns (domain, where_it_came_from).

    `settings.roster_domain` is the configured value (ROSTER_EMAIL_DOMAIN, alias
    COLLEGE_EMAIL_DOMAIN, else the first GOOGLE_ALLOWED_DOMAIN entry). It is read
    through `getattr` so that a checkout where that property has not landed yet
    still seeds against the documented default rather than dying on an
    AttributeError; the fallback is the same string config.py defaults to.
    """
    if override.strip():
        return _normalise_domain(override), "--domain"
    configured = _normalise_domain(str(getattr(settings, "roster_domain", "") or ""))
    if configured:
        return configured, "settings.roster_domain"
    return DEFAULT_COLLEGE_DOMAIN, "default"


def _rekey_candidate(db: Session, holder: Student, usn: str, old_domain: str) -> User | None:
    """The user behind `holder`, if it is safe to move to a new email domain.

    THE PROBLEM THIS SOLVES is the one the configurable domain exists for. The
    local part is derived, never supplied by the roster, so if the institution's
    convention turns out to differ then all 33 students are locked out of a
    Google-only sign-in at once — and simply correcting ROSTER_EMAIL_DOMAIN and
    re-running writes NOTHING, because every USN is already held by a row at the
    wrong address and each one is reported as a conflict. Without a recovery
    path here the "fix in minutes" is hand-written SQL against production, which
    is exactly what a production-safe seed module is supposed to remove.

    SAFE means this module created the row and nothing has happened to it since:
    the unusable-password sentinel, role STUDENT, and an address that is exactly
    what the OLD domain derives for this USN. Then the only thing that changes is
    the half that was misconfigured. An account with a real password, a promoted
    role, or an address a human chose is not ours to rename — renaming those
    would move somebody's identity — so it stays a conflict for a human.
    """
    user = db.get(User, holder.user_id)
    if user is None:
        return None
    if user.password_hash != SSO_ONLY_PASSWORD_HASH:
        return None
    # getattr(..., "value", ...) for the same reason the role note below uses it:
    # a row written outside the ORM can surface as a bare string.
    if getattr(user.role, "value", user.role) != Role.STUDENT.value:
        return None
    if user.email != email_for(usn, old_domain):
        return None
    return user


def db_target() -> str:
    """host:port/database of the configured DB, with credentials stripped.

    Printed before any write. This module is prod-runnable, so the operator's
    only defence against pointing a real roster at the wrong database is being
    told which one it is — and a URL printed whole would leak the password into
    a deploy log. Public rather than module-private because app/grant_access.py
    is prod-runnable for the same reason and must say the same thing.
    """
    try:
        parts = urlsplit(settings.sqlalchemy_url)
        host = parts.hostname or "?"
        port = f":{parts.port}" if parts.port else ""
        name = (parts.path or "/?").lstrip("/") or "?"
        return f"{host}{port}/{name}"
    except ValueError:
        return "?"


def seed_roster(
    db: Session,
    rows: list[tuple[str, str]],
    *,
    domain: str = DEFAULT_COLLEGE_DOMAIN,
    rekey_domain: str = "",
    cohort: Cohort | None = None,
    dry_run: bool = False,
) -> int:
    """Idempotently create User + Student + StudentProfile per roster row.

    Takes an existing session so a caller can run it inside its own scope — the
    shape `app.seed_kb.seed_knowledge` established. Returns the number of
    students CREATED (0 on a clean re-run). Prints a created-vs-skipped summary;
    conflicts, which need a human, go to stderr.

    One commit at the end, not per row: a half-applied roster is a worse state
    than none applied, and the whole batch is small enough to be atomic.
    """
    created = 0
    linked = 0          # User already existed, its Student row did not
    usn_filled = 0      # Student existed with usn NULL
    rekeyed = 0         # moved off a previous (wrong) email domain
    profiles_added = 0
    cohort_set = 0
    unchanged = 0
    conflicts = 0

    # Within-run duplicate guard. The session is autoflush=False, and while the
    # explicit flush below does make each new row visible to later queries, a
    # duplicate inside ROSTER itself deserves a named error rather than an
    # IntegrityError from the driver.
    seen_emails: set[str] = set()
    seen_usns: set[str] = set()

    for raw_usn, raw_name in rows:
        usn = raw_usn.strip().upper()
        name = raw_name.strip()
        if not usn or not name:
            print(f"skipped a roster row with a blank USN or name: {raw_usn!r}", file=sys.stderr)
            conflicts += 1
            continue

        email = email_for(usn, domain)
        if email in seen_emails or usn in seen_usns:
            print(f"skipped {usn}: duplicated inside the roster constant", file=sys.stderr)
            conflicts += 1
            continue
        seen_emails.add(email)
        seen_usns.add(usn)

        user = db.scalar(select(User).where(User.email == email))

        if user is None:
            # USN is UNIQUE too: a roster typo that reuses a USN under a new
            # email would fail the whole commit rather than no-op quietly.
            holder = db.scalar(select(Student).where(usn_is(Student.usn, usn)))
            if holder is not None:
                # This is also the state a CORRECTED domain leaves behind: the
                # student exists, at the address the wrong domain derived. With
                # --rekey-domain, and only for a row this module created at
                # exactly that old derived address, the address is moved.
                # Without it, it stays a conflict a human must look at.
                stale = _rekey_candidate(db, holder, usn, rekey_domain) if rekey_domain else None
                if stale is None:
                    print(
                        f"skipped {usn} ({email}): that USN already belongs to another account",
                        file=sys.stderr,
                    )
                    conflicts += 1
                    continue
                if dry_run:
                    print(f"would move {stale.email} -> {email} ({usn})")
                else:
                    print(f"moved {stale.email} -> {email} ({usn})")
                    stale.email = email
                rekeyed += 1
                continue
            if dry_run:
                print(f"would create {email} ({usn}, {name})")
                created += 1
                continue
            user = User(
                email=email,
                name=name,
                role=Role.STUDENT,
                password_hash=SSO_ONLY_PASSWORD_HASH,
            )
            db.add(user)
            db.flush()  # populates user.id
            stu = Student(user_id=user.id, usn=usn)
            if cohort is not None:
                # Plain nullable String, NOT a foreign key (models/user.py) —
                # nothing in the database validates it, so it is set from a
                # Cohort row this module looked up, never from a literal.
                stu.cohort_id = cohort.id
            db.add(stu)
            db.flush()  # populates stu.id
            # Every other profile column is nullable or python-defaulted, so a
            # bare row inserts cleanly and gives the profile screen something
            # to update on the student's first save.
            db.add(StudentProfile(student_id=stu.id))
            created += 1
            continue

        # The account exists. From here on: fill in what is EMPTY, report what
        # disagrees, overwrite nothing. A student may have edited their profile
        # since the last run.
        touched = False

        # getattr(..., "value", ...): the ORM hands back a Role member, but a
        # row written outside the ORM can surface as a bare string, and a
        # crash on `.value` here would abort the whole roster over one note.
        existing_role = getattr(user.role, "value", user.role)
        if existing_role != Role.STUDENT.value:
            print(
                f"note {email}: existing account has role {existing_role}, left as is",
                file=sys.stderr,
            )
            conflicts += 1
        if user.name != name:
            print(
                f"note {email}: name is {user.name!r}, roster says {name!r} — left as is",
                file=sys.stderr,
            )
            conflicts += 1

        stu = db.scalar(select(Student).where(Student.user_id == user.id))
        if stu is None:
            holder = db.scalar(select(Student).where(usn_is(Student.usn, usn)))
            if holder is not None:
                print(
                    f"skipped {usn} ({email}): that USN already belongs to another account",
                    file=sys.stderr,
                )
                conflicts += 1
                continue
            if dry_run:
                print(f"would link a Student row to {email} ({usn})")
                linked += 1
                continue
            # A User with no Student row signs in fine and gets a session with
            # no studentId — every /api/student/* route then 403s. Repair it.
            stu = Student(user_id=user.id, usn=usn)
            if cohort is not None:
                stu.cohort_id = cohort.id
            db.add(stu)
            db.flush()
            linked += 1
            touched = True
        elif stu.usn is None:
            holder = db.scalar(select(Student).where(usn_is(Student.usn, usn)))
            if holder is not None:
                print(
                    f"skipped USN backfill for {email}: {usn} already belongs to another account",
                    file=sys.stderr,
                )
                conflicts += 1
            elif dry_run:
                print(f"would backfill USN {usn} on {email}")
                usn_filled += 1
                touched = True
            else:
                stu.usn = usn
                usn_filled += 1
                touched = True
        elif stu.usn != usn:
            print(
                f"note {email}: USN is {stu.usn}, roster says {usn} — left as is",
                file=sys.stderr,
            )
            conflicts += 1

        if cohort is not None and stu is not None and stu.cohort_id != cohort.id:
            if dry_run:
                print(f"would set cohort {cohort.code} on {email}")
            else:
                stu.cohort_id = cohort.id
            cohort_set += 1
            touched = True

        if stu is not None:
            profile = db.scalar(
                select(StudentProfile).where(StudentProfile.student_id == stu.id)
            )
            if profile is None:
                if dry_run:
                    print(f"would add an empty profile for {email}")
                else:
                    db.add(StudentProfile(student_id=stu.id))
                profiles_added += 1
                touched = True

        if not touched:
            unchanged += 1

    changed = created + linked + usn_filled + rekeyed + profiles_added + cohort_set
    if dry_run:
        print(f"dry run — nothing written ({changed} changes pending)")
    elif changed:
        db.commit()

    verb = "would create" if dry_run else "created"
    print(f"{verb} students ({created} of {len(rows)} roster rows)")
    if linked:
        print(f"linked Student rows onto existing users ({linked})")
    if usn_filled:
        print(f"backfilled USNs ({usn_filled})")
    if rekeyed:
        print(f"moved onto the new email domain ({rekeyed})")
    if profiles_added:
        print(f"added empty profiles ({profiles_added})")
    if cohort_set and cohort is not None:
        print(f"assigned cohort {cohort.code} ({cohort_set})")
    if unchanged:
        print(f"already present, unchanged ({unchanged})")
    if conflicts:
        print(f"needs a human ({conflicts}, listed above on stderr)")
    return created


def main() -> None:
    # No ENV=prod refusal here, unlike app.seed. See the module docstring: this
    # roster is real institutional data that production needs, and under
    # Google-only sign-in it is the allowlist. What replaces the guard is
    # printing the target database and the row count before writing.
    parser = argparse.ArgumentParser(
        prog="python -m app.seed_roster",
        description="Create the MBA roster's student accounts (Google-only, no passwords).",
    )
    parser.add_argument(
        "--domain",
        default="",
        help=(
            "email domain for derived addresses (default: ROSTER_EMAIL_DOMAIN, "
            f"alias COLLEGE_EMAIL_DOMAIN, else {DEFAULT_COLLEGE_DOMAIN})"
        ),
    )
    parser.add_argument(
        "--rekey-domain",
        default="",
        help=(
            "the PREVIOUS email domain. Moves accounts this module created from "
            "<usn>@OLD to <usn>@NEW instead of reporting a USN conflict — the "
            "recovery path for the day the college's convention turns out to "
            "differ. Pair it with --dry-run first."
        ),
    )
    parser.add_argument(
        "--cohort",
        default="",
        help="code of an EXISTING cohort to attach these students to (see cohorts.code)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change and write nothing",
    )
    args = parser.parse_args()

    domain, source = _email_domain(args.domain)
    print(
        f"roster: {len(ROSTER)} students, "
        f"emails @{domain} (from {source}), "
        f"database {db_target()}"
    )

    db = SessionLocal()
    try:
        cohort: Cohort | None = None
        if args.cohort.strip():
            code = args.cohort.strip()
            # Looked up, never created. Cohort's six NOT NULL columns include
            # start_date/end_date, and inventing a batch's dates here would put
            # fiction in the database — create the cohort deliberately, then
            # name it with --cohort.
            cohort = db.scalar(select(Cohort).where(Cohort.code == code))
            if cohort is None:
                print(
                    f"REFUSED: no cohort with code {code}. Create it first, or "
                    f"re-run without --cohort (students then have no cohort and "
                    f"leaderboards/alerts stay dark for them).",
                    file=sys.stderr,
                )
                raise SystemExit(1)

        seed_roster(
            db,
            ROSTER,
            domain=domain,
            rekey_domain=_normalise_domain(args.rekey_domain),
            cohort=cohort,
            dry_run=args.dry_run,
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
