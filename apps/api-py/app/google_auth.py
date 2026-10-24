"""Google OIDC — the verification layer behind GET /api/auth/sso/google/*.

Deliberately FastAPI-free. Nothing here imports fastapi, touches a Request or
raises HTTPException: it is the part that must be true regardless of transport —
fetch Google's public keys, verify an ID token's signature against them, and
decide whether the claims inside describe a real, verified Google identity. The
router (app/routers/auth.py) owns cookies, redirects and status codes, and is the
only place a failure becomes an HTTP response. That split is what makes this
module testable without a client and reusable if the flow ever moves.

WHAT IS AND IS NOT TRUSTED

  * The ID TOKEN is trusted, and only after `verify_id_token` returns. Its
    signature is checked against Google's published RSA keys, `aud` must equal
    our client id, `iss` must be one of Google's two spellings, `exp` must be in
    the future, the `nonce` must match the one this server minted for this
    flow, and `email_verified` must be true. An unverified email claim is not an
    identity: on a consumer Google account an unverified address can be an
    address the holder does not control, and REEP matches accounts BY EMAIL —
    so accepting it would hand someone else's roster row to whoever typed the
    address in.
  * A userinfo/tokeninfo response is NOT used. Both are "ask Google who this is"
    round-trips whose answer arrives over a channel we would then have to trust;
    the signed token already carries the answer and proves Google wrote it.
  * The `hd` (hosted domain) claim is NOT used as access control. The roster is
    the allowlist (AGENTS.md: staff scope is decided by role, and here account
    existence is decided by the roster), and a domain check on top of it would
    only add a second way to lock out a legitimately enrolled student.

Configuration lives in app/config.py: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and
the optional GOOGLE_REDIRECT_URI. All blank -> `sso_ready()` is False and the
router refuses with 503 rather than bouncing a student to a broken Google page,
the same "feature off means say so" shape GET /api/voice/status and
GET /api/interview/status already use.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
from typing import Any, NamedTuple
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWK, PyJWKSet

from .config import settings

log = logging.getLogger(__name__)

# Google's OAuth 2.0 / OIDC endpoints. Hard-coded rather than read from the
# discovery document (https://accounts.google.com/.well-known/openid-configuration)
# on purpose: discovery adds a second network round-trip and a second thing that
# can be down in front of every sign-in, and these three URLs have been stable for
# a decade. If Google ever moves them, this is the one place to change.
_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_JWKS_ENDPOINT = "https://www.googleapis.com/oauth2/v3/certs"

# Google spells its issuer both ways and always has; a token carrying either is
# genuine, so both must be accepted or roughly half of all sign-ins fail for a
# reason nobody can reproduce.
_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})

# Google signs ID tokens with RS256 and publishes only RSA keys at the JWKS
# endpoint above. Pinning the list is what stops algorithm confusion: without it
# a token whose header says `alg: none`, or `alg: HS256` signed with the public
# modulus as an HMAC key, would verify.
_ALGORITHMS = ["RS256"]

# The claims that must be PRESENT, not merely valid if present. PyJWT verifies
# `exp` only when there is an `exp`; a token with the claim omitted would sail
# through every expiry check. `require` closes that.
_REQUIRED_CLAIMS = ["iss", "aud", "exp", "iat", "sub", "email"]

# Clock skew tolerance for exp/iat. A dev box a minute behind Google would
# otherwise reject every freshly minted token as "not yet valid", which reads as
# "Google sign-in is broken" and is impossible to guess from the error.
_CLOCK_SKEW_SECONDS = 60

# How long Google's public keys are cached in this process.
#
# WHY CACHING MATTERS: verifying a signature needs the public key, and without a
# cache every single sign-in would make a blocking HTTPS request to Google before
# it could even start. That is a second network dependency on the critical path
# of the ONLY way into the app once password login is refused in production — if
# googleapis.com is slow, every login is slow; if it is unreachable, nobody can
# sign in at all, including staff trying to diagnose it. Google's certs endpoint
# serves a Cache-Control max-age of several hours and keeps retired keys
# published well past rotation, so an hour is conservative: keys stay fresh
# enough that a rotation is picked up promptly, while a burst of 33 students
# signing in at 9am costs one fetch, not 33.
_JWKS_TTL_SECONDS = 60 * 60

# Floor between forced refetches. An unknown `kid` triggers an immediate refetch
# so a key rotation mid-TTL does not break sign-in — but `kid` is attacker-chosen
# (it is just a header field on an unverified token), so without a floor anyone
# could turn one HTTP request to us into one HTTP request to Google, forever.
_JWKS_MIN_REFETCH_SECONDS = 60

# How long to leave Google alone after a FAILED fetch.
#
# WHY A SEPARATE CLOCK: the two guards above are both measured from the last
# SUCCESSFUL fetch, so while googleapis.com is down neither of them holds — the
# cache reads as permanently expired and every sign-in re-enters the fetch,
# parking for _HTTP_TIMEOUT_SECONDS INSIDE `_jwks_lock`. Those attempts then
# serialise: a handful of students at 9am become a queue minutes long, and
# because these are `def` endpoints running in Starlette's shared threadpool the
# starvation takes every other synchronous route down with them. A cached key
# still verifies perfectly, so the retry is an optimisation, never the sign-in
# path — it is exactly the thing that should back off first.
_JWKS_FAILURE_BACKOFF_SECONDS = 60

# Google is on the critical path of sign-in; a hung socket must fail fast enough
# that the student sees an error rather than a spinner that never resolves.
_HTTP_TIMEOUT_SECONDS = 10.0

# Path the callback is mounted at, used to derive the redirect_uri when
# GOOGLE_REDIRECT_URI is blank. Must stay in step with the route in
# app/routers/auth.py, with the value documented in .env.example, and with the
# Authorised redirect URI on the OAuth client — Google compares the string it
# was given at /authorize with the one sent at token exchange, byte for byte.
CALLBACK_PATH = "/api/auth/sso/google/callback"

# The transient cookie carrying the CSRF state across the hop to Google, and its
# lifetime. Ten minutes is long enough to pick an account and type a password,
# short enough that an abandoned flow leaves nothing lying around.
FLOW_COOKIE = "reep_oauth_state"
FLOW_TTL_SECONDS = 600

# Marks the short-lived flow token as NOT a session. There is no server-side
# session store to hang OAuth state on — the JWT is the only state this app has
# — so the flow state travels in a cookie, and the risk that creates is a token
# minted for one purpose being replayed as the other. Two independent things
# stop that: this claim, checked in `read_flow_state`, and the DERIVED KEY below.
_FLOW_PURPOSE = "google-oauth-state"

# Domain separation label for the flow token's signing key. A session says "this
# person is signed in as X"; a flow token says "this browser started a sign-in".
# Different statements, so different keys — signing both with AUTH_SECRET made
# each one structurally a valid instance of the other, and a signature check
# cannot tell them apart. Derived rather than a second env var: one secret to
# configure, nothing to rotate, and an existing deployment needs no .env change.
_FLOW_KEY_LABEL = b"reep.google-oauth-state\x00"

_jwks: PyJWKSet | None = None
_jwks_fetched_at: float = 0.0
# Monotonic deadline set when a fetch FAILS; until it passes, do not re-dial
# Google. Separate from _jwks_fetched_at because that one means "the cache is
# this fresh" and a failure must not be allowed to claim freshness it does not
# have — the stale keys are still served, they are just not re-requested.
_jwks_retry_after: float = 0.0
# FastAPI runs `def` endpoints in a threadpool, so two sign-ins really can enter
# this module at once. The lock is held across the fetch on purpose: it collapses
# a thundering herd at TTL expiry into a single request to Google.
_jwks_lock = threading.Lock()


class GoogleAuthError(Exception):
    """A Google sign-in that cannot be trusted, with the code the browser sees.

    `code` is the `?error=` value the callback redirects to /login with, so the
    Angular login screen can say something specific. `args[0]` is the operator-
    facing cause and is logged, never rendered.
    """

    def __init__(self, message: str, *, code: str = "sso_failed") -> None:
        super().__init__(message)
        self.code = code


class GoogleIdentity(NamedTuple):
    """The only four things taken from a verified ID token."""

    sub: str
    email: str
    name: str
    id_token_claims: dict[str, Any]


class FlowState(NamedTuple):
    """What `new_flow_state` stashed in the cookie, once validated."""

    state: str
    nonce: str
    next_path: str


def _flow_secret() -> str:
    """The signing key for the flow cookie — AUTH_SECRET, domain-separated.

    Read fresh on every call rather than computed at import, so a test (or a
    reload) that swaps `settings.auth_secret` is honoured, exactly as
    app/security.py reads it per call.
    """
    return hashlib.sha256(_FLOW_KEY_LABEL + settings.auth_secret.encode()).hexdigest()


def sso_ready() -> bool:
    """Whether Google sign-in is configured on this server.

    Delegates to `settings.google_ready` rather than re-testing the two
    credentials, so the capability probe the login screen reads and the gate the
    router enforces can never disagree — a drift there would show a live Google
    button that refuses, or hide a working one.

    Blank credentials mean "feature off", the house contract every optional
    provider in app/config.py uses.
    """
    return settings.google_ready


def redirect_uri() -> str:
    """The redirect_uri sent to Google, and re-sent at token exchange.

    Derived from WEB_ORIGIN, never from the inbound request. In dev the browser
    is on http://localhost:4200 and apps/web/proxy.conf.json forwards /api with
    `changeOrigin: true`, so this process sees `Host: localhost:3300` — a
    `request.url_for(...)` redirect_uri would come out as :3300, which is not the
    origin the browser is on and does not match the entry registered in the
    Google console. GOOGLE_REDIRECT_URI overrides it for deployments where the
    public URL is not WEB_ORIGIN (a proxy in front, a different API host).
    """
    explicit = settings.google_redirect_uri.strip()
    if explicit:
        return explicit
    return f"{settings.web_origin.rstrip('/')}{CALLBACK_PATH}"


def new_flow_state(next_path: str) -> tuple[FlowState, str]:
    """Mint one sign-in attempt: the FlowState, and the cookie value carrying it.

    The cookie value is a short-lived HS256 token holding the state, the nonce
    and the post-login destination. There is nowhere else to put them: this app
    has no server-side session store, and the browser is about to leave for
    accounts.google.com, which destroys all in-memory Angular state.
    """
    flow = FlowState(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        next_path=next_path,
    )
    now = int(time.time())
    cookie_value = jwt.encode(
        {
            "purpose": _FLOW_PURPOSE,
            "state": flow.state,
            "nonce": flow.nonce,
            "next": flow.next_path,
            "iat": now,
            "exp": now + FLOW_TTL_SECONDS,
        },
        _flow_secret(),
        algorithm="HS256",
    )
    return flow, cookie_value


def read_flow_state(cookie_value: str | None, query_state: str | None) -> FlowState:
    """Validate the callback's `state` against the cookie this server set.

    THE ATTACK THIS STOPS is login CSRF (a.k.a. session fixation via OAuth). An
    attacker starts a Google sign-in as THEMSELVES, stops at the callback, and
    keeps the resulting `?code=`. They then get a victim — a mentor, say — to
    load that callback URL: a plain top-level GET, so SameSite=Lax does nothing
    and no click on our site is required. With no state check the server would
    happily exchange the attacker's code and set the victim's browser to a valid
    reep_session for the ATTACKER'S account. The victim then works inside an
    account the attacker can also open: every resume they upload, every note they
    write, lands somewhere the attacker reads at leisure. Because the code was
    minted for the attacker's own flow, nothing about the exchange looks wrong.

    The state proves the callback finishes a flow THIS browser started, because
    the matching half arrived in a cookie only this server could have written.

    SINGLE USE: the caller deletes the cookie on every callback outcome, success
    or failure, before doing anything else. Replaying the same callback URL then
    arrives with no cookie and is rejected here. That is also why the state is
    not merely a signed value with no cookie — a signature alone is replayable
    for as long as it is unexpired.
    """
    if not query_state:
        raise GoogleAuthError("callback carried no state parameter", code="sso_state")
    if not cookie_value:
        raise GoogleAuthError(
            f"callback carried no {FLOW_COOKIE} cookie (expired, replayed, or third-party "
            "cookies blocked)",
            code="sso_state",
        )
    try:
        claims = jwt.decode(cookie_value, _flow_secret(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise GoogleAuthError(f"state cookie did not verify: {exc}", code="sso_state") from exc

    if claims.get("purpose") != _FLOW_PURPOSE:
        raise GoogleAuthError("state cookie is not a sign-in flow token", code="sso_state")

    stored_state = claims.get("state") or ""
    # compare_digest, not ==: the comparison is against an attacker-supplied
    # value, and it costs nothing to not leak its prefix through timing.
    #
    # The isascii() guards are not decoration. compare_digest RAISES TypeError
    # on a `str` carrying a character outside ASCII — it does not return False —
    # and `state` arrives raw off the query string. Unhandled, that TypeError is
    # a bare FastAPI 500 page on a top-level browser navigation: the one outcome
    # the callback's contract says can never happen, reachable by anyone who
    # starts a real flow and then follows `...&state=évil`. Our own state is
    # secrets.token_urlsafe, so anything non-ASCII is already not ours and can
    # be refused before it is ever compared.
    if not isinstance(query_state, str) or not query_state.isascii():
        raise GoogleAuthError("state parameter is not an ASCII token", code="sso_state")
    if not isinstance(stored_state, str) or not stored_state.isascii() or not stored_state:
        raise GoogleAuthError("state cookie carried no usable state", code="sso_state")
    if not hmac.compare_digest(stored_state, query_state):
        raise GoogleAuthError("state parameter does not match the state cookie", code="sso_state")

    nonce = claims.get("nonce") or ""
    if not nonce:
        raise GoogleAuthError("state cookie carried no nonce", code="sso_state")

    return FlowState(state=stored_state, nonce=nonce, next_path=claims.get("next") or "")


def authorization_url(state: str, nonce: str) -> str:
    """Where to send the browser to start the flow.

    `nonce` is echoed into the ID token and checked in `verify_id_token`, which
    binds the token to THIS flow: a token harvested from somewhere else, even a
    valid one for the same client, carries a different nonce and is refused.
    """
    params = {
        "client_id": settings.google_client_id.strip(),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        # `openid email profile` is the whole ask: a subject, the address the
        # roster is keyed on, and a display name. No Gmail, no Drive, no
        # offline_access — nothing that would need a refresh token to store.
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        # Online only. REEP acts as the student, never on their behalf while they
        # are away, so there is no refresh token to obtain or to protect.
        "access_type": "online",
        # Students share machines in the lab. Without this, Google silently reuses
        # whichever account is already signed in and the previous student's
        # dashboard appears, which looks exactly like a data leak.
        "prompt": "select_account",
    }
    return f"{_AUTH_ENDPOINT}?{urlencode(params)}"


def _fetch_jwks() -> PyJWKSet:
    resp = httpx.get(_JWKS_ENDPOINT, timeout=_HTTP_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return PyJWKSet.from_dict(resp.json())


def _jwks_set(*, force: bool) -> PyJWKSet:
    """Google's signing keys, cached for _JWKS_TTL_SECONDS.

    `force` asks for a refetch mid-TTL (an unknown kid, i.e. a rotation), subject
    to the _JWKS_MIN_REFETCH_SECONDS floor.

    On a fetch failure with keys already cached, the STALE keys are served rather
    than failing the sign-in. That is safe in one direction only, which is the
    direction that matters: a stale public key can fail to verify a token signed
    with a newer key, but it can never make an invalid signature verify. Serving
    them means a blip at googleapis.com does not lock the whole college out —
    and _JWKS_FAILURE_BACKOFF_SECONDS is what stops that same blip being re-dialled
    once per sign-in, under this lock, for as long as it lasts.
    """
    global _jwks, _jwks_fetched_at, _jwks_retry_after

    with _jwks_lock:
        now = time.monotonic()
        age = now - _jwks_fetched_at
        if _jwks is not None:
            if not force and age < _JWKS_TTL_SECONDS:
                return _jwks
            if force and age < _JWKS_MIN_REFETCH_SECONDS:
                return _jwks
        if now < _jwks_retry_after:
            # Google failed recently. Cached keys still verify every token signed
            # before the outage, so serve them; with nothing cached there is
            # nothing to serve and refusing IMMEDIATELY is the point — a fast,
            # legible refusal beats parking this thread on a socket that is
            # already known to be dead.
            if _jwks is not None:
                return _jwks
            raise GoogleAuthError(
                f"Google JWKS ({_JWKS_ENDPOINT}) is unreachable and nothing is cached; "
                f"not retrying for another {_jwks_retry_after - now:.0f}s",
                code="sso_token",
            )
        try:
            # Into a LOCAL first: assigning `_jwks` from the call would let a
            # future _fetch_jwks that returns instead of raising clobber a good
            # cache with an unusable one.
            fetched = _fetch_jwks()
        except (httpx.HTTPError, jwt.PyJWTError, ValueError, KeyError) as exc:
            # jwt.PyJWTError is load-bearing, not padding: PyJWKSet.from_dict
            # raises PyJWKSetError for an empty or unusable `keys` list and
            # MissingCryptographyError when `cryptography` is gone, and NEITHER
            # is a ValueError. Without it both escape this function, escape the
            # router's `except GoogleAuthError`, and 500 the callback — while
            # also skipping the stale-key fallback that is the whole reason a
            # Google blip does not lock the college out.
            _jwks_retry_after = time.monotonic() + _JWKS_FAILURE_BACKOFF_SECONDS
            if _jwks is None:
                raise GoogleAuthError(
                    f"could not fetch Google JWKS from {_JWKS_ENDPOINT}: {exc}",
                    code="sso_token",
                ) from exc
            log.warning(
                "could not refresh Google JWKS (%s) — serving keys cached %.0fs ago, "
                "next attempt in %ds",
                exc,
                age,
                _JWKS_FAILURE_BACKOFF_SECONDS,
            )
        else:
            _jwks = fetched
            _jwks_fetched_at = time.monotonic()
            _jwks_retry_after = 0.0
            log.info("fetched Google JWKS (%d keys)", len(_jwks.keys))
        return _jwks


def _signing_key(kid: str | None) -> PyJWK:
    """The public key for this token's `kid`, refetching once on a miss."""
    if not kid:
        raise GoogleAuthError("ID token header carried no kid", code="sso_identity")
    try:
        return _jwks_set(force=False)[kid]
    except KeyError:
        pass
    try:
        return _jwks_set(force=True)[kid]
    except KeyError as exc:
        raise GoogleAuthError(
            f"no Google signing key for kid {kid!r} (rotation, or a token from elsewhere)",
            code="sso_identity",
        ) from exc


