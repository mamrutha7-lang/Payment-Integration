"""One spelling of a USN, and one way to ask whether two are the same.

THE ROSTER STORES A USN UPPER CASE (`seed_roster`, the Students editor), and
until 2026-10-16 the public registration form stored it AS TYPED. The unique
index on `students.usn` and every lookup were case-sensitive, so an applicant
who typed `1mp25ba033` was not flagged against a student holding `1MP25BA033`,
and approving them put a second student on the roster with the same number in
lower case.

So every writer normalises with `normalise_usn`, and every "does somebody
already hold this USN" question asks `usn_is`, which compares upper case on
both sides. That second half matters as much as the first: a lowercase USN
already on a row (written before this module existed) is still found.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement


def normalise_usn(value: str | None) -> str | None:
    """A USN as the roster stores it: stripped and upper case, None when blank."""
    if value is None:
        return None
    value = value.strip().upper()
    return value or None


def usn_is(column: ColumnElement[str], usn: str) -> ColumnElement[bool]:
    """True where `column` holds `usn`, whatever case either was written in."""
    return func.upper(column) == usn.strip().upper()
