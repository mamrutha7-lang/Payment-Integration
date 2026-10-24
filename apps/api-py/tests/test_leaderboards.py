"""The cohort leaderboards -- `GET /api/student/leaderboards?board=` -- and the
two reasons they ranked nobody (2026-09-17).

A student had finished a mock interview, had their skills verified on
Skilling, and had classmates on the roster; the board still read as if no
ranking had been done. Two defects, pinned here:

  * the Skills board counted `student_skills`, a table the Skilling screen's
    verification never writes (a mentor's APPROVE mints an EARNED
    `student_badges` row), and counted its rows verified or not -- so
    "0 skills" against everybody on a live deployment;
  * every board ranked every classmate, at zero, in database order, so the
    ranks under the zeros were arbitrary and changed between cache refreshes,
    and "CGPA 0.00" was drawn against students whose results were never
    recorded.

A student with nothing recorded on a board is not on it now, equal totals
share a rank, and the Skills board counts what the mentor verified. Every
test here hits the seeded dev database like the rest of the suite, so a
cohort is minted per test and the students are `make_user` accounts seated
in it.

THE BOARD IS THE BATCH (2026-09-22). The second half of this module pins the
scope: a batch never sees another batch, the batch mates who hold nothing yet
are listed by name under the ranking rather than left out of the screen, a
student seated in no batch is ranked within the department they named and told
so, a student seated nowhere is told that instead of shown an empty board, and
the `overall` board adds the four single-component boards scaled to the best
in the batch.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from conftest import requires_db
from sqlalchemy import delete, select, update

from app.db import SessionLocal
from app.models.academics import SemesterResult
from app.models.badge import BADGES, StudentBadge, StudentBadgeStatus
from app.models.cohort import Cohort
from app.models.interview import InterviewScoreSummary
from app.models.institution import College, Department
from app.models.job import DegreeLevel
from app.models.user import Student


@pytest.fixture
def cohort(make_user):
    """A batch of its own, and a factory that seats a fresh STUDENT account in it.

    Depends on `make_user` so this fixture tears down FIRST: the students are
    unseated and the batch deleted before `make_user` deletes the accounts,
    which keeps `students.cohort_id` from pointing at a row that is gone.
    """
    tag = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = Cohort(
            code=f"LB-{tag}",
            name="2026-28",
            batch_label="2026-28",
            degree_level=DegreeLevel.PG,
            start_date=now - timedelta(days=60),
            end_date=now + timedelta(days=600),
        )
        db.add(row)
        db.commit()
        cohort_id = row.id

    def _seat(label: str):
        account = make_user(f"lb-{label}-{tag[:4]}")
        with SessionLocal() as db:
            student = db.scalar(select(Student).where(Student.user_id == account.user_id))
            student.cohort_id = cohort_id
            db.commit()
            account.student_id = student.id
        return account

    yield _seat

    with SessionLocal() as db:
        db.execute(update(Student).where(Student.cohort_id == cohort_id).values(cohort_id=None))
        db.execute(delete(Cohort).where(Cohort.id == cohort_id))
        db.commit()


@pytest.fixture
def batches(make_user):
    """A factory of batches, each with its own seating helper.

    `cohort` above is one batch; the scope tests need two, and one of them
    filed under a department. `make_batch(department_id=None)` returns a
    `seat(label)` closure like `cohort`'s, and every batch is unseated and
    deleted before `make_user` tears the accounts down.
    """
    tag = uuid.uuid4().hex[:8]
    made: list[str] = []
    now = datetime.now(timezone.utc)

    def make_batch(department_id: str | None = None, name: str = "2026-28"):
        with SessionLocal() as db:
            row = Cohort(
                code=f"LB-{tag}-{len(made)}",
                name=name,
                batch_label=name,
                degree_level=DegreeLevel.PG,
                department_id=department_id,
                start_date=now - timedelta(days=60),
                end_date=now + timedelta(days=600),
            )
            db.add(row)
            db.commit()
            cohort_id = row.id
        made.append(cohort_id)

        def seat(label: str):
            account = make_user(f"lb-{label}-{tag[:4]}")
            with SessionLocal() as db:
                student = db.scalar(select(Student).where(Student.user_id == account.user_id))
                student.cohort_id = cohort_id
                db.commit()
                account.student_id = student.id
            return account

        seat.cohort_id = cohort_id
        return seat

    yield make_batch

    with SessionLocal() as db:
        db.execute(update(Student).where(Student.cohort_id.in_(made)).values(cohort_id=None))
        db.execute(delete(Cohort).where(Cohort.id.in_(made)))
        db.commit()


@pytest.fixture
def department():
    """A college and a department of its own, deleted afterwards."""
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        college = College(name=f"Leaderboard College {tag}", code=f"LC{tag}")
        db.add(college)
        db.flush()
        dept = Department(college_id=college.id, name=f"Leaderboard Dept {tag}", code=f"LD{tag}")
        db.add(dept)
        db.commit()
        ids = (college.id, dept.id)
    yield ids[1]
    with SessionLocal() as db:
        # Set up after `batches`, so torn down before it: unhook the batch that
        # was filed under this department first, or the FK refuses the delete.
        db.execute(update(Cohort).where(Cohort.department_id == ids[1]).values(department_id=None))
        db.execute(update(Student).where(Student.department_id == ids[1]).values(department_id=None))
        db.execute(delete(Department).where(Department.id == ids[1]))
        db.execute(delete(College).where(College.id == ids[0]))
        db.commit()


def _earn(db, student_id: str, how_many: int) -> None:
    """Exactly what `_award` writes when a mentor approves the claim."""
    for badge in BADGES[:how_many]:
        db.add(
            StudentBadge(
                student_id=student_id,
                badge_code=badge.code,
                status=StudentBadgeStatus.EARNED,
                points_awarded=badge.points,
                earned_at=datetime.now(timezone.utc),
            )
        )


def _board(client, account, board: str) -> dict:
    r = client.get(f"/api/student/leaderboards?board={board}", headers=account.headers)
    assert r.status_code == 200, r.text
    return r.json()


@requires_db
def test_the_skills_board_counts_what_the_mentor_verified(client, cohort):
    """Two verified skills, one, and none: ranks 1 and 2, and a third student
    who is not on the board at all."""
    two, one, none = cohort("two"), cohort("one"), cohort("none")
    with SessionLocal() as db:
        _earn(db, two.student_id, 2)
        _earn(db, one.student_id, 1)
        db.commit()

    body = _board(client, two, "skills")
    assert body["opted_out"] is False
    assert body["cohort_size"] == 2, "the student holding nothing was counted as ranked"
    assert [(r["rank"], r["student_id"], r["value_label"]) for r in body["rows"]] == [
        (1, two.student_id, "2 skills"),
        (2, one.student_id, "1 skill"),
    ]
    assert [r["is_me"] for r in body["rows"]] == [True, False]

    # The student with nothing recorded gets the client's "not ranked here
    # yet" card: the same two rows, none of them theirs, and a size of two.
    body = _board(client, none, "skills")
    assert body["cohort_size"] == 2
    assert not any(r["is_me"] for r in body["rows"])
    assert {r["student_id"] for r in body["rows"]} == {two.student_id, one.student_id}


@requires_db
def test_a_legacy_skill_row_no_longer_ranks_anybody(client, cohort):
    """`student_skills` is what the board used to count; a claim sitting there
    unverified must not put a student above one the mentor actually verified."""
    from app.models.skill import Skill, StudentSkill

    verified, claimed = cohort("verified"), cohort("claimed")
    with SessionLocal() as db:
        _earn(db, verified.student_id, 1)
        skill = db.scalar(select(Skill).limit(1))
        if skill is None:
            skill = Skill(slug=f"lb-{uuid.uuid4().hex[:6]}", name="Leaderboard test", category="test")
            db.add(skill)
            db.flush()
        db.add(StudentSkill(student_id=claimed.student_id, skill_id=skill.id, level=3, verified=False))
        db.commit()

    body = _board(client, claimed, "skills")
    assert [r["student_id"] for r in body["rows"]] == [verified.student_id]
    assert body["cohort_size"] == 1


@requires_db
def test_a_finished_mock_interview_ranks_on_the_mocks_board(client, cohort):
    """One completed interview counts; an abandoned one does not, exactly as on
    the home chart (B6.3); a student with neither is not on the board."""
    finished, quit_early, none = cohort("finished"), cohort("quit"), cohort("none")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.add_all(
            [
                InterviewScoreSummary(
                    student_id=finished.student_id,
                    session_id=None,
                    track_code="hr",
                    started_at=now - timedelta(days=1),
                    status="completed",
                    overall_score=70,
                ),
                InterviewScoreSummary(
                    student_id=quit_early.student_id,
                    session_id=None,
                    track_code="hr",
                    started_at=now - timedelta(days=1),
                    status="abandoned",
                ),
            ]
        )
        db.commit()

    body = _board(client, finished, "mocks")
    assert body["cohort_size"] == 1
    assert [(r["rank"], r["student_id"], r["value_label"], r["is_me"]) for r in body["rows"]] == [
        (1, finished.student_id, "1 mock", True),
    ]
    for bystander in (quit_early, none):
        body = _board(client, bystander, "mocks")
        assert not any(r["is_me"] for r in body["rows"])
        assert body["cohort_size"] == 1


@requires_db
def test_equal_totals_share_a_rank_and_the_next_rank_skips(client, cohort):
    """1, 1, 3 -- and the order inside the tie is by name, so the board is the
    same list on every refresh rather than whatever Postgres returned first."""
    a, b, c = cohort("alpha"), cohort("bravo"), cohort("charlie")
    with SessionLocal() as db:
        _earn(db, a.student_id, 2)
        _earn(db, b.student_id, 2)
        _earn(db, c.student_id, 1)
        db.commit()

    body = _board(client, c, "skills")
    assert [r["rank"] for r in body["rows"]] == [1, 1, 3]
    assert body["cohort_size"] == 3
    tied = [r for r in body["rows"] if r["rank"] == 1]
    assert [r["name"] for r in tied] == sorted((r["name"] for r in tied), key=str.casefold)
    me = next(r for r in body["rows"] if r["is_me"])
    assert me["rank"] == 3, "the student behind a tie is third, not second"


@requires_db
def test_the_vtu_board_never_draws_a_cgpa_for_a_student_with_no_result(client, cohort):
    """A student with no semester result is absent, and a semester filed
    without a CGPA does not hide the last one that had it."""
    scored, blank, none = cohort("scored"), cohort("blank"), cohort("none")
    with SessionLocal() as db:
        db.add_all(
            [
                SemesterResult(student_id=scored.student_id, semester=1, cgpa=7.1),
                SemesterResult(student_id=scored.student_id, semester=2, cgpa=8.25),
                # Semester 3 is on file with no CGPA yet: the latest RECORDED
                # number is semester 2's, not 0.00 and not a crash on None.
                SemesterResult(student_id=scored.student_id, semester=3, cgpa=None),
                SemesterResult(student_id=blank.student_id, semester=1, cgpa=None),
            ]
        )
        db.commit()

    body = _board(client, none, "vtu")
    assert [(r["rank"], r["student_id"], r["value_label"]) for r in body["rows"]] == [
        (1, scored.student_id, "CGPA 8.25"),
    ]
    assert body["cohort_size"] == 1


@requires_db
def test_board_values_leave_out_a_student_with_nothing_recorded(cohort):
    """The contract `_ranked_board` relies on: no confident zeros, for any board."""
    from app.routers.student import _BOARDS, _board_values

    empty = cohort("empty")
    with SessionLocal() as db:
        for board in _BOARDS:
            if board in ("streak", "overall"):
                # Signing in through `make_user` wrote today's login day, so
                # this is the one board a fresh account IS on — and `overall`
                # carries the streak as one of its four components.
                continue
            assert _board_values(db, board, [(empty.student_id, empty.user_id)]) == {}, board


# --- the scope: a batch, and only the batch ----------------------------------


@requires_db
def test_the_board_is_the_batch_and_never_another_batch(client, batches):
    """Two batches, two students each. A student sees their own batch mates
    and nobody from the other batch, however many skills the others hold."""
    seat_a, seat_b = batches(), batches(name="2027-29")
    a1, a2 = seat_a("a1"), seat_a("a2")
    b1 = seat_b("b1")
    with SessionLocal() as db:
        _earn(db, a1.student_id, 1)
        _earn(db, a2.student_id, 2)
        _earn(db, b1.student_id, 5)  # the best in the world, in another batch
        db.commit()

    body = _board(client, a1, "skills")
    assert body["scope"] == "batch"
    # `cohorts.name` IS the year here, so the label is the year alone — the
    # batch-label rule, applied by `batch_labels.compose` and not restated.
    assert body["scope_label"] == "2026-28"
    assert body["classmates"] == 2
    assert [(r["rank"], r["student_id"]) for r in body["rows"]] == [
        (1, a2.student_id),
        (2, a1.student_id),
    ]
    assert b1.student_id not in {r["student_id"] for r in body["rows"]}

    other = _board(client, b1, "skills")
    assert other["scope_label"] == "2027-29"
    assert [r["student_id"] for r in other["rows"]] == [b1.student_id]
    assert other["classmates"] == 1


@requires_db
def test_classmates_without_a_record_are_listed_unranked(client, cohort):
    """The batch mate holding nothing is not RANKED (no confident zero) but is
    ON THE SCREEN, by name, so a fresh batch reads as a batch and not as
    "no ranking yet" over an empty room."""
    ranked, waiting = cohort("ranked"), cohort("waiting")
    with SessionLocal() as db:
        _earn(db, ranked.student_id, 1)
        db.commit()

    body = _board(client, waiting, "skills")
    assert body["classmates"] == 2
    assert body["cohort_size"] == 1, "the unranked classmate must not count as ranked"
    assert [r["student_id"] for r in body["rows"]] == [ranked.student_id]
    assert [(u["student_id"], u["is_me"]) for u in body["unranked"]] == [
        (waiting.student_id, True)
    ]
    assert body["unranked_total"] == 1

    # And from the ranked student's side the same classmate is listed, not me.
    body = _board(client, ranked, "skills")
    assert [(u["student_id"], u["is_me"]) for u in body["unranked"]] == [
        (waiting.student_id, False)
    ]


@requires_db
def test_an_opted_out_classmate_is_on_neither_list(client, cohort):
    """Opting out means leaving the screen entirely — the unranked list is
    still the screen."""
    shown, hidden = cohort("shown"), cohort("hidden")
    r = client.put(
        "/api/student/leaderboard-visibility", json={"hidden": True}, headers=hidden.headers
    )
    assert r.status_code == 200, r.text

    body = _board(client, shown, "skills")
    assert body["classmates"] == 1
    assert hidden.student_id not in {u["student_id"] for u in body["unranked"]}
    assert hidden.student_id not in {r["student_id"] for r in body["rows"]}


@requires_db
def test_a_removed_classmate_leaves_every_board_and_restore_brings_them_back(
    client, cohort, make_user
):
    """REMOVE answers "… is off every screen", and a classmate's leaderboard is
    a screen: the roster left out `users.deleted_at`, so a removed batch mate
    stayed ranked, or listed as not ranked, on every board. Every board is read
    BEFORE the removal as well, so the removal has to reach the cache too — a
    board served from it for another minute is the same name left standing."""
    from app.models.user import Role
    from app.routers.student import _BOARDS

    me, ranked, waiting = cohort("me"), cohort("ranked"), cohort("waiting")
    admin = make_user("lb-office", Role.ADMIN)
    with SessionLocal() as db:
        _earn(db, ranked.student_id, 1)
        db.commit()
    mates = {ranked.student_id, waiting.student_id}

    def listed(board: str) -> tuple[set[str], int]:
        body = _board(client, me, board)
        ids = {r["student_id"] for r in body["rows"]} | {u["student_id"] for u in body["unranked"]}
        return ids, body["classmates"]

    for board in _BOARDS:
        ids, classmates = listed(board)
        assert mates <= ids and classmates == 3, board

    for mate in (ranked, waiting):
        r = client.post(
            f"/api/admin/students/{mate.student_id}/remove",
            headers=admin.headers,
            json={"reason": "Withdrew from the programme"},
        )
        assert r.status_code == 200, r.text
    for board in _BOARDS:
        ids, classmates = listed(board)
        assert not ids & mates, f"a removed classmate is still on the {board} board"
        assert classmates == 1, board

    for mate in (ranked, waiting):
        r = client.post(f"/api/admin/students/{mate.student_id}/restore", headers=admin.headers)
        assert r.status_code == 200, r.text
    for board in _BOARDS:
        ids, classmates = listed(board)
        assert mates <= ids and classmates == 3, f"restore did not bring them back to {board}"


def _listed_everywhere(client, viewer) -> dict[str, set[str]]:
    """Every student on every board the viewer can read, ranked or not."""
    from app.routers.student import _BOARDS

    out = {}
    for board in _BOARDS:
        body = _board(client, viewer, board)
        out[board] = {r["student_id"] for r in body["rows"]} | {
            u["student_id"] for u in body["unranked"]
        }
    return out


@requires_db
def test_a_classmate_deleted_for_good_leaves_the_boards_at_once(client, cohort, make_user):
    """DELETE FOR GOOD reaches the boards' cache as REMOVE does. Every board is
    read first, so a delete that did not clear the cache would leave the name
    standing for another minute, served from a board that no longer has a
    student behind it."""
    from app import mail_transport
    from app.models.user import Role

    from test_account_deletion import _last_code

    me, gone = cohort("me"), cohort("gone")
    admin = make_user("lb-office", Role.ADMIN)
    with SessionLocal() as db:
        _earn(db, gone.student_id, 1)
        db.commit()
    assert all(gone.student_id in ids for ids in _listed_everywhere(client, me).values())

    mail_transport.outbox.clear()
    assert client.post("/api/admin/deletions/code", headers=admin.headers).status_code == 200
    r = client.post(
        f"/api/admin/students/{gone.student_id}/delete",
        headers=admin.headers,
        json={"code": _last_code(), "reason": "Left the college"},
    )
    assert r.status_code == 200, r.text
    for board, ids in _listed_everywhere(client, me).items():
        assert gone.student_id not in ids, f"a deleted classmate is still on the {board} board"


@requires_db
def test_a_removed_graduate_leaves_the_boards_at_once(client, cohort, make_user):
    """A graduate is ALUMNI and keeps their `students` row and batch, so the
    boards still list them; removing one must reach the cache like removing a
    student does. Deciding that by the ROLE left their name up for a minute."""
    from app.models.user import Role, User

    me, graduate = cohort("me"), cohort("graduate")
    admin = make_user("lb-office", Role.ADMIN)
    with SessionLocal() as db:
        db.execute(update(User).where(User.id == graduate.user_id).values(role=Role.ALUMNI))
        db.commit()
    assert all(graduate.student_id in ids for ids in _listed_everywhere(client, me).values())

    r = client.post(
        f"/api/admin/users/{graduate.user_id}/remove",
        headers=admin.headers,
        json={"reason": "Asked to be taken off the records"},
    )
    assert r.status_code == 200, r.text
    for board, ids in _listed_everywhere(client, me).items():
        assert graduate.student_id not in ids, f"a removed graduate is still on the {board} board"

    r = client.post(f"/api/admin/users/{graduate.user_id}/restore", headers=admin.headers)
    assert r.status_code == 200, r.text
    for board, ids in _listed_everywhere(client, me).items():
        assert graduate.student_id in ids, f"restore did not bring the graduate back to {board}"


@requires_db
def test_an_unseated_student_is_ranked_within_their_department(client, batches, department):
    """No batch yet, but a department named on the form: the board is the
    department — the seated students of its batches AND the unseated ones —
    and the response says so, because the fix is the office seating them."""
    seat = batches(department_id=department)
    seated = seat("seated")
    unseated = seat("unseated")
    with SessionLocal() as db:
        row = db.get(Student, unseated.student_id)
        row.cohort_id = None
        row.department_id = department
        _earn(db, seated.student_id, 2)
        _earn(db, unseated.student_id, 1)
        db.commit()

    body = _board(client, unseated, "skills")
    assert body["scope"] == "department"
    assert body["scope_label"].startswith("Leaderboard Dept")
    assert body["classmates"] == 2
    assert [(r["rank"], r["student_id"]) for r in body["rows"]] == [
        (1, seated.student_id),
        (2, unseated.student_id),
    ]

    # The seated student's own board is still the BATCH, and it is one person.
    body = _board(client, seated, "skills")
    assert body["scope"] == "batch"
    assert body["classmates"] == 1
    assert [r["student_id"] for r in body["rows"]] == [seated.student_id]


@requires_db
def test_a_student_seated_nowhere_is_told_so(client, make_user):
    """Neither a batch nor a department: `scope: none`, nobody listed. It used
    to rank every NULL-cohort student on the deployment against each other,
    which is not a batch and drew a board that looked like one."""
    nobody = make_user("lb-nowhere")
    body = _board(client, nobody, "overall")
    assert body["scope"] == "none"
    assert body["scope_label"] is None
    assert body["classmates"] == 0
    assert body["cohort_size"] == 0
    assert body["rows"] == [] and body["unranked"] == []


# --- the overall board ------------------------------------------------------


def test_overall_points_scale_each_component_to_the_best_and_round_half_up():
    """Pure arithmetic, no database: the best on a component takes the full 25,
    the rest a share of it; the components add; .5 rounds up; a student on no
    component is absent."""
    from app.routers.student import OVERALL_POINTS_PER_COMPONENT, overall_points

    assert OVERALL_POINTS_PER_COMPONENT == 25
    points = overall_points(
        {
            "skills": {"a": 2.0, "b": 1.0},
            "vtu": {"a": 8.0, "b": 8.0},
            "streak": {},
            "mocks": {"c": 3.0},
        }
    )
    # a: 25 + 25; b: 12.5 + 25 = 37.5 -> 38; c: 25 on mocks alone.
    assert points == {"a": 50, "b": 38, "c": 25}
    assert overall_points({"skills": {}, "vtu": {}}) == {}


@requires_db
def test_the_overall_board_adds_the_four_components(client, cohort):
    """Skills and results set apart; the sign-in `make_user` performed puts every
    one of the three on the streak board at the same one day, so each collects
    the same 25 there — which is also why a student with nothing else is
    still on the overall board, at 25."""
    top, mid, low = cohort("top"), cohort("mid"), cohort("low")
    with SessionLocal() as db:
        _earn(db, top.student_id, 2)
        _earn(db, mid.student_id, 1)
        db.add(SemesterResult(student_id=top.student_id, semester=1, cgpa=8.0))
        db.add(SemesterResult(student_id=mid.student_id, semester=1, cgpa=8.0))
        db.commit()

    body = _board(client, low, "overall")
    assert body["cohort_size"] == 3
    assert [(r["rank"], r["student_id"], r["value_label"]) for r in body["rows"]] == [
        (1, top.student_id, "75 pts"),
        (2, mid.student_id, "63 pts"),
        (3, low.student_id, "25 pts"),
    ]
    assert body["unranked"] == []
