"""A link that works once and then stops — activation and password reset.

ONE TABLE, TWO JOBS (and a third is a row, not a migration). Setting a first
password and resetting a forgotten one are the same mechanism: a secret in an
email, a hash of it here, consumed exactly once. `purpose` is a plain String
so a future "confirm new email address" is `purpose="email_change"` and no
`CREATE TYPE`. Registration's own confirmation link is NOT here — it predates
this table and hangs off `registrations`, which is not a user yet; see
`EmailVerification` in registration.py.

THREE RULES THAT MAKE IT SAFE, from the agreed plan (docs/prototypes/.../passwords.html):

  1. THE LINK IS STORED HASHED, NEVER IN PLAIN TEXT. Same reason passwords are:
     a leaked dump of this table yields hashes, not a working link into every
     account with a pending reset. SHA-256, not scrypt — the token is 256
     random bits, not a guessable phrase, so a slow KDF buys nothing and only
     slows every click.
  2. USED ONCE, AND THE CHECK IS ATOMIC. `UPDATE ... WHERE consumed_at IS NULL`
     and act on the row count — the same trick interview finalisation uses for
     `status = 'running'`. Two clicks race; exactly one wins.
  3. SHORT LIVES. Activation 7 days (a new staff member may not check mail
     today). Reset 1 hour (the account exists and may already be under attack).

`consumed_at` is kept rather than the row deleted, so a second click can be
told apart from an expired link and given the right words.

THE SIGN-IN CODE BENDS TWO OF THESE, ON PURPOSE. `token_hash` is globally
unique (`uq_auth_token_hash`) — right for a 256-bit link, and a 500 waiting
to happen for a six-digit code, where two people dealt 482913 in the same
minute, or anyone re-drawing a code consumed last year, would collide on a
bare `sha256(code)`. So a code's hash is `sha256("login_code:<row id>:<code>")`
(account_links._hash_code): unique by construction, and findable ONLY by
(user, purpose), which is the scoping a shared code needs anyway. And a code's
dead rows are DELETED — its predecessors on re-issue, the last one by
`account_links.sweep_login_codes` — because /login/code answers one sentence
for every refusal, so there are no "already used" words to preserve, and one
permanent row per staff sign-in is a leak.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base

PURPOSE_ACTIVATION = "activation"
PURPOSE_RESET = "reset"
# A sign-in one-time code: six digits, minutes to live, hashed WITH its row id
# (see the module docstring), and found by the user it was issued to, never by
# hash — a six-digit value is not unique across users, so
# `consume_user_token`'s hash-only lookup must never be used for one. See
# account_links.consume_user_code.
PURPOSE_LOGIN_CODE = "login_code"
# THE MAIN ADMIN'S SECOND FACTOR FOR A PERMANENT DELETE (2026-09-16). Six
# digits mailed to the office account's OWN address and spent by exactly one
# destructive act — a student, a faculty account or a whole college, gone for
# good. Being signed in is not enough on its own for those: the cookie is on
# whichever machine the office left open, and the mailbox is not. A code, like
# the sign-in and change-password ones: hashed with its row id, found by
# (user, purpose), predecessors deleted on re-issue. See
# account_links.issue_delete_code and routers/admin_deletion.py.
PURPOSE_DELETE_CODE = "delete_code"

# --- student onboarding (2026-09-10) ---------------------------------------
# The three secrets a newly approved student spends, in order. They are three
# purposes and not one because each proves a DIFFERENT thing, and collapsing
# them would let one proof stand in for another:
#
#   PURPOSE_ONBOARD       the link in the approval email. Proves only that the
#                         person opened mail sent to the address on the
#                         application. Long-lived (activation_link_hours).
#   PURPOSE_ONBOARD_CODE  the six-digit code, sent after they type their
#                         address back. Proves they can read that mailbox NOW,
#                         which the link alone does not — a forwarded link is
#                         still a valid link.
#   PURPOSE_ONBOARD_SET   a short-lived ticket minted only once the code is
#                         spent, and the only thing `onboard/password` accepts.
#                         Without it the password step would have to re-accept
#                         the code (already consumed) or trust the link alone,
#                         which would skip the mailbox proof entirely.
PURPOSE_ONBOARD = "onboard"
PURPOSE_ONBOARD_CODE = "onboard_code"
PURPOSE_ONBOARD_SET = "onboard_set"

# The code that authorises a password CHANGE on an account already in use.
# Separate from PURPOSE_ONBOARD_CODE so a code mailed to someone midway through
# setting their account up cannot be replayed against a live account, and vice
# versa — `consume_user_code` finds rows by (user, purpose), so distinct
# purposes are what actually enforces that.
PURPOSE_CHANGE_CODE = "change_code"

#: Purposes whose secret is SIX DIGITS, not a 256-bit link. Everything the
#: module docstring says about the sign-in code — the row-bound hash, the
#: lookup by (user, purpose), the DELETE of predecessors rather than a kept
#: "already used" row — applies to every member, and `issue_user_token` and
#: `sweep_login_codes` both branch on this set rather than on one name. A code
#: purpose left out of it would be minted with a bare sha256 and could collide
#: on `uq_auth_token_hash` with somebody else's identical six digits: a 500 in
#: the face of a person who typed the right code.
CODE_PURPOSES = frozenset(
    {PURPOSE_LOGIN_CODE, PURPOSE_ONBOARD_CODE, PURPOSE_CHANGE_CODE, PURPOSE_DELETE_CODE}
)


def _uuid() -> str:
    return uuid.uuid4().hex


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    __table_args__ = (
        # Named: an unnamed unique is called one thing by create_all() and
        # another by the migration (see College.code's history).
        UniqueConstraint("token_hash", name="uq_auth_token_hash"),
        Index("ix_auth_tokens_user_purpose", "user_id", "purpose"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    # CASCADE, unlike the institution tables: a token for a user who no longer
    # exists cannot mean anything, and keeping it would only be a hash pointing
    # at nothing.
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String)  # PURPOSE_ACTIVATION | PURPOSE_RESET
    # sha256 hex, 64 chars — never the raw. For a link, sha256(token); for a
    # login code, sha256 over the row id AND the code, so the global unique
    # below can never be tripped by six digits.
    token_hash: Mapped[str] = mapped_column(String)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Who issued it — the Main Admin from the console, or nobody (a CLI, or the
    # student themself via forgot-password). SET NULL: losing the issuer must
    # not lose the record that a link was issued.
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
