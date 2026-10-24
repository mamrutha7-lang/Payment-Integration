"""The identity ledger's shape, pinned without a database.

`app/export_identity.py` writes the one file that is meant to outlive every
schema this product will ever have, so the properties that make it replayable
are properties a test has to assert rather than a docstring has to promise.
Everything here runs on a hand-built row: the query needs Postgres, the SHAPE
does not, and the shape is the part with a ten-year contract.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app import export_identity


def _row(**over: object) -> dict[str, object]:
    """One joined row as `_query()` returns it. Overridable per test."""
    base: dict[str, object] = {
        "id": "a3f91c7d4e2b48c19f0a5b6c7d8e9f01",
        "email": "1mp25mdm01@bgscet.ac.in",
        "name": "Asha R",
        "role": "STUDENT",
        "password_hash": "scrypt:" + "a" * 32 + ":" + "b" * 128,
        "google_sub": "107812345678901234567",
        "usn": "1MP25MDM01",
        "designation": None,
        "current_stage": "EXCEL",
        "current_semester": 2,
        "batch_label": "MBA 2025-27",
        "course_name": "MBA",
        "specialization_name": "Business Analytics",
        "cohort_department": "Management Studies",
        "cohort_college": "BGSCET",
        "cohort_college_code": "1MP",
        "student_department": None,
        "student_college": None,
        "student_college_code": None,
        "staff_department": None,
        "staff_college": None,
        "staff_college_code": None,
        "created_at": datetime(2025, 8, 1, 6, 30, tzinfo=timezone.utc),
        "disabled_at": None,
        "disable_reason": None,
    }
    base.update(over)
    return base


STAMP = "2026-09-15T00:00:00+00:00"


# --------------------------------------------------------- the three keys --


def test_the_three_fields_that_rebuild_a_login_are_carried_verbatim() -> None:
    """person_uuid, password_hash and google_sub are the whole point.

    If any one of these is dropped or transformed, the file stops being able to
    do its job and nothing else in the product would notice.
    """
    row = _row()
    rec = export_identity.compose(row, STAMP)
    assert rec["person_uuid"] == row["id"]
    assert rec["password_hash"] == row["password_hash"]
    assert rec["google_sub"] == row["google_sub"]


def test_the_sso_only_sentinel_is_carried_too() -> None:
    """"This account has no password" is itself a fact that must survive.

    `grant_access` and `seed_roster` mint accounts holding a sentinel that is
    deliberately not a scrypt hash. Skipping it on export would replay those
    accounts as password-less-by-accident rather than password-less-by-design,
    and an operator restoring them could not tell the difference.
    """
    rec = export_identity.compose(_row(password_hash="google-only"), STAMP)
    assert rec["password_hash"] == "google-only"


# ------------------------------------------------- labels, never pointers --


def test_no_foreign_key_ever_reaches_the_file() -> None:
    """The load-bearing invariant: labels re-resolve, ids do not.

    A future schema mints new cohort/department/college ids, so an id here is a
    pointer into a database that no longer exists -- a string that looks like
    data and is not. `person_uuid` is the one exception and it is not a pointer:
    it is the anchor every restored record re-attaches to.
    """
    rec = export_identity.compose(_row(), STAMP)
    id_keys = [k for k in rec if k.endswith("_id") or k.endswith("_uuid")]
    assert id_keys == ["person_uuid"], f"a foreign key reached the ledger: {id_keys}"


def test_institution_is_exported_as_names() -> None:
    rec = export_identity.compose(_row(), STAMP)
    assert rec["college"] == "BGSCET"
    assert rec["department"] == "Management Studies"
    assert rec["batch"] == "MBA 2025-27"


def test_an_unseated_student_still_carries_a_college() -> None:
    """31f7a4c60b12's bug, in the ledger.

    College and Department are required at registration while Batch is not, so
    "named a department, seated in nothing" is the NORMAL state of a college
    that has not built its batches. Reading only the cohort path would export
    every one of those students with no institution at all.
    """
    rec = export_identity.compose(
        _row(
            batch_label=None,
            cohort_department=None,
            cohort_college=None,
            cohort_college_code=None,
            student_department="Management Studies",
            student_college="BGSCET",
            student_college_code="1MP",
        ),
        STAMP,
    )
    assert rec["college"] == "BGSCET"
    assert rec["department"] == "Management Studies"
    assert rec["batch"] is None


def test_a_faculty_account_carries_its_college_through_the_staff_pointer() -> None:
    """A MENTOR has no `students` row, so only `users.department_id` places it."""
    rec = export_identity.compose(
        _row(
            role="MENTOR",
            usn=None,
            current_stage=None,
            current_semester=None,
            batch_label=None,
            course_name=None,
            specialization_name=None,
            cohort_department=None,
            cohort_college=None,
            cohort_college_code=None,
            staff_department="Management Studies",
            staff_college="BGSCET",
            staff_college_code="1MP",
        ),
        STAMP,
    )
    assert rec["role"] == "MENTOR"
    assert rec["college"] == "BGSCET"
    assert rec["usn"] is None


def test_the_batch_wins_over_the_students_own_pointer() -> None:
    """Same precedence `student_placement.resolve_student_department` enforces.

    Two readers disagreeing about which pointer is authoritative is how one
    person ends up filed under two departments on two screens.
    """
    rec = export_identity.compose(
        _row(cohort_department="Management Studies", student_department="Civil"),
        STAMP,
    )
    assert rec["department"] == "Management Studies"


# ------------------------------------------------------------ the format --


def test_render_is_one_json_object_per_line() -> None:
    """JSONL, not a JSON array, and the failure mode is the reason.

    A truncated array is unparseable in its entirety; a truncated JSONL file
    loses only its last line and every account above it still reads.
    """
    rows = [export_identity.compose(_row(id=f"id{i}"), STAMP) for i in range(3)]
    body = export_identity.render(rows)
    lines = body.splitlines()
    assert len(lines) == 3
    assert body.endswith("\n")
    for line in lines:
        parsed = json.loads(line)
        assert parsed["ledger_version"] == export_identity.LEDGER_VERSION


def test_every_line_carries_its_format_version() -> None:
    """A reader ten years from now branches on this rather than guessing."""
    rec = export_identity.compose(_row(), STAMP)
    assert rec["ledger_version"] == export_identity.LEDGER_VERSION


def test_a_name_is_stored_as_the_person_spells_it() -> None:
    """ensure_ascii=False: a name is not a pile of escapes."""
    body = export_identity.render([export_identity.compose(_row(name="Ananya Iyer"), STAMP)])
    assert "Ananya Iyer" in body


# --------------------------------------------------------------- the key --


def test_object_key_partitions_by_utc_day() -> None:
    when = datetime(2026, 9, 15, 23, 59, tzinfo=timezone.utc)
    key = export_identity.object_key(when)
    assert key.endswith("2026/09/15.jsonl")


def test_object_key_honours_a_blank_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export_identity.settings, "identity_ledger_prefix", "", raising=False)
    key = export_identity.object_key(datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert key == "2026/09/15.jsonl"
    assert not key.startswith("/")


# ------------------------------------------------------------ the refusal --


def test_an_empty_export_is_refused_rather_than_written() -> None:
    """A file with no accounts would overwrite a good ledger with a bad one.

    An empty export is indistinguishable from a healthy export of an empty
    deployment, so it cannot be allowed to replace today's object on the day a
    query regresses. A genuinely empty deployment has nothing to protect and
    loses nothing by the refusal.
    """

    class _NoAccounts:
        def execute(self, *_a: object, **_k: object) -> "_NoAccounts":
            return self

        def mappings(self) -> list[dict[str, object]]:
            return []

    with pytest.raises(RuntimeError, match="empty ledger"):
        export_identity.run(_NoAccounts())  # type: ignore[arg-type]


# ----------------------------------------------- it never prints unasked --


class _OneAccount:
    """A session that yields exactly one joined row."""

    def execute(self, *_a: object, **_k: object) -> "_OneAccount":
        return self

    def mappings(self) -> list[dict[str, object]]:
        return [_row()]


def test_an_unconfigured_run_refuses_instead_of_printing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The leak Seer caught in review on PR #45.

    With no bucket this used to fall back to stdout, which is fine on a laptop
    and a CREDENTIAL LEAK anywhere else: on Fargate this process's stdout is the
    `/reep/api` CloudWatch group, 30-day retention, and the bytes are every
    account's password hash and Google subject. The window was real -- the
    schedule is created in the harden phase while the bucket variable arrives
    with the ECS half, so between cutover steps 9a and 9b the job would have
    fired nightly, unconfigured, straight into the log.

    Fails without the fix: `run()` returned a summary and wrote the body.
    """
    monkeypatch.setattr(export_identity.settings, "identity_ledger_bucket", "", raising=False)
    with pytest.raises(RuntimeError, match="nowhere to put the ledger"):
        export_identity.run(_OneAccount())  # type: ignore[arg-type]
    assert capsys.readouterr().out == "", "the ledger reached stdout anyway"


def test_no_hash_reaches_stdout_on_the_refusal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The assertion that would have caught it: look for the secret itself.

    Checking only that an exception was raised would pass on a version that
    printed first and raised afterwards.
    """
    monkeypatch.setattr(export_identity.settings, "identity_ledger_bucket", "", raising=False)
    with pytest.raises(RuntimeError):
        export_identity.run(_OneAccount())  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "scrypt:" not in out
    assert "google_sub" not in out


def test_stdout_still_works_when_it_is_asked_for(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--stdout` is the one way bytes reach the terminal, and it must be typed.

    Kept working on purpose: reading the ledger you are about to store is how an
    operator checks it before trusting it.
    """
    monkeypatch.setattr(export_identity.settings, "identity_ledger_bucket", "", raising=False)
    summary = export_identity.run(_OneAccount(), to_stdout=True)  # type: ignore[arg-type]
    assert summary["destination"] == "stdout"
    assert "scrypt:" in capsys.readouterr().out
