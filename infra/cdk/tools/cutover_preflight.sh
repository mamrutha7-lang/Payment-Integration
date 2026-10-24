#!/usr/bin/env bash
# Step 0 of docs/cdk-cutover.md as ONE command, and READ-ONLY against AWS.
#
#     infra/cdk/tools/cutover_preflight.sh
#
# It answers "may the cutover start?" by checking every precondition the
# runbook lists — the tools, the venv, the synth guards, the credentials'
# account, the three CDK bootstraps, that reep-core does not exist yet, and
# that Terraform's plan is clean — and it changes nothing in AWS: no snapshot,
# no bootstrap, no stack. tests/test_cutover_tools.py pins that every `aws`
# call in this file is a describe/get/list/head. The three things it writes
# are local and gitignored: infra/aws/backend.hcl (from the account id, if it
# is missing), infra/cdk/tf-state.json (the state export the next steps read —
# SECRET VALUES, delete after step 6) and infra/aws/prod.tfvars (reconstructed
# from the state by tfvars_from_state.py, because the first apply's -var values
# were recorded nowhere).
#
# Exit 0 means every check passed and step 1 may begin. Exit 1 lists what to
# fix; rerun until it is 0. It is safe to run any number of times.
#
# REEP_ACCOUNT=<id> overrides the account the runbook targets; REEP_REGION the
# home region (default ap-south-1). Runs under bash on Linux, macOS and Git
# Bash on Windows.
set -uo pipefail

# Git Bash rewrites any argument that looks like a POSIX path (`/cdk-bootstrap/…`)
# into a Windows path before a native executable sees it: the SSM parameter
# name below arrived as C:/Program Files/Git/cdk-bootstrap/… and read back as
# "not found" — three bootstrapped regions reported as three missing ones.
# Off, for every command in this script. Unknown to every other shell.
export MSYS_NO_PATHCONV=1

ACCOUNT_EXPECTED="${REEP_ACCOUNT:-445363794125}"
HOME_REGION="${REEP_REGION:-ap-south-1}"
REGIONS=("$HOME_REGION" "us-east-1" "ap-southeast-1")
CDK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$(cd "$CDK_DIR/../aws" && pwd)"
REPO="$(cd "$CDK_DIR/../.." && pwd)"
LOG="${TMPDIR:-/tmp}/reep-cutover-preflight.$$.log"
: >"$LOG"

PASS=0
FAIL=0
WARN=0
pass() { PASS=$((PASS + 1)); printf '  ok    %s\n' "$*"; }
fail() { FAIL=$((FAIL + 1)); printf '  FAIL  %s\n' "$*"; }
warn() { WARN=$((WARN + 1)); printf '  warn  %s\n' "$*"; }
info() { printf '        %s\n' "$*"; }
section() { printf '\n== %s\n' "$*"; }
excerpt() { head -"${1:-12}" "$LOG" | sed 's/^/        | /'; }

summary() {
  printf '\n%d ok, %d warning(s), %d failure(s)\n' "$PASS" "$WARN" "$FAIL"
  if [ "$FAIL" -eq 0 ]; then
    echo "Step 0 is satisfied. Take the manual snapshot if a warning above asks for it, then continue at step 1."
  else
    echo "Fix the failures and rerun. Nothing in AWS was changed."
  fi
  [ -s "$LOG" ] && echo "(last command output: $LOG)"
}

# Windows (Git Bash): winget and npm put the tools on the USER path, which a
# shell opened before the install does not see. Look in the known places too.
case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*)
    for d in "/c/Program Files/Amazon/AWSCLIV2" \
      "$(cygpath "${APPDATA:-.}" 2>/dev/null)/npm" \
      "$(cygpath "${LOCALAPPDATA:-.}" 2>/dev/null)/Microsoft/WinGet/Links" \
      "$(cygpath "${LOCALAPPDATA:-.}" 2>/dev/null)"/Microsoft/WinGet/Packages/Hashicorp.Terraform_*; do
      [ -d "$d" ] && PATH="$PATH:$d"
    done
    export PATH
    ;;
esac

