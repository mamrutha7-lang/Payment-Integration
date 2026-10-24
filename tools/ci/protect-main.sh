#!/usr/bin/env bash
#
# protect-main.sh — put the gate on `main` that this repository does not have.
#
# WHY THIS EXISTS. Every mechanical control in this repo is advisory today, and
# all of them for the same single cause:
#
#     $ gh api repos/darshani8/reep-/branches/main/protection
#     404  "Branch not protected"
#     $ gh api repos/darshani8/reep-/rulesets
#     []
#
# CI has five jobs and they are good jobs — a real Postgres, REEP_REQUIRE_DB=1
# so a silently skipped DB test is a failure, a dependency-completeness check
# per manifest, a production Angular build against the bundle budget. None of
# them can stop anything. `git push origin main` writes to the default branch
# and CI finds out afterwards. Delete the `if not mentor_id` branch in
# apps/api-py/app/routers/mentor.py and tests/test_mentee_records.py goes red —
# and the push lands anyway, and from that moment every MENTOR reads every
# student's marks, attendance and USN. That is rule 2 of AGENTS.md, defended by
# a test that nothing is obliged to run.
#
# This script closes that. It is the one control the rest of the process hangs
# off: the PR template, CONTRIBUTING.md, the pre-commit hooks and CODEOWNERS are
# all conventions, and a convention on an unprotected branch is a note taped to
# an open door.
#
# RUN STEP 1 OF THE PROCESS FIRST — the two-line `cancel-in-progress` fix in
# .github/workflows/ci.yml. ci.yml currently cancels an in-flight run on EVERY
# ref including main, and a `cancelled` check is neither success nor failure: it
# never resolves. Turn on required checks before that fix and main wears a
# permanently amber check, which reads as a flake, and the first person blocked
# by it goes looking for the bypass. AGENTS.md says the same thing about the
# ENV=prod boot guard — a guard that trips on a laptop gets deleted by whoever
# is trying to ship that afternoon. Order matters here for exactly that reason.
#
# -----------------------------------------------------------------------------
# THE APPROVAL DECISION IS YOURS, AND IT IS A REAL TRADE. READ THIS.
# -----------------------------------------------------------------------------
# The default below requires 1 approving review AND applies the rules to
# administrators. On a repository with one collaborator — `gh api
# repos/darshani8/reep-/collaborators` returns exactly one account — those two
# settings together mean YOU CANNOT MERGE YOUR OWN PULL REQUEST. GitHub does not
# let an author approve their own PR, and enforce_admins removes the override
# you would otherwise reach for. There is no third setting that fixes this. Pick
# one of two honest options:
#
#   (a) Keep it. Every merge needs a second human or a second account. This is
#       the right answer the day a second maintainer exists, and it is the only
#       configuration in which "reviewed" means a person other than the author
#       read the diff. The friction is the feature; it is also real, and on a
#       solo repo it is friction with nobody on the other side of it.
#
#   (b) Drop to zero approvals:  ./tools/ci/protect-main.sh --approvals 0
#       You still cannot push to main. You still cannot merge red. Every change
#       still goes branch -> PR -> five green checks -> merge, and the checks are
#       what actually verify anything. What you give up is the second pair of
#       eyes, which on a solo repo you did not have anyway. This is the setting
#       that gets USED, and a used gate beats an unused one that was switched off
#       on a Friday.
#
# A third option that is NOT the default here: leaving admins exempt while
# requiring an approval. That configuration reads as protected and behaves as
# unprotected for the only account that can push, which is the worst of the
# three. The escape hatch exists as --allow-admin-bypass so that taking it is a
# deliberate act with a flag name that says what it does.
#
# -----------------------------------------------------------------------------
# THE STATUS CHECK NAMES ARE STRINGS, AND THAT IS THE SHARP EDGE.
# -----------------------------------------------------------------------------
# GitHub matches a required status check by the DISPLAY NAME of the job — the
# `name:` field in .github/workflows/ci.yml, not the job's YAML key (`api`,
# `pii-gate`, `api-imports`, `web`, `cdk`). The five strings in REQUIRED_CHECKS
# below must match those `name:` values byte for byte: spaces, parentheses and
# capitalisation included.
#
# The failure mode is silent, and it is why this script greps ci.yml before it
# calls the API. Rename "Web (Angular)" to "Web (Angular 20)" in an otherwise
# ordinary PR and: the PR reports a passing "Web (Angular 20)", the required
# "Web (Angular)" is never reported at all, and a required check that has never
# been reported on a branch is a check GitHub has nothing to wait for. The job
# still runs. The job still goes green. It has simply stopped being a gate, and
# nothing anywhere says so — the required-check list lives in a settings pane
# that no pull request reviews. A renamed job does not fail. It retires.
#
# So: rename a job and re-run this script in the same change. The pre-flight
# check below turns "you renamed a gate" into an error today instead of a gap
# discovered three months from now.
#
# That pre-flight only fires for whoever remembers to run this script, and it
# says nothing about .github/rulesets/main.json — which is how the ruleset came
# to ask for "Voice worker (dependency completeness)" for months after that job
# went with the LiveKit stack. Since Phase 5 the agreement is also a TEST:
# apps/api-py/tests/test_codebase_guards.py compares ci.yml's job names against
# this array, against the committed ruleset and against tools/ci/preflight.sh,
# and fails the `api` job when any of the four disagrees. Edit all four in one
# commit; CI will tell you if you missed one.
#
# -----------------------------------------------------------------------------
# WHAT THIS SCRIPT USES, AND WHAT IT DOES NOT
# -----------------------------------------------------------------------------
# It applies CLASSIC branch protection (`PUT /repos/{owner}/{repo}/branches/
# {branch}/protection`), because that endpoint is a single idempotent full-state
# PUT: re-running it converges rather than accumulating, which is what makes it
# safe to run from a runbook by someone who cannot remember whether it was run
# last month. Rulesets are the newer mechanism, and .github/rulesets/main.json
# is the committed record of the equivalent configuration. If you apply BOTH,
# know that GitHub evaluates both and the MOST RESTRICTIVE wins — they do not
# override one another, and a rule you "removed" from one can still be live in
# the other. Choose one as the source of truth and say which in SECURITY.md.
#
# Usage:
#   ./tools/ci/protect-main.sh                 # show the plan, ask, apply
#   ./tools/ci/protect-main.sh --dry-run       # print the exact payload, send nothing
#   ./tools/ci/protect-main.sh --yes           # non-interactive (runbook, CI)
#   ./tools/ci/protect-main.sh --approvals 0   # option (b) above
#   ./tools/ci/protect-main.sh --show          # print current protection and exit
#
# Requires: gh >= 2.0, authenticated with ADMIN on the repository.
#   classic PAT   -> scope `repo` (plus `admin:org` for an org-owned repo)
#   fine-grained  -> Repository permissions -> Administration: Read and write
#
set -euo pipefail

