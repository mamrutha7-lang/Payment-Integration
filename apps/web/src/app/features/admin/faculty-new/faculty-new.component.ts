/**
 * Add faculty member — the four-step wizard.
 *
 * The approved board is `docs/redesign-2026-09/design/admin/AddFaculty.html`,
 * specified in `02-admin-console-spec.md` §8. One endpoint on `main` creates a
 * faculty login — `POST /api/admin/faculty` (`app/routers/admin_faculty.py`,
 * `require_admin`) — and this screen is built on that one and on the two reads
 * the cascade needs: `GET /api/admin/colleges` and
 * `GET /api/admin/colleges/{id}/departments`. Nothing else is called.
 *
 * WHY A WIZARD AND NOT ONE FORM. The account IS the access control (AGENTS.md,
 * "the roster IS the access control"), and the board splits the decision into
 * the four questions the office actually asks in order: where this person
 * teaches, who they are, what they may do, and how they are invited. The
 * institution question is first because a faculty login filed under nothing is
 * invisible to every question that starts "who teaches in…".
 *
 * WHAT PHASE 3 LANDED AND WHAT IS STILL GREY. None of it is invented and no
 * control that would 404 is live:
 *
 *   - **Department is required, on the server now** (`B3.1`).
 *     `AdminFacultyIn.department_id` is a plain `str` and its validator answers
 *     422 on a blank one, saying why. That sentence is rendered VERBATIM
 *     (`DEPARTMENT_REQUIRED_MESSAGE`) rather than paraphrased: it names the
 *     consequence — the department is how this account reaches its COLLEGE, and
 *     the college is what decides which addresses may hold one — and a screen
 *     that restates a server rule in its own words is a second copy of that
 *     rule to keep true.
 *   - **The college email domain fence** (`B3.2`, over the `email_domains`
 *     `B1.1` put on the college). `_email_policy` in
 *     `app/routers/admin_faculty.py` refuses an address off the college's
 *     domains unless `allow_external` is set AND `external_reason` is written,
 *     and it writes the pair, the domain and the list into the audit row. Both
 *     fields are live on step 2: the checkbox reveals the reason, and the
 *     reason is required while it is ticked. The college's own domains come
 *     back on `GET /api/admin/colleges` (`email_domains`) and are shown, so the
 *     fence is visible before it refuses anybody — but the comparison drawn on
 *     screen is a HINT and says so, because a college that has recorded no
 *     domains falls back to the deployment's own list
 *     (`app/institution_domains.py`), which this screen cannot see. The server
 *     is the only fence; this one only saves a round trip.
 *   - **No Employee ID field and no Functions step.** There is no employee id
 *     anywhere in the API, and a function is granted with a reason, a scope
 *     and an expiry that this wizard does not ask for — both were drawn
 *     disabled for board fidelity and removed on 2026-09-15, because a control
 *     that can never work is the first one a first-day clerk presses.
 *   - **Mail.** `GET /api/admin/platform/status` (`B3.7`) still does not exist
 *     — nothing under `/api/admin` answers it — so this screen never predicts
 *     whether the invitation can be emailed. The create response's own
 *     `emailed` flag is the answer, after the fact, and the activation link is
 *     shown exactly when it is `false` — the board's "a link is shown only if
 *     email is unavailable, once", and B3.7's own instruction that this wizard
 *     "must not promise an email that cannot be sent".
 *
 * THE LINK IS SHOWN ONCE, AND THE SERVER IS THE ONE SAYING SO.
 * `AdminFacultyOut.shown_once` is `true` (`B3.4`): nothing stores the raw
 * token, only its sha256, so this response is the only place the link exists.
 * The screen honours that — the link is held in no signal past "Add another",
 * is never listed, and the copy control is offered beside it while it is on
 * screen. Re-minting one is the Faculty screen's "Activation link" action,
 * which is a deliberate act with its own endpoint. Which of the two things
 * happened is stated either way: emailed to the address, or not sent at all
 * and therefore only here — which is what a deployment whose SES is still
 * sandboxed gets, every time.
 *
 * ONE PRIMARY BUTTON. The footer's Continue is the view's primary through
 * steps 1–2, becomes "Create account & invite" on step 3, and becomes "Done"
 * once the account exists. Every other control here is secondary or ghost.
 */

