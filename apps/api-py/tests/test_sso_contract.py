"""The Google sign-in `?error=` vocabulary is one contract in three files.

WHY THIS TEST EXISTS. The callback in app/routers/auth.py redirects a refused
sign-in to `{WEB_ORIGIN}/login?error=<code>`, and the Angular login screen turns
that code into a sentence. The two lists were first written independently and
shared NOT ONE code: the server emitted `sso_not_enrolled`, the screen handled
`not_on_roster`, and every refusal — including the commonest one, a student
using a personal Gmail — rendered as "the reason given is not one this page
knows". Every honest message on the screen was unreachable.

Nothing caught it. pytest does not read TypeScript, `tsc` and `ng build` do not
know what the server emits, and both halves were internally consistent. A string
comparison across the two files is the only thing that can, so it lives here.

The tests read the SOURCE rather than importing, because the frontend half is
TypeScript. That makes them regex-fragile by nature, so each one fails loudly
with the sets it found rather than on a bare assert.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_API = Path(__file__).resolve().parent.parent / "app"
_WEB = (
    Path(__file__).resolve().parents[3]
    / "apps"
    / "web"
    / "src"
    / "app"
    / "features"
    / "login"
)

_ROUTER = _API / "routers" / "auth.py"
_GOOGLE_AUTH = _API / "google_auth.py"
_LOGIN_TS = _WEB / "login.component.ts"


def _codes_emitted() -> set[str]:
    """Every code that can reach the browser, from BOTH places one is minted.

    The router names some literally (`_sso_failure("sso_config")`) and passes the
    rest straight through (`_sso_failure(exc.code)`), so app/google_auth.py's
    `GoogleAuthError(..., code=...)` values are just as much part of the wire
    contract — they are simply written a file away. Reading only one of the two
    is how `sso_state`, `sso_identity` and `sso_unverified_email` would look
    unemitted while being three of the four codes a student actually meets.
    """
    literal = set(
        re.findall(r"_sso_failure\(\s*[\"']([a-z_]+)[\"']", _ROUTER.read_text(encoding="utf-8"))
    )
    raised = set(re.findall(r'code="([a-z_]+)"', _GOOGLE_AUTH.read_text(encoding="utf-8")))
    return literal | raised


def _codes_documented() -> set[str]:
    """The table in the router's module docstring — the stated contract."""
    src = _ROUTER.read_text(encoding="utf-8")
    docstring = src.split('"""')[1]
    block = docstring.split("The codes are:")[1]
    return set(re.findall(r"^\s{4}(sso_[a-z_]+)\s+\S", block, re.MULTILINE))


def _codes_handled() -> set[str]:
    """Every `case '...'` in the login screen's messageFor."""
    src = _LOGIN_TS.read_text(encoding="utf-8")
    body = src.split("function messageFor(")[1].split("\n}")[0]
    return set(re.findall(r"case\s+'([a-z_]+)'", body))


def test_the_login_screen_handles_every_code_the_router_emits() -> None:
    emitted, handled = _codes_emitted(), _codes_handled()
    missing = emitted - handled
    assert not missing, (
        f"app/routers/auth.py redirects with {sorted(missing)}, which "
        f"login.component.ts's messageFor does not handle — each one renders as "
        f"'the reason given is not one this page knows'. It handles {sorted(handled)}."
    )


def test_the_login_screen_promises_no_refusal_the_router_cannot_emit() -> None:
    emitted, handled = _codes_emitted(), _codes_handled()
    invented = handled - emitted
    assert not invented, (
        f"login.component.ts has copy for {sorted(invented)}, which nothing in "
        f"app/routers/auth.py emits. Dead branches are how `wrong_domain` came to "
        f"tell students a personal Gmail cannot sign in when the roster is the "
        f"only check there is."
    )


def test_the_router_docstring_lists_exactly_what_the_router_emits() -> None:
    emitted, documented = _codes_emitted(), _codes_documented()
    assert emitted == documented, (
        f"the callback docstring's code table is the stated contract and has "
        f"drifted: emitted={sorted(emitted)} documented={sorted(documented)}."
    )


@pytest.mark.parametrize(
    "field",
    ["google_available", "password_login_available", "domain", "reason"],
)
def test_the_probe_field_names_match_on_both_sides(field: str) -> None:
    """GET /api/auth/sso/status is snake_case on the wire, in both files.

    The first cut of the client declared `{configured, domain, reason}` against a
    server sending `{google_available, ...}`. `undefined === false` is false, so
    the disabled-button branch could never run and the probe's whole purpose —
    not rendering a live button on a server with no credentials — was silently
    unachieved.
    """
    from app.routers.auth import SsoStatus

    assert field in SsoStatus.model_fields, f"the router no longer sends {field}"
    ts = _LOGIN_TS.read_text(encoding="utf-8")
    interface = ts.split("interface SsoStatus {")[1].split("}")[0]
    assert field in interface, (
        f"login.component.ts's SsoStatus interface does not name {field}, so the "
        f"probe silently reads undefined for it"
    )


def test_the_login_screen_calls_paths_the_router_actually_serves() -> None:
    """Both the button and the probe, against the router's real route table."""
    from app.routers.auth import router
    from app.routers.passwords import router as passwords_router

    # Two routers share the /auth prefix: auth.py owns sign-in, passwords.py
    # owns credentials (activate / forgot / reset / change-password). The login
    # screen's "Forgot password?" form calls the second, so the contract is
    # against the union — the property being pinned is "no 404 that fails
    # open", not "everything lives in one file".
    served = {r.path for r in router.routes} | {r.path for r in passwords_router.routes}
    ts = _LOGIN_TS.read_text(encoding="utf-8")
    called = set(re.findall(r"environment\.apiBase\}(/auth/[a-z/]+)", ts))

    assert called, "no /auth/... URL found in login.component.ts — did it move?"
    missing = called - served
    assert not missing, (
        f"login.component.ts calls {sorted(missing)}, which neither app/routers/auth.py nor passwords.py "
        f"does not serve. It serves {sorted(served)}. A 404 here fails OPEN: the "
        f"probe gives up and the Google button renders live regardless."
    )


def test_the_login_screen_prints_the_disabled_sentence_the_server_answers() -> None:
    """One sentence, two files (2026-10-17). The password and code doors print
    `DISABLED_SIGN_IN_MESSAGE` verbatim; Google's `sso_disabled` and
    `?signedOut=disabled` print the screen's own copy. Edit one and the person
    reads two different explanations of the same lock-out."""
    from app.routers.auth import DISABLED_SIGN_IN_MESSAGE

    src = _LOGIN_TS.read_text(encoding="utf-8")
    block = src.split("export const ACCOUNT_DISABLED_MESSAGE =")[1].split(";")[0]
    on_screen = "".join(re.findall(r"'([^']*)'", block))
    assert on_screen == DISABLED_SIGN_IN_MESSAGE, (
        f"login.component.ts says {on_screen!r}; the server says "
        f"{DISABLED_SIGN_IN_MESSAGE!r}"
    )