REPO="${REEP_PROTECT_REPO:-darshani8/reep-}"
BRANCH="${REEP_PROTECT_BRANCH:-main}"

# EXACT job display names from .github/workflows/ci.yml. See the long note
# above: these are matched as strings, and a rename retires the gate in silence.
REQUIRED_CHECKS=(
  "API (FastAPI + Postgres)"
  "Rule 1 (every model call declares its cargo)"
  "API (dependency completeness)"
  "Web (Angular)"
  "Infra (CDK synth guards)"
)

APPROVALS=1
ENFORCE_ADMINS=true
ASSUME_YES=false
DRY_RUN=false
SHOW_ONLY=false
SKIP_NAME_CHECK=false

# --------------------------------------------------------------- plumbing ---

say()  { printf '%s\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
rule() { printf '%s\n' "-----------------------------------------------------------------"; }

usage() {
  sed -n '2,/^set -euo pipefail/p' "$0" | sed '$d' | sed 's/^#\{1,\} \{0,1\}//'
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y)             ASSUME_YES=true ;;
    --dry-run|-n)         DRY_RUN=true ;;
    --show)               SHOW_ONLY=true ;;
    --approvals)          shift; APPROVALS="${1:?--approvals needs a number}" ;;
    --approvals=*)        APPROVALS="${1#*=}" ;;
    --allow-admin-bypass) ENFORCE_ADMINS=false ;;
    --skip-name-check)    SKIP_NAME_CHECK=true ;;
    --repo)               shift; REPO="${1:?--repo needs owner/name}" ;;
    --repo=*)             REPO="${1#*=}" ;;
    --branch)             shift; BRANCH="${1:?--branch needs a name}" ;;
    --branch=*)           BRANCH="${1#*=}" ;;
    --help|-h)            usage 0 ;;
    *)                    warn "unknown argument: $1"; usage 1 ;;
  esac
  shift
