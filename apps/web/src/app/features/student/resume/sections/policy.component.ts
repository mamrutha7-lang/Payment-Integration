/**
 * Resume Builder — "Placement Policy" section (data-p="policy").
 *
 * THIS SCREEN WROTE THREE CONTROLS INTO A MAP NOBODY READ. The eligibility
 * radio, "interested in jobs" and "interested in internships" were stored in
 * `resume_profiles.data.policy` — the builder's own opaque section map — while
 * the flags that actually decide which postings a student appears against live
 * on `student_profiles`. So a student could set "Eligible for placements: No",
 * read the sentence beneath it promising that this "removes you from all
 * recruiter shortlists immediately" and that "your mentor is notified", press
 * Save, watch the chip say Saved — and remain on every shortlist, with no
 * mentor told anything. A control that reports success and changes nothing is
 * worse than no control: it spends the student's belief that they have acted.
 *
 * What it does now, and why each half is different:
 *
 *   ELIGIBILITY IS READ-ONLY, because it is not the student's to set.
 *   `placement_eligible` is admin-set by design — the model says so
 *   (models/student_profile.py) and `update_profile` deliberately omits it from
 *   the editable set. A student who has been made ineligible must be able to
 *   SEE that, which is why it is still on screen; offering them a radio that
 *   the API will ignore is the thing being removed.
 *
 *   THE TWO INTEREST FLAGS ARE the student's own, so they are written where
 *   they are read: `PUT /api/student/profile`. One source of truth, and the
 *   Jobs feed reflects the change on the next load.
 *
 *   THE POLICY ACCEPTANCE stays in the builder map, because it genuinely has
 *   no home elsewhere and nothing downstream consumes it. It is recorded as a
 *   timestamp so "Accepted on 14 Jul 2026" is a fact about a date rather than a
 *   checkbox that was once ticked — and it can now be withdrawn, because a
 *   one-way switch on a screen that says "a fresh acceptance is required at the
 *   start of every placement season" is a trap with no way out of it.
 *
 * Markup reuses the global reep-v2 classes (.card / .field / .chip / .notice).
 */

import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { environment } from '../../../../../environments/environment';
import { ResumeBuilderService } from '../resume-builder.service';
import { ResumeIdentityService } from '../resume-identity.service';

/** The half of this section the builder still owns. */
interface PolicyModel {
  accepted_at: string | null;
}

/** The fields of GET/PUT /api/student/profile this section reads and writes. */
interface PlacementProfile {
  placement_eligible: boolean;
  interested_in_jobs: boolean;
  interested_in_internships: boolean;
}

/** The placement policy, as the placement cell states it. */
const POLICY_TERMS: string[] = [
  'One offer per student. Once an offer is accepted, you are withdrawn from all further processes.',
  'Interviews and tests are mandatory once shortlisted. Two unexplained absences pause your eligibility for the season.',
  'Your profile and marks are shared with recruiters exactly as recorded by the university.',
  'Any misrepresentation of marks, experience or certifications removes you from the placement process.',
];

