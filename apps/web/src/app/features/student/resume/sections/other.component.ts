/**
 * Resume Builder — "Other Details" section (data-p="other").
 *
 * Editable slice `data.other`:
 *   { career_objective:'', key_expertise:[], achievements:[], awards:[],
 *     co_curricular:[], extra_curricular:[], web_links:[{type,url}] }
 *
 * Reads its slice from ResumeBuilderService via section('other', …) and writes
 * the whole normalized model back with patch('other', …) on every change — it
 * never fetches or PUTs resume-profile itself. Markup mirrors the mockup panel
 * exactly and reuses the global reep-v2 classes (.card / .taginput / .rowline /
 * .addlink / .iconbtn / .notice); nothing is redefined here.
 */

import { Component, effect, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ResumeBuilderService } from '../resume-builder.service';

interface WebLink {
  type: string;
  url: string;
}

interface OtherModel {
  career_objective: string;
  key_expertise: string[];
  achievements: string[];
  awards: string[];
  co_curricular: string[];
  extra_curricular: string[];
  web_links: WebLink[];
}

/** The four plain string-list sub-cards edited through the shared row helpers. */
type ListKey = 'achievements' | 'awards' | 'co_curricular' | 'extra_curricular';

@Component({
  selector: 'rb-other',
  standalone: true,
  imports: [FormsModule],
  template: `
    <div class="card">
      <h3>Career objective</h3>
      <div class="desc">
        What you want from the role and what you bring. This is the paragraph the generated resume
        opens with.
      </div>
      <textarea
        class="ctrl"
        maxlength="6000"
        placeholder="Describe your expectations, both for yourself and the organization…"
        [ngModel]="model.career_objective"
        (ngModelChange)="model.career_objective = $event; push()"
      ></textarea>
      <div style="text-align:right; font-size:11.5px; color:var(--muted); margin-top:5px;">
        {{ model.career_objective.length }} / 6000
      </div>
    </div>

    <div class="card">
      <h3>Key expertise</h3>
      <div class="desc">
        Press Enter after each skill. A comma- or space-separated string is stored as one skill,
        which is almost never what you want.
      </div>
      <div class="taginput">
        @for (skill of model.key_expertise; track $index) {
          <span class="chipx"
            >{{ skill }} <span class="icon" (click)="removeTag($index)">close</span></span
          >
        }
        <input
          placeholder="Add a skill and press Enter"
          [(ngModel)]="newSkill"
          (keydown.enter)="addTag($event)"
        />
      </div>
      <div class="notice evi" style="margin:14px 0 0;">
        <span class="icon">insights</span>
        <div>
          Skills matched to your REEP badges are weighted higher when a job's
          <b>match percentage</b> is calculated.
        </div>
      </div>
    </div>

    <div class="grid2">
      <div class="card" style="margin-bottom:0;">
        <h3>Achievements</h3>
        @for (item of model.achievements; track $index) {
          <div class="rowline">
            <input
              class="ctrl"
              placeholder="Enter an achievement"
              [ngModel]="item"
              (ngModelChange)="setItem('achievements', $index, $event)"
            />
            <button class="iconbtn" (click)="removeItem('achievements', $index)">
              <span class="icon">delete</span>
            </button>
          </div>
        }
        <button class="addlink" (click)="addItem('achievements')">
          <span class="icon" style="font-size:15px">add</span> Add achievement
        </button>
      </div>
      <div class="card" style="margin-bottom:0;">
        <h3>Awards &amp; scholarships</h3>
        @for (item of model.awards; track $index) {
          <div class="rowline">
            <input
              class="ctrl"
              placeholder="Enter an award or scholarship"
              [ngModel]="item"
              (ngModelChange)="setItem('awards', $index, $event)"
            />
            <button class="iconbtn" (click)="removeItem('awards', $index)">
              <span class="icon">delete</span>
            </button>
          </div>
        }
        <button class="addlink" (click)="addItem('awards')">
          <span class="icon" style="font-size:15px">add</span> Add award
        </button>
      </div>
    </div>

    <div class="grid2" style="margin-top:18px;">
      <div class="card" style="margin-bottom:0;">
        <h3>Co-curricular activities</h3>
        @for (item of model.co_curricular; track $index) {
          <div class="rowline">
            <input
              class="ctrl"
              placeholder="Enter an activity"
              [ngModel]="item"
              (ngModelChange)="setItem('co_curricular', $index, $event)"
            />
            <button class="iconbtn" (click)="removeItem('co_curricular', $index)">
              <span class="icon">delete</span>
            </button>
          </div>
        }
        <button class="addlink" (click)="addItem('co_curricular')">
          <span class="icon" style="font-size:15px">add</span> Add activity
        </button>
      </div>
      <div class="card" style="margin-bottom:0;">
        <h3>Extra-curricular activities</h3>
        @for (item of model.extra_curricular; track $index) {
          <div class="rowline">
            <input
              class="ctrl"
              placeholder="Enter an activity"
              [ngModel]="item"
              (ngModelChange)="setItem('extra_curricular', $index, $event)"
            />
            <button class="iconbtn" (click)="removeItem('extra_curricular', $index)">
              <span class="icon">delete</span>
            </button>
          </div>
        }
        <button class="addlink" (click)="addItem('extra_curricular')">
          <span class="icon" style="font-size:15px">add</span> Add activity
        </button>
      </div>
    </div>

    <div class="card" style="margin-top:18px;">
      <h3>Web links</h3>
      @for (link of model.web_links; track $index) {
        <div class="rowline">
          <select
            class="ctrl code"
            style="width:150px;"
            [ngModel]="link.type"
            (ngModelChange)="setLinkType($index, $event)"
          >
            <option value="LinkedIn">LinkedIn</option>
            <option value="GitHub">GitHub</option>
            <option value="Portfolio">Portfolio</option>
          </select>
          <input
            class="ctrl"
            placeholder="https://…"
            [ngModel]="link.url"
            (ngModelChange)="setLinkUrl($index, $event)"
          />
          <button class="iconbtn" (click)="removeLink($index)">
            <span class="icon">delete</span>
          </button>
        </div>
      }
      <button class="addlink" (click)="addLink()">
        <span class="icon" style="font-size:15px">add</span> Add link
      </button>
    </div>
  `,
})
export class RbOtherComponent {
  private readonly svc = inject(ResumeBuilderService);

