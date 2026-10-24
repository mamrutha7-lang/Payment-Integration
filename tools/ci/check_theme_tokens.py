#!/usr/bin/env python3
"""Prove the grid and chart themes hold the same colours as the stylesheet.

WHY THIS EXISTS. Two files copy design tokens as literal hex strings instead
of reading `var(--brand-purple)`, and both have to:

  * shared/grid/reep-grid-theme.ts — AG Grid's Theming API writes its own
    custom properties into a shadow root, and the page's variables do not
    cross that boundary. A `var(...)` there resolves to nothing and the grid
    renders unstyled.
  * shared/charts/reep-echarts-theme.ts — ECharts draws to a canvas or to SVG
    it generates itself and resolves nothing through the CSS cascade. A
    `var(...)` handed to a series is passed to the renderer as an invalid
    colour.

So the duplication is forced. What is not forced is letting the copies drift:
change `--brand-purple` in reep-v2.scss and the app moves while the grid's
accent and the chart's ramp stay where they were, which reads as a rendering
bug on one screen rather than as a stale constant.

HOW IT CHECKS. Each literal in the two theme files is declared beside the
token it copies, in a table below. The check reads the token's value out of
reep-v2.scss's `:root` block, reads the literal out of the theme file, and
compares them after normalising whitespace inside `rgba(...)`.

Adding a token to a theme means adding a row here. That is the point: the row
is the statement that the literal is a copy rather than a colour somebody
picked, and without it nothing would notice when the two part company.

    python3 tools/ci/check_theme_tokens.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "apps" / "web" / "src"

STYLESHEET = WEB / "styles" / "reep-v2.scss"
GRID_THEME = WEB / "app" / "shared" / "grid" / "reep-grid-theme.ts"
CHART_THEME = WEB / "app" / "shared" / "charts" / "reep-echarts-theme.ts"

#: (theme file, the TS constant that holds the copy, the CSS token it copies).
COPIED_TOKENS: tuple[tuple[Path, str, str], ...] = (
    (GRID_THEME, "brandPurple", "--brand-purple"),
    (GRID_THEME, "ink", "--ink"),
    (GRID_THEME, "inkSoft", "--ink-soft"),
    (GRID_THEME, "surface", "--surface"),
    (GRID_THEME, "tintTwo", "--tint-2"),
    (GRID_THEME, "tintThree", "--tint-3"),
    (GRID_THEME, "hairline42", "--hairline-42"),
    (GRID_THEME, "hairline26", "--hairline-26"),
    (CHART_THEME, "INK", "--ink"),
    (CHART_THEME, "AXIS_TEXT", "--axis"),
    (CHART_THEME, "SPLIT_LINE", "--split"),
    (CHART_THEME, "HAIRLINE", "--hairline-42"),
    (CHART_THEME, "TINT_ONE", "--tint-1"),
    (CHART_THEME, "TINT_THREE", "--tint-3"),
    (CHART_THEME, "MUTED", "--muted"),
    (CHART_THEME, "SEQUENTIAL_LIGHT", "--seq-light"),
    (CHART_THEME, "SEQUENTIAL_DEEP", "--seq"),
)

#: The categorical palette, which the chart theme holds as an array and the
#: stylesheet as four tokens. Checked in order, because the order IS the
#: meaning: a track's colour is its position (FIN 1, HR 2, MKT 3, BA 4).
CATEGORICAL = ("--cat-1", "--cat-2", "--cat-3", "--cat-4")

#: The status colours, held in the chart theme as an object literal.
STATUS = (("good", "--st-good"), ("warn", "--st-warn"), ("risk", "--st-risk"),
          ("neutral", "--st-neutral"))


def normalise(value: str) -> str:
    """Lower-case, and squeeze the spaces out of an rgba() so the two spellings
    of the same colour compare equal."""
    return re.sub(r"\s+", "", value).lower()


def css_tokens() -> dict[str, str]:
    """Every custom property declared in reep-v2.scss's :root block."""
    text = STYLESHEET.read_text(encoding="utf-8")
    start = text.index(":root {")
    end = text.index("\n}", start)
    tokens: dict[str, str] = {}
    for name, value in re.findall(r"(--[\w-]+):\s*([^;]+);", text[start:end]):
        tokens[name] = value.strip()
    return tokens


