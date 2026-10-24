import { MAX_SPECIALIZATIONS_FALLBACK, specializationLabel, togglePick } from './specializations';

/**
 * The checklist's two decisions: what a tick does to the list, and how the
 * picks are written as one sentence. The cap is the server's number; these
 * only prove that the client never exceeds one it was given.
 */
describe('togglePick', () => {
  it('adds a tick at the end and removes an untick, without mutating', () => {
    const picks = ['fin'];
    const two = togglePick(picks, 'mkt', true, 2);
    expect(two).toEqual(['fin', 'mkt']);
    expect(picks).toEqual(['fin']);
    expect(togglePick(two, 'fin', false, 2)).toEqual(['mkt']);
  });

  it('is idempotent for a box already in the state it is set to', () => {
    expect(togglePick(['fin'], 'fin', true, 2)).toEqual(['fin']);
    expect(togglePick(['fin'], 'mkt', false, 2)).toEqual(['fin']);
  });

  it('ignores a tick past the cap rather than evicting an earlier pick', () => {
    // The third box is drawn disabled; if the event arrives anyway the first
    // choice must not silently disappear.
    expect(togglePick(['fin', 'mkt'], 'hr', true, 2)).toEqual(['fin', 'mkt']);
    expect(togglePick(['fin', 'mkt'], 'hr', true, 3)).toEqual(['fin', 'mkt', 'hr']);
  });

  it('falls back to a cap of two before the hierarchy has loaded', () => {
    expect(MAX_SPECIALIZATIONS_FALLBACK).toBe(2);
  });
});

describe('specializationLabel', () => {
  it('joins two picks with the word the server uses', () => {
    expect(specializationLabel('Finance', 'Marketing')).toBe('Finance and Marketing');
  });

  it('prints one pick alone and nothing for none', () => {
    expect(specializationLabel('Finance', null)).toBe('Finance');
    expect(specializationLabel(null, 'Marketing')).toBe('Marketing');
    expect(specializationLabel(null, null)).toBeNull();
    expect(specializationLabel(undefined, undefined)).toBeNull();
  });
});
