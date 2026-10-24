"""A batch is a YEAR; its course and specialization are LINKS.

`app/batch_labels.py` is the one place the two are put back together for a
person to read, and this module pins that sentence — plus the fact that its
client twin, `apps/web/src/app/core/batch-label.ts`, says the same thing. Two
implementations of one rule drift the first time somebody changes a separator,
and the symptom is a picker where two batches read alike.

NO DATABASE. Every assertion here is pure, so this file runs on a machine with
no Postgres and fails fast in CI before the integration suite has connected.
"""

from __future__ import annotations

import re
from pathlib import Path

from app import batch_labels
from app.seed_catalogue import batch_name

WEB_TWIN = (
    Path(__file__).resolve().parents[3] / "apps/web/src/app/core/batch-label.ts"
)


def test_the_spine_and_the_year_read_as_one_sentence() -> None:
    assert (
        batch_labels.compose("General MBA", "Finance", "2026-28", "2026-28")
        == "General MBA - Finance · 2026-28"
    )


def test_a_course_that_is_the_qualification_has_no_specialization_to_name() -> None:
    # A two-year MBA in Digital Marketing IS the qualification — there is
    # nothing to specialise into — so the absence is a real shape and must not
    # render as a separator with a hole after it.
    assert batch_labels.compose("Digital Marketing", None, "2026-28", "2026-28") == (
        "Digital Marketing · 2026-28"
    )
    assert batch_labels.compose("Digital Marketing", "  ", "2026-28", "2026-28") == (
        "Digital Marketing · 2026-28"
    )


def test_a_batch_hanging_at_department_level_reads_as_the_year_alone() -> None:
    # Course is OPTIONAL (`HIERARCHY_LEVELS`), so this is legitimate, and the
    # department is already on the row that drew it.
    assert batch_labels.compose(None, None, "2026-28", "2026-28") == "2026-28"
    assert batch_labels.compose(None, "Finance", "2026-28", "2026-28") == "Finance · 2026-28"


def test_the_year_is_always_there_even_when_the_name_does_not_say_it() -> None:
    """The regression `test_registration_hierarchy` caught on the first attempt.

    A batch named "Chain Batch" hanging at DEPARTMENT level has no spine to
    compose and a name that says nothing about the span, so composing from the
    name alone printed "Chain Batch" and the year vanished from the reviewer's
    "Approving seats them in ...". A batch IS a year: the year leads, and the
    office's own words follow it.
    """
    assert batch_labels.compose(None, None, "Chain Batch", "2024-26") == (
        "2024-26 \u00b7 Chain Batch"
    )
    assert batch_labels.compose("General MBA", None, "Chain Batch", "2024-26") == (
        "General MBA \u00b7 2024-26 \u00b7 Chain Batch"
    )


def test_the_office_s_own_name_for_a_batch_survives_verbatim() -> None:
    # The one batch that needs more than its span to be told apart: a section.
    # Printing `batch_label` instead would silently drop the office's word for
    # it, which is why the tail is `name`.
    assert batch_labels.compose("General MBA", "Finance", "2026-28 Section B", "2026-28") == (
        "General MBA - Finance · 2026-28 Section B"
    )


def test_it_never_returns_an_empty_string_for_a_batch_that_has_a_spine() -> None:
    # `cohorts.name` is NOT NULL, so this is a defensive shape rather than a
    # reachable one — but a blank option in a picker is unpickable, and the
    # separator with nothing after it is worse than the course alone.
    assert batch_labels.compose("General MBA", "Finance", "", "") == "General MBA - Finance"
    assert batch_labels.compose("General MBA", None, "   ", "") == "General MBA"


def test_the_seeder_names_a_batch_the_year_and_nothing_else() -> None:
    # The whole point: `seed_catalogue` used to manufacture the spine into the
    # name, so the fact lived in two places and the screens printed the year
    # twice. It is one place now, and it is the foreign keys.
    assert batch_name("2026-28") == "2026-28"


def test_the_client_twin_joins_the_parts_the_same_way() -> None:
    """The separators are the contract between the two implementations.

    Read as text rather than executed: there is no Node in the `api` job, and
    the thing that actually breaks a screen is one file being edited without
    the other, which a string comparison catches exactly as well.
    """
    source = WEB_TWIN.read_text(encoding="utf-8")
    spine = re.search(r"export const SPINE_JOIN = '([^']*)';", source)
    label = re.search(r"export const LABEL_JOIN = '([^']*)';", source)
    assert spine is not None and label is not None, (
        f"{WEB_TWIN} no longer declares SPINE_JOIN and LABEL_JOIN as plain "
        "string constants — keep them readable from here, or this guard stops "
        "comparing anything."
    )
    assert spine.group(1) == batch_labels.SPINE_JOIN
    assert label.group(1) == batch_labels.LABEL_JOIN
