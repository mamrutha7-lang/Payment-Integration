/**
 * Student RECORDS — the "records view" the REEP Agent points at.
 *
 * One read-only screen that aggregates a student's academic records from three
 * independent endpoints, each of which loads, empties and fails on its own so a
 * single unreachable section never blanks the page:
 *   - SEMESTER RESULTS — GET /student/results  (VTU marks, CGPA/SGPA, subjects)
 *   - ATTENDANCE       — GET /student/attendance (overall % + per-course)
 *   - ACADEMIC HISTORY — GET /student/academics  (qualifications + declared gap)
 *
 * This is the merged Records screen of the v2 handoff: the separate Academics
 * screen (and its qualification/gap entry form) is retired, so academic history
 * is READ-ONLY here — the handoff draws no add form, and a student reads what
 * the office has on file beside their VTU marks and attendance.
 *
 * Interfaces are snake_case, verbatim from student.py's *Out models — no client
 * remapping. STATUS is always text + colour together (the .chip tones), never
 * colour alone. Visual language is the global reep-v2 classes; the scss only adds
 * the attendance meter and a little record-card layout.
 */

import { Component, computed, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { environment } from '../../../../environments/environment';

type LoadState = 'loading' | 'ready' | 'error';
type Tone = 'good' | 'warn' | 'risk' | 'neutral';

/** GET /student/results — one row per semester (SemesterResultOut). */
interface SubjectMark {
  subject_code: string;
  subject_name: string;
  credits: number;
  internal: number;
  external: number;
  total: number;
  passed: boolean;
}
interface SemesterResult {
  semester: number;
  sgpa: number | null;
  cgpa: number | null;
  closed_backlogs: number;
  live_backlogs: number;
  result_class: string | null;
  subjects: SubjectMark[];
}

/** GET /student/attendance (AttendanceSummaryOut). */
interface CourseAttendance {
  course_code: string;
  present: number;
  total: number;
  percent: number;
}
interface AttendanceSummary {
  overall_percent: number;
  present: number;
  total: number;
  by_course: CourseAttendance[];
}

/** GET /student/academics (AcademicsOut). */
interface Qualification {
  level: string;
  institution: string;
  board: string | null;
  year: number;
  marks: number;
  max_marks: number;
  percent: number;
  medium: string | null;
  location: string | null;
  subjects: string | null;
}
interface AcademicGap {
  twelfth_to_grad_mo: number;
  diploma_to_grad_mo: number;
  grad_to_pg_mo: number;
  other_mo: number;
  total_mo: number;
}
interface Academics {
  qualifications: Qualification[];
  gap: AcademicGap;
}

interface Chip {
  cls: Tone;
  icon: string;
  label: string;
}

const LEVEL_LABEL: Record<string, string> = {
  TENTH: '10th Standard',
  TWELFTH: '12th Standard',
  DIPLOMA: 'Diploma',
  UNDERGRAD: 'Undergraduate',
  POSTGRAD: 'Postgraduate',
};

const GAP_LABEL: { key: keyof AcademicGap; label: string }[] = [
  { key: 'twelfth_to_grad_mo', label: '12th → Graduation' },
  { key: 'diploma_to_grad_mo', label: 'Diploma → Graduation' },
  { key: 'grad_to_pg_mo', label: 'Graduation → PG' },
  { key: 'other_mo', label: 'Other' },
];

@Component({
  selector: 'app-student-records',
  standalone: true,
  // RouterLink: the "Subject-by-subject progress" link to /student/courses.
  imports: [RouterLink],
  templateUrl: './records.component.html',
  styleUrl: './records.component.scss',
})
export class RecordsComponent {
  readonly levelLabel = LEVEL_LABEL;
  readonly gapRows = GAP_LABEL;

  // --- semester results ---
  readonly results = signal<SemesterResult[]>([]);
  readonly resultsState = signal<LoadState>('loading');

  // --- attendance ---
  readonly attendance = signal<AttendanceSummary | null>(null);
  readonly attendanceState = signal<LoadState>('loading');

  // --- academics ---
  readonly academics = signal<Academics | null>(null);
  readonly academicsState = signal<LoadState>('loading');

  /** Latest CGPA (highest semester with a value) — the headline figure. */
  readonly latestCgpa = computed<number | null>(() => {
    const rows = this.results();
    for (let i = rows.length - 1; i >= 0; i--) {
      if (rows[i].cgpa !== null) return rows[i].cgpa;
    }
    return null;
  });

  readonly liveBacklogs = computed(() =>
    this.results().reduce((sum, r) => sum + r.live_backlogs, 0),
  );

  readonly hasGap = computed(() => (this.academics()?.gap.total_mo ?? 0) > 0);

  constructor() {
    void this.loadResults();
    void this.loadAttendance();
    void this.loadAcademics();
  }

  private async loadResults(): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}/student/results`, { credentials: 'include' });
      if (!res.ok) {
        this.resultsState.set('error');
        return;
      }
      this.results.set((await res.json()) as SemesterResult[]);
      this.resultsState.set('ready');
    } catch {
      this.resultsState.set('error');
    }
  }

  private async loadAttendance(): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}/student/attendance`, {
        credentials: 'include',
      });
      if (!res.ok) {
        this.attendanceState.set('error');
        return;
      }
      this.attendance.set((await res.json()) as AttendanceSummary);
      this.attendanceState.set('ready');
    } catch {
      this.attendanceState.set('error');
    }
  }

  private async loadAcademics(): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}/student/academics`, {
        credentials: 'include',
      });
      if (!res.ok) {
        this.academicsState.set('error');
        return;
      }
      this.academics.set((await res.json()) as Academics);
      this.academicsState.set('ready');
    } catch {
      this.academicsState.set('error');
    }
  }

  levelName(level: string): string {
    return this.levelLabel[level] ?? level;
  }

  /** Attendance tone: good ≥85, warn 75–85, risk <75 — text + colour together. */
  attendanceChip(percent: number): Chip {
    if (percent >= 85)
      return { cls: 'good', icon: 'check_circle', label: `${percent}% · On track` };
    if (percent >= 75) return { cls: 'warn', icon: 'error', label: `${percent}% · Watch` };
    return { cls: 'risk', icon: 'warning', label: `${percent}% · Below 75%` };
  }

  /** Bare tone for the meter fill, sharing the same thresholds as the chip. */
  attendanceTone(percent: number): Tone {
    if (percent >= 85) return 'good';
    if (percent >= 75) return 'warn';
    return 'risk';
  }

  /** A single subject pass/fail chip. */
  subjectChip(passed: boolean): Chip {
    return passed
      ? { cls: 'good', icon: 'check', label: 'Pass' }
      : { cls: 'risk', icon: 'close', label: 'Fail' };
  }

  backlogChip(live: number): Chip {
    return live > 0
      ? { cls: 'risk', icon: 'warning', label: `${live} live backlog${live === 1 ? '' : 's'}` }
      : { cls: 'good', icon: 'check_circle', label: 'No live backlogs' };
  }
}
