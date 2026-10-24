/**
 * Evidence-backed Skills — the resume section that only lets you claim what a
 * mentor has confirmed.
 *
 * Every row shows its status, what that status means, the proof behind it and
 * when it was verified. Only VERIFIED rows carry an Include toggle; the others
 * are shown because the student needs to know where their claim stands, not so
 * they can put them in a document. The service enforces the same rule on the way
 * out, so this template is the polite half of the guard rather than the whole
 * of it.
 */

import { DatePipe } from '@angular/common';
import { Component, inject } from '@angular/core';

import { ResumeEvidenceService, type EvidenceStatus } from '../resume-evidence.service';

@Component({
  selector: 'rb-evidence-skills',
  standalone: true,
  imports: [DatePipe],
  template: `
    <div class="card">
      <h3>
        Evidence-backed skills
        <span class="ev-count"
          >{{ ev.includedCount() }} of {{ ev.verifiedCount() }} verified included</span
        >
      </h3>
      <div class="desc">
        Only skills a mentor has verified can go on the resume. Anything still in review stays here
        until it is confirmed.
      </div>

      @if (ev.error()) {
        <div class="state-note"><span class="icon">error</span>{{ ev.error() }}</div>
      } @else if (ev.rows() === null) {
        <div class="state-note"><span class="icon">hourglass_empty</span>Loading your skills…</div>
      } @else if (ev.rows()!.length === 0) {
        <div class="state-note">
          <span class="icon">info</span>
          No evidence-backed skills yet — verify one in Skilling first.
        </div>
      } @else {
        @for (r of ev.rows()!; track r.slug) {
          <div class="ev-row" [class.ev-row--muted]="!r.includable">
            <div class="ev-main">
              <div class="ev-name">
                {{ r.name }}
                <span class="chip {{ ev.chip(r.status).cls }}">{{ ev.chip(r.status).label }}</span>
              </div>
              <div class="ev-note">{{ r.statusNote }}</div>
              <div class="ev-meta">
                <span>{{ r.category }}</span>
                @if (r.verifiedOn) {
                  <span>· Verified {{ r.verifiedOn | date: 'd MMM y' }}</span>
                }
                @if (r.proofUploadId) {
                  <a [href]="ev.proofUrl(r.proofUploadId)" target="_blank" rel="noopener">
                    · View proof
                  </a>
                }
              </div>
            </div>
            @if (r.includable) {
              <button
                type="button"
                class="ev-include"
                [class.ev-include--on]="r.included"
                [attr.aria-pressed]="r.included"
                (click)="ev.toggle(r.slug)"
              >
                {{ r.included ? 'Included ✓' : 'Include' }}
              </button>
            } @else {
              <span class="ev-locked" title="Only a mentor-verified skill can go on the resume">
                <span class="icon">lock</span>
              </span>
            }
          </div>
        }
      }
    </div>
  `,
  styleUrl: './evidence-skills.component.scss',
})
export class RbEvidenceSkillsComponent {
  readonly ev = inject(ResumeEvidenceService);

  constructor() {
    void this.ev.load();
  }
}
