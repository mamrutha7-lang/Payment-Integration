#!/usr/bin/env python3
"""Prove that every capability in the catalogue is checked somewhere in app/.

WHY THIS EXISTS. `app/models/governance.py` says it in words already:

    "Adding a capability is a code change on purpose: each one has to be
     enforced at a call site, and a row that names a capability nothing checks
     is a promise the API does not keep."

A key nobody checks is worse than a missing feature, because it is VISIBLE. It
appears in Governance, the Main Admin grants it to a faculty member with a
reason, the audit row records the grant -- and nothing anywhere changes. The
office believes it handed something over; the holder believes they were given
something; the API never asked. That is a lie with a paper trail, and the only
moment anyone can catch it is the moment the key is added.

So this is the counterpart of `tools/ci/check_pii_gate.py`: a static AST walk
that asks the source, not the runtime, whether a human wired the key up.

WHAT IT LOOKS FOR. Every call to `require_capability`, `has_capability` or
`policies.scope_filter`, ANYWHERE under `app/` -- not only inside a route
handler. `admin.interview_audio` is checked inside `interview_records.py`'s
`_require_developer` helper and reaches four endpoints through it; a scan that
only inspected `@router`-decorated functions would report it unenforced and send
the next person to "fix" a thing that works.

MODULE-LEVEL CONSTANTS ARE RESOLVED. Three routers -- admin_students.py,
swoc.py, interview_bank.py -- declare `CAPABILITY = "admin.x"` once and pass the
NAME at sixteen call sites. A grep for string literals finds none of them and
demands they be enforced, which is exactly the false alarm that teaches people
to switch a guard off.

THERE ARE NO EXEMPTIONS, AND KEEPING IT THAT WAY IS THE POINT.

`EXEMPT` is empty. It held exactly one key for the length of the 2026-09
redesign -- `ui.console_v2`, a switch that selected which admin console the
CLIENT rendered, off `/auth/me`, with no server request to refuse -- and Phase 5
deleted the key and the entry together.

An exemption is how this guard stops being a guard: with one on the list,
"enforce every catalogue key or delete it" becomes "enforce it, delete it, or
write your name on a list", and the third option is always the cheapest on the
afternoon somebody needs it. The dict is kept (rather than a set, or nothing at
all) so that adding a key REQUIRES writing the reason in the source next to it,
where a reviewer meets it -- not in a commit message nobody reads again. A key
that genuinely cannot be enforced server-side is strong evidence it should be a
FeatureOverride or nothing at all, and `tests/test_codebase_guards.py` asserts
this dict is EMPTY so that conversation happens in review.

Run it:

    python3 apps/api-py/tools/ci/check_capability_enforcement.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

#: Every function that asks "may this session use this key". `scope_filter` is
#: here because narrowing a list IS the enforcement for a list endpoint -- it
#: answers "what may you see" rather than "may you be here", and a key whose
#: only job is to narrow a list is properly enforced by it.
GATES = {"require_capability", "has_capability", "scope_filter"}

#: The argument position and keyword the key arrives on. All three gates take
#: (db, session, key), so one rule covers them.
KEY_POSITION = 2
KEY_KEYWORD = "key"

#: Keys that cannot be enforced server-side, each with the reason written here
#: rather than in a commit message. EMPTY since Phase 5, and a test asserts that.
#: Read the module docstring before adding one -- adding one is the whole risk.
EXEMPT: dict[str, str] = {}

API = Path(__file__).resolve().parents[2]
APP = API / "app"
CATALOGUE = APP / "models" / "governance.py"


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:  # a broken file is a different job's failure
        print(f"skipping {path}: {exc}", file=sys.stderr)
        return None


def catalogue_keys() -> list[str]:
    """The keys `CAPABILITIES` declares, read out of the source.

    Statically, like the rest of this check: importing the package would drag in
    SQLAlchemy and the settings object to answer a question the text already
    answers, and a guard that needs the app to boot is a guard that stops running
    the first time the app does not.
    """
    tree = _parse(CATALOGUE)
    if tree is None:
        return []
    keys: list[str] = []
    for node in ast.walk(tree):
        # `CAPABILITIES: Final[tuple[...]] = (...)` is an AnnAssign, not an
        # Assign -- reading only Assign found nothing and the guard passed by
        # having no catalogue to check.
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target.id] if isinstance(node.target, ast.Name) else []
            value = node.value
        elif isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        else:
            continue
        if "CAPABILITIES" not in targets:
            continue
        for element in ast.walk(value):
            if (
                isinstance(element, ast.Call)
                and isinstance(element.func, ast.Name)
                and element.func.id == "Capability"
                and element.args
                and isinstance(element.args[0], ast.Constant)
                and isinstance(element.args[0].value, str)
            ):
                keys.append(element.args[0].value)
    return keys


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level `NAME = "literal"` bindings, so a constant resolves."""
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value.value
    return constants