@Component({
  selector: 'rb-policy',
  standalone: true,
  imports: [FormsModule],
  styles: [
    `
      /* small "why it matters" pill on placement-critical fields */
      .reqp {
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0;
        text-transform: none;
        color: var(--brand-purple);
        background: rgba(138, 90, 30, 0.1);
        padding: 2px 6px;
        border-radius: 5px;
        margin-left: 6px;
        white-space: nowrap;
      }
      .accept-note {
        background: var(--good-bg);
        color: var(--good);
        margin-bottom: 14px;
      }
      .accept-row {
        display: flex;
        align-items: flex-start;
        gap: 10px;
        padding: 12px 14px;
        border-radius: 14px;
        background: var(--tint-2);
        border: 1px solid var(--hairline);
        font-size: 13.5px;
        color: var(--ink);
        cursor: pointer;
        margin-bottom: 14px;
      }
      .accept-row input {
        width: 17px;
        height: 17px;
        accent-color: var(--purple-mid);
        margin-top: 1px;
        flex: none;
      }
      .terms {
        display: flex;
        flex-direction: column;
        gap: 11px;
        font-size: 13.5px;
        line-height: 1.55;
        color: var(--ink);
        margin: 0;
        padding: 0;
        list-style: none;
      }
      .terms li {
        padding-left: 14px;
        position: relative;
      }
      .terms li::before {
        content: '';
        position: absolute;
        left: 0;
        top: 0.62em;
        width: 6px;
        height: 6px;
        border-radius: 999px;
        background: var(--purple-mid);
      }
      /* The withdraw affordance: quiet, because withdrawing is the rare act. */
      .withdraw {
        background: none;
        border: 0;
        padding: 0;
        margin-top: 8px;
        font: inherit;
        font-size: 12px;
        color: var(--muted);
        text-decoration: underline;
        cursor: pointer;
      }
      .withdraw:hover {
        color: var(--ink);
      }
      .state-note {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 12.5px;
        color: var(--muted);
        margin-top: 6px;
      }
      .state-note.bad {
        color: var(--risk);
      }
      .state-note .icon {
        font-size: 15px;
      }
      .elig-row {
        display: flex;
        align-items: center;
        gap: 10px;
        flex-wrap: wrap;
      }

      /* Phone width (2026-09-22): this label is 10px, the smallest text in the app,
   and it is the one that tells a student a field is required for their
   placement profile. Mobile only — the desktop builder is unchanged. It has to
   live here rather than in the builder's own stylesheet, because Angular scopes
   a component's styles to its OWN template and this span is rendered by this
   child component. */
      @media (max-width: 640px) {
        .reqp {
          font-size: 11px;
        }
      }
    `,
  ],
  template: `
    <div class="card">
      <h3>Placement policy</h3>
      <div class="desc">
        The terms every placement season runs on. Accepting them is what puts you in front of
        recruiters.
      </div>

      @if (acceptedOn(); as on) {
        <div class="notice accept-note">
          <span class="icon">verified</span>
          <div>
            <b>Accepted on {{ on }}.</b> A fresh acceptance is required at the start of every
            placement season.
            <div>
              <button type="button" class="withdraw" (click)="withdraw()">
                Withdraw my acceptance
              </button>
            </div>
          </div>
        </div>
      } @else {
        <label class="accept-row">
          <input type="checkbox" [checked]="false" (change)="accept($any($event.target).checked)" />
          <span>
            <b>I have read and accept the placement policy for this season.</b>
            The date of your acceptance is recorded with your placement profile.
          </span>
        </label>
      }

      <ul class="terms">
        @for (t of terms; track t) {
          <li>{{ t }}</li>
        }
      </ul>
    </div>

    <div class="card">
      <h3>Placement preferences</h3>
      <div class="desc">This governs which opportunities you appear against on the Jobs page.</div>

      <!-- STATUS, not a control. Text and colour together, never colour alone. -->
      <div class="field">
        <label>Eligible for placements</label>
        @if (profile(); as p) {
          <div class="elig-row">
            @if (p.placement_eligible) {
              <span class="chip good"><span class="icon">check_circle</span>Eligible</span>
              <span class="desc" style="margin:0;">
                You appear against postings whose own cut-offs you meet.
              </span>
            } @else {
              <span class="chip risk"><span class="icon">block</span>Not eligible</span>
              <span class="desc" style="margin:0;">
                You are held out of recruiter shortlists. Speak to the placement office — this is
                set by them, not here.
              </span>
            }
          </div>
        } @else if (profileError()) {
          <div class="state-note bad"><span class="icon">error</span>{{ profileError() }}</div>
        } @else {
          <div class="state-note"><span class="icon">hourglass_empty</span>Loading…</div>
        }
      </div>

      <div class="grid2" style="margin-top:6px;">
        <div class="field">
          <label
            >Interested in jobs <span class="req">*</span
            ><span class="reqp">Required for placement profile</span></label
          >
          <select
            class="ctrl"
            [disabled]="profile() === null || saving()"
            [ngModel]="jobsChoice()"
            (ngModelChange)="setInterest('interested_in_jobs', $event)"
          >
            <option value="yes">Yes — full-time roles</option>
            <option value="no">No</option>
          </select>
        </div>
        <div class="field">
          <label
            >Interested in internships <span class="req">*</span
            ><span class="reqp">Required for placement profile</span></label
          >
          <select
            class="ctrl"
            [disabled]="profile() === null || saving()"
            [ngModel]="internshipsChoice()"
            (ngModelChange)="setInterest('interested_in_internships', $event)"
          >
            <option value="yes">Yes — internships</option>
            <option value="no">No</option>
          </select>
        </div>
      </div>

      @if (saveError(); as err) {
        <div class="state-note bad"><span class="icon">error</span>{{ err }}</div>
      } @else if (saving()) {
        <div class="state-note"><span class="icon">progress_activity</span>Saving…</div>
      } @else if (savedOnce()) {
        <div class="state-note"><span class="icon">check_circle</span>Saved to your profile.</div>
      }

      <div class="notice info" style="margin:10px 0 0;">
        <span class="icon">visibility</span>
        <div>
          These two answers are saved to your placement profile as soon as you change them — they
          are not part of the resume draft, and they take effect on the Jobs page immediately.
        </div>
      </div>
    </div>
  `,
})
export class RbPolicyComponent {
  private readonly svc = inject(ResumeBuilderService);
  private readonly identity = inject(ResumeIdentityService);

