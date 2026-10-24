#!/usr/bin/env python3
"""Prove that magenta only ever appears inside the primary gradient.

WHY THIS EXISTS. The 2026-09 design system
(docs/redesign-2026-09/01-design-system.md §1) gives magenta exactly one job:
it is the last stop of `--primary-gradient`, which paints the one primary
button of a view, the active nav pill, the active tab, the brand mark and the
meters. Everywhere else the accent is `--brand-purple`.

That is not a taste rule. #ba2185 measures about 4.0:1 on the card fill, which
is the weakest contrast in the palette -- as a text colour it fails the bar the
rest of the design clears, and it was being used as one (`.band-dial .band`,
the ledger's weekly icon). And a gradient whose colour also turns up on chips,
borders and chart points stops reading as "this is the primary action here",
which is the whole reason the gradient is reserved.

Twenty-one call sites were retokened when this check was written -- every
reference under apps/web/src except the two declarations below. Without the
check they come back one at a time, each looking locally reasonable, and the
rule is gone a year later with nobody having decided to drop it.

WHAT COUNTS AS A HIT. Three spellings of the same colour, because a reviewer
greps for one and the next person writes another:

    --brand-magenta          the token
    #ba2185                  the literal, in CSS or in a TypeScript chart option
    rgba(186, 33, 133, ...)  the same colour with an alpha

WHAT IS ALLOWED, and it is a short list: the `--brand-magenta` declaration
itself, and the `--primary-gradient` declaration that consumes it. Both live in
apps/web/src/styles/reep-v2.scss. Nothing else, in any file, for any reason --
if a new surface genuinely needs the gradient, it reads `var(--primary-gradient)`
rather than rebuilding it out of the stops.

Deliberately a text scan and not a CSS parse: the rule is about what the SOURCE
says, a reviewer checks it the same way, and a parser would still have to be
told which declarations are the two exceptions.

    python3 tools/ci/check_brand_magenta.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WEB_SOURCE = Path(__file__).resolve().parents[2] / "apps" / "web" / "src"

SCANNED_SUFFIXES = {".scss", ".css", ".ts", ".html"}

#: The three spellings of #ba2185. `rgba` is matched on its channel numbers
#: rather than on the word, so `rgb(186 33 133 / .18)` is caught too.
MAGENTA = re.compile(
    r"--brand-magenta"
    r"|#ba2185"
    r"|rgba?\(\s*186\s*[, ]\s*33\s*[, ]\s*133\b",
    re.IGNORECASE,
)

#: The only two declarations that may name the colour. Matched against the
#: whole line, so a rule that merely mentions one of these names in a comment
#: does not buy itself an exemption.
ALLOWED_DECLARATIONS = (
    re.compile(r"^\s*--brand-magenta:\s*#ba2185;\s*$", re.IGNORECASE),
    re.compile(r"^\s*--primary-gradient:\s*linear-gradient\(.*#ba2185\);\s*$", re.IGNORECASE),
)

ALLOWED_FILE = WEB_SOURCE / "styles" / "reep-v2.scss"


def code_only(line: str, inside_block_comment: bool) -> tuple[str, bool]:
    """The part of `line` that is code, plus whether the comment runs on.

    Comments are stripped rather than skipped, because the rule has to be
    explainable where it is enforced: the token's own declaration carries a
    paragraph naming `--brand-magenta` and `#ba2185`, and so does this file.
    A guard that trips on its own documentation gets its documentation deleted.

    Line comments are recognised in both dialects the tree uses -- `//` in SCSS
    and TypeScript, `/* */` in both -- and a block comment is carried across
    lines. A `//` inside a string (a URL, say) would end the line early, which
    can only ever hide a hit on that same line; it cannot invent one.
    """
    out: list[str] = []
    index = 0
    while index < len(line):
        if inside_block_comment:
            end = line.find("*/", index)
            if end == -1:
                return "".join(out), True
            index = end + 2
            inside_block_comment = False
            continue
        start_block = line.find("/*", index)
        start_line = line.find("//", index)
        if start_line != -1 and (start_block == -1 or start_line < start_block):
            out.append(line[index:start_line])
            return "".join(out), False
        if start_block != -1:
            out.append(line[index:start_block])
            index = start_block + 2
            inside_block_comment = True
            continue
        out.append(line[index:])
        break
    return "".join(out), inside_block_comment


def offending_lines_in(path: Path) -> list[tuple[int, str]]:
    """Every line in `path` that names magenta and is not one of the two allowed."""
    offences: list[tuple[int, str]] = []
    inside_block_comment = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        code, inside_block_comment = code_only(line, inside_block_comment)
        if not MAGENTA.search(code):
            continue
        if path == ALLOWED_FILE and any(rule.match(code) for rule in ALLOWED_DECLARATIONS):
            continue
        offences.append((number, line.strip()))
    return offences


def scanned_files() -> list[Path]:
    return sorted(
        path
        for path in WEB_SOURCE.rglob("*")
        if path.is_file() and path.suffix in SCANNED_SUFFIXES
    )


def main() -> int:
    files = scanned_files()
    if not files:
        print(f"check_brand_magenta: found no source under {WEB_SOURCE}", file=sys.stderr)
        return 2

    offences: list[tuple[Path, int, str]] = []
    for path in files:
        for number, text in offending_lines_in(path):
            offences.append((path, number, text))

    if not offences:
        print(f"Magenta is only in the gradient. Checked {len(files)} files.")
        return 0

    print(
        "Magenta is used outside --primary-gradient, which the design system "
        "reserves it for:\n",
        file=sys.stderr,
    )
    for path, number, text in offences:
        print(f"  {path.relative_to(WEB_SOURCE.parents[2])}:{number}: {text}", file=sys.stderr)
    print(
        "\nUse var(--brand-purple) for an accent, var(--purple-mid) for a link or a\n"
        "hover, or var(--primary-gradient) if this really is the view's one primary\n"
        "action. See docs/redesign-2026-09/01-design-system.md §1.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
