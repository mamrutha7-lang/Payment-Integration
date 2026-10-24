/**
 * "1 student", not "1 students".
 *
 * The console counts things in almost every sub-line — students on a batch,
 * mentors with a group, days of leave, rows in view — and the 2026-09 screens
 * were written with the plural noun hard-coded beside the interpolation:
 *
 *     {{ figures.students }} students · {{ figures.mentors }} mentors
 *
 * which reads correctly on a populated deployment and reads as unfinished
 * software on every one-of-a-kind row. It is not a rare state either: a fresh
 * install has one college, one department, one batch and one mentor, and the
 * office meets "1 students · 1 mentors" on the first screen it ever opens.
 *
 *     {{ n | plural: 'student' }}          -> "1 student"   / "4 students"
 *     {{ n | plural: 'entry' : 'entries' }} -> "1 entry"     / "4 entries"
 *
 * Intl.PluralRules rather than `n === 1`, because the rule is a property of the
 * language and not of the number: the app is English today and the pipe does
 * not have to be rewritten when it is not. The second argument is for nouns
 * English does not form by adding an s, and for the handful of places where the
 * word that has to agree is a verb ('holds' / 'hold').
 *
 * The count is formatted too, so four figures get their separator.
 */

import { Pipe, PipeTransform } from '@angular/core';

const RULES = new Intl.PluralRules('en');

@Pipe({ name: 'plural', standalone: true })
export class PluralPipe implements PipeTransform {
  transform(count: number | null | undefined, one: string, many?: string): string {
    const n = count ?? 0;
    const word = RULES.select(n) === 'one' ? one : (many ?? `${one}s`);
    return `${n.toLocaleString('en')} ${word}`;
  }
}

/** The same rule for a component that is composing a string rather than a
 *  template — an aria-label, a confirmation sentence, a grid cell renderer. */
export function plural(count: number | null | undefined, one: string, many?: string): string {
  const n = count ?? 0;
  return `${n.toLocaleString('en')} ${RULES.select(n) === 'one' ? one : (many ?? `${one}s`)}`;
}