done

case "$APPROVALS" in
  ''|*[!0-9]*) die "--approvals takes a whole number (0 disables the review requirement)" ;;
esac

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../.." && pwd))"
CI_WORKFLOW="$REPO_ROOT/.github/workflows/ci.yml"

# ------------------------------------------------------------- pre-flight ---
#
# Fail here, loudly, rather than half way through a PUT. A partially applied
# protection is worse than none, because it looks configured.

command -v gh >/dev/null 2>&1 \
  || die "the GitHub CLI (gh) is not on PATH. Install it: https://cli.github.com"

AUTH_OK=true
if ! gh auth status >/dev/null 2>&1; then
  AUTH_OK=false
  AUTH_MSG="not authenticated to GitHub. Run:  gh auth login   (or export GH_TOKEN)"
  if [ "$DRY_RUN" = true ]; then
    warn "$AUTH_MSG -- continuing anyway because --dry-run sends nothing."
  else
    die "$AUTH_MSG"
  fi
fi

if [ "$AUTH_OK" = true ]; then
  ADMIN="$(gh api "repos/$REPO" --jq '.permissions.admin' 2>/dev/null || echo unknown)"
  if [ "$ADMIN" != "true" ]; then
    ADMIN_MSG="this token does not have ADMIN on $REPO (permissions.admin = $ADMIN).
       Branch protection is an admin-only API: a token with push rights gets a
       403 from the endpoint below and NOTHING is applied.
         classic PAT   -> scope 'repo'
         fine-grained  -> Repository permissions -> Administration: Read and write
       Then re-authenticate:  gh auth refresh -h github.com -s repo"
    if [ "$DRY_RUN" = true ]; then warn "$ADMIN_MSG"; else die "$ADMIN_MSG"; fi
  fi
fi

# Job names are matched as strings by GitHub. Prove they still exist in ci.yml
# before pinning them, so "we renamed a job" is an error now rather than a gate
# that quietly stopped gating.
if [ "$SKIP_NAME_CHECK" = false ] && [ -f "$CI_WORKFLOW" ]; then
  missing=()
  for check in "${REQUIRED_CHECKS[@]}"; do
    grep -Fq "name: $check" "$CI_WORKFLOW" || missing+=("$check")
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    printf '\033[31mERROR: these required check names are NOT job names in %s:\033[0m\n' \
      "$CI_WORKFLOW" >&2
    for m in "${missing[@]}"; do printf '           "%s"\n' "$m" >&2; done
    printf '%s\n' "
       A required check that no job reports is never reported, and GitHub does
       not wait for a check it has never seen on that branch. The job would keep
       running, keep passing, and stop being a gate -- silently.

       Fix the strings in REQUIRED_CHECKS at the top of this script so they match
       the job 'name:' values in ci.yml, or pass --skip-name-check if you are
       deliberately pinning a check that lands in this same change." >&2
    exit 1
  fi
elif [ "$SKIP_NAME_CHECK" = false ]; then
  warn "cannot read $CI_WORKFLOW -- skipping the job-name cross-check."
fi

# ------------------------------------------------------ current vs desired ---
#
# One semicolon-separated line, built the same way from both sides, so "already
# applied" is a string comparison and a second run is a no-op that says so.

