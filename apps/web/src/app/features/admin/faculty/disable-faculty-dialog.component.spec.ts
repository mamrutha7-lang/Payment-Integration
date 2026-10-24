import { TestBed } from '@angular/core/testing';

import { DisableFacultyDialogComponent } from './disable-faculty-dialog.component';
import type { FacultyRow } from './faculty-row';

/**
 * What the dialog says will happen to the faculty member's mentees is what
 * `disable_account` does: `release_mentees_of` puts every one of them back in
 * the unassigned pool (B9.1). The dialog used to say the opposite — "still
 * filed under them", "its mentor group is NOT released" — one click before the
 * screen reported them released.
 */
function facultyRow(menteeCount: number | null): FacultyRow {
  return {
    userId: 'u1',
    name: 'Asha Rao',
    email: 'asha.rao@bgscet.ac.in',
    initials: 'AR',
    designation: 'Assistant Professor',
    departmentLine: 'MBA',
    departmentId: 'd1',
    collegeId: 'c1',
    collegeName: 'BGSCET',
    isFiled: true,
    holdsMentorGroup: menteeCount === null ? null : menteeCount > 0,
    menteeCount,
    disabledAt: null,
    disableReason: null,
    isDisabled: false,
    isRemoved: false,
    deletedAt: null,
    deleteReason: null,
  };
}

function sentencesFor(menteeCount: number | null): string[] {
  const fixture = TestBed.createComponent(DisableFacultyDialogComponent);
  fixture.componentRef.setInput('faculty', facultyRow(menteeCount));
  return fixture.componentInstance.consequences().map((consequence) => consequence.sentence);
}

describe('the disable-faculty dialog · what happens to the mentees', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [DisableFacultyDialogComponent],
    }).compileComponents();
  });

  it('says one mentee is released and needs a new faculty member', () => {
    const sentences = sentencesFor(1);
    expect(sentences).toContain(
      'Their 1 mentee is released to the unassigned pool and needs a new faculty member.',
    );
  });

  it('agrees the verbs with a count above one', () => {
    expect(sentencesFor(14)).toContain(
      'Their 14 mentees are released to the unassigned pool and need a new faculty member.',
    );
  });

  it('never claims the mentees stay or the group is kept', () => {
    for (const count of [null, 0, 1, 14]) {
      const text = sentencesFor(count).join(' ');
      expect(text).not.toContain('still filed under them');
      expect(text).not.toContain('mentor group is NOT released');
    }
  });

  it('still says the granted functions are not revoked', () => {
    expect(sentencesFor(1)).toContain(
      'The functions granted to this account are NOT revoked — a disabled account cannot make a request, so nothing is reachable. Revoke what is no longer wanted in Governance.',
    );
  });

  it('says "their mentees" rather than a zero when the assignment list could not be read', () => {
    expect(sentencesFor(null)).toContain(
      'Their mentees are released to the unassigned pool and need a new faculty member.',
    );
  });

  it('says nothing about mentees for a faculty member who has none', () => {
    const sentences = sentencesFor(0);
    expect(sentences.some((sentence) => sentence.includes('mentee'))).toBe(false);
    expect(sentences).toContain(
      'Sign-in stops immediately — REEP password and Google both. Every device it holds is signed out.',
    );
  });
});
