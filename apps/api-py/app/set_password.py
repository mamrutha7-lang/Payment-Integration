"""Give one existing account a usable password.

    python -m app.set_password someone@bgscet.ac.in

The password is TYPED AT A PROMPT, never passed as a flag. A `--password` option
would put the secret in the operator's shell history, in `ps` output for every
other user on the box, and — on the ECS path this is most likely run through —
in the task definition overrides that CloudTrail records. There is deliberately
no way to supply it non-interactively; a tool that can be scripted with a
password in the command is a tool whose passwords end up in a log.

WHY THIS EXISTS SEPARATELY FROM app.grant_access. Sign-in is Google-first, and
every account minted by `app.grant_access` and `app.seed_roster` carries
SSO_ONLY_PASSWORD_HASH — a sentinel that is not `scrypt:<salt>:<digest>`, so
`verify_password` returns False for every password ever tried. That is why
setting PASSWORD_LOGIN=true does not by itself let anybody in: the door opens,
and every account behind it still has no key. This module is how an operator
issues one key, to one named account, on purpose.

It does NOT create accounts. An address with no row is refused rather than
granted, because "create a user" and "set a password" are different decisions
and `app.grant_access` already owns the first one — with a `--role` it refuses
to default, precisely so that granting privilege is never a side effect of some
other command.

PRODUCTION-SAFE, like `app.grant_access` and `app.seed_kb`, and for the same
reason: production is exactly where it is needed. It writes one scrypt hash for
one address the operator names. That is the opposite of `app.seed`, which
refuses on ENV=prod because it creates SEVERAL accounts behind passwords
published in AGENTS.md — this tool cannot recreate those, because the password
comes from the prompt and never from the repository.

The account keeps Google sign-in. A password is an ADDITIONAL door for that
account, not a replacement: the `users` row is unchanged apart from its hash,
and `google_callback` still finds it. To take the password away again, run
`--revoke`, which restores the SSO-only sentinel.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .grant_access import SSO_ONLY_PASSWORD_HASH
from .models.user import Role, User
from .security import hash_password
from .seed_roster import db_target

# Long enough that the brute-force limiter in app/routers/auth.py is the second
# line of defence rather than the only one. Deliberately a LENGTH floor and not
# a character-class rule: "at least 12" is a real constraint that a passphrase
# satisfies naturally, where "one upper, one digit, one symbol" reliably
# produces Director@123 and a sticky note. NIST dropped composition rules for
# this reason.
MIN_PASSWORD_LENGTH = 12

# Refused outright. These are the seeded demo passwords published in AGENTS.md
# and this repository's own README — the exact strings an operator reaches for
# when setting up "just a test account on prod", and the exact strings anyone
# who has ever cloned this repo will try first.
_PUBLISHED_PASSWORDS = frozenset(
    {
        "student123",
        "mentor123",
        "director123",
        "alumni123",
        "admin123",
        "password",
        "password123",
        "changeme",
        "reep1234",
    }
)


def set_password(db, email: str, password: str) -> User:
    """Hash `password` onto the existing account for `email`. Raises ValueError.

    The caller is responsible for having obtained the password interactively;
    this function is the library half so tests can drive it without a TTY.
    """
    address = email.strip().lower()
    if not address:
        raise ValueError("an email address is required")

    user = db.scalar(select(User).where(User.email == address))
    if user is None:
        raise ValueError(
            f"{address} has no user row. This tool sets a password on an account "
            f"that already exists; create it first with:\n"
            f"    python -m app.grant_access {address} --name \"Full Name\" --role ROLE"
        )

    problem = password_problem(password)
    if problem:
        raise ValueError(problem)

    user.password_hash = hash_password(password)
    db.commit()
    return user


def password_problem(password: str) -> str | None:
    """Why this password is unacceptable, or None. Length and denylist only."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return (
            f"that password is {len(password)} characters; the minimum is "
            f"{MIN_PASSWORD_LENGTH}. A memorable phrase of four or five words "
            f"clears this easily and is stronger than a short scrambled one."
        )
    if password.strip().lower() in _PUBLISHED_PASSWORDS:
        return (
            "that password is one of the demo passwords published in this "
            "repository's AGENTS.md, so it is known to everyone who has ever "
            "cloned it. Choose one that is not written down in the source tree."
        )
    return None


