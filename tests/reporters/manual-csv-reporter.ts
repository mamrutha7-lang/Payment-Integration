/**
 * Writes `manual-test-results.csv`: a row for every manual test case in
 * test-management/cases/ (one markdown file per module, indexed by
 * test-management/manual-test-cases.md), carrying the result of the automated
 * test tagged with its ID.
 *
 * WHY THIS IS A FILE AND NOT A DEPENDENCY. The brief named the npm package
 * `playwright-csv-reporter`, and no package by that name exists on the public
 * registry (a 404 on 2026-09-22, with nothing similar under another name). Do
 * not "fix" this by installing whatever someone publishes under that name
 * later: a reporter runs in-process on every test run, and a name nobody owned
 * when this was written is exactly the name a typosquatter registers.
 *
 * WHAT IT ENFORCES, and why a reporter is the place. The link between the
 * suites is a `@TC-NNN` tag in a test's title (see "How the manual and automated
 * suites stay linked" in test-management/manual-test-cases.md), and a convention nobody checks
 * lasts until the first rename. So the run FAILS, through `onEnd`'s status
 * override, when:
 *   - a test carries no `@TC-NNN` tag, or names a case the manual file lacks;
 *   - a test's top-level `test.step()` titles differ from its case's numbered
 *     steps, or a step's number is not its position. That is what makes the
 *     "Failed Step" column name the step a tester would follow by hand;
 *   - a case's "Automated test" field says it is not automated while a test
 *     automates it, or names a tag that no test in an UNFILTERED run carries;
 *   - the manual file repeats an ID.
 * Each problem is printed and also written into the case's "Sync Problems"
 * cell, so a drifted case never reads as a clean "Passed".
 *
 * STATUS, per row: Passed, Failed, Timed out, Interrupted, Flaky; Blocked (a
 * pre-condition did not hold, see the spec's `beforeEach`); Known failure (a
 * `test.fail()` test that failed as it was expected to); Skipped (`test.skip`
 * or `test.fixme`); Did not run (Playwright never started it, because a hook,
 * a serial sibling or the worker failed first); Not run (no test for the case
 * was in this run, e.g. `--grep` left it out); Not automated (the case's field
 * names no tag).
 */
import * as fs from 'node:fs';
import * as path from 'node:path';
import type {
  FullConfig,
  FullResult,
  Reporter,
  Suite,
  TestCase,
  TestResult,
} from '@playwright/test/reporter';

export interface ManualCsvReporterOptions {
  /** Where the CSV is written, relative to the Playwright config file. */
  outputFile?: string;
  /** The manual test cases, relative to the Playwright config file: one
   *  markdown file, or a directory whose `*.md` files are all read. */
  manualCases?: string;
  /** Set by Playwright itself, not by the config: `'list'` under `--list`. */
  _mode?: string;
  /** Set by Playwright itself: `''` when no `--grep`, `--grep-invert`,
   *  `--project`, file or line argument, `--only-changed`, `--test-list` or
   *  config tag narrowed the run. */
  _commandHash?: string;
}

export interface ManualCase {
  id: string;
  title: string;
  /** The `# Title` of the file the case is in: its module. */
  module: string;
  /** The file the case is in, as given to `parseManualCases`. */
  file: string;
  /** From the case's "Automated test" field: does it name a `@TC-NNN` tag? */
  automated: boolean;
  steps: string[];
  /** Expected results marked "**Manual only.**", as `ER-n: text`. */
  manualOnly: string[];
}

const COLUMNS = [
  'Test Case ID',
  'Module',
  'Manual Test Case',
  'Automated Test',
  'Project',
  'Status',
  'Sync Problems',
  'Duration (ms)',
  'Failed Step',
  'Error',
  'Manual-only Checks',
  'Screenshots',
  'Location',
  'Executed At',
] as const;

type Row = Partial<Record<(typeof COLUMNS)[number], string | number>>;

/** `## TC-001 — Title`. A dash or a colon may separate the two, or nothing. */
const CASE_HEADING = /^##\s+(TC-\d+)\b\s*(?:[—–:-]\s*)?(.*?)\s*$/;
const CASE_TAG = /^@(TC-\d+)$/;
/** A `test.step()` title's list number, e.g. `3. `. Like a markdown list item
 *  it needs whitespace after the dot, so `2.4 GHz ...` is not a number. */
