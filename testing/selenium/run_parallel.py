"""TS-SEL-02  Selenium - N concurrent browser instances (default 100).

Each instance is a real Chromium signing a DIFFERENT student in through the
login form (REEP keeps one session per account, so 100 browsers on one account
would measure 99 sign-outs), then opening Jobs and Time Sheet. Per instance it
records, in milliseconds:

    browser_start   WebDriver session creation
    login_page      /login navigation until the form is interactive
    login_to_home   click "Sign in" -> student home heading visible (end-to-end,
                    includes the scrypt check and the dashboard's API calls)
    jobs_page       /student/jobs until its heading is visible
    ledger_page     /student/time-log until its heading is visible
    total           the whole journey

    python selenium/run_parallel.py --instances 100 --concurrency 100
    python selenium/run_parallel.py --instances 100 --barrier         # all 100 journeys at the same moment
    python selenium/run_parallel.py --instances 1                     # the single-instance baseline

--concurrency caps how many browsers are alive at once. On a Selenium Grid
(SELENIUM_REMOTE_URL) the Grid's own slot count is the real cap.
Output: testing/results/selenium/parallel-<N>x<C>.{json,csv,md}
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
_CD = "/opt/chromedriver141/chromedriver-linux64/chromedriver"
if Path(_CD).exists():
    os.environ.setdefault("CHROMEDRIVER", _CD)

from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.support import expected_conditions as EC  # noqa: E402

from driver_factory import BASE_URL, make_driver  # noqa: E402
from pages.login_page import LoginPage  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
USERS = ROOT / "jmeter" / "data" / "users.csv"
OUT = ROOT / "results" / "selenium"
H1 = (By.TAG_NAME, "h1")
_start_gate = threading.Lock()
_barrier: threading.Barrier | None = None


def journey(idx: int, email: str, password: str) -> dict:
    rec = {"instance": idx, "email": email, "ok": False, "error": ""}
    t_all = time.perf_counter()
    driver = None
    try:
        t = time.perf_counter()
        with _start_gate:  # chromedriver start-up is serialised; the journeys are not
            driver = make_driver()
        rec["browser_start"] = round((time.perf_counter() - t) * 1000)
        if _barrier is not None:
            # --barrier: nobody signs in until EVERY browser is up, so all N
            # journeys genuinely overlap instead of trickling in behind the
            # serialised start-up.
            _barrier.wait(timeout=900)
            rec["barrier_released_at"] = time.time()

        t = time.perf_counter()
        page = LoginPage(driver).load()
        rec["login_page"] = round((time.perf_counter() - t) * 1000)

        t = time.perf_counter()
        page.sign_in(email, password)
        page.wait_path_startswith("/student")
        outcome = page.student_home_outcome()
        rec["login_to_home"] = round((time.perf_counter() - t) * 1000)
        if outcome != "data":
            raise AssertionError("student home showed its error state: 'Could not load your overview.'")

        for key, path in (("jobs_page", "/student/jobs"), ("ledger_page", "/student/time-log")):
            t = time.perf_counter()
            driver.get(f"{BASE_URL}{path}")
            page.wait.until(EC.visibility_of_element_located(H1))
            if page.path().startswith("/login"):
                raise AssertionError(f"{path}: signed out mid-journey")
            rec[key] = round((time.perf_counter() - t) * 1000)
        rec["ok"] = True
    except Exception as exc:  # recorded, not raised: one failed browser is a data point
        rec["error"] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:160] if str(exc) else ''}"
        if os.environ.get("DEBUG"):
            traceback.print_exc()
    finally:
        rec["total"] = round((time.perf_counter() - t_all) * 1000)
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
    return rec


def pct(v, p):
    v = sorted(v)
    if not v:
        return 0
    k = (len(v) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(v) - 1)
    return round(v[f] + (v[c] - v[f]) * (k - f))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instances", type=int, default=100)
    ap.add_argument("--concurrency", type=int, default=None, help="default: = instances")
    ap.add_argument("--barrier", action="store_true",
                    help="start all browsers first, then release every journey at once (needs concurrency == instances)")
    args = ap.parse_args()
    conc = args.concurrency or args.instances
    global _barrier
    if args.barrier:
        if conc != args.instances:
            sys.exit("--barrier needs --concurrency equal to --instances")
        _barrier = threading.Barrier(args.instances)
    users = list(csv.DictReader(open(USERS)))
    if len(users) < args.instances:
        sys.exit(f"{USERS} has {len(users)} accounts; need {args.instances}. Run testing/tools/create_load_users.py")

    print(f"{args.instances} browser instances, {conc} concurrent, against {BASE_URL}")
    t0 = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=conc) as pool:
        futs = [pool.submit(journey, i + 1, u["email"], u["password"]) for i, u in enumerate(users[: args.instances])]
        for f in as_completed(futs):
            r = f.result()
            results.append(r)
            print(f"  #{r['instance']:03d} {'PASS' if r['ok'] else 'FAIL'} {r['total']} ms {r['error']}")
    wall = time.perf_counter() - t0
    results.sort(key=lambda r: r["instance"])

    OUT.mkdir(parents=True, exist_ok=True)
    stem = OUT / f"parallel-{args.instances}x{conc}{'-barrier' if args.barrier else ''}"
    stem.with_suffix(".json").write_text(json.dumps(results, indent=2))
    keys = ["instance", "email", "ok", "browser_start", "login_page", "login_to_home", "jobs_page", "ledger_page", "total", "error"]
    with open(stem.with_suffix(".csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k, "") for k in keys})

    ok = [r for r in results if r["ok"]]
    mode = "all released together after start-up (--barrier)" if args.barrier else "each starts its journey as soon as its browser is up"
    lines = [f"# Selenium parallel run: {args.instances} instances, {conc} concurrent", "", f"- Mode: {mode}",
             f"- Wall clock: {wall:.1f} s",
             f"- Passed: {len(ok)}/{len(results)} ({100 * len(ok) / len(results):.1f} %)",
             f"- Throughput: {len(ok) / wall:.2f} complete journeys / s", "",
             "| Step (ms) | Mean | Median | p90 | p95 | Max |", "|---|---:|---:|---:|---:|---:|"]
    for k in keys[3:9]:
        if args.barrier and k == "total":
            continue  # includes the wait for the slowest browser to start; not a user-facing time
        v = [r[k] for r in ok if k in r]
        if v:
            lines.append(f"| {k} | {statistics.fmean(v):.0f} | {pct(v, 50)} | {pct(v, 90)} | {pct(v, 95)} | {max(v)} |")
    errs = {}
    for r in results:
        if not r["ok"]:
            errs[r["error"]] = errs.get(r["error"], 0) + 1
    if errs:
        lines += ["", "Failures:", ""] + [f"- {n} x {e}" for e, n in sorted(errs.items(), key=lambda kv: -kv[1])]
    stem.with_suffix(".md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
