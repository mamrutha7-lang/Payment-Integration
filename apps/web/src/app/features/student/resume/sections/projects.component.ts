/**
 * Resume Builder — Projects section (data-p="projects" in
 * docs/design-v2/resume-builder.html).
 *
 * Repeatable entries under the `projects` key:
 *   { title, description, tech: string[], link }.
 * `tech` is entered with the global .taginput / .chipx pattern (Enter to add,
 * click the × to remove). Reads via svc.section('projects', []), writes the
 * whole array back with svc.patch('projects', arr). Global classes only.
 */

import { NgTemplateOutlet } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ResumeBuilderService } from '../resume-builder.service';

interface ProjectEntry {
  title: string;
  description: string;
  tech: string[];
  link: string;
}

const KEY = 'projects';

function blank(): ProjectEntry {
  return { title: '', description: '', tech: [], link: '' };
}

@Component({
  selector: 'rb-projects',
  standalone: true,
  imports: [FormsModule, NgTemplateOutlet],
  template: `
    <div class="card">
      <h3>
        Projects
        <button class="btn primary right" style="padding:7px 13px;" (click)="startAdd()">
          <span class="icon">add</span> Add project
        </button>
      </h3>
      <div class="desc">
        Academic, capstone or personal projects. Each accepts a description, tech/skills used, and a
        link or attachment.
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
            <h4>{{ e.title || 'Untitled project' }}</h4>
            @if (e.tech?.length) {
              <div class="meta">{{ e.tech.join(' · ') }}</div>
            }
            @if (e.description) {
              <div class="desc">{{ e.description }}</div>
            }
            @if (e.link) {
              <div class="meta">
                <span class="icon" style="font-size:13px">link</span> {{ e.link }}
              </div>
            }
          </div>
        }
      }

      @if (entries().length === 0 && editing() === null) {
        <div class="empty">
          <span class="icon">lightbulb</span>
          <p>No projects added yet.</p>
          <button class="btn primary" style="margin-top:12px;" (click)="startAdd()">
            <span class="icon">add</span> Add your first project
          </button>
        </div>
      }
    </div>

    <ng-template #form>
      <div class="entry">
        <div class="field">
          <label>Project title</label>
          <input
            class="ctrl"
            [(ngModel)]="draft.title"
            placeholder="e.g. Inventory forecasting model"
          />
        </div>
        <div class="field">
          <label>Description</label>
          <textarea
            class="ctrl"
            [(ngModel)]="draft.description"
            placeholder="What the project does and your contribution…"
          ></textarea>
        </div>
        <div class="field">
          <label>Tech / skills used</label>
          <div class="taginput">
            @for (t of draft.tech; track $index) {
              <span class="chipx"
                >{{ t }} <span class="icon" (click)="removeTech($index)">close</span></span
              >
            }
            <input
              [(ngModel)]="techInput"
              (keydown.enter)="addTech($event)"
              placeholder="Add a tech and press Enter"
            />
          </div>
        </div>
        <div class="field">
          <label>Link</label>
          <input
            class="ctrl"
            [(ngModel)]="draft.link"
            placeholder="https:// repository, demo or write-up"
          />
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
export class ProjectsSection {
  private readonly svc = inject(ResumeBuilderService);

  readonly entries = computed(() => this.svc.section(KEY, []) as ProjectEntry[]);
  readonly editing = signal<number | null>(null);
  /** The entry whose delete is awaiting confirmation; null when none is. */
  readonly pendingDelete = signal<number | null>(null);

  draft: ProjectEntry = blank();
  techInput = '';

  addTech(ev: Event): void {
    ev.preventDefault();
    const v = this.techInput.trim();
    if (!v) return;
    if (!this.draft.tech.includes(v)) this.draft.tech = [...this.draft.tech, v];
    this.techInput = '';
  }

  removeTech(i: number): void {
    this.draft.tech = this.draft.tech.filter((_, idx) => idx !== i);
  }

  startAdd(): void {
    this.draft = blank();
    this.techInput = '';
    this.editing.set(-1);
  }

  startEdit(i: number): void {
    const e = this.entries()[i];
    this.draft = { ...e, tech: [...(e.tech ?? [])] };
    this.techInput = '';
    this.editing.set(i);
  }

  cancel(): void {
    this.editing.set(null);
  }

  commit(): void {
    if (!this.draft.title.trim()) return;
    const arr = [...this.entries()];
    const i = this.editing();
    const record: ProjectEntry = { ...this.draft, tech: [...this.draft.tech] };
    if (i === -1 || i === null) arr.push(record);
    else arr[i] = record;
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