def exchange_code(code: str) -> str:
    """Trade the one-time authorization code for the ID token, and return it raw.

    The redirect_uri is sent again because Google checks it matches the one the
    code was issued for — a code stolen from a log cannot be redeemed against a
    different callback. The client secret proves the exchange comes from this
    server and not from the browser that carried the code.

    Nothing about the response is trusted here; the caller must run the result
    through `verify_id_token`. This is an HTTPS call to Google, so the transport
    is authenticated — but the signature check is what makes the CLAIMS true, and
    it is cheap.
    """
    try:
        resp = httpx.post(
            _TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": settings.google_client_id.strip(),
                "client_secret": settings.google_client_secret.strip(),
                "redirect_uri": redirect_uri(),
                "grant_type": "authorization_code",
            },
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise GoogleAuthError(
            f"token exchange could not reach Google: {exc}", code="sso_token"
        ) from exc

    if resp.status_code != 200:
        # Google's error body names the cause (redirect_uri_mismatch,
        # invalid_client, invalid_grant) and is the single most useful line in
        # the log when a deployment's console entry is wrong. It contains no
        # student data — the code is already spent — so it is safe to log.
        raise GoogleAuthError(
            f"token exchange -> HTTP {resp.status_code}: {resp.text[:400]}",
            code="sso_token",
        )

    try:
        payload = resp.json() or {}
    except ValueError as exc:
        # A 200 carrying something that is not JSON — a captive portal, a proxy
        # error page, a TLS-intercepting middlebox. httpx raises here, and an
        # unhandled ValueError on this path is a raw 500 in the browser instead
        # of the redirect this flow promises. The body is truncated into the log
        # because it names the interceptor; the code is already spent, and no
        # student data has been fetched yet.
        raise GoogleAuthError(
            f"token exchange returned a non-JSON body: {resp.text[:200]}",
            code="sso_token",
        ) from exc

    id_token = payload.get("id_token")
    if not id_token:
        raise GoogleAuthError("token exchange returned no id_token", code="sso_token")
    return id_token


