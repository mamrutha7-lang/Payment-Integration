#!/usr/bin/env bash
# Make Terraform FORGET the resources CloudFormation now owns — without
# touching a single one of them.
#
# RUN THIS ONLY AFTER `cdk import` HAS SUCCEEDED for reep-core and reep-edge-waf
# (docs/cdk-cutover.md steps 5-6). Until then Terraform is the only thing that
# knows these resources exist, and this script removes that knowledge.
#
# What it does, in order:
#   1. pulls a copy of the state to a timestamped file — the undo
#   2. `terraform state rm` for EVERY address, in ONE invocation, so the state
#      is rewritten once; a half-run cannot leave Terraform managing half a VPC
#   3. `terraform plan` — which must now propose to CREATE everything. That is
#      the proof Terraform no longer manages them. DO NOT APPLY THAT PLAN.
#
# The .tf files are then deleted BY A HUMAN, in a commit, after reading the
# plan. Deleting them before step 2 is the failure the whole cutover is
# designed around: an `apply` against files that are gone and a state that
# still lists the database is a DESTROY of the database.
set -euo pipefail

cd "$(dirname "$0")/../../aws"

if [ "${1:-}" != "--i-have-run-cdk-import" ]; then
  echo "Refusing. Run cdk import first (docs/cdk-cutover.md), then:"
  echo "    $0 --i-have-run-cdk-import"
  exit 2
fi

# The precondition, checked rather than trusted: reep-core must exist in
# CloudFormation and have finished importing.
STATUS="$(aws cloudformation describe-stacks --stack-name reep-core --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo MISSING)"
case "$STATUS" in
  IMPORT_COMPLETE|UPDATE_COMPLETE) echo "reep-core is ${STATUS}" ;;
  *) echo "Refusing: reep-core is '${STATUS}', not IMPORT_COMPLETE/UPDATE_COMPLETE. Finish docs/cdk-cutover.md step 5 first."; exit 2 ;;
esac

VARS=()
[ -f prod.tfvars ] && VARS=(-var-file=prod.tfvars)

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="terraform.tfstate.before-release.${STAMP}"
terraform state pull > "$BACKUP"
echo "state backed up to infra/aws/${BACKUP} ($(wc -c < "$BACKUP") bytes) — keep this file"

# Every MANAGED address in the state. Read from the state, not typed — and
# data sources filtered out: they are not resources, nothing owns them, and
# they are re-read on every plan. `terraform state list` returns 87 addresses
# here of which 5 are data sources; only the 82 managed ones are released.
mapfile -t ADDRESSES < <(terraform state list | grep -v '^data\.')
echo "releasing ${#ADDRESSES[@]} managed addresses from Terraform's state"
printf '  %s\n' "${ADDRESSES[@]}"

# The prompt is the default and stays. RELEASE_CONFIRM exists so the step can
# be driven from a non-interactive session; it has to carry the same word, so
# it is no easier to trip over than the prompt.
CONFIRM="${RELEASE_CONFIRM:-}"
if [ -z "$CONFIRM" ]; then
  read -r -p "Type RELEASE to continue: " CONFIRM
fi
if [ "$CONFIRM" != "RELEASE" ]; then
  echo "nothing changed"
  exit 1
fi

terraform state rm "${ADDRESSES[@]}"
echo
echo "Terraform's state is now empty of managed resources. Proving it:"
terraform plan -no-color "${VARS[@]}" | tail -20
echo
echo "The plan above must say it would CREATE resources (it no longer knows they exist)."
echo "DO NOT APPLY IT. Delete infra/aws/*.tf in a commit, and CI stops planning here."