  readonly terms = POLICY_TERMS;

  /**
   * The placement flags, read from the ONE cache the whole builder shares.
   * Local only while a write is in flight; see setInterest.
   */
  readonly profile = computed<PlacementProfile | null>(() => {
    const pending = this.pending();
    if (pending) return pending;
    const p = this.identity.profile();
    return p
      ? {
          placement_eligible: p.placement_eligible,
          interested_in_jobs: p.interested_in_jobs,
          interested_in_internships: p.interested_in_internships,
        }
      : null;
  });
  private readonly pending = signal<PlacementProfile | null>(null);
  readonly profileError = computed(() =>
    this.identity.noProfileRow()
      ? 'Your placement profile has not been created yet. Open the Profile screen once and it appears here.'
      : this.identity.error(),
  );
  readonly saving = signal(false);
  readonly saveError = signal<string | null>(null);
  readonly savedOnce = signal(false);

  readonly jobsChoice = computed(() => (this.profile()?.interested_in_jobs ? 'yes' : 'no'));
  readonly internshipsChoice = computed(() =>
    this.profile()?.interested_in_internships ? 'yes' : 'no',
  );

  model: PolicyModel = { accepted_at: null };

  private seeded = this.svc.loaded();

  constructor() {
    this.seed();
    effect(() => {
      if (this.svc.loaded() && !this.seeded) {
        this.seeded = true;
        this.seed();
      }
    });
    void this.identity.load();
  }

  /** "14 Jul 2026", or null when the policy has not been accepted. */
  acceptedOn(): string | null {
    const iso = this.model.accepted_at;
    if (!iso) return null;
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  }

  accept(checked: boolean): void {
    if (!checked) return;
    this.model.accepted_at = new Date().toISOString();
    this.push();
  }

  /** Undo an acceptance. See the class comment: a season-scoped promise needs
   *  a way back, or the first mis-click is permanent. */
  withdraw(): void {
    this.model.accepted_at = null;
    this.push();
  }

  private seed(): void {
    const s = (this.svc.section('policy', {}) ?? {}) as Partial<PolicyModel>;
    this.model = { accepted_at: typeof s.accepted_at === 'string' ? s.accepted_at : null };
  }

  push(): void {
    this.svc.patch('policy', this.model);
  }

  // --- the two flags that live on the student profile -----------------------

  async setInterest(
    field: 'interested_in_jobs' | 'interested_in_internships',
    choice: string,
  ): Promise<void> {
    const current = this.profile();
    if (!current || this.saving()) return;
    const value = choice === 'yes';
    if (current[field] === value) return;

    // Optimistic, then reconciled with the SERVER'S answer — and rolled back if
    // the write fails, because the one thing this section must never do again
    // is show a setting the server does not hold.
    this.pending.set({ ...current, [field]: value });
    this.saving.set(true);
    this.saveError.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/student/profile`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [field]: value }),
      });
      if (!res.ok) {
        this.saveError.set('Could not save that preference. Please try again.');
        return;
      }
      const body = (await res.json()) as PlacementProfile;
      // The shared cache is what every other section reads, so it is what gets
      // the answer — including `placement_eligible`, in case the office changed
      // it while this screen was open.
      this.identity.applyProfile({
        placement_eligible: !!body.placement_eligible,
        interested_in_jobs: !!body.interested_in_jobs,
        interested_in_internships: !!body.interested_in_internships,
      });
      this.savedOnce.set(true);
    } catch {
      this.saveError.set('Could not reach the server.');
    } finally {
      // Whatever happened, the displayed value goes back to being the cache's:
      // on success the cache now holds the server's answer, on failure it still
      // holds the pre-write one.
      this.pending.set(null);
      this.saving.set(false);
    }
  }
}
