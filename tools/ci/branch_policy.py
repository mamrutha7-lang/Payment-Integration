#!/usr/bin/env python3
"""tools/ci/branch_policy.py — may a pull request from HEAD merge into BASE?

THE BRANCHING STRATEGY, AS CODE (docs/branching-strategy.md is the prose):

    main      production; the only branch deploy.yml's OIDC role may ship from
    stage     pre-production; what main will become at the next release
    dev       integration; every feature lands here first
    feature/* one piece of work, cut from dev, merged back into dev
    hotfix/*  an urgent production fix, cut from main, merged into main AND dev

So the promotion path is one direction only, feature -> dev -> stage -> main,
with hotfix/* as the single way to reach main without passing through stage.

WHY A CHECK AND NOT A CONVENTION. A ruleset can require a pull request and
green checks on `main`, but it cannot say WHICH branch the pull request comes
from. Without this, `feature/login -> main` is a perfectly green PR that skips
stage entirely, and the strategy is a diagram nobody is obliged to follow.

Only the two protected promotions are policed. A PR into `dev` may come from
any work branch (feature/*, fix/*, agent and session branches alike) except
`stage`, because a prefix list there would refuse every automated branch for no
safety gain: dev is where review happens, not where a release is cut. A PR
into any OTHER base (a stacked PR into a feature branch) is not this check's
business.

Usage:  branch_policy.py <base> <head>      exit 0 allowed, 1 refused
"""
from __future__ import annotations

import sys

HOTFIX_PREFIX = "hotfix/"

# base -> (exact heads allowed, head prefixes allowed). A base absent from this
# map is unpoliced.
PROMOTIONS: dict[str, tuple[frozenset[str], tuple[str, ...]]] = {
    "main": (frozenset({"stage"}), (HOTFIX_PREFIX,)),
    # `main` back into stage keeps stage from diverging after a hotfix.
    "stage": (frozenset({"dev", "main"}), ()),
}

# dev takes anything except a branch that would run the path backwards.
DEV_REFUSED = frozenset({"stage"})


def verdict(base: str, head: str) -> tuple[bool, str]:
    """(allowed, one sentence saying why)."""
    if base == head:
        return False, f"a pull request from {head!r} into itself merges nothing"
    if base == "dev":
        if head in DEV_REFUSED:
            return False, (
                "stage -> dev runs the promotion path backwards; stage only ever "
                "holds what dev already had, so merge main -> dev instead"
            )
        return True, f"{head!r} -> dev: work lands on the integration branch"
    rule = PROMOTIONS.get(base)
    if rule is None:
        return True, f"{base!r} is not a protected promotion target"
    exact, prefixes = rule
    if head in exact or any(head.startswith(p) and len(head) > len(p) for p in prefixes):
        return True, f"{head!r} -> {base}: an allowed promotion"
    allowed = sorted(exact) + [f"{p}*" for p in prefixes]
    return False, (
        f"{head!r} may not merge into {base!r}; {base} accepts only "
        f"{', '.join(allowed)}. Open the PR against dev, then promote "
        "dev -> stage -> main (or cut a hotfix/* branch from main for an urgent fix)"
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or not all(args):
        print("usage: branch_policy.py <base> <head>", file=sys.stderr)
        return 2
    ok, why = verdict(args[0], args[1])
    print(("allowed: " if ok else "::error::refused: ") + why)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
