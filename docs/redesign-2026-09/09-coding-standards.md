# 09 · Coding standards — simple, readable, named after the people who use it

These rules apply to every line Claude Code writes in this redesign, Python and TypeScript alike. They exist because the next reader is a student developer or the owner reviewing a PR at midnight, not the author. If a rule here conflicts with a "clever" way, the rule wins.

## 1. The stakeholder approach to naming

Name things after what the people who use REEP call them: **Student, Faculty, Mentor (a function), HOD, Placement office / Main Admin, College admin, Alumni, Batch, Course, Specialization, Semester, Registration, Onboarding invite, Mentor group, Function (grant), Interview policy, Practice report, Question bank, Leave request, SWOC entry, Placement criteria, Offer**. A reader who knows the product should be able to guess what a name holds without opening it.

The naming ladder — every level says *who* and *what*:

| Level | Rule | Good | Bad |
|---|---|---|---|
| **File / module** | one responsibility, named by the stakeholder action or record it serves | `promote_batch.py`, `interview_policy.py`, `mentor_assignment_history.py`, `student-360.component.ts`, `leave-approval-chain.service.ts` | `utils2.py`, `helpers.py`, `misc.ts`, `handler.py`, `stuff.ts` |
| **Class** | the record or the service, singular noun | `InterviewPolicy`, `MentorAssignment`, `PromoteBatchService`, `RegistrationQueue` | `Data`, `Manager`, `Processor`, `Info` |
| **Method / function** | verb + object, says what happens to whom | `promote_batch_to_next_semester(batch)`, `assert_mentor_in_students_college(mentor, student)`, `list_registrations_in_scope(session)`, `record_interview_score_summary(session)` | `process()`, `handle()`, `do_it()`, `run2()`, `calc()` |
| **Variable** | the thing, in full words | `students_in_batch`, `remaining_interviews_today`, `first_signature_by`, `reason_for_grant` | `s`, `tmp`, `res`, `req2`, `data`, `arr`, `obj`, `x` |
| **Boolean** | a question that reads as English | `is_completed`, `has_mentor_group`, `may_sign_first_step`, `policy_stores_audio` | `flag`, `ok`, `done2`, `check` |
| **Constant** | the rule it encodes, with its unit | `INTERVIEWS_PER_STUDENT_PER_DAY = 8`, `HANDOVER_READ_WINDOW_DAYS = 90`, `HALF_HOURS_IN_A_DAY = 48` | `MAX = 8`, `N = 90`, `LIMIT` |
| **Endpoint `operation_id`** | stakeholder + action | `admin_promote_batch`, `student_start_interview`, `faculty_sign_leave_first_step` | `post_cohort_action`, `endpoint7` |
| **Test** | a sentence that states the rule | `test_faculty_without_a_mentor_group_sees_no_mentees`, `test_promotion_rewrites_no_semester_numbers` | `test_1`, `test_edge`, `test_ok` |

No abbreviations except the product's own (USN, CGPA, SWOC, HOD, TPO, PDF, CSV, API, DB). No numbered names (`step2`, `helper3`). No names that are true today and false tomorrow (`new_students`, `temp_fix`).

## 2. Simple over clever

Write the obvious version. Explicit statements, named intermediate values, one idea per line.

**Do not use** (Python): the walrus `:=`; nested or conditional comprehensions (`[a for b in c if d for e in f]`); nested ternaries; `and`/`or` chains used for defaulting or control flow (`x = a or b or c`, `cond and do()`); `lambda` assigned to a name; `*args, **kwargs` pass-throughs in application code; `getattr`/`setattr`/`globals()` tricks; `map`/`filter`/`reduce` where a `for` loop reads better; regex where `str` methods do; chained comparisons that hide meaning; one-letter loop variables outside a two-line loop; `try/except` around code that cannot raise; bare `except:`; mutable default arguments; magic numbers and magic strings.

**Do not use** (TypeScript / Angular): `!!value`, `~indexOf`, `value && call()` as control flow, nested ternaries, chained `??` / `?.` beyond one level without a named variable, the non-null assertion `!`, `any`, comma expressions, bitwise tricks, `reduce` for what a loop does, long RxJS operator chains where a signal and a plain method read better, logic inside templates beyond a simple read (`@if (canSubmitDay())`, never `@if (total() === 24 && !saving() && status() !== 'SUBMITTED')` — that condition becomes a named `computed`).

