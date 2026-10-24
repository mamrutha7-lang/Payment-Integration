"""B2.2 — the student feature switches decide something.

Between 2026-08 and this module the ten switches on the Governance screen were
recorded, audited, shown with a reason and a target count, and INERT:
`governance.feature_enabled()` and `features_for()` were written, correct and
covered by `tests/test_governance.py`, and nothing called either. The office
could switch the mock interviewer off for a whole cohort and watch that cohort
use it all afternoon. A switch wired to nothing is worse than a missing one,
because somebody trusted it.

What is pinned here is therefore not the resolution — `test_governance.py` owns
that — but the three things that make a switch REAL:

  * the routers ask (every feature, at its own screen, with the office's own
    words in the refusal);
  * `GET /api/auth/me` tells a student's client which of them are off, so the
    nav can grey the item instead of letting them walk into a 403;
  * the catalogue admits which keys are enforced, and `PUT /features` REFUSES
    to set one that is not — the console must never offer a switch that does
    nothing, which is the state this task ended.
"""

from __future__ import annotations

import pathlib
import uuid

import pytest
from conftest import requires_db

from app.db import SessionLocal
from app.governance import (
    FEATURE_DISABLED_DEFAULT_MESSAGE,
    FEATURE_DISABLED_HEADER,
    feature_state,
    feature_states_for,
    features_for,
)
from app.models.governance import FEATURES, FEATURES_BY_KEY, Feature, FeatureOverride, ScopeLevel
from app.models.user import Role, Student

GOV = "/api/admin/governance"
REASON = "Switched off for this cohort while the placement drive is running."
MESSAGE = "Your placement cell has paused this until the December drive ends."

#: Every feature, and the endpoint a student reaches its screen through. The
#: table is the test: a feature added to the catalogue with no row here fails
#: `test_every_feature_in_the_catalogue_is_covered_by_this_module`, so the next
#: person cannot add a switch and leave it untested.
SCREEN_OF = {
    "student.jobs": ("GET", "/api/student/jobs"),
    "student.leaderboards": ("GET", "/api/student/leaderboards"),
    "student.uploads": ("GET", "/api/student/uploads"),
    "student.english": ("GET", "/api/student/english-baseline"),
    "student.time_log": ("GET", "/api/student/ledger"),
    "student.skilling": ("GET", "/api/student/badges"),
    "student.mentor_log": ("GET", "/api/student/mentor-meetings"),
    "student.agent": ("POST", "/api/agent/ask"),
    "student.resume": ("POST", "/api/student/resume/generate"),
    # The interviewer is a WebSocket, so its screen is the probe the client
    # asks first; `test_the_interview_probe_...` covers the 200-with-a-reason
    # shape that endpoint deliberately answers with instead of a 403.
    "student.assistant": ("GET", "/api/interview/status"),
}

_BODY = {
    "/api/agent/ask": {"message": "What is my attendance?"},
    "/api/student/resume/generate": {"title": "Test", "target_role": "Analyst"},
}


@pytest.fixture
def student(make_user):
    """A STUDENT account plus its `students.id`, which is what an override names."""
    account = make_user(f"feat-{uuid.uuid4().hex[:4]}")
    with SessionLocal() as db:
        account.student_id = db.query(Student).filter(Student.user_id == account.user_id).one().id
    return account


@pytest.fixture
def switch_off():
    """Switch a feature off for one student, and take the row away afterwards.

    A leaked override outlives the `make_user` account that named it and would
    silently switch a feature off for whatever id Postgres hands out next.
    """
    made: list[str] = []

    def _off(student_id: str, feature: str, message: str | None = MESSAGE) -> str:
        with SessionLocal() as db:
            row = FeatureOverride(
                feature=feature, scope=ScopeLevel.STUDENT, target_id=student_id,
                enabled=False, reason=REASON, student_message=message,
            )
            db.add(row)
            db.commit()
            made.append(row.id)
            return row.id

    yield _off

    if made:
        with SessionLocal() as db:
            for oid in made:
                row = db.get(FeatureOverride, oid)
                if row is not None:
                    db.delete(row)
            db.commit()


