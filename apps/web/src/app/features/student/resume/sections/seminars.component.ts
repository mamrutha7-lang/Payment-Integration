/**
 * Resume Builder — Seminars / trainings / workshops section
 * (data-p="seminars" in docs/design-v2/resume-builder.html).
 *
 * Short-form learning that is not a full certification. Repeatable entries
 * under the `seminars` key: { title, provider, date }. Reads via
 * svc.section('seminars', []), writes the whole array back with
 * svc.patch('seminars', arr). Global reep-v2 classes only.
 */

import { NgTemplateOutlet } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ResumeBuilderService } from '../resume-builder.service';

interface SeminarEntry {
  title: string;
  provider: string;
  date: string;
}

const KEY = 'seminars';

function blank(): SeminarEntry {
  return { title: '', provider: '', date: '' };
}

@Component({
  selector: 'rb-seminars',
  standalone: true,
  imports: [FormsModule, NgTemplateOutlet],
  template: `
    <div class="card">
      <h3>
        Seminars / trainings / workshops
        <button class="btn primary right" style="padding:7px 13px;" (click)="startAdd()">
          <span class="icon">add</span> Add training
        </button>
      </h3>
      <div class="desc">Short-form learning that is not a full certification.</div>

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
            <h4>{{ e.title || 'Untitled training' }}</h4>
            @if (e.provider) {
              <div class="org">{{ e.provider }}</div>
            }
            @if (e.date) {
              <div class="meta">{{ e.date }}</div>
            }
          </div>
        }
      }

      @if (entries().length === 0 && editing() === null) {
        <div class="empty">
          <span class="icon">groups</span>
          <p>No trainings added.</p>
        </div>
      }
    </div>

    <ng-template #form>
      <div class="entry">
        <div class="field">
          <label>Title</label>
          <input
            class="ctrl"
            [(ngModel)]="draft.title"
            placeholder="e.g. Data Storytelling Workshop"
          />
        </div>
        <div class="grid2">
          <div class="field">
            <label>Provider</label>
            <input class="ctrl" [(ngModel)]="draft.provider" placeholder="Who ran it" />
          </div>
          <div class="field">
            <label>Date</label>
            <input class="ctrl" [(ngModel)]="draft.date" placeholder="Feb 2026" />
          </div>
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
export class SeminarsSection {
  private readonly svc = inject(ResumeBuilderService);

  readonly entries = computed(() => this.svc.section(KEY, []) as SeminarEntry[]);
  readonly editing = signal<number | null>(null);
  /** The entry whose delete is awaiting confirmation; null when none is. */
  readonly pendingDelete = signal<number | null>(null);

  draft: SeminarEntry = blank();

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
