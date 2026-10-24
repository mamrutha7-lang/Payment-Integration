/**
 * Resume Builder — "References" section (data-p="references").
 *
 * Editable slice `data.references` is an array of referees:
 *   [{ name, designation, org, relationship, email, phone }]
 *
 * Each referee renders as an editable `.entry` card with a delete tool; the
 * dashed suggestion card offers the faculty mentor as a one-click reference.
 * Reads via section('references', []) and writes the whole array back with
 * patch('references', …) on every change. Markup reuses the global reep-v2
 * classes (.card / .entry / .tools / .field / .empty); nothing redefined here.
 *
 * The mentor in the suggestion card is the student's REAL assigned mentor, read
 * from the shared identity cache; the card is absent when nobody is assigned.
 * See the class body for what it used to be and why that mattered.
 */

import { Component, computed, effect, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ResumeBuilderService } from '../resume-builder.service';
import { ResumeIdentityService } from '../resume-identity.service';

interface Reference {
  name: string;
  designation: string;
  org: string;
  relationship: string;
  email: string;
  phone: string;
}

type RefField = keyof Reference;

@Component({
  selector: 'rb-references',
  standalone: true,
  imports: [FormsModule],
  template: `
    <div class="card">
      <h3>
        References
        <button class="btn primary right" style="padding:7px 13px;" (click)="addRef()">
          <span class="icon">add</span> Add reference
        </button>
      </h3>
      <div class="desc">
        Name, designation, organisation, relationship, email and phone. Your faculty mentor can be
        added with one click.
      </div>

      <!-- Only ever the REAL assigned mentor. No mentor, no card. -->
      @if (mentorName(); as mentor) {
        <div class="entry" style="border-style:dashed;">
          <h4>
            {{ mentor }}
            <span class="tag evi"
              ><span class="icon" style="font-size:12px">bolt</span>Your mentor</span
            >
          </h4>
          <div class="org">{{ mentorOrg() }}</div>
          <div class="meta">Suggested — add with one click</div>
          <button
            class="btn primary"
            style="margin-top:10px; padding:6px 12px; font-size:12px;"
            [disabled]="mentorAlreadyListed()"
            (click)="addMentor()"
          >
            <span class="icon">{{ mentorAlreadyListed() ? 'check' : 'add' }}</span>
            {{ mentorAlreadyListed() ? 'Already a referee' : 'Add as reference' }}
          </button>
        </div>
      }

      @for (ref of model; track $index) {
        <div class="entry">
          <div class="tools">
            <button (click)="removeRef($index)">
              <span class="icon" style="font-size:17px">delete</span>
            </button>
          </div>
          <div class="grid2">
            <div class="field">
              <label>Name</label>
              <input
                class="ctrl"
                placeholder="Full name"
                [ngModel]="ref.name"
                (ngModelChange)="setField($index, 'name', $event)"
              />
            </div>
            <div class="field">
              <label>Designation</label>
              <input
                class="ctrl"
                placeholder="Designation"
                [ngModel]="ref.designation"
                (ngModelChange)="setField($index, 'designation', $event)"
              />
            </div>
            <div class="field">
              <label>Organisation</label>
              <input
                class="ctrl"
                placeholder="Organisation"
                [ngModel]="ref.org"
                (ngModelChange)="setField($index, 'org', $event)"
              />
            </div>
            <div class="field">
              <label>Relationship</label>
              <input
                class="ctrl"
                placeholder="e.g. Faculty Mentor, Manager"
                [ngModel]="ref.relationship"
                (ngModelChange)="setField($index, 'relationship', $event)"
              />
            </div>
            <div class="field">
              <label>Email</label>
              <input
                class="ctrl"
                type="email"
                placeholder="name@example.com"
                [ngModel]="ref.email"
                (ngModelChange)="setField($index, 'email', $event)"
              />
            </div>
            <div class="field">
              <label>Phone</label>
              <input
                class="ctrl"
                placeholder="Phone number"
                [ngModel]="ref.phone"
                (ngModelChange)="setField($index, 'phone', $event)"
              />
            </div>
          </div>
        </div>
      }

      @if (model.length === 0) {
        <div class="empty" style="padding:26px;">
          <span class="icon">handshake</span>
          <p>No references added yet.</p>
        </div>
      }
    </div>
  `,
})
export class RbReferencesComponent {
  private readonly svc = inject(ResumeBuilderService);
  private readonly identity = inject(ResumeIdentityService);

  /**
   * THE SUGGESTION USED TO BE A HARD-CODED PERSON — a name, a title and a batch
   * carried over from the mockup and offered to every student with a one-click
   * "Add as reference" button. That person does not work here. One click put
   * them on a document a recruiter may ring, as a referee the student would
   * then have had to explain.
   *
   * It is now the student's actual assigned mentor, from `mentor_name` on
   * `GET /api/student/profile`, and the card does not render at all when nobody
   * is assigned. No card is the honest state: a faculty account is not a mentor
   * until the Main Admin assigns them a student.
   */
  readonly mentorName = this.identity.mentorName;
  readonly mentorOrg = computed(() => {
    const inst = this.identity.profile()?.institution;
    return ['Faculty Mentor', inst?.college_code, inst?.batch_label].filter(Boolean).join(' · ');
  });
  /** A referee added twice is a referee printed twice on the page. */
  readonly mentorAlreadyListed = computed(() => {
    const name = this.mentorName().trim();
    return !!name && this.model.some((r) => r.name.trim() === name);
  });

  model: Reference[] = [];

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

  private blank(): Reference {
    return { name: '', designation: '', org: '', relationship: '', email: '', phone: '' };
  }

  private seed(): void {
    const s = this.svc.section('references', []) as unknown;
    const rows = Array.isArray(s) ? (s as Partial<Reference>[]) : [];
    this.model = rows.map((r) => ({
      name: r?.name ?? '',
      designation: r?.designation ?? '',
      org: r?.org ?? '',
      relationship: r?.relationship ?? '',
      email: r?.email ?? '',
      phone: r?.phone ?? '',
    }));
  }

  private push(): void {
    this.svc.patch('references', this.model);
  }

  addRef(): void {
    this.model = [...this.model, this.blank()];
    this.push();
  }

  addMentor(): void {
    const name = this.mentorName().trim();
    // No mentor, nothing to add. The button is hidden in that state; this is
    // the guard that survives someone re-rendering the card unconditionally.
    if (!name || this.mentorAlreadyListed()) return;
    this.model = [
      ...this.model,
      {
        name,
        designation: 'Faculty Mentor',
        org: this.identity.profile()?.institution?.college_name ?? '',
        relationship: 'Faculty Mentor',
        // Left blank deliberately: the mentor's own email and phone are not on
        // this student's record, and inventing them is what this whole change
        // exists to stop. The student fills them in, or the referee is listed
        // without contact details.
        email: '',
        phone: '',
      },
    ];
    this.push();
  }

  setField(i: number, key: RefField, v: string): void {
    this.model = this.model.map((r, idx) => (idx === i ? { ...r, [key]: v } : r));
    this.push();
  }

  removeRef(i: number): void {
    this.model = this.model.filter((_, x) => x !== i);
    this.push();
  }
}
