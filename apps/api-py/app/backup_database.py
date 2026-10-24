"""The logical backup: ``python -m app.backup_database``.

WHAT THIS IS, AND WHY THE RDS SNAPSHOTS ARE NOT IT. The database already has
excellent backups: automated RDS snapshots, an AWS Backup daily rule, a
cross-region copy to Singapore, and since `archiveRetentionDays` a monthly rule
measured in years. Every one of them is a PHYSICAL backup -- a block-level image
of PostgreSQL 17 on RDS, restorable only by standing up PostgreSQL 17 on RDS.
That is the right tool for "we dropped a table at 11am" and it is the wrong tool
for the two days this module exists for:

  * THE ENGINE IS RETIRED, or the deployment moves off RDS, or off AWS. A
    physical snapshot is unreadable anywhere else; there is no tool that opens
    one without the vendor.
  * THE APPLICATION IS REWRITTEN. `pg_restore --table=users --table=students`
    into whatever the new thing is, at any time, without recreating the old
    schema first. A snapshot restores the database it came from and nothing
    else.

So this writes `pg_dump -Fc` -- PostgreSQL's own portable archive format,
compressed, with a table of contents, restorable selectively and across major
versions -- to object storage that the database's own deletion cannot reach.

IT IS THE SECOND HALF OF A PAIR. `app/export_identity.py` carries identity:
`person_uuid`, the scrypt hash, the Google subject, the institution as labels.
It is megabytes and it comes back in minutes. This carries EVERYTHING ELSE --
marks, attendance, uploads' metadata, interview records, mentor notes, consent
rows -- keyed by the same `users.id`, and it is the heavy half. The ordering is
deliberate and it is the whole reason they are two files: on the worst day,
access is restored from the ledger while this is still downloading, so nobody
is locked out waiting on a multi-gigabyte restore.

WHY NOT RDS'S OWN SNAPSHOT EXPORT TO S3. `rds:StartExportTask` writes Parquet to
S3 and is genuinely engine-independent, which makes it a real candidate and
worth saying why it was not taken. Parquet is COLUMN DATA WITHOUT THE SCHEMA:
no DDL, no constraints, no foreign keys, no sequences, no extension declarations
-- so restoring from it means somebody hand-writing a schema that matches, which
is the work this artefact exists to avoid doing under pressure. It also needs a
customer-managed KMS key and bills per gigabyte scanned. `-Fc` keeps the
structure and the data in one file that PostgreSQL itself knows how to read.

TWO BUCKETS, AND THEY ARE TWO BECAUSE OBJECT LOCK IS BUCKET-WIDE. The daily
tier keeps 90 days and must then delete; the archive tier keeps the first dump
of each month for years and must never delete. S3's default Object Lock
retention is a property of the BUCKET, not of a prefix, so one bucket cannot
hold both promises -- and the failure is silent in the worst direction: a
lifecycle expiration aimed at an object whose lock has not expired is not an
error, it is a rule that re-evaluates every day, deletes nothing, and reports
success, so the bucket grows forever behind a console that shows a rule working.
Two buckets, one sentence each, and the writer holds `s3:PutObject` on both and
nothing else -- no delete, no `PutObjectRetention`, for the identity ledger's
reason: a writer that can shorten its own retention is not being protected by
Object Lock.

THE `.partial` RULE DOES NOT PORT, AND COPYING IT WOULD BE ACTIVELY HARMFUL.
`docker-compose.prod.yml`'s sidecar writes `<name>.partial` and renames on
success, so a dump the process died inside can never be mistaken for a good one.
On S3 the rename half is unnecessary -- a `PutObject` that does not complete
leaves no object, and a multipart upload is invisible until it is completed --
and the `.partial` half is a TRAP: an Object-Locked bucket would keep that
truncated file for the full retention period, undeletable, forever. What the
rule is really protecting against is not a half-written FILE, it is a half-made
DUMP that uploaded perfectly. So the discipline is kept and moved earlier: the
dump is verified BEFORE it is uploaded, by reading its table of contents back
with `pg_restore --list` and insisting the accounts are in it. Nothing reaches
either bucket that has not been opened and checked.

THE CLIENT MUST BE THE SERVER'S MAJOR VERSION. `pg_dump` refuses to dump a
server newer than itself, and this is the good failure -- it stops rather than
producing something subtly wrong. Debian bookworm ships PostgreSQL 16's client
and the database is 17, so the api image installs `postgresql-client-17` from
PGDG explicitly. That pin and `engine_version` in `infra/cdk/reep_core/stack.py`
are one fact written in two files; `tests/test_codebase_guards.py` compares them,
the same way it already compares the deregistration delay against
`nova_sonic_connection_seconds`. An engine upgrade that forgets the Dockerfile
does not break the API -- it breaks only this job, at 01:00, quietly.

THE PASSWORD NEVER REACHES ARGV. libpq's `PG*` environment variables are used
rather than a connection string on the command line, which is the compose
sidecar's arrangement and `app/set_password`'s argument applied here: a
`--dbname=postgresql://user:pass@...` puts the database password in `ps`, in the
container's own process table, and in the CloudTrail record of an ECS task
override.

Exit code 0 means a verified dump reached the daily bucket. Non-zero means it
did not, and nothing partial was left anywhere.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from .config import settings

log = logging.getLogger("reep.backup")

#: Table-of-contents entries that must be present for a dump to be accepted.
#:
#: NOT a byte floor. "Bigger than N megabytes" is a number somebody has to keep
#: true as the deployment grows and which is wrong on the first day of a new
#: college, and it does not distinguish a truncated archive from a small one.
#: These two names are the ones the whole restore story hangs off -- the ledger
#: replays `users`, and everything else in the product hangs off `students` --
#: so a dump that does not carry their DATA is not a dump worth keeping,
#: whatever it weighs.
REQUIRED_TABLE_DATA = ("users", "students")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def libpq_env(database_url: str) -> dict[str, str]:
    """SQLAlchemy's URL as libpq's own environment variables.

    A PURE FUNCTION, so the one piece of parsing here is testable without a
    database or a subprocess. `+psycopg` is SQLAlchemy's driver suffix and means
    nothing to libpq, so it is dropped rather than passed on.

    The password goes in the environment and NEVER on the command line. See the
    module docstring: argv is readable in `ps`, and an ECS task override's
    command is recorded verbatim in CloudTrail.
    """
    url = make_url(database_url)
    if not url.database:
        raise RuntimeError(
            f"DATABASE_URL names no database: {url.render_as_string(hide_password=True)}"
        )
    env = {
        "PGHOST": url.host or "localhost",
        "PGPORT": str(url.port or 5432),
        "PGDATABASE": url.database,
        # RDS terminates TLS and the instance is not publicly accessible, but
        # `require` costs nothing and means a future move to a reachable host
        # does not silently downgrade to plaintext on the wire.
        "PGSSLMODE": os.environ.get("PGSSLMODE", "require"),
    }
    if url.username:
        env["PGUSER"] = url.username
    if url.password:
        env["PGPASSWORD"] = str(url.password)
    return env


def dump(path: Path, env: dict[str, str]) -> int:
    """`pg_dump -Fc` into `path`. Returns the file's size in bytes.

    Custom format, and deliberately WITH owners and ACLs. They name the RDS
    master role, which will not exist wherever this is eventually restored -- but
    `pg_restore --no-owner --no-acl` discards them at restore time, and a dump
    that discarded them at WRITE time could never be restored faithfully into a
    rebuilt copy of this same deployment. Keep the information; choose later.
    """
    proc = subprocess.run(
        ["pg_dump", "--format=custom", "--file", str(path)],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "pg_dump failed and nothing was uploaded. "
            f"exit={proc.returncode} stderr={proc.stderr.strip()[:2000]}"
        )
    return path.stat().st_size


def verify(path: Path) -> int:
    """Read the archive's table of contents back. Returns the entry count.

    THIS IS THE `.partial` RULE, MOVED TO WHERE IT STILL WORKS. `pg_dump`
    exiting 0 is necessary and not sufficient: a truncated or corrupt archive is
    what the compose sidecar's rename guarded against, and on S3 there is no
    rename to guard with. `pg_restore --list` parses the header and the whole
    TOC, so it fails on exactly the damage that matters, and asserting the
    accounts are IN that list catches the other half -- a technically valid
    archive of the wrong thing.
    """
    proc = subprocess.run(
        ["pg_restore", "--list", str(path)], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "The dump was written but its table of contents will not parse, so "
            "it is not restorable and was NOT uploaded. "
            f"exit={proc.returncode} stderr={proc.stderr.strip()[:2000]}"
        )
    toc = proc.stdout
    missing = [
        name for name in REQUIRED_TABLE_DATA if f"TABLE DATA public {name} " not in toc
    ]
    if missing:
        raise RuntimeError(
            "The dump parses but carries no data for "
            + ", ".join(missing)
            + ". A backup without the accounts is not a backup of this product; "
            "refusing to upload it rather than storing something that reads as a "
            "good dump until the day somebody restores it."
        )
    return sum(1 for line in toc.splitlines() if line and not line.startswith(";"))


def daily_key(now: datetime) -> str:
    """``<prefix>/daily/YYYY/MM/DD.dump`` -- one object per UTC day."""
    prefix = settings.db_dump_prefix.strip("/")
    head = f"{prefix}/" if prefix else ""
    return f"{head}daily/{now:%Y/%m/%d}.dump"


def monthly_key(now: datetime) -> str:
    """``<prefix>/monthly/YYYY/MM.dump`` -- one object per UTC month."""
    prefix = settings.db_dump_prefix.strip("/")
    head = f"{prefix}/" if prefix else ""
    return f"{head}monthly/{now:%Y/%m}.dump"


def _client():
    """boto3, imported here rather than at module scope.

    `export_identity.upload`'s reason: nothing else in this module needs it, and
    `--dry-run` on a laptop should not require AWS credentials to tell an
    operator whether their dump is sound.
    """
    import boto3

    return boto3.client("s3", region_name=settings.db_dump_region or None)


def month_is_archived(client: Any, key: str) -> bool:
    """Has this month's archive object already been written?

    THE ARCHIVE IS THE FIRST SUCCESSFUL DUMP OF THE MONTH, not the dump taken on
    the first of the month. Those are the same thing only when nothing goes
    wrong: keying it to the 1st means a month whose 1st failed -- a bad deploy, a
    maintenance window, a full disk -- has NO archive copy at all, and the gap is
    invisible for years because the daily tier still looks healthy. Asking the
    bucket instead makes the rule self-healing; the 2nd fills in for the 1st.

    LIST, NOT HEAD, AND THE DIFFERENCE IS THE TASK'S ENTIRE READ ACCESS.
    `head_object` is authorised by `s3:GetObject` -- S3 has no separate
    permission for it -- so asking the obvious way would mean granting this task
    read access to every archived dump. That is a complete copy of every student
    record in the deployment, and a task that can read it is one compromise away
    from exfiltrating the whole database from the BACKUPS rather than from the
    database, past every control on the database itself. `list_objects_v2`
    answers the same question under `s3:ListBucket`, which returns key names and
    never contents. The grant in infra/cdk is scoped to this prefix.
    """
    resp = client.list_objects_v2(
        Bucket=settings.db_dump_archive_bucket, Prefix=key, MaxKeys=1
    )
    # An exact-key match, not a prefix match: "monthly/2026/09.dump" is a prefix
    # of nothing else today, but a later key scheme that added a suffix would
    # make this quietly answer "already archived" for a month that is not.
    return any(o.get("Key") == key for o in resp.get("Contents", []))


def run(*, dry_run: bool = False, now: datetime | None = None) -> dict[str, Any]:
    """Dump, verify, upload. Returns a summary; raises on any failure."""
    now = now or _utcnow()
    summary: dict[str, Any] = {"taken_at": now.isoformat()}

    # REFUSED BEFORE THE DUMP IS TAKEN, not after. This is the same refusal
    # `export_identity.run` makes and for a stronger reason: a dump with nowhere
    # to go is many minutes of load on the production database, at the hour the
    # retention sweep and the ledger also run, for a file that is then deleted.
    # Failing first costs nothing and says exactly what is missing.
    if not dry_run and not settings.db_dump_bucket.strip():
        raise RuntimeError(
            "DB_DUMP_BUCKET is not set, so there is nowhere to put the dump and "
            "no reason to take one. This file is a complete copy of every "
            "student record in the deployment -- it is never written anywhere "
            "but the configured bucket, and there is deliberately no fallback. "
            "Set the bucket, or pass --dry-run to check the dump path works."
        )

    env = libpq_env(settings.database_url)

    # NamedTemporaryFile in the task's own filesystem, deleted on the way out
    # whatever happens. Fargate's ephemeral storage is 20 GiB and this dump is
    # compressed, so the headroom is large -- but the file is EVERY student's
    # record in one place, so it lives exactly as long as the upload and not one
    # step of the function longer.
    with tempfile.TemporaryDirectory(prefix="reep-dump-") as tmp:
        path = Path(tmp) / f"reep-{now:%Y%m%dT%H%M%SZ}.dump"
        summary["bytes"] = dump(path, env)
        summary["toc_entries"] = verify(path)
        log.info(
            "Dump verified: %d bytes, %d TOC entries",
            summary["bytes"],
            summary["toc_entries"],
        )

        if dry_run:
            summary["destination"] = "discarded (--dry-run)"
            return summary

        client = _client()
        key = daily_key(now)
        with path.open("rb") as fh:
            client.put_object(
                Bucket=settings.db_dump_bucket,
                Key=key,
                Body=fh,
                ContentType="application/octet-stream",
                # STANDARD_IA on the way in rather than by a lifecycle
                # transition. A transition to IA cannot happen before day 30,
                # so a rule would leave the first month in Standard and bill for
                # it; and this tier is read at most once, on the worst day of
                # the year. What it must NOT be is Glacier: the artefact you
                # most need in a crisis must not have a retrieval time measured
                # in hours. That is the archive tier's trade, made knowingly,
                # and this tier is the one that answers the same night.
                StorageClass="STANDARD_IA",
                ServerSideEncryption="AES256",
            )
        summary["destination"] = f"s3://{settings.db_dump_bucket}/{key}"

        archive_bucket = settings.db_dump_archive_bucket.strip()
        if not archive_bucket:
            # Honest rather than silent, the voice platform's rule for every
            # optional projection: an unconfigured archive tier is a deployment
            # keeping 90 days, and it must say so on every run rather than
            # letting a reader assume the monthly copy exists.
            summary["archived"] = "no archive bucket configured -- 90 days only"
            return summary

        mkey = monthly_key(now)
        if month_is_archived(client, mkey):
            summary["archived"] = f"already held for {now:%Y-%m}"
            return summary

        with path.open("rb") as fh:
            client.put_object(
                Bucket=archive_bucket,
                Key=mkey,
                Body=fh,
                ContentType="application/octet-stream",
                # DEEP_ARCHIVE, and uploaded a second time rather than
                # server-side copied from the daily object. A CopyObject would
                # save the bytes and would make the archive tier DEPEND on the
                # daily object still existing and still being readable -- which
                # is precisely the assumption this tier exists to not make. The
                # two buckets are independent on purpose; the cost is one extra
                # upload of an already-compressed file, once a month.
                StorageClass="DEEP_ARCHIVE",
                ServerSideEncryption="AES256",
            )
        summary["archived"] = f"s3://{archive_bucket}/{mkey}"

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.backup_database",
        description=(
            "Take a logical pg_dump of the database, verify it is restorable, "
            "and upload it to the configured Object-Locked bucket."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "take the dump and verify it, then throw it away without uploading "
            "(for checking the path works before trusting it)"
        ),
    )
    args = parser.parse_args(argv)

    try:
        summary = run(dry_run=args.dry_run)
    except Exception:
        # The message matters more here than anywhere else in the product: this
        # is the artefact that exists so that nothing else has to, and a night
        # it did not run is a night with no logical copy of the database.
        log.exception("The logical backup was NOT taken.")
        return 1

    log.info(
        "Logical backup: %d bytes, %d TOC entries -> %s (archive: %s)",
        summary["bytes"],
        summary["toc_entries"],
        summary["destination"],
        summary.get("archived", "n/a"),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    raise SystemExit(main())
