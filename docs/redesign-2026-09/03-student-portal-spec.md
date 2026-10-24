# 03 · Student Portal — screen by screen

29 approved boards in `design/student/`. Two kinds:

- **Restyle** — the page keeps main's content, fields, copy, states and endpoints; only the dress changes. Rule: *do not add sections, KPIs, filters or features the page on main does not have.* Build these in **Phase 1** with zero backend change.
- **Plan-driven** (mock interview) — the page adds what the future-implementation plan specifies (`04-backend-changes.md` §5–§6). Build in **Phase 4**, after the backend tasks.

Shell for every student screen: new app bar (brand · "Student" · avatar + name/USN · Sign out), the sidebar exactly as main lists it (Home, Jobs, Skilling, Leaderboards, Time Sheet · PROGRAMME: Faculty / TPO Log · DOCUMENTS: Resume Builder) with the profile card (avatar, name, USN, View profile) on top, page head = crumb → h1 → sub-line + actions. Screens not in the sidebar (Profile, Uploads, Records, Courses, Certifications, English Baseline, Mock interview, Interviews, Agent) keep their existing entry points (profile card, Home stage cards, the orb).

## Copy rule
Where main's copy says "your mentor" as an *access* statement ("your mentor and the placement office can read it"), the new copy is "staff your placement office has given access — typically your mentor and the placement cell". Where "your mentor" names a person the student works with ("contact your mentor", "your 1:1s with your mentor") it stays.

## 1. Sign in (`LoginRedesign.html`, `LoginStates.html`, `LoginMobile.html`) — `/login`, restyle
- Left panel (brand, what REEP is), right card: portal tiles Student / Faculty / Alumni (labels only — the role comes from the roster) + dashed Main Admin door; Google button (disabled with reason when `sso/status` says unavailable); REEP-password form only when `password_login_available`; ID without `@` completes to `<id>@bgscet.ac.in`; "remember me"; Forgot password inline; the 6-digit code step; error/notice states (`?error=sso_*`, `signedOut=elsewhere`, rate limited); 390 px mobile layout stacks the card.
- **API unchanged:** `GET /api/auth/sso/status`, `POST /api/auth/login`, `/login/code`, `/forgot`, `GET /api/auth/sso/google?next=`.
- Component: `features/login/login.component.*` (also used by staff — the Main Admin door is the same component; see admin §1).

## 2. Skilling (`SkillingRedesign.html`) — `/student/skilling`, restyle with one frozen card
- Head; **"Claim a skill with a certificate" card is unchanged** — keep its current markup, classes and behaviour (upload → `POST /api/student/uploads` kind CERTIFICATE_PROOF → `POST /api/student/badges/{code}/evidence`); "Claims in progress" as a plain table (badge, status chip, note from the verifier); the badge board by category (Managerial 12 · Sectoral 16 · Platform/Technical 10 · Thinking 6 · Interview Readiness 4) as **hexagonal emblem tiles** — outlined lilac when available, solid brand purple with a green verified seal when earned, 6 tiles per row, legend + footer ("N skills currently illuminated").
- **API unchanged:** `GET /api/student/badges`, `/badges/{code}/start`, `/evidence`, uploads.

## 3. Time Sheet (`TimeSheetRedesign.html`) — `/student/time-log`, restyle
- Crumb "Daily log · Semester N", title, sub; date stepper (prev / date / next disabled at today) + **Submit day** (disabled until the day totals 24 h, tooltip = `submit_blocked_reason`); four metric tiles from `metrics[]` (dot on "Day accounted" when unaccounted > 0; warn tone on subs); the slot × activity table in the grid dress (activity colour swatches in the header, numeric inputs, per-slot logged x / cap + status chip Balanced / n h open / n h over / Empty; day-total row with the day chip); footbar (blocked-reason chip or "Submitted — this day is closed" + **Save draft** disabled until dirty); footnote; "Skilling this week" strip (meter + % chip).
- **API unchanged:** `GET /api/student/ledger?day=`, `PUT /ledger`, `POST /ledger/submit`, `GET /timesheet?days=7`, `GET /dashboard` (semester).

## 4. Leaderboards (`LeaderboardsRedesign.html`) — `/student/leaderboards`, restyle
- Title, sub; pill tabs Skills / VTU results / Streak / Mocks taken; own-rank card (gradient rank medallion "N · of M", headline, encouragement, value chip); explainer note ("Ranked by … Updates as records change."); ranking rows as a grid (rank pill, avatar, name + "You" chip, "of M", total) with the own row highlighted; empty / hidden (opted-out, "Take part again") / error states as main.
- **API unchanged:** `GET /api/student/leaderboards?board=`, `PUT /leaderboard-visibility`.

## 5. Faculty / TPO Log (`MentorLogRedesign.html`, `MentorLogRequest.html`) — `/student/mentor-log`, restyle
- Title, sub, **Request a meeting** (secondary; disabled while the form is open); **SWOC** card (four tinted tiles with a coloured edge and eyebrow; "from TPO and mentor inputs"; empty line when no inputs); optional notice; the **Request a meeting** card (lede, "What would you like to discuss?" textarea, "Preferred time (optional)", Cancel / Send request disabled until typed); **Meeting history** (date block, title · location, action chip 1:1 scheduled / Flagged / note, note text, "Logged by …"); empty state.
- **API unchanged:** `GET /api/student/mentor-meetings`, `POST /mentor-meetings/request`, `GET /api/student/overview` (swoc block). Note: the plain restyle board was removed from the canvas by the owner; the request-a-meeting board shows the same page with the form open — build the page from that board.

