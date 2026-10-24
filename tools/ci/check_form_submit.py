#!/usr/bin/env python3
"""Prove every `<form>` in the SPA has something that owns its submit.

WHY THIS EXISTS, and it is not a style rule. `(ngSubmit)` is not a DOM event.
It is an `@Output` of exactly two directives:

  * `FormGroupDirective` — selector `[formGroup]`, exported by ReactiveFormsModule
  * `NgForm`             — selector `form:not([ngNoForm]):not([formGroup])`,
                           exported by FormsModule and by NOTHING else

A component that imports only ReactiveFormsModule and writes a bare
`<form (ngSubmit)="save()">` matches neither. Angular does not complain: an
event binding whose name matches no directive output is legal, because custom
DOM events are legal, so it compiles to `addEventListener('ngSubmit', …)` on
the form element. Nothing ever dispatches an event by that name.

Two things then happen, and the second is the damaging one. The handler never
runs; and because no directive is listening for the native `submit` either,
nothing calls `preventDefault()`. So the submit button does what a submit
button does when no JavaScript is in the way: a full-page GET to the current
URL, with the query string REPLACED by the form's named fields. Angular fields
bound by `[formControl]` or `formControlName` carry no `name` attribute, so the
replacement is usually empty.

THAT IS HOW A STUDENT LOST THEIR SETUP TOKEN (2026-09-17). `/onboard?token=…`
rendered perfectly, the student typed their college address, pressed "Send me a
code", and the browser navigated to `/onboard?` — no token — so the screen drew
"This page needs the setup link from the email we sent you." The approval mail
was fine and the API was never called. It looked like a broken link because
the only evidence on screen was about the link. Steps 1 and 2 of the walk had
been dead since the screen was written, and `ng build` passed on every one of
those days, because there is nothing here for the compiler to reject.

`tsc` cannot see it, the template type-checker cannot see it, and no unit test
that does not actually press the button can see it. A static check can.

THE RULE. A `<form>` in `apps/web/src` must carry ONE of:

  * `[formGroup]` (or `formGroup`) — FormGroupDirective owns the submit, emits
    `ngSubmit` and returns false from its host listener, which prevents the
    default. This is the house pattern wherever the controls are reactive.
  * `(submit)` — the template handles the native event itself. Then the default
    must actually be prevented, or the page still reloads, so this check
    follows it: either the binding says `$event.preventDefault()` inline, or it
    hands `$event` to a method whose body does. It reads that method body, not
    the whole file — a `preventDefault` somewhere else in a 900-line component
    would let exactly the broken form through. Use this spelling where the
    inputs are signal-driven and there is no group to bind.
  * `ngNoForm` — a deliberate opt-out; the form is a plain HTML form.
  * nothing, IF the component that owns the template imports `FormsModule`,
    which is what makes `NgForm` match a bare `<form>`. The login screen does
    this, legitimately: it drives that form with `[ngModel]`.

    That last case is why this check reads the component and not just the
    template. It is also the fragile one — the import is what makes a binding
    three files away work, and nothing at the call site says so — so a form
    relying on it is reported as OK but the reliance is real: strip FormsModule
    from that component's imports and this check goes red rather than the
    button going quiet.

    python3 tools/ci/check_form_submit.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "apps" / "web" / "src"

#: A standalone component's `imports: [...]`. The FormsModule test is made
#: against THIS and not against the file, and both halves of that are load-
#: bearing. `ReactiveFormsModule` ends in the same eleven characters, so a
#: plain substring test reports every reactive component as importing
#: FormsModule and this check passes everywhere proving nothing. And a
#: file-wide search is satisfied by the word appearing in a COMMENT — which is
#: not hypothetical: the first draft of this guard was defeated by the comment
#: on onboard.component.ts explaining the very bug it was written to catch.
DECORATOR_IMPORTS = re.compile(r"\bimports:\s*\[([^\]]*)\]")
FORMS_MODULE = re.compile(r"(?<![A-Za-z])FormsModule\b")


def declares_forms_module(component: Path | None) -> bool:
    """Does this component list `FormsModule` in a decorator `imports` array?"""
    if component is None:
        return False
    source = component.read_text(encoding="utf-8")
    return any(FORMS_MODULE.search(block) for block in DECORATOR_IMPORTS.findall(source))


def form_tags(text: str) -> list[tuple[int, str]]:
    """Every `<form …>` opening tag, as (1-based line number, the tag).

    Scanned character by character rather than with `<form[^>]*>`, because an
    attribute value may legally contain a `>` — `[disabled]="a > b"` — and a
    regex that stopped at the first one would read half a tag and miss the
    attribute that matters.
    """
    tags: list[tuple[int, str]] = []
    for match in re.finditer(r"<form\b", text):
        i = match.end()
        quote = ""
        while i < len(text):
            ch = text[i]
            if quote:
                if ch == quote:
                    quote = ""
            elif ch in "\"'":
                quote = ch
            elif ch == ">":
                break
            i += 1
        tags.append((text.count("\n", 0, match.start()) + 1, text[match.start() : i + 1]))
    return tags


def owner_of(tag: str) -> str | None:
    """Which of the four allowances this tag claims, or None."""
    if re.search(r"[\s\[]formGroup\]?\s*=", tag):
        return "formGroup"
    if re.search(r"\(submit\)\s*=", tag):
        return "submit"
    if re.search(r"(?<![A-Za-z-])ngNoForm(?![A-Za-z-])", tag):
        return "ngNoForm"
    return None


def method_body(source: str, name: str) -> str:
    """The body of `name(...)  { ... }` in a TypeScript class, braces balanced.

    Read rather than grepping the whole file: a component that calls
    `preventDefault()` in some other handler would otherwise vouch for a form
    whose own handler does not.
    """
    for match in re.finditer(rf"(?<![A-Za-z0-9_$]){re.escape(name)}\s*\(", source):
        open_brace = source.find("{", match.end())
        if open_brace < 0:
            continue
        depth = 0
        for i in range(open_brace, len(source)):
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
                if depth == 0:
                    return source[open_brace : i + 1]
    return ""


def prevents_default(tag: str, component: Path | None) -> bool:
    """Does this `(submit)` binding stop the browser navigating?"""
    binding = re.search(r"\(submit\)\s*=\s*([\"\'])(.*?)\1", tag, re.S)
    statement = binding.group(2) if binding else ""
    if "preventDefault" in statement:
        return True
    # `(submit)="submit($event)"` — the handler is where the prevention lives.
    if "$event" not in statement or component is None:
        return False
    source = component.read_text(encoding="utf-8")
    return any(
        "preventDefault" in method_body(source, handler)
        for handler in re.findall(r"(?<![A-Za-z0-9_$.])([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", statement)
    )


def inline_templates(source: str) -> list[tuple[int, str]]:
    """Every `template: \u0060...\u0060` literal in a .ts, as (line offset, text).

    A .ts file is read for its INLINE template and never whole: this file's own
    docstring contains `<form ...>` several times, and the first draft
    dutifully reported those as broken forms in the guard that describes them.
    The line offset is carried so a report still points at the real line.
    """
    found: list[tuple[int, str]] = []
    for match in re.finditer(r"\btemplate:\s*([\u0060'\"])", source):
        quote = match.group(1)
        i = match.end()
        while i < len(source):
            if source[i] == "\\":
                i += 2
                continue
            if source[i] == quote:
                break
            i += 1
        found.append((source.count("\n", 0, match.end()), source[match.end() : i]))
    return found


def components_by_template() -> dict[Path, Path]:
    """{template file: the .ts whose `templateUrl` points at it}."""
    owners: dict[Path, Path] = {}
    for source in WEB.rglob("*.ts"):
        for raw in re.findall(r"templateUrl:\s*['\"]([^'\"]+)['\"]", source.read_text(encoding="utf-8")):
            owners[(source.parent / raw).resolve()] = source
    return owners


def main() -> int:
    owners = components_by_template()
    problems: list[str] = []

    for path in sorted([*WEB.rglob("*.html"), *WEB.rglob("*.ts")]):
        source = path.read_text(encoding="utf-8")
        if "<form" not in source:
            continue
        # A .ts is read for its inline templates ALONE; one that only names a
        # `templateUrl` is covered when that .html file comes round.
        if path.suffix == ".ts":
            component = path
            tags = [
                (offset + line, tag)
                for offset, template in inline_templates(source)
                for line, tag in form_tags(template)
            ]
        else:
            component = owners.get(path.resolve())
            tags = form_tags(source)
        if not tags:
            continue
        has_forms_module = declares_forms_module(component)
        where = path.relative_to(WEB.parents[1])

        for line, tag in tags:
            owner = owner_of(tag)
            if owner == "submit":
                if not prevents_default(tag, component):
                    problems.append(
                        f"{where}:{line}: <form (submit)=…> never calls "
                        f"$event.preventDefault() — not in the binding, and not in "
                        f"the handler it passes $event to — so the page still reloads."
                    )
                continue
            if owner:
                continue
            if has_forms_module:
                # NgForm matches the bare <form> and owns the submit. Legal, and
                # the component's FormsModule import is now load-bearing.
                continue
            problems.append(
                f"{where}:{line}: nothing owns this <form>'s submit. "
                f"{'It binds (ngSubmit), which will never fire' if '(ngSubmit)' in tag else 'It has no submit binding at all'}"
                f", and the native submit is not prevented — pressing the button "
                f"reloads the page and drops the query string. Add [formGroup], or "
                f"bind (submit) and call $event.preventDefault()."
            )

    if problems:
        print("Forms whose submit nothing owns:\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}\n", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        return 1

    print("check_form_submit: every <form> has an owner for its submit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
