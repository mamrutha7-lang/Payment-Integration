#!/usr/bin/env python3
"""Prove the two global stylesheets do not claim each other's class names.

WHY THIS EXISTS. src/styles.scss loads reep-v2.scss and then
reep-v2-resume.scss, so on every property they both set the resume sheet wins
across the WHOLE app, not just the resume builder. Nothing announces that. Four
selectors were declared in both when this check was written:

    .meter          8px tint-1 track here, 7px hairline there -- and the
                    hairline one was what the assistant's mic level, the
                    records attendance bars and the profile meter rendered
    .chip.neutral   tint-1 here, --ground there; neither written value was
                    the one on screen
    .card > h3      bordered heading here, flex-with-action there, so Skilling
                    and Interviews silently got the resume builder's card title
    .rail           two different components, both dead

AGENTS.md records the same failure from the other direction: adding
`display: flex` to `.step-group`, `.completeness` or `.entry` in reep-v2.scss
reflowed the resume builder, because that file owns those names.

So the rule is symmetric and simple: a top-level selector belongs to exactly
one of the two sheets. The fix for a genuine clash is to merge the rule into
reep-v2.scss (one owner) or to give the resume-only variant a resume-only name
-- never to rely on load order, which is invisible at both call sites.

WHAT IT CHECKS. Top-level selectors only. A nested or descendant selector
(`.card .foo`, `.rail a`) is excluded because it is scoped by its ancestor and
cannot leak on its own; a bare `.x` or `.x.y` or `.x > y` cannot be.

    python3 tools/ci/check_style_duplicates.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

STYLES = Path(__file__).resolve().parents[2] / "apps" / "web" / "src" / "styles"

BASE_SHEET = STYLES / "reep-v2.scss"
RESUME_SHEET = STYLES / "reep-v2-resume.scss"

#: A selector that reaches elements on its own, rather than through an
#: ancestor: `.card`, `.chip.neutral`, `.card > h3`, `.btn:hover`. Anything
#: containing a descendant space (`.card .desc`) is somebody's inside.
BARE_SELECTOR = re.compile(r"^\.[A-Za-z][\w-]*(?:[.:][\w-]+|\s*>\s*[\w.-]+)*$")


def strip_comments(text: str) -> str:
    """Remove /* */ and // comments so a selector named in prose is not a rule."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"(?m)//.*$", "", text)


def top_level_selectors(path: Path) -> dict[str, int]:
    """Every top-level class selector in `path`, mapped to its line number.

    Brace depth decides "top level": a rule opened while depth is zero is one
    the browser matches globally. Both sheets are flat CSS rather than nested
    SCSS, so counting braces is enough and a parser would buy nothing.
    """
    found: dict[str, int] = {}
    depth = 0
    pending: list[str] = []
    pending_line = 0

    for number, raw in enumerate(strip_comments(path.read_text(encoding="utf-8")).splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if depth == 0 and not line.startswith("}"):
            if line.endswith(","):
                if not pending:
                    pending_line = number
                pending.append(line.rstrip(","))
                continue
            if line.endswith("{"):
                if not pending:
                    pending_line = number
                pending.append(line[:-1].strip())
                for selector in pending:
                    selector = selector.strip()
                    if BARE_SELECTOR.match(selector) and selector not in found:
                        found[selector] = pending_line
                pending = []
        depth += line.count("{") - line.count("}")
        depth = max(depth, 0)
    return found


#: THE DEBT THIS CHECK INHERITED, AND THE ONLY LIST THAT MAY SHRINK.
#:
#: Thirty selectors were already declared in both sheets when the check was
#: written. They are not colour swaps like the magenta ones -- each is a
#: structural rule (a grid, a table, a notice, a control) whose two versions
#: differ in layout, so merging them means looking at every screen that uses
#: the name. That is a refactor of its own, not something to bury inside the
#: design-system phase.
#:
#: So they are listed, by name, and the check fails on anything NOT listed.
#: Two properties follow, and both matter:
#:   * a NEW duplicate cannot be introduced -- the rule holds from today;
#:   * a listed one cannot be fixed and left here -- an entry that no longer
#:     collides fails the check too, so the list can only get shorter.
#:
#: `.meter`, `.chip.neutral`, `.card > h3` and `.rail` are deliberately ABSENT:
#: they were on this list in its first draft and were merged into reep-v2.scss
#: in the same change. That is what shrinking it looks like.
KNOWN_DUPLICATES = frozenset(
    {
        ".addlink",
        ".check",
        ".ctrl",
        ".empty",
        ".evi-tag",
        ".footbar",
        ".grid2",
        ".grid3",
        ".grid4",
        ".head-actions",
        ".iconbtn",
        ".iconbtn:hover",
        ".inline",
        ".main-head",
        ".notice",
        ".notice.evi",
        ".notice.info",
        ".preview",
        ".radio-row",
        ".res-actions",
        ".res-card",
        ".res-card.default",
        ".res-grid",
        ".right",
        ".rowline",
        ".tag",
        ".tag.evi",
        ".tag.lock",
        ".taginput",
        ".tbl",
    }
)


def main() -> int:
    for sheet in (BASE_SHEET, RESUME_SHEET):
        if not sheet.exists():
            print(f"check_style_duplicates: {sheet} is missing", file=sys.stderr)
            return 2

    base = top_level_selectors(BASE_SHEET)
    resume = top_level_selectors(RESUME_SHEET)
    shared = set(base) & set(resume)

    new_duplicates = sorted(shared - KNOWN_DUPLICATES)
    resolved_but_listed = sorted(KNOWN_DUPLICATES - shared)

    if not new_duplicates and not resolved_but_listed:
        print(
            f"No new cross-sheet duplicate. {len(base)} top-level selectors in "
            f"reep-v2.scss, {len(resume)} in reep-v2-resume.scss, "
            f"{len(KNOWN_DUPLICATES)} known duplicates still to merge."
        )
        return 0

    if new_duplicates:
        print(
            "These selectors are now declared in BOTH global stylesheets. "
            "reep-v2-resume.scss loads last, so its version wins app-wide:\n",
            file=sys.stderr,
        )
        for selector in new_duplicates:
            print(
                f"  {selector}\n"
                f"      apps/web/src/styles/reep-v2.scss:{base[selector]}\n"
                f"      apps/web/src/styles/reep-v2-resume.scss:{resume[selector]}",
                file=sys.stderr,
            )
        print(
            "\nMerge the rule into reep-v2.scss so one file owns it, or give the\n"
            "resume-only variant a resume-only name. Do not rely on load order.",
            file=sys.stderr,
        )

    if resolved_but_listed:
        print(
            "\nThese are in KNOWN_DUPLICATES but no longer collide. Delete them\n"
            "from the list in this file so it keeps telling the truth:\n",
            file=sys.stderr,
        )
        for selector in resolved_but_listed:
            print(f"  {selector}", file=sys.stderr)

    return 1


if __name__ == "__main__":
    sys.exit(main())
