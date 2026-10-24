/**
 * THE BLANK PAGE ON AN OLD PHONE BROWSER (2026-09-22).
 *
 * A student in Bengaluru opened `/` on VivoBrowser 12.9.0 and got white. Not a
 * slow load, not a broken screen — nothing painted at all, twice in the same
 * session half a second apart (Sentry REEP-WEB-2 and REEP-WEB-3, one trace_id).
 * Both throws are `TypeError: Object.hasOwn is not a function`, and both are in
 * ANGULAR'S OWN CODE: `parseQueryParam` in the router and `ld()` — the
 * `__ngSimpleChanges__` reader — in core, during `bootstrapApplication`. The app
 * dies before its first frame, so there is no error screen to draw it on and
 * nothing the student can do. They cannot even reach the login form to be told
 * why.
 *
 * `Object.hasOwn` is ES2022 and landed in Chromium 93 (2021-08). That browser is
 * a Chromium fork below it.
 *
 * WHAT IT IS NOT IS A SYNTAX PROBLEM, AND THAT IS WHY THERE IS NO
 * `.browserslistrc` BESIDE THIS FILE. The bundle parsed and ran far enough to
 * throw a *runtime* TypeError, so that engine already handles every form
 * esbuild emits at the target Angular picks by default — only the library
 * surface is short, and no compiler target can fill that, because
 * `Object.hasOwn` is a property lookup at runtime and not a form the parser
 * sees. A floor was written first (Chrome 87, to downlevel syntax as well) and
 * removed again: it cost every student ~4 kB of lowering for a gap nothing
 * demonstrated, and `ng build` answered it with "browsers configured in the
 * project's Browserslist fall outside Angular's browser support for this
 * version" on every single run. A warning printed on every build is one nobody
 * reads by the second week, and a config file claiming Chrome 87 is supported
 * is a promise this app cannot keep — Angular 22's own floor is far above it.
 * The honest position is the one here: the framework's baseline stands, and
 * this file is a best-effort bridge for the handset forks below it, covering
 * the gap actually observed.
 *
 * WHAT IS HERE AND WHY IT IS NOT MORE. Each entry was chosen by grepping the
 * BUILT bundle rather than by copying a polyfill preset, because a preset costs
 * the other 99% of students bytes for methods nothing calls. `Object.hasOwn`
 * appears 7 times, `.at(` 10 and `replaceAll` 5; `structuredClone`,
 * `findLast`, `toSorted` and `toReversed` appear zero times and are therefore
 * deliberately absent. The three that are here sit inside one narrow version
 * band — `String.replaceAll` is Chromium 85, `Array/String.prototype.at` is 92,
 * `Object.hasOwn` is 93 — so a browser missing the last is a coin toss on the
 * other two, and finding out from a second blank-page report is the expensive
 * way. Re-run that grep over `dist/web/browser/*.js` before adding a fourth.
 *
 * Every definition is guarded and installed through `define` below, which is
 * the single place the property flags are chosen. A guard means a current
 * browser reads this file, changes nothing and moves on; NON-ENUMERABILITY
 * matters because a bare assignment to `Array.prototype` shows up in every
 * `for...in` over an array in every dependency, which is a far worse bug than
 * the one being fixed — and routing all four installs through one helper is
 * what lets a single test pin that flag for all of them.
 *
 * THE IMPLEMENTATIONS ARE EXPORTED, AND THAT IS FOR THE TEST RATHER THAN FOR
 * ANY CALLER. Nothing imports this module; the Angular build lists it under
 * `polyfills` in angular.json and it runs for its side effects. But a guarded
 * polyfill is unreachable from a test on a modern runtime — the guard is
 * false, nothing installs, and a spec that merely imports the file asserts
 * nothing. The first version of the spec solved that by DELETING the natives
 * and re-importing, which took vitest down with it ("this.executionStack.at is
 * not a function"): the runner shares the realm, so removing a built-in
 * mid-suite breaks the harness, not just the code under test. Exporting the
 * functions and calling them with an explicit receiver tests the same code
 * with nothing global touched.
 */

/**
 * Install `value` at `target[key]` the way the engine would have: writable and
 * configurable like every built-in method, and NOT enumerable.
 */
export function define(target: object, key: string, value: unknown): void {
  Object.defineProperty(target, key, {
    value,
    configurable: true,
    writable: true,
    enumerable: false,
  });
}

/** Object.hasOwn — Chromium 93. The one actually seen failing, in two places. */
export function hasOwn(target: object, key: PropertyKey): boolean {
  if (target === null || target === undefined) {
    throw new TypeError('Cannot convert undefined or null to object');
  }
  // Called off Object.prototype rather than as target.hasOwnProperty(key),
  // which is the whole reason hasOwn exists: a null-prototype object and an
  // object that has shadowed the name both throw the other way round.
  return Object.prototype.hasOwnProperty.call(Object(target), key);
}

