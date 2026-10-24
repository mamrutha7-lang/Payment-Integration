# Institutional spine — build log

> **A CHRONOLOGICAL LOG. Each round describes the code as it stood the day that
> round was written, and a later round supersedes an earlier one — which is the
> point of keeping it, and also the trap.** Round 9 is *"DIRECTOR is not a role"*;
> every mention of `require_director`, a DIRECTOR-gated endpoint or a
> `director@bgscet.ac.in` account in the rounds BEFORE it is history, not a
> description of the product. `require_admin` is the one console gate now.
> Where this log and `AGENTS.md` disagree, `AGENTS.md` is current.
>

Every file changed in this workstream is recorded here **before** it is written, with the reasoning
that produced the change. The purpose is that a developer who has never seen this work can read one
file, understand why each decision went the way it did, and — more importantly — see which
alternatives were rejected and why, so they are not re-proposed six months from now.

**Format for every entry:** the file, the layer, what changed, then a numbered chain of reasoning
ending in the decision. Rejected alternatives are recorded, not omitted.

---

## Development layers

Work is divided into four layers. A change belongs to exactly one, and the layer decides who reviews
it, what breaks if it is wrong, and how it is verified.

| Layer | Directory | What lives here | Verified by |
|---|---|---|---|
| **L1 · Data** | `apps/api-py/app/models/`, `apps/api-py/migrations/` | ORM models, columns, constraints, Alembic revisions | `alembic upgrade head` on a scratch database |
| **L2 · Backend** | `apps/api-py/app/routers/`, `app/*.py` services | Endpoints, request/response schemas, business rules | `pytest` |
| **L3 · Frontend** | `apps/web/src/app/` | Angular screens, services, routes | `npx ng build` (including the bundle budget) |
| **L4 · Infrastructure** | `infra/cdk/`, `.github/workflows/` | AWS resources, deploy pipeline, backup and recovery | `cdk diff` |

Layers are built bottom-up. A frontend screen is never built against an endpoint that does not exist,
and an endpoint is never built against a column that does not exist. This is the whole reason the
locked profile card in the design cannot be delivered today: it is an L3 screen resting on an L1 gap.

---

## Naming conventions

These are **not new rules**. They are the conventions already in force in this repository, written
down so the new code is indistinguishable from the old. Every example below is real code from the
existing tree. *(Refreshed 2026-10-08: the L2 and L3 examples named `director.py`
and `features/director/`, which Round 9 renamed to `console.py` and `features/admin/`;
and the L0 table was added, because three layers of code conventions never said what
the people are called — which is how a poster read "DIRECTOR / ADMIN" and "FACULTY /
MENTOR" for a month after DIRECTOR stopped being a role.)*

### L0 · People — the words on screen and the roles behind them

| On screen | Stored role (`users.role`) | In the code | What it is |
|---|---|---|---|
| **Student** | `STUDENT` | `/student/*`, `app/routers/student.py`, a `students` row, the session's `studentId` | A current student. The roster is the access control. |
| **Faculty** | `MENTOR` | `/mentor/*`, `app/routers/mentor.py`, `require_mentor`, `features/admin/faculty/` | A faculty account. **Not a mentor by existing** — no `mentors` row, sees nobody, until the Main Admin assigns it a student. |
| a student's **mentor** | `MENTOR` **plus a `mentors` row** | `students.mentor_id` → `mentors.id`, the session's `mentorId`, `app/mentor_functions.py` | The faculty member assigned to that student. "Your mentor" on the student's screens; the `Mentor` group row is what rule 2 scopes on. |
| **Alumni** | `ALUMNI` | `/alumni/*`, `app/routers/alumni.py` | A graduate: no `students` row, no `mentors` row, no staff scope. |
| **Main Admin** — the placement office | `ADMIN` | `/admin/*`, `app/routers/console.py` and `admin_*.py`, `require_admin`, `features/admin/`, the login's dashed "Main Admin" door | **One account per deployment.** "Placement office", "placement cell" and "TPO" on the screens all mean this account; its SWOC lines are stamped `SwocSource.PLACEMENT`, the placement cell's viewpoint. |

Three rules, the same three `AGENTS.md` "Who is who" states: *mentor* is a stored
value and a relationship, never an account kind (the account is Faculty); the
placement office is the Main Admin, role `ADMIN`, exactly one, and DIRECTOR is not a
role (Round 9); and a diagram draws the pair on-screen-name first — "Faculty (role
MENTOR)", "Main Admin (role ADMIN)".

### L1 · Data

| Thing | Convention | Real example |
|---|---|---|
| Model class | `PascalCase`, **singular** | `Cohort`, `AlertRuleConfig`, `AlumniProfile` |
| Table name | `snake_case`, **plural** | `cohorts`, `alert_rule_configs`, `alumni_profiles` |
| Primary key | `id`, `String`, `default=_uuid` | every model |
| Foreign key column | `<singular>_id` | `student.mentor_id`, `department.college_id` |
| Enum-ish vocabulary | plain `String`, **never a PG enum**, unless one already exists | `voice_platform.degree_level: String(2)` |
| Migration file | `<12-hex-revision>_<snake_slug>.py` | `fea4515cdba5_cohorts.py` |

**On PG enums.** `app/models/voice_platform.py` states the rule and the reason: a new vocabulary value
should be *"a data change, not a `CREATE TYPE` migration with all three of AGENTS.md's enum gotchas
attached."* New status columns in this workstream are plain `String`.

### L2 · Backend

| Thing | Convention | Real example |
|---|---|---|
| Response schema | `<Thing>Out` | `MentorLoadOut`, `ProfileOut`, `CohortOut` |
| Request schema | `<Thing>In` or `<Verb><Thing>In` | `AlertRuleIn`, `AssignMentorIn` |
| Endpoint function | verb-first `snake_case` | `set_student_mentor`, `upsert_alert_rule`, `create_job` |
| Router module | `app/routers/<area>.py` | `console.py`, `admin_mentoring.py`, `student_programme.py` |
| Scope gate | reuse, never reimplement | `assert_student_scope` in `app/policies.py` |

### L3 · Frontend

| Thing | Convention | Real example |
|---|---|---|
| Directory | `features/<area>/<screen>/` | `features/admin/mentors-students/` |
| Files | `<screen>.component.{ts,html,scss}` | `mentors-students.component.ts` |
| Component class | `<Area><Screen>Component` | `AdminMentorsStudentsComponent` |
| Selector | `app-<area>-<screen>` | `app-admin-mentors-students` |
| Service | `<name>.service.ts` → `<Name>Service` | `auth.service.ts` → `AuthService` |
| API interface | **mirrors the API in `snake_case`, verbatim** | `interface MentorLoad { mentor_id: string; … }` |

**On `snake_case` in TypeScript.** It looks wrong to a TypeScript eye and it is deliberate: the
existing interfaces copy the FastAPI `*Out` model field-for-field with no remapping layer. Introducing
camelCase remapping for new screens would mean two conventions in one codebase and a translation layer
that exists only to be forgotten. New interfaces keep `snake_case`.

---

## One vocabulary decision, made once

**The UI says "Batch". The database says `Cohort`. Both stay.**

Chain of reasoning:

1. The design's admin screens label this object **Batch** throughout (`BATCH-1`, "Add batch", "Batches").
2. The database already has `Cohort` / `cohorts`, created in migration `fea4515cdba5`, carrying `code`,
   `name`, `batch_label`, `degree_level`, `start_date`, `end_date`.
3. `Student.cohort_id` already points at it, and is already indexed as one of the two hottest scope
   columns (`b41c9e2d7f05`).
4. Renaming the table would mean a migration, touching every reference, and re-pointing an index — for
   a word.
5. **Decision:** keep `Cohort` in code and `Batch` in the interface. `CohortOut` gains no `batch`
   alias; the Angular label is the only place the word "Batch" appears.
6. **Rejected:** renaming `Cohort` → `Batch`. The cost is real and the benefit is cosmetic. What is
   *not* acceptable is silence — hence this section, so the next developer meets the mapping here
   rather than discovering it from a confusing diff.

---

# Change log

Entries are appended in build order. Nothing below is written before its reasoning is.

---

## L1-01 · `apps/api-py/app/models/user.py` — `Student.cohort_id` becomes a real foreign key

**Layer:** L1 · Data
**Change:** `cohort_id` moves from a bare indexed `String` to `ForeignKey("cohorts.id")`, still nullable.

**Chain of reasoning**

1. The design's student Profile shows a locked card of five institutional facts: College, Department,
   Batch, Entry date, Expected completion.
2. Four of those five have no data source today. Only Department exists, as free text on `users`, and
   nothing writes it.
3. All five are reachable through one join — `Student → Cohort → Department → College` — provided the
   first hop is real.
4. Today it is not. `app/models/user.py:97` declares
   `cohort_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)` with the comment
   `# FK to Cohort later`. Nothing in the database validates that the value names a cohort that exists.
5. So the smallest change that makes the design's card truthful is to honour that comment.
6. **Nullable is kept.** A student legitimately exists before an admin seats them — provisioning
   creates the row, assignment happens after. Making it `NOT NULL` would force the two into one step
   and break `seed_roster.py`, which creates students with no cohort.
7. **The index is kept.** It is not redundant with the FK: PostgreSQL creates an index for a primary
   key, not for the referencing side of a foreign key. Dropping it would silently undo the
   `b41c9e2d7f05` audit fix that added it.
8. **`ondelete` is deliberately omitted**, which means `NO ACTION` — deleting a cohort that still has
   students will be refused by the database. That is the desired behaviour: the design archives
   cohorts, it never deletes them, and a cascade here would silently delete student rows.
9. **Rejected:** copying College / Department / Batch / the two dates onto `students` as columns. It is
   faster to write and it is the trap — it creates two sources of truth, and it is precisely what makes
   the deferred Academic year / Course / Specialization levels expensive later, because inserting a
   level would mean backfilling every student row.

**Risk:** existing rows may hold a `cohort_id` that matches no cohort. The migration must clean those
to `NULL` *before* adding the constraint, or the `ALTER TABLE` fails on a live database. See L1-02.

---

## L1-02 · `apps/api-py/migrations/versions/<rev>_student_cohort_fk.py` — the migration

**Layer:** L1 · Data
**Change:** null out orphaned `cohort_id` values, then add the foreign key constraint.

**Chain of reasoning**

1. `students.cohort_id` has been an unconstrained string for its whole life, and two seeds write it.
   Nothing has ever prevented a value that names no cohort.
2. Adding a foreign key validates every existing row. One orphan and the migration aborts — on a
   production database, mid-deploy.
3. So the migration must be two statements, in order: `UPDATE students SET cohort_id = NULL WHERE
   cohort_id IS NOT NULL AND cohort_id NOT IN (SELECT id FROM cohorts)`, then `ADD CONSTRAINT`.
4. Nulling an orphan loses nothing real. The value already pointed at nothing; the only difference is
   that the database now says so.
5. **`lock_timeout` and `statement_timeout` are set at the top**, per the expand/contract discipline:
   `ADD CONSTRAINT` takes a lock, and a migration that queues behind a long-running read takes the site
   down. Five seconds of waiting, then fail and retry, is better than an outage.
6. **This is an expand-phase change.** Old code that reads `cohort_id` as a string keeps working
   unchanged, so it is safe to deploy before the code that uses the join.
7. `downgrade()` drops the constraint only. It does **not** restore the nulled orphans — they are
   unrecoverable by then, and pretending otherwise would be a lie in the migration.

---

## L1-03 · `apps/api-py/app/models/institution.py` — `College` and `Department` (new)

**Layer:** L1 · Data
**Change:** two new models, `College` → `colleges` and `Department` → `departments`.

**Chain of reasoning**

1. The locked profile card needs College and Department. There is no College column anywhere in the
   schema, and `users.department` is free text with **no writer of any kind** — verified by grep: it is
   read by `leave.py:120` and `director.py:390` and assigned nowhere.
2. So both must become real entities before any screen can show them truthfully.
3. **Department hangs off College, and Cohort hangs off Department** — not both off College. That is
   what makes the deferred Academic year / Course / Specialization levels cheap to insert later: they
   slot between Department and Cohort without touching anything above or below.
4. **`status` is a plain `String`, not a PG enum.** `voice_platform.py` already argues this: a new
   status value should be a data change, not a `CREATE TYPE` migration carrying AGENTS.md's three enum
   gotchas. It also means archive/restore is an `UPDATE`, never a `DELETE`, which is exactly what the
   design's console does.
5. **The department code is unique per college, not globally.** Two colleges may each run a "CSE".
   A global constraint would force the college name into the department code, and every screen inherits
   that noise forever.
6. **Almost every field is nullable.** An admin creates a college with a name and a code and fills in
   campus and contact later. A required column here becomes a required field on the create form, and a
   required form field is how invented data enters a database.
7. **No `ondelete` on the department → college link.** The database will refuse to delete a college
   that still has departments. A cascade would silently take cohorts, and through them student links.
   The supported retirement path is archiving.

---

## L1-04 · `apps/api-py/app/models/cohort.py` — `Cohort.department_id`

**Layer:** L1 · Data
**Change:** new nullable FK to `departments.id`, indexed. Adds `ForeignKey` to the imports.

**Chain of reasoning**

1. The join the profile card needs is `Student → Cohort → Department → College`. L1-01 built the first
   hop; this is the second.
2. **Nullable, and this one is not a preference.** `cohorts` predates `departments` by many migrations.
   Existing rows have no department to point at, so `NOT NULL` would make the migration unrunnable on
   any database that already holds a cohort — including every developer's.
3. Until an admin sets it, the card shows a dash. That follows the repo's existing rule that a missing
   value renders as a dash rather than a confident zero or an invented default.
4. Indexed because it will be filtered on — every "cohorts in this department" listing in the admin
   console is that query.

---

## L1-05 · `apps/api-py/migrations/versions/e6b2d9c41a83_colleges_departments.py`

**Layer:** L1 · Data
**Change:** create `colleges`, create `departments`, add `cohorts.department_id`.

**Chain of reasoning**

1. Kept **separate from `d5a1c8b30f47`** (the student FK) rather than merged. Each is independently
   deployable and independently revertible; merging them would mean a failure in either takes both back.
2. Ordering inside the migration is forced: `colleges` before `departments` (the FK), and both before
   `cohorts.department_id`.
3. `lock_timeout` / `statement_timeout` at the top, same discipline as L1-02.
4. **Pure expand phase.** Two new tables and one new nullable column; nothing existing reads any of
   them. Safe to deploy well ahead of the code that will.
5. `downgrade()` reverses cleanly in dependency order — index, constraint, column, then the two tables.
   Unlike L1-02 there is nothing lossy here, so the downgrade is honest.

**Verified:** `alembic heads` → `e6b2d9c41a83`; models import; the ORM resolves the full chain
`students.cohort_id → cohorts.id`, `cohorts.department_id → departments.id`,
`departments.college_id → colleges.id`.

**NOT verified:** neither migration has been *run*. Docker Desktop is not running, so there is no
Postgres to run them against. `alembic upgrade head` on a scratch database is the outstanding check,
and L1-02's orphan-nulling `UPDATE` is the specific statement that needs it.

---

## L2-01 · `apps/api-py/app/routers/student.py` — `InstitutionOut`, `_institution_for`, and one `_profile_out`

**Layer:** L2 · Backend

**Chain of reasoning**

1. The card needs five facts. They are reached by walking `students.cohort_id -> cohorts ->
   departments -> colleges`, so the resolver walks as far as the data allows and stops without
   complaint. An unseated student gets nulls, not a 404 — it is one block on a screen with plenty
   else to show.
2. **Every field nullable.** Any hop may legitimately be unset. A missing value renders as a dash,
   never a default and never an invention — the rule the English baseline already follows for a
   pending score, and doubly important on a card headed "verified by Main Admin".
3. **A LIVE BUG WAS FOUND HERE.** `ProfileOut` was constructed in two places: inline in `my_profile`,
   and in a shorter `_profile_out` used by `update_profile`. When `usn` / `full_name` /
   `current_semester` / `current_stage` were added to the schema, only the inline copy was updated —
   so **`PUT /api/student/profile` raised a 4-field ValidationError on every call**, and no test
   exercised the update path's response body, so nothing caught it.
4. Fixed by construction, not by patching: there is now **one** `_profile_out(db, prof)` and both
   endpoints call it. Two constructions of one response is how the drift happened; one cannot drift.
5. **Students cannot write these**, and it is enforced twice: `ProfileUpdateIn` is a closed allowlist
   that does not name them, and `StudentProfile` has no such columns to set even if it did.

---

## L2-02 · `apps/api-py/app/routers/admin.py` (new) — the institutional write layer

**Layer:** L2 · Backend · 8 paths, 11 operations, mounted at `/api/admin`

**Chain of reasoning**

1. Nothing in the running application could create a College, Department or Cohort, seat a student, or
   write `users.department` / `users.designation`. All of it was CLI-and-seed only — and `app/seed.py`
   refuses to run on `ENV=prod`, so on a production host **no cohort could exist at all**.
2. **No DELETE endpoint exists in this file.** Status moves `ACTIVE <-> ARCHIVED`. That is what the
   design's console does, and the foreign keys enforce it anyway: the database refuses to delete a
   college that still has departments. A graduated student's record must still name their college.
3. **`AdminCohortOut`, not `CohortOut`.** Two different `CohortOut` classes already exist with
   different shapes (`director.py:116`, `badge_verification.py:429`) — the same collision this
   codebase has for `LeaderboardOut`, where the payload depends on which URL you hit. A third would
   deepen it, so this one is named for its surface.
4. **`require_director`** (DIRECTOR + ADMIN), matching every other admin surface including
   `voice_platform/api/admin.py`. A narrower Main-Admin-only gate belongs with the capability work;
   inventing a second gate now means two answers to "who may administer", which is the exact shape of
   bug rule 2 exists to prevent.
5. **Department codes unique per college, not globally** — two colleges may each run a "CSE", and a
   global constraint pushes the college name into the code.
6. **Dates converted at the edge.** `cohorts.start_date` / `end_date` are `DateTime(timezone=True)`
   and predate this surface. The API speaks `date`, because entry and completion are calendar facts,
   and `_as_utc_midnight` does the conversion in one place rather than four call sites.

---

## L2-03 · `apps/api-py/app/routers/registration.py` — provision on approve

**Layer:** L2 · Backend

**Chain of reasoning**

1. Approval stamped a status and three audit columns and **created nothing**. Its own docstring called
   provisioning "a separate follow-up step" — and that step existed nowhere in the codebase.
   `registrations.approved_student_id` was declared, returned by the API, and never written.
2. `_provision_student` now creates the User, the Student seated in the rule's cohort, a
   `StudentProfile`, and stamps `approved_student_id` — **in the same transaction as the status**, so
   an application is never left approved with no account behind it.
3. **Idempotent by lookup, not by flag.** A double-clicked Approve must not create two students. The
   endpoint refuses a second decision with 409, and the provisioner finds existing rows rather than
   inserting. Relying on the caller not to double-click is not a design.
4. **No password is set.** The account carries the unusable `"google-only"` sentinel, which is not a
   `scrypt:<salt>:<digest>` string and so can never match in `verify_password`. Sign-in is Google-only
   in production; a password, where wanted, arrives through activation.
5. A `StudentProfile` row is created because `GET /api/student/profile` **404s without one** — a
   freshly provisioned account would otherwise look broken on first sign-in.

**Two bugs the tests caught in this file, both mine, both real:**

- `reg.full_name` — `Registration` has **`name`**. Would have crashed on every approval.
- `db.get(StudentProfile, student.id)` — `StudentProfile`'s primary key is its own `id`; `student_id`
  is a separate unique column. `db.get()` looks up by PK, so it always missed and inserted a duplicate
  that the unique index then rejected. Fixed to query by `student_id`.

---

## L2-04 · `apps/api-py/tests/test_institutional_spine.py` (new) — 9 tests, all passing

**Layer:** L2 · Backend

Three pure tests (no database, run anywhere) and six against Postgres. The one worth naming is
`test_moving_a_batch_moves_every_seated_students_card`: it edits the batch and asserts the student's
card moved, **with no write to the student row**. If someone later denormalises those fields onto
`students`, that test fails — which is the entire point, because the copy is the tempting shortcut
that makes the deferred hierarchy levels expensive.

---

## Verification — actually run, not assumed

The dev database `reep_py` **cannot be migrated**: it is stamped at `a9c3e5f70b21`, a revision whose
file was destroyed in this session's `git clean`, and it carries five ghost `resume_*` tables from
that deleted chain while lacking `users.designation`. It followed a migration history that no longer
exists.

So verification ran against a clean scratch database, `reep_py_verify`:

| Check | Result |
|---|---|
| `alembic upgrade head` from empty | **all migrations applied**, head `e6b2d9c41a83` |
| `d5a1c8b30f47` orphan sweep | ran, `cleared 0 orphaned students.cohort_id value(s)` |
| Schema | `colleges` + `departments` created; `students -> cohorts`, `cohorts -> departments`, `departments -> colleges` FKs present; `uq_department_college_code` present |
| `python -m app.seed` | succeeded |
| `tests/test_institutional_spine.py` | **9 passed** |
| Full suite | **1 failure, pre-existing and not ours** — `test_student_weekly_series_and_cv_download`, caused by `director.py:934` using `strftime("%-d %b")`, a glibc extension that fails on Windows and passes on Linux CI |

**Outstanding for the operator:** `reep_py` needs recreating (drop, `alembic upgrade head`,
`python -m app.seed`), or point `DATABASE_URL` at `reep_py_verify`, which is now fully migrated and
seeded.

---

## G-01 · `apps/api-py/tests/test_codebase_guards.py` (new) — making the failures unrepeatable

