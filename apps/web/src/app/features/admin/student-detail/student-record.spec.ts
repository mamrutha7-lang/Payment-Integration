import {
  type AccessHolderOut,
  type Document360Out,
  type MentorSpell,
  barsOf,
  cvsOf,
  distinctHolders,
  initialsOf,
  mainCvOf,
  mentorSpellRowsOf,
  moveLabelOf,
  pointsOf,
  topPercentOf,
} from './student-record';

function upload(id: string, kind: string, fromApplication = false): Document360Out {
  return {
    id,
    kind,
    title: fromApplication ? 'CV from application' : 'A file',
    original_name: `${id}.pdf`,
    mime_type: 'application/pdf',
    size_bytes: 100,
    status: 'VERIFIED',
    review_note: null,
    reviewed_at: null,
    uploaded_at: '2026-10-01T00:00:00Z',
    from_application: fromApplication,
  };
}

/**
 * The owner's rule for the Student 360 page (2026-10-15): the resume the
 * office reads is the CV the student uploaded when they registered.
 */
describe('which CV the record shows', () => {
  it('is the one from the application, even when a newer one exists', () => {
    const documents = [upload('newer', 'RESUME'), upload('photo', 'PROFILE_PHOTO'), upload('applied', 'RESUME', true)];
    expect(mainCvOf(documents)?.id).toBe('applied');
  });

  it('falls back to the newest CV for a student who applied before CVs were required', () => {
    expect(mainCvOf([upload('newest', 'RESUME'), upload('older', 'RESUME')])?.id).toBe('newest');
  });

  it('is nothing when there is no CV at all, rather than another kind of file', () => {
    expect(mainCvOf([upload('certificate', 'CERTIFICATE_PROOF')])).toBeNull();
  });

  it('lists later CVs above the application one', () => {
    const documents = [upload('applied', 'RESUME', true), upload('later', 'RESUME')];
    expect(cvsOf(documents).map((d) => d.id)).toEqual(['later', 'applied']);
  });
});

describe('the "Top %" beside a rank', () => {
  it('rounds up, so the top of the board is never "Top 0%"', () => {
    expect(topPercentOf(4, 52)).toBe(8);
    expect(topPercentOf(1, 300)).toBe(1);
    expect(topPercentOf(52, 52)).toBe(100);
  });
});

describe('the charts are drawn to scale', () => {
  it('gives a week with no sessions no bar, never a zero', () => {
    const bars = barsOf([80, null, 100], ['a', 'b', 'c'], 300, 100, 75);
    expect(bars[1]).toBeNull();
    expect(bars[0]?.height).toBe(80);
    expect(bars[2]?.y).toBe(0);
    expect(bars[0]?.below).toBe(false);
  });

  it('marks a week under the required line', () => {
    expect(barsOf([60], ['a'], 100, 100, 75)[0]?.below).toBe(true);
  });

  it('places scores on the 0 to 100 axis', () => {
    const points = pointsOf([0, 50, 100], 200, 100, 0, 100);
    expect(points.map((p) => [p.x, p.y])).toEqual([
      [0, 100],
      [100, 50],
      [200, 0],
    ]);
  });
});

describe('initials on an avatar', () => {
  it('skip a word that does not start with a letter', () => {
    expect(initialsOf('Main Admin (seed)')).toBe('MA');
    expect(initialsOf('Priya Shetty')).toBe('PS');
    expect(initialsOf('')).toBe('?');
  });
});

describe('who can open the record', () => {
  function holder(holderId: string, kind: string, grantId: string | null = null): AccessHolderOut {
    return {
      kind,
      name: 'Kiran Hegde',
      detail: '',
      reach_label: null,
      until: null,
      reason: null,
      granted_by: null,
      granted_at: null,
      grant_id: grantId,
      holder_id: holderId,
    };
  }

  it('counts a faculty member with two grants once', () => {
    const people = distinctHolders([holder('a', 'admin'), holder('k', 'grant', 'g1'), holder('k', 'grant', 'g2')]);
    expect(people.map((p) => p.holder_id)).toEqual(['a', 'k']);
  });

  it('keeps two different people who share a name', () => {
    expect(distinctHolders([holder('k1', 'grant', 'g1'), holder('k2', 'grant', 'g2')])).toHaveLength(2);
  });
});

describe('the mentor history on the Faculty mentor card', () => {
  function spell(overrides: Partial<MentorSpell>): MentorSpell {
    return {
      id: 's',
      mentor_id: 'm',
      mentor_name: 'Asha Rao',
      from_at: '2026-06-01T00:00:00Z',
      to_at: null,
      kind: 'assign',
      by_name: 'Main Admin',
      reason: 'Finance cohort',
      end_kind: null,
      ended_by_name: null,
      end_reason: null,
      ...overrides,
    };
  }

  it('says who made each change and why, newest first as sent', () => {
    const rows = mentorSpellRowsOf({
      available: true,
      note: '',
      entries: [
        spell({ id: 'now', mentor_name: 'Ravi Shetty', kind: 'reassign', reason: 'Asha on leave' }),
        spell({
          id: 'before',
          to_at: '2026-09-01T00:00:00Z',
          end_kind: 'reassign',
          ended_by_name: 'Main Admin',
          end_reason: 'Asha on leave',
        }),
      ],
    });
    expect(rows.map((row) => [row.who, row.current])).toEqual([
      ['Ravi Shetty', true],
      ['Asha Rao', false],
    ]);
    expect(rows[0].opened).toBe('Moved here by Main Admin — Asha on leave');
    expect(rows[1].closed).toBe('Moved on by Main Admin — Asha on leave');
  });

  it('does not invent a start date or an actor nobody recorded', () => {
    const [row] = mentorSpellRowsOf({
      available: true,
      note: '',
      entries: [spell({ from_at: null, by_name: null, reason: null, mentor_name: null })],
    });
    expect(row.when).toBe('Since before this was recorded');
    expect(row.opened).toBe('');
    expect(row.who).toBe('A faculty account that is no longer on the roster');
  });

  it('draws nothing for a student with no history', () => {
    expect(mentorSpellRowsOf(undefined)).toEqual([]);
  });
});

describe('the promotions and graduations table', () => {
  it('names each kind of move in words, and prints an unknown one as stored', () => {
    expect(['promote', 'graduate', 'ungraduate', 'hold_back', 'transfer'].map(moveLabelOf)).toEqual([
      'Promoted',
      'Graduated',
      'Graduation reversed',
      'Held back',
      'transfer',
    ]);
  });
});