const STEP_NUMBER = /^\s*(\d+)\.\s+/;
const MANUAL_ONLY_ROW = /^\|\s*(ER-\d+)\s*\|[^|]*\|\s*\*\*Manual only\.?\*\*\s*(.*?)\s*\|$/i;
const ANSI = /\u001b\[[0-9;]*m/g;

/**
 * Reads the cases out of one manual file: the `# Module` title, each case's
 * `## TC-NNN — Title` heading, the
 * "Automated test" row of its field table, the numbered list under its
 * "### Steps" heading and the "**Manual only.**" rows under "### Expected
 * results". Any other level-2 heading ends the case. A step is its list
 * item's first paragraph, lazy continuation lines included; nested lists,
 * later paragraphs and fenced code are not part of it.
 */
export function parseManualCases(markdown: string, file = ''): ManualCase[] {
  const cases: ManualCase[] = [];
  let module = '';
  let current: ManualCase | undefined;
  let section = '';
  let inStep = false;
  let fenced = false;
  for (const raw of markdown.split(/\r?\n/)) {
    const line = raw.trimEnd();
    if (/^\s{0,3}(```|~~~)/.test(line)) {
      fenced = !fenced;
      inStep = false;
      continue;
    }
    if (fenced) continue;
    const title = /^#\s+(.+)$/.exec(line);
    if (title) {
      module = title[1].trim();
      current = undefined;
      continue;
    }
    const heading = CASE_HEADING.exec(line);
    if (heading) {
      current = {
        id: heading[1],
        title: heading[2],
        module,
        file,
        automated: false,
        steps: [],
        manualOnly: [],
      };
      cases.push(current);
      section = '';
      inStep = false;
      continue;
    }
    if (/^##\s/.test(line)) {
      current = undefined;
      continue;
    }
    if (!current) continue;
    const subheading = /^###\s+(.+)$/.exec(line);
    if (subheading) {
      section = subheading[1].trim().toLowerCase();
      inStep = false;
      continue;
    }
    const field = /^\|\s*Automated test\s*\|\s*(.*?)\s*\|$/i.exec(line);
    if (field) {
      current.automated = /@TC-\d+/.test(field[1]);
      continue;
    }
    if (section === 'expected results') {
      const manualOnly = MANUAL_ONLY_ROW.exec(line);
      if (manualOnly) current.manualOnly.push(`${manualOnly[1]}: ${manualOnly[2]}`);
      continue;
    }
    if (section !== 'steps') continue;
    const item = /^\d+\.\s+(.*)$/.exec(line);
    if (item) {
      current.steps.push(item[1]);
      inStep = true;
    } else if (!line.trim()) {
      inStep = false;
    } else if (inStep && !/^\s*([-*+]|\d+[.)])\s/.test(line)) {
      current.steps[current.steps.length - 1] += ` ${line.trim()}`;
    } else {
      inStep = false;
    }
  }
  return cases;
}

/** A step as a sentence: no markdown emphasis, extra spaces or closing full stop. */
export function normalizeStep(text: string): string {
  return text.replace(/[`*_]/g, '').replace(/\s+/g, ' ').trim().replace(/[.:]$/, '');
}

/**
 * One CSV field, RFC 4180 quoted. A string starting with a formula character
 * is prefixed with an apostrophe, because Excel and Sheets would otherwise run
 * `=...`, `+...`, `-...` or `@...` found in an error message as a formula.
 */
export function csvCell(value: string | number | undefined): string {
  if (value === undefined) return '';
  let text = String(value);
  if (typeof value === 'string' && /^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function caseIdsOf(test: TestCase): string[] {
  const ids = test.tags.map((tag) => CASE_TAG.exec(tag)?.[1]).filter((id): id is string => !!id);
  return [...new Set(ids)];
}

function statusOf(test: TestCase, result: TestResult | undefined): string {
  if (!result) return 'Not run';
  if (result.status === 'interrupted') return 'Interrupted';
  if (result.status !== 'passed' && result.annotations.some((a) => a.type === 'blocked')) {
    return 'Blocked';
  }
  // test.fail() marks a known bug. Playwright calls its failure "expected",
  // but the case's expected results do not hold, so it must never read Passed.
  if (test.expectedStatus === 'failed') {
    return result.status === 'passed' ? 'Failed (marked test.fail, but passed)' : 'Known failure';
  }
  switch (test.outcome()) {
    case 'expected':
      return 'Passed';
    case 'flaky':
      return 'Flaky';
    case 'skipped':
      // 'skipped' covers both test.skip/test.fixme and a test Playwright never
      // started because a hook, a serial sibling or the worker failed first.
      return test.expectedStatus === 'skipped' ? 'Skipped' : 'Did not run';
    case 'unexpected':
      return result.status === 'timedOut' ? 'Timed out' : 'Failed';
  }
}

export default class ManualCsvReporter implements Reporter {
  private readonly outputFile: string;
  private readonly manualCases: string;
  /**
   * `playwright test --list` runs no test but still ends with `onEnd`. Writing
   * then would replace the last real run's results with a column of "Not run",
   * so a listing only checks the links. `_mode` is Playwright's internal
   * option. If it is ever renamed, listing writes those "Not run" rows again,
   * which is untidy but never claims a result nobody produced.
   */
  private readonly listOnly: boolean;
  /** Internal like `_mode`. If it is renamed, `unfiltered()` answers false and
   *  the "declared but missing" check goes quiet rather than failing good runs. */
  private readonly commandHash: string | undefined;
  private shard: FullConfig['shard'] = null;
  private configDir = process.cwd();
  private rootSuite: Suite | undefined;

  constructor(options: ManualCsvReporterOptions = {}) {
    this.outputFile = options.outputFile ?? 'manual-test-results.csv';
    this.manualCases = options.manualCases ?? 'test-management/cases';
    this.listOnly = options._mode === 'list';
    this.commandHash = options._commandHash;
  }

  printsToStdio(): boolean {
    return false;
  }

  onBegin(config: FullConfig, suite: Suite): void {
    if (config.configFile) this.configDir = path.dirname(config.configFile);
    this.shard = config.shard;
    this.rootSuite = suite;
  }

  async onEnd(result: FullResult): Promise<{ status: FullResult['status'] } | undefined> {
    const problems: string[] = [];
    const syncByCase = new Map<string, string[]>();
    const flag = (id: string, problem: string) => {
      problems.push(problem);
      syncByCase.set(id, [...(syncByCase.get(id) ?? []), problem]);
    };

    let cases: ManualCase[] = [];
    try {
      cases = this.readManualCases();
    } catch (error) {
      problems.push(
        `cannot read the manual test cases at ${this.manualCases}: ${(error as Error).message}`,
      );
    }
    const seen = new Map<string, ManualCase>();
    for (const c of cases) {
      const first = seen.get(c.id);
      if (first) flag(c.id, `${c.id} is a case in both ${first.file} and ${c.file}`);
      else seen.set(c.id, c);
    }
    const caseById = new Map(cases.map((c) => [c.id, c]));

    const testsByCase = new Map<string, TestCase[]>();
    const unlinked: Row[] = [];
    for (const test of this.rootSuite?.allTests() ?? []) {
      const ids = caseIdsOf(test);
      if (ids.length === 0) {
        const problem = `${this.where(test)} has no @TC-NNN tag in its title`;
        problems.push(problem);
        unlinked.push({ ...this.row(test, undefined), 'Sync Problems': problem });
        continue;
      }
      for (const id of ids) {
        const manual = caseById.get(id);
        if (!manual) {
          const problem = `${this.where(test)} is tagged @${id}, which is not a case in ${this.manualCases}`;
          problems.push(problem);
          unlinked.push({ ...this.row(test, id), 'Sync Problems': problem });
          continue;
        }
        if (!manual.automated) {
          flag(
            id,
            `${id}'s "Automated test" field says it is not automated, but ${this.where(test)} automates it`,
          );
        }
        if (ids.length === 1 && !this.listOnly) {
          for (const problem of this.stepDrift(test, manual)) flag(id, problem);
        }
        testsByCase.set(id, [...(testsByCase.get(id) ?? []), test]);
      }
    }
    if (this.unfiltered()) {
      for (const manual of cases) {
        if (manual.automated && !testsByCase.has(manual.id)) {
          flag(
            manual.id,
            `${manual.id}'s "Automated test" field names a tag, but no test carries @${manual.id}`,
          );
        }
      }
    }

    const rows: Row[] = [];
    for (const manual of cases) {
      const sync = (syncByCase.get(manual.id) ?? []).join('; ');
      const tests = testsByCase.get(manual.id) ?? [];
      if (tests.length === 0) {
        rows.push({
          'Test Case ID': manual.id,
          Module: manual.module,
          'Manual Test Case': manual.title,
          Status: manual.automated ? 'Not run' : 'Not automated',
          'Sync Problems': sync,
          'Manual-only Checks': manual.manualOnly.join('; '),
        });
        continue;
      }
      for (const test of tests) {
        rows.push({ ...this.row(test, manual.id, manual), 'Sync Problems': sync });
      }
    }
    rows.push(...unlinked);
    if (!this.listOnly) this.write(rows);

    if (problems.length === 0) return undefined;
    console.error(`\nThe automated tests and ${this.manualCases} are out of sync:`);
    for (const problem of problems) console.error(`  - ${problem}`);
    return result.status === 'passed' ? { status: 'failed' } : undefined;
  }

  /** Every case in `manualCases`: one file, or each `*.md` file of a directory
   *  in name order, so the numbered module files keep their order in the CSV. */
  private readManualCases(): ManualCase[] {
    const target = path.resolve(this.configDir, this.manualCases);
    const files = fs.statSync(target).isDirectory()
      ? fs
          .readdirSync(target)
          .filter((name) => name.endsWith('.md'))
          .sort()
          .map((name) => path.join(target, name))
      : [target];
    return files.flatMap((file) =>
      parseManualCases(
        fs.readFileSync(file, 'utf8'),
        path.relative(this.configDir, file).split(path.sep).join('/'),
      ),
    );
  }

  /** True only when nothing narrowed the run, so a tag missing from the suite
   *  means a missing test and not a filter. `--last-failed` is read from argv
   *  because Playwright applies it outside `_commandHash`. */
  private unfiltered(): boolean {
    return (
      !this.listOnly &&
      this.commandHash === '' &&
      !this.shard &&
      !process.argv.includes('--last-failed')
    );
  }

  private write(rows: Row[]): void {
    const outputPath = path.resolve(this.configDir, this.outputFile);
    const lines = [
      COLUMNS.map(csvCell).join(','),
      ...rows.map((r) => COLUMNS.map((c) => csvCell(r[c])).join(',')),
    ];
    fs.mkdirSync(path.dirname(outputPath), { recursive: true });
    // A byte-order mark, because Excel on Windows reads a CSV without one as
    // cp1252 and garbles the "×" and "…" that Playwright's call logs put in the
    // Error column. CRLF, because RFC 4180 says so.
    fs.writeFileSync(outputPath, `﻿${lines.join('\r\n')}\r\n`, 'utf8');

    const counts = new Map<string, number>();
    for (const r of rows) counts.set(String(r.Status), (counts.get(String(r.Status)) ?? 0) + 1);
    const summary = [...counts].map(([status, n]) => `${n} ${status}`).join(', ');
    console.log(
      `\n${path.relative(process.cwd(), outputPath) || outputPath}: ${rows.length} rows (${summary})`,
    );
  }

  /** Compares the steps that RAN: a test that failed at step 3 never reached
   *  step 4, and that is a failure, not drift. Only a passing test must have
   *  run every step. */
  private stepDrift(test: TestCase, manual: ManualCase): string[] {
    const result = test.results.at(-1);
    if (!result) return [];
    const ran = result.steps.filter((s) => s.category === 'test.step').map((s) => s.title);
    const expected = manual.steps.map(normalizeStep);
    for (let i = 0; i < ran.length; i++) {
      const numbered = STEP_NUMBER.exec(ran[i]);
      if (numbered && Number(numbered[1]) !== i + 1) {
        return [`${manual.id}: ${this.where(test)} numbers its step ${i + 1} as "${numbered[1]}."`];
      }
      const title = normalizeStep(ran[i].replace(STEP_NUMBER, ''));
      if (title !== expected[i]) {
        return [
          `${manual.id} step ${i + 1}: the manual file says "${expected[i] ?? '(no such step)'}", ` +
            `${this.where(test)} says "${title}"`,
        ];
      }
    }
    if (result.status === 'passed' && ran.length !== expected.length) {
      return [
        `${manual.id} has ${expected.length} steps in the manual file, ${this.where(test)} ran ${ran.length}`,
      ];
    }
    return [];
  }

  private row(test: TestCase, id: string | undefined, manual?: ManualCase): Row {
    const result = test.results.at(-1);
    const status = statusOf(test, result);
    const failed = result?.steps.find((s) => s.error);
    const error = result?.error?.message ?? result?.error?.value;
    const screenshots = (result?.attachments ?? [])
      .filter((a) => a.name === 'screenshot' && a.path)
      .map((a) => path.relative(this.configDir, a.path!).split(path.sep).join('/'));
    return {
      'Test Case ID': id ?? '',
      Module: manual?.module,
      'Manual Test Case': manual?.title ?? `(not in ${this.manualCases})`,
      // Root, project and file titles are dropped: the Project and Location columns carry them.
      'Automated Test': test.titlePath().slice(3).join(' > '),
      Project: test.parent.project()?.name ?? '',
      Status: status,
      'Duration (ms)': result?.duration,
      'Failed Step': status === 'Blocked' ? 'Pre-conditions' : failed?.title,
      // A skipped test has no error; its reason (why the data or the feature
      // it needs is absent) is what a reader of the Skipped row needs instead.
      Error: (status === 'Skipped'
        ? result?.annotations.find((a) => a.type === 'skip')?.description
        : error
      )
        ?.replace(ANSI, '')
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, 1000),
      'Manual-only Checks': manual?.manualOnly.join('; '),
      Screenshots: screenshots.join(' | '),
      Location: this.where(test),
      'Executed At': result?.startTime.toISOString(),
    };
  }

  private where(test: TestCase): string {
    const file = path.relative(this.configDir, test.location.file).split(path.sep).join('/');
    return `${file}:${test.location.line}`;
  }
}
