"""How a batch is written for a person, in one place.

A BATCH IS A YEAR. "2026-28" is the whole of what a batch is: the span a
student who joined a two-year degree belongs to. WHO that batch belongs to --
college, department, course, specialization -- is not part of its name; it is
the spine it hangs off, and `cohorts` already carries every rung of it as a real
foreign key (`department_id`, `course_id`, `specialization_id`, with
`_resolve_ancestry` in `routers/admin.py` as their only writer).

WHAT THIS MODULE FIXES. Both writers of the spine used to MANUFACTURE the
course and the specialization into `cohorts.name` -- `seed_catalogue.batch_name`
and the Set-up-a-college screen agreed on "General MBA - Finance 2026-28" -- and
then every screen printed that string NEXT TO the year again, because the label
is its own column:

    f"{cohort.name} · {cohort.batch_label}"   ->  "General MBA - Finance 2026-28 · 2026-28"

Five endpoints did exactly that (`admin_students`, `swoc`, `registration`,
`governance`, `admin_promotion`), each with its own copy of the f-string. So one
fact was stored twice -- once as a foreign key that `ancestry_of_student` reads
and once as words inside a name nobody could join on -- and the two could
disagree the moment the office renamed a course, with the batch still printing
last year's name under a screen that says "verified by Main Admin".

THE RULE IS THEREFORE: the spine comes from the LINKS, the year comes from the
BATCH, and they are put together HERE and nowhere else. `compose` is the only
spelling any endpoint uses, and its twin on the client is
`apps/web/src/app/core/batch-label.ts` -- pinned against each other by
`tests/test_batch_labels.py`, because two implementations of one sentence drift
the first time somebody changes the separator.

WHY THE TAIL CARRIES BOTH `batch_label` AND `name`. For every batch the seeder
or the setup screen writes they are the same string -- the year -- so only one
of them prints. Where they differ is the NON-STANDARD batch the office types on
College structure: a section, or odd dates, named "2026-28 Section B". That is
the office's own word for this batch and it must survive onto every screen,
which printing `batch_label` alone would silently drop -- and a name that says
nothing about the span ("Chain Batch") must not drop the YEAR, which printing
`name` alone did until `test_registration_hierarchy` caught it. `compose` takes
both and prints the name alone only when it already contains the span.
`batch_label` keeps its other jobs too (sorting, the import picker's term, the
student's profile card); this is not a second place to say the name.
"""

from __future__ import annotations

#: Between a course and its specialization. Both are rungs of one path, so they
#: read as one phrase.
SPINE_JOIN = " - "
#: Between the spine and the batch. A middle dot, the separator the console
#: already uses everywhere it puts two facts on one line.
LABEL_JOIN = " · "


def compose(
    course_name: str | None,
    specialization_name: str | None,
    name: str,
    batch_label: str,
) -> str:
    """"General MBA - Finance \u00b7 2026-28" -- the spine, then the batch.

    Every argument is allowed to be missing, and each absence is a real shape
    rather than an error:

    * no specialization -- a two-year MBA in Digital Marketing IS the
      qualification, with nothing to specialise into (`seed_catalogue.Course`
      says so at length), so it reads "Digital Marketing \u00b7 2026-28";
    * no course -- a batch may legitimately hang at department level, because
      `HIERARCHY_LEVELS` makes Course optional, so it reads "2026-28" and the
      department is on the row already.

    THE YEAR IS ALWAYS THERE, AND THAT TOOK A SECOND ATTEMPT. The first version
    took only `name` on the reasoning that the seeder and the setup screen now
    write the year into it, so the label would be a duplicate. It is -- for the
    batches THOSE two write. It is not for a batch the office typed a name for:
    `test_registration_hierarchy.test_a_batch_pins_its_department_and_college`
    builds one called "Chain Batch" at department level, and composing from the
    name alone printed "Chain Batch" with the span gone from the reviewer's
    "Approving seats them in ...". A batch IS a year; a rendering that can drop
    the year is the bug this module was written to remove, arriving from the
    other side. So the year leads the tail and the office's own words follow
    it: "2024-26 \u00b7 Chain Batch".

    THE ONE SUBSTRING TEST IS WHAT KEEPS A SECTION FROM STUTTERING. Where the
    name already carries the span -- "2026-28" itself, the section "2026-28
    Section B", or a legacy row somebody renamed by hand -- printing the label
    beside it would give "2026-28 \u00b7 2026-28 Section B". Those are their
    words about their batch, and they already answer "which year", so they
    stand alone. Anything else is two different facts and is joined as two.

    It never returns an empty string: `cohorts.name` and `cohorts.batch_label`
    are both NOT NULL, so a batch with nothing to print is unreachable -- but a
    blank option in a picker is one nobody can choose, and a separator with a
    hole after it is worse than the course alone.
    """
    spine = SPINE_JOIN.join(
        part.strip() for part in (course_name, specialization_name) if part and part.strip()
    )
    own = (name or "").strip()
    label = (batch_label or "").strip()
    if own and label and label not in own:
        tail = f"{label}{LABEL_JOIN}{own}"
    else:
        tail = own or label
    if not spine:
        return tail
    if not tail:
        return spine
    return f"{spine}{LABEL_JOIN}{tail}"