## 6. Resume Builder (`ResumeRedesign.html` + 15 `Resume*.html`) — `/student/resume`, restyle (Build-content step)
- Head with the 4-step flow (Build content · Tailor to opportunity · Preview · Export & share); the goal strip (Target role, Location, Selected opportunity, match hint); left rail (Profile complete %, Import verified record, the 16 sections in 5 groups); section card with All Resumes / Generate Resume actions, the section's fields exactly as main's `ResumeBuilderService` section map, footer "Not saved yet · changes are versioned & autosaved · Save section" (or a lock line for read-only sections).
- Sections and their boards: Basic Details (`ResumeRedesign`), Contact, Family, Education, Attachments, Evidence-backed Skills, Professional Experience, Internship, Projects, Publications / Research, Seminars / Trainings, Certification / Assessments, Positions of Responsibility, Other Details, References, Placement Policy. Synced fields (from the student record) render locked with a "Synced" chip.
- **API unchanged:** `GET|PUT /api/student/resume-profile` (1.5 s debounced autosave), `POST /resume/generate`, `GET /resume/{id}/pdf`. The Tailor / Preview / Export views are **not redesigned** — keep them as on main, inside the new frame.

## 7. Mock interview — plan-driven (`InterviewStart.html`, `InterviewLive.html`, `InterviewReport.html`) — `/student/assistant`
What stays from main: title/sub, Clear conversation, Start / End, the Rule 1 note, the stage (orb, status pill, caption, phase, clock, mic meter, transcribing affordance), live transcript, saved-conversation toggle, the practice report card with nullable scores and the calibration line, "Saved to your past interviews".

What the plan adds (and nothing else):
1. **Your track** — the default track comes from the student's batch specialization (`B5.3`); the card shows "Set from your batch · MBA 2024–26 · Business Analytics", persona and voice; other admin-managed tracks (`B5.1`) as "Practise another track"; General interview stays.
2. **Today** — "1 of 8 interviews used today" counting **completed** interviews only (`B6.4`); session length from the programme's time limit (8:00, wrap-up 90 s early); mic + HTTPS check.
3. **Before you start** — read-only policy card: live audio (provider name from `interview_provider_label`), transcript kept (retention days), voice recording on/off **for the programme** — all "Set by the placement office" (`B6.1`). **No switches, no Change / Withdraw, no consent checkbox.** Starting an interview records the acknowledgement server-side.
4. **Live session** — interviewer turns carry their question-bank id (`B6.6`, e.g. "Q2 · bank BA-07"); the phase stepper Opening → Framework probing → Deep dive → Wrap-up.
5. **Practice report** — as main, plus **"Where this report goes"**: readiness score updated (`B6.3`), a next action added, counted as mock #N on Home (unified with `mock_attempts`), and the retention line ("Transcript kept until <date>; your scores stay on your programme record") (`B6.2`).
- **API today:** `GET /api/interview/status`, `WS /api/interview?specialization=`, `GET /api/interview/consent` (+POST/DELETE), `GET /api/agent/history`. **New:** `GET /api/interview/policy` (programme policy + today's usage + default track + tracks) — `B6.1`, `B6.4`, `B5.3`; the consent POST is issued by the client at Start with the policy's scopes (acknowledgement), never from a form.

## 8. Mock interviews history — plan-driven (`InterviewHistory.html`) — `/student/interviews`
- Stat tiles (taken, completed, reports, **best overall**), **Your progress** trend across scored interviews (overall / communication / domain / structure; a not-scored point is marked, not dropped; scores kept beyond transcript retention — `B6.2`), the sessions grid with a **Kept until** column, the open record: report, **Who has opened this record** (every staff read logged — `B6.5`), the transcript with phases and question ids. **No "request removal" control** — a note says records are managed by the placement office.
- **API today:** `GET /api/interview/sessions`, `/{id}`, `/{id}/transcript`, `/{id}/report`. **New:** `GET /api/interview/progress` (score summaries), `GET /api/interview/sessions/{id}/views` — `B6.2`, `B6.5`.
- Note: the owner removed this board from the canvas after approving it in this shape; confirm with him before building §8 (the endpoints in `B6` are needed by the admin Interview records screen regardless).

## 9. REEP Agent (`AgentRedesign.html`) — `/student/agent`, restyle
- Title, sub, Clear conversation (ghost); the general-helper note; thread: user bubbles right (brand purple), agent answers left with the "R" mark, **action rows** (label + reason → `routerLink`), **Source:** chips (accent for policy, teal `--cat-3` for your-record), limitations lines, feedback row (Copy · helpful / not helpful · Report · "Thanks for the feedback"), typing indicator, empty state with centred starters; starters row; composer (textarea grows, Send primary / Stop while pending, disabled when empty); hint line.
- **API unchanged:** `/api/agent/history`, `/ask`, `/feedback`, `DELETE /conversation`. One component serves student / faculty / admin — the admin board (`design/admin/Agent.html`) adds only the "What the agent can see" card.

## 10. Screens with no board (keep as on main, inside the new shell)
Home, Jobs, Profile, Uploads, Records, Courses, Certifications, English Baseline. They inherit tokens and the shell; do not redesign them in this release. If the owner wants them restyled later, the same restyle rule applies.

## Home ↔ mock interview
`B6.3` unifies the Home "mocks" chart with interview evaluations; until it lands the Home chart stays on `mock_attempts`. No other Home change.
