#!/usr/bin/env python3
"""Prove that every model call says out loud whether it carries student data.

WHY THIS EXISTS. Rule 1 in AGENTS.md is enforced by one gate,
`student_data_egress_allowed` in app/ai/llm.py, and that gate only fires when a
caller passes `carries_student_data=True`. The parameter DEFAULTS TO FALSE.

That default is right -- most prompts really do carry no student record, and a
default of True would refuse the knowledge base and the job-posting paths for no
reason. But it means an OMISSION is not a neutral act: it is a silent claim that
this prompt holds no student's name, USN, marks or attendance. A claim nobody
typed is a claim nobody reviewed, and the failure is invisible -- the call
works, the answer comes back, and a student's record has left the machine.

`app/routers/agent.py` had exactly that shape: two call sites, neither
declaring, both inheriting False by default.

So this check does not decide whether a prompt carries student data -- it cannot,
and guessing would be worse than useless. It requires that a HUMAN decided, in
the source, where a reviewer can see it.

HOW IT WORKS. A static AST walk over app/, looking for calls to `complete_chat`
and `stream_chat` that pass no `carries_student_data=` keyword. Static rather
than import-based (unlike check_api_imports.py, which must import to answer its
question) because this one is about what the SOURCE says, and a keyword argument
is visible without executing anything.

Deliberately not clever: a call routed through a variable (`fn = complete_chat`)
slips past. That is acceptable -- this catches the mistake people actually make,
which is copying a nearby call and not noticing an argument is owed.

    python3 tools/ci/check_pii_gate.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

#: The functions that reach a model. Both take the keyword; both default False.
GATED_CALLS = {"complete_chat", "stream_chat"}

#: app/ai/llm.py DEFINES these, and its own internals call the transports
#: directly. Checking the gate's own module for use of the gate is circular.
EXEMPT = {"llm.py"}

APP = Path(__file__).resolve().parents[2] / "apps" / "api-py" / "app"


def offenders() -> list[tuple[Path, int, str]]:
    found: list[tuple[Path, int, str]] = []
    for path in sorted(APP.rglob("*.py")):
        if path.name in EXEMPT:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # a broken file is a different job's failure
            print(f"skipping {path}: {exc}", file=sys.stderr)
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if name not in GATED_CALLS:
                continue
            if not any(kw.arg == "carries_student_data" for kw in node.keywords):
                found.append((path, node.lineno, name))
    return found


def main() -> int:
    if not APP.is_dir():
        print(f"cannot find {APP}", file=sys.stderr)
        return 2

    found = offenders()
    if not found:
        print("OK: every complete_chat/stream_chat call declares carries_student_data.")
        return 0

    print(
        "Model calls that do not say whether they carry student data:\n",
        file=sys.stderr,
    )
    root = APP.parents[2]
    for path, lineno, name in found:
        print(f"  {path.relative_to(root)}:{lineno}  {name}(...)", file=sys.stderr)
    print(
        "\nAdd carries_student_data=True or =False explicitly at each site.\n"
        "\n"
        "OMITTING IT IS NOT NEUTRAL. The parameter defaults to False, so an\n"
        "absent argument silently asserts that this prompt holds no student's\n"
        "name, USN, marks or attendance -- and if that assertion is wrong,\n"
        "nothing fails, nothing logs, and the record leaves the machine.\n"
        "Rule 1 (AGENTS.md) is only as good as the callers that invoke it.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
