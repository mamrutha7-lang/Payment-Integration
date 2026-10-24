#!/usr/bin/env bash
# Grant one Google address a role on the LIVE database, and show what happened.
#
#   ./grant.sh someone@bgscet.ac.in "Full Name" DIRECTOR
#   ./grant.sh mentor@bgscet.ac.in  "A Mentor"  MENTOR --with-group
#   ./grant.sh tester@gmail.com     "Test Stu"  STUDENT --usn TEST01
#   ./grant.sh oldboy@gmail.com     "An Alum"   ALUMNI
#
# WHY THIS EXISTS: sign-in is Google-only and the `users` table IS the
# allowlist, so on a fresh production database nobody can log in at all --
# including the person who has to fix that. `python -m app.grant_access` is the
# production-safe tool for it (it writes no usable password), but reaching it
# means a Fargate one-off task, and nobody should hand-write that JSON five
# times to set up five roles.
#
# DELIBERATELY NOT A BUTTON. This stays a command someone types at a terminal
# with credentials, and is NOT exposed as a workflow input, because --role
# ADMIN grants the highest privilege in the system -- by AGENTS.md rule 2 that
# account reads every student's marks, attendance and USN. app/grant_access.py
# refuses to default the role for the same reason; a dispatchable button would
# undo that care.
#
# Run it with credentials that can ecs:RunTask and read CloudWatch logs.
set -euo pipefail

if [ "$#" -lt 3 ]; then
  sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
  exit 64
fi

EMAIL="$1"; NAME="$2"; ROLE="$3"; shift 3

REGION="${AWS_REGION:-ap-south-1}"
CLUSTER="${ECS_CLUSTER:-reep}"
FAMILY="${ECS_TASK_FAMILY:-reep-api}"
SUBNETS="${ECS_SUBNETS:-subnet-0792c0ddcdd02f34f,subnet-0a41b8c5a98485d1a}"
SG="${ECS_SECURITY_GROUP:-sg-0dfa5c76167d79093}"
LOG_GROUP="${LOG_GROUP:-/reep/api}"

# Build the command array as JSON so a name with spaces survives intact.
CMD=$(python3 -c '
import json, sys
print(json.dumps(["python", "-m", "app.grant_access", sys.argv[1],
                  "--name", sys.argv[2], "--role", sys.argv[3]] + sys.argv[4:]))
' "$EMAIL" "$NAME" "$ROLE" "$@")

echo "granting $ROLE to $EMAIL on cluster $CLUSTER"

TASK_ARN=$(aws ecs run-task --region "$REGION" \
  --cluster "$CLUSTER" --task-definition "$FAMILY" --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=DISABLED}" \
  --overrides "{\"containerOverrides\":[{\"name\":\"api\",\"command\":$CMD}]}" \
  --query 'tasks[0].taskArn' --output text)

echo "task: $TASK_ARN"
aws ecs wait tasks-stopped --region "$REGION" --cluster "$CLUSTER" --tasks "$TASK_ARN"

EXIT=$(aws ecs describe-tasks --region "$REGION" --cluster "$CLUSTER" --tasks "$TASK_ARN" \
  --query 'tasks[0].containers[0].exitCode' --output text)

# The tool's whole output -- including its REFUSED line and the rule 2 warning
# about a mentor with no group -- goes to the container log. Printing it is the
# difference between "exit 2" and knowing why.
echo "--- log ---"
aws logs get-log-events --region "$REGION" \
  --log-group-name "$LOG_GROUP" \
  --log-stream-name "api/api/${TASK_ARN##*/}" \
  --query 'events[].message' --output text 2>/dev/null || echo "(log not available yet; retry in a few seconds)"
echo "-----------"
echo "exit code: $EXIT"
[ "$EXIT" = "0" ] || exit 1
