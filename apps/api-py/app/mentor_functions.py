"""The capabilities a faculty account holds by having mentees.

B2.3 of docs/redesign-2026-09/04-backend-changes.md.

ROLE WAS DOING TWO JOBS. `ROLE_BASELINE["MENTOR"]` handed every faculty account
every SCOPED capability in the catalogue, so "is this person a member of staff"
and "may this person read a mentee's ledger" were one question with one answer.
They are not one question. A faculty member who has never been assigned a student
has no mentee log to read and no notebook to keep.
AGENTS.md says exactly that in words -- "a faculty account is not a mentor by
existing" -- and every one of these endpoints already behaves that way, but the
capability said otherwise, and the capability is what Governance shows the office
when they ask who can see what.

DERIVED, NOT GRANTED, AND THAT IS A DEPARTURE FROM 04 WORTH READING.

The spec writes these as `capability_grants` rows: written by
`ensure_mentor_group`, revoked when the last mentee is released, backfilled by a
migration. That was built first and thrown away, because the first test run
showed what it costs. Five places in this repository set `students.mentor_id` --
three routers, `app/seed.py` and `app/grant_access.py` -- and a stored grant is
correct only while every one of them remembers to re-derive. The fixtures that
went red were not the bug; they were a preview of it. The sixth writer somebody
adds next year will not fail loudly: a faculty member will simply be unable to
open their own mentee log, and the row that would explain why is the one nobody
wrote.

So the capability is a pure function of the state it describes: do you currently
mentor anybody? There is no second copy to drift, no backfill to run, no sweep to
get wrong, and no path that can forget. It is the same reasoning AGENTS.md
already applies elsewhere -- §13's display status is "DERIVED, never stored", the
badge catalogue is code and "only the status is a row", and one milestone is
derived rather than stored because "two sources of truth for one row is how that
row ends up saying 'not started' under a finished report".

WHAT THAT COSTS, HONESTLY: a derived function cannot carry an expiry, cannot be
revoked on its own, and leaves no historical record of who could see what last
March. The first two are features here -- a function you hold because you mentor
somebody should end when that does, not on a date -- and the third is the mentor
assignment history's job (B9.1), which is the record of the fact underneath.

IT IS ADDITIVE TO REAL GRANTS. The Main Admin granting itself `mentor.mentees`
in Governance because a student's evidence is stuck still works exactly as
before: `capabilities_for` unions the baseline, these, and the grants. This only
ever adds.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models.user import Mentor, Student, User

#: The two a mentee brings. `mentor.agent` and `mentor.upskilling` are NOT here
#: and stay in the baseline: the assistant and one's own certificate shelf belong
#: to the person, not to the group, and taking them away between assignments
#: would be removing their own things. `mentor.leave_approve` WAS the fourth
#: until 2026-09-16: leave approval is the Main Admin's alone now
#: (routers/leave.py) and the key left the catalogue with the door.
#:
#: `mentor.verifications` WAS THE THIRD UNTIL 2026-10-16 (the owner's decision:
#: "remove skill verification from all mentors"). Verifying a student's skills
#: and documents is now done only by the faculty the placement office chooses,
#: through a grant in Who can do what; mentoring somebody brings none of it.
#: app/verifiers.py is the one gate every verification door asks.
MENTOR_FUNCTIONS: Final[frozenset[str]] = frozenset({"mentor.mentees", "mentor.notebook"})

#: A MENTOR READS THEIR OWN MENTEES' FULL RECORDS (2026-10-16, the owner's
#: report: "mentors' mentee records are not visible"). Until then the Student
#: 360 page opened for a faculty member only beside a "View student records"
#: grant from the office, so a mentor saw a student's name on the Mentee Log
#: and nothing behind it -- no "Full record" link, no "Student records" row,
#: and the route guard turned them away if they typed the address.
#:
#: Derived for the same reason the two above are: it is a fact about who
#: they mentor, and five places set `students.mentor_id`. It reaches no one
#: else. The key is PROGRAMME on the catalogue, but every read it gates runs
#: rule 2 underneath (`_assert_can_access_student`), which admits a MENTOR to
#: their own group only; a student outside it still needs the office's SCOPED
#: grant, which this does not touch (`policies._holds_records_grant` reads
#: grants, never functions). Every endpoint behind the key is a GET.
MENTEE_RECORD_FUNCTIONS: Final[frozenset[str]] = frozenset({"admin.student_records"})

#: Everything having a mentee brings, which is what `mentor_functions_for`
#: answers.
DERIVED_FROM_MENTEES: Final[frozenset[str]] = MENTOR_FUNCTIONS | MENTEE_RECORD_FUNCTIONS


def mentee_count(db: Session, user_id: str) -> int:
    """How many students this faculty account currently mentors.

    Through the `mentors` row, which is the join students point at. Having the
    row and having a mentee are different things -- `ensure_mentor_group` creates
    it on first assignment and nothing ever deletes it -- and it is the MENTEE
    that carries the work, so it is the mentee that carries the function.
    """
    return (
        db.scalar(
            select(func.count(Student.id))
            .join(Mentor, Student.mentor_id == Mentor.id)
            # A REMOVED student (users.deleted_at) is off the roster and does
            # not keep a faculty member's functions alive; Restore brings
            # both back, because the pointer is never touched.
            .join(User, Student.user_id == User.id)
            .where(Mentor.user_id == user_id, User.deleted_at.is_(None))
        )
        or 0
    )


def mentor_functions_for(db: Session, user_id: str) -> frozenset[str]:
    """The two, and the read of the mentees' records, if this account
    mentors anybody. Otherwise nothing."""
    if not user_id:
        return frozenset()
    return DERIVED_FROM_MENTEES if mentee_count(db, user_id) > 0 else frozenset()
