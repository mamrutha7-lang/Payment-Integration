"""Setting the first password, forgetting it, and changing it.

Three flows, one ending: THE THING THAT LET YOU IN IS DESTROYED. An activation
link is consumed; a reset link is consumed and every other device is signed
out; a change keeps this device and signs out the rest. Logging the other
devices out after a reset matters because the commonest reason for a reset is
that somebody else got in.

STUDENTS COME HERE NOW (2026-09-10). Option B — students sign in with Google
and hold no password — was reversed: a student sets a password at the end of
`routers/onboarding.py`, so `forgot` and `change-password` serve them like
anyone else. Two refusals survive the reversal and are NOT vestigial:
`activate` still refuses a STUDENT, because an activation link is a STAFF
first-password link and a student's equivalent is the onboarding walk, which
proves the mailbox with a code first; and both flows still refuse an account
holding the unusable sentinel, which is an account that has not finished
onboarding rather than one with a forgotten password. The password door itself
is still `password_door_open` in auth.py — the first real scrypt hash opens it,
and the first one is now usually a student's.

AND `activate` REFUSES AN ACCOUNT THAT ALREADY HOLDS ONE (B3.4). A first-password
link that also works as a change-password link is an account takeover wearing a
support action's clothes: the Main Admin can re-mint an activation link for
anybody, so whoever held that link could set a new password on a live faculty
account. It answers 410 and names "Forgot password?", which mails the ACCOUNT.

WHY A SEPARATE MODULE. auth.py is 830 lines and owns sign-in; this owns
credentials. It borrows auth's session issuance rather than copying it, so a
change to the cookie is still one edit.

NEVER SAY WHETHER AN ADDRESS EXISTS. `forgot` answers the same words with the
same status whether the account is real, Google-only, or unknown, and does the
mail work in a background task so the response returns at the same moment in
every case. Otherwise the form is a free tool for discovering who is enrolled.

`forgot` IS ALSO HOW A PASSWORD-LESS STUDENT GETS THEIR FIRST PASSWORD
(2026-09-16). A student whose account holds the unusable sentinel - every
account `app.seed_roster` and `app.grant_access` mint, and every student
provisioned before 2026-09-10 under option B - could sign in with Google and
reach a password by NO path at all: this endpoint skipped them in silence,
`change-password` answered 409 and named "the link you were emailed", which
they never were, and the console has no button for it. From the student's
side that was "I typed the right address and the code never came". So a
STUDENT in that state is now mailed the SETUP link (`issue_password_setup`,
the same `/onboard` walk provisioning sends: address, code, password) rather
than nothing. The answer on screen is still one sentence for every address.
STAFF holding the sentinel are still sent nothing here: their first password
is the activation link their admin holds and can read out on the phone.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import account_links
from ..config import settings
from ..db import SessionLocal, get_db
from ..identity import get_current_session
from ..models.auth_token import PURPOSE_ACTIVATION, PURPOSE_CHANGE_CODE, PURPOSE_RESET
from ..models.user import Role, User
from ..schemas.auth import SessionUser
from ..security import hash_password, note_revocation, verify_password
from ..set_password import password_problem
from .auth import (
    DISABLED_SIGN_IN_MESSAGE,
    DOOR_ACTIVATION,
    _confirm_exclusive_session,
    _issue_session,
    _payload_for,
    _record_login,
    _retire_other_sessions,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["passwords"])


# --------------------------------------------------------------- throttle --
# The login limiter guards wrong passwords. "Forgot password" needs its own
# cap — per address and overall — or it becomes a way to send a student a
# hundred emails. Same in-process, bounded shape as auth.py's, same honesty
# about it: per task, not per fleet.


class _Throttle:
    def __init__(self, limit: int, window_seconds: int, max_keys: int = 4096) -> None:
        self.limit, self.window, self.max_keys = limit, window_seconds, max_keys
        self._hits: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        started, count = self._hits.get(key, (now, 0))
        if now - started >= self.window:
            started, count = now, 0
        if count >= self.limit:
            return False
        if key not in self._hits and len(self._hits) >= self.max_keys:
            self._hits = {k: v for k, v in self._hits.items() if now - v[0] < self.window}
            if len(self._hits) >= self.max_keys:
                self._hits.clear()
        self._hits[key] = (started, count + 1)
        return True

    def reset(self) -> None:
        self._hits.clear()


_forgot_per_address = _Throttle(settings.forgot_password_per_address_per_hour, 3600)
_forgot_global = _Throttle(settings.forgot_password_global_per_hour, 3600)


def reset_throttles() -> None:
    """For tests."""
    _forgot_per_address.reset()
    _forgot_global.reset()


# ------------------------------------------------------------- activation --


class LinkPasswordIn(BaseModel):
    # NO LOWER BOUND ON THE TOKEN. A link cut short by a mail client is a dead
    # link, and the lookup in both handlers already says so: 410 and "This link
    # is not valid", the answer every other unknown token gets. `min_length=16`
    # refused it here instead, as a 422 about the request, and the set-password
    # screen reads a 422 as a refused PASSWORD - so a person holding a broken
    # link was told to choose another password, over and over. The upper bound
    # stays: it caps what is hashed.
    token: str = Field(max_length=200)
    password: str = Field(min_length=1, max_length=256)


@router.post("/activate", response_model=SessionUser)
def activate(
    body: LinkPasswordIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> SessionUser:
    """Set a first password from an activation link, and sign in.

    ORDER MATTERS: the password is checked against policy BEFORE the link is
    spent. A refusal for "12 characters minimum" must not burn the link, or a
    typo costs the person a second email from an admin.

    AN ACTIVATION LINK SETS A FIRST PASSWORD AND NOTHING ELSE (B3.4). It used to
    check only the token, the policy, the race and the role — so re-minting a
    link for a faculty member who ALREADY HELD a password, which
    `POST /api/admin/users/{id}/activation-link` will do for anybody, let
    whoever ended up holding that link set a new one on a live account. That is
    an account takeover through a support action, and it contradicts the
    product's own rule that an admin never sets somebody's password. The
    account's own door for a forgotten password is `/auth/forgot`, which mails
    the ACCOUNT rather than handing a link to whoever asked.
    """
    token = body.token.strip()
    live = account_links.peek_user_token(db, PURPOSE_ACTIVATION, token)
    if live is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=account_links.explain_user_token(db, PURPOSE_ACTIVATION, token),
        )
    # AFTER the peek, so a link that is merely spent keeps saying "already been
    # used" — the words that tell somebody who clicked twice what happened.
    holder = db.get(User, live.user_id)
    if holder is not None and holder.password_hash.startswith("scrypt:"):
        # `account_links.issue_activation` refuses to MINT one of these now, so
        # this closes the links already in circulation.
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=(
                "This account already has a password, so this setup link no "
                "longer works. Use \"Forgot password?\" on the sign-in screen "
                "to choose a new one."
            ),
        )
    if holder is not None and holder.barred_at is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=DISABLED_SIGN_IN_MESSAGE
        )
    problem = password_problem(body.password)
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)

    user = account_links.consume_user_token(db, PURPOSE_ACTIVATION, token)
    if user is None:  # lost the race to a second click
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=account_links.explain_user_token(db, PURPOSE_ACTIVATION, token),
        )
    if user.role is Role.STUDENT:
        # Cannot happen through issue_activation, which refuses students; this
        # is the second lock on the same door.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Students sign in with their college Google account.",
        )
    user.password_hash = hash_password(body.password)
    db.commit()
    log.info("activated %s (%s) via link", user.email, user.role.value)

    # One device at a time, same as every other sign-in door: activating an
    # account signs it in, so it retires anything that account already holds.
    _retire_other_sessions(user)
    # The commit that persists the retirement -- and, since B15, the
    # `login_events` row. Activation IS a sign-in door (it issues the cookie
    # four lines below), so it names itself like the other three; `door` is a
    # required keyword in `_record_login` precisely so this call could not be
    # left behind when that table was added.
    _record_login(db, user, door=DOOR_ACTIVATION, request=request)
    _confirm_exclusive_session(user)
    payload = _payload_for(user)
    _issue_session(response, payload)
    return SessionUser(**payload)


# ------------------------------------------------------------------ forgot --


class ForgotIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class MessageOut(BaseModel):
    detail: str


#: ONE sentence for every address, so it has to be true of both mails this
#: endpoint can send - the reset link a password account gets and the setup
#: link a password-less student gets - and of the nothing everyone else gets.
#: The earlier wording ("if that address has a REEP password ...") told a
#: student who had none that nothing was coming, on the one path that now
#: sends them something.
_FORGOT_ANSWER = (
    "If that address belongs to a REEP account, we've emailed it a link to reset "
    "your password - or to set one up, if you have not yet."
)


def _issue_reset_in_background(email: str) -> None:
    """The half of `forgot` that would reveal whether the account exists if it
    ran in the request. Own session: this runs after the response is gone."""
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            return
        if user.barred_at is not None:
            # Offboarded (B3.3) or removed (2026-09-16). A reset link is a way back in, and there is no
            # way back in. Silently, because /forgot answers the same 202 to
            # every address and must not become "is this account still active".
            # FIRST, before either branch below: a disabled password-less
            # student must not be mailed a setup link either.
            log.info("forgot-password ignored for %s: the account is disabled or removed", email)
            return
        # STUDENTS ARE NO LONGER SKIPPED (2026-09-10). Under option B they held
        # no password, so a reset link would have quietly created one; they set
        # a password during onboarding now, and a student who forgets it has
        # the same claim on this flow as anyone else.
        if not user.password_hash.startswith("scrypt:"):
            # The unusable sentinel: nothing to RESET. What happens next depends
            # on who holds it, and the two answers are deliberately different.
            #
            # A STUDENT gets the SETUP link (2026-09-16) - the same `/onboard`
            # walk an approved applicant is sent, which proves the mailbox with
            # a code before a password is set. "Mailing a link would quietly
            # turn it into a password account" was the reasoning here under
            # option B, and option B is over: students are MEANT to hold a
            # password now, and this account simply never got the mail that
            # hands one out (seed_roster, grant_access, or provisioned before
            # the walk existed). Without this branch such a student can sign in
            # with Google and has no route to a password anywhere in the product.
            #
            # STAFF get nothing. Their first password is an ACTIVATION link,
            # minted by the admin who created the account and re-mintable on the
            # Faculty screen, where the admin can read it out when mail fails;
            # that link sets a password by itself, so it is not one this public
            # form should be able to put in a mailbox on request.
            if user.role is Role.STUDENT:
                account_links.issue_password_setup(db, user)
                log.info("forgot-password sent a SETUP link for %s: student with no password", email)
            return
        account_links.issue_password_reset(db, user)


@router.post("/forgot", response_model=MessageOut, status_code=status.HTTP_202_ACCEPTED)
def forgot(body: ForgotIn, background: BackgroundTasks) -> MessageOut:
    """Always the same answer. The work that differs happens after the reply."""
    email = body.email.strip().lower()
    if not _forgot_global.allow("global") or not _forgot_per_address.allow(email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many reset requests. Please wait an hour and try again.",
            headers={"Retry-After": "3600"},
        )
    background.add_task(_issue_reset_in_background, email)
    return MessageOut(detail=_FORGOT_ANSWER)


# ------------------------------------------------------------------- reset --


@router.post("/reset", response_model=MessageOut)
def reset(body: LinkPasswordIn, db: Session = Depends(get_db)) -> MessageOut:
    """New password from a reset link. Every device is signed out, including
    this one — the person signs in fresh with what they just chose."""
    token = body.token.strip()
    live = account_links.peek_user_token(db, PURPOSE_RESET, token)
    if live is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=account_links.explain_user_token(db, PURPOSE_RESET, token),
        )
    holder = db.get(User, live.user_id)
    if holder is not None and holder.barred_at is not None:
        # A link minted the minute before the account was disabled is still
        # live. Refused BEFORE the consume, so the link is not burnt on a
        # decision nothing about this request can change.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=DISABLED_SIGN_IN_MESSAGE
        )
    problem = password_problem(body.password)
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)

    user = account_links.consume_user_token(db, PURPOSE_RESET, token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=account_links.explain_user_token(db, PURPOSE_RESET, token),
        )
    user.password_hash = hash_password(body.password)
    # Sign out everywhere: the commonest reason for a reset is that someone
    # else got in. token_version is the one number every session check reads.
    user.token_version = (user.token_version or 0) + 1
    # And no other pending link may finish what this one started.
    account_links.revoke_user_tokens(db, user.id, PURPOSE_RESET)
    db.commit()
    note_revocation(user.id, user.token_version)
    log.info("password reset for %s; all sessions revoked", user.email)
    return MessageOut(detail="Password updated. Sign in with your new password.")


# ----------------------------------------------------------------- change --


#: The 409 both change-password endpoints answer an account holding the
#: sentinel. It used to end "finish setting up your account from the link you
#: were emailed", which named a mail a roster-seeded or option-B student never
#: received - a dead end dressed as an instruction. It now names the door that
#: exists: `forgot`, which mails such a student the setup link.
NO_PASSWORD_YET_MESSAGE = (
    "This account has no password yet. To set one, use \"Forgot password?\" on "
    "the sign-in screen: we will email you a setup link, then a code, and you "
    "choose a password at the end. You can keep signing in with Google meanwhile."
)


class ChangePasswordIn(BaseModel):
    """TWO WAYS TO PROVE IT IS YOU, and exactly one must be supplied.

    `code` is what the screens use: a student who has just set their first
    password does not reliably remember it, and asking for it is how "I forgot
    it already" becomes a support call instead of a code. `current_password`
    stays because it is what staff have always typed and what six test modules
    already exercise, and because it needs no mailbox — the door that still
    works when SES does not.
    """

    new_password: str = Field(min_length=1, max_length=256)
    current_password: str | None = Field(default=None, max_length=256)
    code: str | None = Field(default=None, max_length=12)


def _authorised_to_change(db: Session, user: User, body: "ChangePasswordIn") -> bool:
    """Whichever proof was offered, checked. Never both, never neither."""
    code = (body.code or "").strip()
    current = body.current_password or ""
    if bool(code) == bool(current):
        return False
    if code:
        ok = account_links.consume_user_code(db, user.id, PURPOSE_CHANGE_CODE, code)
        if ok:
            db.commit()
        return ok
    return verify_password(current, user.password_hash)


class ChangeCodeOut(BaseModel):
    message: str


@router.post("/change-password/code", response_model=ChangeCodeOut)
def change_password_code(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> ChangeCodeOut:
    """Mail the signed-in account a code authorising a password change.

    It goes to the address ON THE ACCOUNT and is never taken from the request:
    a caller who could name the recipient could mail themselves somebody
    else's authorisation. Being signed in is not enough on its own — that is
    the point of the code, which is why the mail says plainly what to do if you
    did not ask for it.
    """
    user = db.get(User, session["userId"])
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in again.")
    if not user.password_hash.startswith("scrypt:"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=NO_PASSWORD_YET_MESSAGE,
        )
    account_links.issue_change_code(db, user)
    log.info("password-change code sent for %s", user.email)
    return ChangeCodeOut(
        message=f"We have emailed a code to {user.email}. It expires in "
        f"{settings.otp_code_minutes} minutes."
    )


@router.post("/change-password", response_model=SessionUser)
def change_password(
    body: ChangePasswordIn,
    response: Response,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> SessionUser:
    """Signed in, knows the current password. Other devices out; this one stays.

    "This one stays" is the cookie being re-issued with the NEW token_version
    before the response goes out — otherwise the person who just changed their
    password is signed out by their own change and reads it as a bug.
    """
    user = db.get(User, session["userId"])
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in again.")
    # NO LONGER REFUSES A STUDENT (2026-09-10). Option B is over; students set
    # a password during onboarding. What is still refused is an account that
    # holds no password at all — the unusable sentinel — because there is
    # nothing to change and `verify_password` can never match it. Such an
    # account gets in through Google, or finishes onboarding first.
    if not user.password_hash.startswith("scrypt:"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NO_PASSWORD_YET_MESSAGE)
    # THE POLICY IS CHECKED BEFORE THE PROOF IS SPENT, and the order is the
    # whole point. `_authorised_to_change` CONSUMES the one-time code and
    # commits it, so validating afterwards burns a code on a password that was
    # never going to be accepted: the person fixes their typo and is told to
    # request another code, which reads as the form having eaten it. `reset`
    # peeks its token, validates, then consumes, and the onboarding password
    # step does the same; this was the one door with it the other way round.
    problem = password_problem(body.new_password)
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)
    if not _authorised_to_change(db, user, body):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="That is not right. Check the code, or the current password.",
        )
    # This one stays AFTER the proof: it can only fire on the current-password
    # path, where nothing has been consumed and there is no code to protect.
    if body.current_password and body.new_password == body.current_password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The new password is the same as the current one.",
        )

    user.password_hash = hash_password(body.new_password)
    user.token_version = (user.token_version or 0) + 1
    account_links.revoke_user_tokens(db, user.id, PURPOSE_RESET)
    db.commit()
    note_revocation(user.id, user.token_version)

    payload = _payload_for(user)
    _issue_session(response, payload)
    log.info("password changed for %s; other sessions revoked", user.email)
    return SessionUser(**payload)
