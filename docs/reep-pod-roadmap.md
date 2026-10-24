> **A ROADMAP, AND ITS SKETCHES ARE IN THE WRONG ORM.** The `prisma` blocks
> below are shorthand from the pre-migration stack: **Prisma is gone**, along
> with the Next.js app and the NestJS `apps/api`. The schema's source of truth
> is SQLAlchemy 2.0 models under `apps/api-py/app/models/`, migrated with
> Alembic. Read the sketches as field lists, never as files to write.

# REEP ← pod.ai — field-level models + build roadmap

Two parts:
1. **Exact data models** decoded from pod.ai's two richest student surfaces
   (Offer, Academic/Eligibility), written as REEP-ready Prisma sketches.
2. **A phased roadmap** for REEP, prioritising the *verified* gaps and protecting
   the *verified* differentiators (see `docs/pod-ai-decode.md` and the council
   validation).

---

## PART 1 — Field-level data models (build-ready)

### 1.1 Placement Offer / Outcome  (from `/offers/create/`)

pod.ai's offer form is the "candidacy loop" REEP is missing. Exact fields observed:

| Field | Type | Options / notes |
|---|---|---|
| Role type | enum | `Full-Time Job` · `Full-Time Job + Internship` · `Internship` |
| Job Title | autocomplete | from a title catalog |
| Organisation Name | autocomplete | from an org catalog |
| Offer Type | enum | `On Campus` (+ off-campus etc.) |
| Joining Date | date | |
| Location mode | radio | `Remote` · `Specify Location` · `Hybrid` |
| CTC | money (INR) | Cost To Company |
| Fixed Gross | money (INR) | |
| Bonuses | list | N components, each label+amount ("Add another bonus") |
| Offer Letter | file | .pdf/.doc/.docx |
| Letter of Intent | file | .pdf/.doc/.docx |
| Job Description | text(6000) | |
| Bond Details | text(6000) | |
| Other Benefits | text(6000) | |
| Status | workflow | `Draft` → **`Submit for Approval`** (locks, immutable) → TPO approves |

REEP Prisma sketch:

```prisma
enum OfferRoleType { FULL_TIME  FULL_TIME_PLUS_INTERNSHIP  INTERNSHIP }
enum OfferChannel  { ON_CAMPUS  OFF_CAMPUS  POOL  REFERRAL }
enum WorkMode      { REMOTE  ONSITE  HYBRID }
enum OfferStatus   { DRAFT  PENDING_APPROVAL  APPROVED  REJECTED }

model PlacementOffer {
  id            String       @id @default(cuid())
  studentId     String
  student       Student      @relation(fields: [studentId], references: [id], onDelete: Cascade)
  jobId         String?      // link to the Job it came from, when on-campus
  roleType      OfferRoleType
  jobTitle      String
  organisation  String       // -> Company relation once a recruiter side exists
  channel       OfferChannel @default(ON_CAMPUS)
  joiningDate   DateTime?
  workMode      WorkMode     @default(ONSITE)
  location      String?
  ctcInr        Int          @default(0)
  fixedGrossInr Int          @default(0)
  bonuses       Json         @default("[]")   // [{ label, amountInr }]
  offerLetterUploadId String?
  loiUploadId         String?
  jobDescription String?
  bondDetails    String?
  otherBenefits  String?
  status        OfferStatus  @default(DRAFT)
  approvedById  String?      // the TPO/director who signs
  approvedAt    DateTime?
  createdAt     DateTime     @default(now())
  @@index([studentId, status])
}
```

Reuses REEP's existing `Upload` (magic-byte sniffing) for the letter/LOI, and the
`Job` model for the on-campus link. Approval reuses the pattern from the
2-approver leave workflow. **This one model turns REEP's self-reported "I applied"
tick into real placement-outcome tracking + a placement report.**

### 1.2 Academic history + Eligibility  (from `/resumes/profile/academics` + `/placement-policy`)

pod.ai's academic model exists to drive an eligibility engine. Observed structure:

- **Per-semester** (current degree): Year · Semester · Aggregate CGPA ·
  **Closed Backlogs** · **Live Backlogs** · Marksheet upload.
- **Full history**: 10th, 12th/Diploma, prior UG degree(s) — each: institution,
  board, year, marks/max, medium, location, subjects.
- **Academic gaps**: months between 12th↔grad, diploma↔grad, grad↔PG, other, total.
- **Placement Policy**: admin-set **`Eligible For Placements` (Yes/No)** +
  student **Interested In Jobs / Internships**.
- Academic edits → **Update & Request Approval** (TPO verifies).

REEP Prisma sketch (extends the existing `SemesterResult`):