def revoke_password(db, email: str) -> User:
    """Restore the SSO-only sentinel, leaving Google sign-in working."""
    address = email.strip().lower()
    user = db.scalar(select(User).where(User.email == address))
    if user is None:
        raise ValueError(f"{address} has no user row; nothing to revoke")
    user.password_hash = SSO_ONLY_PASSWORD_HASH
    db.commit()
    return user


def hash_for_paste(password: str) -> str:
    """The scrypt hash for `password`, after the same checks set_password applies.

    This is the `--print-hash` path's library half: the plaintext is checked
    against the length floor and the published-password denylist HERE, on the
    operator's machine, and only the hash leaves it — pasted into the ops-task
    workflow's `password_hash` input for `app.grant_access --password-hash`.
    That is how a production account gets a password without the password ever
    crossing the RunTask API, where CloudTrail would keep it for 90 days.
    """
    problem = password_problem(password)
    if problem:
        raise ValueError(problem)
    return hash_password(password)


def _prompt_for_password() -> str | None:
    """Ask twice, echo neither. None if they do not match."""
    first = getpass.getpass("New password (not echoed): ")
    second = getpass.getpass("Type it again: ")
    if first != second:
        return None
    return first


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.set_password",
        description=(
            "Give one existing account a usable password. The password is typed "
            "at a prompt and is never accepted as a command-line flag."
        ),
    )
    parser.add_argument(
        "email",
        nargs="?",
        default=None,
        help="the account's address, e.g. someone@bgscet.ac.in (not needed with --print-hash)",
    )
    parser.add_argument(
        "--revoke",
        action="store_true",
        help="remove the password instead of setting one, restoring the "
        "SSO-only sentinel. Google sign-in for the account is unaffected.",
    )
    parser.add_argument(
        "--print-hash",
        action="store_true",
        help="touch no database: prompt for a password, apply the same checks, "
        "and print its scrypt hash for `app.grant_access --password-hash` (or "
        "the ops-task workflow's password_hash input). The password stays on "
        "this machine; only the hash travels.",
    )
    args = parser.parse_args()

    if args.print_hash:
        password = _prompt_for_password()
        if password is None:
            print("REFUSED: the two entries did not match.", file=sys.stderr)
            return 2
        try:
            print(hash_for_paste(password))
        except ValueError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        return 0

    if not args.email:
        parser.error("an email address is required (or use --print-hash)")
    address = args.email.strip().lower()

    # Named BEFORE the write, for the reason app.grant_access says it out loud:
    # this tool is prod-runnable, and the operator's only defence against
    # issuing a password on the wrong database is being told which one it is.
    print(f"database: {db_target()}")

    if args.revoke:
        with SessionLocal() as db:
            try:
                user = revoke_password(db, address)
            except ValueError as exc:
                print(f"REFUSED: {exc}", file=sys.stderr)
                return 2
            print(f"revoked: {user.email} can no longer sign in with a password.")
            print("  Google sign-in for this account is unchanged.")
        return 0

    # Said before the prompt rather than after the write, so an operator who is
    # about to type a password for a DIRECTOR on production sees what that means
    # while they can still press Ctrl-C.
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == address))
        if user is None:
            print(
                f"REFUSED: {address} has no user row. Create the account first:\n"
                f'    python -m app.grant_access {address} --name "Full Name" --role ROLE',
                file=sys.stderr,
            )
            return 2
        role = user.role

    print(f"account:  {address}  role={role.value}")
    if role in (Role.DIRECTOR, Role.ADMIN):
        # Rule 2, stated at the moment it is being acted on.
        print(
            "  NOTE: this role reads EVERY student's marks, attendance and USN.\n"
            "  A guessed password here is a full-cohort disclosure. Use a long\n"
            "  passphrase and store it in a password manager, not a message."
        )
    if not settings.password_login_allowed:
        # Not a refusal: setting the password first and opening the door second
        # is a perfectly sensible order. But an operator who does only this and
        # then finds sign-in still refused deserves to be told why here.
        print(
            f"  NOTE: PASSWORD_LOGIN is not enabled and ENV={settings.env!r}, so\n"
            "  POST /api/auth/login still answers 403 for everyone. Set\n"
            "  PASSWORD_LOGIN=true in the API's environment to open that door."
        )

    password = _prompt_for_password()
    if password is None:
        print("REFUSED: the two entries did not match; nothing was changed.", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        try:
            user = set_password(db, address, password)
        except ValueError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        print(f"set: {user.email} can now sign in with email and password.")
        print("  Google sign-in for this account still works; this is a second door.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
