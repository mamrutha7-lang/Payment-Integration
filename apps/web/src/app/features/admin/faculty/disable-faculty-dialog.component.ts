/**
 * Disable faculty — the dialog from `design/admin/OffboardFaculty.html`
 * (spec §27).
 *
 * IT WRITES NOW. `POST /api/admin/users/{id}/disable` landed with B3.3 and this
 * dialog is its only caller on the console. It posts from here rather than
 * handing the reason back to the screen, because the three answers that matter
 * belong on the panel the reader is looking at: the 409 for an account somebody
 * else already disabled, the 422 for the Main Admin's own account, and the
 * network failure. A modal that closes itself and drops an error behind it is
 * how an admin comes to believe a destructive act succeeded.
 *
 * THE REASON IS NOT OPTIONAL, AND THE FLOOR IS THE SERVER'S. `DisableIn` folds
 * whitespace and refuses anything under three characters; `reasonIsUsable`
 * applies the same rule so the refusal arrives on the field instead of as a
 * 422. Disabling is the one console action whose effect is invisible from the
 * console afterwards — the person simply cannot get in — and six months later
 * the reason is the only thing that says whether it was a resignation, a
 * secondment or an incident.
 *
 * THERE IS NO EFFECTIVE DATE AND THE INPUT SAYS SO. The board draws one; the
 * endpoint takes `{ reason }` and nothing else, and stamps `disabled_at` at the
 * moment it runs. Posting a date the server ignores would make the dialog claim
 * a schedule nothing keeps, so the input stays disabled with the truth written
 * under it. It is deliberately NOT drawn through PendingControlDirective: that
 * directive says "available with Phase N", and no phase is bringing this — the
 * server has no schedule to take.
 *
 * WHAT IT STATES IS THE POINT, and every sentence is checked against the
 * endpoint's own docstring rather than against the board's copy. The board's
 * copy promised the mentees were released and the grants revoked; this dialog
 * then said `disable_account` did NEITHER, and was right about the grants and
 * wrong about the mentees for as long as B9.1 has been live —
 * `release_mentees_of` puts every one of them back in the unassigned pool, and
 * the office read "still filed under them" one click before the screen said
 * they had been released. A dialog that states something other than what it is
 * about to do is worse than one that says less: the reader stops checking.
 *
 * THE MENTEE COUNT IS THE ONE NUMBER, and it is nullable for the reason
 * faculty-row.ts gives: `GET /api/admin/mentor-load` needs `admin.analytics`
 * while this screen needs `admin.mentors`, so it can be refused. When it is,
 * the sentence says "their mentees" rather than inventing a zero; when it is a
 * real zero there is nobody to release and no sentence.
 */

