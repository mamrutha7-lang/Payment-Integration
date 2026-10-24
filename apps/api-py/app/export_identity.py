"""The identity ledger: ``python -m app.export_identity``.

WHAT THIS IS, AND WHY IT IS NOT A BACKUP. A backup of the database answers
"restore last Tuesday". This answers a different question — "let these people
back in, into whatever we build next" — and the two need different artefacts
with different lifecycles, because they fail on different days.

The database backups are excellent and they are all bounded by the same number.
`backupRetentionDays` is validated 1..35 in `infra/cdk/reep_core/stack.py`
because RDS refuses more on its automated backups, and until the archive tier
that ceiling reached the AWS Backup rule and the cross-region copy as well. Even
with the archive tier, every one of those artefacts is a PHYSICAL snapshot: a
block-level image of PostgreSQL 17 on RDS, restorable only by standing up
PostgreSQL 17 on RDS. That is the right tool for "we dropped a table". It is the
wrong tool — eventually an unusable one, when that engine version is retired —
for "we rewrote the application and we still owe three thousand students the
account they already have".

So this module writes the smallest thing that can rebuild a login, in the most
boring format that will still open in ten years: one JSON object per person, one
line each, one file per day. No schema, no engine, no vendor. A person with a
text editor can read it, and a script in any language can replay it.

WHY THESE FIELDS AND NOT THE ROW. Three of them are the whole point and the rest
are what makes them usable:

  * ``person_uuid`` is ``users.id``, which is ``uuid4().hex`` — a string, not an
    autoincrement integer. That is the single property that makes this file
    replayable: the id is globally unique, was never derived from insert order,
    and can be re-inserted into any future schema as the primary key without
    colliding with anything. Every other record in the product hangs off it, so
    a restored academic history re-attaches to a restored login for free.
  * ``password_hash`` is carried VERBATIM. It is ``scrypt:<salt_hex>:<digest_hex>``
    — self-describing, no KMS key, no pepper, no external dependency. The format
    has already survived one whole-stack rewrite (Next.js to FastAPI) without a
    single password reset, which is the empirical case for carrying the string
    rather than inventing a migration. Accounts holding the SSO-only sentinel
    carry it too, because "this account has no password" is itself a fact that
    must survive.
  * ``google_sub`` is the Google principal the row is pinned to. ``email`` is how
    a sign-in FINDS a row; ``sub`` is what proves it is the same person as last
    time. An institutional address is a lease the college re-issues to the next
    intake — see the column's own comment in models/user.py — so a ledger that
    carried only the address would, on replay, hand a graduate's account to
    whoever holds that mailbox now.

INSTITUTION IS EXPORTED AS RESOLVED LABELS, NEVER AS FOREIGN KEYS, and this is
the detail that decides whether the file is worth anything. A future schema will
mint new cohort ids; ``"cohort_id": "a3f91c…"`` would preserve a pointer into a
database that no longer exists, which is a string that looks like data and is
not. ``"college": "BGSCET"`` re-resolves against any catalogue, including by
hand on the worst day.

WHAT IS DELIBERATELY ABSENT. Marks, attendance, uploads, interview transcripts,
mentor notes — everything that makes a student's record valuable and sensitive.
That is not an oversight and it is not a smaller version of the same thing: this
file is meant to live forever, off to one side, readable in a crisis, and every
field it carries is a field somebody has to keep safe forever. Identity is the
minimum that cannot be reconstructed from anything else. The academic history
comes back from a logical dump, keyed by the same ``person_uuid``, and that
ordering is the point — access is restored in minutes from a file measured in
megabytes, and the heavy data follows behind it.

WHERE IT GOES. ``IDENTITY_LEDGER_BUCKET`` blank means "no ledger destination
configured", and then this prints to stdout and says so rather than pretending:
the same honesty rule the voice platform's optional projections keep. Configured,
it writes one object per UTC day at ``<prefix>/YYYY/MM/DD.jsonl``. The bucket is
expected to carry versioning and S3 Object Lock, which is what keeps this file
outside the 35-day rule AND outside the reach of a compromised operator — but
this module does not and cannot enforce that, because a writer that could
weaken its own retention is not a retention control. `infra/cdk` owns the
bucket; see the `IdentityLedgerBucket` construct.

IT NEVER DELETES ANYTHING. There is no prune, no rotate, no `--force`. A day's
object is written once; re-running the same day overwrites that day's file with
a fresh read of the same table, which is idempotent in the only sense that
matters (the newest export of a person is the truest one). The history is the
object versions, which is why versioning is not optional on that bucket.

Exit code 0 means every account was read and the destination accepted the file.
Non-zero means it did not, and a non-zero here is worth waking somebody for:
this is the file that exists so that nothing else has to.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from .config import settings
from .db import SessionLocal
from .models.cohort import Cohort
from .models.institution import (
    AcademicCourse,
    AcademicSpecialization,
    College,
    Department,
)
from .models.user import Student, User

log = logging.getLogger("reep.identity")

#: The ledger's own format version, written on every line.
#:
#: NOT the application version and not a schema migration id: it is a promise
#: about the SHAPE of the object on this line, so that a reader ten years from
#: now can branch on it instead of guessing. Bump it when a field's meaning
#: changes; adding a field does not need a bump, because a reader that does not
#: know a key ignores it and a reader that needs one checks for it.
LEDGER_VERSION = 1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _query():
    """Every account, with its institution resolved to names.

    TWO DEPARTMENT PATHS, JOINED SEPARATELY AND COALESCED, because a student has
    two pointers and they are reached differently: `cohorts.department_id` for a
    seated student, `students.department_id` for one whose batch does not exist
    yet. `governance.ancestry_of_student` reads both for exactly this reason,
    and a ledger that read only the first would silently lose the college of
    every student at an institution that has not built its batches — which is
    the normal state of a new college, not an edge case.

    Staff get the same treatment through `users.department_id`, their own
    counterpart of `students.cohort_id`. A MENTOR row has no `students` row at
    all, so without that third path every faculty member would export with no
    college and the file could not say which institution they belonged to.

    All joins are OUTER. A ledger is not the place to discover that a catalogue
    row was archived: an account with an unresolvable department exports with
    nulls and a line in the log, never as a missing person.
    """
    cohort_dept = aliased(Department)
    cohort_college = aliased(College)
    student_dept = aliased(Department)
    student_college = aliased(College)
    staff_dept = aliased(Department)
    staff_college = aliased(College)

    return (
        select(
            User.id,
            User.email,
            User.name,
            User.role,
            User.password_hash,
            User.google_sub,
            User.created_at,
            User.disabled_at,
            User.disable_reason,
            User.designation,
            Student.usn,
            Student.current_stage,
            Student.current_semester,
            Cohort.batch_label,
            AcademicCourse.name.label("course_name"),
            AcademicSpecialization.name.label("specialization_name"),
            cohort_dept.name.label("cohort_department"),
            cohort_college.name.label("cohort_college"),
            cohort_college.code.label("cohort_college_code"),
            student_dept.name.label("student_department"),
            student_college.name.label("student_college"),
            student_college.code.label("student_college_code"),
            staff_dept.name.label("staff_department"),
            staff_college.name.label("staff_college"),
            staff_college.code.label("staff_college_code"),
        )
        .select_from(User)
        .join(Student, Student.user_id == User.id, isouter=True)
        .join(Cohort, Cohort.id == Student.cohort_id, isouter=True)
        .join(cohort_dept, cohort_dept.id == Cohort.department_id, isouter=True)
        .join(cohort_college, cohort_college.id == cohort_dept.college_id, isouter=True)
        .join(AcademicCourse, AcademicCourse.id == Cohort.course_id, isouter=True)
        .join(
            AcademicSpecialization,
            AcademicSpecialization.id == Cohort.specialization_id,
            isouter=True,
        )
        .join(student_dept, student_dept.id == Student.department_id, isouter=True)
        .join(student_college, student_college.id == student_dept.college_id, isouter=True)
        .join(staff_dept, staff_dept.id == User.department_id, isouter=True)
        .join(staff_college, staff_college.id == staff_dept.college_id, isouter=True)
        # created_at, then id: a stable order across runs, so two days' files
        # diff line-for-line for everyone who did not change.
        .order_by(User.created_at, User.id)
    )


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def compose(row: Any, stamp: str | None) -> dict[str, Any]:
    """One account's ledger line, from one joined row.

    A PURE FUNCTION and separate from the query on purpose: the shape of this
    dict is the part that has to stay true for a decade, so it is the part that
    must be testable without a database. `tests/test_identity_ledger.py` pins it
    against a row built by hand.

    The batch wins over the student's own pointer wherever it has one, and the
    staff pointer is the last resort — the same precedence
    `student_placement.resolve_student_department` enforces on write. Two
    readers disagreeing about which pointer is authoritative is how one person
    ends up filed under two departments on two screens.
    """
    college = row["cohort_college"] or row["student_college"] or row["staff_college"]
    college_code = (
        row["cohort_college_code"]
        or row["student_college_code"]
        or row["staff_college_code"]
    )
    department = (
        row["cohort_department"] or row["student_department"] or row["staff_department"]
    )
    role = row["role"]
    return {
        "ledger_version": LEDGER_VERSION,
        "person_uuid": row["id"],
        "email": row["email"],
        "name": row["name"],
        "role": getattr(role, "value", role),
        "password_hash": row["password_hash"],
        "google_sub": row["google_sub"],
        "usn": row["usn"],
        "designation": row["designation"],
        # Resolved LABELS. See the module docstring: a future schema mints new
        # ids, so an id here is a pointer into a database that is gone.
        "college": college,
        "college_code": college_code,
        "department": department,
        "course": row["course_name"],
        "specialization": row["specialization_name"],
        "batch": row["batch_label"],
        "stage": getattr(row["current_stage"], "value", row["current_stage"]),
        "semester": row["current_semester"],
        "created_at": _iso(row["created_at"]),
        "disabled_at": _iso(row["disabled_at"]),
        # Carried because "why can this person not sign in" is the question a
        # restored deployment will be asked first, and B3.1 refuses an empty
        # reason precisely so the answer exists.
        "disable_reason": row["disable_reason"],
        "exported_at": stamp,
    }


def records(db: Session, *, now: datetime | None = None) -> Iterator[dict[str, Any]]:
    """One dict per account, in the ledger's shape."""
    stamp = _iso(now or _utcnow())
    for row in db.execute(_query()).mappings():
        yield compose(row, stamp)


