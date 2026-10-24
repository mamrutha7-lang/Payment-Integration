"""Print a `reep_session` cookie for a seeded account, on a development host.

WHY THIS EXISTS. The dev MCP surface (`/mcp`, see app/main.py) forwards the
caller's Cookie header into the real request handlers, so every tool call runs
as whoever that cookie belongs to — which is the point: a screen's data can be
checked as the seeded student, and rule 2's narrowing can be checked by asking
the same question as a mentor. Something has to mint that cookie, and
`POST /api/auth/login` is the wrong thing to use for it. See below.

    python -m app.dev_session --email admin@bgscet.ac.in
    reep_session=eyJhbGciOiJIUzI1NiIs...

    export REEP_DEV_SESSION="$(python -m app.dev_session --email admin@bgscet.ac.in)"

IT DOES NOT SIGN ANYBODY IN, and that distinction is the whole design.

REEP allows ONE LIVE SESSION PER ACCOUNT (AGENTS.md, 2026-09-10): every sign-in
advances `users.token_version` before the token is minted, and app/security.py
refuses any token whose version is behind the row. So a helper that did "the
same as login" would sign the developer's own browser out of that account every
time it ran — and the developer would then sign back in, which would invalidate
the cookie they had just exported. The two would take turns, and the loser each
time would look like a bug in the MCP mount.

So this mints at the CURRENT version without advancing it. The cookie is
therefore valid alongside a browser session on the same account, and it dies
the next time anybody signs in to that account anywhere, which is correct: it
is the same revocation every other REEP session obeys. Re-run this to get
another one.

IT REFUSES OUTSIDE A DEVELOPMENT ENVIRONMENT, on the allowlist rather than on
`not is_prod`, so an unrecognised ENV refuses too. There is no override flag:
the thing it prints is a bearer token for somebody's whole account.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .models.user import User
from .security import SESSION_COOKIE, SESSION_VERSION_CLAIM, create_session_token


def session_cookie_for(email: str) -> str:
    """The `name=value` cookie a browser would hold for `email`.

    The claims are the ones app/routers/auth.py::_payload_for builds, because
    `require_*` in app/identity.py reads those names and must not be able to
    tell the two paths apart. The one difference is deliberate: the version
    claim is READ from the row rather than written to it.
    """
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            raise SystemExit(f"No account with the address {email!r}. Run `python -m app.seed` first.")

        payload: dict[str, object] = {
            "userId": str(user.id),
            "email": user.email,
            "name": user.name,
            "role": user.role.value,
            # Read, never advanced. Advancing it here would retire the
            # developer's browser session for this same account.
            SESSION_VERSION_CLAIM: user.token_version,
        }

        student_id = _student_id_for(db, user)
        if student_id is not None:
            payload["studentId"] = student_id
        mentor_id = _mentor_id_for(db, user)
        if mentor_id is not None:
            payload["mentorId"] = mentor_id

        return f"{SESSION_COOKIE}={create_session_token(payload)}"


def _student_id_for(db, user: User) -> str | None:
    """The account's Student row id, when it has one."""
    from .models.user import Student

    student = db.scalar(select(Student).where(Student.user_id == user.id))
    return str(student.id) if student is not None else None


def _mentor_id_for(db, user: User) -> str | None:
    """The account's Mentor group id, when it has one.

    A faculty account has no Mentor row until the Main Admin assigns it a
    student, and rule 2 reads that absence as "sees nobody". Minting a mentorId
    that the account does not hold would hide exactly the case worth testing.
    """
    from .models.user import Mentor

    mentor = db.scalar(select(Mentor).where(Mentor.user_id == user.id))
    return str(mentor.id) if mentor is not None else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.dev_session",
        description="Print a reep_session cookie for a seeded account (development only).",
    )
    parser.add_argument("--email", required=True, help="the account's address, e.g. admin@bgscet.ac.in")
    arguments = parser.parse_args(argv)

    if not settings.env_is_dev:
        print(
            f"REFUSED: ENV={settings.env.strip() or '(blank)'} is not a development "
            "environment, and this prints a bearer token for a whole account. There "
            "is no override flag.",
            file=sys.stderr,
        )
        return 2

    print(session_cookie_for(arguments.email))
    return 0


if __name__ == "__main__":
    sys.exit(main())