**Layer:** L2 · Backend (test) · 7 guards, no database, sub-second

**Chain of reasoning**

1. Every problem hit during this build was found by a human reading code, or by a test failing for an
   unrelated reason. Neither scales. A rule written in a document is followed until the afternoon
   someone is in a hurry; a rule that fails the build is followed always.
2. So each incident became an assertion. Every guard names the incident it prevents in its docstring,
   because a guard whose reason is forgotten gets deleted by whoever it inconveniences.
3. These are plain pytest tests, so **CI already runs them** — the `api` job needs no change. And CI
   sets `REEP_REQUIRE_DB=1`, so a suite that skipped every database test cannot read as green.

**The seven guards, and the incident behind each**

| Guard | Incident |
|---|---|
| `test_no_platform_specific_strftime` | `director.py:934` used `strftime("%-d %b")`. `%-d` is a **glibc extension**: it works on Linux and raises `ValueError("Invalid format string")` on Windows. `GET /api/director/students/{id}/weekly` was green in CI and dead on every developer machine. **Fixed** to `f"{start.day} {start:%b}"`. |
| `test_no_new_duplicate_schema_names` | `LeaderboardOut` is defined twice with different shapes, so the payload depends on which URL you hit. Nine such names exist. While writing `admin.py` a third `CohortOut` was very nearly added. New duplicates now fail; the nine are an explicit allowlist so the debt is visible. |
| `test_the_duplicate_schema_debt_list_is_accurate` | An allowlist that is never pruned becomes a licence. If a duplicate is cleaned up, its name must leave the list or this fails. |
| `test_every_model_module_is_imported_in_models_init` | AGENTS.md's own rule. An unimported model is invisible to Alembic autogenerate, so its table is silently missing from the next migration and the failure surfaces much later, somewhere unrelated. |
| `test_the_migration_chain_has_exactly_one_head` | Two heads means someone branched, and `alembic upgrade head` refuses — during a deploy, after the image is built. |
| `test_every_down_revision_points_at_a_migration_that_exists` | The dev database was stamped at `a9c3e5f70b21`, a revision whose file had been deleted. Alembic could not compute a path and the database had to be rebuilt. A dangling `down_revision` in the files is the same fault one step earlier, and it is catchable. |
| `test_agents_md_only_references_paths_that_exist` | AGENTS.md documented `app/domains/` and `app/core` in detail; neither has any source. Documentation describing code that does not exist is worse than none, because it is trusted. Paths described as *deleted / removed / retired* are skipped — that is the document doing its job. |

**Two bugs found while writing the guards themselves, both worth recording**

- The `strftime` guard flagged **the comment written to explain the fix**. Fixed by stripping trailing
  comments before matching — a guard that cannot tell code from prose will be disabled by the first
  person it annoys.
- The AGENTS.md guard's `` word boundary was written into the file as a literal **backspace
  character (0x08)** by a shell heredoc, so the regex silently never matched. It was invisible to
  `grep` and to reading the file; only `repr()` of the line showed it. Worth knowing: a
  pattern that "should obviously work" and does not is worth dumping as `repr` before assuming the
  logic is wrong.

---

## OPS-01 · The development database, rebuilt

**Layer:** L1 · Data (operational)

`reep_py` was stamped at `a9c3e5f70b21` — a revision destroyed by this session's `git clean` — and
carried five ghost `resume_*` tables from that dead chain while missing `users.designation`. It had
followed a migration history that no longer exists, so no `alembic upgrade` could move it forward.

Dropped and rebuilt: `CREATE DATABASE` -> `alembic upgrade head` -> `python -m app.seed`. The scratch
database used for earlier verification (`reep_py_verify`) was dropped afterwards.

**Verification, on the rebuilt database, with `REEP_REQUIRE_DB=1` so a skipped suite cannot read as
green:**

    729 passed · 2 skipped · 0 failed · 0 errors

That includes `test_student_weekly_series_and_cv_download`, which had been failing on Windows for the
`strftime` reason above and now passes on both platforms.

---

# Round 2 — three independent adversarial reviews, and the fixes

The spine above was handed to three reviewers, each told to find faults rather than confirm
correctness: **correctness & security**, **schema & migrations**, and **test quality**. Two of them
built throwaway databases and ran the code rather than reading it; the third mutation-tested the
tests. All three put the same item at the top: `app/routers/admin.py` had 11 operations and zero tests.

What follows is one entry per fix, in the order the danger was ranked.

---

## SEC-01 · Approval had become a self-service door into the roster

**Layer:** L2 · Backend · `app/routers/registration.py`, `app/config.py`
**Severity:** the worst thing in the round, and it was introduced by the previous entry.

**Chain of thought.**

1. `app/google_auth.py` states the contract: a verified Google account with no `users` row is refused;
   *nothing self-provisions*. AGENTS.md repeats it: **the roster IS the access control.**
2. `PROV-01` made approval **insert a `users` row**.
3. The row's email comes from `Registration.email`, written by `POST /api/register` — which is
   **public, unauthenticated**, and validates the address no further than "contains an @".
4. `EmailVerification` and `registrations.email_verified_at` exist in the model and have **no writer
   and no reader anywhere**. Nobody has proved the applicant can even read that mailbox.
5. Therefore: apply as "Priya Sharma / attacker@gmail.com", wait in the queue looking exactly like
   every other application, and one Approve click grants a stranger a real Google sign-in to REEP —
   with a `studentId`. **Before this feature that click created nothing.** The door was built here.

**The fix, and why it is a fence here but not on sign-in.** `settings.provisionable_email_domains`
gates provisioning on the college domain. `google_allowed_domain`'s own comment says the domain list
is a label and *"nothing in the sign-in path refuses on it, and nothing should start to"* — that is
still true and unchanged. The two run in opposite directions:

| | question asked | what a domain test can do |
|---|---|---|
| sign-in | a verified Google account -> is it on the roster? | only lock out someone already enrolled |
| provisioning | an unauthenticated form -> may it JOIN the roster? | only stop a stranger becoming a row |

A fence before enrolment cannot lock anybody out of anything. That asymmetry is the whole
justification, and it is written into the property's docstring so the next reader does not "fix" the
inconsistency by deleting one of them. Staff are unaffected — `app/grant_access.py` exists precisely
to admit an address off the student domain and does not come through this path.

**Test:** `test_an_application_from_outside_the_college_domain_cannot_be_approved`. Asserts the 422,
*and* that no account was left behind, *and* that the application is still decidable.

---

## SEC-02 · Approval could attach a Student row to a staff account

**Layer:** L2 · Backend · `app/routers/registration.py`

**Chain of thought.**

1. `_provision_student` looked up the User **by email alone** and never read `user.role`.
2. The duplicate check on submit is against `registrations.email`, **not** `users.email` — so an
   application naming `mentor@bgscet.ac.in` is accepted by the public form and queues up looking
   ordinary.
3. `_payload_for` mints `studentId` into the session for **any** user who has a Student row.
4. So approving it gives that mentor a session carrying `role: MENTOR` **and** a `studentId`.

Rule 2 says staff scope is decided by role. This let an unauthenticated form edit it. Now a 409 that
names the conflict and tells the director what to do instead.

**Test:** `test_an_application_naming_a_staff_address_cannot_be_approved` — asserts the victim's role
is untouched and that no Student row appeared.

---

## SEC-03 · The double-approval 409 was not race-safe

`db.get(Registration, ...)` then check-then-write: two directors clicking Approve together both read
`PENDING`, both pass, both provision, and the second dies on the unique email — a **500**, not the
409 the endpoint promises. Now `SELECT ... FOR UPDATE`, so the second request waits, reads `APPROVED`,
and gets its 409.

---

## FEAT-01 · `department_id` had no writer, so the feature was unreachable

**Layer:** L2 · Backend · `app/routers/admin.py`
**This is the one that made the headline claim untrue in practice.**

**Chain of thought.**

1. `cohorts.department_id` was written in exactly **one** place: cohort *creation*.
2. `AdminCohortPatchIn` did not expose it. So an existing cohort could never be filed.
3. `list_cohorts` is keyed on a `department_id` **path segment** — so a cohort with
   `department_id IS NULL` matches no route at all. **Invisible, and therefore unfixable.**
4. Every cohort predating this feature is in exactly that state, the seeded one included.
5. My own migration comment said *"an admin sets it afterwards"*. No code path let them.
6. And the spine tests hid it, because the fixture built the chain through `SessionLocal` rather than
   through the API. **Green suite, broken feature.**

**Fix:** `department_id` on the PATCH body (nullable — an explicit null un-seats, which is the only
way to correct a mis-filed batch), the target validated so a bad id is a 404 rather than an
IntegrityError 500, and a new `GET /admin/cohorts/unassigned` — the console's inbox for stranded
batches. Without that endpoint the writer alone would not have been enough.

**And the fixture lesson:** `tests/test_admin_institution.py` builds its College -> Department ->
Batch chain **through the endpoints**, so the fixture itself fails if an admin could not do this.

---

## FEAT-02 · A cleared form field returned a 500 on all three PATCH endpoints

**Layer:** L2 · Backend · `app/routers/admin.py` — new `PatchModel` base

Every PATCH field is `X | None = None` so that omitting it means "leave alone", read back with
`model_dump(exclude_unset=True)`. But **`exclude_unset` is not `exclude_none`**: `{"name": null}` is
*sent*, survives into the dict, and `setattr(college, "name", None)` reaches a NOT NULL column as an
unhandled IntegrityError — a 500 with a Postgres constraint name in the body.

`{"name": null}` is exactly what a form emits when someone clears an input and saves.

The wire needs a **three-way** distinction — omitted / a value / explicitly null — and only the third
is an error. `PatchModel` subclasses declare `NON_NULLABLE`; genuinely nullable columns (`campus`,
`contact`, `head`) stay off the list because nulling them **is** how they are cleared. A blanket
refusal would have removed the only way to unset them, which is why there is a test for that too.

---

## SEED-01 · The card showed dashes on every fresh database

`app/seed.py` created the Cohort with **no `department_id`** and no College or Department at all. So
after `docker compose up` -> `alembic upgrade head` -> `python -m app.seed`, the card headed
*"Institutional assignment — verified by Main Admin"* rendered College and Department as **dashes**.
That is the state every developer and every demo starts from: the empty version of a working feature.

Against AGENTS.md's own discipline — the ledger is seeded deliberately 0.5 h short so the interesting
state is the default one. Now seeds College -> Department -> Cohort, and back-fills a cohort seeded
before departments existed.

    BGS College of Engineering and Technology | Department of Management Studies | 2024-26 | 2024-08-01 | 2026-07-31 | 1

---

## MIG-01 · `alembic upgrade --sql` crashed, and my migration broke it

**Layer:** L1 · Data · `migrations/versions/d5a1c8b30f47_student_cohort_fk.py`

`op.get_bind()` returns **`None`** in offline mode, so reading `.rowcount` off it raised
`AttributeError`. `migrations/env.py` implements `run_migrations_offline()` in full, with
`literal_binds=True` — someone deliberately made `--sql` work, because that is the DBA-review
workflow: generate the script, have it read, run it in a change window. It is exactly the workflow a
**locking** migration wants, and this was the migration that made it impossible.
**Every migration before it was `--sql`-clean.**

Fixed by dropping the rowcount entirely. The count now lives in a table, which is better than the
`print()` it replaced for three separate reasons (MIG-03).

---

## MIG-02 · `SET` instead of `SET LOCAL` leaked timeouts into every future migration

`env.py` wraps the whole `upgrade head` in **one transaction on one connection**, and `SET` is
session-scoped. So `statement_timeout = '60s'` applied from this revision **to the end of the run, and
to every migration added after it, forever**.

The failure it sets up: the next person's backfill over `messages` runs 90 s on their laptop, passes,
and aborts in production with `canceling statement due to statement timeout` — from a timeout set two
revisions upstream that their own file never mentions and that a hand-run `psql` session will not
reproduce. `SET LOCAL` costs one word.

---

## MIG-03 · The destructive branch had never actually been run

The build log above records `cleared 0`. A reviewer ran it with real orphans — it worked, and three
things about it were wrong:

1. **`print()` goes to stdout; alembic logs to stderr.** In a deploy log that captures the streams
   separately the count is orphaned from the migration it describes. No other migration in the
   55-file tree prints anything.
2. **It printed before the transaction committed** — so a later failure meant the operator had been
   told "cleared 400" about an UPDATE that was rolled back. This repo has a whole runbook section
   about logs that lie.
3. **`downgrade()` called the orphans "unrecoverable by now" — that was a choice dressed as a fact.**
   The column was free-form string for its entire life across a Prisma migration; assuming every
   value in it was a UUID pointing nowhere is the over-confident reading.

Now `students_orphaned_cohort_ids` is written **before** the UPDATE, the downgrade restores from it
(only into rows still NULL, so a legitimately re-seated student is never overwritten), and the table
is deliberately **not** dropped — deciding those rows are safe to lose is a judgement for a person
looking at them, not for a downgrade running unattended.

**And the trap that created:** autogenerate sees a table with no model and proposes `op.drop_table` —
which would delete the only copy of the rows. `migrations/env.py` now carries `_PRESERVED_DATA_TABLES`
and an `include_object` hook, so the contract is the prefix: a migration that stashes rows before a
destructive statement names the table `*_orphaned_*` and it survives every future autogenerate.

---

## MIG-04 · `NOT IN` -> `NOT EXISTS`, and two naming faults

- **`NOT IN (SELECT id FROM cohorts)`** evaluates to NULL — never true — the instant one NULL appears
  in the subquery. The sweep would clear nothing and the ADD CONSTRAINT would then abort on exactly
  the orphans the sweep exists to remove. Correct today only because `cohorts.id` is a NOT NULL
  primary key; `NOT EXISTS` stays correct without depending on that.
- **`College.code` was `unique=True` unnamed.** `create_all()` names it `colleges_code_key`; the
  migration named it `uq_college_code`. The same declaration produces **two different constraint
  names depending on how the database was built**, so a future `op.drop_constraint("uq_college_code")`
  fails on half of them. Now a named `UniqueConstraint` in `__table_args__`, matching `Department`.
- **`op.drop_table(..., if_exists=True)`** needs Alembic >= 1.16 and was the tree's first use of it —
  an unrecorded version floor whose failure mode is a `TypeError` inside a migration during a deploy.
  `DROP TABLE IF EXISTS` has no floor.

---

## MIG-05 · Two claims in the comments were false

1. **"The database refuses to delete a college that still has departments"** — false through the ORM.
   `College.departments` declared no cascade, so SQLAlchemy helpfully **nulls the children first**,
   and because `college_id` is NOT NULL you get a `NotNullViolation` naming `departments.college_id`
   while trying to delete a `College` — an error that reads like a bug in the wrong table. There is no
   delete endpoint today, which is the only reason it has not bitten. `passive_deletes="all"` makes
   the comment true.
2. **`ix_departments_college_id` was redundant.** `uq_department_college_code` is
   `(college_id, code)` and serves a predicate on its leading column — proven with `EXPLAIN`. The
   build log reasoned carefully about *not* dropping `ix_students_cohort_id`; the symmetric question
   about the new index was never asked. Dropped.

---

## TEST-01 · The tests were softer than their own docstrings

The test reviewer's finding, and the hardest to read: **every one of the nine spine tests was
defeatable by a plausible mutation**, and in four cases the docstring described a guarantee the
assertions did not deliver. That is worse than a thin test, because the next reader believes the area
is covered.

| test | how it was defeated | fix |
|---|---|---|
| `..._cannot_write_their_own_institutional_assignment` | **structurally incapable of failing.** It forged `college_name` — *not a column anywhere in the codebase* — and `ProfileUpdateIn` is `extra="ignore"`, so pydantic dropped the keys before the handler ran. It asserted that nothing happened, which was guaranteed. The mutation that breaks it (`extra="allow"`) let a student set their own `placement_eligible` and stayed green. | forges the three fields that are **real, writable columns** deliberately absent from the schema, reads the **columns** back rather than the response, and sets the restrictive value first so the forge has to flip it |
| `..._twice_provisions_one_student` | the 409 fires **before** provisioning, so all four idempotency lookups could be deleted and it passed — including the one whose comment records the duplicate-insert bug it was written to fix | split in two: one calls `_provision_student` **directly** twice; a second covers the endpoint 409. Plus assertions on `role`, `usn` and `cohort_id`, none of which were checked |
| `test_updating_a_profile_returns_a_complete_body` | compared **key names only**, so a second builder returning the right keys with hardcoded wrong values passed | full value equality |
| `test_moving_a_batch_...` | one GET **after** the mutation on a fresh cohort — any cache would be cold and pass while every real student saw stale data. `student.py` already has a `_leaderboard_cache`, so not hypothetical | reads **before and after**, and asserts the fields that did *not* move still resolve |
| `test_every_institution_field_accepts_none` | tested nothing `_institution_for` does — it constructed the model directly | **deleted**, and replaced by end-to-end partial-chain coverage |
| `T8`/`T9` cleanup | inline, **after** the assertions — any failure leaked a User, Student, Registration and StudentProfile permanently, and the leak then *masked* the very regression the test existed for on the next run | a yield fixture, whose finaliser runs on failure. It records whether the user pre-existed, so it never deletes a `make_user` account it did not create |

**The gap with no coverage at all** was the partial chain — a cohort whose `department_id` is NULL,
which is *every* cohort predating this work. Now `test_a_partly_built_chain_renders_what_exists_and_nulls_the_rest`.

That test needed a trick worth recording: dropping the null guard **does not raise** —
`db.get(Department, None)` quietly returns `None`. So a behavioural assertion could not tell guarded
from unguarded code. SQLAlchemy *does* warn (`fully NULL primary key identity cannot load any object.
This condition may raise an error in a future release`), so the test promotes `SAWarning` to an error.
That makes the mutation visible **now** instead of on the upgrade that turns it into an exception.

---

## TEST-02 · `admin.py` — 11 operations, 0 tests -> 18 tests

New `tests/test_admin_institution.py`, ordered by the reviewers' ranking:

- **every one of the 11 operations refuses a STUDENT** — enumerated explicitly, not walked off the
  router, so a new endpoint without a line here is a visible omission rather than silently covered
- the cleared-field 422 across five field/endpoint pairs, **and** that a nullable field can still be
  cleared
- college code normalised and globally unique; department code unique **per college**, so two
  colleges may each run a "CSE" — a mutation to global uniqueness breaks exactly that and nothing else
- the unassigned-batch inbox: un-seat -> appears -> re-file -> disappears
- seating a student populates **their** card and **nobody else's** (a resolver that scanned for "the
  newest cohort" would pass a single-student test and put one batch on every card), plus the release path
- archive **and restore**, and that an archived college stays in the list — a row that cannot be seen
  cannot be restored
- `designation` / `department` actually persist, `""` clears to `None`, and an omitted field is not touched

---

## TEST-03 · The guards were also softer than they claimed

- **`_KNOWN_DUPLICATE_SCHEMAS` said "may SHRINK, never grow" — and nothing enforced it.** Adding a
  name went green. A comment is not a mechanism; there is now a ceiling constant.
- **`test_agents_md_only_references_paths_that_exist` did not catch its own incident.** The regex
  required a `.py` suffix or an `apps/api-py/` prefix, so `app/domains/<context>/` (the `<` was not in
  the character class) and `app/core` — *the two examples in its own docstring* — both evaded it
  silently. Also: any line containing the word "removed" had **every** path on it skipped, so writing
  "(the old X was removed)" laundered a whole paragraph. Now proximity-based within `_GONE_RADIUS`.
- **The strftime guard split on `#` anywhere on the line**, including inside a string literal, and its
  character class covered six letters of the dozen-plus glibc directives. Now quote-aware, `%[-#][a-zA-Z]`,
  and scanning migrations and `tools/ci` — not just `app/`.
- **The migration chain guard silently skipped any file whose `revision:` line it could not parse**,
  and **silently overwrote duplicate revision ids** — the classic copy-paste merge artefact — so two
  files claiming one revision collapsed to one entry and the head count came out right on a broken
  chain. Both now fail loudly.

---

## VERIFY · Mutation-tested, because a passing test is not evidence

Every fix above was broken on purpose to confirm the guarding test goes red. A test that passes both
with and against the bug is not a guard.

**Production fixes — 9/9 mutations caught:** domain guard removed · role guard removed · provisioning
mints a DIRECTOR · `cohort_id` no longer copied from the rule · the User idempotency lookup deleted ·
explicit-null refusal removed · `department_id` writer removed · `ProfileUpdateIn` opened to extra
fields · the partial-chain null guard dropped.

Two survived the first run and both were real gaps, not false alarms:

- *the User idempotency lookup* survived because the `approved_student_id` short-circuit returns
  first. The realistic path is not a double-click at all — it is an applicant **already on the roster
  from `seed_roster`**, which is the normal state for an enrolled student. Added
  `test_approving_an_applicant_who_is_already_on_the_roster_reuses_their_account`.
- *the partial-chain guard* survived for the `SAWarning` reason in TEST-01.

**Guards — 7/7 caught**, including one that exposed a scope hole: `MIGRATIONS` points at
`migrations/versions`, so `env.py` — Python that runs on every deploy — was unscanned. Widened.

**Suite, with `REEP_REQUIRE_DB=1` so a skipped suite cannot read as green:**

    752 passed · 2 skipped · 0 failed        (was 729 — 23 new tests)

`alembic upgrade --sql` generates the full 55-migration script cleanly. `alembic check` reports no
drift on any table in this work; the five `redesign_*` index diffs predate it.

---

# Round 3 — the optional levels, and the one-line switch