def ts_constants(path: Path) -> dict[str, str]:
    """String constants in a theme file, however they are declared.

    Catches `const NAME = '#fff';`, an object property `name: '#fff',` and an
    array entry, which is all three shapes the two files use.
    """
    text = path.read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for name, value in re.findall(r"(?:const\s+)?(\w+)\s*[:=]\s*'([^']+)'", text):
        found.setdefault(name, value)
    return found


def string_array(path: Path, name: str) -> list[str]:
    """The string entries of `export const NAME = [ … ]`, in order."""
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"{name}\s*=\s*\[(.*?)\]", text, re.DOTALL)
    if not match:
        return []
    return re.findall(r"'([^']+)'", match.group(1))


def object_values(path: Path, name: str) -> dict[str, str]:
    """The string entries of `export const NAME = {{ … }}`."""
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"{name}\s*=\s*\{{(.*?)\n\}}", text, re.DOTALL)
    if not match:
        return {}
    return dict(re.findall(r"(\w+):\s*'([^']+)'", match.group(1)))


def main() -> int:
    for path in (STYLESHEET, GRID_THEME, CHART_THEME):
        if not path.exists():
            print(f"check_theme_tokens: {path} is missing", file=sys.stderr)
            return 2

    tokens = css_tokens()
    problems: list[str] = []
    checked = 0

    for path, constant, token in COPIED_TOKENS:
        if token not in tokens:
            problems.append(f"{token} is not declared in reep-v2.scss's :root block")
            continue
        constants = ts_constants(path)
        if constant not in constants:
            problems.append(f"{path.name}: no string constant named {constant}")
            continue
        checked += 1
        if normalise(constants[constant]) != normalise(tokens[token]):
            problems.append(
                f"{path.name}: {constant} is {constants[constant]!r}, "
                f"but {token} is {tokens[token]!r}"
            )

    palette = string_array(CHART_THEME, "CATEGORICAL_PALETTE")
    if len(palette) != len(CATEGORICAL):
        problems.append(
            f"{CHART_THEME.name}: CATEGORICAL_PALETTE has {len(palette)} colours, "
            f"expected {len(CATEGORICAL)} (--cat-1 … --cat-4)"
        )
    else:
        for index, token in enumerate(CATEGORICAL):
            checked += 1
            if normalise(palette[index]) != normalise(tokens.get(token, "")):
                problems.append(
                    f"{CHART_THEME.name}: CATEGORICAL_PALETTE[{index}] is "
                    f"{palette[index]!r}, but {token} is {tokens.get(token)!r}"
                )

    status = object_values(CHART_THEME, "STATUS_COLOURS")
    for key, token in STATUS:
        if key not in status:
            problems.append(f"{CHART_THEME.name}: STATUS_COLOURS has no {key!r}")
            continue
        checked += 1
        if normalise(status[key]) != normalise(tokens.get(token, "")):
            problems.append(
                f"{CHART_THEME.name}: STATUS_COLOURS.{key} is {status[key]!r}, "
                f"but {token} is {tokens.get(token)!r}"
            )

    if problems:
        print("The grid and chart themes have drifted from reep-v2.scss:\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "\nThese files copy tokens as literals because neither library reads\n"
            "CSS custom properties. Update the copy, or the row in\n"
            "tools/ci/check_theme_tokens.py if the token itself moved.",
            file=sys.stderr,
        )
        return 1

    print(f"Grid and chart themes match reep-v2.scss. {checked} colours checked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
