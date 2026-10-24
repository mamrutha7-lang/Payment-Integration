import { describe, expect, it } from 'vitest';

import { emptyBoardSentence, scopeNoun, scopeSentence } from './leaderboard-scope';

describe('scopeSentence', () => {
  it('names the batch when the student is seated in one', () => {
    const s = scopeSentence('batch', 'General MBA - Finance · 2026-28');
    expect(s).toContain('within your batch');
    expect(s).toContain('General MBA - Finance · 2026-28');
  });

  it('says the student is unseated when the board is the department', () => {
    const s = scopeSentence('department', 'Management Studies');
    expect(s).toContain('not seated in a batch yet');
    expect(s).toContain('Management Studies');
    expect(s).toContain('placement office');
  });

  it('says there is nobody to rank against when neither is recorded', () => {
    const s = scopeSentence('none', null);
    expect(s).toContain('nobody to rank you against');
    expect(s).not.toContain('null');
  });

  it('survives a missing label without printing null', () => {
    expect(scopeSentence('batch', null)).not.toContain('null');
    expect(scopeSentence('department', null)).not.toContain('null');
  });
});

describe('scopeNoun', () => {
  it('is batch except for the department fallback', () => {
    expect(scopeNoun('batch')).toBe('batch');
    expect(scopeNoun('none')).toBe('batch');
    expect(scopeNoun('department')).toBe('department');
  });
});

describe('emptyBoardSentence', () => {
  it('tells an unseated student what happens next', () => {
    expect(emptyBoardSentence('none', 'Skills', 0)).toContain('seats you in a batch');
  });

  it('distinguishes an empty batch from a batch with no records yet', () => {
    expect(emptyBoardSentence('batch', 'Skills', 1)).toContain('only student');
    const many = emptyBoardSentence('batch', 'Skills', 30);
    expect(many).toContain('Nobody in your batch has a record on the Skills board yet');
    expect(many).toContain('30 of you');
  });

  it('says department when that is the scope', () => {
    expect(emptyBoardSentence('department', 'Mocks taken', 4)).toContain('your department');
  });
});
