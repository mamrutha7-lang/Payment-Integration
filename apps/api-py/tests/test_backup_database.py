"""The logical backup's contract, pinned without a database or a pg_dump.

`app/backup_database.py` runs once a night, unattended, and produces the file
that makes a rewrite or a vendor exit tractable. The properties that make it
worth having are the ones a test has to assert: that the database password never
reaches a command line, that a dump which is not restorable is never uploaded,
and that an unconfigured run refuses instead of quietly doing nothing.

Everything here runs on fakes. The subprocesses need PostgreSQL 17's client
binaries and the uploads need AWS; the CONTRACT needs neither, and the contract
is the part with a ten-year job.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import backup_database

NOW = datetime(2026, 9, 15, 19, 30, tzinfo=timezone.utc)

#: A `pg_restore --list` TOC as the real tool prints one, trimmed.
GOOD_TOC = """;
; Archive created at 2026-09-15 19:30:00 UTC
;     dbname: reep_py
;
215; 1259 16400 TABLE public users reep
216; 1259 16410 TABLE public students reep
3201; 0 16400 TABLE DATA public users reep
3202; 0 16410 TABLE DATA public students reep
3203; 0 16420 TABLE DATA public marks reep
"""


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ------------------------------------------------- the password never travels --


def test_the_password_becomes_an_environment_variable() -> None:
    """libpq's PG* variables, never a connection string.

    `app.set_password` makes this argument about a student's password and it is
    the same argument about the database's: argv is readable in `ps`, in the
    container's own process table, and in the CloudTrail record of an ECS task
    override. A dump job is the one place a `--dbname=postgres://user:pass@...`
    looks harmless.
    """
    env = backup_database.libpq_env(
        "postgresql+psycopg://reep:s3cr3t@db.internal:5432/reep_py"
    )
    assert env["PGPASSWORD"] == "s3cr3t"
    assert env["PGHOST"] == "db.internal"
    assert env["PGDATABASE"] == "reep_py"
    assert env["PGUSER"] == "reep"


def test_the_sqlalchemy_driver_suffix_is_dropped() -> None:
    """`+psycopg` is SQLAlchemy's and means nothing to libpq."""
    env = backup_database.libpq_env("postgresql+psycopg://u:p@h:5432/d")
    assert "+psycopg" not in "".join(env.values())


def test_pg_dump_is_invoked_with_no_secret_in_argv(monkeypatch, tmp_path) -> None:
    """The assertion that would catch a regression: look at the argv itself.

    Checking only that PGPASSWORD is set would pass on a version that set it AND
    also passed a URL on the command line.
    """
    seen: dict[str, object] = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        Path(cmd[cmd.index("--file") + 1]).write_bytes(b"PGDMP fake archive")
        return _Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = tmp_path / "x.dump"
    backup_database.dump(out, {"PGPASSWORD": "s3cr3t"})
    assert "s3cr3t" not in " ".join(str(c) for c in seen["cmd"])


def test_a_url_with_no_database_is_refused() -> None:
    with pytest.raises(RuntimeError, match="names no database"):
        backup_database.libpq_env("postgresql+psycopg://u:p@h:5432/")


def test_the_refusal_does_not_echo_the_password() -> None:
    """An error message is a log line, and a log line is forever."""
    with pytest.raises(RuntimeError) as exc:
        backup_database.libpq_env("postgresql+psycopg://u:s3cr3t@h:5432/")
    assert "s3cr3t" not in str(exc.value)


# ----------------------------------------------- nothing unverified is stored --


def test_a_failed_dump_raises_rather_than_returning(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Completed(1, stderr="connection refused")
    )
    with pytest.raises(RuntimeError, match="pg_dump failed"):
        backup_database.dump(tmp_path / "x.dump", {})


def test_an_unparseable_archive_is_refused(monkeypatch, tmp_path) -> None:
    """THE `.partial` RULE, MOVED TO WHERE IT STILL WORKS.

    The compose sidecar writes `<name>.partial` and renames on success. On S3
    there is no rename, and worse -- on an Object-Locked bucket a `.partial`
    object would be kept undeletable for the whole retention period. So the
    check moves ahead of the upload: read the table of contents back.
    """
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _Completed(1, stderr="did not find magic string in file header"),
    )
    with pytest.raises(RuntimeError, match="will not parse"):
        backup_database.verify(tmp_path / "x.dump")


def test_an_archive_without_the_accounts_is_refused(monkeypatch, tmp_path) -> None:
    """A valid archive of the wrong thing is the failure a header check misses.

    `pg_dump` pointed at an empty database exits 0 and writes a perfectly
    well-formed archive. Stored nightly, it reads as a healthy backup until the
    day somebody restores it and finds no people in it.
    """
    toc = GOOD_TOC.replace("TABLE DATA public users reep\n", "")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed(0, stdout=toc))
    with pytest.raises(RuntimeError, match="carries no data for users"):
        backup_database.verify(tmp_path / "x.dump")


