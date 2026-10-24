/**
 * Resume Builder — Publications section (data-p="publications" in
 * docs/design-v2/resume-builder.html).
 *
 * Repeatable entries under the `publications` key:
 *   { title, publisher, date, coauthors, doi }.
 * Reads via svc.section('publications', []), writes the whole array back with
 * svc.patch('publications', arr). Global reep-v2 classes only.
 */

import { NgTemplateOutlet } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ResumeBuilderService } from '../resume-builder.service';

interface PublicationEntry {
  title: string;
  publisher: string;
  date: string;
  coauthors: string;
  doi: string;
}

const KEY = 'publications';

function blank(): PublicationEntry {
  return { title: '', publisher: '', date: '', coauthors: '', doi: '' };
}

@Component({
  selector: 'rb-publications',
  standalone: true,
  imports: [FormsModule, NgTemplateOutlet],
  template: `
    <div class="card">
      <h3>
        Publications / research / white papers
        <button class="btn primary right" style="padding:7px 13px;" (click)="startAdd()">
          <span class="icon">add</span> Add publication
        </button>
      </h3>
      <div class="desc">Title, publisher or journal, date, co-authors, DOI or link.</div>

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
            <h4>{{ e.title || 'Untitled publication' }}</h4>
            @if (e.publisher) {
              <div class="org">{{ e.publisher }}</div>
            }
            @if (metaLine(e)) {
              <div class="meta">{{ metaLine(e) }}</div>
            }
            @if (e.doi) {
              <div class="meta">
                <span class="icon" style="font-size:13px">link</span> {{ e.doi }}
              </div>
            }
          </div>
        }
      }

      @if (entries().length === 0 && editing() === null) {
        <div class="empty">
          <span class="icon">menu_book</span>
          <p>No research papers added.</p>
        </div>
      }
    </div>

    <ng-template #form>
      <div class="entry">
        <div class="field">
          <label>Title</label>
          <input class="ctrl" [(ngModel)]="draft.title" placeholder="Paper or article title" />
        </div>
        <div class="grid2">
          <div class="field">
            <label>Publisher / journal</label>
            <input class="ctrl" [(ngModel)]="draft.publisher" placeholder="e.g. IEEE Access" />
          </div>
          <div class="field">
            <label>Date</label>
            <input class="ctrl" [(ngModel)]="draft.date" placeholder="Mar 2026" />
          </div>
        </div>
        <div class="grid2">
          <div class="field">
            <label>Co-authors</label>
            <input class="ctrl" [(ngModel)]="draft.coauthors" placeholder="Comma-separated names" />
          </div>
          <div class="field">
            <label>DOI / link</label>
            <input class="ctrl" [(ngModel)]="draft.doi" placeholder="10.xxxx/… or a URL" />
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
export class PublicationsSection {
  private readonly svc = inject(ResumeBuilderService);

  readonly entries = computed(() => this.svc.section(KEY, []) as PublicationEntry[]);
  readonly editing = signal<number | null>(null);
  /** The entry whose delete is awaiting confirmation; null when none is. */
  readonly pendingDelete = signal<number | null>(null);

  draft: PublicationEntry = blank();

  metaLine(e: PublicationEntry): string {
    return [e.date, e.coauthors].filter(Boolean).join(' · ');
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