CURRENT_SUMMARY_JQ='
  "strict=\(.required_status_checks.strict // false)"
+ ";checks=\((.required_status_checks.checks // [] | map(.context) | sort | join("|")))"
+ ";admins=\(.enforce_admins.enabled // false)"
+ ";pull_request_required=\(if .required_pull_request_reviews == null then false else true end)"
+ ";approvals=\(.required_pull_request_reviews.required_approving_review_count // 0)"
+ ";dismiss_stale=\(.required_pull_request_reviews.dismiss_stale_reviews // false)"
+ ";conversation_resolution=\(.required_conversation_resolution.enabled // false)"
+ ";force_pushes=\(.allow_force_pushes.enabled // false)"
+ ";deletions=\(.allow_deletions.enabled // false)"
'

# Prints the summary line, or NOTHING when the branch is unprotected.
#
# The `if` is not decoration. On an unprotected branch the endpoint answers 404
# with a JSON body, and `gh api` writes that body to STDOUT and ignores --jq
# entirely — so a naive `gh api ... --jq X || true` hands back
# {"message":"Branch not protected",...} as if it were a summary. Branch on the
# exit status, and re-check the shape, because "unprotected" and "protected"
# must never be told apart by a string that the error path can also produce.
read_current() {
  local out
  if out="$(gh api "repos/$REPO/branches/$BRANCH/protection" --jq "$CURRENT_SUMMARY_JQ" 2>/dev/null)"; then
    case "$out" in
      strict=*) printf '%s' "$out" ;;
    esac
  fi
}

print_summary() {   # one semicolon line -> an indented key/value block
  # printf '%s\n', not '%s': without the trailing newline `read` fails on the
  # final field and the loop silently drops it — which quietly hid `deletions`,
  # the one setting on this list that decides whether main can be erased.
  printf '%s\n' "$1" | tr ';' '\n' | while IFS='=' read -r k v; do
    printf '    %-26s %s\n' "$k" "${v:-<unset>}"
  done
}

# jq's sort is by codepoint; LC_ALL=C sort is the same ordering, so the two
# sides of the comparison agree without either being re-sorted at read time.
joined_checks=""
while IFS= read -r line; do
  if [ -z "$joined_checks" ]; then joined_checks="$line"; else joined_checks="$joined_checks|$line"; fi
done <<< "$(printf '%s\n' "${REQUIRED_CHECKS[@]}" | LC_ALL=C sort)"

DESIRED_SUMMARY="strict=true;checks=$joined_checks;admins=$ENFORCE_ADMINS;pull_request_required=true;approvals=$APPROVALS;dismiss_stale=true;conversation_resolution=true;force_pushes=false;deletions=false"
CURRENT_SUMMARY="$(read_current)"

if [ "$SHOW_ONLY" = true ]; then
  rule
  say "Current protection — $REPO @ $BRANCH"
  rule
  if [ -z "$CURRENT_SUMMARY" ]; then
    say "    (none — GET .../branches/$BRANCH/protection answers 404 \"Branch not protected\")"
  else
    print_summary "$CURRENT_SUMMARY"
  fi
  exit 0
fi

# ------------------------------------------------------------- the payload ---

checks_json=""
for check in "${REQUIRED_CHECKS[@]}"; do
  escaped="${check//\\/\\\\}"
  escaped="${escaped//\"/\\\"}"
  if [ -n "$checks_json" ]; then checks_json="$checks_json,"; fi
  checks_json="$checks_json
        { \"context\": \"$escaped\" }"
done

# Zero approvals still REQUIRES a pull request. This block is what forces the
# branch-and-check path; the count only decides whether a second human is in it.
reviews_json="{
    \"dismiss_stale_reviews\": true,
    \"require_code_owner_reviews\": false,
    \"required_approving_review_count\": $APPROVALS
  }"

PAYLOAD_FILE="$(mktemp -t reep-protection.XXXXXX)"
trap 'rm -f "$PAYLOAD_FILE"' EXIT