def verify_id_token(id_token: str, *, nonce: str) -> GoogleIdentity:
    """Verify a Google ID token and return the identity inside it.

    Checks, in order: the header names RS256; the signature verifies against
    Google's published key for that kid; `aud` equals our client id; `iss` is one
    of Google's two issuer spellings; `exp` is in the future and `iat` sane (60s
    skew); iss/aud/exp/iat/sub/email are all PRESENT; the `nonce` matches this
    flow; and `email_verified` is true.

    Raises GoogleAuthError on every failure. A return value means the email may
    be used to look up a roster row — and nothing weaker ever should.
    """
    client_id = settings.google_client_id.strip()
    if not client_id:
        raise GoogleAuthError("GOOGLE_CLIENT_ID is not configured", code="sso_config")

    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise GoogleAuthError(
            f"ID token header is not readable: {exc}", code="sso_identity"
        ) from exc

    # Belt to the `algorithms=` brace below. Reading the header is reading
    # UNVERIFIED attacker-controlled data, and this is the only thing taken from
    # it: the kid, to pick a key, and the alg, to refuse early and legibly.
    if header.get("alg") not in _ALGORITHMS:
        raise GoogleAuthError(
            f"ID token alg is {header.get('alg')!r}, expected RS256", code="sso_identity"
        )

    key = _signing_key(header.get("kid"))
    try:
        claims = jwt.decode(
            id_token,
            key=key.key,
            algorithms=_ALGORITHMS,
            audience=client_id,
            issuer=_ISSUERS,
            leeway=_CLOCK_SKEW_SECONDS,
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.PyJWTError as exc:
        raise GoogleAuthError(f"ID token did not verify: {exc}", code="sso_identity") from exc

    token_nonce = claims.get("nonce") or ""
    # isascii(): see read_flow_state. compare_digest raises TypeError rather than
    # returning False on a non-ASCII str, and every claim in this token was
    # attacker-shaped until the signature check a few lines above — a `nonce`
    # claim is not constrained by that check to be anything in particular.
    if not isinstance(token_nonce, str) or not token_nonce.isascii():
        raise GoogleAuthError("ID token nonce is not an ASCII token", code="sso_identity")
    if not hmac.compare_digest(token_nonce, nonce):
        raise GoogleAuthError(
            "ID token nonce does not match this sign-in flow", code="sso_identity"
        )

    # Google sends a JSON boolean; some OIDC providers send the string "true".
    # Accept both spellings of TRUE and nothing else — absent, false, or anything
    # unexpected is not a verified address.
    verified = claims.get("email_verified")
    if verified is not True and str(verified).strip().lower() != "true":
        raise GoogleAuthError(
            "Google reports this address as unverified", code="sso_unverified_email"
        )

    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise GoogleAuthError("ID token carried no email", code="sso_identity")

    return GoogleIdentity(
        sub=claims["sub"],
        email=email,
        # Google omits `name` when the profile scope is withheld; the roster name
        # is authoritative anyway, so this is only a fallback for display.
        name=(claims.get("name") or "").strip(),
        id_token_claims=claims,
    )