  newSkill = '';
  model: OtherModel = this.blank();

  /** True once seeded from real (loaded) data — see the constructor effect. */
  private seeded = this.svc.loaded();

  constructor() {
    this.seed();
    // The shell fires svc.load() on init; if it had not resolved by the time
    // this section mounted, re-seed once the profile arrives (without clobbering
    // later edits: this effect only tracks loaded(), never data()).
    effect(() => {
      if (this.svc.loaded() && !this.seeded) {
        this.seeded = true;
        this.seed();
      }
    });
  }

  private blank(): OtherModel {
    return {
      career_objective: '',
      key_expertise: [],
      achievements: [],
      awards: [],
      co_curricular: [],
      extra_curricular: [],
      web_links: [],
    };
  }

  /** Normalize the stored slice into a fresh, fully-shaped local model. */
  private seed(): void {
    const s = (this.svc.section('other', {}) ?? {}) as Partial<OtherModel>;
    this.model = {
      career_objective: s.career_objective ?? '',
      key_expertise: [...(s.key_expertise ?? [])],
      achievements: [...(s.achievements ?? [])],
      awards: [...(s.awards ?? [])],
      co_curricular: [...(s.co_curricular ?? [])],
      extra_curricular: [...(s.extra_curricular ?? [])],
      web_links: (s.web_links ?? []).map((l) => ({
        type: l?.type ?? 'LinkedIn',
        url: l?.url ?? '',
      })),
    };
  }

  // Public because the career-objective textarea calls it directly from the
  // template (the list/tag/link helpers below call it internally).
  push(): void {
    this.svc.patch('other', this.model);
  }

  // --- key_expertise tag input ---
  addTag(ev: Event): void {
    ev.preventDefault();
    const v = this.newSkill.trim();
    if (!v) return;
    this.model.key_expertise = [...this.model.key_expertise, v];
    this.newSkill = '';
    this.push();
  }

  removeTag(i: number): void {
    this.model.key_expertise = this.model.key_expertise.filter((_, x) => x !== i);
    this.push();
  }

  // --- shared string-list rowlines ---
  addItem(key: ListKey): void {
    this.model[key] = [...this.model[key], ''];
    this.push();
  }

  setItem(key: ListKey, i: number, v: string): void {
    this.model[key] = this.model[key].map((x, idx) => (idx === i ? v : x));
    this.push();
  }

  removeItem(key: ListKey, i: number): void {
    this.model[key] = this.model[key].filter((_, x) => x !== i);
    this.push();
  }

  // --- web links (typed rows) ---
  addLink(): void {
    this.model.web_links = [...this.model.web_links, { type: 'LinkedIn', url: '' }];
    this.push();
  }

  setLinkType(i: number, v: string): void {
    this.model.web_links = this.model.web_links.map((l, idx) =>
      idx === i ? { ...l, type: v } : l,
    );
    this.push();
  }

  setLinkUrl(i: number, v: string): void {
    this.model.web_links = this.model.web_links.map((l, idx) => (idx === i ? { ...l, url: v } : l));
    this.push();
  }

  removeLink(i: number): void {
    this.model.web_links = this.model.web_links.filter((_, x) => x !== i);
    this.push();
  }
}