# ------------------------------------------------------------------ tools --
section "tools"
tool() { # name  version-command  how-to-install
  if command -v "$1" >/dev/null 2>&1; then
    pass "$1: $($2 2>/dev/null | head -1)"
  else
    fail "$1 is not on PATH — $3"
  fi
}
tool terraform "terraform version" "winget install Hashicorp.Terraform, or https://developer.hashicorp.com/terraform/install (>= 1.6)"
tool aws "aws --version" "winget install Amazon.AWSCLI (accept the elevation prompt), or https://aws.amazon.com/cli/"
tool cdk "cdk --version" "npm install -g aws-cdk"
tool node "node --version" "Node 20+ (https://nodejs.org)"
if command -v node >/dev/null 2>&1; then
  major="$(node -p 'process.versions.node.split(".")[0]')"
  if ! [ "$major" -ge 20 ] 2>/dev/null; then
    fail "node $major is older than 20"
  fi
fi
if command -v terraform >/dev/null 2>&1; then
  tfv="$(terraform version | head -1 | sed 's/^Terraform v//')"
  if [ "$(printf '%s\n' 1.6.0 "$tfv" | sort -V | head -1)" != "1.6.0" ]; then
    fail "terraform $tfv is older than versions.tf's >= 1.6.0"
  fi
fi

HAVE_VENV=0
if [ -f "$CDK_DIR/.venv/Scripts/activate" ]; then
  # shellcheck disable=SC1091
  . "$CDK_DIR/.venv/Scripts/activate"
elif [ -f "$CDK_DIR/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  . "$CDK_DIR/.venv/bin/activate"
else
  fail "no venv at infra/cdk/.venv — python -m venv .venv && pip install -r requirements-dev.txt"
fi
if command -v python >/dev/null 2>&1 && python -c "import aws_cdk" >/dev/null 2>&1; then
  pass "venv: python $(python -c 'import sys; print(sys.version.split()[0])') with aws-cdk-lib $(python -c 'import importlib.metadata as m; print(m.version("aws-cdk-lib"))' 2>/dev/null)"
  HAVE_VENV=1
else
  fail "the venv cannot import aws_cdk — pip install -r requirements-dev.txt"
fi

# ------------------------------------------------------------- repository --
section "repository"
if [ -e "$TF_DIR/cleanup-orphans.sh" ]; then
  fail "infra/aws/cleanup-orphans.sh exists — it deletes production by name; git rm it (step 0)"
else
  pass "no cleanup-orphans.sh in the tree"
fi
if git -C "$REPO" ls-files --error-unmatch infra/cdk/tf-state.json >/dev/null 2>&1; then
  fail "infra/cdk/tf-state.json is TRACKED by git — it holds secret values: git rm --cached it"
else
  pass "tf-state.json is not tracked by git"
fi

# --------------------------------------------------- offline proofs (no AWS) --
section "offline proofs (no AWS)"
if [ "$HAVE_VENV" -eq 1 ]; then
  if (cd "$CDK_DIR" && python -m pytest -q >"$LOG" 2>&1); then
    pass "synth guards: $(tail -1 "$LOG")"
  else
    fail "synth guards failed:"
    excerpt 20
  fi
  if command -v cdk >/dev/null 2>&1; then
    if (cd "$CDK_DIR" && CDK_DEFAULT_ACCOUNT="$ACCOUNT_EXPECTED" CDK_DEFAULT_REGION="$HOME_REGION" cdk synth --all --quiet -c phase=import >"$LOG" 2>&1); then
      pass "cdk synth --all in the import phase (the CLI and the library agree)"
    else
      fail "cdk synth failed — a CLI/library version mismatch is the usual cause:"
      excerpt
    fi
  fi
fi

# ----------------------------------------------------------- aws identity --
section "aws identity"
if ! command -v aws >/dev/null 2>&1; then
  fail "cannot continue without the aws CLI"
  summary
  exit 1
fi
if ! ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>"$LOG")"; then
  fail "no usable AWS credentials: $(grep -m1 -v '^[[:space:]]*$' "$LOG")"
  info "export AWS_PROFILE=<admin profile for account $ACCOUNT_EXPECTED>   (or AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN)"
  info "then rerun this script; everything above already passes or fails on its own."
  summary
  exit 1
fi
CALLER="$(aws sts get-caller-identity --query Arn --output text 2>/dev/null)"
if [ "$ACCOUNT" = "$ACCOUNT_EXPECTED" ]; then
  pass "credentials: $CALLER"