def render(rows: list[dict[str, Any]]) -> str:
    """JSON Lines. One object per line, newline-terminated.

    JSONL and not a JSON array, because the failure mode matters: a truncated
    array is unparseable in its entirety, while a truncated JSONL file loses
    only its last line and every account above it still reads. For the one file
    whose job is to survive a bad day, that difference is the whole choice.

    `sort_keys` so two days' exports diff cleanly, and `ensure_ascii=False` so a
    name is stored as the person spells it rather than as escapes.
    """
    return "".join(
        json.dumps(r, sort_keys=True, ensure_ascii=False, default=str) + "\n" for r in rows
    )


def object_key(now: datetime | None = None) -> str:
    """``<prefix>/YYYY/MM/DD.jsonl`` — one object per UTC day.

    Date-partitioned rather than one growing object: a re-run replaces that day
    and leaves every other day untouched, and the prefix lists chronologically
    in every console and CLI without a manifest.
    """
    stamp = now or _utcnow()
    prefix = settings.identity_ledger_prefix.strip("/")
    head = f"{prefix}/" if prefix else ""
    return f"{head}{stamp:%Y/%m/%d}.jsonl"


def upload(body: str, key: str) -> str:
    """Put the file in the configured bucket; return the s3:// uri.

    boto3 is imported HERE rather than at module scope for mail_transport.py's
    reason: the dependency is present but nothing else in this module needs it,
    and an operator running `--stdout` on a laptop should not need AWS
    credentials to read their own ledger.
    """
    import boto3

    client = boto3.client("s3", region_name=settings.identity_ledger_region or None)
    client.put_object(
        Bucket=settings.identity_ledger_bucket,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType="application/x-ndjson",
        # The bucket is expected to be SSE-encrypted by policy; asking for
        # AES256 explicitly costs nothing and means a bucket that lost its
        # default still stores this encrypted.
        ServerSideEncryption="AES256",
    )
    return f"s3://{settings.identity_ledger_bucket}/{key}"