def test_a_good_archive_verifies(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed(0, stdout=GOOD_TOC))
    assert backup_database.verify(tmp_path / "x.dump") > 0


# ---------------------------------------------------------------- the refusal --


def test_an_unconfigured_run_refuses_before_taking_the_dump(monkeypatch) -> None:
    """Refused FIRST, not after -- and that ordering is the point.

    A dump with nowhere to go is many minutes of load on the production
    database, at the hour the retention sweep and the identity ledger also run,
    for a file that is then thrown away. There is deliberately no stdout
    fallback here at all: unlike the ledger there is nothing a human would ever
    want printed, and `export_identity`'s own fallback was a credential leak.
    """
    monkeypatch.setattr(backup_database.settings, "db_dump_bucket", "", raising=False)
    called: list[object] = []
    monkeypatch.setattr(backup_database, "dump", lambda *a, **k: called.append(1))
    with pytest.raises(RuntimeError, match="DB_DUMP_BUCKET is not set"):
        backup_database.run(now=NOW)
    assert called == [], "the dump was taken before the destination was checked"


# ------------------------------------------------------------------- the keys --


def test_the_two_tiers_never_share_a_key_space(monkeypatch) -> None:
    """Separate prefixes, so the two lifecycles can never be aimed at one set."""
    monkeypatch.setattr(backup_database.settings, "db_dump_prefix", "", raising=False)
    assert backup_database.daily_key(NOW) == "daily/2026/09/15.dump"
    assert backup_database.monthly_key(NOW) == "monthly/2026/09.dump"


def test_a_blank_prefix_never_produces_a_leading_slash(monkeypatch) -> None:
    monkeypatch.setattr(backup_database.settings, "db_dump_prefix", "", raising=False)
    assert not backup_database.daily_key(NOW).startswith("/")


# -------------------------------------------------------- the monthly is first --


def test_a_missing_month_reports_absent(monkeypatch) -> None:
    """THE ARCHIVE IS THE FIRST SUCCESSFUL DUMP OF THE MONTH, not the 1st's.

    Keyed to the 1st, a month whose 1st failed has no archive copy at all, and
    the gap is invisible for years because the daily tier still looks healthy.
    Asking the bucket makes the rule self-healing.
    """

    class _Empty:
        def list_objects_v2(self, **kw):
            return {"KeyCount": 0}

    monkeypatch.setattr(
        backup_database.settings, "db_dump_archive_bucket", "b", raising=False
    )
    assert backup_database.month_is_archived(_Empty(), "monthly/2026/09.dump") is False


def test_a_present_month_reports_held(monkeypatch) -> None:
    class _Held:
        def list_objects_v2(self, **kw):
            return {"Contents": [{"Key": "monthly/2026/09.dump"}]}

    monkeypatch.setattr(
        backup_database.settings, "db_dump_archive_bucket", "b", raising=False
    )
    assert backup_database.month_is_archived(_Held(), "monthly/2026/09.dump") is True


def test_a_near_miss_key_is_not_read_as_a_match(monkeypatch) -> None:
    """Prefix search, exact-key answer.

    `list_objects_v2(Prefix=...)` matches by prefix, so a later key scheme that
    appended anything -- a checksum, a part number -- would make this report a
    month archived when the object it names does not exist.
    """

    class _Nearby:
        def list_objects_v2(self, **kw):
            return {"Contents": [{"Key": "monthly/2026/09.dump.meta"}]}

    monkeypatch.setattr(
        backup_database.settings, "db_dump_archive_bucket", "b", raising=False
    )
    assert backup_database.month_is_archived(_Nearby(), "monthly/2026/09.dump") is False


def test_the_job_never_asks_for_object_contents(monkeypatch) -> None:
    """The check that keeps the IAM grant honest.

    `head_object` is authorised by s3:GetObject, so using it would mean granting
    this task read access to every archived dump -- a complete copy of every
    student record, reachable past every control on the database itself. If a
    later edit reaches for the obvious API, this fails rather than quietly
    requiring a wider policy.
    """

    class _Strict:
        def list_objects_v2(self, **kw):
            return {"KeyCount": 0}

        def head_object(self, **kw):  # pragma: no cover - must never be called
            raise AssertionError("month_is_archived used HeadObject; it needs GetObject")

        def get_object(self, **kw):  # pragma: no cover - must never be called
            raise AssertionError("the backup job must never read an object back")

    monkeypatch.setattr(
        backup_database.settings, "db_dump_archive_bucket", "b", raising=False
    )
    assert backup_database.month_is_archived(_Strict(), "monthly/2026/09.dump") is False