def _call(client, account, feature: str):
    method, path = SCREEN_OF[feature]
    if method == "GET":
        return client.get(path, headers=account.headers)
    return client.post(path, headers=account.headers, json=_BODY[path])


# --------------------------------------------------------------------------- #
# The routers ask
# --------------------------------------------------------------------------- #

@requires_db
@pytest.mark.parametrize("feature", [k for k in SCREEN_OF if k != "student.assistant"])
def test_every_switched_off_feature_refuses_at_its_own_screen(
    client, student, switch_off, feature
):
    """Switch a feature off for one student and their screen answers 403.

    Parametrised over the whole catalogue rather than written once for jobs,
    because the failure this module exists to prevent is PER FEATURE: nine
    gates wired and one forgotten looks exactly like a working system until the
    day the office switches off the tenth.

    DELETE THIS and a switch goes back to being a row with a reason and an audit
    trail that changes nothing a student sees — the state B2.2 was written to
    end, which stood undetected for a month.
    """
    ok = _call(client, student, feature)
    assert ok.status_code != 403, (
        f"{feature} refused BEFORE it was switched off: {ok.text}"
    )

    switch_off(student.student_id, feature)

    r = _call(client, student, feature)
    assert r.status_code == 403, f"{feature} is still reachable while switched off: {r.text}"


@requires_db
def test_the_refusal_carries_the_offices_own_words_and_never_its_private_reason(
    client, student, switch_off
):
    """`student_message` reaches the student; `reason` never does.

    They are two different sentences on purpose (models/governance.py says so):
    `reason` is the office's note to itself — "withheld pending the disciplinary
    meeting" is a true reason and not something to put on a student's screen —
    and `student_message` is the only half written to be read by them.

    DELETE THIS and the obvious refactor is to print `reason`, which publishes
    the office's internal note to the person it is about.
    """
    switch_off(student.student_id, "student.jobs")
    r = client.get("/api/student/jobs", headers=student.headers)
    assert r.status_code == 403
    assert r.json()["detail"] == MESSAGE
    assert REASON not in r.text, "the office's private reason reached the student"


@requires_db
def test_a_switch_with_no_message_still_says_something_true(client, student, switch_off):
    """No `student_message` is a real choice, and it must not print an empty box.

    Null means the office switched it off and chose not to explain. The refusal
    then falls back to a sentence that says what happened without inventing a
    reason nobody gave.

    DELETE THIS and a null message renders as `null` or an empty 403 body, and
    the student reads "the app is broken" — the outcome the column was added to
    prevent.
    """
    switch_off(student.student_id, "student.uploads", message=None)
    r = client.get("/api/student/uploads", headers=student.headers)
    assert r.status_code == 403
    assert r.json()["detail"] == FEATURE_DISABLED_DEFAULT_MESSAGE


@requires_db
def test_the_refusal_names_the_feature_in_a_header(client, student, switch_off):
    """A client can tell "switched off for you" from every other 403.

    `detail` is what the screens print, so it carries the office's sentence and
    nothing machine-readable. The key travels in a header instead — the same
    idiom as `X-Reep-Session: retired`, and for the same reason: the body stays
    human, the machine reads a header.

    DELETE THIS and a client that wants to grey out a nav item has to pattern
    match on English prose, which breaks the first time somebody rewords it.
    """
    switch_off(student.student_id, "student.time_log")
    r = client.get("/api/student/ledger", headers=student.headers)
    assert r.status_code == 403
    assert r.headers.get(FEATURE_DISABLED_HEADER) == "student.time_log"


@requires_db
def test_switching_one_feature_off_leaves_the_other_screens_alone(
    client, student, switch_off
):
    """The gate is per feature, not a blanket refusal of the student surface.

    Each `require_feature` call names its own key, and a copy-paste that names
    the wrong one would take down a screen nobody switched off — silently, for
    the students under that rule only.

    DELETE THIS and the day somebody pastes `student.jobs` into the badges
    handler, the suite stays green and a cohort loses its Skilling screen.
    """
    switch_off(student.student_id, "student.jobs")
    assert client.get("/api/student/jobs", headers=student.headers).status_code == 403
    for other in ("/api/student/badges", "/api/student/ledger", "/api/student/uploads"):
        r = client.get(other, headers=student.headers)
        assert r.status_code != 403, f"{other} was caught by the jobs switch: {r.text}"


