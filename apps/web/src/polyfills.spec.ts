import { arrayAt, define, hasOwn, relativeIndex, replaceAll, stringAt } from './polyfills';

/**
 * These call the implementations with an explicit receiver and touch no global.
 *
 * The first version of this file deleted the natives and re-imported the module
 * so the guards would fall through — and it took the test runner down with it,
 * eleven unrelated spec files failing on `this.executionStack.at is not a
 * function`, because vitest shares the realm with the code under test. The
 * lesson is in polyfills.ts's header: a guarded polyfill is tested through its
 * exports, never by removing the thing it stands in for.
 *
 * What is being pinned is not "a polyfill exists" but the two properties that
 * decide whether a polyfilled browser behaves like a current one or merely
 * stops crashing: the values match the spec, and the install is
 * non-enumerable.
 */

describe('define', () => {
  /**
   * The single choke point all four installs go through, so this one case
   * pins the property flags for every one of them. A bare
   * `Array.prototype.at = fn` would be enumerable, and would then appear in
   * every `for...in` over an array in every dependency in the app.
   */
  it('installs non-enumerably, writably and configurably', () => {
    const target: Record<string, unknown> = {};
    define(target, 'thing', 1);

    const d = Object.getOwnPropertyDescriptor(target, 'thing');
    expect(d).toEqual({ value: 1, enumerable: false, writable: true, configurable: true });
    expect(Object.keys(target)).toEqual([]);

    const seen: string[] = [];
    for (const k in target) seen.push(k);
    expect(seen).toEqual([]);
  });

  it('puts a prototype method out of reach of for...in over an instance', () => {
    // The actual failure mode, rehearsed on a throwaway prototype rather than
    // on Array.prototype.
    class Box {}
    define(Box.prototype, 'at', arrayAt);

    const seen: string[] = [];
    const box = new Box() as Record<string, unknown>;
    box['own'] = 1;
    for (const k in box) seen.push(k);
    expect(seen).toEqual(['own']);
  });
});

describe('hasOwn', () => {
  it('answers for an own property and refuses an inherited one', () => {
    // Exactly Angular's use: `ld()` asks whether a view carries its own
    // __ngSimpleChanges__, and an inherited hit there would be wrong.
    const own = Object.create({ inherited: 1 }) as Record<string, unknown>;
    own['mine'] = 2;

    expect(hasOwn(own, 'mine')).toBe(true);
    expect(hasOwn(own, 'inherited')).toBe(false);
    expect(hasOwn(own, 'absent')).toBe(false);
  });

  it('sees a key whose value is undefined, which a truthiness check does not', () => {
    expect(hasOwn({ k: undefined }, 'k')).toBe(true);
  });

  it('works on an object with a null prototype', () => {
    // The case `obj.hasOwnProperty(k)` throws on, and the reason hasOwn exists.
    const bare = Object.create(null) as Record<string, unknown>;
    bare['k'] = 1;
    expect(hasOwn(bare, 'k')).toBe(true);
  });

  it('is not fooled by an object that has shadowed hasOwnProperty', () => {
    const liar = { hasOwnProperty: () => true } as unknown as Record<string, unknown>;
    expect(hasOwn(liar, 'absent')).toBe(false);
  });

  it('reads an index and a symbol key, not only a string', () => {
    const sym = Symbol('s');
    expect(hasOwn(['a'], 0)).toBe(true);
    expect(hasOwn(['a'], 1)).toBe(false);
    expect(hasOwn({ [sym]: 1 }, sym)).toBe(true);
  });

  it('throws on null and undefined', () => {
    expect(() => hasOwn(null as unknown as object, 'k')).toThrow(TypeError);
    expect(() => hasOwn(undefined as unknown as object, 'k')).toThrow(TypeError);
  });

  it('agrees with the native on every case above', () => {
    // The runtime running this suite HAS Object.hasOwn, so it is the oracle.
    const own = Object.create({ inherited: 1 }) as Record<string, unknown>;
    own['mine'] = 2;
    for (const key of ['mine', 'inherited', 'absent']) {
      expect(hasOwn(own, key)).toBe(Object.hasOwn(own, key));
    }
  });
});

describe('relativeIndex', () => {
  it('counts from the front, and from the back for a negative index', () => {
    expect(relativeIndex(3, 0)).toBe(0);
    expect(relativeIndex(3, 2)).toBe(2);
    expect(relativeIndex(3, -1)).toBe(2);
    expect(relativeIndex(3, -3)).toBe(0);
  });

  it('answers undefined out of range rather than clamping', () => {
    expect(relativeIndex(1, 1)).toBeUndefined();
    expect(relativeIndex(1, -2)).toBeUndefined();
    expect(relativeIndex(0, 0)).toBeUndefined();
  });

  it('truncates toward zero and reads NaN as 0, as ToIntegerOrInfinity does', () => {
    expect(relativeIndex(3, 1.7)).toBe(1);
    expect(relativeIndex(3, -1.7)).toBe(2);
    expect(relativeIndex(3, -0)).toBe(0);
    expect(relativeIndex(3, NaN)).toBe(0);
    expect(relativeIndex(3, undefined as unknown as number)).toBe(0);
  });
});

