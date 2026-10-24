/**
 * Resume Builder — Internship section (data-p="internship" in
 * docs/design-v2/resume-builder.html).
 *
 * Same record shape as Experience but kept a separate section (recruiters weigh
 * internships differently). Array under the `internship` key:
 *   { title, org, sector, start, end, location, description }.
 * Reads via svc.section('internship', []), writes the whole array back with
 * svc.patch('internship', arr). Reuses the global reep-v2 classes only.
 */

import { NgTemplateOutlet } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ResumeBuilderService } from '../resume-builder.service';

interface InternshipEntry {
  title: string;
  org: string;
  sector: string;
  start: string;
  end: string;
  location: string;
  description: string;
}

const KEY = 'internship';

function blank(): InternshipEntry {
  return { title: '', org: '', sector: '', start: '', end: '', location: '', description: '' };
}

@Component({
  selector: 'rb-internship',
  standalone: true,
  imports: [FormsModule, NgTemplateOutlet],
  template: `
    <div class="card">
      <h3>
        Internships
        <button class="btn primary right" style="padding:7px 13px;" (click)="startAdd()">
          <span class="icon">add</span> Add internship
        </button>
      </h3>
      <div class="desc">
        Kept separate from professional experience because recruiters weigh them differently.
      </div>

      @if (editing() === -1) {
        <ng-container [ngTemplateOutlet]="form"></ng-container>
      }

      @for (e of entries(); track $index) {
        @if (editing() === $index) {
          <ng-container [ngTemplateOutlet]="form"></ng-container>
        } @else {
          <div class="entry">
            <div class="tools">
              <!-- Two-step delete: one click asks, the second removes. The card
                   can hold a paragraph the student typed, and the builder
                   autosaves, so a single-click delete was unrecoverable. -->
              @if (pendingDelete() === $index) {
                <span style="font-size:11.5px; color:var(--risk); margin-right:2px;">Delete?</span>
                <button (click)="remove($index)" title="Yes, delete this entry">
                  <span class="icon" style="font-size:17px; color:var(--risk)">check</span>
                </button>
                <button (click)="cancelRemove()" title="Keep it">
                  <span class="icon" style="font-size:17px">close</span>
                </button>
              } @else {
                <button (click)="startEdit($index)" title="Edit">
                  <span class="icon" style="font-size:17px">edit</span>
                </button>
                <button (click)="askRemove($index)" title="Delete">
                  <span class="icon" style="font-size:17px">delete</span>
                </button>
              }
            </div>
            <h4>{{ e.title || 'Untitled internship' }}</h4>
            @if (orgLine(e)) {
              <div class="org">{{ orgLine(e) }}</div>
            }
            @if (metaLine(e)) {
              <div class="meta">{{ metaLine(e) }}</div>
            }
            @if (e.description) {
              <div class="desc">{{ e.description }}</div>
            }
          </div>
        }
      }

      @if (entries().length === 0 && editing() === null) {
        <div class="empty">
          <span class="icon">work_history</span>
          <p>No internships added yet.</p>
          <button class="btn primary" style="margin-top:12px;" (click)="startAdd()">
            <span class="icon">add</span> Add your first internship
          </button>
        </div>
      }
    </div>

    <ng-template #form>
      <div class="entry">
        <div class="grid2">
          <div class="field">
            <label>Role / title</label>
            <input class="ctrl" [(ngModel)]="draft.title" placeholder="e.g. Software Intern" />
          </div>
          <div class="field">
            <label>Organisation</label>
            <input class="ctrl" [(ngModel)]="draft.org" placeholder="Company name" />
          </div>
        </div>
        <div class="grid2">
          <div class="field">
            <label>Sector</label>
            <input class="ctrl" [(ngModel)]="draft.sector" placeholder="e.g. IT Services" />
          </div>
          <div class="field">
            <label>Location</label>
            <input class="ctrl" [(ngModel)]="draft.location" placeholder="City, State, Country" />
          </div>
        </div>
        <div class="grid2">
          <div class="field">
            <label>Start</label>
            <input class="ctrl" [(ngModel)]="draft.start" placeholder="Jun 2023" />
          </div>
          <div class="field">
            <label>End</label>
            <input class="ctrl" [(ngModel)]="draft.end" placeholder="Aug 2023 or Present" />
          </div>
        </div>
        <div class="field">
          <label>Description</label>
          <textarea
            class="ctrl"
            [(ngModel)]="draft.description"
            placeholder="What you did and delivered…"
          ></textarea>
        </div>
        <div style="display:flex; gap:8px; justify-content:flex-end; margin-top:4px;">
          <button class="btn" (click)="cancel()">Cancel</button>
          <button class="btn primary" [disabled]="!draft.title.trim()" (click)="commit()">
            <span class="icon">check</span> Save
          </button>
        </div>
      </div>
    </ng-template>
  `,
})
export class InternshipSection {
  private readonly svc = inject(ResumeBuilderService);

  readonly entries = computed(() => this.svc.section(KEY, []) as InternshipEntry[]);
  readonly editing = signal<number | null>(null);
  /** The entry whose delete is awaiting confirmation; null when none is. */
  readonly pendingDelete = signal<number | null>(null);

  draft: InternshipEntry = blank();

  orgLine(e: InternshipEntry): string {
    return [e.org, e.sector].filter(Boolean).join(' · ');
  }

  metaLine(e: InternshipEntry): string {
    const range = [e.start, e.end].filter(Boolean).join(' — ');
    return [range, e.location].filter(Boolean).join(' · ');
  }

  startAdd(): void {
    this.draft = blank();
    this.editing.set(-1);
  }

  startEdit(i: number): void {
    this.draft = { ...this.entries()[i] };
    this.editing.set(i);
  }

  cancel(): void {
    this.editing.set(null);
  }

  commit(): void {
    if (!this.draft.title.trim()) return;
    const arr = [...this.entries()];
    const i = this.editing();
    if (i === -1 || i === null) arr.push({ ...this.draft });
    else arr[i] = { ...this.draft };
    this.svc.patch(KEY, arr);
    this.editing.set(null);
  }

  /** First click on the trash: ask. */
  askRemove(i: number): void {
    this.pendingDelete.set(i);
  }

  cancelRemove(): void {
    this.pendingDelete.set(null);
  }

  remove(i: number): void {
    this.pendingDelete.set(null);
    const arr = this.entries().filter((_, idx) => idx !== i);
    this.svc.patch(KEY, arr);
    if (this.editing() === i) this.editing.set(null);
  }
}