else
  fail "credentials are for account $ACCOUNT; the runbook targets $ACCOUNT_EXPECTED (REEP_ACCOUNT=<id> overrides)"
  summary
  exit 1
fi
export AWS_DEFAULT_REGION="$HOME_REGION" CDK_DEFAULT_REGION="$HOME_REGION" CDK_DEFAULT_ACCOUNT="$ACCOUNT"
# Terraform and the CDK CLI resolve credentials through their own SDKs, which
# do not all understand an `aws login` session yet. Hand them the same session
# as plain environment variables — temporary, and only for this process.
if creds="$(aws configure export-credentials --format env 2>/dev/null)" && [ -n "$creds" ]; then
  eval "$creds"
  pass "session exported to the environment for terraform and cdk"
else
  warn "aws configure export-credentials produced nothing; terraform and cdk fall back to their own credential chain"
fi
configured="$(aws configure get region 2>/dev/null || true)"
if [ -n "$configured" ] && [ "$configured" != "$HOME_REGION" ]; then
  warn "the profile's region is $configured; every runbook command must run with CDK_DEFAULT_REGION=$HOME_REGION (exported for the rest of this script)"
else
  pass "region $HOME_REGION"
fi

# --------------------------------------------------------- cloudformation --
section "cloudformation"
stack_status() {
  aws cloudformation describe-stacks --stack-name "$1" --region "$2" --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo MISSING
}
for r in "${REGIONS[@]}"; do
  v="$(aws ssm get-parameter --name /cdk-bootstrap/hnb659fds/version --region "$r" --query Parameter.Value --output text 2>/dev/null || true)"
  if [ -n "$v" ]; then
    pass "cdk bootstrap present in $r (version $v)"
  elif [ "$(stack_status CDKToolkit "$r")" != "MISSING" ]; then
    fail "$r has a CDKToolkit stack but no /cdk-bootstrap/hnb659fds/version parameter — run cdk bootstrap aws://$ACCOUNT/$r again"
  else
    fail "$r is not bootstrapped: cdk bootstrap aws://$ACCOUNT/$r"
  fi
done
s="$(stack_status reep-core "$HOME_REGION")"
case "$s" in
  MISSING) pass "reep-core does not exist yet (step 4 creates it by import)" ;;
  IMPORT_COMPLETE | UPDATE_COMPLETE) warn "reep-core already exists ($s) — you are past step 4; this preflight is for step 0" ;;
  *) fail "reep-core is $s — resolve that stack before importing (after a failed import: delete-stack; every resource is Retain)" ;;
esac
s="$(stack_status reep-edge-waf us-east-1)"
case "$s" in
  MISSING) pass "reep-edge-waf does not exist yet (step 3 creates it by import)" ;;
  IMPORT_COMPLETE | UPDATE_COMPLETE) warn "reep-edge-waf already exists ($s) — step 3 has run" ;;
  *) fail "reep-edge-waf is $s — resolve before continuing" ;;
esac
s="$(stack_status reep-voice-platform "$HOME_REGION")"
case "$s" in
  CREATE_COMPLETE | UPDATE_COMPLETE) pass "reep-voice-platform is $s (it holds the deploy role's grant to assume the CDK bootstrap roles)" ;;
  MISSING) warn "reep-voice-platform is not deployed — the import does not need it, but step 10's browser check and every later CDK deploy from CI do (infra/cdk/README.md)" ;;
  *) warn "reep-voice-platform is $s" ;;
esac

# -------------------------------------------------------------- terraform --
section "terraform (init, show, plan — nothing is applied)"
if ! command -v terraform >/dev/null 2>&1; then
  fail "cannot plan without terraform"
  summary
  exit 1
fi
if [ ! -f "$TF_DIR/backend.hcl" ]; then
  {
    echo "bucket         = \"reep-tfstate-${ACCOUNT}\""
    echo "key            = \"reep/aws/terraform.tfstate\""
    echo "region         = \"${HOME_REGION}\""
    echo "dynamodb_table = \"reep-tfstate-lock\""
    echo "encrypt        = true"
  } >"$TF_DIR/backend.hcl"
  info "wrote infra/aws/backend.hcl (gitignored) from bootstrap-state.sh's template"
