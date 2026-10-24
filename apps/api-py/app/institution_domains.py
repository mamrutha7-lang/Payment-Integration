"""Which email domains a college will admit an applicant on.

ONE ANSWER, ONE PLACE. Three call sites fence an address before it becomes an
account — approving a registration (`routers/registration.py` GUARD 1), the
Main Admin editing a student's address (`routers/admin_students.py`), and
creating a faculty account (`routers/admin_faculty.py`, B3.2) — and until now
each of them read `settings.provisionable_email_domains` directly. That was
correct while there was one fence for the whole deployment. It stops being
correct the moment there are two colleges, because the question is no longer
"is this address one of ours" but "is this address one of THEIRS", and three
call sites each reading a global is three places to forget that.

THE ENVIRONMENT IS THE FALLBACK, NOT THE FLOOR. A college with no domains
recorded falls back to `settings.provisionable_email_domains`, which is exactly
what every college gets today, so nothing changes for a deployment that never
opens the Colleges screen. A college that HAS a list is fenced by that list
alone — the environment does not widen it, because a per-tenant fence that the
deployment's own settings can quietly reopen is not a fence.

Read `Settings.provisionable_email_domains` for why this fence exists on
provisioning and deliberately not on sign-in: the two run in opposite
directions, and a domain test on sign-in can only lock out somebody already
enrolled.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models.cohort import Cohort
from .models.institution import College, Department


import re

#: Domains that hand an address to anybody who asks, so one of them is never a
#: college's. Only the big providers, and deliberately not a complete list: the
#: point is the common mistake, not a directory of free mail. Read by the
#: registration form's college-email box and by a college's own domain list,
#: which must agree: a list holding gmail.com would admit every Gmail address
#: as that college's.
PUBLIC_MAIL_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.in",
        "outlook.com", "hotmail.com", "live.com", "msn.com", "icloud.com",
        "me.com", "rediffmail.com", "proton.me", "protonmail.com", "aol.com",
    }
)

#: A bare hostname: dot-separated labels of letters, digits and inner hyphens,
#: ending in a letter-only top-level label. No scheme, path, port or space.
_HOSTNAME = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def domain_problem(domain: str) -> str | None:
    """Why a normalised domain cannot be a college's, or None.

    `normalise_domain` only lowercases and strips the `@`, so a list could hold
    "not a domain" or "http://x.com/" (entries that match no address and say
    nothing) or a public mail provider (an entry that matches everybody's).
    """
    if not _HOSTNAME.match(domain):
        return f"{domain!r} is not a domain. Enter it as it appears after the @, for example bgscet.ac.in."
    if domain in PUBLIC_MAIL_DOMAINS:
        return f"{domain} is a public mail provider, not a college's own domain."
    return None


def normalise_domain(value: str) -> str:
    """`@BGSCET.ac.in ` -> `bgscet.ac.in`. The shape stored and compared."""
    return value.strip().lstrip("@").lower()


def domain_of(email: str) -> str:
    """The domain part of an address, normalised. Empty when there isn't one.

    `str.rpartition` returns the WHOLE STRING as its third element when the
    separator is absent, so the obvious one-liner answers "no-at-sign" for
    `no-at-sign` — a truthy value that is not a domain. The inline code this
    replaces had the same behaviour and got away with it, because the result was
    only ever tested for membership in a domain set and failed that. A named
    helper does not get away with it: the next caller writes `if not domain_of(x)`
    to mean "no domain here" and is told there is one.
    """
    local, at, domain = (email or "").rpartition("@")
    return normalise_domain(domain) if at else ""


def provisionable_domains_for(db: Session, college_id: str | None) -> frozenset[str]:
    """The domains this college admits, or the deployment's list if it has none.

    `college_id` of None is the pre-spine case — an application that named no
    college, a student whose batch has no department — and it answers the
    environment list, which is what that address was fenced by before colleges
    had domains at all.
    """
    if college_id:
        college = db.get(College, college_id)
        if college is not None and college.email_domains:
            return frozenset(normalise_domain(d) for d in college.email_domains if d.strip())
    return settings.provisionable_email_domains


def college_ids_for_cohorts(db: Session, cohort_ids) -> dict[str, str | None]:
    """The college each of these batches belongs to — ONE query for a whole page.

    THE JOIN IS WRITTEN ONCE. `college_id_for_cohort` below is the single-row
    caller, not a second copy: a list endpoint that restated this walk to avoid
    an N+1 would be a second reading of the spine, and the two disagree the first
    time a rung moves. The registration queue calls this directly with the
    distinct cohort ids of the page it is about to answer.

    A cohort that was never filed under a department, or one that has been
    deleted, is simply absent from the result — the caller's `.get()` answers
    None, which is the same thing the single-row version says.
    """
    ids = [c for c in dict.fromkeys(cohort_ids) if c]
    if not ids:
        return {}
    return {
        cohort_id: college_id
        for cohort_id, college_id in db.execute(
            select(Cohort.id, Department.college_id)
            .select_from(Cohort)
            .join(Department, Cohort.department_id == Department.id)
            .where(Cohort.id.in_(ids))
        ).all()
    }


def college_id_for_cohort(db: Session, cohort_id: str | None) -> str | None:
    """The college a batch belongs to, through its department.

    A cohort carries `department_id` and the department carries `college_id`;
    neither is copied onto the cohort, which is the spine's whole point (see
    AGENTS.md, "the institutional spine"). Both are nullable, so this answers
    None rather than raising for a batch that has not been filed yet.
    """
    if not cohort_id:
        return None
    return college_ids_for_cohorts(db, [cohort_id]).get(cohort_id)
