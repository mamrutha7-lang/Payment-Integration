#!/usr/bin/env bash
# Creates the two things that must exist BEFORE the first terraform apply and
# that terraform therefore cannot create itself: the S3 bucket holding the state
# and the DynamoDB table holding the lock. Idempotent — safe to re-run.
set -euo pipefail

REGION="${REGION:-ap-south-1}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="reep-tfstate-${ACCOUNT}"
TABLE="reep-tfstate-lock"

echo "account ${ACCOUNT} / region ${REGION}"
echo "state bucket: ${BUCKET}"

if aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  echo "  bucket exists — leaving it alone"
else
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
    --create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null
  echo "  created"
fi

# Versioning is the undo button: a corrupt or truncated state push is
# recoverable only if the previous object version still exists.
aws s3api put-bucket-versioning --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled

aws s3api put-bucket-encryption --bucket "$BUCKET" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

# State holds the database endpoint, subnet ids and secret ARNs. It is never
# public, and this makes that structural rather than a matter of care.
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true'

if aws dynamodb describe-table --table-name "$TABLE" --region "$REGION" >/dev/null 2>&1; then
  echo "lock table exists — leaving it alone"
else
  aws dynamodb create-table --table-name "$TABLE" --region "$REGION" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST >/dev/null
  aws dynamodb wait table-exists --table-name "$TABLE" --region "$REGION"
  echo "lock table created"
fi

cat > "$(dirname "$0")/backend.hcl" <<EOF
bucket         = "${BUCKET}"
key            = "reep/aws/terraform.tfstate"
region         = "${REGION}"
dynamodb_table = "${TABLE}"
encrypt        = true
EOF

echo
echo "wrote backend.hcl — now run:"
echo "  terraform init -backend-config=backend.hcl"
