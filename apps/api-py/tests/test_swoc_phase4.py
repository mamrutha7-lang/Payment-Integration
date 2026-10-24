"""B7 — ownership, the viewpoint, history, the semester and the acknowledgement.

The claims, and why each is here rather than assumed:

* B7.1 — the viewpoint is derived from the RELATIONSHIP, not from the role. A
  mentor of this student writes MENTOR; a granted colleague who is not their
  mentor writes PLACEMENT. The board is deliberately un-averaged and a label
  that means "any faculty account" erases the one thing the source column keeps.
* B7.2 — the author edits their own line; a colleague with the same grant is
  refused; the Main Admin may edit any. An authorless line (seeded, or written
  before the column) is the OFFICE's alone: "no author" must not read as
  "everybody is the author".
* B7.3 — a PATCH writes a revision, and the scoped history endpoint returns it.
  An acknowledgement is NOT an edit and must not stamp `updated_at`.
* B7.4 — the semester is stamped at write time from the student's own row, and
  a promotion afterwards does not move the line.
* B7.5 — the student sees id, author, date and acknowledgement, acknowledging is
  idempotent and first-person, and somebody else's entry is a 404.
* B7.6 — a link must belong to this student. `linked_session_id` naming another
  student's interview is a 422, not a stored id echoed back on every GET. And
  the other half of the same task: a weight >= 4 WEAKNESS reaches the student's
  next actions, with the link supplying the `cta_route` a sentence about a
  person does not have, capped at two so one board cannot take over the card.
* B7.7 — `?cohort_id=` and `?q=` narrow within the reach and cannot widen it;
  paging is opt-in, the response stays a bare array, and `X-Reep-Total` is
  stated either way so a short list and a truncated one are distinguishable.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app.db import SessionLocal
from app.models.redesign import AuditEvent
from app.models.swoc import SwocEntry, SwocEntryRevision
from app.models.user import Mentor, Role, Student, User

API = "/api/admin/swoc"
MINE = "/api/student/swoc"
GOV = "/api/admin/governance"
ADMIN = "/api/admin"


def _student_id(user_id: str) -> str:
    with SessionLocal() as db:
        return db.scalar(select(Student.id).where(Student.user_id == user_id))


@pytest.fixture
def swept(make_user):
    """Entries CASCADE with their student; the audit rows have no FK. Requesting
    `make_user` orders this teardown BEFORE the accounts go, so the ids are
    still readable. Revisions cascade with their entry, so they need no line of
    their own — which is the whole reason that FK is CASCADE and the three
    `linked_*` ones are SET NULL."""
    student_ids: list[str] = []
    yield student_ids
    with SessionLocal() as db:
        for sid in student_ids:
            entry_ids = db.scalars(select(SwocEntry.id).where(SwocEntry.student_id == sid)).all()
            if entry_ids:
                db.execute(
                    delete(AuditEvent).where(
                        AuditEvent.entity_type == "swoc_entry",
                        AuditEvent.entity_id.in_(entry_ids),
                    )
                )
            db.execute(delete(SwocEntry).where(SwocEntry.student_id == sid))
        db.commit()


def _grant(client, admin, user_id: str, capability: str = "admin.swoc") -> list[str]:
    """A grant through the real endpoint, by the Main Admin — live at once.

    `admin.swoc` carries PII, so a DEPUTY's grant of it would land
    `pending_approval` and wait for the office (B2.4); the office's own does
    not (2026-09-16), which is why nothing here approves anything."""
    r = client.post(
        f"{GOV}/grants",
        headers=admin.headers,
        json={
            "capability": capability,
            "user_ids": [user_id],
            "reason": "This faculty member writes SWOC lines for the batch this term.",
        },
    )
    assert r.status_code == 201, r.text
    return [g["id"] for g in r.json()]


# ------------------------------------------------------------- B7.1 --


@requires_db
def test_the_viewpoint_follows_the_relationship_and_not_the_role(
    client, make_user, swept
):
    """A mentor of THIS student writes MENTOR; the same account writes
    PLACEMENT about a student they do not mentor.

    Before B7.1 `_source_for` read `session["role"]` alone, so one granted
    lecturer filed every line on a department's board as MENTOR — including
    lines about students they had never met. The board exists to keep two
    viewpoints apart; a label that means "any faculty account" collapses them.
    """
    admin = make_user("sw4-vp-adm", Role.ADMIN)
    faculty = make_user("sw4-vp-fac", Role.MENTOR)
    mine = make_user("sw4-vp-stu1", Role.STUDENT)
    theirs = make_user("sw4-vp-stu2", Role.STUDENT)
    mine_id, theirs_id = _student_id(mine.user_id), _student_id(theirs.user_id)
    swept += [mine_id, theirs_id]

    assert client.post(
        f"{ADMIN}/students/{mine_id}/mentor",
        headers=admin.headers,
        json={"mentor_user_id": faculty.user_id, "reason": "Their tutor this term."},
    ).status_code == 204
    _grant(client, admin, faculty.user_id)

    entry = {"kind": "weakness", "text": "Needs structured problem-solving practice."}
    r = client.post(f"{API}/{mine_id}", headers=faculty.headers, json=entry)
    assert r.status_code == 201, r.text
    assert r.json()["source"] == "MENTOR"

    r = client.post(f"{API}/{theirs_id}", headers=faculty.headers, json=entry)
    assert r.status_code == 201, r.text
    assert r.json()["source"] == "PLACEMENT", (
        "a granted faculty member who does not mentor this student speaks for "
        "the placement cell, not as their mentor"
    )

    # And the office always speaks for the placement cell.
    r = client.post(f"{API}/{mine_id}", headers=admin.headers, json=entry)
    assert r.status_code == 201 and r.json()["source"] == "PLACEMENT"


# ------------------------------------------------------------- B7.2 --


@requires_db
def test_only_the_author_edits_their_line_and_the_office_edits_any(
    client, make_user, swept
):
    """B7.2. Any holder in reach could rewrite or delete anybody's line before
    this — which is "erase the one thing the source column exists to keep",
    named in this module's own docstring as the risk.

    The override is the ROLE and not `admin.swoc`: that key is what everybody on
    this screen holds, so "author or `admin.swoc` holder" is "anybody" written
    at more length.
    """
    admin = make_user("sw4-own-adm", Role.ADMIN)
    author = make_user("sw4-own-a", Role.MENTOR)
    colleague = make_user("sw4-own-b", Role.MENTOR)
    stu = make_user("sw4-own-stu", Role.STUDENT)
    sid = _student_id(stu.user_id)
    swept.append(sid)
    _grant(client, admin, author.user_id)
    _grant(client, admin, colleague.user_id)

    r = client.post(
        f"{API}/{sid}", headers=author.headers,
        json={"kind": "strength", "text": "Quick learner, asks good questions."},
    )
    assert r.status_code == 201, r.text
    entry_id = r.json()["id"]

    # The colleague can SEE it — same reach — and cannot rewrite or remove it.
    assert any(
        e["id"] == entry_id
        for row in client.get(API, headers=colleague.headers).json()
        for e in row["entries"]
    )
    bad = client.patch(f"{API}/entries/{entry_id}", headers=colleague.headers, json={"text": "No."})
    assert bad.status_code == 403 and "somebody else" in bad.text
    assert client.delete(f"{API}/entries/{entry_id}", headers=colleague.headers).status_code == 403

    # The author can.
    assert client.patch(
        f"{API}/entries/{entry_id}", headers=author.headers,
        json={"text": "Quick learner; asks unusually good questions."},
    ).status_code == 200
    # And so can the office.
    assert client.patch(
        f"{API}/entries/{entry_id}", headers=admin.headers, json={"weight": 5}
    ).status_code == 200


@requires_db
def test_an_authorless_line_belongs_to_the_office_alone(client, make_user, swept):
    """A seeded row, or one written before `author_user_id` existed, is
    editable by the Main Admin and by nobody else.

    Treating "no author" as "everybody is the author" would make exactly the
    rows nobody is answerable for the easiest to rewrite.
    """
    admin = make_user("sw4-anon-adm", Role.ADMIN)
    faculty = make_user("sw4-anon-fac", Role.MENTOR)
    stu = make_user("sw4-anon-stu", Role.STUDENT)
    sid = _student_id(stu.user_id)
    swept.append(sid)
    _grant(client, admin, faculty.user_id)

    with SessionLocal() as db:
        from app.models.swoc import SwocKind, SwocSource

        e = SwocEntry(
            student_id=sid, source=SwocSource.PM, kind=SwocKind.OPPORTUNITY,
            text="Fintech internships opening this quarter.", weight=3,
        )
        db.add(e)
        db.commit()
        entry_id = e.id

    assert client.patch(
        f"{API}/entries/{entry_id}", headers=faculty.headers, json={"weight": 1}
    ).status_code == 403
    assert client.patch(
        f"{API}/entries/{entry_id}", headers=admin.headers, json={"weight": 1}
    ).status_code == 200
    # And the two "no author" facts are finally separable on the wire.
    row = client.get(API, headers=admin.headers).json()
    entry = next(e for r in row for e in r["entries"] if e["id"] == entry_id)
    assert entry["author"] is None and entry["author_recorded"] is False


# ------------------------------------------------------------- B7.3 --


@requires_db
def test_an_edit_writes_a_revision_and_an_acknowledgement_does_not(client, make_user, swept):
    """The history endpoint returns what changed, and `updated_at` means EDITED.

    `SwocEntry.updated_at` deliberately carries no `onupdate`: SQLAlchemy fires
    that on ANY update of the row, so the student pressing "I have read this"
    would stamp it and the admin board would report the line as edited — by
    nobody, with no revision behind it.
    """
    admin = make_user("sw4-hist-adm", Role.ADMIN)
    stu = make_user("sw4-hist-stu", Role.STUDENT)
    sid = _student_id(stu.user_id)
    swept.append(sid)

    r = client.post(
        f"{API}/{sid}", headers=admin.headers,
        json={"kind": "weakness", "text": "Rushes the closing summary.", "weight": 3},
    )
    assert r.status_code == 201, r.text
    entry_id, written_at = r.json()["id"], r.json()["updated_at"]
    assert r.json()["recorded_at"] == written_at, "an unedited line reads as written"

    assert client.get(f"{API}/{sid}/history", headers=admin.headers).json() == [], (
        "no edit yet means no revision — not a fabricated one"
    )

    patched = client.patch(
        f"{API}/entries/{entry_id}", headers=admin.headers,
        json={"text": "Rushes the closing summary under time pressure.", "weight": 4},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["updated_at"] != written_at

    history = client.get(f"{API}/{sid}/history", headers=admin.headers).json()
    assert len(history) == 1
    assert history[0]["before"]["weight"] == 3 and history[0]["after"]["weight"] == 4
    assert history[0]["entry_id"] == entry_id

    # The student acknowledging is NOT an edit.
    before_ack = patched.json()["updated_at"]
    assert client.post(f"{MINE}/{entry_id}/acknowledge", headers=stu.headers).status_code == 200
    again = next(
        e for row in client.get(API, headers=admin.headers).json()
        for e in row["entries"] if e["id"] == entry_id
    )
    assert again["updated_at"] == before_ack, "an acknowledgement reported the line as edited"
    assert again["acknowledged_at"] is not None
    assert len(client.get(f"{API}/{sid}/history", headers=admin.headers).json()) == 1

    with SessionLocal() as db:
        assert db.scalar(
            select(SwocEntryRevision.id).where(SwocEntryRevision.entry_id == entry_id)
        ) is not None


# ------------------------------------------------------------- B7.4 --


@requires_db
def test_the_semester_is_stamped_at_write_time_and_a_promotion_does_not_move_it(
    client, make_user, swept
):
    """The only moment anybody knows which semester a line belongs to is the
    moment it is written — which is exactly why the migration refuses to
    backfill one onto historical rows, and leaves them NULL to render as
    "semester not recorded"."""
    admin = make_user("sw4-sem-adm", Role.ADMIN)
    stu = make_user("sw4-sem-stu", Role.STUDENT)
    sid = _student_id(stu.user_id)
    swept.append(sid)
    with SessionLocal() as db:
        db.get(Student, sid).current_semester = 2
        db.commit()

    r = client.post(
        f"{API}/{sid}", headers=admin.headers,
        json={"kind": "challenge", "text": "Public speaking under time pressure."},
    )
    assert r.status_code == 201 and r.json()["semester"] == 2

    with SessionLocal() as db:
        db.get(Student, sid).current_semester = 3
        db.commit()
    still = next(
        e for row in client.get(API, headers=admin.headers).json()
        for e in row["entries"] if e["id"] == r.json()["id"]
    )
    assert still["semester"] == 2, "a promotion rewrote when an observation was made"

    # And the filter narrows ENTRIES, never the student rows: a student with
    # nothing written this term is still on the board with an empty quadrant.
    rows = client.get(f"{API}?semester=3", headers=admin.headers).json()
    mine = next(row for row in rows if row["student_id"] == sid)
    assert mine["entries"] == []


# ------------------------------------------------------------- B7.5 --


@requires_db
def test_the_student_sees_who_wrote_it_and_acknowledges_only_their_own(
    client, make_user, swept
):
    """First-person, with the id from the session and never from the request.

    An entry id is a bare uuid; without the `student_id` check this endpoint
    would acknowledge any line about any student on the deployment, which is a
    write against somebody else's record. Acknowledging twice keeps the FIRST
    timestamp — a double-tap is not a second reading — and there is no un-
    acknowledge, because "I read it" is not a thing a later click makes untrue.
    """
    admin = make_user("sw4-ack-adm", Role.ADMIN)
    stu = make_user("sw4-ack-stu", Role.STUDENT)
    other = make_user("sw4-ack-stu2", Role.STUDENT)
    sid, other_id = _student_id(stu.user_id), _student_id(other.user_id)
    swept += [sid, other_id]

    mine = client.post(
        f"{API}/{sid}", headers=admin.headers,
        json={"kind": "strength", "text": "Strong analytical and quantitative skills."},
    ).json()
    theirs = client.post(
        f"{API}/{other_id}", headers=admin.headers,
        json={"kind": "strength", "text": "Runs a good meeting."},
    ).json()

    board = client.get(MINE, headers=stu.headers).json()
    item = board["strengths"][0]
    assert item["id"] == mine["id"]
    assert item["author_recorded"] is True and item["author"] is not None
    assert item["recorded_at"] and item["acknowledged_at"] is None

    r = client.post(f"{MINE}/{mine['id']}/acknowledge", headers=stu.headers)
    assert r.status_code == 200 and r.json()["acknowledged_at"] is not None
    first = r.json()["acknowledged_at"]
    again = client.post(f"{MINE}/{mine['id']}/acknowledge", headers=stu.headers)
    assert again.status_code == 200 and again.json()["acknowledged_at"] == first

    # Somebody else's line is a 404, not a 403: to this student it does not exist.
    assert client.post(f"{MINE}/{theirs['id']}/acknowledge", headers=stu.headers).status_code == 404
    # And staff cannot acknowledge on a student's behalf — the endpoint is
    # first-person and `_require_student` is what resolves the subject.
    assert client.post(f"{MINE}/{mine['id']}/acknowledge", headers=admin.headers).status_code in (403, 404)


# ------------------------------------------------------------- B7.6 --


@requires_db
def test_a_link_must_belong_to_this_student(client, make_user, swept):
    """Unchecked, `linked_session_id` is a way to name another student's
    interview on a board a third party reads: the id is echoed back on every GET
    of this entry, and an id is enough to ask the interview endpoints for the
    rest. A job posting is public and belongs to nobody, so it is checked for
    existence only — the same line rule 1 draws."""
    admin = make_user("sw4-link-adm", Role.ADMIN)
    stu = make_user("sw4-link-stu", Role.STUDENT)
    other = make_user("sw4-link-stu2", Role.STUDENT)
    sid, other_id = _student_id(stu.user_id), _student_id(other.user_id)
    swept += [sid, other_id]

    from app.models.skill import Skill, StudentSkill

    with SessionLocal() as db:
        skill_id = db.scalar(select(Skill.id).limit(1))
        if skill_id is None:
            pytest.skip("no skills catalogue on this database")
        theirs = StudentSkill(student_id=other_id, skill_id=skill_id)
        db.add(theirs)
        db.commit()
        theirs_id = theirs.id

    entry = {"kind": "weakness", "text": "Needs more reps on this."}
    bad = client.post(
        f"{API}/{sid}", headers=admin.headers, json={**entry, "linked_skill_id": theirs_id}
    )
    assert bad.status_code == 422 and "this student's skills" in bad.text

    with SessionLocal() as db:
        ours = StudentSkill(student_id=sid, skill_id=skill_id)
        db.add(ours)
        db.commit()
        ours_id = ours.id
    good = client.post(
        f"{API}/{sid}", headers=admin.headers, json={**entry, "linked_skill_id": ours_id}
    )
    assert good.status_code == 201, good.text
    assert good.json()["linked_skill_id"] == ours_id

    # An explicit null REMOVES the link — `model_fields_set`, not a truth test,
    # or removal would be impossible while looking like it worked.
    cleared = client.patch(
        f"{API}/entries/{good.json()['id']}", headers=admin.headers,
        json={"linked_skill_id": None},
    )
    assert cleared.status_code == 200 and cleared.json()["linked_skill_id"] is None

    with SessionLocal() as db:
        db.execute(delete(StudentSkill).where(StudentSkill.id.in_([ours_id, theirs_id])))
        db.commit()


# ------------------------------------------------------------- B7.6 --


@requires_db
def test_a_heavy_weakness_becomes_a_next_action_and_the_link_gives_it_a_route(
    client, make_user, swept
):
    """B7.6's two halves are one task, and this is why.

    `NextActionOut` requires a `cta_route`; a sentence somebody wrote about a
    student has no natural destination, and the LINK is what supplies one. So a
    weakness linked to a skill sends the student to their skilling screen and an
    unlinked one to the board it is written on.

    Weight >= 4 is the author's own judgement off the composer's weight picker,
    not a threshold this endpoint invented — a 3 is an observation, not a task.
    Only WEAKNESSES qualify: a weight-5 strength is the opposite of something to
    do. At most two reach the card, so one board cannot take over a list it is
    one input to. And the student's acknowledgement does NOT clear it: "I have
    read this" is not "I have done something about it".
    """
    admin = make_user("sw4-act-adm", Role.ADMIN)
    stu = make_user("sw4-act-stu", Role.STUDENT)
    sid = _student_id(stu.user_id)
    swept.append(sid)

    def ids() -> set[str]:
        r = client.get("/api/student/next-actions", headers=stu.headers)
        assert r.status_code == 200, r.text
        return {a["id"] for a in r.json()["actions"]}

    def action(entry_id: str) -> dict | None:
        r = client.get("/api/student/next-actions", headers=stu.headers)
        return next((a for a in r.json()["actions"] if a["id"] == f"swoc-{entry_id}"), None)

    light = client.post(
        f"{API}/{sid}", headers=admin.headers,
        json={"kind": "weakness", "text": "Could tighten the closing summary.", "weight": 3},
    ).json()
    strong = client.post(
        f"{API}/{sid}", headers=admin.headers,
        json={"kind": "strength", "text": "Strong analytical and quantitative skills.", "weight": 5},
    ).json()
    assert f"swoc-{light['id']}" not in ids(), "a weight-3 observation became a task"
    assert f"swoc-{strong['id']}" not in ids(), "a strength became something to fix"

    heavy_text = "Needs structured problem-solving practice before the drive."
    heavy = client.post(
        f"{API}/{sid}", headers=admin.headers,
        json={"kind": "weakness", "text": heavy_text, "weight": 5},
    ).json()
    got = action(heavy["id"])
    assert got is not None, "a weight-5 weakness did not reach the student's next actions"
    # The author's own sentence, not a paraphrase — B6.3's rule for the drill.
    assert got["title"] == heavy_text
    assert got["cta_route"] == "/student/mentor-log", "an unlinked line has one home"
    assert "placement cell" in got["reason"], "the viewpoint travels with the line"

    # The link moves the destination, which is the half 04 leaves implicit.
    from app.models.skill import Skill, StudentSkill

    with SessionLocal() as db:
        skill_id = db.scalar(select(Skill.id).limit(1))
        assert skill_id is not None, "no skills catalogue on this database"
        ours = StudentSkill(student_id=sid, skill_id=skill_id)
        db.add(ours)
        db.commit()
        ours_id = ours.id
    assert client.patch(
        f"{API}/entries/{heavy['id']}", headers=admin.headers,
        json={"linked_skill_id": ours_id},
    ).status_code == 200
    assert action(heavy["id"])["cta_route"] == "/student/skilling"

    # Reading it is not doing it.
    assert client.post(f"{MINE}/{heavy['id']}/acknowledge", headers=stu.headers).status_code == 200
    assert action(heavy["id"]) is not None, "an acknowledgement hid the line from the card"

    # Two at most, however many the board carries.
    more = [
        client.post(
            f"{API}/{sid}", headers=admin.headers,
            json={"kind": "weakness", "text": f"Heavy weakness number {n}.", "weight": 5},
        ).json()
        for n in (2, 3)
    ]
    flagged = {i for i in ids() if i.startswith("swoc-")}
    assert len(flagged) == 2, f"one board took over the card: {flagged}"
    assert flagged <= {f"swoc-{e['id']}" for e in [heavy, *more]}

    # The author's own control is what clears it: drop the weight below the bar.
    for entry in [heavy, *more]:
        assert client.patch(
            f"{API}/entries/{entry['id']}", headers=admin.headers, json={"weight": 3}
        ).status_code == 200
    assert not {i for i in ids() if i.startswith("swoc-")}

    with SessionLocal() as db:
        db.execute(delete(StudentSkill).where(StudentSkill.id == ours_id))
        db.commit()


# ------------------------------------------------------------- B7.7 --


@requires_db
def test_the_board_narrows_by_batch_and_search_and_states_its_page(client, make_user, swept):
    """Pagination is OPT-IN and the response stays a bare array.

    A default page size would silently truncate a screen whose whole read model
    is "the list IS the data" and which has no paging control to reach row 51
    with. So `page_size` is what turns it on, and `X-Reep-Total` is stated
    either way — it is what lets a client that asked for no page tell a short
    list from a truncated one.

    Both filters narrow WITHIN the reach and neither can widen it: each is ANDed
    onto `reach.student_ids()`.
    """
    from datetime import datetime, timezone

    from app.models.cohort import Cohort
    from app.models.institution import College, Department
    from app.models.job import DegreeLevel

    admin = make_user("sw4-page-adm", Role.ADMIN)
    tag = uuid.uuid4().hex[:8]
    people = [make_user(f"sw4pg{tag}-{n}", Role.STUDENT) for n in "abc"]
    sids = [_student_id(p.user_id) for p in people]
    swept += sids

    with SessionLocal() as db:
        college = College(code=f"PG{tag[:4].upper()}", name=f"Paging College {tag}")
        db.add(college)
        db.flush()
        dept = Department(college_id=college.id, name=f"Paging {tag}", code=f"PD{tag[:4].upper()}")
        db.add(dept)
        db.flush()
        batch = Cohort(
            code=f"PB-{tag}", name=f"Paging Batch {tag}", batch_label="2026-28",
            degree_level=DegreeLevel.PG, department_id=dept.id,
            start_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end_date=datetime(2028, 7, 31, tzinfo=timezone.utc),
        )
        db.add(batch)
        db.flush()
        cohort_id, dept_id, college_id = batch.id, dept.id, college.id
        db.get(Student, sids[0]).cohort_id = cohort_id
        db.commit()

    def rows(query: str) -> tuple[list[dict], dict]:
        r = client.get(f"{API}?{query}", headers=admin.headers)
        assert r.status_code == 200, r.text
        return r.json(), r.headers

    # `q` matches the name `make_user` gives every one of the three.
    mine, headers = rows(f"q=sw4pg{tag}")
    assert {row["student_id"] for row in mine} == set(sids)
    assert headers["X-Reep-Total"] == "3"
    assert "X-Reep-Page" not in headers, "an un-asked-for page was reported anyway"

    only, headers = rows(f"cohort_id={cohort_id}")
    assert [row["student_id"] for row in only] == [sids[0]]
    assert headers["X-Reep-Total"] == "1"

    # Paging: one row at a time, the same order, no repeats and no gaps.
    seen: list[str] = []
    for page in (1, 2, 3):
        page_rows, headers = rows(f"q=sw4pg{tag}&page={page}&page_size=1")
        assert headers["X-Reep-Total"] == "3", "the total must survive the window"
        assert headers["X-Reep-Page"] == str(page) and headers["X-Reep-Page-Size"] == "1"
        assert len(page_rows) == 1
        seen.append(page_rows[0]["student_id"])
    assert sorted(seen) == sorted(sids), f"a page dropped or repeated a row: {seen}"
    assert rows(f"q=sw4pg{tag}&page=4&page_size=1")[0] == []

    # A filter cannot widen the reach: an id outside it matches nothing.
    empty, headers = rows(f"q=sw4pg{tag}&cohort_id=no-such-cohort")
    assert empty == [] and headers["X-Reep-Total"] == "0"

    with SessionLocal() as db:
        for sid in sids:
            db.get(Student, sid).cohort_id = None
        db.commit()
        db.execute(delete(Cohort).where(Cohort.id == cohort_id))
        db.execute(delete(Department).where(Department.id == dept_id))
        db.execute(delete(College).where(College.id == college_id))
        db.commit()