def enforced_keys() -> dict[str, list[str]]:
    """Every key checked under app/, mapped to the sites that check it."""
    sites: dict[str, list[str]] = {}
    for path in sorted(APP.rglob("*.py")):
        tree = _parse(path)
        if tree is None:
            continue
        constants = _module_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if name not in GATES:
                continue
            argument: ast.expr | None = None
            for keyword in node.keywords:
                if keyword.arg == KEY_KEYWORD:
                    argument = keyword.value
            if argument is None and len(node.args) > KEY_POSITION:
                argument = node.args[KEY_POSITION]
            key: str | None = None
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                key = argument.value
            elif isinstance(argument, ast.Name):
                key = constants.get(argument.id)
            if key is None:
                continue
            sites.setdefault(key, []).append(f"{path.relative_to(API)}:{node.lineno}")
    return sites


def main() -> int:
    if not APP.is_dir():
        print(f"cannot find {APP}", file=sys.stderr)
        return 2

    keys = catalogue_keys()
    if not keys:
        print(f"cannot read CAPABILITIES from {CATALOGUE}", file=sys.stderr)
        return 2
    sites = enforced_keys()

    unenforced = [k for k in keys if k not in sites and k not in EXEMPT]
    # An exemption for a key that IS enforced, or for one the catalogue no longer
    # defines, is stale: it would go on excusing a key that never needed it.
    stale = [k for k in EXEMPT if k not in keys or k in sites]
    # A gate naming a key the catalogue does not define never fires: the three
    # gate functions raise ValueError on an unknown key, so this is a 500 in
    # waiting, on a screen nobody tests until someone reaches it.
    unknown = {k: v for k, v in sites.items() if k not in keys}

    if not unenforced and not stale and not unknown:
        print(
            f"OK: {len(keys)} capabilities, {len(keys) - len(EXEMPT)} enforced, "
            f"{len(EXEMPT)} exempt by design."
        )
        return 0

    if unenforced:
        print("Capabilities in the catalogue that nothing checks:\n", file=sys.stderr)
        for key in unenforced:
            print(f"  {key}", file=sys.stderr)
        print(
            "\nEnforce each one with require_capability(db, session, <key>) at the\n"
            "endpoint it names, or DELETE it from CAPABILITIES. A key nobody checks\n"
            "still appears in Governance: the office grants it with a reason, the\n"
            "audit records the grant, and nothing changes. That is a promise the\n"
            "API does not keep, with a paper trail saying it does.",
            file=sys.stderr,
        )
    if stale:
        print(
            "\nExemptions that are no longer true (enforced, or no longer in the "
            "catalogue):\n  " + "\n  ".join(sorted(stale)),
            file=sys.stderr,
        )
    if unknown:
        print(
            "\nGates naming a capability the catalogue does not define -- these "
            "raise ValueError at runtime:",
            file=sys.stderr,
        )
        for key, where in sorted(unknown.items()):
            print(f"  {key}  ({', '.join(where)})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