@requires_db
def test_a_feature_that_is_on_answers_exactly_what_it_answered_before(client, student):
    """The gate is a refusal on the way in, never a change to the success shape.

    B2.2 touched twenty-odd handlers. A gate that also reshaped the 200 — an
    envelope, a wrapper, a new key — would be a redesign of the whole student
    API smuggled in behind an access-control task.

    DELETE THIS and the next gate someone adds is free to return
    `{"enabled": true, "data": [...]}` and break every screen at once.
    """
    r = client.get("/api/student/jobs", headers=student.headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list), "the jobs feed is still a bare list"

    r = client.get("/api/student/ledger", headers=student.headers)
    assert r.status_code == 200
    assert "cells" in r.json() or "slots" in r.json(), "the ledger day is unchanged"


@requires_db
def test_a_student_can_still_take_their_own_data_out_of_a_switched_off_feature(
    client, student, switch_off
):
    """Off means "not from here, for now" — not confiscation.

    Two controls stay open with the feature off, and they are the same kind:
    opting out of a leaderboard, and deleting a document you uploaded. A student
    who cannot reach the screen cannot be asked to turn the setting off on it
    first, and the office switching a screen off did not decide to keep their
    certificate.

    DELETE THIS and the tidy-looking refactor — "gate every endpoint of the
    feature" — quietly takes away a privacy control and a delete button.
    """
    switch_off(student.student_id, "student.leaderboards")
    r = client.put(
        "/api/student/leaderboard-visibility",
        headers=student.headers,
        json={"hidden": True},
    )
    assert r.status_code in (200, 204), f"opting out broke with the boards off: {r.text}"

    # And the per-file door out of Uploads. 404 (no such upload) rather than 403
    # is the whole assertion: the gate is not on this route, so a student can
    # still fetch a certificate they filed in March.
    switch_off(student.student_id, "student.uploads")
    assert client.get("/api/student/uploads", headers=student.headers).status_code == 403
    gone = client.get(f"/api/student/uploads/{uuid.uuid4().hex}/file", headers=student.headers)
    assert gone.status_code == 404, f"the file door was gated too: {gone.status_code}"


# --------------------------------------------------------------------------- #
# The interviewer, which refuses differently on purpose
# --------------------------------------------------------------------------- #

