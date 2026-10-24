#!/usr/bin/env bash
# Swagger/OpenAPI property-based API testing with Schemathesis.
#
# Schemathesis reads the API's own OpenAPI document (/openapi.json - what
# Swagger UI at /docs renders), GENERATES requests from every parameter schema
# (valid and invalid), and checks each response against the same document.
#
# READ-ONLY BY DESIGN: only GET operations. Fuzzing POST/PATCH/DELETE against a
# database people use would create, rewrite and delete real rows (and REEP's
# delete endpoints are deliberately destructive). Run write fuzzing only against
# a throwaway database.
#
# HOW the writes are kept out matters, because Schemathesis 4.28 surprised us
# twice (TW-003): include filters of different kinds are OR'ed, and exclude
# filters of different kinds are AND'ed - so `--exclude-method-regex X
# --exclude-path-regex Y` excludes only operations matching BOTH, and every
# write came back. So: ONE exclude (the method, case-insensitive), the unwanted
# paths as a negative lookahead in the include, and no stateful phase (it
# follows OpenAPI links into write operations).
#
#   testing/api/run_schemathesis.sh            # uses REEP_API, default http://localhost:3300
set -uo pipefail
cd "$(dirname "$0")/.."
API="${REEP_API:-http://localhost:3300}"
ST=".venv/bin/schemathesis"
OUT="results/api/schemathesis"
mkdir -p "$OUT"

cookie() {  # email password -> reep_session value
  curl -s -c - -X POST "$API/api/auth/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"$2\"}" | awk '/reep_session/ {print $7}'
}

run() {  # name cookie path-regex
  local name=$1 c=$2 re=$3
  # An ARRAY, not ${c:+-H "Cookie: ..."}: unquoted, that splits at the space
  # after "Cookie:" and the run goes out unauthenticated (it did, once).
  local auth=()
  [ -n "$c" ] && auth=(-H "Cookie: reep_session=$c")
  echo "== schemathesis: $name ($re)"
  "$ST" run "$API/openapi.json" \
    --include-path-regex "$re(?!.*(\.csv|\.pdf|/export|/file|/audio|/paper|/report))" \
    --exclude-method-regex '(?i)^(post|put|patch|delete)$' \
    --phases examples,coverage,fuzzing \
    --checks not_a_server_error,status_code_conformance,content_type_conformance,response_schema_conformance \
    --max-examples "${MAX_EXAMPLES:-15}" --request-timeout 30 --workers 1 \
    "${auth[@]}" --suppress-health-check all \
    --report junit,ndjson --report-dir "$OUT/$name" \
    --continue-on-failure 2>&1 | tee "$OUT/$name.txt" | tail -40
}

run public "" '^/(health|api/(auth/sso/status|register/hierarchy|interview/status))$'
run student "$(cookie loadtest105@bgscet.ac.in 'LoadTest#2026')" '^/api/student/'
run admin "$(cookie admin@bgscet.ac.in admin123)" '^/api/admin/'