import {
  Component,
  ElementRef,
  afterNextRender,
  computed,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';

import { environment } from '../../../../environments/environment';
import { PluralPipe, plural } from '../../../shared/text/plural.pipe';
import type { AccountStateApi, FacultyRow } from './faculty-row';
import { identityLineOf } from './faculty-row';

/** B3.3: `POST …/enable` restores the login within this window. Mirrored from
 *  `admin_faculty.ENABLE_WINDOW_DAYS`; the server is the one that refuses. */
const REVERSIBLE_FOR_DAYS = 90;

/** `DisableIn._reason` folds whitespace and refuses anything shorter. */
const MINIMUM_REASON_LENGTH = 3;

/** The rule `plural` applies, for the second verb in the mentee sentence. */
const PLURAL_RULES = new Intl.PluralRules('en');

interface DisableConsequence {
  icon: string;
  /** True for the half that is KEPT — the records, which read as reassurance
   *  rather than as one more thing being taken away. */
  isKept: boolean;
  sentence: string;
}

@Component({
  selector: 'app-disable-faculty-dialog',
  standalone: true,
  imports: [PluralPipe],
  templateUrl: './disable-faculty-dialog.component.html',
  styleUrl: './disable-faculty-dialog.scss',
  host: { '(document:keydown.escape)': 'dismiss()' },
})
export class DisableFacultyDialogComponent {
  readonly faculty = input.required<FacultyRow>();
  readonly dismissed = output<void>();
  /** The server's own `AccountStateOut`, handed up so the screen can show its
   *  `detail` verbatim and reread the roster. Named for the act rather than
   *  called `disabled`, which on a component would collide with the DOM
   *  property of that name at every call site. */
  readonly accountDisabled = output<AccountStateApi>();

  /** `aria-modal` is a claim, not a mechanism. Without moving focus into the
   *  panel the reader stays on the "Disable account" button BEHIND the
   *  backdrop, and the next Tab walks the screen they think they just covered
   *  — so the dialog takes focus itself and Escape (the host listener) and
   *  Cancel both hand it back to the page. */
  private readonly panel = viewChild.required<ElementRef<HTMLElement>>('panel');

  readonly titleId = 'disable-faculty-title';
  readonly reversibleForDays = REVERSIBLE_FOR_DAYS;
  /** The date the office would record against the offboarding — which is today,
   *  because the server stamps the moment it runs. */
  readonly today = new Date().toISOString().slice(0, 10);

  readonly reason = signal('');
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);

  constructor() {
    afterNextRender(() => this.panel().nativeElement.focus());
  }

  readonly headline = computed(() => `Disable ${this.faculty().name}’s account`);

  readonly subLine = computed(() => identityLineOf(this.faculty()));

  /** The server's floor, applied here so a blank reason is refused on the field
   *  rather than by a 422 the reader has to translate. */
  readonly reasonIsUsable = computed(
    () => this.reason().trim().split(/\s+/).join(' ').length >= MINIMUM_REASON_LENGTH,
  );

  readonly canDisable = computed(() => this.reasonIsUsable() && !this.busy());

  readonly consequences = computed<DisableConsequence[]>(() => {
    const menteesReleased = this.menteesReleasedSentence();
    return [
      {
        icon: 'lock',
        isKept: false,
        sentence:
          'Sign-in stops immediately — REEP password and Google both. Every device it holds is signed out.',
      },
      {
        icon: 'link',
        isKept: false,
        sentence:
          'Every activation, reset and onboarding link still outstanding on this account is spent, so nothing in circulation can set a password on it.',
      },
      ...(menteesReleased === null
        ? []
        : [{ icon: 'diversity_3', isKept: false, sentence: menteesReleased }]),
      {
        icon: 'key',
        isKept: false,
        sentence:
          'The functions granted to this account are NOT revoked — a disabled account cannot make a request, so nothing is reachable. Revoke what is no longer wanted in Governance.',
      },
      {
        icon: 'history_edu',
        isKept: true,
        sentence:
          'Notes, verifications, signatures and leave records stay attached to their name, for the students’ history.',
      },
    ];
  });

  dismiss(): void {
    if (this.busy()) return;
    this.dismissed.emit();
  }

  setReason(event: Event): void {
    this.reason.set((event.target as HTMLInputElement).value);
  }

  /** Post it. One reason, no date — see the header. */
  async confirm(): Promise<void> {
    if (!this.canDisable()) return;
    this.busy.set(true);
    this.error.set(null);
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/users/${this.faculty().userId}/disable`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reason: this.reason().trim() }),
        },
      );
      if (!response.ok) {
        this.error.set(await this.detailOf(response));
        return;
      }
      this.accountDisabled.emit((await response.json()) as AccountStateApi);
    } catch {
      this.error.set('The account could not be reached. Nothing has been changed.');
    } finally {
      this.busy.set(false);
    }
  }

  /** What `release_mentees_of` does to their group, in the words the delete
   *  dialog uses for the same release: "Their 14 mentees are released …", or
   *  "Their mentees are …" when the assignment list could not be read — never
   *  "Their 0 mentees" from a failed request. A real zero releases nobody, so
   *  there is nothing to say. The verbs travel with the count: "Their 1 mentee
   *  are released" is the same unfinished-software tell as "1 mentees", on the
   *  one dialog a reader cannot undo by clicking. */
  private menteesReleasedSentence(): string | null {
    const menteeCount = this.faculty().menteeCount;
    if (menteeCount === 0) return null;
    if (menteeCount === null) {
      return 'Their mentees are released to the unassigned pool and need a new faculty member.';
    }
    const one = PLURAL_RULES.select(menteeCount) === 'one';
    return (
      `Their ${plural(menteeCount, 'mentee is', 'mentees are')} released to the unassigned pool ` +
      `and ${one ? 'needs' : 'need'} a new faculty member.`
    );
  }

  /** FastAPI answers a schema error with `detail` as a LIST; rendered raw it
   *  reads "[object Object]". Its refusals here name the reason — an account
   *  already disabled, the Main Admin's own — so the server's sentence is kept
   *  wherever there is one. */
  private async detailOf(response: Response): Promise<string> {
    try {
      const body = (await response.json()) as { detail?: unknown };
      const detail = body.detail;
      if (typeof detail === 'string') return detail;
      if (Array.isArray(detail)) {
        const messages = detail
          .map((entry) => (entry as { msg?: string }).msg)
          .filter((message): message is string => typeof message === 'string');
        if (messages.length > 0) return messages.join(' ');
      }
    } catch {
      /* not JSON — fall through to the status */
    }
    return `The request was refused (${response.status}).`;
  }
}