@requires_db
def test_the_interview_probe_reports_the_switch_as_a_reason_not_a_403(
    client, student, switch_off
):
    """`GET /api/interview/status` answers 200-with-a-reason, and now says this one.

    The interview client treats ANY non-2xx from this probe as "probe
    unavailable" and falls through to the socket, so a 403 here would throw away
    the explanation the endpoint exists to give. The switch is asked BEFORE
    "is the engine configured", so a student whose office switched them off
    reads the office's sentence and not "Voice service not configured", which is
    an operator's problem and is not even true for them.

    DELETE THIS and the obvious tidy-up — make every feature refusal a 403 —
    turns the one screen that explains itself into an opaque 1006.
    """
    switch_off(student.student_id, "student.assistant")
    r = client.get("/api/interview/status", headers=student.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is False
    assert body["reason"] == MESSAGE, "the probe reported the engine instead of the switch"


@requires_db
def test_the_socket_reads_the_same_switch_and_closes_on_a_code_of_its_own(
    student, switch_off
):
    """The WebSocket refusal, as far as this suite can drive it.

    Nothing in this repository opens a real `/api/interview` socket in a test —
    the engines are the reason — so what is pinned is the two pieces the close
    is built from: the reader the handler calls on a worker thread, and the fact
    that 4016 is ITS OWN code. Sharing 1008 with "not a student" would make the
    client print its generic "you are not allowed here" over the sentence the
    office wrote, which is the whole point of `student_message`.

    DELETE THIS and `_assistant_switch` looks like dead code to the next person
    tidying the module, and the private-use code can be collapsed into 1008 with
    nothing objecting.
    """
    from app.interview_core import (
        _CLOSE_CONSENT_REQUIRED,
        _CLOSE_DAILY_CAP,
        _CLOSE_USER_SESSION_CAP,
    )
    from app.routers.interview import _CLOSE_FEATURE_DISABLED, _CLOSE_NOT_A_STUDENT, _assistant_switch

    assert _CLOSE_FEATURE_DISABLED not in {
        _CLOSE_NOT_A_STUDENT, _CLOSE_CONSENT_REQUIRED, _CLOSE_USER_SESSION_CAP, _CLOSE_DAILY_CAP,
    }

    assert _assistant_switch(student.student_id).enabled is True
    switch_off(student.student_id, "student.assistant")
    state = _assistant_switch(student.student_id)
    assert state.enabled is False and state.message == MESSAGE


# --------------------------------------------------------------------------- #
# GET /api/auth/me
# --------------------------------------------------------------------------- #

@requires_db
def test_me_tells_a_student_which_of_their_features_are_off(client, student, switch_off):
    """The client can grey the nav item instead of letting them walk into a 403.

    Dense on purpose — every key, with `enabled` — because features are ALLOW by
    default and a client reading an absent key as "off" would hide the whole
    product the first time this map was trimmed.

    DELETE THIS and the switch works but nothing on screen says so: the student
    clicks Jobs, gets a refusal they could not have predicted, and files a bug.
    """
    r = client.get("/api/auth/me", headers=student.headers)
    assert r.status_code == 200, r.text
    features = r.json()["features"]
    assert set(features) == set(FEATURES_BY_KEY), "every feature is reported, not only the off ones"
    assert all(f["enabled"] for f in features.values()), "a fresh student has everything"

    switch_off(student.student_id, "student.skilling")
    features = client.get("/api/auth/me", headers=student.headers).json()["features"]
    assert features["student.skilling"] == {"enabled": False, "message": MESSAGE}
    assert features["student.jobs"]["enabled"] is True
    assert features["student.jobs"]["message"] is None, (
        "a message on a feature somebody can use is a sentence with nowhere to go"
    )


@requires_db
def test_me_reports_no_features_for_staff(client, make_user):
    """A feature override is a statement about a STUDENT, so staff get `{}`.

    Resolving an ancestry for an id that is not in `students` would hand a
    faculty client eleven meaningless `true`s that it could learn to read as
    permission.

    DELETE THIS and the map silently becomes "eleven trues for everyone", which
    is indistinguishable from a working answer until somebody switches a feature
    off and a mentor's screen does not change.
    """
    staff = make_user(f"feat-staff-{uuid.uuid4().hex[:4]}", Role.MENTOR)
    body = client.get("/api/auth/me", headers=staff.headers).json()
    assert body["features"] == {}
    assert body["capabilities"], "the capability half of /me still answers for staff"


# --------------------------------------------------------------------------- #
# The catalogue is honest about what it enforces
# --------------------------------------------------------------------------- #

#: Everything under `app/` except the two modules that DEFINE the keys. A key
#: appearing only in its own catalogue entry and its own resolver is precisely
#: the inert state B2.2 ended.
_APP = pathlib.Path(__file__).resolve().parents[1] / "app"
_DEFINING_MODULES = {"governance.py"}


def _call_sites(key: str) -> list[str]:
    """Modules that both NAME the key and ASK the question.

    Both halves, because either alone lies. A module naming the key in a comment
    is not enforcement; a module calling `require_feature` on some OTHER key does
    not enforce this one. It is a grep rather than an AST walk, in the same
    spirit as `tools/ci/check_pii_gate.py` — it cannot prove the two are in the
    same statement, and it does not have to: what it catches is a key with no
    enforcing module at all, which is the state every one of these was in.
    """
    hits = []
    for path in _APP.rglob("*.py"):
        if path.name in _DEFINING_MODULES and path.parent.name in {"app", "models"}:
            continue
        source = path.read_text(encoding="utf-8")
        if f'"{key}"' in source and ("require_feature" in source or "feature_state" in source):
            hits.append(str(path.relative_to(_APP)))
    return hits


def test_a_feature_marked_enforced_is_actually_asked_about_somewhere() -> None:
    """`enforced=True` is a claim, and this is what makes it one you must back up.

    The flag is what the console renders its Enforcement column from and what
    `PUT /features` refuses on. If it can be set without a call site, it is the
    same lie the whole task removed — only now with a green tick next to it.

    DELETE THIS and somebody adds `Feature("student.mentoring", ..., enforced=True)`
    on a Friday, the screen shows a working switch, and no router ever asks.
    """
    for feature in FEATURES:
        sites = _call_sites(feature.key)
        if feature.enforced:
            assert sites, (
                f"{feature.key} claims enforced=True but no module under app/ names it — "
                "wire it at its endpoint or set enforced=False"
            )
        else:
            assert not sites, (
                f"{feature.key} says enforced=False but {sites} names it — "
                "the console is telling the office this switch does nothing"
            )


def test_every_feature_in_the_catalogue_is_covered_by_this_module() -> None:
    """A new switch cannot be added without deciding where it is enforced.

    DELETE THIS and the parametrised refusal test above silently stops covering
    the catalogue: it iterates a table in this file, not the catalogue itself.
    """
    assert set(SCREEN_OF) == set(FEATURES_BY_KEY), (
        "SCREEN_OF and FEATURES disagree — add the new feature's screen here "
        f"(missing: {set(FEATURES_BY_KEY) - set(SCREEN_OF)}, "
        f"stale: {set(SCREEN_OF) - set(FEATURES_BY_KEY)})"
    )


@requires_db
def test_the_catalogue_reports_enforced_per_feature(client, make_user, monkeypatch):
    """The screen reads `enforced` from the server, never from its own list.

    The Phase 2 switches screen carried a hand-audited constant of which keys
    the API really checked, with a comment saying a mirror of the server's
    behaviour is right only on the day it is written. This is what lets that
    constant be deleted.

    An unwired feature is PUT INTO THE CATALOGUE for the second half, because
    every real key is enforced today and a handler that simply hard-coded `True`
    would be indistinguishable from one that reads the flag. That was not
    hypothetical: written the obvious way first, this test stayed green through
    exactly that mutation.

    DELETE THIS and the field can quietly stop being served while the client
    keeps rendering `undefined` as "not wired".
    """
    from app.routers import governance as gov_router

    admin = make_user(f"feat-adm-{uuid.uuid4().hex[:4]}", Role.ADMIN)
    r = client.get(f"{GOV}/catalogue", headers=admin.headers)
    assert r.status_code == 200, r.text
    served = {f["key"]: f["enforced"] for f in r.json()["features"]}
    assert served == {f.key: f.enforced for f in FEATURES}

    unwired = Feature("student.not_wired_yet", "Not wired yet", enforced=False)
    monkeypatch.setattr(gov_router, "FEATURES", (*FEATURES, unwired))
    served = {f["key"]: f["enforced"] for f in client.get(
        f"{GOV}/catalogue", headers=admin.headers
    ).json()["features"]}
    assert served[unwired.key] is False, "the catalogue reported a flag it had not read"


@requires_db
def test_an_unwired_feature_cannot_be_switched_at_all(client, make_user, student):
    """422: a rule that would be recorded and ignored is refused, in both directions.

    This is the honest half of B2.2. The console must not offer a switch that
    does nothing — and if a key ever is added ahead of its wiring, saying so at
    the write is the only thing that stops an admin acting on a promise the API
    does not keep. Refused for `enabled=True` too: on an unwired key that row is
    equally inert, and it is indistinguishable on screen from a rule that works.

    422 and not 403 because the caller holds the capability and the request is
    well formed; it is the FEATURE that cannot take a rule yet. A 403 would send
    the admin hunting for a grant that would change nothing.

    DELETE THIS and the refusal can be dropped as "unreachable — everything is
    wired today", which is true until the next feature is added.
    """
    admin = make_user(f"feat-adm-{uuid.uuid4().hex[:4]}", Role.ADMIN)
    key = f"student.unwired_{uuid.uuid4().hex[:6]}"
    # The catalogue is code, so an unwired feature can only be simulated by
    # putting one in it. The router holds a reference to this same dict.
    FEATURES_BY_KEY[key] = Feature(key, "Not wired yet", enforced=False)
    try:
        for enabled in (False, True):
            r = client.put(
                f"{GOV}/features",
                headers=admin.headers,
                json={
                    "feature": key, "scope": "STUDENT", "target_id": student.student_id,
                    "enabled": enabled, "reason": REASON,
                },
            )
            assert r.status_code == 422, (
                f"an unwired feature accepted enabled={enabled}: {r.status_code} {r.text}"
            )
            assert "not wired" in r.json()["detail"].lower()
    finally:
        FEATURES_BY_KEY.pop(key, None)
        # Cleared even when the assertions above FAILED, which is the case that
        # matters: a run where the refusal is missing is exactly the run that
        # writes the row, and a leaked override outlives the throwaway student
        # it named.
        with SessionLocal() as db:
            written = db.query(FeatureOverride).filter(FeatureOverride.feature == key).all()
            leaked = len(written)
            for row in written:
                db.delete(row)
            db.commit()

    assert leaked == 0, "the refusal did not stop the row being written"


@requires_db
def test_the_console_writes_the_message_the_student_reads(client, make_user, student):
    """`PUT /features` carries `student_message` end to end, or the column is furniture.

    The column existed before this task and no endpoint accepted it, so every
    refusal would have printed the generic fallback and the office would have had
    no way to say why. This walks the real path: admin sets the message, student
    hits the screen, reads those exact words.

    DELETE THIS and `student_message` can be dropped from the request model by
    anybody tidying it, with nothing failing.
    """
    admin = make_user(f"feat-adm-{uuid.uuid4().hex[:4]}", Role.ADMIN)
    words = "Paused for your batch until results are published."
    r = client.put(
        f"{GOV}/features",
        headers=admin.headers,
        json={
            "feature": "student.time_log", "scope": "STUDENT",
            "target_id": student.student_id, "enabled": False,
            "reason": REASON, "student_message": words,
        },
    )
    assert r.status_code == 200, r.text
    override_id = r.json()["id"]
    assert r.json()["student_message"] == words
    assert r.json()["feature_enforced"] is True
    try:
        refusal = client.get("/api/student/ledger", headers=student.headers)
        assert refusal.status_code == 403
        assert refusal.json()["detail"] == words
    finally:
        client.delete(f"{GOV}/features/{override_id}", headers=admin.headers)

    # And clearing the rule gives the screen back, so an admin can undo.
    assert client.get("/api/student/ledger", headers=student.headers).status_code == 200


# --------------------------------------------------------------------------- #
# The resolver's own shape
# --------------------------------------------------------------------------- #

@requires_db
def test_the_message_only_travels_with_a_refusal(student, switch_off):
    """A message on a feature somebody can use is a sentence with nowhere to go.

    `FeatureState.message` is populated only when `enabled` is false, so no
    client can render "switched off" beside a working screen by reading a
    leftover message off an `enabled=True` override.

    DELETE THIS and the natural refactor — "always return the winning row's
    message" — starts printing an old explanation next to a screen that works.
    """
    with SessionLocal() as db:
        on = feature_state(db, student.student_id, "student.jobs")
        assert on.enabled is True and on.message is None

    # The case the mutation actually hides in: a row that is present, carries a
    # message, and says ON — which is how an admin re-enables one student inside
    # a switched-off cohort. Asserting only on "no row at all" left the refactor
    # above passing.
    with SessionLocal() as db:
        back_on = FeatureOverride(
            feature="student.jobs", scope=ScopeLevel.STUDENT, target_id=student.student_id,
            enabled=True, reason=REASON, student_message="stale words from when it was off",
        )
        db.add(back_on)
        db.commit()
        row_id = back_on.id
    try:
        with SessionLocal() as db:
            state = feature_state(db, student.student_id, "student.jobs")
            assert state.enabled is True
            assert state.message is None, "a working screen was handed a switched-off message"
    finally:
        with SessionLocal() as db:
            row = db.get(FeatureOverride, row_id)
            if row is not None:
                db.delete(row)
                db.commit()

    switch_off(student.student_id, "student.jobs")
    with SessionLocal() as db:
        off = feature_state(db, student.student_id, "student.jobs")
        assert off.enabled is False and off.message == MESSAGE
        # And the three views of one resolution agree with each other.
        assert features_for(db, student.student_id)["student.jobs"] is False
        assert feature_states_for(db, student.student_id)["student.jobs"].message == MESSAGE