cat > "$PAYLOAD_FILE" <<PAYLOAD
{
  "required_status_checks": {
    "strict": true,
    "checks": [$checks_json
    ]
  },
  "enforce_admins": $ENFORCE_ADMINS,
  "required_pull_request_reviews": $reviews_json,
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true,
  "lock_branch": false,
  "allow_fork_syncing": false
}
PAYLOAD

# ------------------------------------------------------------------ report ---

rule
say "Branch protection — $REPO @ $BRANCH"
rule
say ""
say "  NOW:"
if [ -z "$CURRENT_SUMMARY" ]; then
  say "    (unprotected — the API answers 404 \"Branch not protected\")"
else
  print_summary "$CURRENT_SUMMARY"
fi
say ""
say "  AFTER:"
print_summary "$DESIRED_SUMMARY"
say ""
say "  strict = \"require branches to be up to date before merging\", and it stays on:"
say "  GitHub does not re-run an open PR's checks when main moves underneath it, so"
say "  without strict two Alembic revisions generated against the same parent each"
say "  pass their own run and then collide on main as multiple heads."
say ""

if [ "$APPROVALS" -gt 0 ] && [ "$ENFORCE_ADMINS" = true ]; then
  warn "  NOTE: $APPROVALS approval(s) + enforced-for-admins, on a one-collaborator repo,"
  warn "        means you cannot merge your own pull request at all. That is option (a)"
  warn "        in this script's header and it is a real choice — if it is not the one"
  warn "        you meant to make, re-run with --approvals 0."
  say ""
fi

say "  PUT repos/$REPO/branches/$BRANCH/protection"
say ""
sed 's/^/    /' "$PAYLOAD_FILE"
say ""

if [ "$DRY_RUN" = true ]; then
  rule
  say "--dry-run: nothing was sent."
  exit 0
fi

if [ -n "$CURRENT_SUMMARY" ] && [ "$CURRENT_SUMMARY" = "$DESIRED_SUMMARY" ]; then
  rule
  say "Already applied — every setting above already matches. Nothing to do."
  exit 0
fi

if [ "$ASSUME_YES" != true ]; then
  if [ ! -t 0 ] && [ ! -e /dev/tty ]; then
    die "no terminal to confirm on. Re-run with --yes, or --dry-run to inspect first."
  fi
  printf 'Apply this to %s@%s? [y/N] ' "$REPO" "$BRANCH"
  if [ -e /dev/tty ]; then read -r reply < /dev/tty; else read -r reply; fi
  case "$reply" in
    y|Y|yes|YES) ;;
    *) say "Aborted. Nothing was sent."; exit 1 ;;
  esac
fi

# The PUT is full-state: it REPLACES the protection object rather than merging
# into it, which is exactly why re-running this script converges instead of
# accumulating half-remembered settings.
gh api -X PUT "repos/$REPO/branches/$BRANCH/protection" \
  -H "Accept: application/vnd.github+json" \
  --input "$PAYLOAD_FILE" >/dev/null \
  || die "the API rejected the request. The usual causes, in order:
       - the token lost ADMIN on $REPO
       - branch '$BRANCH' does not exist on the remote
       - a repository ruleset already governs '$BRANCH' and conflicts with this
       Re-read the live state with:  $0 --show"

APPLIED="$(read_current)"
rule
say "Applied. Protection now reads:"
print_summary "$APPLIED"
say ""
if [ "$APPLIED" != "$DESIRED_SUMMARY" ]; then
  warn "...which is NOT what was requested. Compare the two blocks above before"
  warn "trusting this gate. The usual cause is a repository ruleset also in force"
  warn "on $BRANCH — GitHub evaluates both and the most restrictive wins."
  exit 1
fi
say "Now verify it from the other side. This must be refused:"
say "    git push origin $BRANCH     ->  remote: Changes must be made through a pull request"
say ""
say "Then record the date in SECURITY.md. A control nobody can point at the moment"
say "it was turned on is a control nobody can prove was ever on."
