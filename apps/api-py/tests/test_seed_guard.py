"""`app.seed` must never create demo credentials on a production host.

It creates director@bgscet.ac.in / director123 — a DIRECTOR, who by AGENTS.md
rule 2 reads EVERY student's marks, attendance and USN — behind a password
printed in the repo's own README. The guard is one `if`, which is exactly the
kind of thing a later refactor drops without noticing, so it is pinned here.

Run out-of-process: `settings` is read at import time, so ENV has to be set
before the interpreter starts. This costs nothing in DB terms — the guard fires
before SessionLocal() is ever called, which is itself part of the contract.
"""

import subprocess
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parent.parent


def _run(module: str, env_value: str) -> subprocess.CompletedProcess:
    import os

    env = dict(os.environ, ENV=env_value)
    # Point at a database that cannot exist. If the guard works, nothing ever
    # tries to connect and this is invisible; if it is removed, the test fails
    # on the connection rather than passing by accident against a real DB.
    #
    # connect_timeout is load-bearing, not decoration: libpq on Windows does not
    # get a refusal from 127.0.0.1:1 and waits indefinitely, which turned this
    # into a hang instead of a failure. It also depends on Settings.sqlalchemy_url
    # PRESERVING query parameters — it used to strip every one of them.
    env["DATABASE_URL"] = (
        "postgresql+psycopg://nobody:nobody@127.0.0.1:1/does_not_exist?connect_timeout=2"
    )
    return subprocess.run(
        [sys.executable, "-m", module],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_seed_refuses_when_env_is_prod():
    proc = _run("app.seed", "prod")

    assert proc.returncode == 1, (
        "app.seed exited "
        f"{proc.returncode} under ENV=prod — it must refuse.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "REFUSED" in proc.stderr
    # The refusal has to point somewhere: production still needs the KB.
    assert "app.seed_kb" in proc.stderr
    # Never name the password in the refusal — this message lands in deploy logs.
    assert "director123" not in proc.stdout + proc.stderr


def test_seed_guard_fires_before_touching_the_database():
    """The refusal must not depend on a reachable DB.

    A guard that runs after SessionLocal() would let a prod deploy fail with a
    connection error instead of a refusal — and would then happily seed the
    moment the database came up.
    """
    proc = _run("app.seed", "prod")

    combined = proc.stdout + proc.stderr
    for connection_noise in ("could not connect", "OperationalError", "Connection refused"):
        assert connection_noise not in combined, (
            f"app.seed reached the database before refusing ({connection_noise!r} in output)"
        )


def test_seed_kb_is_not_blocked_in_prod():
    """The production-safe seed must stay runnable under ENV=prod.

    app.seed_kb carries the grounded assistant's entire knowledge base and no
    credentials. Blocking it too would leave production with an assistant that
    has nothing to ground against — so the guard must be on app.seed alone.
    """
    proc = _run("app.seed_kb", "prod")

    # It WILL fail to connect (the URL above is deliberately dead) — that is the
    # proof it got past import and tried to do its job. What must never appear
    # is the refusal.
    assert "REFUSED" not in proc.stderr, "app.seed_kb is blocked in prod; only app.seed should be"


def test_seed_roster_is_not_blocked_in_prod():
    """The roster seed must stay runnable under ENV=prod — deliberately.

    app.seed's guard exists for one reason: demo accounts whose passwords are
    published in AGENTS.md, a DIRECTOR among them. app.seed_roster creates real
    students, no DIRECTOR, and no usable password at all — and under Google-only
    sign-in those rows ARE the allowlist, so a production host without them has
    a batch of students who cannot sign in. Copying the refusal onto it (the
    obvious "make all seeds safe" refactor) breaks production sign-in outright.
    """
    proc = _run("app.seed_roster", "prod")

    # Like app.seed_kb it WILL fail on the deliberately dead DB — that is the
    # proof it got past import and tried to work. The refusal must not appear.
    assert "REFUSED" not in proc.stderr, (
        "app.seed_roster is blocked in prod; it is production-intended.\n"
        f"stderr: {proc.stderr}"
    )
    # It must say which database it is about to write to, and never leak the
    # password from the URL while doing so.
    assert "127.0.0.1:1/does_not_exist" in proc.stdout, (
        f"app.seed_roster did not name its target database.\nstdout: {proc.stdout}"
    )
    assert "nobody:nobody" not in proc.stdout + proc.stderr


def test_seed_roster_dry_run_matches_the_source_roster():
    """The roster constant is the allowlist; pin its shape.

    Sequence numbers 11, 23 and 30 are absent from the source document. A future
    reader "fixing" the gap would invent three students, and dropping a row locks
    a real one out of a Google-only sign-in.
    """
    from app.seed_roster import ROSTER, email_for

    usns = [usn for usn, _ in ROSTER]
    assert len(ROSTER) == 33
    assert len(set(usns)) == 33, "duplicate USN in the roster constant"
    assert sorted(usns) == usns, "roster is not in USN order"
    missing = [n for n in range(1, 37) if f"1MP25MDM{n:02d}" not in set(usns)]
    assert missing == [11, 23, 30], missing
    assert all(name.strip() == name and name for _, name in ROSTER)
    assert email_for("1MP25MDM01", "bgscet.ac.in") == "1mp25mdm01@bgscet.ac.in"
