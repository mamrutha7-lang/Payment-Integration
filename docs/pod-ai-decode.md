# pod.ai (RV University tenant) — full application decode

Directly observed from the logged-in **student** view at `rvu.pod.ai` (account: a
student). Captures module structure and capability only — no third-party
personal data, no account-holder field values. Accumulated across `/loop`
verification passes.

Base path pattern: `https://rvu.pod.ai/d/<COMMUNITY>/…` (community id per tenant).
Backend: `app.pod.ai`; media on AWS S3 (`calyx-production-media`); React SPA
(`react.pod-cdn.com`). Login: email/password + Google/Facebook/Microsoft OAuth.

## Top-level navigation (student)

- **Home** — social feed (admin/posts, likes, PDF attachments, "…more")
- **Placements** (expandable) → **Jobs (Opportunities)**, **Resumes**
- **My Network** (expandable, "2K" connections) — social/alumni network
- **Discover** (dropdown/listbox)
- **Contacts** · **Calendar** · **Messages** · **Notifications** (push opt-in)
- **Profile** (avatar → edit) · **Generate Resume** (global button)
- **Members list** (directory) — not opened (other students' PII)
- **Get the App** — Android/iOS deep links

## Module: Placements → Jobs (Opportunities)  `/opportunities/`

A complete campus-drive lifecycle, not a static board.

- **Three tabs = the funnel:** `Opportunities` → `Applications` → `Offers`.
- **Eligibility engine:** filter by `All / Core Courses / Relevant Courses /
  Other Courses`, and `Eligible / Non-Eligible` toggles. The system computes
  per-opportunity eligibility against the student's course/specialization.
- **Outcome tracking (counts at every stage), split by type** Jobs /
  Job+Internship / Internships:
  - *Opportunity* — "Opportunities you are / were eligible for"
  - *Application* — "Opportunities you have applied for"
  - *Offer in hand* — "Opportunities you have received an offer for"
- **Create Offer** (`/offers/create/`) — student self-reports an offer received
  directly; routed to **TPO for records & approval**. => pod.ai tracks real
  placement OUTCOMES end-to-end (eligible→applied→offer), with a TPO approval
  workflow. (This is the "candidacy loop" gap REEP has: REEP only has a
  self-reported "I applied" tick and no offer/outcome stage.)

## Module: Resumes → Resume Profile  `/resumes/profile`

A structured-data resume builder (NOT a template), 16 sections; "Generate
Resume" + "All Resumes" (multi-version output).

Sections: Basic Details · Contact Details · Education · Attachments · Family
Details · Professional Experience · Internship · Projects · Publications /
Research / White Papers · Seminars / Trainings / Workshops · **Certification /
Assessments** · Positions of Responsibility · Other Details · References ·
**Placement Policy** (acknowledgment/consent gate).

Notable architecture:
- **Institution-locked fields** — USN, name, Course, Primary Specialization are
  read-only, pushed from the university SIS/registration; student edits only
  softer fields. (SIS → locked profile integration.)
- **Collects sensitive personal data** — Medical History (6000 chars),
  Disability, Blood Group, Marital Status, Family Details. Under India's DPDP
  Act these are sensitive categories. Contrast: pod.ai *maximizes* the profile;
  REEP's design *minimizes* PII exposure (egress gate, field-whitelist).
- Photo upload (<3MB), Dream Company field, Known Languages.

## Module: Resumes list  `/resumes`

- **Edit Profile** → the 16-section profile builder.
- **Default Resume** governance: "Your default resume is accessible to the
  **Department for download** and use anyway they see fit." One default at a
  time; any resume can be marked Default; a different resume can be submitted
  per placement event. => resumes are *governed, department-visible artifacts*,
  not private drafts. (REEP's resume is student-private, AI-generated per job.)

## Module: Offers → Create Offer  `/offers/create/`

A full structured placement-**outcome** data model with a TPO approval gate.

- Role: `Full-Time Job` / `Full-Time Job + Internship` / `Internship`.
- Job Title (autocomplete catalog) · Organisation Name (autocomplete catalog).
- Offer Type (`On Campus` / off-campus…) · Joining Date.
- Location model: `Remote` / `Specify Location` / `Hybrid`.
- **Compensation:** CTC (INR), Fixed Gross (INR), **multiple Bonus components**.
- **Document capture:** Offer Letter upload + Letter of Intent upload (pdf/doc/docx).
- Job Description (6000), Bond Details (6000), Other Benefits (6000).
- `Save as Draft` / **`Submit for Approval`** — locks after submission (immutable
  once sent to TPO). => end-to-end outcome tracking with compensation + offer
  documents feeding placement analytics. This is the whole "candidacy loop"
  REEP lacks: REEP has only a self-reported "I applied" tick, no offer/CTC/doc
  capture and no approval workflow.

## Module: Calendar  `/calendar/`

Full events calendar — `Month / Week / Day / Agenda` views, prev/today/next,
date-ranged. Carries placement/drive/interview events. (REEP has a
`ScheduleItem` model but no calendar UI.)

## Module: Resume § Certification / Assessments  `/resumes/profile/assessments-certifications`

Manual list — "Add New" certificate/assessment entries for the resume. A flat
resume-input list, NOT a tracked engine. => **REEP is deeper here**: REEP's
certifications have a pace engine (expected-vs-actual curve, OVERDUE-by-pace)
and provider-sync architecture; pod.ai just captures cert rows for the resume.

## Module: Resume § Placement Policy  `/resumes/profile/placement-policy`

A governance gate REEP doesn't have on the student side:
- **"Eligible For Placements" (Yes/No) — read-only/disabled**, set by the
  department/TPO. The institution can bar a student from placements (backlogs,
  policy). This drives the Opportunities eligibility engine.
- **Interested In Jobs** (dropdown) · **Interested In Internships** (dropdown) —
  student preference capture.
=> Admin-controlled eligibility flag + student interest → who may apply.
REEP computes `evaluatePlacementReadiness` but does not gate applications on an
admin flag.

## Module: Social Profile (edit)  `/profile/edit/`

A **second, LinkedIn-style profile** distinct from the resume profile — pod.ai
is also a professional network, not only a placement tool. Tabs:
`Basic Details · Summary · Career Journey · Awards · Contact Info · Notifications`.
- Basic: profile picture, name (locked), DOB, gender, marital status, USN
  (locked), **Joining Year / Graduation Year (locked, SIS)**, institutional
  Email (locked), **Organisation / Designation** (autocomplete — for working
  students/alumni), Web Links / IMs.
- **Alumni continuity:** Career Journey + Organisation/Designation persist the
  profile into alumni/working status (a networking graph over time).
- Per-profile **Notification preferences** tab.
=> Two-profile model: *resume profile* (placement) + *social profile*
(networking/alumni). REEP has neither the social graph nor alumni continuity.

## Verification log

- Pass 1: Home, Resume Profile (Basic Details), Opportunities, Resumes list,
  Offer create form, Calendar.
- Pass 2: Resume § Certification/Assessments, Resume § Placement Policy, Social
  Profile edit (6 networking tabs). New: admin eligibility gate; dual-profile
  (resume + social) model; alumni continuity.

## Module: My Network  (`Messages` + `People`)

Expands to **Messages** and **People (2K)** (`/members-list/` — a searchable
member directory of ~2000 students/alumni/faculty; not enumerated here for
privacy). A professional social graph over the whole community.

## Module: Messages  `/messages/`

Group + direct messaging. `Create new message`; conversation list; per-group
`Details` (Mute Notifications, member roster). **Cohort groups are
auto-provisioned** ("System added … to group") — e.g. batch/programme groups
with 1000+ members. (Individual members/threads not recorded — PII.)

## Module: Home feed · Discover toggle

The Home feed switches between **Discover** (community-wide posts) and
**My Feed** (personalised). Admin posts carry text + PDF attachments + Likes.

## Module: Resume § Education (Academic Details)  `/resumes/profile/academics`

The richest data model, built for **placement eligibility filtering**:
- Per-semester grid: Year · Semester · **Aggregate CGPA** · **Closed Backlogs**
  · **Live Backlogs** · **Marksheet** upload; plus overall Aggregate CGPA.
- Full academic history: current degree (PG), **Other Degrees** (prior UG),
  **12th / Diploma**, **10th** — each with board, year, marks, medium, location.
- **Academic Gap Details** — months of gap between 12th↔graduation,
  diploma↔graduation, graduation↔PG, other, total (a standard placement
  eligibility criterion in India).
- **"Update & Request Approval"** — academic edits go through TPO verification.
=> Backlog (closed/live) + gap tracking + full 10th→PG chain feed the
eligibility engine. **REEP is narrower**: it has SemesterResult/SubjectMark
(VTU CGPA) but no 10th/12th/prior-degree chain, no explicit backlog model, and
no academic-gap tracking — all of which Indian recruiters filter on.

---

# COMPLETE CAPABILITY MAP — pod.ai student app (decoded)

1. **Auth/identity** — email+password + Google/FB/Microsoft SSO; institutional
   email; USN/name/course/years locked from SIS.
2. **Social network** — LinkedIn-style profile (Summary, Career Journey, Awards,
   Contact Info), a 2K-member People directory, connections, alumni continuity.
3. **Feed** — Discover / My Feed, admin posts + attachments + likes.
4. **Messaging** — group + direct, auto-provisioned cohort groups, mute.
5. **Calendar** — Month/Week/Day/Agenda, drive/interview events.
6. **Resume builder** — 16 structured sections → Generate Resume, multi-version,
   Default resume the department can download; sensitive-data capture (medical,
   disability, family); rich academic model (CGPA/backlogs/gaps/10th→PG).
7. **Placement funnel** — Opportunities (eligibility engine: Core/Relevant/Other,
   Eligible/Non-eligible) → Applications → **Offers**; Create Offer with role,
   org/title catalogs, CTC/fixed/bonuses, offer-letter+LOI upload, bond,
   Submit-for-Approval (locks). Admin **placement-eligibility gate** +
   interest capture.
8. **Governance/approval** — resume approval, offer approval, academic
   update-approval, admin eligibility flag — a TPO approval spine throughout.
9. **Notifications** — in-app + browser push; per-profile prefs.
10. **Mobile** — native iOS/Android (+ Pod-Assessment, Pod-Recruit, Pod-TPO apps).

# NET COMPARISON vs REEP (post-decode)

**pod.ai clearly ahead / REEP lacks:**
- The whole **placement funnel to offer** (CTC + documents + approval) — REEP has
  only a self-reported "applied" tick.
- **Eligibility engine** driven by CGPA/backlog/gap/course + admin flag.
- **Social network + messaging + directory + alumni continuity.**
- **Approval spine** (resume/offer/academic/eligibility).
- **Native mobile + push**, **SSO**, proven scale.
- **Full academic history** (10th→PG, backlogs, gaps).

**REEP ahead / pod.ai lacks (verified):**
- **Certification pace engine** (expected-vs-actual, OVERDUE-by-pace) — pod.ai's
  cert section is a flat resume list.
- **Time-usage learning analytics** (5-bucket day sheet + session log).
- **REEP developmental framework** (Reboot/Excel/Elevate + dimensions), 3-source
  **SWOC**, attendance+VTU fusion, **skilling recommender**, deterministic
  **skills-to-JD match %**.
- **Privacy-first engineering** — egress gate + local-model, leaderboard
  field-whitelist, mentor-scope, upload magic-byte sniffing — sharper given
  pod.ai *maximises* sensitive-data capture (medical/disability/family).
- Internal **mentor ops** (timestamped notes, 2-approver leave), director
  **placement-readiness evaluator**.

**One-line:** pod.ai = a two-sided **placement marketplace + campus social
network** with an approval spine and mobile/SSO/scale. REEP = a privacy-first
**student-development & learning-analytics** tracker. The gaps that matter most
for REEP to be a placement product: the **offer/eligibility funnel**, **SSO**,
and **real-time push** — the rest is REEP's genuine, defensible different lane.

## Decode status

Every distinct student-side module and capability has been mapped (auth, social,
feed, messaging, calendar, resume+16 sections, placement funnel, offers,
governance, notifications, mobile). Further passes would only re-scan mapped
modules or open PII-heavy directories (People/Contacts/threads), which are
deliberately not recorded. Decode considered COMPLETE for the student
application.

## Still to decode (only if access granted / needed)

Resumes list & generation output; each resume sub-section's fields; Calendar;
Messages (structure); Discover menu; Notifications; Profile edit; Contacts
(structure only); My Network categories (structure only, no people); any
Assessments/Practice area; Placement Policy content; Offer create form fields;
mobile-app parity. Verify each up to 10×; append new findings here.
