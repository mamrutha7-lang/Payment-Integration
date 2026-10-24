"""Summarise a JMeter JTL (CSV) into a Markdown table, in MILLISECONDS.

Per label: samples, errors, mean / median / p90 / p95 / p99 / max elapsed,
mean latency (time to first byte) and connect time, and throughput. Also the
peak and mean requests-per-second over the run, computed from the per-sample
millisecond timestamps.
"""
import csv
import statistics
import sys
from collections import defaultdict


def pct(values, p):
    if not values:
        return 0
    k = (len(values) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(values) - 1)
    return values[f] + (values[c] - values[f]) * (k - f)


def main(path):
    rows = list(csv.DictReader(open(path, newline="")))
    if not rows:
        print("no samples")
        return
    by = defaultdict(list)
    for r in rows:
        by[r["label"]].append(r)
    by["TOTAL"] = rows
    t0 = min(int(r["timeStamp"]) for r in rows)
    t1 = max(int(r["timeStamp"]) + int(r["elapsed"]) for r in rows)
    secs = max((t1 - t0) / 1000, 0.001)
    per_sec = defaultdict(int)
    for r in rows:
        per_sec[(int(r["timeStamp"]) - t0) // 1000] += 1
    print(f"Run window: {secs:.1f} s, {len(rows)} samples, "
          f"mean {len(rows)/secs:.1f} req/s, peak {max(per_sec.values())} req/s\n")
    print("| Endpoint | Samples | Errors | Error % | Mean ms | Median ms | p90 ms | p95 ms | p99 ms | Max ms | Latency ms | Connect ms | req/s |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for label, rs in sorted(by.items(), key=lambda kv: (kv[0] == "TOTAL", kv[0])):
        el = sorted(int(r["elapsed"]) for r in rs)
        err = sum(1 for r in rs if r["success"] != "true")
        lat = statistics.fmean(int(r["Latency"]) for r in rs)
        con = statistics.fmean(int(r.get("Connect", 0) or 0) for r in rs)
        name = f"**{label}**" if label == "TOTAL" else label
        print(f"| {name} | {len(rs)} | {err} | {100*err/len(rs):.2f} | {statistics.fmean(el):.1f} | "
              f"{pct(el,50):.0f} | {pct(el,90):.0f} | {pct(el,95):.0f} | {pct(el,99):.0f} | {el[-1]} | "
              f"{lat:.1f} | {con:.2f} | {len(rs)/secs:.1f} |")
    errs = defaultdict(int)
    for r in rows:
        if r["success"] != "true":
            errs[(r["label"], r["responseCode"], " ".join((r.get("failureMessage") or "").split())[:90])] += 1
    if errs:
        print("\nFailures:\n")
        for (lbl, code, msg), n in sorted(errs.items(), key=lambda kv: -kv[1])[:15]:
            print(f"- {n} x {lbl} -> {code} {msg}")


if __name__ == "__main__":
    main(sys.argv[1])