def run(db: Session, *, to_stdout: bool = False, now: datetime | None = None) -> dict[str, Any]:
    """Read every account, render the ledger, and put it where it belongs."""
    now = now or _utcnow()
    rows = list(records(db, now=now))
    body = render(rows)
    summary: dict[str, Any] = {
        "accounts": len(rows),
        "bytes": len(body.encode("utf-8")),
        "roles": {},
    }
    for r in rows:
        summary["roles"][r["role"]] = summary["roles"].get(r["role"], 0) + 1

    # An empty export is refused rather than written. A file with no accounts is
    # indistinguishable from a healthy export of an empty deployment, and
    # overwriting today's object with one would destroy a good ledger with a bad
    # one on the day a query regressed. A genuinely empty deployment has nothing
    # to protect, so refusing costs it nothing.
    if not rows:
        raise RuntimeError(
            "No accounts were read, so nothing was written. An empty ledger "
            "would overwrite a good one; refusing. Check the database "
            "connection, then re-run."
        )

    # IT NEVER PRINTS UNASKED, AND THIS IS THE WHOLE OF THE FALLBACK STORY.
    #
    # This used to fall back to stdout whenever no bucket was configured, on the
    # reasoning that a developer running it on a laptop should see something.
    # That reasoning is correct on a laptop and a CREDENTIAL LEAK anywhere else:
    # on Fargate this process's stdout is the `/reep/api` CloudWatch log group,
    # 30-day retention, readable by every role holding `logs:FilterLogEvents` --
    # and the thing being printed is every account's `password_hash` and
    # `google_sub`. The window was real rather than theoretical: the schedule is
    # created in the harden phase while the bucket variable arrives with the ECS
    # half, so between step 9a and 9b of docs/cdk-cutover.md the job would have
    # fired nightly, unconfigured, straight into the log. Found in review by
    # Seer on PR #45 before it ever ran.
    #
    # So an unconfigured run is a FAILURE, not a quieter success. `--stdout` is
    # the only way bytes reach the terminal, and it has to be typed. The
    # schedule is gated on the same condition as the variable now as well
    # (reep_core/stack.py), which closes the same hole a second time; this half
    # is the one that holds no matter how the stack is deployed.
    if not to_stdout and not settings.identity_ledger_bucket.strip():
        raise RuntimeError(
            "IDENTITY_LEDGER_BUCKET is not set, so there is nowhere to put the "
            "ledger. Refusing rather than printing it: this file carries every "
            "account's password hash and Google subject, and on a hosted "
            "runtime stdout is a log group somebody else can read. Set the "
            "bucket, or pass --stdout if you meant to read it here."
        )

    if to_stdout:
        sys.stdout.write(body)
        summary["destination"] = "stdout"
        return summary

    key = object_key(now)
    summary["destination"] = upload(body, key)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.export_identity",
        description=(
            "Write the identity ledger: every account's login facts, as JSON "
            "Lines, to the configured S3 bucket."
        ),
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help=(
            "print the ledger instead of uploading it, even when a bucket is "
            "configured (for inspecting what would be written)"
        ),
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        summary = run(db, to_stdout=args.stdout)
    except Exception:
        log.exception("The identity ledger was NOT written.")
        return 1
    finally:
        db.close()

    log.info(
        "Identity ledger: %d accounts (%s), %d bytes -> %s",
        summary["accounts"],
        ", ".join(f"{k} {v}" for k, v in sorted(summary["roles"].items())),
        summary["bytes"],
        summary["destination"],
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())
