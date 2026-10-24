"""Password hashing and session tokens — byte-compatible with the Next.js app.

Password:  "scrypt:<salt_hex>:<digest_hex>", scrypt(N=16384, r=8, p=1, dklen=64)
           with the salt passed as its hex STRING (exactly what node:crypto's
           scryptSync does), so hashes migrate across without a reset.
Session:   HS256 JWT signed with AUTH_SECRET, claims = the session payload, plus
           iat/exp (12h). Same secret + alg as jose, so tokens verify on both
           sides during cutover. Cookie name: reep_session.
"""

import hashlib
import hmac
import logging
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

import jwt
from sqlalchemy import select

from .config import settings

log = logging.getLogger(__name__)

SESSION_COOKIE = "reep_session"
SESSION_TTL_SECONDS = 60 * 60 * 12

# --- Revocation ------------------------------------------------------------
#
# A session cookie is a STATELESS BEARER TOKEN: valid until `exp`, twelve hours
# away, whatever happens in between. "Log out" therefore used to mean only "this
# browser forgets the cookie" — a token copied off a shared lab machine stayed
# good for the rest of the day, and neither the student nor the placement office
# could do anything about it. That is the whole of finding M8.
#
# `users.token_version` closes it: the version at minting time rides in the
# token as `tokenVersion`, logout bumps the row, and a token whose version is
# BEHIND the row is refused. Doing that literally costs a SELECT on every
# authenticated request — including on the interview WebSocket's async path,
# where a blocking query sits on the event loop — so the version is cached per
# process for `settings.auth_revocation_cache_seconds`.
#
# THE HONEST CONSEQUENCE, which must not be dressed up as more than it is:
# revocation is IMMEDIATE in the worker that served the logout (it seeds its own
# cache) and takes UP TO auth_revocation_cache_seconds to reach the other
# workers. This is a best-effort logout, not a session store. If instant global
# revocation is ever genuinely needed, the answer is a shared store (Redis, or a
# per-request read), NOT a smaller TTL — a TTL of 0 simply puts the SELECT back
# on every request, which is the thing this cache exists to avoid.
SESSION_VERSION_CLAIM = "tokenVersion"

# DISABLED ACCOUNTS RIDE THE SAME LOOKUP, AND THAT IS THE WHOLE POINT (B3.3).
#
# The plan said "checked in verify_session_token, auth.login, the Google
# callback, onboarding/activation" — five doors, and a sixth the day somebody
# adds one. A check that has to be repeated at every door is a check the next
# door forgets, and the door it is forgotten at is the one a disabled faculty
# member walks through. There IS a chokepoint for the authenticated half:
# `verify_session_token` is the one function that turns a cookie into an
# identity, for HTTP, for the WebSocket and for every test, and it already pays
# for a users row on the revocation path. So `disabled_at` is read in the SAME
# query and cached in the SAME entry — no second round trip, no second cache,
# and no route that can opt out of it.
#
# It fails CLOSED with the version check it shares: a lookup that raises answers
# "disabled", because "we could not tell whether this account still exists" must
# not admit a bearer token.
#
# The other half — MINTING a session — cannot live here, because this function
# never sees the User row a sign-in door has just loaded. Its chokepoint is
# `_payload_for` in app/routers/auth.py, which every door calls before issuing a
# cookie and which raises for a disabled row.
#
# user_id -> (monotonic deadline, version, disabled). Bounded by the number of
# real users who have signed in on this worker: only a token that already passed
# the HS256 signature check ever reaches the lookup, so an attacker cannot seed
# entries.
_version_cache: dict[str, tuple[float, int, bool]] = {}
# FastAPI runs `def` endpoints in a threadpool and the WS path enters from the
# event loop, so two requests really can be in here at once. The lock is held
# only around the dict, never across the query — a database that has gone slow
# must not serialise every authenticated request behind one connection attempt.
_version_lock = threading.Lock()

# Monotonic deadline armed when the lookup FAILS. A failure caches nothing (a
# transient error must not stick), so without this the read is retried on every
# single authenticated request while the database is down: a connect attempt per
# request, each parked for the driver's timeout, plus a stack trace per request
# in the log. That is the same trap _JWKS_FAILURE_BACKOFF_SECONDS was written
# for in app/google_auth.py, arriving from the other side. During the backoff
# the check simply admits — which is what it would have done anyway.
_db_retry_after: float = 0.0
# Fixed, not derived from auth_revocation_cache_seconds, because that setting is
# allowed to be 0 ("ask the database every request") and a zero backoff is no
# backoff at all. Short enough that revocation resumes within seconds of the
# database coming back.
_DB_FAILURE_BACKOFF_SECONDS = 10.0