**The request.** *"create which not designed db in ui give this user to fill optional what way i
can avoid more work and missing correct designing db / both side / when i feels its need i will
make mandatory to fill this information / just simple code change."* With the sketch:

    College → Department → [Academic year → Course → Specialization] → Batch → Student.batchId

Three council reviews were commissioned — schema, UI contract, and a reading of an LMS-schema
article the owner shared. They disagreed on two things, and both disagreements changed the build.

---

## DEC-01 · Academic year is NOT a table

**Layer:** L1 · Data · decision
**Who argued it:** the article reviewer, against the sketch and against the schema reviewer.

**Chain of thought.**

1. The article makes "year" an `int` column, not an entity.
2. REEP already carries the fact **four ways**: `cohorts.batch_label` ("2024-26"),
   `cohorts.start_date` / `end_date`, `students.current_semester`, `courses.semester`.
3. A fifth representation of one idea is a fact that will eventually disagree with itself.
4. If a year ever needs a record of its *own* — admission open/close dates, courses that run only in
   certain years — it inserts then at exactly the cost it would today. That is the property this
   whole shape was built for, so deferring costs nothing.

Put to the owner as a decision, with a recommendation. **Chosen: drop it.** Two levels, not three.

---

## DEC-02 · `AcademicCourse` / `AcademicSpecialization`, not `Course` / `Specialization`, and not `Degree` / `Stream`

**Layer:** L1 · Data · naming
**Verified, not taken on trust:**

| name | already means |
|---|---|
| `Course` | `app/models/course.py` — a taught subject a student enrols in (22MBA11) |
| `CourseOut` | `student.py:1239` |
| `Specialization` | **three things**: `interview_matrix.Specialization` (the four mock-interview tracks), `PlatformSpecialization`, `interview_sessions.specialization` |
| `SpecializationOut` | `voice_platform/api/admin.py:108` |

A bare `Course` or `Specialization` here would be the collision `test_no_new_duplicate_schema_names`
exists to refuse — the build would fail, and the only way past it is the one edit that test forbids.

The article reviewer proposed `Degree` / `Stream` (both free). Refused on the owner's own
requirement — *"any developer who sees the code can understand easily"*: `AcademicCourse` → "Course"
is a mapping a reader resolves instantly; `Degree` → "Course" needs a lookup table in the head.
**The UI says "Course" and "Specialization"**, which is the Batch/Cohort precedent applied twice
more. The label is typed once, in `HIERARCHY_LEVELS`, so the word cannot drift.

---

## L1-05 · Two tables, two nullable columns, `created_by` on all four

**Files:** `app/models/institution.py`, `app/models/cohort.py`,
`migrations/versions/f7c3a1d92e54_academic_courses_specializations.py`

One shape, twice: `<parent>_id` + `code` + `name` + `status`, uniqueness **scoped to the parent**
(two departments may each run an "MBA"), no `ondelete`, named constraints. `cohorts` gains
`course_id` and `specialization_id`, nullable, beside `department_id`.

**Why all three pointers and not just the deepest.** A batch legitimately attaches at *any* depth —
department only, or course, or specialization — because the levels are individually optional. One
`specialization_id` cannot express "attached at course level", and a polymorphic column cannot be a
foreign key. So all three exist.

**Why that is not three sources of truth.** There is exactly one writer (`_resolve_ancestry`, below).
And the denormalisation rule the build log set in L1-01 §9 is about copying onto `students` —
thousands of rows, where inserting a level becomes a backfill. `cohorts` numbers in the dozens, and
`department_id` staying always-set is what keeps every existing endpoint and test untouched.

**`created_by_user_id`** on all four institution tables, from the owner's PostgreSQL checklist. The
design's Governance tab wants who/what/when and nothing recorded who. Nullable — the seed and the
CLIs have no user. Folded into this migration rather than a fourth, because nothing is deployed and a
schema that gains the same column in two revisions gains it a third time.

**The migration honours every lesson from round 2**: `SET LOCAL`, `--sql`-clean (pure DDL, no
`get_bind()`), named uniques byte-identical to `__table_args__`, no PG enum, no `if_exists`. And it
records what it deliberately does *not* do — `CREATE INDEX CONCURRENTLY` cannot run inside env.py's
single transaction, and `NOT VALID` buys nothing when the referenced tables were created empty three
statements earlier. The escalation path is written next to the reason it is not taken.

---

## L2-04 · The switch

**File:** `app/models/institution.py`

```python
HIERARCHY_LEVELS: Final[tuple[HierarchyLevel, ...]] = (
    HierarchyLevel("course", "Course", "course_id", required=False),
    HierarchyLevel("specialization", "Specialization", "specialization_id", required=False),
)
```

**The owner's edit:** `required=False` → `required=True`, on one line, in one file. Deploy.

**Why in the models module.** AGENTS.md says models are the schema's source of truth, and a developer
reading these tables must meet the requiredness rule in the same file — not discover it in a router.
It is `milestone.py`'s catalogue-is-code pattern, reused on purpose.

**Why not an env var.** AGENTS.md already makes this argument for `password_door_open`: on Fargate the
env lives in the task definition, so a second switch is a `terraform apply`, and `deploy.yml` ships
code, never infrastructure. Two tools for one decision is how a form ends up 422-ing over a value
nobody can change.

**Why the columns stay nullable forever.** Nullability is what the database promises about rows that
already exist; `required` is what this release asks of a new one. Conflating them turns "make it
mandatory" back into a migration — one that aborts on the first legacy batch, halfway through a
deploy. `test_hierarchy_columns_stay_nullable` is the argument the next person in a hurry meets
instead of a blank column definition.

**Two readers, one constant.** `AdminCohortIn._honour_required_levels` reads it; `GET
/api/admin/hierarchy/levels` serves it; the Angular form builds `Validators.required` from the
response. **Read through the module attribute at call time**, not an import-time binding — a mutation
proved that a module-level `from ... import HIERARCHY_LEVELS` freezes a copy the flip never reaches.

---

## L2-05 · The single ancestry writer

**File:** `app/routers/admin.py` — `_resolve_ancestry`

The client names only the **deepest** level it knows; the API walks *up* the real foreign keys to fill
the rest; a shallower value the client also sent is **checked against the derived one**, never stored
beside it. A contradiction is a 422 naming both. An explicit null on a shallower level while a deeper
one is set is a contradiction too — "no course" cannot coexist with a specialization that belongs to
one.

This is what makes three pointers one truth. The silent alternative surfaces as a profile card
printing the wrong department under the words "verified by Main Admin".

---

## L2-06 · The escape hatch ships WITH the switch, not after it

Flipping `required=True` cannot make forty pre-existing batches compliant, and no code change ever
can. Ship the switch alone and the morning after, the console refuses saves on rows nobody can fix,
and the fix will be to flip it back. So three things are one piece of work:

| | rule |
|---|---|
| **CREATE** | enforced — the whole point |
| **PATCH** | enforced only against **regression**: the gap is computed before and after, and the edit is refused only if it *widened*. A legacy batch missing a course can still have its name corrected; filling the gap is always allowed; emptying a compliant batch is refused |
| **READ** | never enforced. No list, profile or export fails over compliance |
| **seating** | not gated. Refusing to seat a student into a grandfathered batch turns a metadata rule into an admissions outage |

`AdminCohortOut.missing_levels` flags each row (rendered as a `.chip warn` — text and colour); `GET
/admin/cohorts/incomplete` is the inbox, for the reason `/cohorts/unassigned` exists: a row the
console cannot reach is one it cannot fix. Its predicate is built from the same tuple, so it is honestly
empty before the flip rather than pretending everything is compliant.

---

## L2-07 · The student's card: one query, and blanks that say why

**File:** `app/routers/student.py`

`_institution_for` was three sequential `db.get()` round trips on the app's hottest endpoint;
extended to five levels it would have been five. Now **one flat LEFT-JOIN query**, every join on a
primary key, each hanging directly off `cohorts` rather than chained — which is what the ancestor
pointers buy: a batch attached at department level with no specialization still resolves its college.

**A blank means two different things**, and the card must not confuse them:

- `pending` — expected and not yet recorded. Dash + "Not yet recorded". Something to chase.
- `not_in_use` — optional and this institution does not use it. **The row is hidden and the
  omission is stated once** in `not_in_use_note` ("Course and Specialization are not used at
  BGSCET."). A dash there would tell the student their record is incomplete when it is complete: a
  false pending, the mirror image of the confident zero the English-baseline rule forbids.

Only the server can tell them apart, because only it knows the switch. Flip a level to required and
the same blank becomes `pending` — `test_flipping_a_level_to_required_turns_its_blank_into_pending`
observes the switch from the student's side.

---

## L3-01 · The Angular console, and the card

**Files:** `core/hierarchy-schema.service.ts`, `features/director/institution/*`, `app.routes.ts`,
`app-shell.component.html`, `features/student/profile/*`

`/api/admin/*` had **zero callers** — twenty operations and no screen, which made "optional to fill in
the UI" true in the only sense that mattered: unfillable. `/director/institution` is the first
caller: drill-down (college → department → course → specialization) in one column, batches in the
other, one inline add-form per level, the batch form in ReactiveForms.

**The batch form's validators come from the server.** `HierarchySchemaService` fetches
`/admin/hierarchy/levels`; `buildBatchForm` attaches `Validators.required` from `lv.required`; the
"Required" / "Optional" chip is the same flag. **The word "Course" is never typed in the component.**
Flip the constant and the form follows.

**Grandfathered rows stay editable in the UI too.** A required-now, blank-as-loaded control is marked
(the admin should know) but excluded from the save gate (the row must not be bricked). A validator's
job is to tell; the gate's job is to refuse.

**The profile card renders `levels`**, hiding `not_in_use` rows and printing the footnote. It did not
render `institution` at all before — the locked card existed only on the API.

`npx ng build`: initial 176.56 kB; the console is a 32.79 kB lazy chunk.

---

## L1-06 · The owner's PostgreSQL checklist, classified honestly

Applied now: `created_by` (L1-05); index on every FK; `SET LOCAL` timeouts; expand-phase migrations.
Already true: snake_case plurals, 3NF (the whole point of not denormalising), `timestamptz`,
JSONB, Alembic, archive-never-delete.

Deliberately not applied, with the reason written where the next person will look:

| item | why not |
|---|---|
| `is_deleted boolean` | REEP uses a `status` string — strictly more expressive than a boolean, and it is the design's archive/restore |
| `CHECK` on `status` | alterable without `CREATE TYPE`, but adding a value is still a migration; the repo's rule is a new status is a **data** change. The allowlist is `_SETTABLE_STATUSES` |
| UUIDv7 | a repo-wide convention change (every model uses `uuid4().hex`); noted for L4, not smuggled into one table |
| column alignment ordering | tables in the dozens of rows |
| `CONCURRENTLY` / `NOT VALID` | cannot run in env.py's single transaction; referenced tables created empty in the same migration |
| private subnet, least-privilege roles, TLS, PgBouncer/RDS Proxy, PITR, restore drill, alerting | **L4 infrastructure** — belongs to the CDK work, with the backup/DR the owner already flagged |

---

## VERIFY · Round 3

**Switch and escape hatch — 10/10 mutations caught:** validator stops reading the switch ·
no-regression rule removed · incomplete inbox always empty · contradiction check removed · ancestors
no longer derived · **import-time freeze** (a first attempt at this mutation was itself wrong — a
function-local import still reads at call time; corrected to a module-level binding, and caught) ·
required-but-blank rendered as `not_in_use` · required level below an optional one · switch column
made NOT NULL · `created_by` not stamped.

**Suite, `REEP_REQUIRE_DB=1`:**

    764 passed · 2 skipped · 0 failed        (round 2: 752 — 12 new tests)

`alembic upgrade --sql` clean across 56 migrations. `alembic check`: no drift on any table in this work.
Seed on a fresh database: `BGSCET → MGMT → MBA → FIN → 2024-26 → 1 student` — every level populated. (First cut gave the department the code `MBA` too, so the chain read `MBA → MBA`; the department is the unit, the course is the programme it offers, and the seed now renames a legacy `MBA` department to `MGMT` in place.)

---

# Round 4 — Activation, forgot-password, change-password, confirmed registration

The first item on the remaining-work list, built to the plan agreed earlier in the session
(`docs/prototypes/admin-faculty-student/passwords.html`, "option B": **students sign in with
Google and hold no password; staff get password accounts through activation**). One decision
that plan called out is carried through unchanged: the password door stays
`password_door_open` in auth.py — the first staff activation issues the first scrypt hash,
and that act opens it, exactly as that function's comment says it should.

What existed already, and was reused rather than rebuilt: the send-exactly-once mailer, scrypt
hashing with the 12-character floor, `token_version` session revocation, the account-keyed
login limiter, and the `EmailVerification` table — which had no writer. What was missing was
small and is now here: a way for mail to leave the building, one table of hashed single-use
links, and the four endpoints.

---

## L1-05 · `app/models/auth_token.py` + migration `a8e5c3f17b92`

**Layer:** L1 · Data

**Chain of thought.**

1. Activation and reset are the same mechanism — a secret in an email, a hash of it here,
   consumed exactly once — so they are **one table**, and `purpose` is a plain String so a
   third kind of link is a row, not a `CREATE TYPE`.
2. The link is stored **hashed, never raw**, for the reason passwords are: a leaked dump
   yields hashes, not a working link into every account with a pending reset. **SHA-256, not
   scrypt** — the token is 256 random bits, not a guessable phrase, so a slow KDF buys nothing
   and only slows every click.
3. Registration's own confirmation link is **not** in this table: it predates it and hangs off
   `registrations`, which is not a user yet. Two tables, one shape.
4. `ON DELETE CASCADE` on `user_id`, unlike the institution tables — a token for a user who no
   longer exists cannot mean anything. `SET NULL` on `created_by_user_id`: losing the issuer
   must not lose the record that a link was issued.
5. `consumed_at` is **kept, not deleted**, so a second click can be told apart from an expired
   link and given the right words.
6. Named unique (`uq_auth_token_hash`), `SET LOCAL`, offline-clean — every lesson from round 2
   applied on the first try rather than the second.

---

## L2-07 · `app/mail_transport.py` — Amazon SES, or the console

**Chain of thought.**

1. `app/mailer.py` left the hole: it takes a `send(recipient, subject)` driver and says the
   transport is out of scope. This is the transport, and the provider choice lives in one
   function — swapping SES for the college's relay later is this file, not a project.
2. **Why SES:** not price (every option is effectively free at a college's volume) but that it
   needs **no credential** — it authenticates through the task role that already reaches
   Bedrock. Every other provider adds an API key to store and rotate, and puts student
   addresses in a third party's systems.
3. **The console transport is not a stub.** With `SES_FROM_ADDRESS` blank — every laptop, CI,
   and a fresh deployment before IT has verified the domain — a message is logged in full and
   kept in a **bounded** in-memory `outbox`. A developer reads the link out of the uvicorn log;
   the suite reads it out of `outbox`. It is only ever filled when no real transport exists,
   so it never becomes a copy of student mail sitting in a production process.
4. **The SES sandbox** is called out in the module docstring: new accounts can only send to
   verified addresses; leaving it means DKIM/SPF on `bgscet.ac.in` (an IT task) and a
   production-access request that takes a day or two. Nothing is blocked on it.
5. Rule 1 is not engaged and the docstring says why: these messages carry a name and a link,
   never a mark, a USN or a transcript. Keep it that way.

---

## L2-08 · `app/account_links.py` — issue, consume, and the mail that carries them

**Chain of thought.**

1. The library half, HTTP-free, so `grant_access` can issue an activation link from the
   command line with the **same function** the console uses.
2. **Consume is one atomic UPDATE** — `WHERE consumed_at IS NULL AND expires_at > now()
   RETURNING user_id` — and the row count is the arbiter. Two clicks race; exactly one wins.
   The same trick interview finalisation uses for `status = 'running'`.
3. **Issue supersedes.** Minting a new link revokes every earlier live one of the same
   purpose — on issue, not only on use — so "resend" hands over exactly one working link and
   a student who asks twice does not have two live links in two inboxes.
4. `peek_user_token` exists so a password can be checked against policy **before** the link
   is spent. A refusal for "12 characters minimum" must not burn the link.
5. `issue_activation` **refuses a STUDENT**. Under option B a student never holds a password;
   handing one a link would open a door the roster design does not want. They get
   `send_enrolment_notice` from provisioning instead — "sign in with your college Google
   account", no link, no password.
6. Dedupe keys include the **token id** for activation and reset: every re-issued link is a
   new message that must go out. Deduping on the user alone would make "resend" send nothing.
   The enrolment notice has no token, so it dedupes on the user — once, ever.
7. `issue_password_reset` is only ever called for an account that **holds** a password. A
   Google-only account has nothing to reset, and mailing it a link would quietly turn it into
   a password account.

---

## L2-09 · `app/routers/passwords.py` — the four endpoints

**Chain of thought.**

1. A separate module from auth.py (830 lines, owns sign-in). This owns credentials, and
   **borrows** auth's `_issue_session` / `_payload_for` / `_record_login` rather than copying
   them, so a change to the cookie is still one edit.
2. **Three flows, one ending: the thing that let you in is destroyed.** Activation consumes
   the link and signs in. Reset consumes the link, bumps `token_version` so **every** device is
   out — this one included; the person signs in fresh — and kills every other pending reset
   link. Change verifies the current password, bumps `token_version`, and **re-issues this
   cookie with the new version before answering**, so the person who just changed their
   password is not signed out by their own change and does not read it as a bug.
3. **`forgot` never says whether an address exists.** Same words, same 202, whether the account
   is real, Google-only or unknown — and the work that differs runs in a **`BackgroundTasks`**
   task with its own session, so the response returns at the same moment in every case.
   Otherwise the form is a free tool for discovering who is enrolled.
4. **`forgot` has its own throttle** — per address and overall — or it is a way to send someone
   a hundred emails. Same bounded in-process shape as auth.py's limiter, same honesty: per task,
   not per fleet.
5. Policy before consume, in both `activate` and `reset`. See VERIFY for what the mutation
   run taught about that order.
6. **410 Gone** for a dead link (with "already used" vs "expired" told apart), **422** for a
   refused password (same link, try again). The screen says which.

---

## L2-10 · `app/routers/registration.py` — the address is confirmed before anything is decided

**Chain of thought.**

1. `EmailVerification` and `registrations.email_verified_at` existed and had **no writer**.
   Round 2's domain fence stopped a stranger *joining the roster*; it did not stop someone
   applying with a classmate's `@bgscet.ac.in` address and looking ordinary in the queue.
2. So `submit` no longer applies the rule. It writes `PENDING_VERIFICATION`, issues a
   confirmation link (24 h) and sends it. The director's queue lists only `PENDING_REVIEW`
   (unchanged filter), so an unconfirmed application is invisible to it by construction.
3. **`GET /api/register/verify?token=`** is public and a GET because it is opened from a mail
   client. It consumes the link, stamps `email_verified_at`, applies the rule, and **redirects**
   into the app — `/login?verified=1`, or `?verified=0&why=expired_or_used` — so the outcome is
   shown in the app's own words rather than as bare JSON in a browser tab.
4. **`_apply_rule` runs at confirmation, not submit**, so the rule engine only ever sees
   addresses somebody has proved they own. An auto-approve that provisioning **refuses** — the
   domain fence, a staff address — is not dropped and not force-approved: it lands in the queue
   with the refusal as its reason. That also closes review finding #15: **`AUTO_APPROVED` rows
   are now provisioned**, and told.
5. A director's APPROVE now sends the enrolment notice after `_provision_student`.

---

## L2-11 · `app/config.py`, `app/main.py`, `app/grant_access.py`, `app/routers/admin.py`

- **config:** `SES_FROM_ADDRESS` (blank = console), `SES_REGION`, the three lifetimes from the
  plan (activation 168 h, reset 60 min, confirmation 24 h), the two forgot caps.
  `mail_configured` is a property, read per call.
- **main:** the router is mounted beside auth; at boot the mail state is **said out loud, not
  refused** — mail is optional (the on-screen link works without it), but an operator wondering
  why nobody gets their email should find the answer in the first screen of the log.
- **grant_access:** a STAFF account created without `--password-hash` gets an activation link
  **printed**, and emailed if a transport exists. The CLI was the only way staff were created;
  now it is also the way they are activated.
- **admin:** `POST /admin/users/{id}/activation-link`, DIRECTOR-gated. **The on-screen link is
  permanent, not a stopgap** while SES is in its sandbox: when someone says "the email never
  arrived", the admin reads them this instead of waiting on a mail queue. Refuses a STUDENT.

---

## L3-04 · The web side

- **`features/login/password-link/`** — one component, two modes (`activate` / `reset`) from
  route data, because the API is one shape twice. Outside the shell: nobody here has a session.
  Confirm-password field; the API's 12-character floor mirrored only so the message appears
  before the round trip. **410 and 422 are rendered differently on purpose**: a dead link says
  "ask for a new one" and hides the form; a refused password keeps the form and the same link.
  Activation reads the session back through `AuthService.refresh()` and routes by role; a reset
  shows "every device signed out — sign in" and does not sign in.
- **`features/account/change-password`** — inside the shell at `/account/password`; the link
  sits in the titlebar beside Sign out and is shown only when `navKind() !== 'student'`,
  because a student would only meet the 409. Refreshes the session after success so the
  re-issued cookie is read back and the shell never flickers to signed-out.
- **Login:** the two "resets are handled by your mentor" notes are gone; "Forgot password?"
  opens an inline email form that POSTs `/auth/forgot` and shows **the server's words verbatim**
  — they are written to be identical in every case, and rephrasing them client-side is where
  that difference would creep back in. `?verified=1|0` from the confirmation redirect renders
  a banner. `ng build`: initial 176.98 kB, unchanged.

---

## Two things worth recording that are not bugs

- **`_PUBLISHED_PASSWORDS` is unreachable.** Every entry in `set_password.py`'s denylist is
  under 12 characters, so the 12-character floor answers first and the denylist never fires.
  Harmless — the floor is the stronger rule — but a comment that says "refuses the demo
  passwords" is describing a check that cannot run. Left as is; noted so nobody adds a 12+
  entry expecting it to have been working all along.
- **"Corrections Requested" is deferred.** It is a new `RegistrationStatus` value on a **PG
  enum column**, which is `ALTER TYPE … ADD VALUE` with all three of AGENTS.md's enum gotchas
  attached. It deserves its own entry, not a footnote inside this one.

## VERIFY

**Suite, with `REEP_REQUIRE_DB=1` so a skipped suite cannot read as green:**

    785 passed, 2 skipped in 42.60s        (was 764 before this round; +21 tests in tests/test_passwords.py)

**Mutation-tested — 15/15 caught on a real run.** The first run caught 11. The four survivors
were each a lesson, none a false alarm:

- *raw token stored instead of its hash* — the guard test only READ existing rows and created
  none, so every row it could see predated the mutation. A guard has to exercise the writer;
  it now issues a link under whatever code is running and checks that row.
- *consume no longer requires `consumed_at IS NULL`* and *consume ignores expiry* — through the
  endpoints, `peek_user_token` answers first, so consume's own predicates were never the
  deciding check. Under two racing clicks the peek is NOT the arbiter; the atomic UPDATE is.
  Both predicates now have direct tests with no peek in front of them.
- *policy checked after the link is spent* — an **equivalent mutant**, and the interesting one:
  `get_db` never commits, so an HTTPException raised after a consume rolls the consume back
  anyway. The behavioural test passed for a reason that is not the design, and one stray
  `db.commit()` before the policy check would quietly start burning links on typos. The order
  is now pinned textually in `test_policy_is_checked_before_the_link_is_spent_in_the_source`.

**Two verification incidents, recorded because they could recur:**

- **Docker Desktop's engine stopped mid-session** and a mutation run reported 15/15 — falsely.
  Every pytest invocation had failed with the "Postgres not reachable" usage error, and the
  script read any non-zero exit as "caught". The three mutation scripts now treat only exit
  code 1 (tests failed) as a kill; 2–5 (interrupted / internal / usage / nothing collected)
  abort the run with the reason. A green mutation report needs a reachable database, and the
  harness now says so instead of lying.
- **A parallel session is editing this tree.** `c2f7a9d41e63` (CHECK constraints for every
  documented bound, an index behind every foreign key) landed on top of `a8e5c3f17b92` with
  `index=True` on `auth_tokens.created_by_user_id`. I added the same index to my own
  migration, which made the chain create one index twice and drop it twice — the downgrade
  died on the second drop. Reverted: `c2f7a9d41e63` owns that index. The chain now round-trips
  `head -> f7c3a1d92e54 -> head` cleanly, `alembic check` reports no drift on `auth_tokens`,
  and `alembic upgrade --sql` generates the full script. Lesson for anyone in this tree:
  **check `alembic heads` and `git status` before editing a migration you wrote earlier the
  same day** — it may no longer be the head.

`ng build`: initial 176.98 kB, unchanged by the two new lazy screens.


---

# Round 5 — The two console callers that were missing

Item 2 on the remaining-work list. Both writes existed, were tested, and had no Angular
caller: `PUT /api/admin/students/{id}/cohort` (seating a student in a batch) and
`PATCH /api/admin/users/{id}/institutional-identity` (a faculty member's designation and
department — the two columns the BGSCET leave form reads and labels "(synced)"). A tested
write with no caller is a feature that works for `curl` and nobody else.

**What "endpoints done" turned out to omit.** A screen cannot be built on a write alone. To
seat a student the console must be able to list who is in a batch and who is in none, and
neither read existed — the director's `/unassigned-students` is *mentor*-unassigned, a
different fact (a student can have a mentor and no batch, or a batch and no mentor). And to
PATCH a mentor's designation the screen needs the **user** id, which `MentorLoadOut` did not
carry: it could read the two fields and had no way to address the row that holds them —
which is how "(synced)" stayed a promise.

---

## L2-12 · `app/routers/admin.py` — two reads beside the seating write

**Chain of thought.**

1. `GET /admin/cohorts/{id}/students` and `GET /admin/students/unseated`, both DIRECTOR-gated,
   both returning the same `AdminStudentRowOut` — a roster line (name, email, USN, stage,
   cohort), not a record. The console does not need marks to seat someone, and rule 1 has no
   reason to be near this.
2. One `_student_rows(db, *where)` builds both, so the two lists cannot drift in shape, and the
   invariant they exist for is a property of one query with two predicates: **a student is in
   exactly one of them.**
3. `/students/unseated` sits before `/students/{student_id}/cohort` in the file and is a GET
   against a PUT, so there is no route shadowing — noted because the next endpoint someone
   adds under `/students/` will be a GET with an id, and then there will be.
4. The 403 enumeration in the tests gained both — a new endpoint without a line there is a
   visible omission, which is why that list is written out and not walked off the router.

## L2-13 · `app/routers/director.py` — `MentorLoadOut.user_id`

Additive. The query already joined `users` to read `department` and `designation`; it now
selects `Mentor.user_id` beside them. The existing mentor-load test asserts the id is present.

## L3-05 · Institution console — the seating panel

**Chain of thought.**

1. Home: the batch table in `/director/institution`, because seating is an institutional act
   and the batch row is already there with its `student_count`. A **Students** button beside
   Edit opens a panel for that batch.
2. The panel is two lists that must agree with the write: a picker over the unseated pool, and
   the seated table with a Release button per row. Seat and Release are the **same PUT** with
   a batch id or `null` — the API's "explicit null un-seats, absent means nothing" contract,
   surfaced as two buttons rather than one dropdown with a blank option that could be chosen
   by accident.
3. After either write, **both lists and the batch table reload**, so `student_count` moves in
   front of the admin and the student vanishes from one list as they appear in the other.
4. The console's `write()` helper only knew POST and PATCH and always parsed a JSON body; the
   seating PUT answers **204 with no body**. It now accepts PUT and returns `{}` on 204, so
   "null means the write was refused" stays true for every caller.
5. The pool's empty state says *"Every student is already seated in a batch"* in the select
   itself, and the seated list's empty state says nobody is here yet — two different absences,
   two different sentences.

## L3-06 · Mentors & Students — the identity editor

**Chain of thought.**

1. Home: the selected mentor's card on `/director/mentors`, under the capacity chips, because
   that is where designation and department already *render* — a screen that shows a value
   and cannot change it is where the "(synced)" complaint would be raised.
2. Read state: *"Associate Professor · Management Studies"*, or *"Designation not on record ·
   Department not on record"* — words, never a blank, so a missing value reads as a fact about
   the roster rather than a rendering gap.
3. Edit state: two inputs and Save. The draft holds strings, never null: an empty input is
   sent as `""` and the API clears the column itself, so "not on record" and "cleared" are one
   state on both sides — the contract the identity test already pins.
4. After Save the row is **updated in place** rather than re-fetching the whole load screen:
   nothing else changed, and a full refresh would drop the mentor selection.

## VERIFY

**Suite, with `REEP_REQUIRE_DB=1` so a skipped suite cannot read as green:**

    786 passed, 2 skipped in 43.40s        (785 before this round; +1 seating-reads test, and the mentor-load test gained an assertion)

**Mutation-tested — 5/5 caught, after one survivor.** The first run caught 4: the unseated
list no longer filtering on `cohort_id IS NULL`; the batch list ignoring the batch; the 404 on
an unknown batch removed; the two reads admitting a STUDENT. The survivor was *`mentor-load`
sending the MENTOR id in the `user_id` field* — the test asserted only that the field was
truthy, and a mentor id is truthy. That mutation is not academic: the console would PATCH
`/admin/users/{mentor_id}` and 404 on every mentor. The test now requires the value to differ
from `mentor_id` and to resolve to a `users` row. (The harness refuses to score a run where
pytest itself could not start, after round 4's Docker outage.)

`ng build`: initial 176.98 kB, unchanged; the institution chunk grew to 36.87 kB with the
seating panel, lazily loaded as before.


---

# Round 6 — L4 · The core stack in CDK, and the cutover that does not touch the database

Item 3. The standing rule is *CDK, never Terraform*; the standing fear is the one AGENTS.md's
plan recorded: **`terraform state rm` on all 78 resources before deleting the `.tf` files, or a
later apply destroys the database.** This round builds the CDK side and the procedure, and
stops exactly where a human with credentials has to take over. Nothing here was run against
the account.

**One assumption from the earlier plan was wrong and is corrected here.** The `stopTimeout` fix
was going to be 500 s, above the 480 s interview. **Fargate caps `stopTimeout` at 120 s** and
refuses a task definition above it. What actually keeps a live interview socket open through a
deploy is the target group's **deregistration delay** — the ALB keeps a draining target's open
connections until they close, and ECS waits for draining before SIGTERM — so that is 600 s,
with `stopTimeout` at the ceiling and uvicorn's `--timeout-graceful-shutdown 110` inside it.

---

## L4-01 · `infra/cdk/reep_core/stack.py` — the mirror, in two phases

**Chain of thought.**

1. **These resources exist and hold student data.** So this is not a fresh build; it is a
   description of what is running, precise enough for `cdk import` to adopt each resource into
   CloudFormation without changing it. That one fact decided everything below.
2. **Physical names are the Terraform names, literally**, and the synth tests read them out of
   the `.tf` files rather than a copy: `reep-api-task`, `/reep/api`, `reep-postgres`,
   `reep-vault`, the alarm names, the autoscaling policy names. An import matches on the
   identifier and then compares properties; a mismatch on an *immutable* property (a security
   group's name, a task family) is a **replacement** on the next deploy — an outage for the
   database, a lost network for every task.
3. **L1 wherever L2 would invent structure.** `ec2.Vpc` lays out its own subnets;
   `iam.OpenIdConnectProvider` is a Lambda-backed custom resource; `rds.DatabaseInstance` mints
   a new master password. None can be imported over what exists. Network, security groups,
   EFS, RDS, backup, ECR, OIDC and the scheduler are L1; ECS, ALB, CloudFront, alarms, roles
   and buckets are L2 where they map 1:1.
4. **Two phases, one context key.** `phase=import` renders the mirror and nothing else,
   because `cdk import` refuses a template whose diff adds resources it cannot adopt (and
   custom resources are exactly that — which is also why the WAF's ARN and the DR vault's ARN
   are plain context strings, not cross-region references). `phase=harden` is the same stack
   plus the fixes. A test pins that the import phase is a strict subset under the same logical
   ids, so the second deploy updates in place and replaces nothing.
5. **`MasterUserPassword` never appears.** The instance keeps the password Terraform generated,
   held inside the app secret's `DATABASE_URL`; writing one here would rotate it under the
   running api. A synth test refuses the template if the property ever shows up, in either
   phase. The two secrets are imported by ARN and never versioned — a CloudFormation deploy
   must not be able to overwrite the operator's values.
6. **`DeletionPolicy: Retain` on everything** (an aspect; the database is `Snapshot`). Import
   *requires* it, and after the cutover a construct rename or a `cdk destroy` typed into the
   wrong terminal must forget the data, never delete it.
7. **What harden adds, and why each is a real change**: the deregistration delay above; the
   service at 100/200 so the old task lives until the new one is healthy; **one retention
   number** for RDS automated backups, the daily rule, the DR copy and the vault lock (they were
   14 and 35 — two answers to "how far back can we go", and the honest one was the shorter);
   the vault **locked in governance mode** (compliance mode is irreversible and opt-in); every
   recovery point **copied to ap-southeast-1**; a **weekly restore test**, because a backup that
   has never been restored is a hope; Multi-AZ on by default with the cost written down; the
   task role allowed `ses:SendEmail` for the college's verified identity only, from the
   configured sender only; a `reep-backup-job-failed` alarm.

Four Terraform resources are dropped on purpose — the two `random_password`s and the two
secret versions — because their values already live in the secrets and the generators are not
infrastructure. Six collapse into bucket properties.

## L4-02 · `edge.py`, `dr.py`, `app.py`

The WAF must be in us-east-1 and the copy target must be in another region, and a
CloudFormation stack is one region: three stacks, one app. The DR vault carries the same
minimum retention lock as the primary, so the copy cannot be quietly shortened either.

## L4-03 · `tests/test_core_synth.py` — the guards the cutover leans on

Both phases synthesise; the import phase contains no custom resource, no Lambda, and is a
subset of harden; no master password; every physical name in the `.tf` files is in the
template; the alarm set is identical to Terraform's; the network is the Terraform layout down
to the four `/20`s; `stopTimeout == 120`; deregistration ≥ 570; 100/200; one retention number
in four places; governance lock by default; the DR copy action; the restore test; Multi-AZ on
with the opt-out working; the SES statement scoped to identity and sender; secrets referenced
never written; everything retained. 39 tests, no AWS.

**And one on the api's side.** `tests/test_codebase_guards.py` now reads
`DEREGISTRATION_DELAY_SECONDS` out of the CDK file and asserts it exceeds
`settings.nova_sonic_connection_seconds + 90` — the two numbers live in different languages in
different directories, and this is the one place they are compared. A second guard pins
`stopTimeout ≤ 120`, so the next person who "fixes" it upward meets the Fargate ceiling in a
test instead of at `RegisterTaskDefinition`.

## L4-04 · `tools/import_map.py` and `tools/terraform_release.sh`

**The identifiers are read from the Terraform state, never typed.** Two dozen of them, several
random suffixes Terraform chose (`name_prefix`, `bucket_prefix`). One typo in a security-group
name is a group CloudFormation cannot find; one typo in the database identifier is an import
that adopts nothing and a first deploy that tries to create `reep-postgres` beside the real
one. The tool refuses on any unmapped resource, because a half-imported stack is one whose
next deploy creates the other half twice.

The release script backs the state up first, removes every address in **one** `state rm` so
a half-run cannot leave Terraform managing half a VPC, then runs `terraform plan` — which must
propose to *create* everything, the proof Terraform has forgotten. It refuses to run without a
flag whose name says what must already have happened.

## L4-05 · `docs/cdk-cutover.md` — the runbook

Ten steps, a rollback at each, and the two rules at the top. The order is **import → prove
the diff is empty → release → delete the files → harden**, and step 5 — `cdk diff` printing
"no differences" while Terraform still owns everything — is named as the last point at which
nothing has happened. The WAF is imported first as a one-resource rehearsal. Multi-AZ
conversion is the one harden change with a performance dip, so the runbook says to run it at
night IST.

## L4-06 · `cdk-deploy.yml`, `cdk.json`, `.gitignore`, README

The workflow gains a `stack` selector (`voice-platform | core | edge-waf | dr-vault | all`).
**`cdk import` is deliberately not in it**: it is a one-time, human-attended step. `cdk.json`
carries the harden defaults in the open. `tf-state.json` is gitignored because
`terraform show -json` includes secret values. The README no longer says the core stack "stays
in Terraform".

## L4-07 · The council's review, and what it changed before anyone ran anything

The runbook and the stack went to an adversarial reviewer with one brief: find every way this
destroys data, breaks the running service, or leaves the account half-owned. It resolved the
CloudFormation registry schemas rather than trusting the docs. **Four blockers, eight must-fixes,
and every one is now in the code or the runbook.** The ones that would have hurt:

- **Eight of thirty import identifiers were wrong.** Composite keys collapsed to one part
  (`AWS::EC2::Route` needs `RouteTableId|CidrBlock`; `ScalableTarget` needs three parts;
  `ECS::Service` needs the cluster too), a name where an ARN was required (CloudFront
  `Function`), and `BackupSelection`'s id, which the provider composes as `<plan>_<selection>`.
  CloudFormation validates these before adopting anything, so the failure was a runbook that
  stops at step 4 — not data loss — but a runbook that stops is not a runbook. The table is
  now the registry's, every part of a composite is emitted, and the runbook cross-checks the
  keys with `get-template-summary`, the only authoritative source.
- **The L2 constructs added three resources that exist nowhere** — and one was a hole. The ALB
  listener wrote a `0.0.0.0/0:80` ingress on the imported security group (the exact rule the
  WAF exists to prevent); the service attachment wrote a duplicate `:3300` rule; the secrets
  helper attached a generated IAM policy. The tool refused them, which was correct, but the
  obvious "fix" — gate them out of the import phase — would have had harden *create* them: EC2
  accepts the open rule and rejects the duplicate, so the stack rolls back mid-deploy while
  converting RDS to Multi-AZ. Fix: imported groups are `mutable=False`, listeners are
  `open=False`, the execution role is `without_policy_updates()`, and a test asserts the
  import template contains no `SecurityGroupIngress` and no `IAM::Policy`.
- **The database's `Snapshot` policy made the documented rollback a `DeleteDBInstance`.**
  "Snapshot" means delete-after-snapshot; `delete-stack` would have called it, held off only by
  `DeletionProtection` — one toggled property from a new endpoint and an invalid
  `DATABASE_URL`. It is `Retain` now, and the test that pinned `Snapshot` pins `Retain`.
- **`cleanup-orphans.sh` deleted the production cluster, service, log group and roles by
  name**, and after the state release *everything* looks like an orphan to Terraform. Removed.
- **`cdk.json`'s harden targets leaked into the import mirror.** The CLI always loads
  `cdk.json`, so the import phase rendered `MultiAZ: true` against a single-AZ instance;
  import does not compare properties, so it would have succeeded, and harden — carrying the
  same value — would have sent no change. **Multi-AZ would silently never have happened**,
  with `cdk diff` reporting nothing. The import phase now reads `live*` keys the tool writes
  from the state, and the test fixture loads `cdk.json` the way the CLI does.
- **Step 5 could not be satisfied as written.** `cdk import` strips outputs and metadata from
  what it stores, so "no differences" never prints; and drift detection returns `NOT_CHECKED`
  rows, so "only `[]`" was unattainable. The step now names the expected residue, filters to
  `MODIFIED|DELETED`, and lists the four known-harmless differences — anything else is a mirror
  error, fixed in the template, never by deploying.
- **The harden deploy rolled the service itself** and sent `DesiredCount: 2` (autoscaling
  owns it; a busy day would have scaled back down), and the `services-stable` waiter in
  `deploy.yml` would have timed out on every future deploy — its cap is 600 s, which is
  exactly one drain. `DesiredCount` is gone, harden is two deploys (`hardenEcs=false` first),
  and the waiter is a `describe-services` poll capped at 25 minutes.
- Smaller: security-group and subnet-group descriptions are Terraform's create-only default
  ("Managed by Terraform"); CloudFront IPv6 stays off; the import mirror keeps
  `ManagedBy=terraform` and the EIP keeps it forever (a tag update may reassociate the
  address); the edge ACL is `Retain`; the tool derives the TLS shape, task sizes, instance
  class and container environment from the state rather than from a `tfvars` nobody can read
  in CI; `core` is removed from the browser workflow's choices until the cutover; the release
  script checks `IMPORT_COMPLETE` before touching the state; three backup alarms instead of one.

**What the review confirmed correct, in one line each:** 22 of the 30 identifiers; import has
no side effects on ECS, CloudFront or the database; `stopTimeout` 120 is Fargate's maximum
and the graceful-shutdown flag fits inside it; the ALB keeps in-flight WebSockets through the
600 s delay and ECS waits for `UNUSED` before SIGTERM; no secret, version or master password
anywhere; harden forces no replacement other than the task definition's expected revision.

## VERIFY

**Nothing was run against AWS.** There are no credentials here, and the runbook's first
executable step is a human's. What is proven is the code and the procedure:

| Check | Result |
|---|---|
| CDK synth guards, both phases, three stacks, no AWS | 58 passed |
| api guards, including the two infra guards | 15 passed |
| api suite, `REEP_REQUIRE_DB=1` | 788 passed, 2 skipped |
| council review of runbook + stack + tools | 4 blockers, 8 must-fixes — all applied |

The synth guards now also assert: the import phase carries the live database values with
`cdk.json` loaded; no generated ingress or IAM policy exists in it; every type in it has a
registry identifier; `DesiredCount` is never sent; the ECS half of harden can be held back;
retention above 35 is refused; descriptions and tags mirror Terraform; all three stacks retain
everything; three backup alarms; an existing OIDC provider is referenced, not redeclared.

## L4-08 · Step 0 as one command, and the rehearsal that needed no account

**The ask:** "fix that blocker" — the cutover needing a human with credentials, an
afternoon, and two attended hours. Only the credentials are the user's to supply; everything
else about that blocker turned out to be fixable from here.

**What this machine had, checked step by step:** no `aws`, no `terraform`, no `cdk` CLI, no
credentials, no `backend.hcl`, no `prod.tfvars`, and a runbook written for a Unix shell
(`.venv/bin/activate`). The GitHub deploy role was examined as an alternative path: it holds
ECR, ECS, S3 and CloudFront rights only, nothing for CloudFormation, RDS or the state bucket,
so a browser-run cutover is impossible by design and the laptop path is the only path.
Terraform 1.15.8 (winget), aws-cdk 2.1140.0 (npm) and AWS CLI 2.36.40 (winget, after the user
accepted the elevation prompt the first attempt had cancelled) are now installed for the
user. A pip `awscli` briefly put in the venv was removed: with the venv active it shadows v2.

**`prod.tfvars` never existed.** `docs/aws-deployment.md` §3 shows the first apply run with
`-var` flags. Step 0 said `terraform plan -var-file=prod.tfvars` MUST exit 0 and nothing said
where the file comes from; without the applied values the plan proposes to undo the
certificate, the domain and the alert address, and the cutover stalls on its first check.
The state does not store variable values, but every one lands in an attribute of a resource
the state does hold — so `tools/tfvars_from_state.py` reads them back (the certificate from
the 443 listener, the OIDC subjects from the deploy role's trust policy, the observer
principal from its own, the container environment, the sizes, the retention) and refuses to
write anything it cannot source. A test pins its key set to the `variable` blocks in
`infra/aws/*.tf`, so a variable added without a rule fails CI rather than the plan.

**`tools/cutover_preflight.sh`** is step 0 as one read-only command: tools → repository
hygiene → synth guards and a full `cdk synth` → identity (account 445363794125) → the three
bootstraps → the three stacks' existence → `terraform init` / `show` / tfvars / `plan
-detailed-exitcode` → RDS deletion protection and the pre-cutover snapshot. It stops at the
first thing only a human can provide and says exactly what. A test pins that every `aws`
call in it is a describe/get/list/head and that terraform is never applied; the printed
snapshot command is a message line, which the test excludes.

**The rehearsal, `tests/test_cutover_tools.py`, found four blockers no guard had reached.**
It builds a synthetic `terraform show -json` shaped like the account and runs the tools in
the runbook's order, then re-synthesises from the merged context and demands a fixed point.
Writing it surfaced, in order:

1. **The runbook synthesised before running the tool.** A context-free synth renders one
   plain-HTTP listener; the live ALB has two. The listener rule matches on port and action,
   so the 80/forward template listener matched nothing in a TLS state and the tool refused —
   at step 2, with credentials in hand. `import_map.py` is now two passes: the context from
   the state alone, then the synth, then the map against that template. The runbook test
   pins that order.
2. **`githubOidcProviderArn` was written whenever the provider existed** — the default — and
   the stack references instead of declares when that key is set, so the re-synth dropped
   the resource the map had just listed. It is written only when the provider is NOT in the
   state, read from the deploy role's `Federated` principal.
3. **The TLS branch of `stack.py` did not synthesise.** `SslPolicy.TLS13_12` is not a member
   of the library; Terraform's `ELBSecurityPolicy-TLS13-1-2-2021-06` is `RECOMMENDED_TLS`.
   None of the 58 guards had ever set a certificate, so the crash waited for the live
   context. A new guard reads the policy out of `alb.tf` and synthesises the branch.
4. **Eight mapping rules named logical ids that are not in the template.** The roles and
   the buckets are L2 constructs and render as `BackupRoleF43CFD90`; `put("BackupRole", …)`
   was a `KeyError` waiting for step 2. The rules now resolve by the `RoleName` /
   `BucketName` property, and `put` reports an unknown id instead of crashing.

Also: the voice-platform stack is a warning, not a failure (only step 10 and CI deploys need
its grant, not the import); the `.tf`-reading guards carry `requires_terraform` and skip once
step 7 deletes the files, so the cutover commit stays green instead of failing the guards
that proved it safe.

**Mutation checks** — each original bug reintroduced, its guard red, the file restored:
M1 `TLS13_12` → 1 failed; M2 a real but wrong policy (`TLS12`) → 1 failed; M3 the ARN written
unconditionally → 2 failed; M4 a role by plain logical id → 3 failed; M5 a tfvars rule
dropped → 1 failed; M6 a mutating `aws` call in the preflight → 1 failed. Suite after
restore: 74 passed.

## VERIFY

**Still nothing run against AWS.** The preflight ran on this machine and stopped, by
design, at the credentials check:

| Check | Result |
|---|---|
| CDK suite: 58 synth guards + 1 TLS guard + 15 cutover-tool tests | 74 passed |
| api guards (`tests/test_codebase_guards.py`, AGENTS.md paths included) | 15 passed |
| `bash -n` on both shell scripts | clean |
| `tools/cutover_preflight.sh` on this machine | 9 ok, 1 failure: "no usable AWS credentials", exit 1 |
| mutation checks M1–M6 | all red, all restored |


## L4-09 · The cutover, actually run: step 0 through step 2 against the live account

**The ask:** "if you can do without my intervention, go ahead and do it." Everything up to the
credentials was already fixed in L4-08; the user then signed in as account root of 445363794125 and
authorised the `terraform`/`cdk` commands. This entry is what the runbook met when it finally touched
a real account — because every one of the four things it hit is a thing the offline rehearsal could
not have contained.

**The preflight was right to exist, and its first real run failed four checks.** Two were true (the
`us-east-1` and `ap-southeast-1` bootstraps did not exist, and `cdk bootstrap` created them); one was
the preflight's own bug; one was the real work.

**M1 · Git Bash rewrote an SSM parameter name into a Windows path.** `ap-south-1` reported as *not
bootstrapped* while its `CDKToolkit` stack sat at `CREATE_COMPLETE` with `BootstrapVersion: 32`. MSYS
converts any argument that looks like a POSIX path before a native `.exe` sees it, so
`/cdk-bootstrap/hnb659fds/version` arrived as `C:/Program Files/Git/cdk-bootstrap/hnb659fds/version`
and read back `ParameterNotFound`. Three bootstrapped regions would have been reported as three
missing ones, and the operator's fix — re-running `cdk bootstrap` — would have looked like it worked.
`export MSYS_NO_PATHCONV=1` at the top of the preflight, and a second branch that says so explicitly
when a `CDKToolkit` stack exists but its parameter does not.

**M2 · The 78-resource count was never a check.** `terraform state list` returns 87 addresses, five of
them **data sources**, which are not resources and cannot be imported. The count warned about a number
that was never wrong. Replaced with a count of managed addresses only, and the comment now says what
the real check is: `import_map.py` refuses anything the template needs and the state lacks.

**M3 · The state was stale in three ways, and the three resolutions are different.** This is the part
worth reading, because it will be true on any account that a human or a CI job also edits:

- *Reality changed, the config did not.* On 2026-09-02 someone used the console to give the
  distribution the alias `reep.sast-skills.com`, an ACM certificate and TLS policy `TLSv1.3_2025`.
  `terraform apply -refresh-only` records that into the state and changes nothing in AWS. It also
  fixes `prod.tfvars`, because `tfvars_from_state.py` reads the state: the first run produced
  `domain_name = ""` — not a tool bug, a stale input.
- *A resource was replaced outside Terraform.* CI had registered task definition revision 4 and the
  service runs it; the state held revision 3. `state rm` + `import`. An imported task definition then
  returns AWS's populated form (auto-named port mappings, empty default lists,
  `configure_at_launch`), none of which the source spells out, so Terraform proposed to **replace it
  and roll the service** to normalise defaults. `ecs.tf` now ignores exactly that noise.
- *A value the provider cannot express.* No aws provider 5.x accepts `TLSv1.3_2025`; 5.100 validates
  against a list ending at `TLSv1.2_2021`, so the plan **errored** rather than drifted. `cdn.tf`
  ignores that one attribute. The CDK mirror renders `TLS_V1_3_2025`, which CloudFormation does
  accept — so the mirror is the more truthful of the two records, and the ignore is what keeps
  Terraform from lying about it in the meantime.

**One change was applied on purpose, through a saved plan.** The nightly retention schedule still
targeted revision 3. That is not cosmetic for this cutover: the mirror renders the schedule's target
as a `Ref` to the imported task definition, so leaving it would have produced a fifth drift row at
step 5 — one the runbook does not list, which is exactly how a real mirror error gets waved through.
`terraform plan -out=` then `apply <file>`, so what was reviewed is what ran: one resource,
in-place, `0 added, 1 changed, 0 destroyed`. `terraform plan -detailed-exitcode` then exited **0**
for the first time.

**M4 · `cdk synth` emits a resource the tests never produce.** `import_map.py` refused the first real
template: `CDKMetadata: type AWS::CDK::Metadata is not in IDENTIFIERS`. The synth guards build their
template with `Template.from_stack()`, which does not append the CLI's base64 analytics resource. So
the rehearsal passed and the tool failed on the real thing — the very failure mode L4-08 was written
to remove, one layer deeper. It is skipped now (`NON_RESOURCE_TYPES`), not suppressed at synth, so
the tool is right however the template was produced; `cdk import` strips it too, which is why the
runbook already predicted `[+] CDKMetadata` in the post-import diff.

**M5 · The step-2 cross-check, as written, told the operator to break a correct table.** The runbook
said to compare `get-template-summary`'s `Keys` against `import-map.json` **by eye**. CloudFormation
returns a *composite* primaryIdentifier as ONE comma-joined string —
`["ResourceId,ScalableDimension,ServiceNamespace"]`, one element, not three — while the map correctly
sends three separate keys. Compared by eye, **all seven composite types read as mismatches**: the ECS
service, both scaling policies, the scalable target, the metric filter, the IGW attachment, the EIP
and both default routes. The first run of that comparison reported 16 problems, every one of them
false, to the person who had written the table. `tools/check_identifiers.py` now does it — it fetches
the summary for the exact template (staging it in the CDK assets bucket, since it exceeds the
51,200-byte inline limit), splits composite keys, and checks coverage both ways. Against the live
registry: **65 resources, 39 types, every identifier matches.**

Both incidents became tests, per the standing rule that a problem faced once must not recur:
`test_the_cli_analytics_resource_is_skipped_not_refused` (which also proves a genuinely unknown type
is still refused — the skip is an exemption for CDK's bookkeeping, not a hole) and
`test_a_composite_identifier_arrives_comma_joined_and_must_be_split`.

**Where this stopped, and why.** At the gate before `cdk import`. Steps 0, 1 and 2 are complete and
proven; nothing has been imported, no state released, nothing hardened. The last act before touching
CloudFormation was to put the whole position — the mirror, the map, the reconciled state, today's
diff and the unrun steps — through an adversarial review, because import is the first step that
writes a stack over 66 live resources holding student records.

## VERIFY · Round 6, second half

| Check | Result |
|---|---|
| `terraform plan -var-file=prod.tfvars -detailed-exitcode` | **exit 0** — "No changes. Your infrastructure matches the configuration." |
| Manual pre-cutover snapshot `reep-postgres-pre-cdk-20260907` | available, 100% |
| CDK bootstrap, all three regions | present (two created today) |
| `tools/import_map.py` passes 1 and 3 | context 31 keys; **65 resources mapped** |
| `tools/check_identifiers.py` against the live registry | **every identifier matches**, 39 types |
| CDK suite | 77 passed |
| api guards | 15 passed |
| Mutation: remove the `NON_RESOURCE_TYPES` skip | its guard goes red, restored |
| Changed in AWS so far | one EventBridge schedule target, revision 3 → 4 |

## L4-10 · The pre-import review, and the four things it stopped

Before `cdk import` wrote a CloudFormation stack over 66 live resources holding student records,
the whole position — the mirror, the map, the reconciled state, the day's diff and every unrun step
— went through an adversarial review: six independent dimensions, each finding then put to three
verifiers whose instruction was to REFUTE it. It raised **36 findings**. The session's usage limit
killed 92 of the 114 agents mid-flight, so most findings came back *unverified* rather than
refuted — and the scoring silently dropped those, which is itself worth recording: a finding whose
verifiers all died is not a refuted finding. The raw journal had to be read by hand to recover them.

**Four would have done real damage. Two would have failed the import outright.**

**B1 · `AttachmentType: "internet"` — the import would have failed.** Almost every identifier value
is READ from the state; two are *composed* by the tool, and both were composed wrong.
`AWS::EC2::VPCGatewayAttachment.AttachmentType` is read-only and Terraform has nothing to read it
from (it models the attachment as `vpc_id` on the gateway), so it was written by hand as
`"internet"`. CloudFormation answers `Invalid Attachment Type 'internet'`. The live identifier is
`IGW|vpc-…`.

**B2 · The backup selection's composite was backwards.** `<BackupPlanId>_<SelectionId>`, when the
registry wants `<SelectionId>_<BackupPlanId>`. CloudFormation answers `Cannot find Backup plan with
ID`. **This one was not found by a reviewer** — it was found by the gate built to answer the
reviewer's *other* finding, which is the point of building gates.

**B3 · The mirror said `MinimumHealthyPercent: 50`; the live service is 100.** Written as
`100 if harden_ecs else 50` on the assumption that 50 was live and 100 the improvement. `ecs.tf`
never sets the property, so 100 is the ECS default and the refresh recorded it. It would have been
an unlisted drift row at step 5 — the checkpoint whose entire job is to come back empty — and step
9a would have pushed the live service *down* to 50 for the length of the Multi-AZ conversion.

**B4 · Step 9a was not "the database half".** The split exists so an ECS circuit-breaker rollback
cannot undo a Multi-AZ conversion in the same update. It did not work. The harden phase flips every
resource's `ManagedBy` tag, and **a task definition is immutable** — tagging it registers a new
revision, which the service rolls onto. Synthesising all three phases showed 9a modifying **46
resources**, the task definition, service and target group among them: the API would have rolled in
the same CloudFormation update as the database conversion. The tag flip on those three now waits for
9b. 9a's diff shows them unchanged.

**The gate that came out of it.** Finding 9 said `check_identifiers.py` validated identifier key
NAMES and never VALUES, so no gate in the runbook could catch a wrong value. True — and the fix
found B2 within a minute. It now resolves all 65 identifiers through Cloud Control
`get-resource` before anything is adopted. Finding 29 said today's own preflight edit had removed
the only state→template coverage check; also true, so `import_map.py` now tracks which addresses it
read and **refuses** on any managed address that is neither mapped nor named in
`UNMAPPED_ON_PURPOSE` — a table that, for the first time, writes down that the two Secrets Manager
secrets are left unmanaged *on purpose* and why owning them would be worse.

Four more were fixed in the mirror rather than tolerated as drift, because a short drift table is
what makes step 5 provable: the HTTP redirect's `Host`/`Path`/`Query`, the web OAC's `Description`,
tags on the CloudFront function (live has none — the provider cannot set them), and B3.

**And one finding was about a comment I had written that afternoon.** `ecs.tf`'s new `lifecycle`
block said "the values are this config's; only the noise is ignored". `ignore_changes` cannot reach
inside a jsonencoded string: ignoring `container_definitions` ignores the environment and the
secrets too. The block stays — step 7 deletes the file within the hour — but the comment now says
what it actually costs.

Every incident became a guard: `test_the_two_identifier_values_that_are_written_rather_than_read`,
`test_the_service_keeps_both_tasks_in_every_phase`,
`test_the_database_half_does_not_touch_the_ecs_trio`,
`test_every_managed_address_has_a_home_or_a_written_reason`, and
`test_a_composite_identifier_arrives_comma_joined_and_must_be_split`. The tautological assertion the
review found in the registry cross-check test is gone; that test now pins detection, and says in its
docstring that the registry can only be checked online.

## VERIFY · Round 6, third pass

| Check | Result |
|---|---|
| CDK suite | **81 passed** (was 58 this morning) |
| api guards | 15 passed |
| Mutations: B1–B4 and the coverage check each reintroduced | every guard went red, all restored |
| `tools/check_identifiers.py` — names **and** all 65 values against the live account | every identifier matches the registry and resolves |
| `tools/import_map.py` coverage | 82 managed addresses: 62 mapped, 20 named in `UNMAPPED_ON_PURPOSE`, 0 unaccounted |
| Mirror vs the **live AWS API** (not the state) on the data plane | every property the mirror sets matches; the three it omits are AWS defaults |
| Phase diff: does 9a touch the ECS trio? | no — byte-identical to the import mirror |
| `terraform plan -detailed-exitcode` | 0 |
| Changed in AWS | still only the one EventBridge schedule target |

---

*Entries continue as each file is written.*

## L4-11 · Step 3 ran, and failed in a way nothing offline could have found

`cdk import reep-edge-waf` — the rehearsal, one resource, one region, chosen precisely so that
whatever goes wrong goes wrong here — was refused by CloudFormation:

    As part of the import operation, you cannot modify or add [RoleArn, Tags]

**Nothing changed. The stack was not created.** That is the property the whole design leans on:
CloudFormation validates an import change set before it adopts anything.

**The cause is invisible in the template.** `Tags.of(stack).add(...)` tags the Stack itself as well
as its resources, and CDK passes a tagged stack's tags to `CreateChangeSet` as **stack** tags, which
an IMPORT change set refuses. The rendered template is byte-identical either way — the difference is
in `cdk.out/manifest.json`, and therefore in the API call. No synth assertion could have seen it,
and `reep-core` carried exactly the same stack tags: **all 65 resources would have failed the same
way at step 4.**

The fix is to apply the tag aspects to the stack's CHILDREN, last, once every construct exists. The
stack goes untagged in the manifest; 42 of the 66 resources still carry their tags; the template is
unchanged. `test_no_stack_level_tags_in_any_phase` asserts the stack is untagged in every phase for
both stacks the cutover imports, *and* that resources still carry tags — a guard that only checked
the first half would pass on a stack that had lost its tagging entirely.

Found in the same pass and fixed with it: `edge.py` tagged the ACL `ManagedBy=cdk` unconditionally,
the one place the core stack's import-mirror tag discipline was not applied, which would have put a
property difference into the rehearsal diff whose only job is to be empty. The WAF's rule *array
order* differs from the state export and is **not** a difference — the priorities (1 aws-common,
2 aws-bad-inputs, 3 rate-limit) match exactly, and WAF evaluates by priority.

Retried, the import succeeded: `reep-edge-waf` now owns the ACL. The session's AWS credentials
expired moments later, before the post-import diff could be read.

**State at this point.** Steps 0, 1, 2 and 3 complete. `reep-core` has NOT been imported, no
Terraform state released, nothing deployed. The only changes to AWS all day are one EventBridge
schedule target (revision 3 → 4) and the adoption of the WAF into a CloudFormation stack that
changed no property of it.

## L4-12 · Steps 3, 4 and 5 are done. The mirror is proven.

**Step 3, the rehearsal, retried and complete.** `reep-edge-waf` is `IMPORT_COMPLETE`. The diff
afterwards is the description, the bootstrap parameter and the `WebAclArn` output — the three things
`cdk import` strips — and **no resource property difference at all**. The live ACL still reports
capacity 902, its three rules in priority order and `DefaultAction: Allow`, and CloudFront is still
associated with it.

**Step 4, the core import, needed one retry for a reason worth writing down.** The first attempt
died mid-flight on `getaddrinfo ENOTFOUND ap-south-1.signin.aws.amazon.com` — a DNS blip while the
CLI refreshed its login token — and left the stack in `REVIEW_IN_PROGRESS` with **zero resources**
and a change set already `CREATE_COMPLETE` / `AVAILABLE`. That intermediate state is worth knowing:
CloudFormation had already **validated all 65 identifiers** and built the change set; only the
execute step was lost. Inspecting it was the best pre-flight evidence available anywhere in this
procedure:

    Changes: 65     Actions: 65 x Import     Replacements: none

So it was executed directly rather than rebuilt. `reep-core` is `IMPORT_COMPLETE` with **65
resources adopted**, every one reporting `UPDATE_COMPLETE`. Throughout, the service stayed `ACTIVE`
at 2/2 with `rolloutState: COMPLETED`, both target-group targets `healthy`, and the database
`available`.

**Step 5, the proof.** Two independent checks, and this is the step the whole design exists for.

*The template diff*: `[+]` on the description, the `BootstrapVersion` parameter and the nine
outputs, **and not one resource property change**. Exactly what the runbook predicted.

*Drift detection*, which compares the template to reality rather than to the stored record:
**60 `IN_SYNC`, 3 `MODIFIED`, 0 `DELETED`.** Two of the three are the rows the pre-import review
predicted and the runbook now lists — `reep-voice-platform` owns an inline policy on `reep-api-task`
and another on `reep-github-deploy`, confirmed by `list-role-policies` returning
`invoke-nova, voice-platform` and `deploy-api-and-spa, deploy-cdk-stacks`. The third is
`ApiTaskDef` `MountPoints/0/ReadOnly`: the mirror renders `false`, the live definition omits it, and
those mean the same thing — CDK's `MountPoint` requires the field and AWS defaults it. It resolves
itself at 9b, which registers a new revision regardless.

**Four rows the runbook predicted came back `IN_SYNC`** — the two bucket policies, the ECR lifecycle
JSON whitespace and `Db.EngineVersion` — so the table has been cut to what actually happened. The
mirror is more faithful than its own author expected, which is the return on fixing four fidelity
findings in the mirror rather than tolerating them as drift.

The stack's headline status reads `DRIFTED` because `DriftedStackResourceCount` is 3. That is not a
failure and the runbook now says so: two of the three belong to another stack and the third is a
default. What had to be empty was `DELETED`, and it is.

**Terraform still owns everything.** Nothing has been released; no `.tf` file deleted; nothing
deployed.

## L4-13 · Steps 6 and 7: Terraform released, and the files deleted the same minute

**Step 6.** `tools/terraform_release.sh --i-have-run-cdk-import` checked that `reep-core` was
`IMPORT_COMPLETE`, pulled `infra/aws/terraform.tfstate.before-release.20260907T142149Z` (184 KB —
**that file is the undo and must be kept**), and removed **82 managed addresses** in one
invocation. Two edits went in first: data sources are now filtered out (`terraform state list`
returns 87 here, five of them `data.*`, which are not resources and are re-read on every plan), and
the interactive confirmation can come from `RELEASE_CONFIRM` so the step can be driven
non-interactively — carrying the same word, so it is no easier to trip over.

The proof is the plan that follows: **`Plan: 82 to add, 0 to change, 0 to destroy`**. Terraform no
longer knows these resources exist. **That plan must never be applied** — it would build a second
VPC, a second database and a second everything.

**Which is why step 7 followed immediately.** The window between the release and the file deletion
is the most dangerous in the whole procedure: the configuration still exists, the state is empty,
and a single `terraform apply` duplicates production. `git rm` on the fifteen `.tf` files,
`backend.hcl.example` and `dev-setup-terraform.sh` closes it. `bootstrap-state.sh` stays (the state
bucket still holds the backup) and so does `grant.sh` (unrelated). `terraform plan` now answers
"Backend initialization required" and can do nothing at all.

The `.tf`-reading guards skip themselves as designed — **61 passed, 22 skipped** — and the mirror is
proven from here on by the import and the drift report instead, both of which are recorded above.

Three stale references were corrected in the same commit, because each was an instruction that would
now mislead: `deploy.yml`'s one-time setup pointed at `terraform output`, which is replaced with the
`reep-core` stack outputs; `ci.yml`'s `cdk` job described gating against `infra/aws/*.tf`, which is
replaced with what it actually gates now; and `docs/aws-deployment.md` carries a banner marking its
Terraform commands historical while keeping the account's design rationale, which is still accurate
and still the best description of what each resource is for.

**`cdk-deploy.yml` still does NOT offer `core`, and the reason has inverted.** It was excluded so a
browser click could not create a second VPC before the import. That risk is gone. The risk now is
that the workflow runs a bare `cdk deploy reep-core` — the FULL harden, the database conversion and
the ECS roll in one update — which is exactly what step 9 splits apart. It goes back after 9b.

## L4-14 · The `reep-api` DSN arrived with Sentry's install snippet, and the snippet's one line that must not land

**Step by step.** Sentry's onboarding page handed over a DSN with its stock recipe — `pip install
"sentry-sdk"`, `sentry_sdk.init(dsn=…, send_default_pii=True)`, and `1 / 0` to verify. The first
two are already true here: `sentry-sdk[fastapi]==2.68.1` is pinned in `requirements.txt`, and
`app/main.py:61-89` initialises it at import time behind `SENTRY_DSN`. The only line the snippet
would add is the one this codebase refuses. `send_default_pii=True` attaches cookies — the
`reep_session` JWT — request headers and the client IP to every event;
`test_sentry_never_ships_local_variables_or_request_bodies` exists because the two flags beside it
were once found shipping a student's transcript. `main.py` was not touched.

**Which project, checked rather than assumed.** The org `bgs-college-of-engineering-and` (US region)
was read through the Sentry MCP. Five projects: `reep-api`, `reep-web`, `reep-interview-worker`,
`reep-scheduled-jobs`, `node`. The DSN is `reep-api`'s single key, `Default`. The playbook's proposed
slugs are real; the two extra projects are unplanned and are now recorded there for someone to
decide what they are for.

**Where it goes, and why it is not there yet.** The `reep/external-…` secret was read — key names
only, values never entered the session: `SENTRY_DSN` present and blank, beside `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `OPENAI_API_KEY` and `VOICE_WORKER_SECRET`. A `PutSecretValue` that fills
that one key and preserves the other four was prepared and **refused by the session's permission
classifier**; so was the snippet's verify step, a scratchpad script sending one `ZeroDivisionError`
with this repo's three flags. **Production is unchanged.** Both are operator actions now: Secrets
Manager → `reep/external-…` → Retrieve secret value → Edit → `SENTRY_DSN`, then a redeploy, because
ECS resolves secrets when a task starts. `gh secret list` on `darshani8/reep-` returns nothing, so
`WEB_SENTRY_DSN` is unset too and the SPA still ships without telemetry.

**What did change.** `apps/api-py/.env.example` gains the block the playbook's settings table flagged
as missing: `SENTRY_DSN=""` with the production path, the tracing test that skips while it is set,
and the warning against `send_default_pii=True`; `#SENTRY_TRACES_SAMPLE_RATE="0.2"` beside it.
`docs/sentry-playbook.md`: the two settings-table cells and the "cannot be checked from here"
paragraph now say what was checked and when.

**One incident, worth the paragraph.** The first insert ran `sed -i` with a scratchpad variable that
was empty in that shell: the `r` file did not exist, so nothing was inserted — and MSYS `sed -i`
still rewrote the file, stripping every carriage return. The index blob is mixed (roughly 389 CRLF
lines and 21 LF), `core.autocrlf` is `true`, and git leaves such a file alone only while the working
copy still matches it — so `git diff --stat` went to 389/389 before one intended line had landed, and
stayed there after the second insert. `git show HEAD:…` gives the original bytes; splicing the block
in with the neighbouring line's own ending (CRLF) brought the diff to `19 insertions(+)`, no
deletions, and `git ls-files --eol` back to `i/mixed w/mixed`. Lesson, now in memory: on this
checkout, check `git ls-files --eol` and `git diff --stat` after every in-place edit, not after the
last one.

## VERIFY

`pytest tests/test_codebase_guards.py tests/test_tracing.py -q` — 19 passed, 0 skipped (the
inert-without-DSN test ran, because no `.env` exists locally). `git diff --stat
apps/api-py/.env.example` — `19 insertions(+)`, no deletions, `i/mixed w/mixed`. The guard test
passing is the proof that `main.py`'s six-flag init is exactly as it was.

## L4-15 · "done": the DSN went in twice, and production has been dropping every event since 11:38Z

**What the outside said.** The `reep/external-…` secret has a new `AWSCURRENT` version from
11:30:25Z; the `api` service rolled `reep-api:4` at 11:37Z and both tasks started at 11:38Z; and
`reep-api` in Sentry holds exactly one event, at 11:41:15Z — `ReepSentryVerification`, environment
`production`, tags `service: reep-api`, `verification: production-dsn-redeploy`, `pii: none`, geo
Bengaluru, no `server_name`, no `request_id`, no release. That event proves the DSN accepts events.
It does not prove production delivers them: the API tags `environment=prod` (the task definition
carries `ENV=prod`), never `production`; every API event carries the `request_id` tag from
`app/traceability.py`; and the container sits behind a Mumbai NAT gateway, not a Bengaluru
address. It was sent from a laptop.

**What the secret holds.** Read structurally, never as a value: the key part is the correct 32-hex
public key, but the host parses as `o…ingest.us.senhttps:` and the path contains a second copy of
the DSN. The DSN was pasted into the middle of itself, between `…ingest.us.sen` and `try.io/…` —
222 characters where 111 belong. The same shape rebuilt locally (at a nearby offset) and fed to the
pinned SDK's `Dsn` parser does **not** raise: it yields a well-formed public key and project id and
a host that does not exist, so `sentry_sdk.init` succeeds, `tracing.enabled()` answers true, and
every envelope is posted to a hostname that does not resolve. The SDK's own logger is silent
unless `debug=True`. **Zero spans have reached `reep-api` in 24 hours** — with a 0.2 sample rate
and the ALB probing `/ready` every 15 s on two tasks, a working DSN would have produced dozens by
now. Production has had Sentry "on" and delivering nothing since 11:38Z.

**Why this is the failure the playbook warned about, and one it did not.** The playbook's delivery
row says the DSN's absence is announced only by the absence of one log line; a *malformed* DSN is
worse, because that log line prints. The one-line put in the session summary
(`jq '.SENTRY_DSN = …'` on the retrieved JSON) cannot double-paste, which is why it was written
that way; the console's edit box can.

**The fix is the same two steps, done once:** rewrite that one key with the 111-character DSN and
force a new deployment. Proof of delivery is spans appearing in `reep-api` within two minutes of
the tasks starting, or an error tagged `environment: prod` carrying a `request_id` — never an
event sent from a laptop with the tags typed in.

## VERIFY

Sentry `reep-api`: errors in 24 h = 1 (laptop-sent, above); spans in 24 h = 0. Secret `AWSCURRENT`
created 11:30:25Z; tasks started 11:38:04Z and 11:38:12Z. CloudWatch `/reep/api` since 11:30Z:
no line matching `Sentry` (INFO is dropped in prod — L4-13's 3i — so absence proves nothing
either way). Local `Dsn()` parse of the reconstructed shape: no exception, garbage host, correct
project id.

## L4-16 · Sentry, completed: one init for three processes, the scrubbers as the floor, the release in the image

**Step by step, and what was NOT re-derived.** `docs/sentry-playbook.md` already held the
adversarially reviewed design — the sampler, the scrubber, the guards, the proposed `main.ts`,
the source-map and release plumbing, the cron monitor. Implementing it meant following that
record, not writing a second one; where this entry departs from it, it says so. Two audits ran
first (the worker/scheduler runtime and the Angular bootstrap) and agreed with it on every fact
that mattered, adding two: the compose path sets no DSN anywhere, and a chunk-load failure on the
SDK's dynamic import blanked the whole app.

**L2 · `app/observability.py` (new).** `init_sentry(service, dsn)` is the ONE `sentry_sdk.init`
for `reep-api`, `reep-scheduled-jobs` and `reep-interview-worker`. Idempotent: a second call for
the same service is a no-op, a second SERVICE in one process is refused with an ERROR line and the
first stays — two clients in one process is how the interview ends up split across two projects.
Blank DSN is OFF with one INFO line. `build_options` is pure, so a test can hand the same options
to a client bound to a capturing transport and read every decision back. The three rule-1 flags
are written out inline because the guard reads the file as text. `traces_sampler` is the
playbook's table verbatim, reading `asgi_scope.path` (the transaction name is the raw URL inside
a sampler), normalising `/api/v1`, honouring the browser's decision, keeping every hand-started
`websocket.server`, `task` and `queue.process` transaction, and never raising. `AsyncioIntegration`
was NOT listed, against the playbook: its setup patches the RUNNING loop and this init happens
at import time before uvicorn's loop exists, so the line would read as doing something it cannot.
`job_run` wraps a job entry point — a `task` transaction, low-cardinality tags, the cron check-in
pair, the exception captured (the SDK's dedupe collapses the entrypoint's own `log.exception` of
the same object into it), a flush before the process can exit — and `JobRun.mark_error` lets a
run that COMPLETED without doing its job report ERROR without changing the exit code the
entrypoint already promised.

**L2 · `app/telemetry_scrub.py` (new).** The playbook's scrubber, plus the carriers it named
as uncovered: exception messages and `message` events redacted, `threads` frames stripped as
well as `exception` frames, `extra` walked with a depth cap and credential-named keys blanked,
`REMOTE_ADDR` dropped, `scrub_log` for the structured-logs pipeline (which bypasses
`before_breadcrumb`), `scrub_transaction` touching the request block and breadcrumbs but never
the spans (two hundred of them on an interview; span data is names and counts by contract).
Every function is total. `redact_pii` is reused, not reimplemented — a second regex for the same
identifier is a second one to get wrong.

**L2 · `app/config.py`.** Seven settings beside `sentry_dsn`: `sentry_environment` (blank =
`ENV`, one fact one source), `sentry_release` (the field name binds `SENTRY_RELEASE`, which the
Dockerfile sets), `sentry_profiles_sample_rate` (the brief's name; the playbook proposed
`SENTRY_PROFILE_SESSION_SAMPLE_RATE`, the SDK option is the same), `sentry_send_default_pii`
(RECOGNISED AND REFUSED — a truthy value is an ERROR line at init and nothing else, so the
refusal is visible rather than silent), `sentry_logs_enabled`, and the two per-process DSNs.
Strings with clamped properties, the house pattern, so a blank `.env` line is "use the default".

**L2 · `app/main.py`.** Thirty lines of init became one call. The rule-1 comment moved with the
flags; the comment that stayed says where they went and that the guard reads both files.

**L2 · `app/retention_job.py`.** `init_sentry(SERVICE_JOBS, settings.sentry_jobs_dsn)` — its
OWN DSN, never the api's that sits in the same task environment — then the sweep inside
`job_run("retention.sweep", monitor_slug="reep-retention-daily", monitor_config=…)`. A held-back
hard-delete is `run.mark_error(...)`: the monitor says ERROR and the exit code keeps its docstring's
promise. A guard compares the monitor's crontab to the CDK schedule so the two clocks cannot drift;
its first draft matched the AWS Backup cron earlier in the file and went red on its own subject,
which is the correct failure — the anchor is now the schedule's name.

**L2 · `app/voice_platform/queue/worker.py`.** The fourth project has no process behind it today
— the interviewer runs inside the api, the candidate-ingest Lambda is stdlib-only with no SDK in
its zip — so `reep-interview-worker` maps to the candidate drain worker, the interview platform's
only out-of-process consumer, and the report says the Lambda is uninstrumented rather than
pretending. One transaction per NON-EMPTY cycle (a 20 s long-poll that returns nothing is not
work, and `task` samples at 1.0), one span per message, `failure.kind=permanent` with one
fingerprint per stream for a validation error (a bad spreadsheet is one issue with a count, not a
thousand) and `failure.kind=retryable` for a store the next delivery may survive; `--once` flushes
before exit. Nothing about the candidate travels.

**L2 · `app/tracing.py`.** `scoped()` and `annotate()` added. `transaction()` now REUSES an
active transaction of the same op: on this pin the Starlette integration already opens a
`websocket.server` transaction for the socket's whole life, so the interview was two transactions
with two names at double the cost. Same op → the outer one is renamed and tagged; a different op
(a `task` inside a socket) stays its own. Pinned by a test that starts the outer one by hand.

**L3 · `apps/web`.** `core/sentry-lazy.ts` (the one place the package is named — a dynamic import
of `@sentry/angular` retains its whole namespace and ships the Replay recorder), `core/telemetry-scrub.ts`
(the twin of the Python scrubber, same allowlist, plus `redactPii` for console breadcrumbs; types
only from the SDK so it can live in the initial chunk) with a vitest spec, `main.ts` per the
playbook's proposal plus a guarded import and a guarded `init()` — a chunk 404 or a throwing
init now costs telemetry, never the app — and `initialScope` tags. `environment.ts` loses
`production` (its only reader was the environment ternary that filed every production browser
event under `development`) and gains `sentryRelease`, `sentryEnvironment`, `sentryTracesSampleRate`.
Replay stays unconstructed, and the comment in that file says why in full. `angular.json`
production emits hidden source maps with `sourcesContent`; Angular stamps debug ids when it does.

**L3 · `apps/web/Dockerfile`.** Found by the mismatch review, not by a test: the self-hosted
image copies `dist/web/browser` wholesale into nginx, and once `angular.json` emits hidden maps
that directory holds every chunk's `.js.map` with `sourcesContent` inlined — nginx.conf's
`try_files $uri` would serve each one to anyone who guessed the name, and the name is the chunk's
name plus `.map`. The AWS path never had this hole because `deploy.yml` deletes the maps before
`s3 sync`; the compose path had no such step. The build stage now deletes them and the `test`
that follows makes a silent failure of that delete a failed build. Proven by building the image
and listing `/usr/share/nginx/html` for `*.map`.

**L4 · `Dockerfile`, `deploy.yml`, `stack.py`.** `ARG GIT_SHA` → `ENV SENTRY_RELEASE`, after every
COPY so the pip layer is never invalidated; `deploy.yml` passes it, stamps release/environment/rate
into `environment.ts` beside the DSN (each sed proved by a grep), uploads the maps with
`sentry-cli@3.7.0 --strict` when the token exists and DELETES them unconditionally before publish
(an `--exclude`d map is also excluded from `--delete`, and would stay in the bucket forever), and a
`release` job runs after both halves with the three-clause `if` the playbook argued for. In the
CDK, `SENTRY_JOBS_DSN` is referenced only behind `sentryJobsDsn` context, OFF by default, because
a task that references a secret key the JSON does not hold FAILS TO START — every task, the api
included — and because the import mirror must stay byte-identical; a synth test pins both shapes.
Nothing here deploys anything.

**Tests.** `tests/test_observability.py` (25 tests) proves the boundary against the real SDK: off
without a DSN, once per process, service/environment/release on every event, the sampler table,
the hooks called directly, an unexpected 500 captured with none of the student text, the token,
the cookie or the bearer header in the envelope while the stack trace survives, a 422 and a probe
producing no issue, a raising scrubber dropping everything while the app still serves, the job
wrapper's check-ins and dedupe, the retention entrypoint's three outcomes, the drain worker's
classification, and the transaction reuse. Eight guards joined `tests/test_codebase_guards.py`.
Then six mutations — the allowlist bypassed, frame vars kept, `include_local_variables=True`, the
ERROR check-in forced OK, `/health` sampled at 0.5, the job reading the api's DSN — each sent the
named tests red before the source was restored.

**What is still an operator's.** The `reep/external` secret still holds the malformed `SENTRY_DSN`
from L4-15 and no `SENTRY_JOBS_DSN`; the repository has no `WEB_SENTRY_DSN` or `SENTRY_AUTH_TOKEN`
secret; Sentry carries one default alert rule per project. The classifier refused the settings
change that would let this session write the secret, so all of it is listed in the report with
the exact commands.

## VERIFY

`pytest` (full api suite, Docker up): **893 passed, 3 skipped**. The three telemetry modules
alone: 65 passed. `tools/ci/check_api_imports.py`: 153 modules import against `requirements.txt`
alone. Web: `tsc --noEmit` clean, `ng test` 7 passed across 2 files, `ng build` initial
183.35 kB raw against the 250 kB warning, `sentry-lazy` chunk 151.39 kB raw / 45.14 kB estimated
transfer (the playbook's 151,343 B, measured again), 48 `.js.map` emitted, `debugId=` present in
`main-*.js`. CDK: `test_core_synth.py` 36 passed, 21 skipped (the `.tf`-reading guards). Mutations:
6/6 caught. `git diff` contains no DSN-shaped string. Nothing was deployed and no event was sent.

---

# Round 7 — The Resume Builder, after an end-to-end test of every input box

**Where this came from.** Not a feature request. On 2026-09-09 the whole builder was
driven through a browser as the seeded student — 108 controls across 15 sections and
4 flow steps, every one filled, saved, reloaded and read back through the API. The form
layer passed: autosave, manual save, add/edit/delete, tag inputs, validation, photo
upload with content sniffing, all cross-step navigation, and a full reload that
re-hydrated fourteen section keys at 100% completeness. What failed was everything the
form was *for*. Seventeen findings, three of them high, and the three high ones share
one shape: **a control that reports success and changes nothing.** That is worse than a
missing control, because it spends the student's belief that they have acted.

The findings and the evidence are in the postmortem published with the test. This round
is the fix.

## L2-01 · `app/routers/student.py` — the document is made of the builder now

**The bug, stated exactly.** `_compose_resume_markdown` read the `student_profiles` row,
the verified skills and the academic record. The builder writes `resume_profiles.data`.
No reader existed. A student filled all fifteen sections, watched the sidebar climb to
100%, pressed Generate and received a five-line page carrying none of their experience,
internships, projects, publications, certifications, positions, objective, achievements
or referees — under a card reading "this resume is drawing on your full record".

**Why an allowlist and not "just render the map".** The Resume Builder is *also* the
placement office's intake form. The same map holds next-of-kin details, a date of birth,
medical history, gender, blood group and marital status — each collected under an
on-screen promise that it stays off the exported resume ("Used for placement eligibility
only — not shown on your exported resume"; "visible to your mentor and the placement
office, never to recruiters"). A denylist would publish the NEXT section someone adds to
the builder by default, and the first person to find out would be the student, in front
of an employer. So `_PRIVATE_BUILDER_SECTIONS` names what is private, every public
section is emitted by a named function, and two tests hold it from both sides: one fills
every section with a marker and fails if any goes missing, the other fails if a private
one appears.

**Shape decisions that are not arbitrary.** `app/resume_pdf.py` understands `# `, `## `,
`- ` and paragraphs — it has no nested bullets — so an entry with a description is a bold
headline paragraph plus a plain one, not a bullet with a child (which renders as two
sibling bullets and reads as two separate claims). Empty sections are omitted entirely:
"Publications — none" is a question a resume should not invite. A row with no title is
skipped, because a row someone opened and left unnamed is not a claim. Every leaf is
read through `_rt`/`_rrows`/`_rstrs`/`_rmap`, because the map is client-owned and a 500
here is a student who cannot produce a resume the day applications open.

**Verified vs self-reported stay apart.** `skill_names` is already filtered to
mentor-verified skills; `key_expertise` and `external_certs` are the student's own word,
so they travel under headings that say so. One undifferentiated "Skills" line would
present work in review as work confirmed — the thing the verification flow exists to
prevent. Testing this found a second gap: *nothing* pinned "only verified skills reach
the document". Deleting that filter left all 895 tests green. It has a test now.

**`max_tokens` 1500 → 3000.** A one-page budget written for a five-line draft. A
truncated AI polish silently returns less than the deterministic draft the student would
have got for free.

**`_section_filled` — completeness stops counting furniture.** Adding one empty phone row
and unticking one checkbox moved a fresh profile 8% → 17%, because the contact section
seeds `{code: "+91"}` and `country: "India"` the moment a row exists, and the old rule
counted any non-empty leaf of any type. Now only a non-blank string counts, booleans and
numbers are structure, and `_STRUCTURAL_LEAF_KEYS` names the two the form supplies. A
percentage reachable by clicking Add is not a measure of anything — and the sidebar
advertises 70% as the point where the document gets materially stronger.

**`ProfileOut.mentor_name`.** Additive, and it exists to turn a UI card OFF. See L3-04.

**The generation note.** "No model configured" and "the configured model is remote" were
one sentence, sending the reader to look for a setting that was not the reason.

## L2-02 · `app/resume_pdf.py` — the evidence appendix, which was a checkbox and nothing else

"Include an evidence appendix with proof links" flipped a signal that changed one
sentence of consent copy. `exportPdf()` opened the same URL either way. A student who
ticked it believed their certificates were attached and stopped attaching them by hand.

"Proof **links**" could never have worked as written: an upload URL needs the student's
own session cookie, so a recruiter following one reaches a login page. The proof has to
be the file, embedded. `append_evidence` binds the resume, an index page, and then each
proof — PDFs merged page-for-page, images drawn onto a page scaled to fit (never
enlarged), and anything the parser rejects becomes a page that SAYS the file could not be
included. A missing page is silent; a page saying so is not, and the student sees the gap
before an employer does. No proofs at all returns the resume byte-for-byte, so a student
with nothing to attach does not get a lone page announcing that.

`_evidence_proofs` applies two rules that are really one decision: **verified**, because a
claim still with a mentor is not evidence; and **included**, because the student curates
which verified skills this copy presents and an appendix carrying proofs the resume never
mentions hands an employer answers to questions nobody asked. Mutation testing caught the
first version of that test being blind to the verified half — the fixture had no
unverified-but-included skill, so deleting the filter changed nothing. It has one now.

## L2-03 · `app/seed.py` — rows that pointed at files nobody had written

The seeded uploads carried a `stored_name` the file store had never been asked to write,
so every "View proof" link on the seeded certificate — the one the builder's
Evidence-backed Skills step renders — answered 404 "Stored file is missing" on a
completely fresh database. A broken link in seed data reads as a bug in the download
path, and the first thing anyone does is debug a path that was working perfectly.

`_seed_upload_files` writes real bytes: the PDFs through the app's own renderer (so a
mentor opens a document, not four bytes that merely start with `%PDF`), and the photo as
a PNG built from `zlib` and `struct` — Pillow is not a dependency and must not become one
for dev data, because `requirements.txt` is what the production image installs. The photo
row became PNG to match its bytes: metadata that disagrees with content is how a preview
breaks in the browser while every check on the server passes. `size_bytes` is now
`len(content)` rather than a plausible-looking number the ledger would repeat as fact.
The files are written on every run and a legacy `.jpg` row is repointed, because telling
a developer to drop their database is not what "idempotent" is supposed to mean.

## L3-01 · `resume-evidence.service.ts`, `tailor`, `preview` — two vocabularies for one thing

`GET /student/jobs` returns `required_skills` as **slugs** (`excel`, `financial-modeling`).
The evidence service holds **display names** ("MS Excel"). Both screens compared them
directly, so nothing ever matched, and one screen said two contradictory things at once:

> **You are eligible to apply · 100% skill match**
> Still missing for this role: excel — Not started · financial-modeling — Not started

Whichever line the student believed, the screen had lied to them. `includedSlugs` is the
slug twin of `includedNames`, `label(slug)` resolves a name for display (prettifying a
slug the student has never claimed, so a "missing" list does not read like a database
leak), and the pre-existing private `includedSlugs` — which meant "what STORAGE says",
not "what reaches the document" — was renamed `storedIncludedSlugs`. Verified after the
fix: the same posting now reports exactly one missing requirement, correctly labelled
"Financial Modeling — Student-added".

## L3-02 · `policy.component.ts` — three controls writing to a map nobody reads

Eligibility and both interest flags were stored in `resume_profiles.data.policy`. The
flags that decide which postings a student appears against live on `student_profiles`.
So "Eligible for placements: No" — under copy promising this "removes you from all
recruiter shortlists immediately" and that "your mentor is notified" — left the student
on every shortlist with no mentor told anything, behind a green Saved chip.

Split by ownership, which is the only division that holds:

- **Eligibility is read-only**, because it is not the student's to set. The model says so
  and `update_profile` deliberately omits it. A student who has been made ineligible must
  still SEE it, so it stays on screen as a chip (text + colour together); what goes is the
  radio the API was always going to ignore. The "mentor is notified" sentence is deleted.
- **The two interest flags** are the student's own, so they are written where they are
  read — `PUT /api/student/profile`, optimistic with rollback on failure. Verified against
  the server after the fix.
- **The acceptance timestamp** stays in the builder map, because it genuinely has no home
  elsewhere — and it can now be **withdrawn**. A one-way switch on a screen that says "a
  fresh acceptance is required at the start of every placement season" is a trap with no
  way out of it.

## L3-03 · `resume-identity.service.ts` (new) — the locked fields that were locked and empty

Basic Details showed Course and Primary specialization as a dash behind a SYNCED badge,
and Contact showed the primary phone as the placeholder "Synced from record" — for a
student whose record holds all three. `GET /api/student/profile` has returned
`institution.course_name`, `institution.specialization_name` and `phone` the whole time;
nothing asked. A required-looking field the student may not fill and the system declines
to fill reads as a system that has lost their enrolment.

One owner, because four sections need the same answer and four independent fetches of one
endpoint is four chances to disagree. Two endpoints, and the second is not redundant:
`/student/profile` 404s for a student with no profile row — a real state — and the USN
lives on the `students` row either way, so `/student/dashboard` supplies it. Without that
fallback a brand-new student would watch their own USN vanish from a field labelled
SYNCED.

## L3-04 · `references.component.ts` — a referee who does not work here

The suggestion card offered a hard-coded person, with a one-click "Add as reference"
button, to every student. One click put them on a document a recruiter may ring, as a
referee the student would then have had to explain. It is now the real assigned mentor
from `mentor_name`, and **the card does not render at all when nobody is assigned** — no
card is the honest state, because a faculty account is not a mentor until the Main Admin
assigns them a student. Email and phone are left blank deliberately: the mentor's contact
details are not on this student's record, and inventing them is the whole problem.

## L3-05 · The rest, briefly, with the reasoning that is not obvious

- **`education.component.ts`** — "Update & request approval" called the ordinary save: one
  PUT of the resume draft, no approval, nobody told. The request now lives *in* the
  section, next to the figures it is about, and goes to `POST /mentor-meetings/request`,
  which writes a **mentor note** — the mentor's own instrument for this student, already
  on their screen. No parallel corrections table, and a student with no mentor gets the
  API's truthful 409 rather than a success message for a request nobody can receive. The
  footbar shows no save button on the read-only mirrors.
- **Stepper dots** — Education, Attachments and Certifications derived their dot from
  `data[key]`, which those sections never write, so all three read "Not started" forever:
  Education beside a full semester record, Certifications beside a certificate just added
  (it stores under `external_certs`). Each now reports its own state once loaded. Before
  it has, the tooltip says "Imported from your record — open to see" rather than accusing
  the student of neglect. Deliberately not solved by pre-fetching all three on mount: four
  round-trips on every visit to colour three dots.
- **Goal strip** — picking a posting and then changing the role left the posting selected,
  so Tailor headed its advice "Tailored for Junior Accountant" above a verdict card
  reading "BI Developer · DataWorks". The posting is the strongest statement of intent, so
  editing either of the weaker two now abandons it rather than quietly keeping an answer
  the student has just contradicted.
- **Two-step delete** on the six repeatable entry sections. One click deleted a card
  holding a paragraph of typed description, and the builder autosaves 1.5 s later, so it
  was unrecoverable. Applied by script rather than typed six times: doing it by hand is
  how five get the guard and the sixth keeps the old behaviour.
- **`sectionHasContent`** — `isEmptySection` was written twice, in tailor and preview, and
  both copies counted a boolean or a form default as content. One exported function now,
  matching the API's rule.
- Dead `views/all-resumes.component.*` deleted (~300 lines, referenced only by a comment);
  the no-op `visibility` buttons removed; the import receipt cleared on step change; string
  `@for` loops tracked by `$index` (NG0956).

## VERIFY

- `pytest` — **916 passed, 3 skipped** (was 895), including `tests/test_resume_document.py`
  (18 new) and three new guards in `tests/test_codebase_guards.py`.
- `npx ng build` — clean, initial bundle within budget.
- **Mutation-verified, per the standing rule.** Nine mutations, each reverted after: the
  composer stops passing the builder map; a private section starts reaching the page;
  completeness counts furniture again; the mentor name goes back to a constant; the
  appendix flag is ignored; the appendix stops honouring the included list; an empty
  appendix page is emitted; unverified skills reach the appendix; unverified skills reach
  the document. All nine turned the relevant test **red**. Two earlier attempts came back
  **green** and both were real blind spots — one anchor had hit a duplicate string in the
  wrong query, and one fixture had no unverified-but-included skill. The three guards were
  mutated too (composer reads a private section; the two completeness rules drift apart;
  an invented referee returns) and all three went red. One of them caught its own author:
  the referee guard failed on the explanatory comment naming the person, which is exactly
  what it is for.
- **Driven in the browser afterwards**, as the seeded student: course and specialization
  populate; Tailor reports one missing requirement instead of contradicting itself;
  Generate produces a full document carrying every section; the interest flag round-trips
  to the server; the appendix export goes from 2 pages to 4 with the certificate merged in
  and an index page naming it; the seeded "View proof" link returns 200 instead of 404.

**A note for whoever reads this next.** An external process rewrote `app/routers/student.py`
mid-session from a stale buffer (a bulk rename of a deprecated Starlette constant across
`app/routers/`) and silently reverted this round's first set of edits. They were re-applied
on top. If work disappears here, check `git diff` before assuming you imagined it.

---

# Round 8 — One device at a time

**The ask, and what it did not say.** "make it one device at a time." Before this
round a REEP account could be signed in on unlimited devices at once: the session
is a stateless 12-hour JWT in `reep_session`, there is no sessions table and no
device tracking anywhere in the codebase, and the only revocation lever —
`users.token_version` — was per-user and only ever pulled by a logout, a password
reset or a password change.

**The decision the ask leaves open: which device wins.** Two readings of "one
device at a time" are possible, and they behave very differently for a student.
Refusing the SECOND sign-in keeps whoever got there first, and needs a store, an
eviction policy and an answer for the student whose browser crashed and who now
cannot get back in until a row ages out. Retiring the FIRST needs none of that
and matches what every consumer product does. **Newest wins**, and the login
screen says so.

## L2-01 · `app/routers/auth.py` — four doors, one rule

The mechanism already existed. `users.token_version` rides in the token and
`app/security.py` refuses any token whose version is BEHIND the row; logout has
bumped it since the revocation work. Advancing it at SIGN-IN retires every
session minted before it — no sessions table, no device list, no new state to
keep consistent, and nothing downstream to teach.

**Two halves, because they belong at different moments.**
`_retire_other_sessions` is the in-memory half: it advances the column and
nothing else, and THE CALLER'S COMMIT IS WHAT PERSISTS IT. That is not
tidiness. On the emailed-code door the same transaction lands the one-time
code's `consumed_at`, and the call site's existing comment explains why: a
separate earlier commit makes "code spent, session refused" reachable.
`_confirm_exclusive_session` is the cache half and must run AFTER the commit; it
seeds this worker so the retired tokens stop working here at once rather than at
the end of the revocation cache window.

**All four doors, or none.** Password (`/auth/login`), emailed code
(`/auth/login/code`), Google (`/sso/google/callback`) and the activation link
(`/auth/activate`, which lives in `routers/passwords.py` and is the easiest to
forget). A student who wants two devices only has to find the one door that did
not get the call.

**Ordering on the Google door is load-bearing.** Its payload is built BEFORE
`_record_login` for a reason its own comment gives — that commit can fail, and
reading expired attributes back off a database that has just failed would raise
from inside the recovery path. So the bump goes before `_payload_for` and rides
on the same commit as the `google_sub` pin. If that commit fails, the session is
still issued and the older devices survive; the handler's log line now says so in
those words. **Fail-open on exclusivity, never on authentication** — the
alternative is refusing a correct password because a streak row could not be
written.

**A claim that used to be conditional is now almost always present.**
`_payload_for` omits `tokenVersion` while it is zero, so that the deploy which
added the column did not sign the whole college out. Every session a sign-in
mints now carries it, because the bump happens first. `test_google_callback.py`
asserts the exact claim key set, so that test names it explicitly rather than
tolerating it loosely — that assertion IS the contract app/identity.py, the
interview WebSocket and the Angular `SessionPayload` all read.

## L2-02 · `app/security.py`, `app/identity.py` — saying WHY

Being signed out stops being an incident and becomes a Tuesday: open REEP on a
phone and the laptop drops, mid-form. A screen that simply reappears at `/login`
saying nothing reads as an app that logs you out at random — so the student signs
in on the laptop, drops the phone, and learns not to trust it.

`session_was_retired(token)` answers the one question the server alone can
answer: was this cookie well-formed and unexpired but SUPERSEDED, as opposed to
expired, forged or absent? It is a second, narrow function rather than a wider
return from `verify_session_token`, because that function answers one question
for every caller in the codebase and widening it would touch the HTTP dependency,
the WebSocket dependency and every test that reads a session. A failed
`token_version` lookup returns False: "we could not check" must never be
reported to a student as "someone else signed in".

The 401 carries it as the header `X-Reep-Session: retired`. **A header rather
than a richer `detail`**: `detail` is a string every existing client renders as
prose, and widening it to an object would change a contract shared by every
authenticated route in the app to serve one screen.

## L3-01 · `auth.service.ts`, `auth.guard.ts`, the login screen

`AuthService.refresh()` records the header as `retiredElsewhere`; the guard
redirects to `/login?signedOut=elsewhere`; the login screen renders one sentence
that also tells the student what will happen if they sign in again. Verified in
the browser: a second sign-in elsewhere put the open tab on that URL with that
message.

## VERIFY

- `pytest` — **935 passed, 3 skipped**, including `tests/test_single_device_session.py`
  (8 tests) and a Google-door case added to `tests/test_google_callback.py`.
- `npx ng build` — clean.
- **Two existing tests failed first, and both were right to.**
  `test_change_password_keeps_this_device_and_drops_the_others` built its premise
  from two simultaneous logins, which the new rule forbids; it now asserts the
  retirement on its way through and keeps testing the half that is not automatic
  (the acting device's cookie is re-issued). `test_google_issues_the_same_session_as_the_password_door`
  pins the exact claim key set, and `tokenVersion` had joined it.
- **Mutation-verified.** Six mutations, each reverted: each of the four doors
  stops retiring; the helper becomes a no-op; and the payload is built BEFORE the
  bump. All six went red. The last is the one worth having — it mints a token one
  version behind the row, which is a 200 from `/login` followed by a 401 on the
  very next request, for every user.
- **Driven against the running API and the browser.** Device A signs in (200),
  device B signs in (200), device A's next request answers 401 with
  `x-reep-session: retired`; the open browser tab lands on
  `/login?next=…&signedOut=elsewhere` showing the explanation.
- **A performance scare that was not one.** One suite run took 874 s against a
  normal 78 s. It was measurement contention — an `ng build` and the `ng serve`
  watcher rebuilding after twenty frontend edits, on the same machine. A clean
  run is 118 s and `--durations=20` shows the slowest test at 3.1 s, with nothing
  from this round in the list. The added work is one cached `token_version` read
  on the 401 path only.

**What this costs the student, stated plainly.** Two REEP tabs on two machines no
longer coexist. Someone reviewing a resume on a laptop while checking a job on a
phone will be signed out of one of them. That is the feature, not a defect, but it
is worth knowing before it is reported as a bug — and it is exactly why the
"signed out elsewhere" message was built in the same round rather than left for
later.

---

# Round 8 — Faculty belong to an institution too

**Where this came from.** The owner, reading the student hierarchy: *"students are
only comes under institution but adding faculty is not done yet... if we do not
add this how can we manage all other institution faculty, it will be mess."*
Correct, and the audit that followed found the gap is wider than faculty alone.

## The gap, measured before touching anything

A student's institution is reachable through ONE pointer: `students.cohort_id`,
with college / department / course / batch read through the join and stored on
nothing (`routers/student.py::_institution_for`). Staff had no pointer at all.

- `Mentor` carries exactly two columns: `id` and `user_id`.
- The only institutional field on a staff row was `users.department` — **free
  text typed into a form**. "Dept of Management Studies", "DMS" and "Management"
  are one department to a reader and three to a `GROUP BY`, and no staff row
  could name its college at all. Every staff row in the dev database had it
  `NULL` anyway, so even the free-text field was unused.
- `colleges` already holds five rows. The multi-college shape is live in the
  data, not hypothetical.

**And the correlated problem the owner asked me to look for is real.** There IS
a tenancy layer — `redesign_tenants`, `redesign_tenant_memberships`, wired into
rule 2's gate through `policies.assert_student_scope` — and it is **completely
inert**: zero rows, and *nothing in the application ever constructs a
TenantMembership*. `tenant_id_for_session` returns None in that case by design
("preserving the pre-tenant path"), so the narrowing never fires. College is
therefore **not an access boundary anywhere**: a DIRECTOR or ADMIN reads every
student in every college, and mentor-group narrowing is the only scoping that
actually works. That is recorded here and NOT fixed in this round — see the
closing note.

## L1 · `models/user.py`, `migrations/…_31ca99852acd` — one pointer, on `users`

`users.department_id` -> `departments.id`, nullable, indexed.

**On `users` and not on `mentors`, and the placement is forced.** A faculty
account deliberately has NO `Mentor` row until the Main Admin assigns it a
student (AGENTS.md, and `routers/director.py`). Hanging the department off
`mentors` would mean the newly created faculty member — the exact row an admin
is trying to file — had nowhere to record where they work.

Nullable for the same reason `cohort_id` is: staff exist before anyone files
them, and every account predating the column keeps working. The free-text
`department` is kept beside it because the leave form prints that line; the
column is the source of truth wherever it is set, and both write paths keep the
two in step by writing the department's NAME into the text line.

**Two things autogenerate got wrong, both caught before they shipped.** The
column closes a cycle in the FK graph (`users -> departments -> colleges ->
users`, the last hop being `colleges.created_by_user_id`), and
`Base.metadata.create_all` — which the dev seed uses — cannot topologically sort
a cycle, so it silently drops the constraints it cannot order. Hence
`use_alter=True`. And autogenerate emitted `op.create_foreign_key(None, …)` with
a matching `op.drop_constraint(None, …)`: a downgrade that cannot run, because
there is no constraint named None. Both fixed, and the migration was
round-tripped down and up against the real database to prove it.

## L2 · `staff_placement.py` (new) — resolved, never stored

`placements_for(db, user_ids)` answers "where does this staff member sit" in ONE
query for a whole list, because the faculty screen renders every account at
once and a per-row resolve is the N+1 that makes the console slow the week a
college finishes onboarding. `placement_for` is the single-row convenience.

`StaffPlacement.filed` is the boolean a screen branches on. A falsy
`department_name` cannot distinguish "nobody has filed this person" from "a
department whose name is blank", and those mean opposite things on a screen
whose entire job is to get everyone placed — the same reasoning as the English
Baseline's nullable scores.

`resolve_department` raises `LookupError` for an id that does not exist, and the
router turns that into a 422. **A write that reports success and stores null is
the exact failure this whole round exists to remove**, so an unknown department
must never fall through to "filed under nothing".

## L2 · `routers/admin_faculty.py`, `routers/admin.py`, `routers/director.py`

- `POST /api/admin/faculty` accepts `department_id` and files on creation.
- `PATCH /api/admin/faculty/{user_id}` files an EXISTING faculty member, or
  moves them. This is not a nicety: the column arrived after the accounts did,
  so without it every faculty member created before this change is permanently
  unfiled and the only remedy is a hand-written UPDATE. It refuses any id that
  is not a MENTOR — this endpoint writes institutional fields and must never
  become a route to another kind of account.
- `GET /api/admin/faculty` lists faculty **unfiled first**, then by name. A list
  that buries six unfiled accounts alphabetically among two hundred filed ones
  is a list nobody finishes.
- `GET /api/admin/departments` is a flat picker — every department with its
  college, pre-labelled "Computer Science · BGSCET". The existing endpoint is
  per-college, and filing a faculty member would otherwise force the form to ask
  for a college first purely because of how the API happened to be shaped. The
  college still travels, because two colleges may each have a CSE.
- `GET /api/director/mentor-load` carries the placement, batched, because
  Mentors & Students is where faculty are actually managed.

`test_admin_institution.py`'s route-count guard caught the new endpoint
immediately and made me prove it refuses a STUDENT. That guard works.

## L3 · `mentors-students.component.*`

A department select on the add-faculty form (falling back to the free-text input
when no departments exist or the reader lacks `admin.institution`), a
filed/not-filed chip on each faculty row as **text and colour together**, and an
inline File/Move control. `unfiledCount` is the number that has to reach zero.

**A bug of my own, found only by driving the browser.** I wrote the inline form
with `(ngSubmit)`. This component declares `imports: []`, so there is no
FormsModule, no NgForm, and `(ngSubmit)` binds to a DOM event of that name that
nothing ever raises. The template compiled, the build passed, the button
rendered, and clicking it ran nothing — no error anywhere. Its two neighbours in
the same template already used the native `(submit)` + `preventDefault` pattern.
It is the same family as the `routerLink`-with-empty-imports trap Round 7's
audit found: inert markup that reads as working. Fixed, and turned into
`test_ngsubmit_is_never_used_without_a_forms_module`, which strips HTML comments
first so the comment explaining the fix does not trip it.

## VERIFY

- `pytest` — **949 passed, 3 skipped** (was 942), including
  `tests/test_staff_placement.py` (6 new).
- `npx ng build` — clean.
- **Mutation-verified.** Six mutations, each reverted: create ignores the
  department; an unknown department is silently filed as null; the edit endpoint
  reaches any account; unfiled faculty are buried in the list; mentor-load drops
  the placement; and the `(ngSubmit)` bug reintroduced verbatim. All six turned
  the relevant test **red**.
- **Driven in the browser** as the Main Admin: the picker lists five departments
  across five colleges, filing a faculty member resolves department AND college
  through the join, the free-text line follows the pointer, a bogus department id
  answers 422, and `unfiledCount` went 1 -> 0 with the server agreeing.
- Dev database cleaned of test accounts leaked by earlier runs (including one
  created by this round's own mutation testing, which is what a mutation that
  disables a refusal does).

**What is NOT fixed, and is the owner's call.** Filing faculty makes the
institution *legible*; it does not make it *enforced*. College is still not an
access boundary, because the tenancy layer that would enforce it has no rows and
no writer. Making it one means provisioning memberships and then narrowing every
director/admin query by college — a project, not a patch, and one that changes
who can see what on a live deployment. The pointer added here is the
prerequisite either way: nothing can be scoped by an institution that staff rows
cannot name.


# Round 9 — DIRECTOR is not a role

The ask: *"remove director there no director fuinctionality if any code exist as
director any name as director fix that also check api indexing and its anming
and security of that fix thos iisue and also fix rious ossues"* — and then, on
seeing the folder tree, *"change name also from file named as dictor"*.

## The gap, measured before touching anything

REEP has one office account, the Main Admin (ADMIN). DIRECTOR was a second one
under a different name: it held **every console screen by baseline**, which is
what an administrator is, while being labelled as something else.
`app.grant_access` had refused to mint one for months, so nothing new could
become a DIRECTOR — but the role was still wired into `policies.STAFF_ROLES`,
`require_mentor`, a `require_director` gate with 29 call sites, the dev seed's
`director@bgscet.ac.in / director123`, 28 `/api/director/*` routes, a
`routers/director.py`, an Angular `features/director/` folder of 14 screens, and
`ROLE_BASELINE`.

Two Explore agents were sent over the API first — one on indexing and naming, one
on authorisation — because "remove a role" is exactly the change where the thing
you forget is the thing that matters.

## L1 · `app/governance.py` — the half that was nearly missed

The role gates went first: `policies.STAFF_ROLES`, `require_mentor`,
`require_admin`. For a few hours `ROLE_BASELINE["DIRECTOR"]` still read
`_ALL - {"admin.interview_audio"}`, and **that state is worse than not having
started**:

> a DIRECTOR session was refused by every `require_*` gate — it could not open
> its own mentee log — and passed every `require_capability` gate, which is
> roughly fifty endpoints and the whole console, `DELETE /api/admin/students/{id}`
> and the full-roster exports CSV included.

Two systems decide access in this app and they are checked separately **on
purpose** — `governance.py`'s own words: *"a capability can never relax the
student filter"*. The corollary nobody had written down is that a role removal
which reaches one system and not the other **opens the other one**. No test that
looked at either half alone would have noticed; the authorisation audit did,
which is the entire argument for having run it.

`ROLE_BASELINE["DIRECTOR"]` is now `frozenset()`, and
`tests/test_no_director_privilege.py` asserts **both halves for one session** —
the only assertion that would have failed during those hours. It mints a DIRECTOR
row directly, because no supported path can make one any more, and that is the
point: this is the account an un-migrated checkout still holds.

## L1 · `migrations/…_7c4e0b21d9aa` — the rows, and a downgrade that refuses

`UPDATE users SET role = 'MENTOR', token_version = token_version + 1 WHERE role
= 'DIRECTOR'`. The version bump is not decoration: a session is a signed
snapshot, so without it a DIRECTOR cookie minted before the deploy keeps
asserting the claim until it expires. `downgrade()` raises `NotImplementedError`
deliberately — nothing records which MENTOR rows used to be DIRECTOR, and a
downgrade that guesses would promote faculty.

The enum VALUE stays. A Postgres enum value cannot be dropped without recreating
the type, and the test needs to be able to mint one to prove it reaches nothing.

## L2 · `require_director` → `require_admin`, and `director.py` → `console.py`

One console gate, not two. `require_director` is deleted and its 29 call sites
now read `require_admin`; `_DIRECTORS` is gone and `_STAFF` is `{"MENTOR",
"ADMIN"}`. The module that served the console is `app/routers/console.py`.

`/api/director/*` → `/api/admin/*`, checked collision-free first: 28 director
routes against the 35 already under `/admin`, no overlap. The OpenAPI **tag** was
still `["director"]` on the console router, which is what `/docs` groups by — the
kind of naming that survives a rename because nothing renders it in the app.

While in there, two reads that name a student in the path — `student_weekly` and
`student_resume_pdf` — were missing `_assert_can_access_student`. Rule 2 is not
optional on a path that carries a student id.

## L2 · `app/routers/auth.py` — a bug the rename created and nothing caught

`_HOME_FOR_ROLE` still read `"ADMIN": "/director"`. It is used by exactly one
caller — the **Google SSO callback** — so a Main Admin signing in through Google
was redirected to a route the SPA no longer has. The password door was fine,
which is precisely why nobody saw it: the seeded logins in AGENTS.md never take
that path.

`_DEFAULT_HOME` was `/student`, and it was wrong in the one case it exists for:
an unrecognised role holds no capability and passes no gate, so every screen
behind `/student` answers 403 to it. Sending it to a workspace it cannot use
turns "you have no access" into "the app is broken". It is `/login` now, and
`homeForRole()` in `core/session.ts` is the same decision on the client.

The comment above that map already asked for the two to be kept in step. **A
comment is not a guard** — `test_the_two_post_login_home_maps_agree` reads both
files and compares them.

## L2 · `app/routers/registration.py` — the reviewer's side of a public endpoint

The audit flagged the public upload endpoint as returning applicant PII. Read
properly, the data it returns is the applicant's *own* submission, behind an
unguessable uuid4 they were handed — not a leak. What did not belong there was
the **reviewer's** side of the record: `review_note` is a director writing about
this person for colleagues.

Today it cannot travel — uploads are refused once an application is decided, and
`reopen` clears the note on the way back into the queue. Both of those are one
small edit away from being untrue, and the edit that breaks them will not look
like it touches a public response. So the fix is a **shape, not a filter**:
`PublicRegistrationOut`, built by narrowing the staff model through
`model_dump()`, so a field added to the staff model tomorrow is private by
default. `cohort_id` stayed after a second look — the batch an applicant was
placed in is their own fact, no more private than the one they typed on the form.

## L3 · `features/director/` → `features/admin/`

14 subfolders, 18 files: imports, route paths, API URLs, `Director*` → `Admin*`
class names, `app-director-*` → `app-admin-*` selectors. `git mv` failed with
Permission denied until `ng serve` was stopped — it holds the directory.

**The client knew DIRECTOR as a role, and that was the frontend half of the same
half-done removal.** `roleGuard('DIRECTOR', 'ADMIN')` guarded the console routes,
`HOME_FOR_ROLE` sent DIRECTOR to `/admin`, and the shell's `navKind` gave it the
admin navigation — so a retired DIRECTOR cookie was painted a fifteen-link
sidebar in which the API answers 403 to every single link. Removing `'DIRECTOR'`
from the `Role` union is what made the compiler find all of them; `homeForRole()`
is the answer for a role the client does not recognise.

The **PROGRAM DIRECTOR** block on the leave form is untouched, and must stay
untouched: it is a job title printed on the college's own PDF, at coordinates
measured from that file.

## L3 · The vocabulary, everywhere else

~120 prose corrections across both apps, but only the ones that were **false**
mattered:

- `config.py` and `auth.py` argued that a forged `{"role":"DIRECTOR"}` cookie
  reads every student's records. It grants nothing now — the sentence had become
  the opposite of true, in a security argument. It names ADMIN.
- `interview_records.py` carried a long passage explaining why a DIRECTOR gets
  403 on a recording and 200 everywhere else in the file. A DIRECTOR now gets 403
  everywhere. The argument was always operator-versus-programme rather than about
  that role, so it transfers to MENTOR unchanged — and says so.
- `seed_kb.py` seeded Knowledge Base rows with `owner_role="DIRECTOR"`. Nothing
  reads that column, so nothing broke; the label was simply wrong.
- Docstrings are **published**: FastAPI puts them in `/docs`, so `"""Director:
  approve or reject a pending application."""` was API documentation.

## L3 · `_FACULTY_ONLY` — the Main Admin is not a faculty member either

Four capabilities — `mentor.mentees`, `mentor.notebook`, `mentor.verifications`,
`mentor.upskilling` — are a mentor's own instruments, and the office account has
no mentees, no private notebook, nobody's evidence to verify and no upskilling
shelf. `ROLE_BASELINE["ADMIN"]` is therefore **deliberately not a superset of**
`ROLE_BASELINE["MENTOR"]`, which is exactly the assertion `test_governance.py`
used to make.

Rewriting that test was the right response, not widening the baseline to make it
pass. It now pins both directions, and the four stay GRANTABLE — so
`test_mentee_records.py` asserts **403 first** (the assertion that fails if
somebody quietly widens the baseline to make a screen work) and 200 after the
grant. `tests/conftest.py` grew a `granted` fixture: one writer for "the Main
Admin grants itself a faculty instrument", going through the real endpoint so the
reason floor and the audit write are exercised, and removing the row afterwards
because a grant outlives the `make_user` account that named it.

## L1 · `migrations/…_a91f3c5d80e4` — the indexing half of the ask

Read out of `pg_indexes` on the running database, not out of the models, which
is the only way both kinds of mistake here are visible at once.

**Three indexes added, each one a query that could use nothing.**
`ix_users_email_lower` is the one that matters: NINE call sites ask
`WHERE lower(email) = :x` — the Google SSO callback, registration
provisioning, `grant_access`, and the admin faculty/student create-and-edit
paths — and `ix_users_email` is a plain btree on `email`, which Postgres cannot
use for that predicate because `lower(email)` is an expression, not the indexed
value. Every one of those was a sequential scan of `users`: invisible against
six dev rows, and every single sign-in once a roster is seeded. Proven rather
than assumed — with `enable_seqscan = off` the plan is
`Index Scan using ix_users_email_lower ... Index Cond: (lower(email) = ...)`.
The alternative, lower-casing on write, was rejected: the address is stored as
the person typed it, and that is what gets printed on a leave form.

`ix_maillog_sent_at` and `ix_interview_session_started` are the same shape: an
`ORDER BY <col> DESC LIMIT n` over the whole table where the only index carrying
that column carries it SECOND. An index is walked in sort order from its first
column, so both were a full scan plus a sort. The mail screen measured
`Seq Scan + Sort` over 4,689 rows to show 100; it is now
`Limit → Index Scan`, 0.12 ms.

**Thirteen indexes dropped, each a strict prefix of — or identical to — a UNIQUE
index on the same table.** `ix_studentbadge_student (student_id)` beside
`uq_student_badge (student_id, badge_code)`; `ix_ledger_day_student_day` and
`uq_ledger_day` with the same two columns in the same order. Postgres answers
those from the unique index, so the duplicate only bought a write on every
insert and update to the row. They accumulate because each is individually
reasonable — a unique constraint added to a table that already had an index on
its lead column, or `index=True` on a foreign key whose unique constraint
already leads with it — and from inside a model file both lines look necessary.

Dropping them left `test_every_foreign_key_column_is_indexed` green, and for the
right reason: that guard counts an index whose FIRST column is the FK, and every
covering unique index leads with the same column. The new
`test_no_index_duplicates_the_prefix_of_another` computes the whole thing from
model metadata, skipping partial and functional indexes deliberately — comparing
`uq_interview_consent_active ... WHERE revoked_at IS NULL` by column name is how
a guard starts recommending the deletion of an index the planner needs.

**Two of the audit's findings did not survive contact with the database, and
saying so is the point.** `interview_consents.user_id` was reported as an
unindexed foreign key; both queries that read it filter
`(user_id, version, revoked_at IS NULL)`, which is exactly
`uq_interview_consent_active`. Adding an index there would have been pure cost.
And 3,576 `PENDING` outbox rows read like a stuck queue until `app/worker.py`
turned up with `outbox_relay.relay_once` behind it: the relay exists, it has
simply never been run on this laptop. Neither was changed.

## L2 · `app/routers/mentor.py` — the alert feed had no bound at all

`GET /api/mentor/alerts` was `ORDER BY triggered_at DESC` with no `LIMIT`, and
the two callers pull in opposite directions: a MENTOR's query is narrowed to
their own group, but the Main Admin's is not narrowed at all. `open_only=false`
therefore asked for every alert ever raised for every student in the programme,
sorted, in one response. `_MAX_ALERTS_LISTED = 200`, and `Alert.id` as the
tiebreak because two alerts raised in the same transaction share a
`triggered_at`, and an unstable sort silently drops and repeats rows.

## VERIFY

- `pytest` — **959 passed, 3 skipped** (was 953). Six new tests; five existing
  ones rewritten to pin the new contract rather than the old one.
- `alembic upgrade head`, then `downgrade -1` and `upgrade head` again against
  the real database; `alembic check` reports **no new upgrade operations**, so
  the models and the schema agree — functional index included. That also
  disposes of the audit's "constraint naming mismatch" finding: autogenerate
  sees nothing to change.
- `npx ng build` — clean, **204.23 kB** initial.
- **Mutation-verified**, each reverted: a `/api/director/*` route reintroduced;
  `'DIRECTOR'` put back in the client's `Role` union; the server's home map
  pointed at `/director` again; the public upload endpoint returned the staff
  model again (two tests red). All turned the relevant test **red**.
- **Driven in the browser** as the Main Admin: all 15 console screens under
  `/admin/*` reached through the sidebar and the analytics tiles — every one
  arrived, rendered a real `<h1>` and real data, with **zero failed API requests
  and zero console errors** across the whole tour. The old `/api/director/*`
  paths answer 404; the demoted seed account holds MENTOR and faculty
  capabilities only.
- One-device-at-a-time confirmed itself mid-session: a `curl` login retired the
  browser session, and the login screen explained why in its live region.
- **A stale worker nearly sold me a false negative.** The live check said the
  public registration endpoint still returned `review_note`, while the suite was
  green. The process on :3300 had started at 16:17, before the edit; the
  `nohup` restart had failed to bind and died silently, and the old worker
  answered the readiness probe in 1 s. AGENTS.md warns about exactly this. Killed
  by PID, restarted, re-checked: **no staff fields**. Every live number above was
  taken from the fresh worker.

**What is NOT fixed, and is still the owner's call.** Unchanged from Round 8:
college is not an access boundary, because the tenancy layer has no rows and no
writer. Removing DIRECTOR narrows *who* the console admits to one account; it
does not narrow *which students* that account sees, which is still every student
in every college.

# Round 10 — Deleting the students without deleting the faculty

**The ask, verbatim: "delete all students permently", and before it "not
faculty" and "only student".** The deployment at reep.sast-skills.com holds a
demonstration cohort — 35 student accounts entered for testing — alongside the
faculty accounts that are staying. The office wants the first set gone and the
second set untouched.

## The gap, measured before touching anything

`app.purge_people` cannot express this and it is not a near miss. Its verdicts
are WHOLE-TABLE: `students: EMPTY`, `users: SURVIVOR`. "Survivor" means the one
Main Admin, so pointing it at this deployment would delete every faculty and
alumni account on the way to clearing the cohort. Nothing else in the product
can do it either — `DELETE /api/admin/students/{id}` answers **405 by design**
(Round 9's reasoning: a student account is minted by exactly one path and erased
by none), and `DELETE /api/admin/cohorts/{id}` refuses a batch that is not
already empty.

So the choice was a new destructor or "purge everything and re-create the five
survivors by hand". The second was tempting — read out of the 2026-09-10 dry run
on production, the faculty side owns almost nothing: `mentor_notes`,
`leave_requests`, `staff_signatures`, `staff_upskilling_certs`, `swoc_entries`,
the mentor notebook, `uploads` and `registrations` are ALL at zero rows there,
and `build_plan` only reports tables that have any. But "delete the accounts and
make them again" is not what was asked for twice, and the next demonstration
cohort would pose the same question again.

**Sizing it first, because the answer decides the design.** Every one of the 64
tables `purge_people` empties was classified by how a students-only pass would
have to reach it:

| scoping key | count | note |
|---|---|---|
| `student_id` | 30 | all NOT NULL — only a student can own one |
| `user_id` | 12 | shared with staff; 5 of them are staff-only |
| neither | 21 | a hand-written parent join, or a different key entirely |

That third row is the reason this is a module and not a flag.

## L1 · `app/purge_students.py` (new) — a verdict is three values, not two

The load-bearing idea is that **"delete the student rows" is a different
sentence in a table only a student can own and in a table shared with staff.**
`resumes.student_id` is NOT NULL, so every row goes; `leave_requests` is the
form BOTH roles apply on, so the row survives unless its requester is doomed.
Collapsing those two into one verdict is how a purge either leaves a student's
records behind or deletes a faculty member's leave.

So `STUDENT_VERDICTS` carries `KEEP`, `ALL`, or a scope, for all 93 tables —
**38 emptied, 33 untouched, 22 scoped** — and an unclassified table aborts the
run exactly as it does next door. One check is new and is the point of the
pairing: the key set is compared against `purge_people.VERDICTS`, so the next
person to add a model is stopped by BOTH destructors rather than by the one they
happened to open. A model classified in one and forgotten in the other is silent
in every other way.

**The doomed set is read ONCE, before anything is deleted, and held as literal
ids.** Written the obvious way — a live subquery over `users` — every scope
would re-ask "who is a student" as the pass runs, and the accounts are deleted
near the END of it, because `users` is a parent of almost everything and the
order runs children first. Any table ordered AFTER the accounts would then match
nothing and leave its rows behind. Silently. This is not hypothetical:
`redesign_api_idempotency_keys` names its owner in `principal_id`, a plain
string with **no foreign key to `users` at all**, so nothing constrains where it
lands in the topological sort. The mutation test below confirms it — reverting
to a subquery leaves that table's rows behind and turns
`test_the_plan_counts_what_the_delete_deletes` red.

**`registrations` is scoped by something that is not an account, and that is
deliberate.** It is the only table here holding rows for people who are not
anybody yet. An application that was APPROVED (it became a student) or that
carries a doomed address goes; the office's PENDING queue survives a cohort
being cleared. Leaving an approved application behind would be worse than untidy
— `POST /api/register`'s duplicate guard would then refuse that address forever,
and the person it refuses has no account left to explain why.

**Four tables are KEPT here that `purge_people` empties**, and the set is pinned
by a test so that adding a fifth is an edit somebody has to justify:
`staff_signatures` and `staff_upskilling_certs` (a faculty member's own shelf —
the reason this module exists), and `redesign_outbox_events` /
`redesign_domain_jobs` (no foreign key to `users` and an opaque aggregate id;
guessing which of those is a student is how a purge deletes another tenant's
pending work).

**Two tables are scoped that read as though they should be KEEP.** `mentors` and
`alumni_profiles` belong to people this module protects — but `mentors.user_id`
carries **no ON DELETE**, so a STUDENT-role account holding a mentor row would
turn a data error into a constraint violation that aborts the whole pass. A
scope that is normally a no-op costs one line in the report and removes that
failure mode.

**It refuses to touch a non-student, twice.** `_refuse_unless_only_students`
reads the doomed accounts back BY ID and checks their roles before a single byte
is destroyed — the one way this module could be catastrophic is a scope that is
wrong about who a row belongs to, and that mistake has to surface while it is
still only a refusal. Then `execute` counts the non-student accounts before and
compares after, INSIDE the transaction, so a wrong verdict anywhere else that
took an account with it rolls the pass back instead of committing it.

## L1 · `app/purge_people.py` — three functions extracted, not copied

`_destroy_files` became `destroy_document_files`, `destroy_interview_audio` and
`destroy_s3_recordings`, each a function over the rows it is handed rather than
over the whole database, with `_destroy_files` as a thin caller. `purge_students`
destroys a SUBSET of the same files and calls exactly these.

The alternative was copying forty lines of try/except into the new module, and
this is the half of a purge that cannot be rolled back: two versions of it means
one of them stops getting the fix. Behaviour is unchanged — `test_purge_people.py`
passes untouched, which is what makes the extraction safe to assert.

## L4 · `.github/workflows/ops-task.yml` — its own sentence, in its own box

`purge-students-dry-run` and `purge-students` join the FIXED menu, and the second
demands `purge_students_confirm` read exactly **`DELETE EVERY STUDENT`**.

It deliberately does NOT share `purge-people`'s box. The typed confirmation is
the one step here whose entire value is that it is read rather than repeated, and
sharing the sentence would have an operator typing "PURGE EVERYONE EXCEPT THE
MAIN ADMIN" to perform an act that keeps everyone except the students. The header
comment now says plainly that the two purges are not degrees of the same thing.

## VERIFY

- `pytest` — **973 passed, 3 skipped, 0 failed**. Twelve are new.
  `test_purge_people.py` passes unchanged, which is the assertion that the
  extraction above did not alter behaviour.
- `tools/ci/check_pii_gate.py` and `tools/ci/check_api_imports.py` both clean —
  the latter now imports **159 modules** against `requirements.txt` alone.
- **Mutation-verified, each reverted.** Five, and every one turned a test red:
  `staff_signatures` KEEP→ALL (3 tests); `leave_requests` scoped→ALL, which
  deletes a faculty member's leave (2); `users` ACCOUNTS→ALL, which deletes
  everybody (4); the doomed set back to a live subquery (4, including the plan
  and the new subquery guard); and one table's verdict removed, which stops the
  run at `check_verdicts` (8).
- **The confirmation gate exercised as shell**, with the step's real `run`
  script: the exact sentence passes; wrong case, blank, and — the one worth
  testing — `purge-people`'s sentence are all refused with nothing changed.
- **Dry run against the real dev database**: 3 student accounts, 800 rows across
  38 tables, 3 stored documents, and the report's last line reads
  `Accounts kept: 1 ADMIN, 1 ALUMNI, 1 MENTOR` — which is the whole point of the
  module, printed where an operator will actually look at it.

**What is NOT done.** Nothing here has run on production: the module is on a
branch, and the deployed image (commit `490710e`) does not contain it. The order
is merge → deploy → `purge-students-dry-run` → read the counts → `purge-students`.
And the caveat from Round 9 is unchanged: this narrows WHICH ACCOUNTS exist, not
which students an office account can see — college is still not an access
boundary.