**Do use**: guard clauses with early returns; small functions (aim ≤ 40 lines, one screen); named intermediate variables that carry the stakeholder meaning; explicit `if / else`; explicit types (Pydantic models and TS interfaces named after the product concept, e.g. `PromoteBatchRequest`, `InterviewPolicyOut`, `LeaveApprovalChain`); constants for every number a rule depends on; comments that say **why** (the house style in `AGENTS.md`), never what the code already says.

### Example — the ledger submit gate

Before (clever):
```python
def can_submit(d):
    return d and d.status != "SUBMITTED" and sum(c.halves for c in d.cells) == 48 and not any(c.halves > CAP[c.slot] for c in d.cells)
```
After (readable):
```python
HALF_HOURS_IN_A_DAY = 48

def day_can_be_submitted(ledger_day: TimeLedgerDay) -> bool:
    if ledger_day.status == LedgerStatus.SUBMITTED:
        return False
    logged_half_hours = total_half_hours_logged(ledger_day)
    if logged_half_hours != HALF_HOURS_IN_A_DAY:
        return False
    if any_slot_is_over_capacity(ledger_day):
        return False
    return True
```

### Example — scope check

Before:
```python
ok = s.role == "ADMIN" or (g := grants_for(s, key)) and any(t in anc(student) for t in [x.scope_id for x in g])
```
After:
```python
def caller_may_access_student(session: Session, capability_key: str, student: Student) -> bool:
    if session.role == Role.ADMIN:
        return True
    grants_for_capability = grants_held_by(session, capability_key)
    student_ancestry = institution_ancestry_of(student)   # cohort → course → department → college
    for grant in grants_for_capability:
        if grant.scope_id in student_ancestry:
            return True
    return False
```

### Example — Angular template condition

Before: `@if (ledger() && ledger()!.status !== 'SUBMITTED' && dayTotal() === 24 && !saving())`
After: in the component, `readonly canSubmitDay = computed(() => this.dayIsComplete() && !this.dayIsSubmitted() && !this.saving());` and in the template `@if (canSubmitDay())`.

## 3. Structure that reads top-down

- **Router → service → query.** A router function only parses the request, calls one service function and shapes the response. The rule lives in a service function named after the stakeholder action (`promote_batch_to_next_semester`). SQL lives in a query function named after what it returns (`students_in_batch(batch_id)`). A reader follows one rule from HTTP to SQL in three named steps.
- **One concept per file.** `interview_policy.py` holds the policy model, its service functions and nothing else. When a file passes ~400 lines, split it by stakeholder action, not by "utils".
- **Angular:** one component per screen (`student-360.component.ts`), one service per API area named after it (`interview-policy.service.ts`), signals named after product state (`remainingInterviewsToday`, `policyStoresAudio`), `computed` for every derived value the template reads, methods named as the user's action (`startInterview()`, `submitDay()`, `requestMeeting()`).
- **Errors are sentences a stakeholder understands.** `HTTPException(status_code=422, detail="This batch's course has 4 semesters; semester 5 does not exist.")` — never `detail="invalid"`.
- **No dead paths.** No commented-out code, no `TODO` without an issue number, no feature left half-wired "for later".
- **Tests read like the rulebook.** One assertion theme per test, names as sentences, fixtures named after the people in them (`faculty_without_group`, `student_in_second_college`, `main_admin`).

## 4. Where the existing code is not like this

The repo already has long files (`routers/student.py` ~3 100 lines, `interview.service.ts` ~2 900 lines). Do not rewrite them for this redesign. New code follows this standard; when you touch an existing function for a task, leave it clearer than you found it (rename a local, extract a named helper) without changing behaviour — and say so in the commit.

## 5. Review checklist (paste into the PR)

- [ ] Every new file, class, function and variable is named after the stakeholder concept it serves; no abbreviations, no numbered names
- [ ] No walrus, nested comprehension, nested ternary, `and/or` control flow, `!!`, `!`, `any`, `~indexOf`, comma expressions, bit tricks
- [ ] Every rule number is a named constant with its unit
- [ ] Functions ≤ ~40 lines, guard clauses, one idea per line, named intermediates
- [ ] Router → service → query; template conditions are named `computed`s
- [ ] Errors are readable sentences; no bare `except`; nothing swallowed
- [ ] Tests are sentences; fixtures are people
- [ ] Comments explain why; no commented-out code
