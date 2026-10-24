"""Delete every college EXCEPT one: ``python -m app.purge_colleges --keep 1MP``.

The bulk form of the Colleges screen's Delete button, for the deployment that
has been set up three times over and carries the practice colleges beside the
real one. Same module underneath (`app.college_deletion`), same refusals: a
college with a student seated, a faculty account filed or an application
waiting under it is REPORTED and SKIPPED, never taken, and the run says which
and why.

DRY RUN IS THE DEFAULT. `--apply` deletes, and also demands
`--i-understand-this-is-permanent`, for the two purge modules' reason. Each
college goes in its own transaction, so a refusal on the third leaves the
first two deleted and the third exactly as it was.

`--keep` is the CODE (the handle beside a student's name, `1MP`), matched
case-insensitively, and an unknown code is refused rather than deleting
everything: a typo must not be the same command as "delete every college".
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import select

from .college_deletion import CollegeDeleteRefused, build_plan, execute
from .db import SessionLocal
from .models.institution import College

log = logging.getLogger("reep.purge_colleges")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", required=True, help="the code of the ONE college to keep, e.g. 1MP")
    parser.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    parser.add_argument(
        "--i-understand-this-is-permanent",
        action="store_true",
        dest="understood",
        help="required with --apply; there is no undo",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    if args.apply and not args.understood:
        log.error("--apply needs --i-understand-this-is-permanent. Nothing was changed.")
        return 2

    keep = args.keep.strip().upper()
    with SessionLocal() as db:
        colleges = db.scalars(select(College).order_by(College.code)).all()
        kept = [c for c in colleges if c.code.strip().upper() == keep]
        if not kept:
            log.error(
                "No college has the code %s. The codes on this deployment: %s. Nothing was changed.",
                keep,
                ", ".join(c.code for c in colleges) or "(none)",
            )
            return 2
        others = [c for c in colleges if c.code.strip().upper() != keep]
        log.info("%s", "DELETING" if args.apply else "DRY RUN - nothing was deleted")
        log.info("Keeping %s (%s).", kept[0].code, kept[0].name)
        if not others:
            log.info("No other college exists. Nothing to delete.")
            return 0

        failed = 0
        for college in others:
            plan = build_plan(db, college)
            head = f"{college.code} ({college.name}):"
            if not plan.deletable:
                failed += 1
                log.warning("%s SKIPPED - %s", head, plan.refusal())
                continue
            summary = ", ".join(f"{n} {table}" for table, n in sorted(plan.rows.items()))
            unscoped = ", ".join(f"{n} {col}" for col, n in sorted(plan.cleared.items()))
            log.info("%s %d row(s) would go (%s)%s", head, plan.total_rows, summary or "nothing",
                     f"; un-scoped: {unscoped}" if unscoped else "")
            if not args.apply:
                continue
            try:
                execute(db, plan)
                log.info("%s deleted.", head)
            except CollegeDeleteRefused as exc:
                failed += 1
                db.rollback()
                log.error("%s REFUSED - %s", head, exc)
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