# Node scryptSync defaults: N=16384, r=8, p=1, keylen here=64. maxmem must exceed
# 128 * N * r (= 16 MiB); give it headroom.
_SCRYPT = dict(n=16384, r=8, p=1, dklen=64, maxmem=64 * 1024 * 1024)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), **_SCRYPT).hex()
    return f"scrypt:{salt}:{digest}"


def verify_password(password: str, stored: str) -> bool:
    parts = stored.split(":")
    if len(parts) != 3:
        return False
    scheme, salt, digest = parts
    if scheme != "scrypt" or not salt or not digest:
        return False
    derived = hashlib.scrypt(password.encode(), salt=salt.encode(), **_SCRYPT).hex()
    return hmac.compare_digest(derived, digest)


def create_session_token(payload: dict) -> str:
    now = datetime.now(timezone.utc)
    claims = {**payload, "iat": now, "exp": now + timedelta(seconds=SESSION_TTL_SECONDS)}
    return jwt.encode(claims, settings.auth_secret, algorithm="HS256")


def _cache_ttl() -> float:
    """Read per call, not at import, so a test can move it. Never negative."""
    return max(float(settings.auth_revocation_cache_seconds), 0.0)


class AccountState(NamedTuple):
    """What the one authorization read answers: is this session still current,
    and is the account still allowed in at all.

    `version` is -1 when the LOOKUP FAILED, which is a different thing from a
    missing row (version 0): a missing row cannot say "you revoked", but a
    failed read cannot say anything, so it refuses. `disabled` is True on that
    same failure, for the same reason and in the same direction.
    """

    version: int
    disabled: bool


#: The answer when the database could not be asked. Fails closed on BOTH halves.
_LOOKUP_FAILED = AccountState(version=-1, disabled=True)


def account_state(user_id: str) -> AccountState:
    """The user's `token_version` and `disabled_at`, from cache or the database.

    ONE QUERY FOR BOTH, because both are asked on every authenticated request
    and both are answers to the same question — may this cookie still act as
    this person. Reading `disabled_at` separately would double the per-request
    round trips that `auth_revocation_cache_seconds` exists to avoid, and give
    the two facts two cache windows that can disagree.

    Fail-closed: a database exception answers `_LOOKUP_FAILED`, which
    `verify_session_token` refuses. Revocation and offboarding are security
    state, so the application must not admit a bearer token when it cannot
    confirm either.
    """
    global _db_retry_after

    now = time.monotonic()
    with _version_lock:
        entry = _version_cache.get(user_id)
        if entry is not None and now < entry[0]:
            return AccountState(version=entry[1], disabled=entry[2])
        if now < _db_retry_after:
            # Still inside the backoff from a failed read. Revocation is
            # authorization state, so refuse until the database can answer.
            return _LOOKUP_FAILED

    try:
        # Imported HERE, not at module scope. app/seed.py, app/seed_roster.py
        # and the CLI tools import this module for `hash_password` alone, and
        # `app.models.user` drags the whole 31-module models package — and the
        # engine underneath it — in behind it. Nothing else in this file needs a
        # database, and the password half must stay importable without one.
        from .db import SessionLocal
        from .models.user import User

        with SessionLocal() as db:
            row = db.execute(
                select(User.token_version, User.disabled_at, User.deleted_at).where(
                    User.id == user_id
                )
            ).first()
    except Exception:
        # OperationalError, InterfaceError, DNS failure and a half-applied
        # migration all fail closed. Privileged authorization must not continue
        # when the source of truth cannot confirm the identity state.
        with _version_lock:
            _db_retry_after = time.monotonic() + _DB_FAILURE_BACKOFF_SECONDS
        log.exception(
            "could not read token_version/disabled_at for user %s; refusing the "
            "session and not re-asking for %.0fs until the database answers",
            user_id,
            _DB_FAILURE_BACKOFF_SECONDS,
        )
        return _LOOKUP_FAILED

    # A row that has vanished — a user deleted while still holding a session —
    # reads as version 0 rather than as a refusal. This function answers exactly
    # one question, "has this user revoked since the token was minted?", and a
    # missing row cannot answer yes. Deciding that a deleted user's session must
    # die is a different decision belonging to the deps that do authorisation,
    # not to a signature check. It is likewise NOT read as disabled: an absent
    # row was never offboarded.
    # `deleted_at` (removed from the roster, 2026-09-16) is read through the
    # same door as `disabled_at` and means the same thing to a cookie: the
    # person may not act. One query, one cache entry, for the reason above.
    version, disabled_at, deleted_at = row if row is not None else (0, None, None)
    state = AccountState(
        version=int(version or 0),
        disabled=disabled_at is not None or deleted_at is not None,
    )
    with _version_lock:
        _version_cache[user_id] = (now + _cache_ttl(), state.version, state.disabled)
    return state


