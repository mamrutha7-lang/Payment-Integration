#!/usr/bin/env bash
# REEP API performance tests with Apache JMeter (non-GUI mode, per the JMeter
# user manual's "Best Practices": never load-test from the GUI, no listeners in
# the plan, write a JTL and build the HTML dashboard from it afterwards).
#
#   testing/jmeter/run.sh <scenario>        scenario: smoke | load | stress | spike | soak | all
#
# Env: JMETER (default: jmeter on PATH, else /opt/apache-jmeter-5.6.3/bin/jmeter)
#      HOST PORT PROTOCOL (default localhost 3300 http)
# Needs the load-test accounts first (virtual user N signs in as loadtestNNN, so
# threads must not exceed them - REEP allows one live session per account):
#   (cd apps/api-py && .venv/bin/python ../../testing/tools/create_load_users.py --count 100)
set -euo pipefail
cd "$(dirname "$0")"
JMETER="${JMETER:-$(command -v jmeter || echo /opt/apache-jmeter-5.6.3/bin/jmeter)}"
HOST="${HOST:-localhost}"; PORT="${PORT:-3300}"; PROTOCOL="${PROTOCOL:-http}"
OUT="../results/jmeter"
mkdir -p "$OUT"

run() {  # name threads rampup duration think_ms
  local name=$1 threads=$2 ramp=$3 dur=$4 think=$5
  echo "== $name: $threads VUs, ramp ${ramp}s, ${dur}s, think ${think}ms"
  rm -rf "$OUT/$name" "$OUT/$name.jtl"
  "$JMETER" -n -t reep-api-load.jmx -l "$OUT/$name.jtl" -j "$OUT/$name.log" \
    -Jhost="$HOST" -Jport="$PORT" -Jprotocol="$PROTOCOL" \
    -Jthreads="$threads" -Jrampup="$ramp" -Jduration="$dur" -Jthink_ms="$think" \
    -Jthink_jitter_ms="$think" -Jsla_ms="${SLA_MS:-1000}" -Jmax_users="${MAX_USERS:-100}" \
    -Jjmeter.save.saveservice.timestamp_format=ms \
    -e -o "$OUT/$name"
  python3 summarize_jtl.py "$OUT/$name.jtl" > "$OUT/$name-summary.md"
  cat "$OUT/$name-summary.md"
}

case "${1:-smoke}" in
  smoke)  run smoke    1   1   30  0   ;;   # does the script work at all
  load)   run load    50  30  180  500 ;;   # expected busy-hour load
  stress) run stress 100  60  180  0   ;;   # 100 VUs, no think time: find the knee
  spike)  run spike  100   2   90  200 ;;   # 100 users arrive within 2 s
  soak)   run soak    30  30  600  1000 ;;  # 10 min endurance: leaks, pool exhaustion
  all)    for s in smoke load stress spike soak; do "$0" "$s"; done ;;
  *) echo "unknown scenario $1" >&2; exit 2 ;;
esac