describe('arrayAt', () => {
  const at = (xs: unknown[], i: number) => arrayAt.call(xs, i);

  it('indexes from the front and from the back', () => {
    expect(at(['a', 'b', 'c'], 0)).toBe('a');
    expect(at(['a', 'b', 'c'], -1)).toBe('c');
    expect(at(['a', 'b', 'c'], -3)).toBe('a');
  });

  it('returns undefined out of range rather than throwing', () => {
    expect(at(['a'], 1)).toBeUndefined();
    expect(at(['a'], -2)).toBeUndefined();
    expect(at([], 0)).toBeUndefined();
  });

  it('agrees with the native across a sweep of indices', () => {
    const xs = ['a', 'b', 'c'];
    for (const i of [-5, -3, -1, -0, 0, 1, 2, 3, 1.7, NaN]) {
      expect(at(xs, i)).toBe(xs.at(i));
    }
  });

  it('works on an array-like receiver', () => {
    expect(arrayAt.call({ length: 2, 0: 'x', 1: 'y' }, -1)).toBe('y');
  });
});

describe('stringAt', () => {
  const at = (s: string, i: number) => stringAt.call(s, i);

  it('indexes from both ends and returns undefined out of range', () => {
    expect(at('abc', 0)).toBe('a');
    expect(at('abc', -1)).toBe('c');
    expect(at('abc', 3)).toBeUndefined();
    expect(at('', 0)).toBeUndefined();
  });

  it('returns a code unit, matching the native on a surrogate pair', () => {
    // '😀' is two code units. The native returns the lone high surrogate, so
    // charAt is correct here and spreading to an array of characters is not.
    const emoji = '😀';
    expect(at(emoji, 0)).toBe(emoji.at(0));
    expect(at(emoji, 1)).toBe(emoji.at(1));
    expect(at(emoji, -1)).toBe(emoji.at(-1));
  });

  it('agrees with the native across a sweep of indices', () => {
    for (const i of [-4, -1, 0, 1, 2, 3, 1.7, NaN]) {
      expect(at('abc', i)).toBe('abc'.at(i));
    }
  });
});

describe('replaceAll', () => {
  const all = (s: string, p: string | RegExp, r: string) => replaceAll.call(s, p, r);

  it('replaces every occurrence, not just the first', () => {
    expect(all('a-b-c', '-', '/')).toBe('a/b/c');
    expect(all('aaa', 'a', 'b')).toBe('bbb');
    expect(all('abc', 'z', 'y')).toBe('abc');
  });

  it('applies $-substitution patterns, as the spec requires', () => {
    // THE CASE THAT CAUGHT THE FIRST IMPLEMENTATION. replaceAll runs
    // GetSubstitution for a string replacement exactly as replace does, so `$&`
    // is the match and not two literal characters. A split/join spelling reads
    // naturally and answers 'a$&b' here, which is wrong on every real engine.
    expect(all('a-b', '-', '$&')).toBe('a-b');
    expect(all('a-b', '-', '$`')).toBe('aab');
    expect(all('a-b', '-', "$'")).toBe('abb');
    expect(all('a-b', '-', '$$')).toBe('a$b');
    // No capture group, so `$1` has nothing to name and stays literal.
    expect(all('a-b', '-', '$1')).toBe('a$1b');
  });

  it('keeps a backslash in the replacement literal', () => {
    expect(all('a-b', '-', '\\')).toBe('a\\b');
  });

  it('does not treat the pattern as a regular expression', () => {
    expect(all('a.b.c', '.', '-')).toBe('a-b-c');
    expect(all('a+b', '+', '-')).toBe('a-b');
  });

  it('passes match, offset and string to a function replacement', () => {
    const seen: Array<[string, number, string]> = [];
    const out = replaceAll.call('a-b-c', '-', (match, offset, whole) => {
      seen.push([match, offset as number, whole as string]);
      return String(offset);
    });
    expect(out).toBe('a1b3c');
    expect(seen).toEqual([
      ['-', 1, 'a-b-c'],
      ['-', 3, 'a-b-c'],
    ]);
  });

  it('accepts a global RegExp and refuses a non-global one', () => {
    expect(all('a1b2', /\d/g, '#')).toBe('a#b#');
    // Per spec. Replacing only the first match would be the silent wrong answer.
    expect(() => all('a1b2', /\d/, '#')).toThrow(TypeError);
  });

  it('agrees with the native on the cases above', () => {
    const cases: Array<[string, string, string]> = [
      ['a-b-c', '-', '/'],
      ['aaa', 'a', 'b'],
      ['abc', 'z', 'y'],
      ['a-b', '-', '$&'],
      ['a-b', '-', '$`'],
      ['a-b', '-', '$$'],
      ['a-b', '-', '$1'],
      ['a.b.c', '.', '-'],
      ['a+b', '+', '-'],
      ['a$b', '$', '-'],
      ['a\\b', '\\', '-'],
      ['a[b]c', '[b]', '-'],
      // The empty needle: 'abc' becomes 'xaxbxcx' natively, which is worth
      // inheriting from the engine rather than reimplementing.
      ['abc', '', 'x'],
      ['', '', 'x'],
    ];
    for (const [s, p, r] of cases) {
      expect(replaceAll.call(s, p, r)).toBe(s.replaceAll(p, r));
    }
  });
});
