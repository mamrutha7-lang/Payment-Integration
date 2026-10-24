#!/usr/bin/env python3
"""CDK entry point — four stacks, three regions.

    reep-voice-platform   ap-south-1      the voice-assistant platform (S3, SQS, Lambda, DynamoDB, SSM)
    reep-core             ap-south-1      everything infra/aws/ was: VPC, ALB, ECS, RDS, EFS, S3, CloudFront, backup, IAM
    reep-edge-waf         us-east-1       the CloudFront-scope WAF
    reep-dr-vault         ap-southeast-1  the cross-region backup copy target

Context (cdk.json or `-c key=value`), the ones that change behaviour:

    project                  name prefix (default reep)
    phase                    core stack: "import" (mirror what exists, nothing more) or
                             "harden" (default: stopTimeout, vault lock, DR copy, restore
                             tests, SES, multi-AZ). See reep_core/stack.py.
    wafWebAclArn             reep-edge-waf's output, pasted into the core stack
    drVaultArn               reep-dr-vault's output, pasted into the core stack
    backupRetentionDays      the ONE retention number (default 35 in harden)
    dbMultiAz                default true in harden; cdk.json sets false (2026-10-03 cost decision)
    sesIdentityDomain        the SES-verified sending domain (grants ses:SendEmail)
    sesFromAddress           SES_FROM_ADDRESS for the api
    vaultLockCompliance      irreversible compliance-mode lock (default false)

The import-time identifiers (bucket names, security-group names, secret ARNs,
availability zones, the CloudFront prefix list) are produced by
tools/import_map.py from the Terraform state and land in cdk.context.json.

Account and region come from the CDK CLI's environment (CDK_DEFAULT_*), i.e.
whatever AWS profile the operator runs `cdk deploy` with.
"""

import os

import aws_cdk as cdk

from reep_core import CoreStack, DrVaultStack, EdgeWafStack
from reep_voice_platform import VoicePlatformStack

app = cdk.App()
project = app.node.try_get_context("project") or "reep"
account = os.environ.get("CDK_DEFAULT_ACCOUNT")
home_region = os.environ.get("CDK_DEFAULT_REGION") or "ap-south-1"


def _flag(key: str, default: bool) -> bool:
    v = app.node.try_get_context(key)
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _number(key: str, default: int) -> int:
    """A numeric context value, where ZERO IS A VALUE AND NOT AN ABSENCE.

    `int(try_get_context(key) or default)` is the obvious spelling and it is
    WRONG for every knob whose "off" is 0. cdk.json stores JSON integers, so
    `recordingRetentionDays: 0` arrives as the int `0`, which is falsy -- `or`
    then silently replaces the operator's explicit "never expire" with the
    default, and the stack synthesises a lifecycle rule the operator turned
    off. Nothing reports it: the template is valid, the deploy succeeds, and
    student voice is deleted on a clock somebody believed they had disabled.

    It is worse than an ordinary falsy-zero bug because it is INVISIBLE FROM
    THE TESTS. `infra/cdk/tests/*` construct the stacks directly with keyword
    arguments, so nothing that exercises the stack reaches this expression.
    That is why this helper is a named function with its own test rather than
    an inline fix at the one call site that needed it.
    """
    v = app.node.try_get_context(key)
    if v is None or v == "":
        return default
    return int(v)


retention = _number("backupRetentionDays", 35)

VoicePlatformStack(
    app,
    f"{project}-voice-platform",
    project=project,
    api_task_role_name=app.node.try_get_context("apiTaskRoleName"),
    github_deploy_role_name=app.node.try_get_context("githubDeployRoleName"),
    recording_retention_days=_number("recordingRetentionDays", 180),
    env=cdk.Environment(account=account, region=home_region),
    description="REEP voice-assistant platform: S3, SQS, Lambda, DynamoDB, IAM, SSM",
)

EdgeWafStack(
    app,
    f"{project}-edge-waf",
    project=project,
    env=cdk.Environment(account=account, region="us-east-1"),
    description="REEP edge WAF (CloudFront scope, us-east-1)",
)

DrVaultStack(
    app,
    f"{project}-dr-vault",
    project=project,
    min_retention_days=retention,
    compliance_lock=_flag("vaultLockCompliance", False),
    env=cdk.Environment(account=account, region="ap-southeast-1"),
    description="REEP cross-region backup copy target (ap-southeast-1)",
)

CoreStack(
    app,
    f"{project}-core",
    project=project,
    env=cdk.Environment(account=account, region=home_region),
    description="REEP core: VPC, ALB, ECS, RDS, EFS, S3, CloudFront, backup, alarms, IAM (the former infra/aws/)",
)

app.synth()