import { Component, OnInit, computed, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { environment } from '../../../../environments/environment';
import { PluralPipe } from '../../../shared/text/plural.pipe';

// ---- the exact snake_case shapes of the routers' Out models ---------------

interface CollegeOut {
  id: string;
  code: string;
  name: string;
  campus: string | null;
  contact: string | null;
  status: string;
  department_count: number;
  /** B1.1. The addresses this college admits. EMPTY is not "nobody": it means
   *  the deployment's own list applies (`app/institution_domains.py`), and that
   *  list is not on any endpoint this screen calls — so an empty list is
   *  rendered as "not recorded here", never as a fence. */
  email_domains: string[];
}

interface DepartmentOut {
  id: string;
  college_id: string;
  code: string;
  name: string;
  head: string | null;
  status: string;
  cohort_count: number;
}

interface StaffPlacementOut {
  filed: boolean;
  department_id: string | null;
  department_code: string | null;
  department_name: string | null;
  college_id: string | null;
  college_code: string | null;
  college_name: string | null;
}

/** The created account, as `admin_faculty.AdminFacultyOut` answers it. */
interface CreatedFacultyOut {
  user_id: string;
  name: string;
  email: string;
  designation: string | null;
  department: string | null;
  placement: StaffPlacementOut;
  activation_link: string;
  emailed: boolean;
  expires_in_hours: number;
  /** B3.4. The server's own statement that this response is the only place the
   *  raw link exists. The wording on screen is driven off this rather than
   *  hard-coded, so a server that ever stops promising it stops being quoted. */
  shown_once: boolean;
}

/** What the three steps collect, in the order the board asks for it. */
interface FacultyDraft {
  collegeId: string;
  departmentId: string;
  name: string;
  email: string;
  designation: string;
  /** B3.2. `AdminFacultyIn.allow_external` — the deliberate exception. */
  allowExternal: boolean;
  /** B3.2. `AdminFacultyIn.external_reason` — what justifies it on the trail. */
  externalReason: string;
}

const EMPTY_DRAFT: FacultyDraft = {
  collegeId: '',
  departmentId: '',
  name: '',
  email: '',
  designation: '',
  allowExternal: false,
  externalReason: '',
};

type LoadState = 'loading' | 'ready' | 'error';

/** `AdminFacultyIn._department_id`'s own refusal, copied verbatim. The
 *  consequence is the half that matters and the half a paraphrase drops. */
const DEPARTMENT_REQUIRED_MESSAGE =
  'A department is required: it is how this account reaches its college, and ' +
  'the college decides which addresses may hold one.';

/** `_email_policy`'s own refusal when the box is ticked and the box alone. */
const EXTERNAL_REASON_REQUIRED_MESSAGE =
  "An address outside the college's domains needs a reason. It is recorded " +
  'against this account.';

/** `models/institution.py::STATUS_ACTIVE`. Anything else is archived. */
const ACTIVE_STATUS = 'ACTIVE';

const FIRST_STEP = 1;
const INSTITUTION_STEP = 1;
const IDENTITY_STEP = 2;
const INVITE_STEP = 3;
const LAST_STEP = INVITE_STEP;

interface WizardStep {
  number: number;
  label: string;
}

const WIZARD_STEPS: WizardStep[] = [
  { number: INSTITUTION_STEP, label: 'Institution' },
  { number: IDENTITY_STEP, label: 'Identity' },
  { number: INVITE_STEP, label: 'Invite' },
];

@Component({
  selector: 'app-admin-add-faculty',
  standalone: true,
  imports: [RouterLink, PluralPipe],
  templateUrl: './faculty-new.component.html',
  styleUrl: './faculty-new.component.scss',
})
export class AdminAddFacultyComponent implements OnInit {
  readonly steps = WIZARD_STEPS;
  readonly departmentRequiredMessage = DEPARTMENT_REQUIRED_MESSAGE;
  readonly externalReasonRequiredMessage = EXTERNAL_REASON_REQUIRED_MESSAGE;
  readonly lastStep = LAST_STEP;

  readonly step = signal(FIRST_STEP);
  readonly draft = signal<FacultyDraft>({ ...EMPTY_DRAFT });

  readonly collegesState = signal<LoadState>('loading');
  readonly colleges = signal<CollegeOut[]>([]);
  readonly collegesError = signal<string | null>(null);

  readonly departmentsState = signal<LoadState>('ready');
  readonly departments = signal<DepartmentOut[]>([]);
  readonly departmentsError = signal<string | null>(null);

  readonly saving = signal(false);
  readonly createError = signal<string | null>(null);
  readonly created = signal<CreatedFacultyOut | null>(null);
  readonly copyMessage = signal<string | null>(null);

  /** Set when the admin tries to continue past a step that is not complete.
   *  The field errors are always drawn from the draft; this only decides
   *  whether they are shown yet, so an untouched form is not red. */
  readonly showValidation = signal(false);

  ngOnInit(): void {
    void this.loadColleges();
  }

  // ---- reads ------------------------------------------------------------

  async loadColleges(): Promise<void> {
    this.collegesState.set('loading');
    this.collegesError.set(null);
    try {
      const response = await fetch(`${environment.apiBase}/admin/colleges`, {
        credentials: 'include',
      });
      if (!response.ok) {
        this.collegesError.set(await this.detailOf(response));
        this.collegesState.set('error');
        return;
      }
      const colleges = (await response.json()) as CollegeOut[];
      this.colleges.set(colleges);
      this.collegesState.set('ready');
      if (colleges.length === 1) {
        this.selectCollege(colleges[0].id);
      }
    } catch {
      this.collegesError.set('The server could not be reached. No college was loaded.');
      this.collegesState.set('error');
    }
  }

  async loadDepartments(collegeId: string): Promise<void> {
    this.departmentsState.set('loading');
    this.departmentsError.set(null);
    this.departments.set([]);
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/colleges/${collegeId}/departments`,
        { credentials: 'include' },
      );
      if (!response.ok) {
        this.departmentsError.set(await this.detailOf(response));
        this.departmentsState.set('error');
        return;
      }
      this.departments.set((await response.json()) as DepartmentOut[]);
      this.departmentsState.set('ready');
    } catch {
      this.departmentsError.set(
        'The server could not be reached. No department was loaded.',
      );
      this.departmentsState.set('error');
    }
  }

  // ---- the draft --------------------------------------------------------

  selectCollege(collegeId: string): void {
    // Changing the college clears the department: a department belongs to one
    // college, so carrying the old id forward would file this person in a
    // department of a college they were just moved out of.
    this.draft.update((draft) => ({ ...draft, collegeId, departmentId: '' }));
    if (collegeId === '') {
      this.departments.set([]);
      this.departmentsState.set('ready');
      return;
    }
    void this.loadDepartments(collegeId);
  }

  selectDepartment(departmentId: string): void {
    this.draft.update((draft) => ({ ...draft, departmentId }));
  }

  setName(name: string): void {
    this.draft.update((draft) => ({ ...draft, name }));
  }

  setEmail(email: string): void {
    this.draft.update((draft) => ({ ...draft, email }));
  }

  setDesignation(designation: string): void {
    this.draft.update((draft) => ({ ...draft, designation }));
  }

  /** Un-ticking clears the reason as well as the flag. A reason left behind in
   *  the draft would be sent on the next attempt with `allow_external` false,
   *  where the server ignores it — so the audit row would not carry the
   *  sentence the admin can still see on screen. */
  setAllowExternal(allowExternal: boolean): void {
    this.draft.update((draft) => ({
      ...draft,
      allowExternal,
      externalReason: allowExternal ? draft.externalReason : '',
    }));
  }

  setExternalReason(externalReason: string): void {
    this.draft.update((draft) => ({ ...draft, externalReason }));
  }

  // ---- what each step needs before it is complete -----------------------

  readonly selectedCollege = computed(() => {
    const collegeId = this.draft().collegeId;
    return this.colleges().find((college) => college.id === collegeId) ?? null;
  });

  readonly selectedDepartment = computed(() => {
    const departmentId = this.draft().departmentId;
    return this.departments().find((department) => department.id === departmentId) ?? null;
  });

  /** Both reads return ARCHIVED rows — `list_colleges`'s own docstring says so
   *  ("archived ones included"), and `list_departments` does not filter either.
   *  An archived department is still a legal `department_id`, so this wizard
   *  offers it and SAYS SO on the option: filtering it out makes a destination
   *  vanish with nothing on screen to explain where it went, and offering it
   *  unlabelled files a new faculty member into a department the office has
   *  already closed. */
  archivedSuffix(status: string | null | undefined): string {
    // A missing status is not an archived one: nothing chosen yet must read as
    // nothing, never as "Archived".
    return !status || status === ACTIVE_STATUS ? '' : ' · Archived';
  }

  readonly archivedPlacementChosen = computed(() => {
    const college = this.selectedCollege();
    const department = this.selectedDepartment();
    return (
      (college !== null && college.status !== ACTIVE_STATUS) ||
      (department !== null && department.status !== ACTIVE_STATUS)
    );
  });

  readonly collegeIsChosen = computed(() => this.selectedCollege() !== null);
  readonly departmentIsChosen = computed(() => this.selectedDepartment() !== null);

  readonly collegeHasNoDepartments = computed(
    () => this.departmentsState() === 'ready' && this.collegeIsChosen() && this.departments().length === 0,
  );

  /** True only while step 1 is actually showing the two selects. When the
   *  colleges are still loading, the read was refused, or this deployment has
   *  no college at all, there is no field to redden — so Continue has to say
   *  why it refused rather than doing nothing at all. */
  readonly institutionFieldsAreOnScreen = computed(
    () => this.collegesState() === 'ready' && this.colleges().length > 0,
  );

  readonly nameIsGiven = computed(() => this.draft().name.trim().length > 0);

  /** The server's own rule (`AdminFacultyIn._email`) is "contains an @ that is
   *  neither first nor last". A college address also has a dot in its domain,
   *  and saying so here costs one round trip less than the 422 does. */
  readonly emailLooksLikeAnAddress = computed(() => {
    const address = this.draft().email.trim().toLowerCase();
    const at = address.indexOf('@');
    if (at <= 0 || at !== address.lastIndexOf('@') || at === address.length - 1) {
      return false;
    }
    if (address.includes(' ')) {
      return false;
    }
    return address.slice(at + 1).includes('.');
  });

  /** B1.1, as this college recorded it. Empty means the college has none and
   *  the deployment's list applies; the screen says that rather than guessing
   *  at a list it cannot read. */
  readonly collegeDomains = computed(() => this.selectedCollege()?.email_domains ?? []);

  /** `institution_domains.domain_of` — the part after the LAST `@`, lower-cased.
   *  Empty when there is no `@` at all, which is the same distinction that
   *  helper's docstring is about: "no domain here" must not answer a domain. */
  readonly emailDomain = computed(() => {
    const address = this.draft().email.trim().toLowerCase();
    const at = address.lastIndexOf('@');
    return at < 0 ? '' : address.slice(at + 1).replace(/^@+/, '');
  });

  /** A HINT, never a gate. It can only fire when this college has recorded its
   *  own domains: a college with none is fenced by the deployment's list, which
   *  is not on any endpoint here, so the honest answer for that case is to say
   *  nothing and let the server decide. Nothing on this screen is disabled by
   *  it — `_email_policy` is the fence, and a client that refused first would
   *  be a second fence quietly drifting from the real one. */
  readonly addressLooksOffCollegeDomain = computed(() => {
    const domains = this.collegeDomains();
    const domain = this.emailDomain();
    return (
      domains.length > 0 &&
      domain.length > 0 &&
      this.emailLooksLikeAnAddress() &&
      !domains.includes(domain)
    );
  });

  /** The reason is required while the box is ticked, which is very slightly
   *  stricter than the server: `_email_policy` returns before it looks at the
   *  pair when the address IS on the college's domains, so a reason typed for
   *  an on-domain address is ignored. That case is a box ticked for nothing,
   *  and the fix for it is to untick the box. The alternative — asking for the
   *  reason only when this screen thinks the address is outside — hands the
   *  decision to the hint above, which cannot see the deployment fallback and
   *  would let the create go out reasonless and come back 422. */
  readonly externalReasonIsGiven = computed(
    () => this.draft().externalReason.trim().length > 0,
  );

  readonly externalReasonIsMissing = computed(
    () => this.draft().allowExternal && !this.externalReasonIsGiven(),
  );

  readonly institutionStepIsComplete = computed(
    () => this.collegeIsChosen() && this.departmentIsChosen(),
  );
  readonly identityStepIsComplete = computed(
    () => this.nameIsGiven() && this.emailLooksLikeAnAddress() && !this.externalReasonIsMissing(),
  );

  readonly currentStepIsComplete = computed(() => {
    const step = this.step();
    if (step === INSTITUTION_STEP) {
      return this.institutionStepIsComplete();
    }
    if (step === IDENTITY_STEP) {
      return this.identityStepIsComplete();
    }
    // Invite is the submit step, which has its own gate below.
    return true;
  });

  readonly readyToCreate = computed(
    () => this.institutionStepIsComplete() && this.identityStepIsComplete(),
  );

  readonly accountExists = computed(() => this.created() !== null);

  /** The board's footer line. */
  readonly stepPosition = computed(() => `Step ${this.step()} of ${LAST_STEP}`);

  stepState(stepNumber: number): 'done' | 'active' | 'pending' {
    if (this.accountExists() && stepNumber < LAST_STEP) {
      return 'done';
    }
    if (stepNumber < this.step()) {
      return 'done';
    }
    if (stepNumber === this.step()) {
      return 'active';
    }
    return 'pending';
  }

  // ---- moving through the wizard ----------------------------------------

  continueToNextStep(): void {
    if (!this.currentStepIsComplete()) {
      this.showValidation.set(true);
      return;
    }
    this.showValidation.set(false);
    this.step.update((step) => Math.min(step + 1, LAST_STEP));
  }

  goToPreviousStep(): void {
    this.showValidation.set(false);
    this.step.update((step) => Math.max(step - 1, FIRST_STEP));
  }

  // ---- the one write ----------------------------------------------------

  async createFacultyAccount(): Promise<void> {
    if (!this.readyToCreate()) {
      this.showValidation.set(true);
      return;
    }
    if (this.saving() || this.accountExists()) {
      return;
    }
    this.saving.set(true);
    this.createError.set(null);
    const draft = this.draft();
    try {
      const response = await fetch(`${environment.apiBase}/admin/faculty`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: draft.name.trim(),
          email: draft.email.trim().toLowerCase(),
          designation: draft.designation.trim() || null,
          department_id: draft.departmentId,
          // B3.2. Sent on every create, not only when this screen suspects the
          // address is outside: the server is the one that knows the effective
          // domain list, and a flag withheld because the client guessed "on
          // domain" is a 422 the admin cannot act on without retyping.
          allow_external: draft.allowExternal,
          external_reason: draft.allowExternal ? draft.externalReason.trim() || null : null,
        }),
      });
      if (!response.ok) {
        this.createError.set(await this.detailOf(response));
        return;
      }
      this.created.set((await response.json()) as CreatedFacultyOut);
    } catch {
      this.createError.set(
        'The server could not be reached. No faculty account was created.',
      );
    } finally {
      this.saving.set(false);
    }
  }

  async copyActivationLink(): Promise<void> {
    const account = this.created();
    if (account === null) {
      return;
    }
    try {
      await navigator.clipboard.writeText(account.activation_link);
      this.copyMessage.set('The activation link was copied to the clipboard.');
    } catch {
      this.copyMessage.set(
        'The clipboard is not available in this browser — select the link and copy it by hand.',
      );
    }
  }

  /** A second faculty member, from step 1, with nothing carried over — the
   *  previous activation link included, which is why it is not stored. */
  startAnotherFacultyMember(): void {
    this.draft.set({ ...EMPTY_DRAFT });
    this.departments.set([]);
    this.departmentsState.set('ready');
    this.departmentsError.set(null);
    this.created.set(null);
    this.createError.set(null);
    this.copyMessage.set(null);
    this.showValidation.set(false);
    this.step.set(FIRST_STEP);
    if (this.colleges().length === 1) {
      this.selectCollege(this.colleges()[0].id);
    }
  }

  /** The server's own sentence where there is one — its refusals name the
   *  address and the role ("already belongs to a MENTOR account") and a generic
   *  message would throw that away. FastAPI answers a schema error with
   *  `detail` as a LIST, which rendered raw says "[object Object]". */
  private async detailOf(response: Response): Promise<string> {
    try {
      const body = await response.json();
      const detail = body?.detail;
      if (typeof detail === 'string') {
        return detail;
      }
      if (Array.isArray(detail) && detail.length > 0 && typeof detail[0]?.msg === 'string') {
        // Pydantic v2 prefixes a validator's own ValueError with "Value error, ".
        // The sentence after it is the one written for a person to read; the
        // prefix is an implementation detail of the server's schema library.
        return detail[0].msg.replace(/^Value error,\s*/, '');
      }
    } catch {
      /* fall through to the status */
    }
    return `The request was refused (${response.status}).`;
  }
}