fi
BUCKET="$(sed -n 's/^bucket *= *"\(.*\)".*/\1/p' "$TF_DIR/backend.hcl")"
if aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  pass "state bucket $BUCKET"
else
  fail "state bucket $BUCKET is not reachable — wrong account, or bootstrap-state.sh never ran here"
  summary
  exit 1
fi
if (cd "$TF_DIR" && terraform init -backend-config=backend.hcl -input=false -no-color >"$LOG" 2>&1); then
  pass "terraform init"
else
  fail "terraform init:"
  excerpt
  summary
  exit 1
fi
# `terraform state list` includes data sources; only managed addresses count,
# and the number itself is not the check — import_map.py refuses anything the
# template needs that the state lacks, which is the check that matters.
count="$(cd "$TF_DIR" && terraform state list 2>/dev/null | grep -vc '^data\.' | tr -d ' ')"
if [ "$count" -gt 0 ] 2>/dev/null; then
  pass "the state holds $count managed addresses (data sources excluded)"
else
  fail "the state is empty — this is not the account the stack was applied to"
  summary
  exit 1
fi
if (cd "$TF_DIR" && terraform show -json >"$CDK_DIR/tf-state.json" 2>"$LOG"); then
  pass "state exported to infra/cdk/tf-state.json — SECRET VALUES, gitignored, delete after step 6"
else
  fail "terraform show -json:"
  excerpt
  summary
  exit 1
fi
if [ -f "$TF_DIR/prod.tfvars" ]; then
  info "infra/aws/prod.tfvars exists — left alone (delete it to regenerate from the state)"
elif (cd "$CDK_DIR" && python tools/tfvars_from_state.py tf-state.json >"$LOG" 2>&1); then
  pass "prod.tfvars reconstructed from the state (the first apply's -var values were recorded nowhere)"
else
  fail "tfvars_from_state.py refused:"
  excerpt 20
fi
if [ -f "$TF_DIR/prod.tfvars" ]; then
  (cd "$TF_DIR" && terraform plan -var-file=prod.tfvars -detailed-exitcode -input=false -no-color -lock-timeout=60s >"$LOG" 2>&1)
  rc=$?
  case "$rc" in
    0) pass "terraform plan: no changes — the state matches reality and the variables" ;;
    2)
      fail "terraform plan proposes changes; resolve them before importing (a variable in prod.tfvars that differs from the first apply is the usual cause):"
      grep -E '^\s*# .* (will|must) be|^Plan:' "$LOG" | head -30 | sed 's/^/        | /'
      ;;
    *)
      fail "terraform plan errored:"
      excerpt 20
      ;;
  esac
fi

# --------------------------------------------------------------- database --
section "database"
if row="$(aws rds describe-db-instances --db-instance-identifier reep-postgres --region "$HOME_REGION" \
  --query 'DBInstances[0].[DBInstanceStatus,MultiAZ,BackupRetentionPeriod,AllocatedStorage,DeletionProtection,DBInstanceClass]' --output text 2>"$LOG")"; then
  # shellcheck disable=SC2086
  set -- $row
  info "reep-postgres: status=$1 multi_az=$2 retention=${3}d storage=${4}GiB deletion_protection=$5 class=$6"
  if [ "$5" = "True" ]; then
    pass "deletion protection is on"
  else
    fail "deletion protection is OFF — the mirror renders it on, so the import would show drift; enable it first"
  fi
  if [ "$1" = "available" ]; then
    pass "the instance is available"
  else
    warn "the instance is $1"
  fi
else
  fail "cannot describe reep-postgres:"
  excerpt
fi
snap="$(aws rds describe-db-snapshots --db-instance-identifier reep-postgres --snapshot-type manual --region "$HOME_REGION" \
  --query "DBSnapshots[?starts_with(DBSnapshotIdentifier, 'reep-postgres-pre-cdk-') && Status == 'available'].DBSnapshotIdentifier | [-1]" --output text 2>/dev/null || true)"
if [ -n "$snap" ] && [ "$snap" != "None" ]; then
  pass "manual pre-cutover snapshot exists: $snap"
else
  warn "no reep-postgres-pre-cdk-* snapshot yet. Take it before step 1 — the one action in step 0 this script will not do for you:"
  info "aws rds create-db-snapshot --db-instance-identifier reep-postgres --db-snapshot-identifier reep-postgres-pre-cdk-$(date -u +%Y%m%d)"
fi

summary
[ "$FAIL" -eq 0 ]
