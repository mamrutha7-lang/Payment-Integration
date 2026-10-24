/**
 * What the university already knows about this student — fetched ONCE for the
 * whole Resume Builder.
 *
 * THE LOCKED FIELDS WERE LOCKED AND EMPTY. Basic Details showed "Course" and
 * "Primary specialization" as a dash behind a SYNCED badge, and Contact Details
 * showed the primary phone as the placeholder "Synced from record" — for a
 * student whose record holds all three. `GET /api/student/profile` has returned
 * `institution.course_name`, `institution.specialization_name` and `phone` the
 * whole time; nothing on this screen asked. A student was shown a required
 * field they were forbidden to fill and that the system declined to fill for
 * them, which reads as a system that has lost their record.
 *
 * ONE OWNER, because four sections need the same answer. Basic Details wants
 * the USN, name, course and specialization; Contact wants the phone and email;
 * References wants the assigned mentor's name; Placement Policy wants the two
 * interest flags and the eligibility gate. Four independent fetches of one
 * endpoint is four chances to disagree about what the record says.
 *
 * TWO ENDPOINTS, and the second is not redundant. `/student/profile` answers
 * 404 for a student who has no `student_profiles` row yet — a real state, not
 * an error — and the USN lives on the `students` row either way, so
 * `/student/dashboard` supplies it. Without that fallback a brand-new student
 * would see their own USN disappear from a field labelled SYNCED.
 */

import { Injectable, computed, signal } from '@angular/core';

import { environment } from '../../../../environments/environment';

/** The institution block of ProfileOut — read-only, resolved via the cohort. */
export interface StudentInstitution {
  college_name: string | null;
  college_code: string | null;
  department_name: string | null;
  course_name: string | null;
  specialization_name: string | null;
  batch_label: string | null;
}

/** The fields of GET /api/student/profile this feature reads. */
export interface StudentIdentity {
  usn: string | null;
  full_name: string | null;
  phone: string | null;
  email: string | null;
  mentor_name: string | null;
  placement_eligible: boolean;
  interested_in_jobs: boolean;
  interested_in_internships: boolean;
  institution: StudentInstitution | null;
}

@Injectable({ providedIn: 'root' })
export class ResumeIdentityService {
  /** null until the first load resolves (or fails). */
  private readonly _profile = signal<StudentIdentity | null>(null);
  readonly profile = this._profile.asReadonly();

  /** The USN, which survives even when there is no profile row. */
  readonly usn = signal<string>('');

  /** Set when the profile row does not exist yet — a state, not a failure. */
  readonly noProfileRow = signal(false);
  readonly error = signal<string | null>(null);

  readonly courseName = computed(() => this._profile()?.institution?.course_name ?? '');
  readonly specializationName = computed(
    () => this._profile()?.institution?.specialization_name ?? '',
  );
  readonly phone = computed(() => this._profile()?.phone ?? '');
  readonly mentorName = computed(() => this._profile()?.mentor_name ?? '');

  private started = false;

  /** One fetch per session; later callers ride the first one's result. */
  async load(): Promise<void> {
    if (this.started) return;
    this.started = true;
    this.error.set(null);

    const [profileRes, dashboardRes] = await Promise.allSettled([
      fetch(`${environment.apiBase}/student/profile`, { credentials: 'include' }),
      fetch(`${environment.apiBase}/student/dashboard`, { credentials: 'include' }),
    ]);

    if (profileRes.status === 'fulfilled' && profileRes.value.ok) {
      const body = (await profileRes.value.json()) as StudentIdentity;
      this._profile.set({
        usn: body.usn ?? null,
        full_name: body.full_name ?? null,
        phone: body.phone ?? null,
        email: body.email ?? null,
        mentor_name: body.mentor_name ?? null,
        placement_eligible: !!body.placement_eligible,
        interested_in_jobs: !!body.interested_in_jobs,
        interested_in_internships: !!body.interested_in_internships,
        institution: body.institution ?? null,
      });
      if (body.usn) this.usn.set(body.usn);
    } else if (profileRes.status === 'fulfilled' && profileRes.value.status === 404) {
      this.noProfileRow.set(true);
    } else {
      this.error.set('Could not load your student record.');
    }

    // Best-effort, and only for what the profile row could not supply.
    if (!this.usn() && dashboardRes.status === 'fulfilled' && dashboardRes.value.ok) {
      const d = (await dashboardRes.value.json()) as { usn?: string | null };
      if (d.usn) this.usn.set(d.usn);
    }
  }

  /**
   * Replace the cached profile after a successful write.
   *
   * The Placement Policy section PUTs the two interest flags; without this the
   * cache would hold the pre-write answer for the rest of the session and the
   * next section to read it would disagree with the server.
   */
  applyProfile(next: Partial<StudentIdentity>): void {
    const current = this._profile();
    if (!current) return;
    this._profile.set({ ...current, ...next });
  }
}