if (!Object.hasOwn) define(Object, 'hasOwn', hasOwn);

/**
 * Array.prototype.at / String.prototype.at — Chromium 92.
 *
 * The spec's own relative-index algorithm: truncate toward zero, add the length
 * for a negative index, and return undefined rather than throwing when the
 * result falls outside. `at(-0)` is index 0, which `Math.trunc` gives for free.
 */
export function relativeIndex(length: number, index: number): number | undefined {
  // `|| 0` is what turns NaN into 0, which is what the spec's ToIntegerOrInfinity
  // does and what makes `at(NaN)` the first element rather than undefined.
  const n = Math.trunc(Number(index)) || 0;
  const i = n < 0 ? length + n : n;
  return i < 0 || i >= length ? undefined : i;
}

export function arrayAt(this: ArrayLike<unknown>, index: number): unknown {
  const self = Object(this) as ArrayLike<unknown>;
  const i = relativeIndex(self.length >>> 0, index);
  return i === undefined ? undefined : self[i];
}

export function stringAt(this: string, index: number): string | undefined {
  const self = String(this);
  const i = relativeIndex(self.length, index);
  // Deliberately charAt and not [...self][i]: this returns a CODE UNIT,
  // matching the spec, so a surrogate pair reads the same as it does on a
  // browser that ships the method natively.
  return i === undefined ? undefined : self.charAt(i);
}

if (!Array.prototype.at) define(Array.prototype, 'at', arrayAt);
if (!String.prototype.at) define(String.prototype, 'at', stringAt);

/**
 * String.prototype.replaceAll — Chromium 85.
 *
 * The string-pattern case is a split/join, which needs no escaping and cannot
 * misread a pattern containing `$&` or a backslash. A RegExp pattern is handed
 * straight to `replace`, which already does the work — but a non-global RegExp
 * is a TypeError per spec, and silently replacing only the first match is
 * exactly the sort of difference that makes a polyfilled browser behave
 * subtly, rather than visibly, unlike a current one.
 */
type Replacer = string | ((substring: string, ...args: unknown[]) => string);

/**
 * Characters that carry meaning in a RegExp source. Escaped so a string pattern
 * stays a literal — `replaceAll('.', '-')` must not match every character.
 */
const REGEXP_SPECIALS = /[.*+?^${}()|[\]\\]/g;

export function replaceAll(this: string, pattern: string | RegExp, replacement: Replacer): string {
  const self = String(this);

  // A non-global RegExp is a TypeError per spec. Replacing only the first match
  // instead is exactly the sort of difference that makes a polyfilled browser
  // behave subtly, rather than visibly, unlike a current one.
  if (pattern instanceof RegExp) {
    if (!pattern.global) {
      throw new TypeError('replaceAll must be called with a global RegExp');
    }
    return typeof replacement === 'function'
      ? self.replace(pattern, replacement)
      : self.replace(pattern, replacement);
  }

  // A STRING PATTERN IS ESCAPED INTO A GLOBAL RegExp AND HANDED TO `replace`,
  // WHICH IS NOT THE OBVIOUS SPELLING AND IS THE CORRECT ONE.
  //
  // The obvious spelling is `self.split(needle).join(replacement)`, and this
  // file shipped that first, on the reasoning that it needs no escaping and
  // cannot misread a `$&` in the replacement as a capture reference. That
  // reasoning is wrong about the spec: `String.prototype.replaceAll` runs
  // GetSubstitution for a string replacement exactly as `replace` does, so
  // `'a-b'.replaceAll('-', '$&')` is `'a-b'` on every real engine and split/join
  // answers `'a$&b'`. It was caught by the oracle case in the spec — the same
  // inputs run through the native method the test runtime has — and not by
  // reading, which is why that case is there.
  //
  // Delegating gets the substitution patterns, the (match, offset, string)
  // arguments of a function replacement, and the empty-needle behaviour
  // (`'abc'.replaceAll('', 'x')` is `'xaxbxcx'`) from the engine rather than
  // from a reimplementation of them here. The branches are separate only
  // because `replace` is overloaded on the replacer's type.
  const every = new RegExp(String(pattern).replace(REGEXP_SPECIALS, '\\$&'), 'g');
  return typeof replacement === 'function'
    ? self.replace(every, replacement)
    : self.replace(every, replacement);
}

if (!String.prototype.replaceAll) define(String.prototype, 'replaceAll', replaceAll);