```prisma
// extend SemesterResult:
//   closedBacklogs Int @default(0)
//   liveBacklogs   Int @default(0)
//   marksheetUploadId String?

enum QualificationLevel { TENTH  TWELFTH  DIPLOMA  UNDERGRAD  POSTGRAD }

model AcademicQualification {
  id          String @id @default(cuid())
  studentId   String
  level       QualificationLevel
  institution String
  board       String?   // ICSE, PUE Karnataka, university…
  year        Int
  marks       Float
  maxMarks    Float      @default(100)
  medium      String?
  location    String?
  subjects    String?    // "PCMB"
  @@index([studentId, level])
}

model AcademicGap {   // or fold onto StudentProfile as four Int columns
  studentId          String @id
  twelfthToGradMo    Int    @default(0)
  diplomaToGradMo    Int    @default(0)
  gradToPgMo         Int    @default(0)
  otherMo            Int    @default(0)
}

// extend StudentProfile:
//   placementEligible Boolean @default(true)   // ADMIN-set (director/PM), read-only to student
//   interestedInJobs        Boolean @default(true)
//   interestedInInternships Boolean @default(true)
```

**Eligibility engine** (pure, testable — REEP's house style):

```ts
// eligibleFor(student, opportunity, criteria) -> { eligible, reasons[] }
// checks, all AND: profile.placementEligible === true
//   && latestCgpa >= criteria.minCgpa
//   && liveBacklogs <= criteria.maxLiveBacklogs (usually 0)
//   && totalGapMonths <= criteria.maxGapMonths
//   && courseMatches(opportunity.eligibleCourses)
//   && interest matches role type
```

This slots onto REEP's existing `PlacementCriteria` (director-set) and
`evaluatePlacementReadiness`, upgrading them from a *readiness score* to an
*application gate* — which is what pod.ai's Opportunities tab actually does.

---

## PART 2 — REEP roadmap

Ordered by **(verified value ÷ effort)**, using the council's effort estimates.
"Build" = do it in REEP; "Buy" = integrate; "Bet" = strategic decision first.

### Phase A — cheap gates + the candidacy loop  (build, ~2–4 weeks)
1. **SSO / OIDC** — Google Workspace + Microsoft Entra via Auth.js/openid-client.
   *Days of work, and it's a procurement/security-review gate that can block a
   university sale outright. REEP has none today (scrypt+JWT only). Do first.*
2. **Placement Offer model + approval** (Part 1.1). Turns "I applied" into real
   outcome tracking with CTC/documents; reuses Upload + the leave-style approval.
3. **Admin placement-eligibility flag + interest** (Part 1.2 profile fields).

### Phase B — eligibility engine + real-time alerts  (build, ~1–3 months)
4. **Academic history + backlog/gap model** (Part 1.2) and the **eligibility
   engine** — upgrade the jobs board from a UG/PG split to a real per-student
   eligible/non-eligible filter, feeding an Opportunities→Applications→Offers
   funnel (mirror pod.ai's three tabs).
5. **Real-time push** — PWA + Capacitor wrapper + FCM/APNs. *Replaces the unbuilt
   weekly email; drive/interview date-venue alerts are time-critical. Do NOT
   build native twin apps (6+ mo, low payoff).*

### Phase C — assessments  (buy/integrate, ~1–2 months)
6. **Integrate a third-party proctored-assessment provider** (HackerRank / Mettl
   / CoCubes / AMCAT) rather than building a lockdown browser. *Proctoring is a
   permanent arms-race and a whole product category — integrate, don't build. It
   gives companies a reason to trust a "ready" score.*

### Phase D — recruiter/company portal  (BET — decide before building, 6–12 months)
7. A company-facing side is **not a feature, it's a second two-sided product**
   (untrusted-company auth, per-company data isolation, JD posting, candidate
   pipeline, scheduling, offer state machine) with a **cold-start problem code
   can't solve**. It is the most structural gap but the largest, riskiest build,
   and it likely dilutes REEP's current edge. *Make this a business decision, not
   a roadmap ticket.*

### Ongoing — protect & lead with the moat
8. **Do NOT chase parity on:** the social network / feed / directory (a different
   product), native twin apps, or proctoring. pod.ai is AI-saturated, so "has AI"
   is not a pitch.
9. **Lead go-to-market with REEP's verified, defensible edge:**
   - **Privacy-first engineering** — the LLM egress gate + local-model fallback,
     leaderboard field-whitelist, mentor-scope, magic-byte upload sniffing.
     Sharper because pod.ai *maximises* sensitive-data capture (medical,
     disability, family) — a DPDP-Act contrast worth naming to Indian buyers.
   - **Program-specific development depth** — the Reboot/Excel/Elevate stage
     framework, the **certification pace engine** (where REEP beats pod.ai),
     time-usage learning analytics, 3-source SWOC, attendance+VTU fusion,
     skilling recommender, deterministic skills-to-JD match.

### Sequencing note
Phases A–C are ordinary engineering that make REEP a credible placement product
without abandoning its lane. Phase D is the fork: enter it and REEP becomes a
marketplace competing with a proven incumbent; stay out and REEP is a
best-in-class *development & analytics* layer that could even **integrate** with
a marketplace rather than replace it.