def current_token_version(user_id: str) -> int:
    """Just the version half of `account_state`. Kept as a name because that is
    the one question the revocation comments above are written about."""
    return account_state(user_id).version


def note_revocation(user_id: str, version: int, *, disabled: bool = False) -> None:
    """Record a just-committed bump so THIS worker refuses the old token now.

    Without it a logout would not bite even in the process that served it until
    the TTL lapsed — and that is the one case a student is watching: sign out,
    press Back, still signed in. Other workers converge within the TTL.

    `disabled` carries the offboarding half through the same door, for the same
    reason: the admin who has just disabled an account watches the faculty list
    and must not see that account still able to act for a cache window. Default
    False because every OTHER caller (logout, reset, change-password, sign-in)
    is by construction acting for an account that is not disabled — a disabled
    one cannot reach any of them.
    """
    with _version_lock:
        _version_cache[user_id] = (time.monotonic() + _cache_ttl(), int(version), bool(disabled))


def _claimed_version(claims: dict) -> int:
    """The `tokenVersion` the token carries. A MISSING CLAIM IS VERSION 0.

    That is not laxity, it is the deploy story: every session minted before this
    column existed lacks the claim, and reading "absent" as "invalid" would sign
    the entire college out at the moment the new build goes live, for no
    security gain whatsoever — those tokens predate any revocation to enforce.
    A claim that is present but not an integer is a different thing: only this
    server mints these, so a non-integer means a forged or hand-edited token,
    and -1 puts it behind every possible row value.
    """
    raw = claims.get(SESSION_VERSION_CLAIM, 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


def verify_session_token(token: str) -> dict | None:
    """The claims of a valid session cookie, or None. Signature AND shape.

    A SESSION IS A TOKEN THAT CARRIES AN IDENTITY, and nothing else is. The
    shape check is not belt-and-braces: AUTH_SECRET signs a second kind of token
    since Google sign-in landed — the ten-minute OAuth state/nonce cookie in
    app/google_auth.py, which has no server-side store to live in — and that one
    deliberately carries no userId and no role. It authorises nothing (every
    require_* guard reads `role` through .get() and refuses), but it IS
    structurally a valid HS256 JWT under this secret, and the consumers past the
    guards index session["userId"] / session["role"] directly: pasted into
    `reep_session` by the browser's own owner, it turned an unauthenticated
    request into a 500 instead of a clean 401. google_auth.read_flow_state
    already refuses the mirror case; this closes the other direction. Every
    payload _payload_for() mints has both keys, so no real session changes.

    REVOCATION LIVES HERE, not in app/identity.py, because this is the ONE function
    that turns a cookie into an identity — the HTTP dependency, the WebSocket
    dependency and every test read the session through it. A revocation check
    bolted onto one caller is a revocation check the next caller forgets.

    OFFBOARDING LIVES HERE FOR EXACTLY THE SAME REASON (B3.3). A disabled
    account is refused on every authenticated request, on every route, without
    any route knowing about it. The bump that `POST /admin/users/{id}/disable`
    makes would already retire the sessions that EXIST; this is what makes the
    refusal survive anything that mints a fresh one.
    """
    try:
        claims = jwt.decode(token, settings.auth_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if not isinstance(claims, dict) or not claims.get("userId") or not claims.get("role"):
        return None
    # STRICTLY BEHIND is revoked; equal or ahead is admitted. `<` rather than
    # `!=` on purpose: "ahead" cannot happen in normal operation, only after a
    # database restore rolls the counter back, and in that case admitting the
    # sessions people are actually holding beats logging the whole college out
    # of a system that has just been through an incident.
    state = account_state(str(claims["userId"]))
    if state.version < 0:
        return None
    if state.disabled:
        return None
    if _claimed_version(claims) < state.version:
        return None
    return claims


#: The value of the `X-Reep-Session` header on a 401 whose cookie was RETIRED
#: rather than merely expired or absent. See session_was_retired.
SESSION_RETIRED_HEADER = "X-Reep-Session"
SESSION_RETIRED_VALUE = "retired"


def session_was_retired(token: str | None) -> bool:
    """Was this cookie well-formed and unexpired, but superseded?

    True only when the token verifies cryptographically, is inside its `exp`,
    carries a real identity, and its version is BEHIND the row — which happens
    for exactly two reasons: the account signed out, or the account signed in
    somewhere else and one-device-at-a-time retired this one.

    WHY THE DISTINCTION IS WORTH A FUNCTION. Under one device at a time this is
    no longer an incident, it is a Tuesday: a student who opens REEP on their
    phone drops their laptop, mid-form. A screen that simply reappears at
    /login, saying nothing, reads as an app that logs you out at random — so
    they sign in on the laptop, drop the phone, and learn not to trust it. The
    server is the only party that knows which of the two happened, so it says.

    Deliberately NOT part of `verify_session_token`'s return: that function
    answers one question for every caller in the codebase, and widening it into
    a reason-carrying result would touch the HTTP dependency, the WebSocket
    dependency and every test that reads a session. This is a second, narrow
    question asked only on the failure path.
    """
    if not token:
        return False
    try:
        claims = jwt.decode(token, settings.auth_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        # Forged, tampered, signed with another secret — or simply expired,
        # which is the ordinary end of a session and needs no explanation.
        return False
    if not isinstance(claims, dict) or not claims.get("userId") or not claims.get("role"):
        return False
    state = account_state(str(claims["userId"]))
    # A negative sentinel means the lookup FAILED. "We could not check" must not
    # be reported to the student as "someone else signed in".
    if state.version < 0:
        return False
    # A DISABLED account's version is behind too — `disable` bumps it — but the
    # sentence this header buys ("you signed in on another device") is then a
    # lie, and a worse one than saying nothing: it tells an offboarded faculty
    # member to go and sign in again. They get the plain "Sign in required" and
    # the truth from the person who disabled them.
    if state.disabled:
        return False
    return _claimed_version(claims) < state.version


#: The value of `X-Reep-Session` on a 401 whose cookie belongs to an account the
#: office DISABLED. See session_was_disabled.
SESSION_DISABLED_VALUE = "disabled"


def session_was_disabled(token: str | None) -> bool:
    """Was this cookie genuine, but its account disabled by the Main Admin?

    The other half of the comment above (2026-10-17, the owner's request):
    "they get the plain Sign in required" left a student disabled mid-session
    at a login screen with no word of why, signing in again into the same
    refusal. The sign-in doors already tell them; this lets the login screen
    say it too, the moment their open session is cut.

    TRUE ONLY FOR A DISABLE, never for a removal. `account_state` folds both
    into one flag because a cookie may not act for either, but the sentence the
    screen prints is about a security issue the office is holding, and a
    student removed from the roster has not been told that. So the row is read
    again for `disabled_at` alone: one query, on the failure path of a cookie
    that verified, which is rare.

    A lookup that fails answers False, for `session_was_retired`'s reason: "we
    could not check" must not be reported as a fact about the account.
    """
    if not token:
        return False
    try:
        claims = jwt.decode(token, settings.auth_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return False
    if not isinstance(claims, dict) or not claims.get("userId") or not claims.get("role"):
        return False
    state = account_state(str(claims["userId"]))
    if state.version < 0 or not state.disabled:
        return False
    try:
        from .db import SessionLocal
        from .models.user import User

        with SessionLocal() as db:
            disabled_at = db.scalar(
                select(User.disabled_at).where(User.id == str(claims["userId"]))
            )
    except Exception:
        log.exception("could not read disabled_at for user %s", claims["userId"])
        return False
    return disabled_at is not None
